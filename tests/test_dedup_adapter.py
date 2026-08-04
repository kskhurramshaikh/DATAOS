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
