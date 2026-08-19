"""
MDM Field-Level Lineage (Item 3, MDM page group -- Section 04 page 3's
4-required-pages list: Golden Record Registry, Duplicate Resolution
Queue, Field-Level Lineage, Data Stewardship). This module is the
Field-Level Lineage half -- traces the specific 4 fields Dr. Saber
named (ECL_SAR, NATIONAL_ID, DUPLICATE_FLAG, NDI_SCORE) through
whichever REAL, currently-queryable system actually produces each one.

FLAGGED HONESTLY (2026-08-18, before building, same pattern as the
Item 6 OpenMetadata resource-wall): the doc's own wording implies all
four fields are "sourced from OpenLineage/Marquez." In the system as
built, only two of them genuinely are:

  - NATIONAL_ID and DUPLICATE_FLAG live entirely in the MDM/Postgres
    world (dataset_adapter's Bronze/Silver CSV -> dedup_adapter's
    duplicate_clusters/golden_records) -- this never touches the
    Airflow DAG or Marquez at all. DUPLICATE_FLAG isn't a literal raw
    column; it's a record's duplicate_clusters.status lifecycle
    (pending -> confirmed_duplicate/not_duplicate -> golden_records
    if confirmed).
  - ECL_SAR and NDI_SCORE can each come from TWO real, separate code
    paths: the Airflow DAG's gold_compute task (genuinely
    Marquez-traceable, reuses marquez_client.py) OR an on-demand chat/
    dashboard action (banking_adapter.run_ifrs9()/
    compute_ndi_assessment(), not part of the DAG, not Marquez-
    tracked). IFRS9's on-demand result isn't durably stored anywhere
    after it's shown; NDI's on-demand result IS, via ndi_history's
    snapshot table -- but that history is dataset-AGNOSTIC (NDI
    assessment runs on a fixed baseline, not per-dataset data, per
    Item 5's own design), unlike the DAG-computed per-dataset NDI
    Iceberg table Marquez does track.

This module traces each field through every real path that actually
exists for it, labels which system each trace came from, and never
fabricates a trace where none exists -- a field with no golden
records yet, or a dataset that never ran the DAG's gold_compute
IFRS9/NDI path, correctly shows "not available for this dataset" for
that specific source rather than an invented path.

PERFORMANCE FIX (2026-08-18, found live): the first version called
marquez_client.get_field_lineage() TWICE per request -- once inside
_ecl_sar_lineage(), once inside _ndi_score_lineage() -- each a fresh
call that itself makes several sequential HTTP round-trips to the
separate Marquez service (list_jobs(), then get_job_runs() once per
job). Confirmed live: the dashboard page got stuck on "Loading..."
long enough to look hung, and the network panel showed a single
request sitting "pending" well past what a normal response should
take -- consistent with this endpoint doing roughly double the
necessary external calls, against a Marquez service that may itself
be a free-tier instance subject to the same cold-start slowness this
whole stack has documented elsewhere. Fixed by fetching the graph
ONCE in get_field_lineage_for_dataset() and passing it into both
field functions, instead of each re-fetching it independently.
"""

from app import marquez_client
from app.adapters import dataset_adapter, dedup_adapter, ndi_history


def _dataset_record(dataset_name: str) -> dict | None:
    try:
        result = dataset_adapter.list_datasets({"dataset_name": dataset_name})
    except ValueError:
        return None
    datasets = result.get("datasets", [])
    return datasets[0] if datasets else None


def _national_id_lineage(dataset_name: str, record: dict | None, golden_records: list[dict]) -> dict:
    """NATIONAL_ID: a raw passthrough column, MDM/Postgres world only.
    Traces Bronze upload -> Silver -> whether it ever contributed to a
    merged golden record's field_sources (i.e. survived into curated
    output)."""
    if record is None or "NATIONAL_ID" not in (record.get("columns") or []):
        return {
            "field": "NATIONAL_ID",
            "source_system": "MDM / Postgres",
            "available": False,
            "reason": "Column not present in this dataset.",
            "steps": [],
        }

    null_count = (record.get("null_counts") or {}).get("NATIONAL_ID", 0)
    contributing = [
        {"golden_record_id": g["id"], "source_row_id": g["field_sources"].get("NATIONAL_ID")}
        for g in golden_records
        if "NATIONAL_ID" in g.get("field_sources", {}) and g["merged_record"].get("NATIONAL_ID") is not None
    ]

    return {
        "field": "NATIONAL_ID",
        "source_system": "MDM / Postgres",
        "available": True,
        "reason": None,
        "steps": [
            {"stage": "Bronze", "description": "Raw uploaded file, column present as-is."},
            {
                "stage": "Silver",
                "description": f"Deduplicated CSV in object storage. {null_count} null value(s) out of {record.get('rows')} rows.",
            },
            {
                "stage": "Golden Records",
                "description": (
                    f"Contributed a value to {len(contributing)} of {len(golden_records)} merged golden record(s)."
                    if golden_records
                    else "No duplicate detection run yet for this dataset -- no golden records exist to check."
                ),
            },
        ],
        "golden_record_contributions": contributing,
    }


def _duplicate_flag_lineage(dataset_name: str, record: dict | None, golden_records: list[dict]) -> dict:
    """DUPLICATE_FLAG: not a literal raw column -- represents a record's
    position in the duplicate_clusters status lifecycle. Traces Silver
    data -> clustering -> per-status counts -> golden records."""
    if record is None:
        return {
            "field": "DUPLICATE_FLAG",
            "source_system": "MDM / Postgres",
            "available": False,
            "reason": "Dataset not found.",
            "steps": [],
        }

    pending = dedup_adapter.get_pending_clusters(dataset_name)
    decided = dedup_adapter.get_audit_log(dataset_name)
    confirmed = [d for d in decided if d["status"] == "confirmed_duplicate"]
    rejected = [d for d in decided if d["status"] == "not_duplicate"]
    checked = record.get("duplicate_check_last_run_at")

    return {
        "field": "DUPLICATE_FLAG",
        "source_system": "MDM / Postgres",
        "available": bool(checked),
        "reason": None if checked else "Duplicate detection has not been run yet for this dataset.",
        "steps": [
            {"stage": "Silver", "description": "Cleaned dataset used as clustering input."},
            {
                "stage": "Duplicate detection",
                "description": f"Exact-DOB clustering. {len(pending)} pending, {len(confirmed)} confirmed_duplicate, {len(rejected)} not_duplicate.",
            },
            {"stage": "Golden Records", "description": f"{len(golden_records)} record(s) actually merged as a result."},
        ],
        "status_counts": {
            "pending": len(pending),
            "confirmed_duplicate": len(confirmed),
            "not_duplicate": len(rejected),
        },
    }


def _dag_dataset_lineage(graph: dict, dataset_name: str, keyword: str) -> dict | None:
    """Shared helper for ECL_SAR/NDI_SCORE's DAG-based path: takes an
    ALREADY-FETCHED marquez_client lineage graph (see the module
    docstring's PERFORMANCE FIX -- this used to fetch it itself, once
    per field, doubling the external Marquez round-trips per request),
    filters to this dataset's gold_compute job, and finds the one Gold
    output dataset node whose URI contains `keyword` (e.g. "ndi" or
    "ifrs9" -- the actual Iceberg table name fragments gold_compute
    writes, per the DAG's own conditional-outlet logic). Returns None
    if Marquez isn't configured or this dataset never produced that
    specific output."""
    if not graph.get("configured"):
        return None

    job_node = next(
        (
            n
            for n in graph["nodes"]
            if n["type"] == "job" and n.get("dataset_name") == dataset_name and "gold_compute" in n["id"]
        ),
        None,
    )
    if not job_node:
        return None

    output_uri = next(
        (
            e["to"]
            for e in graph["edges"]
            if e["from"] == job_node["id"] and e["type"] == "output" and keyword in e["to"].lower()
        ),
        None,
    )
    if not output_uri:
        return None

    input_uris = [e["from"] for e in graph["edges"] if e["to"] == job_node["id"] and e["type"] == "input"]
    return {"run_state": job_node.get("run_state"), "inputs": input_uris, "output": output_uri}


def _ecl_sar_lineage(dataset_name: str, graph: dict) -> dict:
    """ECL_SAR: two real code paths -- only the Airflow DAG one is
    Marquez-traceable. The on-demand chat "Compute IFRS 9" action
    (banking_adapter.run_ifrs9()) is NOT part of the DAG, NOT
    Marquez-tracked, and not durably stored anywhere after the chat
    response is shown -- stated plainly rather than inventing a trace
    for it."""
    dag_trace = _dag_dataset_lineage(graph, dataset_name, "ifrs9")
    return {
        "field": "ECL_SAR",
        "source_system": "Airflow DAG / Marquez (when computed via the pipeline)",
        "available": dag_trace is not None,
        "reason": (
            None
            if dag_trace
            else "This dataset's gold_compute run hasn't produced an IFRS9 Gold table yet -- either the "
            "pipeline hasn't run, or this dataset lacks the IFRS9 modeling columns."
        ),
        "steps": (
            [
                {"stage": "Silver (Iceberg)", "description": f"Input table(s): {', '.join(dag_trace['inputs']) or 'none recorded'}."},
                {"stage": "gold_compute (Airflow task)", "description": f"Run state: {dag_trace['run_state']}."},
                {"stage": "Gold (Iceberg)", "description": f"Output table: {dag_trace['output']}."},
            ]
            if dag_trace
            else []
        ),
        "note": (
            "A separate on-demand \"Compute IFRS 9\" chat/dashboard action can also produce an "
            "ECL_SAR figure without going through this pipeline at all -- that path isn't tracked "
            "by Marquez and isn't durably stored after the result is shown, so it has no lineage "
            "trace to display here."
        ),
    }


def _ndi_score_lineage(dataset_name: str, graph: dict) -> dict:
    """NDI_SCORE: same DAG-vs-on-demand split as ECL_SAR, but the
    on-demand path IS durably recorded (ndi_history's snapshot table)
    -- just dataset-agnostic, since NDI assessment runs on a fixed
    baseline per Item 5's own design, not per-dataset uploaded data.
    Both real sources are shown, clearly labeled as separate."""
    dag_trace = _dag_dataset_lineage(graph, dataset_name, "ndi")
    history = ndi_history.list_snapshots(limit=1)
    latest_snapshot = history["snapshots"][0] if history["snapshots"] else None

    steps = []
    if dag_trace:
        steps.append(
            {"stage": "Silver (Iceberg)", "description": f"Input table(s): {', '.join(dag_trace['inputs']) or 'none recorded'}."}
        )
        steps.append({"stage": "gold_compute (Airflow task)", "description": f"Run state: {dag_trace['run_state']}."})
        steps.append({"stage": "Gold (Iceberg)", "description": f"Output table: {dag_trace['output']}."})

    return {
        "field": "NDI_SCORE",
        "source_system": "Airflow DAG / Marquez (per-dataset) + NDI History (dataset-agnostic)",
        "available": bool(dag_trace) or bool(latest_snapshot),
        "reason": (
            None
            if (dag_trace or latest_snapshot)
            else "No DAG-computed NDI Gold table for this dataset yet, and no assessment has ever been recorded."
        ),
        "steps": steps,
        "dag_lineage_available": dag_trace is not None,
        "latest_recorded_snapshot": (
            {
                "id": latest_snapshot["id"],
                "recorded_at": latest_snapshot["recorded_at"],
                "recorded_by": latest_snapshot["recorded_by"],
                "display_score": latest_snapshot["display_score"],
            }
            if latest_snapshot
            else None
        ),
        "note": (
            "The recorded-snapshot source runs on Dr. Saber's fixed BAJ demo baseline (per Item "
            "5's own scoping), not this specific dataset's uploaded data -- the same score would "
            "show regardless of which dataset is selected here. The DAG-based source above, when "
            "available, IS specific to this dataset's own Gold Iceberg output."
        ),
    }


def get_field_lineage_for_dataset(dataset_name: str) -> dict:
    """The full Field-Level Lineage result for one dataset -- all four
    fields Dr. Saber named, each traced through every real system that
    actually produces it. Raises ValueError if the dataset doesn't
    exist, matching every other lookup-by-name function in this
    codebase.

    Fetches the Marquez lineage graph ONCE (see module docstring's
    PERFORMANCE FIX) and passes it to both ECL_SAR and NDI_SCORE's
    trace functions, instead of each fetching it independently."""
    if not dataset_name:
        raise ValueError("dataset_name is required.")

    record = _dataset_record(dataset_name)
    if record is None:
        raise ValueError(f"No dataset found matching '{dataset_name}'.")

    graph = marquez_client.get_field_lineage()
    # SECOND PERFORMANCE FIX (2026-08-19, found by actually profiling):
    # golden records were fetched twice per request (once per MDM field),
    # and get_golden_records() itself was an N+1 (see its docstring).
    # Fetched once here and shared, same pattern as the Marquez graph.
    golden_records = dedup_adapter.get_golden_records(dataset_name)

    return {
        "dataset_name": dataset_name,
        "fields": {
            "NATIONAL_ID": _national_id_lineage(dataset_name, record, golden_records),
            "DUPLICATE_FLAG": _duplicate_flag_lineage(dataset_name, record, golden_records),
            "ECL_SAR": _ecl_sar_lineage(dataset_name, graph),
            "NDI_SCORE": _ndi_score_lineage(dataset_name, graph),
        },
    }
