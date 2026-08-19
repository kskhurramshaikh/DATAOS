# Data Quality Rules (Item 7's second page). Section 02 names Great
# Expectations for this; checked directly before building (same
# resource-check discipline as every other tool decision in this
# codebase) -- unlike OpenMetadata, Great Expectations is a plain
# `pip install great-expectations` Python library, not a service. It
# runs in-process via its "ephemeral" context mode: no new Render
# service, no new cost, genuinely satisfies "Great Expectations" per
# Section 02 rather than needing a lighter substitute the way Item 6's
# catalog did.
#
# Rules run against a dataset's real Silver-stage data (post-dedup,
# pre-Gold-curation) -- the same data dataset_adapter.py's own
# promotion-gate logic already inspects. Thresholds are NOT invented
# for this page: they're the literal constants dataset_adapter.py
# already uses to decide Silver-hold, imported directly rather than
# restated, so this page can never silently drift from the actual
# gating logic elsewhere in the app.

import contextlib
import io

import great_expectations as gx
import pandas as pd

from app.adapters import dataset_adapter

# Progress-bar output (tqdm, via GE's internal metric calculation) goes
# to stderr by default and has no public suppression flag in this GE
# version's Batch.validate() signature (checked directly, not assumed)
# -- redirected rather than left to spam server logs on every call.


def _validate_silently(batch, suite):
    with contextlib.redirect_stderr(io.StringIO()):
        return batch.validate(suite)


def _build_batch(df: pd.DataFrame):
    context = gx.get_context(mode="ephemeral")
    data_source = context.data_sources.add_pandas("dataos_pandas")
    data_asset = data_source.add_dataframe_asset(name="dataos_asset")
    batch_definition = data_asset.add_batch_definition_whole_dataframe("dataos_batch")
    return batch_definition.get_batch(batch_parameters={"dataframe": df})


def run_quality_rules(dataset_name: str) -> dict:
    """Runs a real Great Expectations suite against a dataset's Silver
    data. Every rule is built dynamically from that dataset's own
    columns -- nothing hardcoded to Banking_Demo's specific schema, so
    this works for any uploaded dataset."""
    listing = dataset_adapter.list_datasets({"dataset_name": dataset_name})
    ds = listing["datasets"][0]
    columns = ds["columns"]
    total_rows = ds["rows"]

    silver_csv = dataset_adapter.read_silver_csv(dataset_name)
    df = pd.read_csv(io.StringIO(silver_csv))

    batch = _build_batch(df)
    suite = gx.ExpectationSuite(name=f"dataos_{dataset_name}_quality")

    rule_defs = []  # (expectation, description, column_or_None)

    # Rule 1: dataset isn't empty.
    rule_defs.append((
        gx.expectations.ExpectTableRowCountToBeBetween(min_value=1),
        "Dataset has at least one row.",
        None,
    ))

    # Rule 2, per column: null-rate threshold. SAME thresholds
    # dataset_adapter.py's own Silver-hold gate uses -- imported
    # constants, not restated numbers, so this rule can't silently
    # diverge from what actually gates promotion elsewhere in the app.
    hold_threshold = (
        dataset_adapter.SMALL_TABLE_NULL_RATE_HOLD_THRESHOLD
        if total_rows < dataset_adapter.SMALL_TABLE_ROW_THRESHOLD
        else dataset_adapter.NULL_RATE_HOLD_THRESHOLD
    )
    min_not_null_fraction = round(1 - hold_threshold, 4)
    for col in columns:
        rule_defs.append((
            gx.expectations.ExpectColumnValuesToNotBeNull(column=col, mostly=min_not_null_fraction),
            f"'{col}' is at most {hold_threshold:.0%} null (the same threshold that gates Silver promotion).",
            col,
        ))

    # Rule 3, per identifier-like column: values are unique. Reuses
    # dataset_adapter's own identifier heuristic -- the same one that
    # decides which columns get excluded from numeric summaries.
    identifier_cols = [c for c in columns if dataset_adapter._looks_like_identifier(c)]
    for col in identifier_cols:
        rule_defs.append((
            gx.expectations.ExpectColumnValuesToBeUnique(column=col),
            f"'{col}' (an identifier-shaped column) has no duplicate values.",
            col,
        ))

    for expectation, _, _ in rule_defs:
        suite.add_expectation(expectation)

    result = _validate_silently(batch, suite)

    # BUG FOUND VIA DIRECT TESTING (2026-08-19), fixed before ship: GE's
    # result.results does NOT preserve the order expectations were added
    # to the suite -- confirmed directly (it groups/reorders internally,
    # observed grouping by column). A positional zip(rule_defs,
    # result.results) silently paired each result with the WRONG rule's
    # description/column -- e.g. a real duplicate-value failure on
    # CUST_ID's uniqueness rule got reported under a different column's
    # description while showing "pass". Fixed by matching each result
    # back to its rule via (expectation_type, column) instead of
    # position -- safe here since a single suite never has two rules
    # with the same (type, column) pair.
    by_key = {(expectation.expectation_type, column): (expectation, description) for expectation, description, column in rule_defs}

    rules_out = []
    for validation_result in result.results:
        cfg = validation_result.expectation_config
        column = cfg.kwargs.get("column")
        key = (cfg.type, column)
        expectation, description = by_key[key]
        rule_result = validation_result.result
        rules_out.append({
            "rule_type": expectation.expectation_type,
            "column": column,
            "description": description,
            "status": "pass" if validation_result.success else "fail",
            "element_count": rule_result.get("element_count"),
            "unexpected_count": rule_result.get("unexpected_count"),
            "unexpected_percent": (
                round(rule_result["unexpected_percent"], 2) if rule_result.get("unexpected_percent") is not None else None
            ),
        })

    # Non-GE check: whether entity-duplicate detection has actually
    # been run on this dataset. Not an Expectation (it's not a data
    # quality property GE evaluates from the dataframe itself), but a
    # real quality signal already tracked elsewhere in the app --
    # included here so the page gives one complete quality picture,
    # not two the user has to cross-reference themselves.
    dup_check_status = {
        "rule_type": "duplicate_detection_run",
        "column": None,
        "description": "Entity-duplicate detection has been run on this dataset (see Duplicate Queue).",
        "status": "pass" if ds.get("duplicate_check_last_run_at") else "warn",
        "element_count": None,
        "unexpected_count": None,
        "unexpected_percent": None,
        "detail": (
            f"Last run: {ds['duplicate_check_last_run_at']}"
            if ds.get("duplicate_check_last_run_at")
            else "Never run on this dataset -- see the Duplicate Queue page."
        ),
    }
    rules_out.append(dup_check_status)

    passed = sum(1 for r in rules_out if r["status"] == "pass")

    return {
        "dataset_name": dataset_name,
        "rules": rules_out,
        "rules_total": len(rules_out),
        "rules_passed": passed,
        "engine": "Great Expectations (great_expectations Python library, ephemeral in-process context -- no separate service)",
        "methodology_note": (
            "Null-rate rules use dataset_adapter.py's own Silver-promotion thresholds directly "
            "(10% normal, 35% for tables under 20 rows) -- not separate numbers invented for this "
            "page. Uniqueness rules apply to columns already identified as identifier-shaped by the "
            "same heuristic used elsewhere in the app. The duplicate-detection-run check reuses the "
            "existing duplicate_check_last_run_at signal rather than re-deriving it."
        ),
    }
