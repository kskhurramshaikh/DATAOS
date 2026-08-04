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
# tier by confidence, persist them for human review, and gate Gold
# promotion until every group has a decision.
#
# What v1 deliberately does NOT do: execute a merge -- decide which
# record's data "wins" and write one consolidated record back to Gold.
# That's real, separate, riskier logic. Flagged honestly as the next
# iteration, not silently skipped.

import io
import json

import pandas as pd
from rapidfuzz import fuzz

from app.db import get_conn

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


def decide_cluster(cluster_id: int, status: str) -> dict:
    if status not in ("confirmed_duplicate", "not_duplicate"):
        raise ValueError(f"Invalid status '{status}' -- must be 'confirmed_duplicate' or 'not_duplicate'.")

    with get_conn() as conn:
        row = conn.execute("SELECT id FROM duplicate_clusters WHERE id = ?", (cluster_id,)).fetchone()
        if row is None:
            raise ValueError(f"No duplicate cluster found with id {cluster_id}.")
        conn.execute(
            "UPDATE duplicate_clusters SET status = ?, decided_at = CURRENT_TIMESTAMP WHERE id = ?",
            (status, cluster_id),
        )
        conn.commit()

    return {"id": cluster_id, "status": status}


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


def _bulk_confirm(dataset_safe_name: str | None, tier: str | None) -> dict:
    """
    Applies 'confirmed_duplicate' to every PENDING cluster matching the
    given tier ('high_confidence' or None/'all' for every pending
    cluster regardless of tier). This is a batch-apply of a decision the
    human has already made about which tier to trust -- not an
    algorithmic merge decision. If dataset_safe_name isn't given and
    exactly one dataset has pending clusters, that one is used
    automatically; if more than one does, this raises so the caller can
    ask which dataset was meant rather than guessing.
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
                f"decided_at = CURRENT_TIMESTAMP WHERE id IN ({placeholders})",
                ids,
            )
            conn.commit()

    return {
        "dataset_name": dataset_safe_name,
        "tier_confirmed": tier or "all",
        "clusters_confirmed": len(ids),
        "clusters_remaining_pending": get_pending_count(dataset_safe_name),
    }


def confirm_high_confidence(payload: dict) -> dict:
    return _bulk_confirm(payload.get("dataset_name"), tier="high_confidence")


def confirm_all_pending(payload: dict) -> dict:
    return _bulk_confirm(payload.get("dataset_name"), tier="all")
