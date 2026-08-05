from app.adapters import banking_adapter


NDI_CSV = """#,Domain (Arabic),Domain (English),Current Score,Target Score,Gap,Priority
1,حوكمة البيانات,Data Governance,2.8,4.0,1.2,High
2,جودة البيانات,Data Quality,2.0,4.0,2.0,Critical
3,أمن البيانات,Data Security,3.0,4.0,1.0,Medium
Average,,,2.6,4.0,1.4,
"""

IFRS9_CSV = """LOAN_ID,STAGE,PD,LGD,EAD,ECL_SAR
LN-1,1,0.02,0.30,100000,600.0
LN-2,2,0.10,0.40,50000,2000.0
LN-3,3,0.50,0.60,20000,6000.0
"""


def test_ndi_computes_index_and_ranks_gaps():
    result = banking_adapter.run_ndi({"csv_content": NDI_CSV})

    assert result["domain_count"] == 3  # summary row excluded
    assert result["average_target_score"] == 4.0
    assert result["computed_readiness_index_0_100"] is not None
    # Data Quality has the biggest gap (2.0) -- should rank first
    assert result["top_gap_domains"][0]["domain"] == "Data Quality"
    assert "methodology_note" in result


def test_ndi_requires_csv_content():
    try:
        banking_adapter.run_ndi({})
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_ndi_requires_numeric_columns():
    try:
        banking_adapter.run_ndi({"csv_content": "domain,notes\nA,foo\nB,bar\n"})
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_ndi_column_detection_is_name_based_not_positional():
    """Reproduces the exact failure mode found in manual testing: if the
    index/row-number column happens to stay fully numeric (rather than
    being demoted to text by an unrelated summary row, as in the real
    file), a purely positional 'first two numeric columns' heuristic
    would silently pick the wrong columns. Detection must be name-based
    first, so this must still resolve correctly."""
    csv_with_clean_index = """#,Domain,Current,Target
1,Data Governance,2.8,4.0
2,Data Quality,2.0,4.0
3,Data Security,3.0,4.0
"""
    result = banking_adapter.run_ndi({"csv_content": csv_with_clean_index})
    # If it had wrongly used "#" (1,2,3) as the current-score column, the
    # average would be 2.0 (mean of 1,2,3), not 2.6 (mean of 2.8,2.0,3.0).
    assert result["average_current_score"] == 2.6
    assert result["average_target_score"] == 4.0


def test_ifrs9_computes_ecl_correctly():
    result = banking_adapter.run_ifrs9({"csv_content": IFRS9_CSV})

    assert result["loan_count"] == 3
    expected_ecl = (0.02 * 0.30 * 100000) + (0.10 * 0.40 * 50000) + (0.50 * 0.60 * 20000)
    assert abs(result["total_computed_ecl"] - expected_ecl) < 0.01
    assert result["loans_by_stage"] == {"1": 1, "2": 1, "3": 1}


def test_ifrs9_flags_match_with_source_figure():
    result = banking_adapter.run_ifrs9({"csv_content": IFRS9_CSV})
    assert result["matches_source_figure"] is True


def test_ifrs9_flags_mismatch_with_source_figure():
    bad_csv = IFRS9_CSV.replace("600.0", "999999.0")
    result = banking_adapter.run_ifrs9({"csv_content": bad_csv})
    assert result["matches_source_figure"] is False


def test_ifrs9_requires_pd_lgd_ead_columns():
    try:
        banking_adapter.run_ifrs9({"csv_content": "LOAN_ID,STAGE\nLN-1,1\n"})
        assert False, "expected ValueError"
    except ValueError as e:
        assert "PD" in str(e) or "LGD" in str(e) or "EAD" in str(e)


def test_ifrs9_requires_csv_content():
    try:
        banking_adapter.run_ifrs9({})
        assert False, "expected ValueError"
    except ValueError:
        pass


MODELED_IFRS9_CSV = """LOAN_ID,RATING,FACILITY_TYPE,DPD,ORIGINATION,MATURITY,EAD,STAGE,ECL_SAR
LN-1,AAA,تمويل عقاري,0,2020-01-01,2025-01-01,100000,1,50
LN-2,B,تمويل شخصي,15,2021-01-01,2026-01-01,50000,1,800
LN-3,CCC,بطاقة ائتمانية,45,2022-01-01,2024-01-01,20000,2,3000
LN-4,D,تمويل تجاري,150,2019-01-01,2023-01-01,80000,3,60000
"""


def test_ifrs9_modeled_path_used_when_attributes_present():
    result = banking_adapter.run_ifrs9({"csv_content": MODELED_IFRS9_CSV, "scenario": "base"})
    assert result["scenario"] == "base"
    assert "scikit-learn LogisticRegression" in result["methodology_note"]


def test_ifrs9_modeled_exposes_pd_and_lgd_as_first_class_results():
    """PD and LGD must be visible results in their own right, not just
    hidden inputs to the final ECL number."""
    result = banking_adapter.run_ifrs9({"csv_content": MODELED_IFRS9_CSV})

    assert result["portfolio_weighted_average_pd"] is not None
    assert 0 < result["portfolio_weighted_average_pd"] < 1
    assert result["portfolio_weighted_average_lgd"] is not None
    assert 0 < result["portfolio_weighted_average_lgd"] < 1

    assert set(result["modeled_pd_by_rating"].keys()) == {"AAA", "B", "CCC", "D"}
    assert set(result["modeled_lgd_by_facility_type"].keys()) == {
        "تمويل عقاري", "تمويل شخصي", "بطاقة ائتمانية", "تمويل تجاري",
    }
    # AAA should have a lower modeled PD than D
    assert result["modeled_pd_by_rating"]["AAA"] < result["modeled_pd_by_rating"]["D"]


def test_ifrs9_modeled_staging_matches_dpd_backstop():
    result = banking_adapter.run_ifrs9({"csv_content": MODELED_IFRS9_CSV})
    # DPD 0, 15 -> stage 1; DPD 45 -> stage 2; DPD 150 -> stage 3
    assert result["loans_by_stage"] == {"1": 2, "2": 1, "3": 1}
    assert result["stage_agreement_with_source"] == 1.0  # matches the STAGE column in the test data


def test_ifrs9_modeled_scenario_scales_ecl_up():
    optimistic = banking_adapter.run_ifrs9({"csv_content": MODELED_IFRS9_CSV, "scenario": "optimistic"})
    base = banking_adapter.run_ifrs9({"csv_content": MODELED_IFRS9_CSV, "scenario": "base"})
    adverse = banking_adapter.run_ifrs9({"csv_content": MODELED_IFRS9_CSV, "scenario": "adverse"})
    assert optimistic["total_computed_ecl"] < base["total_computed_ecl"] < adverse["total_computed_ecl"]


def test_ifrs9_scenarios_attributed_to_dr_saber_with_real_sama_context():
    """Scenario structure must match Dr. Saber's stated specification
    (optimistic/base/adverse, his multipliers), with real SAMA context
    layered into the base scenario -- not arbitrary numbers."""
    base = banking_adapter.run_ifrs9({"csv_content": MODELED_IFRS9_CSV, "scenario": "base"})
    adverse = banking_adapter.run_ifrs9({"csv_content": MODELED_IFRS9_CSV, "scenario": "adverse"})
    optimistic = banking_adapter.run_ifrs9({"csv_content": MODELED_IFRS9_CSV, "scenario": "optimistic"})

    assert "SAMA" in base["scenario_description"]
    assert "1.2%" in base["scenario_description"]  # real NPL ratio context
    assert "Dr. Saber" in base["scenario_description"] or "specification" in base["scenario_description"]
    assert banking_adapter.MACRO_SCENARIOS["optimistic"]["pd_multiplier"] == 0.85
    assert banking_adapter.MACRO_SCENARIOS["base"]["pd_multiplier"] == 1.0
    assert banking_adapter.MACRO_SCENARIOS["adverse"]["pd_multiplier"] == 1.25


def test_lgd_uses_dr_sabers_stated_collateral_rates_for_clear_mappings():
    """Real estate, salary assignment, and unsecured facility types
    should fit close to Dr. Saber's stated rates (25%/30%/65%), since
    that's the training target now -- not our own invented numbers."""
    result = banking_adapter.run_ifrs9({"csv_content": MODELED_IFRS9_CSV})
    lgd_by_type = result["modeled_lgd_by_facility_type"]

    assert abs(lgd_by_type["تمويل عقاري"] - 0.25) < 0.05      # real estate
    assert abs(lgd_by_type["تمويل شخصي"] - 0.30) < 0.05        # salary assignment
    assert abs(lgd_by_type["بطاقة ائتمانية"] - 0.65) < 0.05    # unsecured


def test_pending_clarification_facility_types_are_flagged():
    """Facility types that don't map cleanly onto Dr. Saber's 4
    categories must be explicitly flagged, not silently guessed."""
    result = banking_adapter.run_ifrs9({"csv_content": MODELED_IFRS9_CSV})
    pending = result.get("facility_types_pending_dr_saber_clarification", [])
    assert "تمويل تجاري" in pending  # commercial financing, present in the test fixture


def test_staging_triggers_disclose_what_was_actually_evaluated():
    """The staging result must honestly disclose which SICR triggers
    this data could support, not silently claim the full multi-trigger
    logic ran when most of the underlying columns don't exist."""
    result = banking_adapter.run_ifrs9({"csv_content": MODELED_IFRS9_CSV})

    assert "DPD backstop (30/90 days past due)" in result["staging_triggers_evaluated"]
    not_evaluated_text = " ".join(result["staging_triggers_not_evaluated"])
    assert "watchlist" in not_evaluated_text
    assert "rating downgrade" in not_evaluated_text


def test_ifrs9_modeled_rejects_unknown_scenario():
    try:
        banking_adapter.run_ifrs9({"csv_content": MODELED_IFRS9_CSV, "scenario": "made_up"})
        assert False, "expected ValueError"
    except ValueError as e:
        assert "scenario" in str(e).lower()


def test_ifrs9_modeled_flags_unrecognized_rating():
    csv_with_bad_rating = MODELED_IFRS9_CSV.replace("AAA", "ZZZ")
    result = banking_adapter.run_ifrs9({"csv_content": csv_with_bad_rating})
    assert "ZZZ" in result["unrecognized_ratings"]


def test_ifrs9_falls_back_to_simple_aggregation_without_modeling_columns():
    # The existing simple test CSV (PD/LGD/EAD/STAGE/ECL_SAR only, no
    # rating/facility/DPD/dates) should still route to the old
    # aggregation path, not error out.
    result = banking_adapter.run_ifrs9({"csv_content": IFRS9_CSV})
    assert "aggregates the PD/LGD values already present" in result["methodology_note"]
    assert "scenario" not in result


def test_pd_model_predictions_are_monotonic_by_rating():
    """A real fitted model should produce a smooth, monotonically
    increasing PD as credit quality worsens -- not identical to the
    illustrative training targets (that's expected: it's a fitted
    sigmoid, not a lookup), but ordering must hold."""
    import pandas as pd

    ratings = pd.Series(banking_adapter.RATING_ORDER)
    preds, unknown_mask = banking_adapter._predict_pd(ratings)

    assert not unknown_mask.any()
    values = preds.tolist()
    assert values == sorted(values)  # strictly non-decreasing from AAA to D
    assert 0 < values[0] < 0.01  # AAA should be very low risk
    assert values[-1] > 0.5  # D should be high risk


def test_lgd_model_predictions_differ_by_facility_type():
    """Secured facility types (real estate) should predict meaningfully
    lower LGD than unsecured ones (credit card) -- confirming the model
    actually learned the secured/unsecured distinction, not just
    returning one flat number."""
    import pandas as pd

    types = pd.Series(["تمويل عقاري", "بطاقة ائتمانية"])
    preds, unknown_mask = banking_adapter._predict_lgd(types)

    assert not unknown_mask.any()
    assert preds.iloc[0] < preds.iloc[1]  # real estate LGD < credit card LGD


def test_lgd_model_falls_back_for_unknown_facility_type():
    import pandas as pd

    types = pd.Series(["some_never_seen_facility"])
    preds, unknown_mask = banking_adapter._predict_lgd(types)

    assert unknown_mask.iloc[0]
    assert preds.iloc[0] == banking_adapter.DEFAULT_LGD
