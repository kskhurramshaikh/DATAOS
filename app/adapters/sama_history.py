# SAMA Compliance History -- closes the "no trend-over-time exists"
# gap flagged in the 2026-08-19 gap analysis (SAMA has 8-domain detail
# + priority alerts + action items, but unlike NDI has no History page
# at all).
#
# HOW THIS DIFFERS FROM ndi_history.py, DELIBERATELY: NDI's per-domain
# inputs are Dr. Saber's fixed BAJ baseline, so its history is
# genuinely "the same score every time until the baseline changes" --
# documented there as an honesty constraint, not a bug. SAMA's domain
# scores (DG, DQ, RMD, PDP) are computed from REAL signals already
# tracked by DataOS -- the duplicate-review audit log and dataset
# null-rate metrics (see banking_adapter.compute_sama_compliance) --
# so recording a snapshot at two different points in a dataset's
# lifecycle (e.g. before and after a round of duplicate review) CAN
# show genuine movement. This module doesn't need NDI's
# all_identical-and-say-so disclosure as a result, though the same
# principle -- record what was actually computed, never manufacture
# movement -- still applies throughout.
#
# Snapshots are per-dataset (dataset_name is part of every row and
# every query here), unlike NDI's single global series -- SAMA compliance
# is scoped to one dataset at a time, same as the live SAMA Compliance
# page's own dataset picker.
#
# average_measured_score is what the trend chart plots: the mean of
# whichever domains were status="measured" at record time (currently
# up to 4 of the 8 official domains -- DG, DQ, RMD, PDP; see
# banking_adapter's own docstring for why the other 4 aren't
# instrumented). This can be None if zero domains were measured yet
# (e.g. duplicate detection never run) -- stored and shown honestly as
# "not yet measurable" rather than defaulted to 0.
#
# SCHEMA LOCATION NOTE (same as ndi_history.py): DDL lives here, not in
# app/db.py, since this table is read/written only by this module.

import json

from app import db
from app.adapters import banking_adapter, dataset_adapter


def _ensure_schema():
    """CREATE TABLE IF NOT EXISTS on whichever backend is live. Called
    at the top of every public function rather than cached -- see
    ndi_history.py's identical comment for why (the test suite
    monkeypatches db.DB_PATH per test)."""
    pk = "SERIAL PRIMARY KEY" if db._is_postgres() else "INTEGER PRIMARY KEY AUTOINCREMENT"
    with db.get_conn() as conn:
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS sama_snapshots (
                id {pk},
                dataset_name TEXT NOT NULL,
                recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                recorded_by TEXT NOT NULL,
                note TEXT,
                average_measured_score REAL,
                priority_alert TEXT NOT NULL,
                domain_scores_json TEXT NOT NULL
            )
            """
        )
        conn.commit()


def _row_to_dict(row, include_domains: bool) -> dict:
    out = {
        "id": row["id"],
        "dataset_name": row["dataset_name"],
        "recorded_at": row["recorded_at"],
        "recorded_by": row["recorded_by"],
        "note": row["note"],
        "average_measured_score": row["average_measured_score"],
        "priority_alert": row["priority_alert"],
    }
    if include_domains:
        out["domain_scores"] = json.loads(row["domain_scores_json"])
    return out


def record_snapshot(dataset_name: str, recorded_by: str, note: str | None = None) -> dict:
    """Computes the current SAMA compliance view for one dataset and
    stores it as a dated, attributed record -- same "who/when/what,
    stored as it read at that moment" contract as
    ndi_history.record_snapshot(). recorded_by is required for the
    same reason: an unattributed record has no audit value."""
    recorded_by = (recorded_by or "").strip()
    if not recorded_by:
        raise ValueError(
            "A name is required to record a SAMA snapshot -- an audit record with no named "
            "recorder isn't an audit record."
        )
    note = (note or "").strip() or None

    # Validates the dataset exists -- raises the same plain-English
    # ValueError as every other dataset-scoped adapter if it doesn't.
    dataset_adapter.list_datasets({"dataset_name": dataset_name})

    result = banking_adapter.compute_sama_compliance(dataset_name)
    measured = [d for d in result["domain_scores"] if d["status"] == "measured" and d["score"] is not None]
    avg_score = round(sum(d["score"] for d in measured) / len(measured), 1) if measured else None

    _ensure_schema()
    with db.get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO sama_snapshots (
                dataset_name, recorded_by, note, average_measured_score,
                priority_alert, domain_scores_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                dataset_name,
                recorded_by,
                note,
                avg_score,
                result["priority_alert"],
                json.dumps(result["domain_scores"]),
            ),
        )
        snapshot_id = cur.lastrowid
        conn.commit()

    return get_snapshot(snapshot_id)


def get_snapshot(snapshot_id: int) -> dict:
    """One recorded snapshot in full, including the per-domain
    breakdown exactly as computed at record time -- not recomputed on
    read, so an old record keeps showing what was actually true then
    even if the dataset's underlying data changes later."""
    _ensure_schema()
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM sama_snapshots WHERE id = ?", (snapshot_id,)
        ).fetchone()
    if not row:
        raise ValueError(f"No recorded SAMA snapshot found with id {snapshot_id}.")
    return _row_to_dict(row, include_domains=True)


def list_snapshots(dataset_name: str, limit: int = 100) -> dict:
    """Every snapshot recorded for ONE dataset, newest first, each with
    movement against the chronologically previous record for that same
    dataset -- same "what changed since last time" framing as
    ndi_history.list_snapshots(). Deltas computed across the full
    ordered set before `limit` is applied, same reasoning as there."""
    _ensure_schema()
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM sama_snapshots WHERE dataset_name = ? ORDER BY recorded_at ASC, id ASC",
            (dataset_name,),
        ).fetchall()

    ordered = [_row_to_dict(r, include_domains=False) for r in rows]
    for i, entry in enumerate(ordered):
        if i == 0 or entry["average_measured_score"] is None or ordered[i - 1]["average_measured_score"] is None:
            entry["delta_average_score"] = None
        else:
            entry["delta_average_score"] = round(
                entry["average_measured_score"] - ordered[i - 1]["average_measured_score"], 1
            )

    newest_first = list(reversed(ordered))[:limit]

    return {
        "dataset_name": dataset_name,
        "snapshots": newest_first,
        "count": len(ordered),
        "note": (
            "Each snapshot records the SAMA compliance view exactly as it read at that moment -- "
            "the average of whichever domains were measured then (currently up to DG, DQ, RMD, "
            "PDP; the other 4 official SAMA domains have no DataOS signal yet and never count "
            "toward this average). Genuine movement here reflects real changes in this dataset's "
            "duplicate-review completion and data quality, not a modelled trend."
        ),
    }


def export_history_csv(dataset_name: str) -> str:
    """One row per recorded snapshot for this dataset -- same summary
    data list_snapshots() shows, as a real downloadable CSV."""
    import csv
    import io

    data = list_snapshots(dataset_name, limit=100000)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["id", "dataset_name", "recorded_at", "recorded_by", "note", "average_measured_score", "priority_alert"])
    for s in reversed(data["snapshots"]):
        writer.writerow([
            s["id"], s["dataset_name"], s["recorded_at"], s["recorded_by"], s["note"] or "",
            s["average_measured_score"] if s["average_measured_score"] is not None else "",
            s["priority_alert"],
        ])
    return buf.getvalue()


def export_snapshot_csv(snapshot_id: int) -> str:
    """The full per-domain breakdown for one recorded snapshot."""
    import csv
    import io

    snap = get_snapshot(snapshot_id)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([f"SAMA snapshot #{snap['id']}", snap["dataset_name"], f"recorded {snap['recorded_at']}", f"by {snap['recorded_by']}"])
    if snap["note"]:
        writer.writerow([f"note: {snap['note']}"])
    writer.writerow([])
    writer.writerow(["code", "name", "score", "status"])
    for d in snap["domain_scores"]:
        writer.writerow([d["code"], d["name"], d["score"] if d["score"] is not None else "", d["status"]])
    return buf.getvalue()
