from app.adapters import dedup_adapter


def test_not_applicable_without_dob_and_name_columns():
    csv = "id,value\n1,10\n2,20\n"
    result = dedup_adapter.find_duplicate_candidates({"csv_content": csv, "dataset_name": "x"})
    assert result["applicable"] is False


def test_requires_csv_content():
    try:
        dedup_adapter.find_duplicate_candidates({"dataset_name": "x"})
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_forms_high_confidence_cluster_on_matching_dob_and_name(tmp_path, monkeypatch):
    monkeypatch.setattr("app.db.DB_PATH", str(tmp_path / "test.db"))
    from app.db import init_db
    init_db()

    csv = (
        "CUST_ID,FULL_NAME,DOB,PHONE\n"
        "C1,Ahmed Al-Rashidi,1980-01-01,0501111111\n"
        "C2,Ahmed Al-Rashidi,1980-01-01,0502222222\n"
        "C3,Sara Al-Otaibi,1990-05-05,0503333333\n"
    )
    result = dedup_adapter.find_duplicate_candidates({"csv_content": csv, "dataset_name": "dedup_test_1"})

    assert result["applicable"] is True
    assert result["total_clusters"] == 1
    cluster = result["clusters"][0]
    assert cluster["size"] == 2
    assert cluster["confidence_tier"] == "high_confidence"
    assert {m["row_id"] for m in cluster["members"]} == {"C1", "C2"}


def test_different_dob_does_not_cluster(tmp_path, monkeypatch):
    monkeypatch.setattr("app.db.DB_PATH", str(tmp_path / "test.db"))
    from app.db import init_db
    init_db()

    csv = (
        "CUST_ID,FULL_NAME,DOB,PHONE\n"
        "C1,Ahmed Al-Rashidi,1980-01-01,0501111111\n"
        "C2,Ahmed Al-Rashidi,1985-06-15,0502222222\n"
    )
    result = dedup_adapter.find_duplicate_candidates({"csv_content": csv, "dataset_name": "dedup_test_2"})
    assert result["total_clusters"] == 0


def test_same_dob_different_name_tiers_as_needs_review(tmp_path, monkeypatch):
    monkeypatch.setattr("app.db.DB_PATH", str(tmp_path / "test.db"))
    from app.db import init_db
    init_db()

    csv = (
        "CUST_ID,FULL_NAME,DOB,PHONE\n"
        "C1,Ahmed Al-Rashidi,1980-01-01,0501111111\n"
        "C2,Fatima Al-Zahrani,1980-01-01,0502222222\n"
    )
    result = dedup_adapter.find_duplicate_candidates({"csv_content": csv, "dataset_name": "dedup_test_3"})
    assert result["total_clusters"] == 1
    assert result["clusters"][0]["confidence_tier"] == "needs_review"


def test_decide_cluster_updates_status(tmp_path, monkeypatch):
    monkeypatch.setattr("app.db.DB_PATH", str(tmp_path / "test.db"))
    from app.db import init_db, get_conn
    init_db()

    csv = (
        "CUST_ID,FULL_NAME,DOB,PHONE\n"
        "C1,Ahmed Al-Rashidi,1980-01-01,0501111111\n"
        "C2,Ahmed Al-Rashidi,1980-01-01,0502222222\n"
    )
    dedup_adapter.find_duplicate_candidates({"csv_content": csv, "dataset_name": "dedup_test_4"})

    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM duplicate_clusters WHERE dataset_safe_name = 'dedup_test_4'"
        ).fetchone()
    cluster_id = row["id"]

    result = dedup_adapter.decide_cluster(cluster_id, "confirmed_duplicate")
    assert result["status"] == "confirmed_duplicate"

    pending = dedup_adapter.get_pending_clusters("dedup_test_4")
    assert pending == []


def test_decide_cluster_rejects_invalid_status():
    try:
        dedup_adapter.decide_cluster(1, "maybe")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_decide_cluster_rejects_unknown_id(tmp_path, monkeypatch):
    monkeypatch.setattr("app.db.DB_PATH", str(tmp_path / "test.db"))
    from app.db import init_db
    init_db()
    try:
        dedup_adapter.decide_cluster(99999, "not_duplicate")
        assert False, "expected ValueError"
    except ValueError:
        pass


def _seed_clusters(tmp_path, monkeypatch, dataset_name="bulk_test"):
    monkeypatch.setattr("app.db.DB_PATH", str(tmp_path / "test.db"))
    from app.db import init_db
    init_db()
    # High-confidence pair (same name) + needs-review pair (different name, same DOB)
    csv = (
        "CUST_ID,FULL_NAME,DOB,PHONE\n"
        "C1,Ahmed Al-Rashidi,1980-01-01,0501111111\n"
        "C2,Ahmed Al-Rashidi,1980-01-01,0502222222\n"
        "C3,Sara Al-Otaibi,1990-05-05,0503333333\n"
        "C4,Noura Al-Harbi,1990-05-05,0504444444\n"
    )
    return dedup_adapter.find_duplicate_candidates({"csv_content": csv, "dataset_name": dataset_name})


def test_confirm_high_confidence_only_confirms_that_tier(tmp_path, monkeypatch):
    result = _seed_clusters(tmp_path, monkeypatch)
    assert result["total_clusters"] == 2

    outcome = dedup_adapter.confirm_high_confidence({"dataset_name": "bulk_test"})
    assert outcome["clusters_confirmed"] == 1
    assert outcome["clusters_remaining_pending"] == 1

    pending = dedup_adapter.get_pending_clusters("bulk_test")
    assert len(pending) == 1
    assert pending[0]["confidence_tier"] == "needs_review"


def test_confirm_all_confirms_every_pending_tier(tmp_path, monkeypatch):
    _seed_clusters(tmp_path, monkeypatch)

    outcome = dedup_adapter.confirm_all_pending({"dataset_name": "bulk_test"})
    assert outcome["clusters_confirmed"] == 2
    assert outcome["clusters_remaining_pending"] == 0
    assert dedup_adapter.get_pending_clusters("bulk_test") == []


def test_bulk_confirm_auto_detects_single_dataset_with_pending(tmp_path, monkeypatch):
    _seed_clusters(tmp_path, monkeypatch, dataset_name="only_one")

    outcome = dedup_adapter.confirm_all_pending({})  # no dataset_name given
    assert outcome["dataset_name"] == "only_one"
    assert outcome["clusters_confirmed"] == 2


def test_bulk_confirm_raises_when_multiple_datasets_have_pending(tmp_path, monkeypatch):
    monkeypatch.setattr("app.db.DB_PATH", str(tmp_path / "test.db"))
    from app.db import init_db
    init_db()
    csv = (
        "CUST_ID,FULL_NAME,DOB,PHONE\n"
        "C1,Ahmed Al-Rashidi,1980-01-01,0501111111\n"
        "C2,Ahmed Al-Rashidi,1980-01-01,0502222222\n"
    )
    dedup_adapter.find_duplicate_candidates({"csv_content": csv, "dataset_name": "dataset_a"})
    dedup_adapter.find_duplicate_candidates({"csv_content": csv, "dataset_name": "dataset_b"})

    try:
        dedup_adapter.confirm_all_pending({})
        assert False, "expected ValueError"
    except ValueError as e:
        assert "dataset_a" in str(e) and "dataset_b" in str(e)


def test_bulk_confirm_raises_when_nothing_pending(tmp_path, monkeypatch):
    monkeypatch.setattr("app.db.DB_PATH", str(tmp_path / "test.db"))
    from app.db import init_db
    init_db()
    try:
        dedup_adapter.confirm_all_pending({})
        assert False, "expected ValueError"
    except ValueError:
        pass
