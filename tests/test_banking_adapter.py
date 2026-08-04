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
