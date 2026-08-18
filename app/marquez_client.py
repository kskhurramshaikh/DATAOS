"""
Data Catalog + Field Lineage dashboard (Item 6 of the DataOS 3.0
Development Queue) -- reads REAL lineage data from Marquez
(dataos-marquez.onrender.com), the actual OpenLineage reference
implementation this stack sends every DAG run's lineage events to.
See app/adapters/../../spike/dags/banking_demo_lakehouse_spike.py's own
FIELD LINEAGE docstring section for what emits the data this module
reads.

WHAT THIS DOES NOT DO, stated plainly (same honesty principle as
lakehouse_client.py's own "not the full picture" notes): this is NOT
OpenMetadata. No PII tagging, no business glossary, no governance
approval workflows, no RBAC hooks to Ranger/OPA/Keycloak, no
cross-type search, no collaboration features. It's a real, working
job/run/dataset catalog and field-lineage view built on Marquez's
actual OpenLineage data -- see the 2026-08-18 Slack message to
#one-tech-ai (msg 1787048591.904819) for the full resource-wall
reasoning behind that scope decision.

FIELD LINEAGE, HOW IT'S ACTUALLY BUILT (2026-08-18): Marquez's own
/datasets endpoint stays empty in this setup -- CONFIRMED directly
(not assumed) via two real triggered DAG runs checked against the
live API. Airflow's OpenLineage provider faithfully captures our
DAG's runtime-set inlets/outlets into each job run's `airflow` facet
(also confirmed directly), but doesn't convert them into first-class
Marquez Dataset nodes -- matching a "limited support" caveat the
OpenLineage provider's own docs state for this exact fallback path.
Rather than depend on a conversion step that doesn't trigger here,
get_field_lineage() below parses inlets/outlets straight out of each
job's latest run facet itself and builds the lineage graph from that
-- real data, just assembled by us instead of by Marquez's own
/datasets endpoint.

CROSS-REGION: this app (dataos-2-0-pipeline) runs in Oregon; Marquez
runs in Singapore, same as the rest of the spike -- so this module
talks to Marquez over its PUBLIC URL (MARQUEZ_URL), same pattern
lakehouse_client.py already uses for LAKEHOUSE_DB_URI/SEAWEEDFS_
PUBLIC_URL.

Every function here degrades gracefully (returns an explicit
"not configured" / empty result, or a per-field "error") rather than
raising when Marquez isn't reachable or a response is malformed --
same principle as lakehouse_client.py, so one bad response never takes
down the rest of the dashboard.
"""
from __future__ import annotations

import os

import requests

MARQUEZ_URL = os.environ.get("MARQUEZ_URL", "")
NAMESPACE = "dataos-spike"  # the only namespace this stack's Airflow instance emits to (OPENLINEAGE_NAMESPACE)

_TIMEOUT = 10


def is_configured() -> bool:
    return bool(MARQUEZ_URL)


def _get(path: str) -> dict:
    resp = requests.get(f"{MARQUEZ_URL.rstrip('/')}{path}", timeout=_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def list_jobs() -> dict:
    """Every job Marquez knows about in this namespace -- both DAG-level
    (jobType DAG) and per-task (jobType TASK) jobs, since Airflow's
    OpenLineage provider registers both. Each includes its latest run's
    real status, duration, and which real dataset (dag_run.conf's
    dataset_name) it ran against -- pulled straight from the airflow
    facet already captured on every run, not re-derived."""
    if not is_configured():
        return {"configured": False, "jobs": []}
    try:
        data = _get(f"/api/v1/namespaces/{NAMESPACE}/jobs")
    except requests.RequestException as e:
        return {"configured": True, "jobs": [], "error": str(e)}

    jobs = []
    for j in data.get("jobs", []):
        job_type = (j.get("facets", {}).get("jobType") or {}).get("jobType", "UNKNOWN")
        latest = j.get("latestRun") or {}
        airflow_facet = (latest.get("facets") or {}).get("airflow") or {}
        dag_run = airflow_facet.get("dagRun") or {}
        jobs.append({
            "name": j.get("name"),
            "simple_name": j.get("simpleName", j.get("name")),
            "job_type": job_type,
            "description": (j.get("facets", {}).get("documentation") or {}).get("description"),
            "latest_run": {
                "id": latest.get("id"),
                "state": latest.get("state"),
                "started_at": latest.get("startedAt"),
                "ended_at": latest.get("endedAt"),
                "duration_ms": latest.get("durationMs"),
                "dataset_name": dag_run.get("conf", {}).get("dataset_name"),
                "run_id": dag_run.get("run_id"),
            } if latest else None,
            "updated_at": j.get("updatedAt"),
        })
    # DAG-level jobs first, then their tasks -- matches how a reader
    # would actually want to scan this (pipeline, then its steps).
    jobs.sort(key=lambda j: (j["job_type"] != "DAG", j["name"]))
    return {"configured": True, "jobs": jobs}


def get_job_runs(job_name: str, limit: int = 10) -> dict:
    """Real run history for one job, newest first -- same underlying
    data list_jobs()'s latest_run summarizes one of, just the fuller
    history. job_name must be the full Marquez job name (e.g.
    "banking_demo_lakehouse_spike" or
    "banking_demo_lakehouse_spike.silver_to_iceberg")."""
    if not is_configured():
        return {"configured": False, "runs": []}
    try:
        data = _get(f"/api/v1/namespaces/{NAMESPACE}/jobs/{job_name}/runs?limit={limit}")
    except requests.RequestException as e:
        return {"configured": True, "runs": [], "error": str(e)}

    runs = []
    for r in data.get("runs", []):
        airflow_facet = (r.get("facets") or {}).get("airflow") or {}
        dag_run = airflow_facet.get("dagRun") or {}
        error_facet = (r.get("facets") or {}).get("errorMessage") or {}
        runs.append({
            "id": r.get("id"),
            "state": r.get("state"),
            "started_at": r.get("startedAt"),
            "ended_at": r.get("endedAt"),
            "duration_ms": r.get("durationMs"),
            "dataset_name": dag_run.get("conf", {}).get("dataset_name"),
            "run_id": dag_run.get("run_id"),
            "error_message": error_facet.get("message"),
        })
    return {"configured": True, "runs": runs}


def get_field_lineage() -> dict:
    """Builds a real lineage graph from every job's latest run facet --
    see FIELD LINEAGE, HOW IT'S ACTUALLY BUILT in the module docstring
    for why this reads inlets/outlets directly rather than Marquez's
    own /datasets endpoint (which stays empty in this setup).

    Returns nodes (one per distinct dataset URI seen, plus one per
    DAG-level job) and edges (dataset -> job for each inlet, job ->
    dataset for each outlet) -- only from TASK-level jobs' latest runs,
    since those are what actually carry inlets/outlets (DAG-level jobs
    don't). Silently skips jobs with no inlets/outlets on their latest
    run (e.g. verify_silver_ready, which deliberately has none -- see
    the DAG's own docstring) rather than showing an empty/misleading
    node for them.
    """
    if not is_configured():
        return {"configured": False, "nodes": [], "edges": []}
    try:
        data = _get(f"/api/v1/namespaces/{NAMESPACE}/jobs")
    except requests.RequestException as e:
        return {"configured": True, "nodes": [], "edges": [], "error": str(e)}

    dataset_uris: set[str] = set()
    edges = []
    task_jobs_with_lineage = []

    for j in data.get("jobs", []):
        job_type = (j.get("facets", {}).get("jobType") or {}).get("jobType", "UNKNOWN")
        if job_type != "TASK":
            continue
        latest = j.get("latestRun") or {}
        airflow_facet = (latest.get("facets") or {}).get("airflow") or {}
        task_facet = airflow_facet.get("task") or {}

        # inlets/outlets arrive as a Python-repr'd string (e.g.
        # "[{'uri': 's3://...', 'extra': None}]") inside the facet,
        # not real JSON -- Airflow's own serialization, not something
        # this module controls. ast.literal_eval is the correct tool
        # for this (a trusted string we produced ourselves via the
        # DAG's _set_lineage(), not external input), not a raw eval().
        import ast
        try:
            inlets = ast.literal_eval(task_facet.get("inlets", "[]"))
        except (ValueError, SyntaxError):
            inlets = []
        try:
            outlets = ast.literal_eval(task_facet.get("outlets", "[]"))
        except (ValueError, SyntaxError):
            outlets = []

        if not inlets and not outlets:
            continue

        job_name = j.get("name")
        dag_run = airflow_facet.get("dagRun") or {}
        task_jobs_with_lineage.append({
            "job_name": job_name,
            "simple_name": j.get("simpleName", job_name),
            "dataset_name": dag_run.get("conf", {}).get("dataset_name"),
            "run_state": latest.get("state"),
        })

        for inp in inlets:
            uri = inp.get("uri")
            if not uri:
                continue
            dataset_uris.add(uri)
            edges.append({"from": uri, "to": job_name, "type": "input"})
        for out in outlets:
            uri = out.get("uri")
            if not uri:
                continue
            dataset_uris.add(uri)
            edges.append({"from": job_name, "to": uri, "type": "output"})

    nodes = [{"id": j["job_name"], "type": "job", "label": j["simple_name"], "dataset_name": j["dataset_name"], "run_state": j["run_state"]} for j in task_jobs_with_lineage]
    nodes += [{"id": uri, "type": "dataset", "label": uri.split("/")[-1] or uri, "uri": uri} for uri in sorted(dataset_uris)]

    return {"configured": True, "nodes": nodes, "edges": edges}
