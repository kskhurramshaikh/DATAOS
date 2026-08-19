# Duplicate/Entity-Match Adapter -- fuzzy near-duplicate detection, v1
#
# This is v1 of an ongoing effort, not a finished algorithm -- harness-
# tested against a real 600-row file before any of this was built (see
# project notes). Key finding from that testing: exact Date-of-Birth
# match is a far stronger signal than name/phone fuzzy matching alone --
# 100% recall on known duplicates at far higher precision (108
# candidates vs 509 for a naive name+phone blend). DOB is what forms
# clusters here; name similarity only RANKS confidence within an
# already-formed cluster, never forms one -- a naive full-graph approach
# on name/phone similarity produced a 14-person cluster in testing,
# which is obviously wrong (chaining). Only exact-DOB edges are trusted
# to merge records into a group.
#
# What v1 does: detect candidate duplicate groups, cluster them safely,
# tier by confidence, persist them for human review, gate Gold
# promotion until every group has a decision, and (as of Item 3 --
# see _execute_merge below) actually EXECUTE the merge the moment a
# cluster is confirmed -- not just decide and stop there.

import io
import json
from datetime import datetime, timezone

import pandas as pd
from rapidfuzz import fuzz

from app.db import get_conn
from app.adapters import dataset_adapter

DOB_COL_CANDIDATES = ["dob", "date_of_birth", "birth_date", "birthdate"]
NAME_COL_CANDIDATES = ["full_name", "name", "customer_name"]
PHONE_COL_CANDIDATES = ["phone", "mobile", "phone_number", "mobile_number"]
ID_COL_CANDIDATES = ["cust_id", "customer_id", "id"]

HIGH_CONFIDENCE_NAME_THRESHOLD = 85


def _find_col(columns, candidates):
    lower_map = {str(c).lower().replace(" ", "_"): c for c in columns}
    for cand in candidates:
        if cand in lower_map:
            return lower_map[cand]
    return None


def is_applicable(csv_content: str) -> bool:
    """Cheap check: does this data have the columns duplicate detection
    needs (name + DOB), without running full detection or touching the
    database? Used to decide whether to recommend this action at all."""
    try:
        header_only = pd.read_csv(io.StringIO(csv_content), nrows=0)
    except Exception:
        return False
    return bool(_find_col(header_only.columns, DOB_COL_CANDIDATES) and _find_col(header_only.columns, NAME_COL_CANDIDATES))


def find_duplicate_candidates(payload: dict) -> dict:
    csv_content = payload.get("csv_content")
    dataset_name = payload.get("dataset_name", "")
    if not csv_content:
        raise ValueError("csv_content is required to detect duplicate candidates.")

    df = pd.read_csv(io.StringIO(csv_content))
    n = len(df)

    dob_col = _find_col(df.columns, DOB_COL_CANDIDATES)
    name_col = _find_col(df.columns, NAME_COL_CANDIDATES)
    phone_col = _find_col(df.columns, PHONE_COL_CANDIDATES)
    id_col = _find_col(df.columns, ID_COL_CANDIDATES)

    if not dob_col or not name_col:
        dataset_adapter.mark_duplicate_check_run(dataset_name)
        return {
            "applicable": False,
            "reason": (
                "This dataset doesn't have both a date-of-birth and a name column, so "
                "entity-duplicate detection doesn't apply to it."
            ),
        }

    dobs = df[dob_col].fillna("")
    names = df[name_col].fillna("")
    phones = df[phone_col].astype(str).fillna("") if phone_col else pd.Series([""] * n)
    row_ids = df[id_col] if id_col else pd.Series(df.index)

    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[rx] = ry

    dob_groups: dict[str, list[int]] = {}
    for i in range(n):
        if dobs[i] != "":
            dob_groups.setdefault(dobs[i], []).append(i)

    for group in dob_groups.values():
        if len(group) < 2:
            continue
        for k in range(1, len(group)):
            union(group[0], group[k])

    clusters: dict[int, set[int]] = {}
    for group in dob_groups.values():
        if len(group) < 2:
            continue
        for idx in group:
            clusters.setdefault(find(idx), set()).add(idx)

    cluster_list = []
    for members in clusters.values():
        members = sorted(members)
        pairwise_scores = [
            fuzz.token_sort_ratio(names[members[a]], names[members[b]])
            for a in range(len(members))
            for b in range(a + 1, len(members))
        ]
        min_name_score = min(pairwise_scores) if pairwise_scores else 0
        tier = "high_confidence" if min_name_score >= HIGH_CONFIDENCE_NAME_THRESHOLD else "needs_review"

        cluster_list.append({
            "members": [
                {
                    "row_id": str(row_ids[m]),
                    "name": names[m],
                    "dob": dobs[m],
                    "phone": phones[m] if phone_col else None,
                }
                for m in members
            ],
            "size": len(members),
            "confidence_tier": tier,
            "min_name_similarity": round(min_name_score, 1),
        })

    cluster_list.sort(
        key=lambda c: (0 if c["confidence_tier"] == "high_confidence" else 1, -c["size"])
    )

    with get_conn() as conn:
        # A fresh detection run reflects the current authoritative state
        # of the dataset -- stale pending clusters from an earlier run
        # must not just sit there accumulating. Without this, re-running
        # "find duplicates" on the same dataset piled a whole new batch
        # on top of whatever was already pending every single time
        # (confirmed via testing: pending count climbed 83 -> 162 -> 186
        # -> 269 across repeated runs on the same dataset, which also
        # fed directly into SAMA's DG/RMD scores and Customer 360's
        # duplicate counts). Already-decided clusters are untouched --
        # they're real audit history, never cleared by a re-run.
        conn.execute(
            "DELETE FROM duplicate_clusters WHERE dataset_safe_name = ? AND status = 'pending'",
            (dataset_name,),
        )

        # A cluster whose exact member set was already decided (confirmed
        # or rejected) must not reappear as newly pending -- that would
        # silently reopen a real human decision every time detection
        # re-runs, which is worse than the accumulation bug it would
        # otherwise still leave behind.
        decided_member_sets = {
            frozenset(json.loads(r["member_row_ids_json"]))
            for r in conn.execute(
                "SELECT member_row_ids_json FROM duplicate_clusters WHERE dataset_safe_name = ? AND status != 'pending'",
                (dataset_name,),
            ).fetchall()
        }
        cluster_list = [
            c for c in cluster_list
            if frozenset(m["row_id"] for m in c["members"]) not in decided_member_sets
        ]

        for i, cluster in enumerate(cluster_list):
            cur = conn.execute(
                """INSERT INTO duplicate_clusters
                   (dataset_safe_name, cluster_index, member_row_ids_json, member_summary_json,
                    confidence_tier, evidence_json, status)
                   VALUES (?, ?, ?, ?, ?, ?, 'pending')""",
                (
                    dataset_name, i,
                    json.dumps([m["row_id"] for m in cluster["members"]]),
                    json.dumps(cluster["members"], ensure_ascii=False),
                    cluster["confidence_tier"],
                    json.dumps({"min_name_similarity": cluster["min_name_similarity"]}),
                ),
            )
            cluster["id"] = cur.lastrowid
        conn.commit()

    dataset_adapter.mark_duplicate_check_run(dataset_name)

    high_confidence_count = sum(1 for c in cluster_list if c["confidence_tier"] == "high_confidence")
    return {
        "applicable": True,
        "dataset_name": dataset_name,
        "total_clusters": len(cluster_list),
        "high_confidence_clusters": high_confidence_count,
        "needs_review_clusters": len(cluster_list) - high_confidence_count,
        "total_records_involved": sum(c["size"] for c in cluster_list),
        "clusters": cluster_list,
        "methodology_note": (
            "Clusters are formed using exact date-of-birth matches -- the strongest signal "
            "found during testing against real data (100% recall on known duplicates, versus "
            "a 15% precision / high-noise result from name+phone fuzzy matching alone). Name "
            "similarity ranks confidence within a cluster, never forms one, specifically to "
            "avoid incorrectly chaining unrelated people together. Detects and groups "
            "candidates only -- does not merge or modify any record."
        ),
    }


def sanitize_clusters_output_for_display(output: dict) -> dict:
    """Strips internal matching internals -- per-cluster similarity
    scores, the methodology explanation, the matching algorithm name --
    from a find_duplicate_candidates() result before it's ever shown in
    a raw-JSON 'view details' panel. The review cards (the
    "duplicate_review" event) are the correct user-facing view for this
    data; this function only governs whatever raw-result debug view
    sits alongside them. Used by both the chip-click path (main.py) and
    the natural-language chat path (interpreter.py) so there's one
    definition of "safe to show," not two that can drift apart.
    """
    if not output.get("applicable"):
        return output
    return {
        "applicable": True,
        "total_clusters": output["total_clusters"],
        "high_confidence_groups": output["high_confidence_clusters"],
        "needs_review_groups": output["needs_review_clusters"],
        "total_records_involved": output["total_records_involved"],
        "note": "Per-record detail and decisions are in the review cards above -- this summary omits internal matching scores.",
    }


# ---------------------------------------------------------------------
# Golden Record merge execution (Item 3, MDM) -- this is what v1's own
# docstring flagged as deliberately NOT built: "decide which record's
# data wins and write one consolidated record back to Gold." That's
# what this does now, for real, not an estimate.
# ---------------------------------------------------------------------

def _execute_merge(cluster_id: int, merged_by: str) -> dict:
    """Survivorship strategy: the cluster member with the FEWEST missing
    fields (across the dataset's real columns, not just name/DOB/phone)
    becomes the base record. Any field still missing on the base is
    filled from the first other member that has a non-null value for
    it. Every field's contributing source row is recorded individually
    -- not just the final merged value -- so a golden record's drill-
    down can show exactly which source row each field came from, not
    just present a black-box result.

    Idempotent: re-confirming an already-merged cluster returns the
    existing golden record rather than creating a duplicate one.

    Reads the CURRENT Silver CSV for the dataset (not the name/DOB/phone
    snapshot stored on the cluster at detection time) specifically to
    get every real column, since a genuine merge has to reconcile the
    whole record, not just the three fields used to detect the match.
    """
    with get_conn() as conn:
        cluster_row = conn.execute(
            "SELECT * FROM duplicate_clusters WHERE id = ?", (cluster_id,)
        ).fetchone()
        if cluster_row is None:
            raise ValueError(f"No duplicate cluster found with id {cluster_id}.")
        if cluster_row["status"] != "confirmed_duplicate":
            raise ValueError(f"Cluster {cluster_id} is not confirmed_duplicate -- cannot merge.")

        existing = conn.execute(
            "SELECT id FROM golden_records WHERE cluster_id = ?", (cluster_id,)
        ).fetchone()
        if existing:
            return get_golden_record_detail(existing["id"])

    dataset_safe_name = cluster_row["dataset_safe_name"]
    member_row_ids = json.loads(cluster_row["member_row_ids_json"])

    silver_csv = dataset_adapter.read_silver_csv(dataset_safe_name)
    df = pd.read_csv(io.StringIO(silver_csv))

    id_col = _find_col(df.columns, ID_COL_CANDIDATES)
    row_key_series = df[id_col].astype(str) if id_col else df.index.astype(str)
    df = df.assign(_row_key=row_key_series)

    member_rows = df[df["_row_key"].isin(member_row_ids)]
    if member_rows.empty:
        raise ValueError(
            f"Could not find the source rows for cluster {cluster_id} in the current Silver data "
            f"for '{dataset_safe_name}' -- the data may have changed since detection ran."
        )

    data_cols = [c for c in df.columns if c != "_row_key"]

    completeness = member_rows[data_cols].notna().sum(axis=1)
    base_idx = completeness.idxmax()
    base_row = member_rows.loc[base_idx]

    def _clean(val):
        if hasattr(val, "item"):
            val = val.item()
        return val

    merged_record: dict = {}
    field_sources: dict = {}
    for col in data_cols:
        base_val = base_row[col]
        if pd.notna(base_val):
            merged_record[col] = _clean(base_val)
            field_sources[col] = str(base_row["_row_key"])
            continue
        filled = False
        for _, row in member_rows.iterrows():
            val = row[col]
            if pd.notna(val):
                merged_record[col] = _clean(val)
                field_sources[col] = str(row["_row_key"])
                filled = True
                break
        if not filled:
            merged_record[col] = None
            field_sources[col] = None

    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO golden_records
               (dataset_safe_name, cluster_id, merged_data_json, field_sources_json,
                source_row_ids_json, base_row_id, merged_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                dataset_safe_name, cluster_id,
                json.dumps(merged_record, default=str, ensure_ascii=False),
                json.dumps(field_sources, ensure_ascii=False),
                json.dumps(member_row_ids),
                str(base_row["_row_key"]),
                merged_by, now,
            ),
        )
        golden_record_id = cur.lastrowid
        conn.commit()

    return get_golden_record_detail(golden_record_id)


def get_golden_record_detail(golden_record_id: int) -> dict:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM golden_records WHERE id = ?", (golden_record_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"No golden record found with id {golden_record_id}.")
        cluster_row = conn.execute(
            "SELECT * FROM duplicate_clusters WHERE id = ?", (row["cluster_id"],)
        ).fetchone()
    return _golden_record_from_rows(row, cluster_row)


def _golden_record_from_rows(row, cluster_row) -> dict:
    """Shared shaping for one golden record -- used by both the single-
    record path (get_golden_record_detail) and the list path
    (get_golden_records) so the two can never drift apart."""
    return {
        "id": row["id"],
        "dataset_safe_name": row["dataset_safe_name"],
        "cluster_id": row["cluster_id"],
        "merged_record": json.loads(row["merged_data_json"]),
        "field_sources": json.loads(row["field_sources_json"]),
        "source_row_ids": json.loads(row["source_row_ids_json"]),
        "base_row_id": row["base_row_id"],
        "merged_by": row["merged_by"],
        "created_at": row["created_at"],
        "source_summary": json.loads(cluster_row["member_summary_json"]) if cluster_row else [],
        "confidence_tier": cluster_row["confidence_tier"] if cluster_row else None,
    }


def get_golden_records(dataset_safe_name: str | None = None, limit: int = 200) -> list[dict]:
    """PERFORMANCE FIX (2026-08-19, found live while profiling the MDM
    Field-Level Lineage page hang): the first version SELECTed the id
    list, then called get_golden_record_detail() ONCE PER ROW -- and
    each of those opens a brand-new get_conn() (on Render: a fresh
    cross-region psycopg2.connect() + CREATE SCHEMA + SET search_path
    round-trips) and runs two more queries. With ~78 golden records
    that was ~300+ sequential cross-region round-trips per call; the
    field-lineage endpoint called this twice, which is the whole 40+
    second "Loading..." hang. Marquez was measured at <1s and was NOT
    the bottleneck. Now: one connection, one JOIN query, same output
    shape."""
    sql = (
        "SELECT g.id, g.dataset_safe_name, g.cluster_id, g.merged_data_json, "
        "g.field_sources_json, g.source_row_ids_json, g.base_row_id, g.merged_by, "
        "g.created_at, c.member_summary_json, c.confidence_tier "
        "FROM golden_records g LEFT JOIN duplicate_clusters c ON c.id = g.cluster_id "
    )
    with get_conn() as conn:
        if dataset_safe_name:
            rows = conn.execute(
                sql + "WHERE g.dataset_safe_name = ? ORDER BY g.created_at DESC LIMIT ?",
                (dataset_safe_name, limit),
            ).fetchall()
        else:
            rows = conn.execute(sql + "ORDER BY g.created_at DESC LIMIT ?", (limit,)).fetchall()
    return [
        _golden_record_from_rows(
            r,
            {"member_summary_json": r["member_summary_json"], "confidence_tier": r["confidence_tier"]}
            if r["member_summary_json"] is not None else None,
        )
        for r in rows
    ]


# ---------------------------------------------------------------------
# Decisions -- now trigger a real merge the moment a cluster is
# confirmed, rather than leaving "confirmed" and "actually merged" as
# two separate manual steps.
# ---------------------------------------------------------------------

def decide_cluster(cluster_id: int, status: str, decided_by: str) -> dict:
    if status not in ("confirmed_duplicate", "not_duplicate"):
        raise ValueError(f"Invalid status '{status}' -- must be 'confirmed_duplicate' or 'not_duplicate'.")

    with get_conn() as conn:
        row = conn.execute("SELECT id FROM duplicate_clusters WHERE id = ?", (cluster_id,)).fetchone()
        if row is None:
            raise ValueError(f"No duplicate cluster found with id {cluster_id}.")
        conn.execute(
            "UPDATE duplicate_clusters SET status = ?, decided_at = CURRENT_TIMESTAMP, decided_by = ? WHERE id = ?",
            (status, decided_by, cluster_id),
        )
        conn.commit()

    result = {"id": cluster_id, "status": status, "decided_by": decided_by}

    if status == "confirmed_duplicate":
        # The decision itself already committed above -- a merge failure
        # here (e.g. Silver data changed underneath it) must not silently
        # swallow that decision or pretend nothing happened. Surfaced as
        # a separate field, not hidden behind a generic success response.
        try:
            result["golden_record"] = _execute_merge(cluster_id, merged_by=decided_by)
        except ValueError as e:
            result["merge_error"] = str(e)

    return result


def get_audit_log(dataset_safe_name: str | None = None, limit: int = 200) -> list[dict]:
    """Every decided cluster -- who decided it, what they decided, and when.
    This is the durable record a bank compliance review needs; unlike the
    chat transcript, it's queryable independently of any one conversation.
    """
    with get_conn() as conn:
        if dataset_safe_name:
            rows = conn.execute(
                "SELECT * FROM duplicate_clusters WHERE dataset_safe_name = ? AND status != 'pending' "
                "ORDER BY decided_at DESC LIMIT ?",
                (dataset_safe_name, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM duplicate_clusters WHERE status != 'pending' "
                "ORDER BY decided_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return [
        {
            "id": r["id"],
            "dataset_safe_name": r["dataset_safe_name"],
            "members": json.loads(r["member_summary_json"]),
            "confidence_tier": r["confidence_tier"],
            "status": r["status"],
            "decided_by": r["decided_by"],
            "decided_at": r["decided_at"],
        }
        for r in rows
    ]


def get_pending_clusters(dataset_safe_name: str) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM duplicate_clusters WHERE dataset_safe_name = ? AND status = 'pending' "
            "ORDER BY cluster_index",
            (dataset_safe_name,),
        ).fetchall()
    return [
        {
            "id": r["id"],
            "members": json.loads(r["member_summary_json"]),
            "confidence_tier": r["confidence_tier"],
            "evidence": json.loads(r["evidence_json"]),
        }
        for r in rows
    ]


def get_pending_count(dataset_safe_name: str) -> int:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as c FROM duplicate_clusters WHERE dataset_safe_name = ? AND status = 'pending'",
            (dataset_safe_name,),
        ).fetchone()
    return row["c"] if row else 0


def _bulk_confirm(dataset_safe_name: str | None, tier: str | None, decided_by: str) -> dict:
    """
    Applies 'confirmed_duplicate' to every PENDING cluster matching the
    given tier ('high_confidence' or None/'all' for every pending
    cluster regardless of tier). This is a batch-apply of a decision the
    human has already made about which tier to trust -- not an
    algorithmic merge decision. If dataset_safe_name isn't given and
    exactly one dataset has pending clusters, that one is used
    automatically; if more than one does, this raises so the caller can
    ask which dataset was meant rather than guessing.

    Each newly-confirmed cluster is merged immediately, same as a single
    decide_cluster() confirm -- see decide_cluster()'s own comment for
    why a merge failure doesn't swallow the confirmation itself.
    """
    with get_conn() as conn:
        if not dataset_safe_name:
            rows = conn.execute(
                "SELECT DISTINCT dataset_safe_name FROM duplicate_clusters WHERE status = 'pending'"
            ).fetchall()
            names = [r["dataset_safe_name"] for r in rows]
            if len(names) == 0:
                raise ValueError("There are no pending duplicate clusters for any dataset right now.")
            if len(names) > 1:
                raise ValueError(
                    f"More than one dataset has pending duplicate clusters ({', '.join(names)}) -- "
                    f"please say which one."
                )
            dataset_safe_name = names[0]

        if tier and tier != "all":
            rows = conn.execute(
                "SELECT id FROM duplicate_clusters WHERE dataset_safe_name = ? AND status = 'pending' "
                "AND confidence_tier = ?",
                (dataset_safe_name, tier),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id FROM duplicate_clusters WHERE dataset_safe_name = ? AND status = 'pending'",
                (dataset_safe_name,),
            ).fetchall()
        ids = [r["id"] for r in rows]

        if ids:
            placeholders = ",".join("?" * len(ids))
            conn.execute(
                f"UPDATE duplicate_clusters SET status = 'confirmed_duplicate', "
                f"decided_at = CURRENT_TIMESTAMP, decided_by = ? WHERE id IN ({placeholders})",
                [decided_by, *ids],
            )
            conn.commit()

    golden_records_created = 0
    merge_errors = []
    for cid in ids:
        try:
            _execute_merge(cid, merged_by=decided_by)
            golden_records_created += 1
        except ValueError as e:
            merge_errors.append({"cluster_id": cid, "error": str(e)})

    result = {
        "dataset_name": dataset_safe_name,
        "tier_confirmed": tier or "all",
        "clusters_confirmed": len(ids),
        "clusters_remaining_pending": get_pending_count(dataset_safe_name),
        "golden_records_created": golden_records_created,
    }
    if merge_errors:
        result["merge_errors"] = merge_errors
    return result


def confirm_high_confidence(payload: dict) -> dict:
    return _bulk_confirm(payload.get("dataset_name"), tier="high_confidence", decided_by=payload.get("decided_by", "unknown"))


def confirm_all_pending(payload: dict) -> dict:
    return _bulk_confirm(payload.get("dataset_name"), tier="all", decided_by=payload.get("decided_by", "unknown"))
