# Classification & PDPL (Item 7, Section 04's 6th page group, first of
# two pages: "Classification & PDPL" + "Data Quality Rules").
#
# Scoped 2026-08-19, confirmed with Khurram before building: Section 02
# names OPA for the policy/enforcement half of this page group. Checked
# directly -- unlike OpenMetadata (Item 6's resource wall), OPA is a
# single small Go binary, genuinely light enough for this tier. But OPA
# is a POLICY ENFORCEMENT engine -- it needs something real to enforce
# against (authenticated users, roles, allow/deny decisions), and that
# doesn't exist yet: there's no login on this dashboard (Item 9,
# multi-tenancy/RBAC, is still unbuilt). Standing up OPA now would only
# evaluate policies with nothing actually gating access -- indistinguishable
# from real enforcement in a demo, but not actually enforcing anything.
# Rather than build that illusion, this module does the real, honest half
# available today: classifying every column's sensitivity for real, and
# computing PDPL completeness per classified column. OPA enforcement is
# deferred until Item 9 exists, and that gap is disclosed on the page
# itself, not hidden -- same pattern as the NDI/PDP disclosures elsewhere
# in this codebase.
#
# Classification is a deterministic, name-based heuristic -- same style
# and same honesty level as dataset_adapter.py's _looks_like_identifier()
# and banking_adapter.py's PII-column detection (email/phone/national_id).
# It can miss an oddly-named sensitive column; it never invents a lower
# sensitivity tier than the name pattern supports. Unmatched columns
# default to INTERNAL, not PUBLIC -- for banking data, "we don't know
# what this is" should never auto-resolve to the least protected tier.

from app.adapters import dataset_adapter
from app.db import get_conn

TIERS = ["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"]

TIER_DESCRIPTIONS = {
    "RESTRICTED": "Highest sensitivity -- national ID, account/IBAN, payment card, income/salary. Direct financial or identity exposure if leaked.",
    "CONFIDENTIAL": "Personal data under PDPL scope -- name, email, phone, address, date of birth, gender.",
    "INTERNAL": "Business/operational data -- not personally identifying on its own, not meant for public release.",
    "PUBLIC": "No sensitivity concern if disclosed.",
}

# Checked first (most sensitive wins on any overlap). Same keyword-
# matching style as dataset_adapter.IDENTIFIER_COL_KEYWORDS -- a
# normalized column name matching, prefixing, or suffixing a keyword.
RESTRICTED_KEYWORDS = [
    "national_id", "ssn", "passport", "iban", "account_number", "credit_card",
    "card_number", "salary", "income",
]
CONFIDENTIAL_KEYWORDS = [
    "email", "phone", "mobile", "address", "dob", "date_of_birth", "birthdate",
    "full_name", "customer_name", "gender", "nationality",
]
# No PUBLIC keyword list -- see module docstring: unmatched columns
# default to INTERNAL, never auto-classified as PUBLIC.


def _normalize(col_name: str) -> str:
    return str(col_name).lower().replace(" ", "_")


def _matches_any(low: str, keywords: list[str]) -> str | None:
    for kw in keywords:
        if low == kw or low.startswith(kw + "_") or low.endswith("_" + kw):
            return kw
    return None


def classify_column(col_name: str) -> dict:
    """Real, deterministic classification for one column name. Returns
    the tier and, if matched, which keyword drove the decision -- so
    the page can show its work, not just a label."""
    low = _normalize(col_name)

    matched = _matches_any(low, RESTRICTED_KEYWORDS)
    if matched:
        return {"tier": "RESTRICTED", "matched_keyword": matched}

    matched = _matches_any(low, CONFIDENTIAL_KEYWORDS)
    if matched:
        return {"tier": "CONFIDENTIAL", "matched_keyword": matched}

    # Reuses dataset_adapter's own identifier heuristic -- an ID/
    # reference-shaped column (CUST_ID, BRANCH_CODE) is business-
    # operational, not public, but also not personal data on its own.
    if dataset_adapter._looks_like_identifier(col_name):
        return {"tier": "INTERNAL", "matched_keyword": None}

    return {"tier": "INTERNAL", "matched_keyword": None}


def classify_dataset(dataset_name: str) -> dict:
    """Every column of one dataset, classified, with real completeness
    per column pulled from the dataset's own recorded null_counts --
    not recomputed from a fresh file read, so this always reflects the
    same Silver-stage numbers the rest of the dashboard shows."""
    listing = dataset_adapter.list_datasets({"dataset_name": dataset_name})
    ds = listing["datasets"][0]
    columns = ds["columns"]
    null_counts = ds["null_counts"]
    total_rows = ds["rows"]

    columns_out = []
    tier_counts = {t: 0 for t in TIERS}
    pii_columns = []

    for col in columns:
        classification = classify_column(col)
        tier = classification["tier"]
        tier_counts[tier] += 1
        null_count = null_counts.get(col, 0)
        completeness_pct = round(100 * (total_rows - null_count) / total_rows, 1) if total_rows else None

        col_entry = {
            "column": col,
            "tier": tier,
            "matched_keyword": classification["matched_keyword"],
            "null_count": null_count,
            "completeness_pct": completeness_pct,
        }
        columns_out.append(col_entry)
        if tier in ("RESTRICTED", "CONFIDENTIAL"):
            pii_columns.append(col_entry)

    if pii_columns and total_rows:
        pii_avg_completeness = round(sum(c["completeness_pct"] for c in pii_columns) / len(pii_columns), 1)
    else:
        pii_avg_completeness = None

    return {
        "dataset_name": dataset_name,
        "total_columns": len(columns),
        "total_rows": total_rows,
        "tier_counts": tier_counts,
        "columns": columns_out,
        "pdpl": {
            "pii_column_count": len(pii_columns),
            "pii_columns": [c["column"] for c in pii_columns],
            "average_completeness_pct": pii_avg_completeness,
            "note": (
                "PDPL completeness is the average non-null rate across every column classified "
                "CONFIDENTIAL or RESTRICTED (PDPL-scoped personal data) -- the same completeness "
                "signal SAMA's PDP domain uses, shown here per-column instead of blended into one "
                "dataset-wide number."
                if pii_columns
                else "No columns in this dataset were classified as CONFIDENTIAL or RESTRICTED."
            ),
        },
        "enforcement_note": (
            "Classification tiers are computed and real. Policy ENFORCEMENT (blocking or masking "
            "access based on these tiers, via OPA) is not built -- it requires authenticated users "
            "and roles to enforce against, which don't exist on this dashboard yet (Item 9, "
            "multi-tenancy/RBAC). Deferred until that exists, not silently skipped."
        ),
        "tier_reference": [
            {"tier": t, "description": TIER_DESCRIPTIONS[t]} for t in TIERS
        ],
    }


def get_coverage_summary() -> dict:
    """Across every dataset, how many RESTRICTED/CONFIDENTIAL columns
    exist -- a small at-a-glance summary, same shape as stewardship's
    coverage summary."""
    with get_conn() as conn:
        rows = conn.execute("SELECT safe_name, display_name FROM datasets").fetchall()

    datasets_out = []
    for r in rows:
        try:
            detail = classify_dataset(r["safe_name"])
        except ValueError:
            continue
        datasets_out.append({
            "dataset_name": r["safe_name"],
            "display_name": r["display_name"],
            "restricted_count": detail["tier_counts"]["RESTRICTED"],
            "confidential_count": detail["tier_counts"]["CONFIDENTIAL"],
        })

    return {"datasets": datasets_out, "dataset_count": len(datasets_out)}
