# Banking Adapter -- NDI readiness and IFRS 9 ECL
#
# Both intents here compute something from raw inputs rather than just
# displaying numbers a source sheet already had. That distinction is the
# whole point: showing a pre-filled number back to someone isn't a
# capability, independently deriving it is.
#
# IFRS 9 ECL: standard formula, ECL = PD x LGD x EAD, summed across the
# portfolio. Verified against a real test file to match the source
# sheet's stated total to the cent -- this is a genuine, exact
# computation, not an approximation.
#
# NDI readiness: deliberately NOT presented as a reproduction of the
# official SDAIA-weighted index. The real methodology's domain
# weightings aren't derivable from a plain scorecard sheet, and
# overclaiming an exact match here would be dishonest in front of
# someone who actually knows the framework. What's computed is real
# (average current vs target score, ranked gaps) -- it's just labeled
# as DataOS's own reading, not an official index reproduction.

import io

import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression, LogisticRegression

from app.adapters import dataset_adapter, dedup_adapter


def _find_column(columns, keywords: list[str]):
    for c in columns:
        low = str(c).lower()
        if any(k in low for k in keywords):
            return c
    return None


def run_ndi(payload: dict) -> dict:
    csv_content = payload.get("csv_content")
    if not csv_content:
        raise ValueError("csv_content is required for an NDI readiness assessment.")

    df = pd.read_csv(io.StringIO(csv_content))

    # Prefer matching columns BY NAME first (English "current"/"target" or
    # Arabic "الحالي"/"مستهدف") -- this is the reliable path. Only fall
    # back to "guess by numeric column position" if no name match is
    # found, and even then, exclude columns that are obviously something
    # else (an index/row-number column, a gap column, a priority score)
    # so the fallback doesn't silently pick the wrong pair. If neither
    # path finds a confident answer, this raises rather than guessing.
    current_col = _find_column(df.columns, ["current", "الحالي"])
    target_col = _find_column(df.columns, ["target", "مستهدف", "هدف"])

    if current_col is None or target_col is None:
        numeric_cols = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
        exclude_keywords = ["#", "index", "no.", "gap", "فجو", "priority", "أولوي"]
        candidate_cols = [
            c for c in numeric_cols if not any(k in str(c).lower() for k in exclude_keywords)
        ]
        if len(candidate_cols) != 2:
            raise ValueError(
                "Could not confidently identify current/target score columns in this sheet -- "
                "expected columns named with 'current'/'target' (or Arabic equivalents), or "
                "exactly two unambiguous numeric score columns."
            )
        current_col, target_col = candidate_cols[0], candidate_cols[1]

    if not pd.api.types.is_numeric_dtype(df[current_col]) or not pd.api.types.is_numeric_dtype(df[target_col]):
        raise ValueError(f"Columns '{current_col}' and '{target_col}' were identified but are not numeric.")

    text_cols = [c for c in df.columns if not pd.api.types.is_numeric_dtype(df[c])]
    domain_col = next((c for c in text_cols if "english" in str(c).lower()), text_cols[-1] if text_cols else None)

    domains_df = df.copy()
    if domain_col:
        domains_df = domains_df[domains_df[domain_col].notna()]
    if domains_df.empty:
        raise ValueError("No domain rows found after filtering out summary/total rows.")

    # Sanity check: if the detected "current" column averages higher than
    # "target", they're almost certainly swapped -- fix rather than report
    # a nonsensical negative gap.
    if domains_df[current_col].mean() > domains_df[target_col].mean():
        current_col, target_col = target_col, current_col

    domain_names = domains_df[domain_col].tolist() if domain_col else [f"Domain {i + 1}" for i in range(len(domains_df))]
    avg_current = float(domains_df[current_col].mean())
    avg_target = float(domains_df[target_col].mean())
    computed_index_0_100 = round(avg_current / avg_target * 100, 1) if avg_target else None

    gaps = sorted(
        (
            {
                "domain": str(d),
                "current_score": round(float(c), 2),
                "target_score": round(float(t), 2),
                "gap": round(float(t) - float(c), 2),
            }
            for d, c, t in zip(domain_names, domains_df[current_col], domains_df[target_col])
        ),
        key=lambda x: x["gap"],
        reverse=True,
    )

    return {
        "domain_count": len(domains_df),
        "average_current_score": round(avg_current, 2),
        "average_target_score": round(avg_target, 2),
        "computed_readiness_index_0_100": computed_index_0_100,
        "methodology_note": (
            "Computed as (average current score / average target score) x 100. This is DataOS's "
            "own reading of the raw scorecard -- it is not presented as a reproduction of any "
            "official weighted index, since the real methodology's domain weightings aren't "
            "derivable from a plain scorecard sheet alone."
        ),
        "top_gap_domains": gaps[:5],
    }


# ---------------------------------------------------------------------
# SAMA compliance and Customer 360 -- both cross-cutting views built
# from data DataOS already computes elsewhere (dataset quality metrics,
# the duplicate-review audit log), not new measurements of their own.
# SAMA's official 8 compliance domains are DG, DQ, DIS, RMD, DC, PDP,
# BIA, DS -- DataOS only has a real, computed signal for four of them
# today (DQ, PDP, RMD, DG). The other four (DIS, DC, BIA, DS) are
# marked "not_measured" rather than given an invented score -- same
# principle as the IFRS 9 training-data disclosure and the NDI
# methodology-gap note: a specific-looking percentage for something
# DataOS has no actual telemetry for would be worse than admitting the
# gap outright.
# ---------------------------------------------------------------------

def compute_sama_compliance(dataset_name: str) -> dict:
    ds = dataset_adapter.list_datasets({"dataset_name": dataset_name})["datasets"][0]
    total_records = ds["rows"]
    null_counts = ds["null_counts"]
    columns = ds["columns"]

    # DQ -- overall data-quality reading: average null rate across the
    # columns that actually have any nulls, inverted to a "clean %".
    if null_counts and total_records:
        avg_null_rate = sum(null_counts.values()) / (len(columns) * total_records)
        dq_score = round(max(0.0, 100 - avg_null_rate * 100), 1)
    else:
        dq_score = 100.0

    # PDP (PDPL) -- completeness specifically on PII-bearing columns
    # (email/phone/national ID), since that's what PDPL actually
    # governs -- a generic null rate isn't the same signal.
    pii_cols = [c for c in columns if any(k in c.lower() for k in ("email", "phone", "national_id"))]
    if pii_cols and total_records:
        pii_null_total = sum(null_counts.get(c, 0) for c in pii_cols)
        pdp_score = round(max(0.0, 100 - 100 * pii_null_total / (len(pii_cols) * total_records)), 1)
    else:
        pdp_score = None

    check_never_run = ds.get("duplicate_check_last_run_at") is None

    pending = dedup_adapter.get_pending_count(dataset_name)
    decided = dedup_adapter.get_audit_log(dataset_name, limit=10000)
    total_clusters = pending + len(decided)

    # DG -- governance-process completion: how much of the duplicate
    # review queue has actually been decided, not left pending.
    # IMPORTANT: total_clusters == 0 is genuinely ambiguous on its own --
    # it means either "fully resolved" or "detection was never run in
    # the first place" (an untouched dataset has zero rows in
    # duplicate_clusters either way). Defaulting that to 100%/"OK" would
    # tell a bank exec their duplicate-review governance is complete
    # when nobody has even checked yet -- exactly the kind of false
    # claim this view exists to avoid. duplicate_check_last_run_at is
    # the real signal that disambiguates the two.
    if check_never_run:
        dg_score = None
    elif total_clusters:
        dg_score = round(100 * len(decided) / total_clusters, 1)
    else:
        dg_score = 100.0  # checked, and genuinely found nothing to review

    # RMD -- record-impact weighted, not the same ratio as DG: what
    # share of the dataset's actual records currently sit in an
    # unresolved duplicate cluster, not just what fraction of review
    # WORK is done. A few large unresolved clusters hurt this more
    # than many small ones, even at the same cluster count -- DG and
    # RMD measuring the identical decided/total ratio would make them
    # numerically indistinguishable in every run, undermining the
    # point of showing two separate domains. Same never-run caveat as
    # DG applies here too.
    if check_never_run:
        rmd_score = None
    else:
        pending_clusters = dedup_adapter.get_pending_clusters(dataset_name)
        records_in_pending = sum(len(c["members"]) for c in pending_clusters)
        rmd_score = round(100 * max(0, total_records - records_in_pending) / total_records, 1) if total_records else 100.0

    domain_scores = [
        {"code": "DG", "name": "Data Governance", "score": dg_score, "status": "not_measured" if dg_score is None else "measured"},
        {"code": "DQ", "name": "Data Quality", "score": dq_score, "status": "measured"},
        {"code": "DIS", "name": "Data Integration & Sharing", "score": None, "status": "not_measured"},
        {"code": "RMD", "name": "Risk Management Data", "score": rmd_score, "status": "not_measured" if rmd_score is None else "measured"},
        {"code": "DC", "name": "Data Classification", "score": None, "status": "not_measured"},
        {"code": "PDP", "name": "Personal Data Protection", "score": pdp_score, "status": "measured" if pdp_score is not None else "not_measured"},
        {"code": "BIA", "name": "Business Impact Assessment", "score": None, "status": "not_measured"},
        {"code": "DS", "name": "Data Security", "score": None, "status": "not_measured"},
    ]

    modeling_cols = ["RATING", "FACILITY_TYPE", "DPD", "ORIGINATION", "MATURITY", "EAD"]
    ifrs9_ready = all(c in columns for c in modeling_cols)

    checks = [
        {
            "label": "Data Governance",
            "status": "not_measured" if check_never_run else ("ok" if dg_score >= 80 else "warn"),
            "value": "Duplicate detection not yet run" if check_never_run else f"{dg_score}% of duplicate reviews decided",
        },
        {
            "label": "MDM",
            "status": "not_measured" if check_never_run else ("ok" if pending == 0 else "warn"),
            "value": "Duplicate detection not yet run" if check_never_run else f"{pending} duplicate cluster(s) still pending review",
        },
        {"label": "Data Classification", "status": "not_measured", "value": "Not yet instrumented"},
        {"label": "PDPL", "status": "ok" if (pdp_score or 0) >= 90 else "warn", "value": f"{pdp_score}% PII field completeness" if pdp_score is not None else "No PII columns detected"},
        {"label": "IFRS 9 + Basel III readiness", "status": "ok" if ifrs9_ready else "warn", "value": "Modeling columns present" if ifrs9_ready else "Missing required modeling columns"},
    ]

    if check_never_run:
        priority_alert = "Duplicate detection hasn't been run on this dataset yet -- DG and RMD can't be assessed until it has. Run \"Find duplicate customers\" first."
    elif pending > 0:
        priority_alert = f"RMD domain: {pending} unresolved duplicate cluster(s) still need review -- affects reliability of downstream risk figures until decided."
    else:
        measured = [d for d in domain_scores if d["status"] == "measured"]
        lowest = min(measured, key=lambda d: d["score"]) if measured else None
        priority_alert = f"{lowest['name']} is the lowest-scoring measured domain at {lowest['score']}%." if lowest else "No measured domains yet -- upload and review a dataset first."

    return {
        "checks": checks,
        "domain_scores": domain_scores,
        "priority_alert": priority_alert,
        "methodology_note": (
            "DG, DQ, RMD, and PDP are computed from real signals already tracked by DataOS -- "
            "the duplicate-review audit log and dataset null-rate metrics, not invented figures. "
            "DIS, DC, BIA, and DS have no corresponding measurement in DataOS today and are shown "
            "as not_measured rather than given a placeholder score."
        ),
    }


# ---------------------------------------------------------------------
# NDI Radar View -- real SDAIA NDI v1.1 methodology (14 domains, 191
# specs, official weights and 6-level maturity scale), per Dr. Saber's
# DataOS2_NDI_Methodology_Spec.pdf (2026-08-09). Domain weights,
# maturity/OE scales, compliance thresholds, and formulas below are
# his exact stated values, not guessed.
#
# The per-domain maturity/compliance INPUTS are his preset BAJ demo
# baseline (Section 2.6) -- an explicit instruction for this component,
# unlike IFRS 9/SAMA/Customer 360 which all compute their inputs from
# real uploaded data. Flagged for visibility, not silently normalized
# to match the other components' pattern.
#
# IMPORTANT: applying his exact formula to his exact baseline table
# produces 48.27/100 display score, 60.21% compliance, "Developing"
# level -- NOT the 52.3/100, 63.4%, "Emerging" his document states as
# the target. Verified twice by hand and once programmatically before
# concluding this; not a rounding difference. Rather than silently
# force the output to match his stated target (which would mean either
# not implementing the real formula, or guessing at which domain score
# has a typo), this returns what the formula actually computes, with
# both figures disclosed so the discrepancy is visible, not hidden.
# ---------------------------------------------------------------------

NDI_DOMAINS = [
    # code, name, spec_count, is_oe_domain
    ("DG", "Data Governance", 18, False),
    ("MDC", "Metadata & Data Catalogue", 14, True),
    ("DQ", "Data Quality", 20, True),
    ("DS", "Data Storage", 12, True),
    ("CDM", "Content & Document Management", 10, False),
    ("DMD", "Data Modelling & Architecture", 8, False),
    ("DIS", "Data Integration & Sharing", 20, True),
    ("RMD", "Reference & Master Data", 15, True),
    ("BIA", "Business Intelligence & Analytics", 16, False),
    ("DVR", "Data Value Realisation", 14, False),
    ("OD", "Open Data", 8, True),
    ("FOI", "Freedom of Information", 10, False),
    ("DC", "Data Classification", 16, False),
    ("PDP", "Personal Data Protection", 10, False),
]

NDI_OE_WEIGHT_PCT = 100 / 6  # 16.6667%, equal across the 6 OE domains

NDI_MATURITY_LEVELS = [
    (0.00, 0.24, "Capability No"),
    (0.25, 1.24, "Emerging"),
    (1.25, 2.49, "Developing"),
    (2.50, 3.99, "Defined"),
    (4.00, 4.74, "Managed"),
    (4.75, 5.00, "Leading"),
]

# Dr. Saber's preset BAJ demo baseline (DataOS2_NDI_Methodology_Spec.pdf,
# Section 2.6) -- fixed per-domain maturity score (0-5) and compliance %,
# not derived from an uploaded file.
NDI_BAJ_BASELINE = {
    "DG": {"maturity": 2.8, "compliance_pct": 67, "evidence": "DGPM policy framework"},
    "MDC": {"maturity": 2.0, "compliance_pct": 50, "evidence": "Partial data catalogue"},
    "DQ": {"maturity": 2.5, "compliance_pct": 60, "evidence": "Automated DQ checks"},
    "DS": {"maturity": 3.2, "compliance_pct": 75, "evidence": "Tier-1 storage compliant"},
    "CDM": {"maturity": 2.1, "compliance_pct": 50, "evidence": "SharePoint-based DMS"},
    "DMD": {"maturity": 2.3, "compliance_pct": 50, "evidence": "Enterprise data model v2"},
    "DIS": {"maturity": 2.7, "compliance_pct": 65, "evidence": "API gateway 60% coverage"},
    "RMD": {"maturity": 1.8, "compliance_pct": 47, "evidence": "340K duplicates pending"},
    "BIA": {"maturity": 3.0, "compliance_pct": 75, "evidence": "Tableau + PowerBI deployed"},
    "DVR": {"maturity": 2.4, "compliance_pct": 57, "evidence": "Value tracking in progress"},
    "OD": {"maturity": 1.5, "compliance_pct": 38, "evidence": "Limited open data published"},
    "FOI": {"maturity": 2.0, "compliance_pct": 50, "evidence": "FOI portal live"},
    "DC": {"maturity": 2.6, "compliance_pct": 69, "evidence": "65% systems classified"},
    "PDP": {"maturity": 2.9, "compliance_pct": 70, "evidence": "DPIA in progress"},
}


def _ndi_domain_weight_pct(code: str) -> float:
    # All 14 domains weighted equally at 7.14%, except PDP adjusted to
    # 7.1423% so the 14 weights sum to exactly 100% -- his exact stated
    # rounding correction, not ours.
    return 7.1423 if code == "PDP" else 7.14


def _ndi_maturity_level(score: float) -> str:
    for lo, hi, name in NDI_MATURITY_LEVELS:
        if lo <= score <= hi:
            return name
    return "Capability No"


def _ndi_compliance_status(pct: float) -> str:
    if pct >= 80:
        return "high"
    if pct >= 50:
        return "medium"
    return "low"


def compute_ndi_assessment() -> dict:
    domains_out = []
    weighted_maturity_sum = 0.0
    oe_weighted_sum = 0.0
    total_compliant_specs = 0
    total_specs = 0

    for code, name, spec_count, is_oe in NDI_DOMAINS:
        baseline = NDI_BAJ_BASELINE[code]
        weight_pct = _ndi_domain_weight_pct(code)
        maturity = baseline["maturity"]
        compliance_pct = baseline["compliance_pct"]

        weighted_maturity_sum += maturity * weight_pct
        if is_oe:
            oe_weighted_sum += maturity * NDI_OE_WEIGHT_PCT
        total_compliant_specs += round(spec_count * compliance_pct / 100)
        total_specs += spec_count

        domains_out.append({
            "code": code,
            "name": name,
            "spec_count": spec_count,
            "maturity_score": maturity,
            "compliance_pct": compliance_pct,
            "compliance_status": _ndi_compliance_status(compliance_pct),
            "is_oe_domain": is_oe,
            "evidence": baseline["evidence"],
        })

    overall_maturity_score = round(weighted_maturity_sum / 100, 3)
    display_score = round(overall_maturity_score * 20, 1)
    maturity_level = _ndi_maturity_level(overall_maturity_score)
    overall_compliance_pct = round(100 * total_compliant_specs / total_specs, 1)
    overall_oe_score = round(oe_weighted_sum / 100, 3)

    return {
        "overall_maturity_score": overall_maturity_score,
        "display_score": display_score,
        "maturity_level": maturity_level,
        "overall_compliance_pct": overall_compliance_pct,
        "overall_oe_score": overall_oe_score,
        "total_specs": total_specs,
        "compliant_specs": total_compliant_specs,
        "domains": domains_out,
        "methodology_note": (
            "Domain weights (7.14% x 13 + 7.1423% PDP), the 6-level maturity scale, the OE "
            "domain set, and all scoring formulas are the official SDAIA NDI v1.1 methodology, "
            "provided directly by Dr. Saber. The per-domain maturity/compliance inputs are his "
            "preset BAJ demo baseline, not computed from an uploaded file -- unlike IFRS 9, "
            "SAMA, and Customer 360, which compute their inputs from real uploaded data. "
            "These are the official baseline display values, confirmed by Dr. Saber's own "
            "independent re-derivation (2026-08-09) -- his spec's original stated totals "
            "(52.3 / 63.4% / \"Emerging\") were superseded as an error in his document, not "
            "in this computation; the 14 domain scores were always correct."
        ),
    }


def compute_customer_360(dataset_name: str) -> dict:
    ds = dataset_adapter.list_datasets({"dataset_name": dataset_name})["datasets"][0]
    total_records = ds["rows"]
    check_never_run = ds.get("duplicate_check_last_run_at") is None

    entries = dedup_adapter.get_audit_log(dataset_name, limit=10000)
    confirmed = [e for e in entries if e["status"] == "confirmed_duplicate"]
    # Each confirmed cluster of N members represents N-1 "extra" records
    # that a real golden-record merge would collapse into one -- golden-
    # record merge execution itself isn't built yet (see change log item
    # 2), so this is a projection based on real review decisions, not an
    # executed count.
    #
    # IMPORTANT: if duplicate detection has never been run, "0 confirmed
    # duplicates" is ambiguous between "genuinely clean dataset" and
    # "nobody has checked yet" -- defaulting to a perfect 100% uniqueness
    # score in the unchecked case is the exact same false-confidence bug
    # SAMA had (see compute_sama_compliance's check_never_run handling).
    if check_never_run:
        golden_records_estimate = None
        uniqueness_ratio = None
    else:
        extra_records = sum(max(len(e["members"]) - 1, 0) for e in confirmed)
        golden_records_estimate = max(total_records - extra_records, 0)
        uniqueness_ratio = round(100 * golden_records_estimate / total_records, 1) if total_records else None

    quality_trend = None
    if not check_never_run and uniqueness_ratio is not None:
        # No real historical tracking exists yet (see note below) -- this
        # is a clearly-labeled illustrative trend, not real history. The
        # only real number in it is the last point (today's actual
        # uniqueness_ratio); the 5 earlier points are a synthetic,
        # plausible improvement curve leading up to it, generated for
        # demo purposes per the original spec's request -- not fabricated
        # to look like real tracking, and disclosed as such directly on
        # the chart, not just in a footnote.
        start = max(0.0, uniqueness_ratio - 8.0)
        step = (uniqueness_ratio - start) / 5
        quality_trend = [
            {"month": label, "uniqueness_pct": round(start + step * i, 1)}
            for i, label in enumerate(["5 months ago", "4 months ago", "3 months ago", "2 months ago", "Last month", "This month"])
        ]
        quality_trend[-1]["uniqueness_pct"] = uniqueness_ratio  # exact real value, not rounded-through synthetic math

    return {
        "total_records": total_records,
        "golden_records_estimate": golden_records_estimate,
        "uniqueness_ratio": uniqueness_ratio,
        "uniqueness_target": 99.0,
        "duplicate_clusters_confirmed": len(confirmed) if not check_never_run else None,
        "duplicate_records_involved": (sum(max(len(e["members"]) - 1, 0) for e in confirmed) if not check_never_run else None),
        "check_never_run": check_never_run,
        "quality_trend": quality_trend,
        "note": (
            "Duplicate detection hasn't been run on this dataset yet -- golden-record and "
            "uniqueness figures can't be assessed until it has. Run \"Find duplicate customers\" first."
        ) if check_never_run else (
            "golden_records_estimate assumes every confirmed duplicate group would collapse to "
            "one record if golden-record merge were executed -- that merge step isn't built yet, "
            "so this is a projection from real review decisions, not an executed count. The "
            "6-month trend is illustrative demo data, not real historical tracking -- only "
            "\"This month\" is a real measured value; DataOS doesn't track data-quality history "
            "over time yet."
        ),
    }


def run_ifrs9(payload: dict) -> dict:
    csv_content = payload.get("csv_content")
    if not csv_content:
        raise ValueError("csv_content is required for an IFRS 9 ECL computation.")

    df = pd.read_csv(io.StringIO(csv_content))

    modeling_cols = ["RATING", "FACILITY_TYPE", "DPD", "ORIGINATION", "MATURITY", "EAD"]
    has_modeling_cols = all(c in df.columns for c in modeling_cols)

    customer_lookup = None
    customer_csv_content = payload.get("customer_csv_content")
    if customer_csv_content:
        customer_lookup = _build_customer_name_lookup(customer_csv_content)

    if has_modeling_cols:
        return _run_ifrs9_modeled(df, payload.get("scenario", "base"), customer_lookup)
    return _run_ifrs9_simple_aggregation(df)


def _build_customer_name_lookup(customer_csv_content: str) -> dict | None:
    """Builds a CUST_ID -> name map from a separate customer sheet
    (e.g. Customer_MDM), for joining real names onto the IFRS 9 sheet's
    top_5_risk table when the loan sheet itself has no name column --
    only when an EXACT 'cust_id' column exists on the customer side
    (confirmed against the real Banking_Demo_Dataset.xlsx: CUST_ID is
    the actual shared key between Customer_MDM and IFRS9_Portfolio;
    NATIONAL_ID only exists on the customer sheet, not the loan sheet).
    Returns None (never guesses) if it doesn't."""
    try:
        cust_df = pd.read_csv(io.StringIO(customer_csv_content))
    except Exception:
        return None
    id_col = _find_column_exact(cust_df.columns, ["cust_id"])
    name_col = _find_column(cust_df.columns, ["full_name", "name", "customer_name"])
    if not id_col or not name_col:
        return None
    return {
        str(row[id_col]): str(row[name_col])
        for _, row in cust_df.iterrows()
        if pd.notna(row[id_col]) and pd.notna(row[name_col])
    }


def _find_column_exact(columns, keywords: list[str]):
    """Like _find_column, but only matches a column whose normalized
    name IS one of the keywords, not just contains it as a substring --
    for join keys, where 'loan_id' accidentally containing 'id' as a
    substring would be a wrong, silent mismatch, not just an imprecise
    one."""
    for c in columns:
        if str(c).lower().replace(" ", "_") in keywords:
            return c
    return None


def _run_ifrs9_simple_aggregation(df: pd.DataFrame) -> dict:
    """
    Fallback for files that don't have enough loan attributes (rating,
    facility type, DPD, dates) to actually model PD/LGD from -- just
    aggregates whatever PD/LGD/EAD the file already provides. This is
    the "aggregation, not modeling" behavior Dr. Saber's assessment
    correctly identified as insufficient on its own -- kept only as a
    graceful degradation path, not the primary approach anymore.
    """
    required = ["PD", "LGD", "EAD"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns for ECL computation: {missing}")

    df["computed_ecl"] = df["PD"] * df["LGD"] * df["EAD"]
    total_computed_ecl = float(df["computed_ecl"].sum())
    total_ead = float(df["EAD"].sum())

    result = {
        "loan_count": len(df),
        "total_exposure_ead": round(total_ead, 2),
        "total_computed_ecl": round(total_computed_ecl, 2),
        "coverage_ratio": round(total_computed_ecl / total_ead, 4) if total_ead else None,
        "methodology_note": (
            "This file doesn't have the loan attributes (rating, facility type, days past due, "
            "origination/maturity dates) needed to model PD/LGD -- so this aggregates the PD/LGD "
            "values already present in the file (ECL = PD x LGD x EAD, summed), rather than "
            "computing them independently."
        ),
    }

    if "STAGE" in df.columns:
        stage_counts = df["STAGE"].value_counts().sort_index()
        result["loans_by_stage"] = {str(k): int(v) for k, v in stage_counts.items()}

    if "ECL_SAR" in df.columns:
        stated_total = float(df["ECL_SAR"].sum())
        result["stated_total_ecl_in_source"] = round(stated_total, 2)
        result["matches_source_figure"] = abs(stated_total - total_computed_ecl) < max(1.0, stated_total * 0.001)

    return result


# ---------------------------------------------------------------------
# PD and LGD modeling -- real scikit-learn model fits (LogisticRegression
# for PD, LinearRegression for LGD), the standard textbook approach for
# PD scorecards and LGD estimation. This is genuine orchestration: the
# actual probability/severity estimation is scikit-learn's math, not
# ours.
#
# What's still illustrative -- and clearly labeled as such -- is the
# TRAINING DATA these models are fit on. No real historical default or
# recovery history exists yet (Dr. Saber was asked directly for this;
# see the change log), so each model is trained on a small synthetic
# dataset constructed to average out near a plausible target rate per
# rating grade / facility type. The moment real historical data arrives,
# only the training-data construction below needs to change -- the
# model-fitting code itself doesn't.
# ---------------------------------------------------------------------

RATING_ORDER = ["AAA", "AA", "A", "BBB", "BB", "B", "CCC", "CC", "C", "D"]

ILLUSTRATIVE_TARGET_PD_BY_RATING = {
    # Base (Most Likely) 12-month PD, exactly as specified in Dr. Saber's
    # DataOS_IFRS9_Parameters file (PD Lookup Table by Credit Rating,
    # referenced from SAMA Financial Stability Reports). His table
    # doesn't include CC/C grades (it has 8: AAA/AA/A/BBB/BB/B/CCC/D) --
    # our data has 10, so CC and C are geometrically interpolated
    # between his CCC (25%) and D (100%) values, disclosed as such below.
    "AAA": 0.0010, "AA": 0.0025, "A": 0.0060, "BBB": 0.0150, "BB": 0.0400,
    "B": 0.1000, "CCC": 0.2500,
    "CC": 0.3969,   # interpolated between Dr. Saber's CCC (25%) and D (100%) -- not in his table
    "C": 0.6300,    # interpolated between Dr. Saber's CCC (25%) and D (100%) -- not in his table
    "D": 1.0000,
}
RATINGS_INTERPOLATED_NOT_IN_SABER_TABLE = ["CC", "C"]

# LGD by facility type -- Dr. Saber's exact stated rates from his
# DataOS_IFRS9_Parameters file (LGD Parameters by Facility Type sheet),
# not our own invention. All 5 facility types present in this dataset
# now have a direct, named rate from his file -- nothing left as an
# unresolved placeholder.
ILLUSTRATIVE_TARGET_LGD_BY_FACILITY = {
    "تمويل عقاري": 0.25,      # Real Estate Financing -- strong collateral, ~75% recovery
    "تمويل مشاريع": 0.35,     # Project Financing -- structured, asset-backed but illiquid
    "تمويل مركبات": 0.40,     # Vehicle Financing -- depreciating asset (not in this file's data, kept for completeness)
    "تمويل تجاري": 0.45,      # Commercial Financing -- mixed collateral, harder to liquidate
    "تمويل شخصي": 0.30,       # Personal/Salary-Based Financing -- salary assignment, predictable recovery
    "بطاقة ائتمانية": 0.65,    # Credit Card / Unsecured -- no collateral, lowest recovery
}
DEFAULT_LGD = 0.55  # used only for a facility type never seen in training

# Macro scenario definitions -- Dr. Saber's exact structure from his
# DataOS_IFRS9_Parameters file (Macroeconomic Scenarios sheet), sourced
# by him to SAMA Quarterly Economic Reports. Includes both a PD
# multiplier AND an LGD multiplier per scenario -- LGD isn't scenario-
# invariant either (collateral recovers less in a downturn too), which
# our earlier version didn't model. Probability weights are included
# for a probability-weighted "expected ECL across scenarios" figure.
MACRO_SCENARIOS = {
    "optimistic": {
        "pd_multiplier": 0.75,
        "lgd_multiplier": 0.90,
        "probability_weight": 0.25,
        "description": (
            "Per Dr. Saber's DataOS_IFRS9_Parameters file: GDP +3.5%, oil $90+ per barrel, "
            "unemployment 4.5%, credit growth +8%. Source: SAMA Quarterly Economic Reports."
        ),
    },
    "base": {
        "pd_multiplier": 1.0,
        "lgd_multiplier": 1.0,
        "probability_weight": 0.55,
        "description": (
            "Per Dr. Saber's DataOS_IFRS9_Parameters file (\"Most Likely\"): GDP +2.0%, oil $75 "
            "per barrel, unemployment 6.0%, credit growth +4%. Source: SAMA Quarterly Economic "
            "Reports. Real context from SAMA's 2025 Financial Stability Report: actual 2024 GDP "
            "growth was 2.7%, unemployment 7.4% (historic low), and the aggregate Saudi banking "
            "sector NPL ratio was 1.2% (down from 1.5% in 2023) -- a real, citable sector-wide "
            "figure, though not directly comparable to this portfolio's own modeled PD."
        ),
    },
    "adverse": {
        "pd_multiplier": 1.50,
        "lgd_multiplier": 1.15,
        "probability_weight": 0.20,
        "description": (
            "Per Dr. Saber's DataOS_IFRS9_Parameters file: GDP -1.0%, oil $50 per barrel, "
            "unemployment 9.0%, credit growth -2%. Source: SAMA Quarterly Economic Reports. "
            "Directionally consistent with SAMA's own published adverse stress-test narrative "
            "(2025 Financial Stability Report, Box 3.1): oil price weakness, rising "
            "unemployment, and credit risk intensifying, particularly in real estate."
        ),
    },
}

_pd_model = None
_lgd_model = None
_lgd_facility_columns = None


def _build_pd_model():
    """Fits a LogisticRegression PD scorecard on illustrative synthetic
    default data by rating grade. Cached module-wide so repeated calls
    don't refit from scratch."""
    global _pd_model
    if _pd_model is not None:
        return _pd_model

    rng = np.random.default_rng(42)
    samples_per_grade = 300
    X, y = [], []
    for rank, rating in enumerate(RATING_ORDER):
        target_rate = ILLUSTRATIVE_TARGET_PD_BY_RATING[rating]
        defaults = rng.random(samples_per_grade) < target_rate
        X.extend([[rank]] * samples_per_grade)
        y.extend(defaults.astype(int))

    model = LogisticRegression()
    model.fit(X, y)
    _pd_model = model
    return _pd_model


def _predict_pd(ratings: pd.Series) -> tuple[pd.Series, pd.Series]:
    model = _build_pd_model()
    rank_map = {r: i for i, r in enumerate(RATING_ORDER)}
    ranks = ratings.map(rank_map)
    unknown_mask = ranks.isna()
    ranks_filled = ranks.fillna(rank_map["B"]).astype(int)  # mid-range fallback for an unrecognized grade
    probs = model.predict_proba(ranks_filled.to_numpy().reshape(-1, 1))[:, 1]
    return pd.Series(probs, index=ratings.index), unknown_mask


def _build_lgd_model():
    """Fits a LinearRegression LGD estimator on illustrative synthetic
    recovery severity data by facility type."""
    global _lgd_model, _lgd_facility_columns
    if _lgd_model is not None:
        return _lgd_model, _lgd_facility_columns

    rng = np.random.default_rng(43)
    samples_per_type = 200
    rows, targets = [], []
    for ftype, target in ILLUSTRATIVE_TARGET_LGD_BY_FACILITY.items():
        severities = np.clip(rng.normal(target, 0.05, samples_per_type), 0.01, 0.99)
        rows.extend([ftype] * samples_per_type)
        targets.extend(severities)

    dummies = pd.get_dummies(pd.Series(rows, name="facility_type"))
    model = LinearRegression()
    model.fit(dummies.to_numpy(), targets)
    _lgd_model = model
    _lgd_facility_columns = list(dummies.columns)
    return _lgd_model, _lgd_facility_columns


def _predict_lgd(facility_types: pd.Series) -> tuple[pd.Series, pd.Series]:
    model, columns = _build_lgd_model()
    unknown_mask = ~facility_types.isin(columns)
    dummies = pd.get_dummies(facility_types).reindex(columns=columns, fill_value=0)
    preds = np.clip(model.predict(dummies.to_numpy()), 0.05, 0.95)
    result = pd.Series(preds, index=facility_types.index)
    result[unknown_mask] = DEFAULT_LGD  # never-seen facility type -- conservative fallback, not extrapolation
    return result, unknown_mask


def _compute_staging(df: pd.DataFrame) -> tuple[pd.Series, list[str], list[str]]:
    """
    Computes IFRS 9 staging using Dr. Saber's multi-trigger SICR rules
    (DataOS_IFRS9_Parameters, Loan Staging Rules sheet), evaluated where
    the underlying data actually supports them:
    - Stage 2: DPD > 30, OR a 2-notch rating downgrade, OR a watchlist
      flag, OR the borrower requests restructuring
    - Stage 3: DPD > 90, OR the loan is formally restructured, OR legal
      proceedings are initiated, OR a formal default is declared
    The DPD backstop (30/90 days) is a real, standard rule and is
    always evaluated. The other triggers only fire if the corresponding
    column exists in the uploaded file -- this keeps the logic honest
    about what it actually checked, rather than silently assuming a
    trigger fired when the data to evaluate it doesn't exist.
    """
    stage = df["DPD"].apply(lambda d: 3 if d > 90 else (2 if d >= 30 else 1))
    evaluated = ["DPD backstop (30/90 days past due)"]
    not_evaluated = []

    watchlist_col = _find_column(df.columns, ["watchlist"])
    if watchlist_col:
        flagged = df[watchlist_col].fillna(False).astype(bool)
        stage = stage.where(~(flagged & (stage == 1)), 2)
        evaluated.append("watchlist flag")
    else:
        not_evaluated.append("watchlist flag (no watchlist column in this file)")

    restructure_request_col = _find_column(df.columns, ["restructure_request", "restructuring_requested"])
    if restructure_request_col:
        flagged = df[restructure_request_col].fillna(False).astype(bool)
        stage = stage.where(~(flagged & (stage == 1)), 2)
        evaluated.append("borrower-requested restructuring flag")
    else:
        not_evaluated.append("borrower-requested restructuring flag (no such column in this file)")

    restructured_col = _find_column(df.columns, ["restructured", "restructuring"])
    if restructured_col:
        flagged = df[restructured_col].fillna(False).astype(bool)
        stage = stage.where(~flagged, 3)
        evaluated.append("formally restructured flag")
    else:
        not_evaluated.append("formally restructured flag (no restructuring column in this file)")

    legal_col = _find_column(df.columns, ["legal_proceedings", "legal_action"])
    if legal_col:
        flagged = df[legal_col].fillna(False).astype(bool)
        stage = stage.where(~flagged, 3)
        evaluated.append("legal proceedings initiated flag")
    else:
        not_evaluated.append("legal proceedings initiated flag (no such column in this file)")

    default_col = _find_column(df.columns, ["formal_default", "default_flag", "is_default"])
    if default_col:
        flagged = df[default_col].fillna(False).astype(bool)
        stage = stage.where(~flagged, 3)
        evaluated.append("formal default flag")
    else:
        not_evaluated.append("formal default flag (no default-status column in this file)")

    # A 2-notch rating downgrade requires an origination-time rating to
    # compare against the current one -- this file only has the current
    # RATING, not a rating history, so this trigger can never fire here
    # regardless of column-name matching.
    not_evaluated.append("2-notch rating downgrade (needs an origination-time rating, not present in this file)")

    return stage, evaluated, not_evaluated


def _run_ifrs9_modeled(df: pd.DataFrame, scenario: str, customer_lookup: dict | None = None) -> dict:
    if scenario not in MACRO_SCENARIOS:
        raise ValueError(f"Unknown scenario '{scenario}' -- choose one of: {list(MACRO_SCENARIOS.keys())}")

    origination = pd.to_datetime(df["ORIGINATION"], errors="coerce")
    maturity = pd.to_datetime(df["MATURITY"], errors="coerce")
    term_years = ((maturity - origination).dt.days / 365.25).clip(lower=0.1)

    base_pd, unknown_rating_mask = _predict_pd(df["RATING"])
    unknown_ratings = df.loc[unknown_rating_mask, "RATING"].unique().tolist()

    computed_stage, staging_triggers_evaluated, staging_triggers_not_evaluated = _compute_staging(df)

    # Stage 1 uses 12-month PD directly; Stage 2/3 use a lifetime
    # (cumulative) PD over the loan's remaining term -- the actual
    # methodological distinction IFRS 9 requires and the earlier
    # aggregation-only approach didn't make at all.
    lifetime_pd = 1 - (1 - base_pd) ** term_years
    effective_pd = pd.Series(
        [lt if stage > 1 else bp for bp, lt, stage in zip(base_pd, lifetime_pd, computed_stage)],
        index=df.index,
    )

    base_lgd, unknown_facility_mask = _predict_lgd(df["FACILITY_TYPE"])
    unknown_facilities = df.loc[unknown_facility_mask, "FACILITY_TYPE"].unique().tolist()

    ead = df["EAD"]

    # Compute ECL under all three scenarios -- needed both for the
    # requested scenario's result and for the probability-weighted
    # expected-ECL figure across all of them (Dr. Saber's parameter
    # file includes scenario probability weights specifically for this).
    ecl_by_scenario = {}
    for scenario_name, scenario_params in MACRO_SCENARIOS.items():
        scenario_pd = (effective_pd * scenario_params["pd_multiplier"]).clip(upper=1.0)
        scenario_lgd = (base_lgd * scenario_params["lgd_multiplier"]).clip(upper=1.0)
        ecl_by_scenario[scenario_name] = float((scenario_pd * scenario_lgd * ead).sum())

    effective_pd = (effective_pd * MACRO_SCENARIOS[scenario]["pd_multiplier"]).clip(upper=1.0)
    lgd = (base_lgd * MACRO_SCENARIOS[scenario]["lgd_multiplier"]).clip(upper=1.0)

    expected_ecl_across_scenarios = sum(
        ecl_by_scenario[name] * params["probability_weight"] for name, params in MACRO_SCENARIOS.items()
    )

    computed_ecl = effective_pd * lgd * ead

    total_computed_ecl = float(computed_ecl.sum())
    total_ead = float(ead.sum())

    # PD and LGD are the whole point of an IFRS 9 engine, not just
    # inputs hidden inside the final ECL number -- surface them as
    # first-class results: the modeled rate per rating/facility type
    # actually present in this portfolio, plus EAD-weighted portfolio
    # averages.
    pd_model = _build_pd_model()
    modeled_pd_by_rating = {}
    for rating in sorted(df["RATING"].dropna().unique(), key=lambda r: RATING_ORDER.index(r) if r in RATING_ORDER else len(RATING_ORDER)):
        if rating in RATING_ORDER:
            rank = RATING_ORDER.index(rating)
            pd_value = float(pd_model.predict_proba([[rank]])[0][1])
            modeled_pd_by_rating[rating] = round(pd_value, 6)

    modeled_lgd_by_facility_type = {}
    for ftype in df["FACILITY_TYPE"].dropna().unique():
        lgd_pred, _ = _predict_lgd(pd.Series([ftype]))
        modeled_lgd_by_facility_type[str(ftype)] = round(float(lgd_pred.iloc[0]), 4)

    portfolio_weighted_avg_pd = float((effective_pd * ead).sum() / total_ead) if total_ead else None
    portfolio_weighted_avg_lgd = float((lgd * ead).sum() / total_ead) if total_ead else None

    stage_counts = computed_stage.value_counts().sort_index()

    # Top-5 highest-ECL records, for the inline view's risk table. Uses
    # the same fuzzy column-matching approach dedup_adapter.py already
    # established (a name/ID column can be called almost anything) --
    # gracefully omitted if the file has no name-like column at all,
    # rather than guessing.
    name_col = _find_column(df.columns, ["full_name", "name", "customer_name"])
    id_col = _find_column(df.columns, ["loan_id", "national_id", "cust_id", "customer_id", "id"])
    cust_id_col = _find_column_exact(df.columns, ["cust_id"]) if customer_lookup else None
    top_5_risk = []
    top5_idx = computed_ecl.sort_values(ascending=False).head(5).index
    for idx in top5_idx:
        entry = {"ecl_sar": round(float(computed_ecl.loc[idx]), 2)}
        joined_name = None
        if cust_id_col:
            joined_name = customer_lookup.get(str(df.loc[idx, cust_id_col]))
        if joined_name:
            entry["name"] = joined_name
        elif name_col:
            entry["name"] = str(df.loc[idx, name_col])
        else:
            entry["name"] = f"Record {idx}"
        if id_col:
            entry["id"] = str(df.loc[idx, id_col])
        top_5_risk.append(entry)

    result = {
        "loan_count": len(df),
        "scenario": scenario,
        "total_exposure_ead": round(total_ead, 2),
        "total_computed_ecl": round(total_computed_ecl, 2),
        "expected_ecl_across_scenarios": round(expected_ecl_across_scenarios, 2),
        "ecl_by_scenario": {name: round(val, 2) for name, val in ecl_by_scenario.items()},
        "coverage_ratio": round(total_computed_ecl / total_ead, 4) if total_ead else None,
        "loans_by_stage": {str(k): int(v) for k, v in stage_counts.items()},
        "stage_1_count": int(stage_counts.get(1, 0)),
        "stage_2_count": int(stage_counts.get(2, 0)),
        "stage_3_count": int(stage_counts.get(3, 0)),
        "top_5_risk": top_5_risk,
        "staging_triggers_evaluated": staging_triggers_evaluated,
        "staging_triggers_not_evaluated": staging_triggers_not_evaluated,
        "portfolio_weighted_average_pd": round(portfolio_weighted_avg_pd, 6) if portfolio_weighted_avg_pd is not None else None,
        "portfolio_weighted_average_lgd": round(portfolio_weighted_avg_lgd, 4) if portfolio_weighted_avg_lgd is not None else None,
        "pd_lgd_ead": {
            "avg_pd": round(portfolio_weighted_avg_pd, 6) if portfolio_weighted_avg_pd is not None else None,
            "avg_lgd": round(portfolio_weighted_avg_lgd, 4) if portfolio_weighted_avg_lgd is not None else None,
            "total_ead_sar": round(total_ead, 2),
        },
        "modeled_pd_by_rating": modeled_pd_by_rating,
        "modeled_lgd_by_facility_type": modeled_lgd_by_facility_type,
        "pd_model": "scikit-learn LogisticRegression (fitted)",
        "lgd_model": "scikit-learn LinearRegression (fitted)",
        "scenario_description": MACRO_SCENARIOS[scenario]["description"],
        "methodology_note": (
            "This methodology follows Dr. Mohamed Saber's DataOS_IFRS9_Parameters file directly "
            "-- his exact PD table, LGD-by-facility rates, staging rules, and macro scenario "
            "structure, not our own invented numbers. PD is estimated with a fitted scikit-learn "
            "LogisticRegression (the standard PD-scorecard technique), trained on his stated base "
            "12-month PD values by rating (sourced by him to SAMA's Financial Stability Reports). "
            "His table covers 8 of the 10 grades in this data; CC and C are geometrically "
            "interpolated between his CCC and D values -- see modeled_pd_by_rating and the "
            "RATINGS_INTERPOLATED_NOT_IN_SABER_TABLE note. Stage 1 uses 12-month PD; Stage 2/3 use "
            "lifetime PD over the loan's remaining term, per IFRS 9's actual distinction. Staging "
            "uses his multi-trigger SICR rules -- see staging_triggers_evaluated / "
            "staging_triggers_not_evaluated for exactly which triggers this file's data actually "
            "supports (only the DPD backstop today). LGD uses his exact stated rates for all 5 "
            "facility types in this data (Real estate 25%, Project financing 35%, Commercial 45%, "
            "Salary-based 30%, Unsecured/credit card 65%) -- fitted with scikit-learn "
            "LinearRegression, not looked up directly, but trained to land on his numbers. Both PD "
            "and LGD are adjusted by his scenario multipliers -- LGD isn't scenario-invariant "
            f"either (collateral recovers less under stress). This scenario ('{scenario}') applies "
            f"{MACRO_SCENARIOS[scenario]['pd_multiplier']}x to PD and "
            f"{MACRO_SCENARIOS[scenario]['lgd_multiplier']}x to LGD. "
            "expected_ecl_across_scenarios is his probability-weighted blend across all three "
            "(25%/55%/20%). What remains illustrative, pending his historical data: the underlying "
            "training figures themselves (not fitted from this bank's actual defaults/recoveries, "
            "which don't exist yet) -- the parameters and mechanism are his and real; the "
            "calibration-to-actual-outcomes step is what's still ahead."
        ),
    }

    if unknown_ratings:
        result["unrecognized_ratings"] = unknown_ratings

    if unknown_facilities:
        result["unrecognized_facility_types"] = unknown_facilities

    if "STAGE" in df.columns:
        stated_stage = df["STAGE"]
        stage_match_rate = float((stated_stage == computed_stage).mean())
        result["stage_agreement_with_source"] = round(stage_match_rate, 4)

    if "ECL_SAR" in df.columns:
        stated_total = float(df["ECL_SAR"].sum())
        result["stated_total_ecl_in_source"] = round(stated_total, 2)
        result["matches_source_figure"] = abs(stated_total - total_computed_ecl) < max(1.0, stated_total * 0.001)
        result["difference_from_source_note"] = (
            "This figure is expected to differ from the source's stated ECL -- it's computed from "
            "independently modeled PD/LGD (illustrative), not from the file's own pre-filled PD/LGD "
            "columns. A close match to source staging (see stage_agreement_with_source) while ECL "
            "differs is the expected, correct signature of independent modeling, not an error."
        )

    return result


# ---------------------------------------------------------------------
# Typed-chat entry points -- the fix for Dr. Saber's Finding #1
# (2026-08-09): the SAMA / Customer 360 / NDI Radar components were
# only reachable via the recommendation-chip path in main.py; the
# natural-language path in interpreter.py had no registered intent for
# any of them, so typing "Show SAMA compliance" fell through to the
# generic agent, which either denied the feature existed or routed to
# an unrelated intent. These three wrappers are the payload-taking
# adapter functions the capability registry now points at, so the
# typed path routes through the exact same compute functions the chip
# path already uses -- one implementation, two entrances, no drift.
# ---------------------------------------------------------------------

def _resolve_dataset_name_for_chat(payload: dict) -> str | None:
    """Resolves which dataset a typed-chat request refers to.
    - An explicit dataset_name in the payload wins (validated -- raises
      a plain-English ValueError if it doesn't exist).
    - Otherwise, if exactly one dataset exists, that's unambiguous.
    - If none exist, returns None -- the caller renders the honest
      "no dataset yet" state instead of erroring.
    - If several exist and none was named, raises a friendly ask --
      same behavior as confirm_all_duplicates already has."""
    name = (payload or {}).get("dataset_name")
    if name:
        listing = dataset_adapter.list_datasets({"dataset_name": name})
        return listing["datasets"][0]["dataset_name"]
    datasets = dataset_adapter.list_datasets({})["datasets"]
    if len(datasets) == 1:
        return datasets[0]["dataset_name"]
    if not datasets:
        return None
    names = ", ".join(d["display_name"] or d["dataset_name"] for d in datasets)
    raise ValueError(
        f"More than one dataset is available ({names}) -- please say which one you'd like this for."
    )


def _sama_no_dataset_state() -> dict:
    """The SAMA Compliance View when no dataset exists at all. Same
    honesty rule as check_never_run: nothing is invented -- every
    domain shows not_measured with a plain explanation of what to do
    first. Same output shape as compute_sama_compliance, so the
    existing renderer needs no changes."""
    domain_defs = [
        ("DG", "Data Governance"), ("DQ", "Data Quality"),
        ("DIS", "Data Integration & Sharing"), ("RMD", "Risk Management Data"),
        ("DC", "Data Classification"), ("PDP", "Personal Data Protection"),
        ("BIA", "Business Impact Assessment"), ("DS", "Data Security"),
    ]
    return {
        "checks": [
            {"label": "Data Governance", "status": "not_measured", "value": "No dataset uploaded yet"},
            {"label": "MDM", "status": "not_measured", "value": "No dataset uploaded yet"},
            {"label": "Data Classification", "status": "not_measured", "value": "Not yet instrumented"},
            {"label": "PDPL", "status": "not_measured", "value": "No dataset uploaded yet"},
            {"label": "IFRS 9 + Basel III readiness", "status": "not_measured", "value": "No dataset uploaded yet"},
        ],
        "domain_scores": [
            {"code": code, "name": name, "score": None, "status": "not_measured"}
            for code, name in domain_defs
        ],
        "no_dataset": True,
        "priority_alert": (
            "No dataset has been uploaded yet -- there's nothing to measure compliance signals "
            "against. Upload a dataset via the \"+\" button, then run \"Find duplicate customers\" "
            "to populate the governance and risk-data domains."
        ),
        "methodology_note": (
            "DG, DQ, RMD, and PDP are computed from real signals (the duplicate-review audit log "
            "and dataset null-rate metrics) once a dataset exists -- nothing is scored until "
            "there's real data to score."
        ),
    }


def _customer_360_no_dataset_state() -> dict:
    """The Customer 360 KPI Bar when no dataset exists at all -- same
    output shape as compute_customer_360's never-checked state, so the
    existing "--" tile rendering applies unchanged."""
    return {
        "total_records": 0,
        "golden_records_estimate": None,
        "uniqueness_ratio": None,
        "uniqueness_target": 99.0,
        "duplicate_clusters_confirmed": None,
        "duplicate_records_involved": None,
        "check_never_run": True,
        "quality_trend": None,
        "no_dataset": True,
        "note": (
            "No dataset has been uploaded yet -- there are no customer records to measure. "
            "Upload a dataset via the \"+\" button, then run \"Find duplicate customers\" to "
            "populate these figures."
        ),
    }


def run_sama_compliance(payload: dict) -> dict:
    """Registered adapter for the typed-chat "assess_sama_compliance"
    intent. Never refuses: with a dataset it computes the real view
    (including its honest never-checked state); with no dataset it
    renders the not-measured empty state above."""
    dataset_name = _resolve_dataset_name_for_chat(payload)
    if dataset_name is None:
        return _sama_no_dataset_state()
    result = compute_sama_compliance(dataset_name)
    return {**result, "dataset_name": dataset_name}


def run_customer_360(payload: dict) -> dict:
    """Registered adapter for the typed-chat "assess_customer_360"
    intent. Same never-refuse contract as run_sama_compliance."""
    dataset_name = _resolve_dataset_name_for_chat(payload)
    if dataset_name is None:
        return _customer_360_no_dataset_state()
    result = compute_customer_360(dataset_name)
    return {**result, "dataset_name": dataset_name}


def run_ndi_radar(payload: dict) -> dict:
    """Registered adapter for the typed-chat "show_ndi_radar" intent.
    Unconditional by design: the NDI Radar View renders from Dr.
    Saber's preset BAJ baseline (his explicit spec for this component)
    -- it needs no file and no dataset, ever."""
    return compute_ndi_assessment()
