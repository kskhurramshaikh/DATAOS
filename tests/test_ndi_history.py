# Item 5 -- NDI assessment history. Runs on the SQLite fallback, the
# same path CI uses (LAKEHOUSE_DB_URI is deliberately unset there), via
# the app.db.DB_PATH monkeypatch pattern the dedup tests established.

import pytest

from app.adapters import ndi_history


def _fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr("app.db.DB_PATH", str(tmp_path / "test.db"))
    from app.db import init_db

    init_db()


def test_record_snapshot_stores_full_domain_breakdown(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)

    snap = ndi_history.record_snapshot("reviewer@example.com", note="Q3 baseline")

    assert snap["recorded_by"] == "reviewer@example.com"
    assert snap["note"] == "Q3 baseline"
    assert snap["recorded_at"]
    # The signed-off figures -- same values Item 1's Gold table was
    # verified against, so a change here is a real regression, not a
    # cosmetic one.
    assert snap["display_score"] == 48.3
    assert snap["maturity_level"] == "Developing"
    assert snap["overall_compliance_pct"] == 60.2
    assert len(snap["domains"]) == 14
    assert {d["code"] for d in snap["domains"]} >= {"DG", "DQ", "PDP", "OD"}


def test_record_snapshot_requires_a_named_recorder(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)

    for bad in ("", "   ", None):
        with pytest.raises(ValueError):
            ndi_history.record_snapshot(bad)


def test_empty_note_is_stored_as_null_not_blank(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)

    snap = ndi_history.record_snapshot("reviewer@example.com", note="   ")
    assert snap["note"] is None


def test_history_is_newest_first_with_deltas(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)

    ndi_history.record_snapshot("first@example.com")
    ndi_history.record_snapshot("second@example.com")

    history = ndi_history.list_snapshots()

    assert history["count"] == 2
    assert history["snapshots"][0]["recorded_by"] == "second@example.com"
    # Oldest record has nothing to compare against -- explicitly None,
    # never a fabricated 0.0 that would read as "measured no change."
    assert history["snapshots"][-1]["delta_display_score"] is None
    # Fixed baseline -> genuinely no movement between the two.
    assert history["snapshots"][0]["delta_display_score"] == 0.0


def test_all_identical_flag_is_honest_about_the_fixed_baseline(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)

    # A single record can't be "all identical" -- there's nothing to
    # compare it to yet.
    ndi_history.record_snapshot("a@example.com")
    assert ndi_history.list_snapshots()["all_identical"] is False

    ndi_history.record_snapshot("b@example.com")
    assert ndi_history.list_snapshots()["all_identical"] is True


def test_empty_history_is_a_clean_empty_state(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)

    history = ndi_history.list_snapshots()
    assert history["count"] == 0
    assert history["snapshots"] == []
    assert history["all_identical"] is False


def test_get_snapshot_rejects_unknown_id(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)

    with pytest.raises(ValueError):
        ndi_history.get_snapshot(99999)


def test_compare_snapshots_orders_chronologically_regardless_of_arg_order(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)

    first = ndi_history.record_snapshot("first@example.com")
    second = ndi_history.record_snapshot("second@example.com")

    # Pass the LATER id as "a" and the EARLIER id as "b" -- the
    # comparison must still put "first" under "from" and "second"
    # under "to", proving it orders by recorded_at/id, not argument
    # position.
    result = ndi_history.compare_snapshots(second["id"], first["id"])

    assert result["from"]["id"] == first["id"]
    assert result["to"]["id"] == second["id"]
    # Fixed baseline -> genuinely identical, real signal not a stub.
    assert result["identical"] is True
    assert result["delta_display_score"] == 0.0
    assert len(result["domains"]) == 14
    assert all(d["comparable"] for d in result["domains"])
    assert all(d["delta_maturity_score"] == 0 for d in result["domains"])


def test_compare_snapshots_rejects_comparing_a_record_to_itself(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)

    snap = ndi_history.record_snapshot("solo@example.com")
    with pytest.raises(ValueError):
        ndi_history.compare_snapshots(snap["id"], snap["id"])


def test_compare_snapshots_rejects_unknown_id(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)

    snap = ndi_history.record_snapshot("solo2@example.com")
    with pytest.raises(ValueError):
        ndi_history.compare_snapshots(snap["id"], 999999)


def test_export_history_csv_has_one_row_per_snapshot_oldest_first(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)

    ndi_history.record_snapshot("first@example.com", note="Q1")
    ndi_history.record_snapshot("second@example.com", note="Q2")

    csv_text = ndi_history.export_history_csv()
    lines = csv_text.strip().splitlines()

    assert lines[0].startswith("id,recorded_at,recorded_by")
    assert len(lines) == 3  # header + 2 real rows
    assert "first@example.com" in lines[1]
    assert "Q1" in lines[1]
    assert "second@example.com" in lines[2]
    assert "Q2" in lines[2]


def test_export_snapshot_csv_includes_full_domain_breakdown(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)

    snap = ndi_history.record_snapshot("reviewer@example.com", note="full detail check")
    csv_text = ndi_history.export_snapshot_csv(snap["id"])

    assert f"NDI assessment #{snap['id']}" in csv_text
    assert "note: full detail check" in csv_text
    assert "code,name,spec_count,maturity_score" in csv_text
    # 14 real domain rows, not a placeholder count.
    domain_lines = [l for l in csv_text.strip().splitlines() if l and l[0].isalpha() and "," in l and "code,name" not in l]
    # Every domain code from the real assessment appears somewhere in the export.
    for d in snap["domains"]:
        assert d["code"] in csv_text


def test_export_snapshot_csv_rejects_unknown_id(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)

    with pytest.raises(ValueError):
        ndi_history.export_snapshot_csv(999999)
