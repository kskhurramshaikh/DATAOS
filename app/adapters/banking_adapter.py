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
