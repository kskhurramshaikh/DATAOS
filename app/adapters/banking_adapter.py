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

import pandas as pd


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
        "methodology_note": "ECL computed per loan as PD x LGD x EAD, then summed across the portfolio.",
    }

    if "STAGE" in df.columns:
        stage_counts = df["STAGE"].value_counts().sort_index()
        result["loans_by_stage"] = {str(k): int(v) for k, v in stage_counts.items()}

    if "ECL_SAR" in df.columns:
        stated_total = float(df["ECL_SAR"].sum())
        result["stated_total_ecl_in_source"] = round(stated_total, 2)
        result["matches_source_figure"] = abs(stated_total - total_computed_ecl) < max(1.0, stated_total * 0.001)

    return result
