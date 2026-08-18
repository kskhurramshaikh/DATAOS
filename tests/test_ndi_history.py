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
