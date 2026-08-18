"""
Read-only(-ish) data access for the Lakehouse dashboard (Item 2 of the
DataOS 3.0 Development Queue) -- reads REAL data from the spike
infrastructure:

  - Bronze zone stats: direct S3 listing against SeaweedFS via boto3.
  - Silver/Gold zone stats: real Iceberg table row counts via
    PostgresIcebergCatalog (same module the spike DAG uses).
  - Pipeline run history/status: Airflow's own Postgres metadata tables
    (dag_run, task_instance), queried directly with plain SQL.
  - Task log content: read from SeaweedFS, at the path Airflow's own
    S3TaskHandler writes to.
  - trigger_dag_run(): the ONE write this module does -- a manual,
    per-dataset trigger via Airflow's REST API. See its own docstring
    for why this is the only way the pipeline DAG ever runs.

CROSS-REGION NOTE (2026-08-17): this app (dataos-2-0-pipeline) runs in
Oregon; the spike services (dataos-spike-orchestrator/-storage, and
their Postgres) run in Singapore. Render's private networking only
resolves WITHIN a region, so this module talks to both services over
their PUBLIC endpoints:
  - LAKEHOUSE_DB_URI must be Postgres's *External* Database URL.
  - SEAWEEDFS_PUBLIC_URL is the storage service's public HTTPS URL.
  - Falls back to the internal-hostname construction if
    SEAWEEDFS_PUBLIC_URL isn't set, so this still works unmodified if
    this module is ever reused by something running in the same region
    as the spike services.

MULTI-DATASET (2026-08-18): originally this module (and the DAG it
reads) only ever knew about one hardcoded pipeline run for one file.
Every read function below now takes a required dataset_name and scopes
its query to that dataset specifically:
  - Bronze: lists s3://{APP_DATA_BUCKET}/bronze/{dataset_name}/ instead
    of a global "bronze/" prefix.
  - Silver/Gold: looks up the specific Iceberg tables
    silver.{dataset_name}, gold.{dataset_name}_ndi, and (only if it
    exists) gold.{dataset_name}_ifrs9 -- not a scan of every table in
    the namespace.
  - Pipeline runs/task history: filtered by run_id LIKE
    '{dataset_name}__%' -- see trigger_dag_run()'s docstring for why
    run_id (a plain queryable column) is used for this instead of
    Airflow's internal `conf` column, whose on-disk JSON storage format
    isn't a stable external contract.

DIAGNOSTIC (2026-08-18): debug_catalog_scan() bypasses the catalog
abstraction and shows every row in iceberg_tables directly, over THIS
app's own LAKEHOUSE_DB_URI connection -- added after a DAG run reported
'success' in Airflow's own UI while get_zone_stats() kept showing that
same dataset's Silver/Gold as 'never run'. See its own docstring for
the two failure modes it distinguishes.

Every function here degrades gracefully (returns an explicit
"not configured" / empty result, or a per-field "error") rather than
raising when something isn't set up yet or a query fails, so the rest
of the dashboard keeps working even when one zone/table has a problem.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import boto3
import psycopg2
import psycopg2.extras
import requests

LAKEHOUSE_DB_URI = os.environ.get("LAKEHOUSE_DB_URI", "")
SEAWEEDFS_PUBLIC_URL = os.environ.get("SEAWEEDFS_PUBLIC_URL", "")
SEAWEEDFS_INTERNAL_HOST = os.environ.get("SEAWEEDFS_INTERNAL_HOST", "")
SEAWEEDFS_S3_PORT = os.environ.get("SEAWEEDFS_S3_PORT", "8333")
SEAWEEDFS_ACCESS_KEY = os.environ.get("SEAWEEDFS_ACCESS_KEY", "any")
SEAWEEDFS_SECRET_KEY = os.environ.get("SEAWEEDFS_SECRET_KEY", "any")
SEAWEEDFS_S3_REGION = os.environ.get("SEAWEEDFS_S3_REGION", "us-east-1")

# Airflow's own REST API -- ONLY used by trigger_dag_run() below. Not
# required for anything else in this module (every read function above
# talks to Postgres/SeaweedFS directly, not Airflow's API).
AIRFLOW_API_BASE_URL = os.environ.get("AIRFLOW_API_BASE_URL", "")
AIRFLOW_API_USERNAME = os.environ.get("AIRFLOW_API_USERNAME", "")
AIRFLOW_API_PASSWORD = os.environ.get("AIRFLOW_API_PASSWORD", "")

# The app's real dataset storage (see app/object_storage.py) -- where
# dataset_adapter.py's land_bronze/clean_to_silver actually write.
# Bronze zone stats below read from HERE now, not a spike-only bucket.
APP_DATA_BUCKET = os.environ.get("DATAOS_APP_BUCKET", "dataos-app-datasets")

LOGS_PREFIX = "airflow-logs"
DAG_ID = "banking_demo_lakehouse_spike"  # legacy name -- see the DAG's own module docstring

IFRS9_MODELING_COLS = ["RATING", "FACILITY_TYPE", "DPD", "ORIGINATION", "MATURITY", "EAD"]


def _s3_endpoint() -> str:
    if SEAWEEDFS_PUBLIC_URL:
        return SEAWEEDFS_PUBLIC_URL.rstrip("/")
    return f"http://{SEAWEEDFS_INTERNAL_HOST}:{SEAWEEDFS_S3_PORT}"


def is_configured() -> bool:
    return bool(LAKEHOUSE_DB_URI and (SEAWEEDFS_PUBLIC_URL or SEAWEEDFS_INTERNAL_HOST))


def is_trigger_configured() -> bool:
    return bool(AIRFLOW_API_BASE_URL and AIRFLOW_API_USERNAME and AIRFLOW_API_PASSWORD)


def _pg_conn():
    return psycopg2.connect(LAKEHOUSE_DB_URI)


def _s3_client():
    return boto3.client(
        "s3",
        endpoint_url=_s3_endpoint(),
        aws_access_key_id=SEAWEEDFS_ACCESS_KEY,
        aws_secret_access_key=SEAWEEDFS_SECRET_KEY,
    )


def _iceberg_catalog():
    from app.pg_iceberg_catalog import PostgresIcebergCatalog

    return PostgresIcebergCatalog(
        "dataos_spike",
        **{
            "uri": LAKEHOUSE_DB_URI,
            "warehouse": "s3://dataos-spike-iceberg/",
            "s3.endpoint": _s3_endpoint(),
            "s3.access-key-id": SEAWEEDFS_ACCESS_KEY,
            "s3.secret-access-key": SEAWEEDFS_SECRET_KEY,
            "s3.region": SEAWEEDFS_S3_REGION,
            "s3.path-style-access": "true",
            "py-io-impl": "app.boto3_file_io.Boto3FileIO",
        },
    )


def _last_task_run(cur, task_id: str, dataset_name: str) -> dict | None:
    """Last completed run of one task, scoped to this dataset via a
    run_id prefix match -- see MULTI-DATASET in the module docstring
    for why run_id, not Airflow's `conf` column."""
    cur.execute(
        """
        SELECT end_date, state
        FROM task_instance
        WHERE dag_id = %s AND task_id = %s AND end_date IS NOT NULL AND run_id LIKE %s
        ORDER BY end_date DESC
        LIMIT 1
        """,
        (DAG_ID, task_id, f"{dataset_name}__%"),
    )
    row = cur.fetchone()
    if not row:
        return None
    end_date, state = row
    return {"last_run_at": end_date.isoformat(), "last_state": state}


def _freshness_label(iso_timestamp: str | None) -> str:
    if not iso_timestamp:
        return "never run"
    then = datetime.fromisoformat(iso_timestamp)
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - then
    seconds = delta.total_seconds()
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)}m ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h ago"
    return f"{int(seconds // 86400)}d ago"


def get_zone_stats(dataset_name: str | None) -> dict:
    """Live Bronze/Silver/Gold stats for ONE dataset. Bronze comes from
    a plain S3 listing scoped to that dataset's prefix; Silver/Gold
    come from real Iceberg table lookups for that dataset's specific
    tables (not a scan of every table in the namespace)."""
    if not is_configured():
        return {"configured": False, "zones": {}}
    if not dataset_name:
        return {"configured": True, "zones": {}, "error": "dataset_name is required"}

    zones: dict = {}

    # -- Bronze: S3 listing scoped to this dataset's prefix --
    try:
        s3 = _s3_client()
        resp = s3.list_objects_v2(Bucket=APP_DATA_BUCKET, Prefix=f"bronze/{dataset_name}/")
        objects = resp.get("Contents", [])
        total_bytes = sum(o["Size"] for o in objects)
        latest = max((o["LastModified"] for o in objects), default=None)
        zones["bronze"] = {
            "tables": len(objects),
            "size_bytes": total_bytes,
            "last_run_at": latest.isoformat() if latest else None,
            "freshness": _freshness_label(latest.isoformat() if latest else None),
        }
    except Exception as e:  # noqa: BLE001 -- surface as a degraded zone, not a 500 for the whole page
        zones["bronze"] = {"error": str(e)}

    # -- Silver / Gold: this dataset's specific Iceberg tables --
    try:
        catalog = _iceberg_catalog()
        conn = _pg_conn()
        try:
            with conn.cursor() as cur:
                silver_table_id = f"silver.{dataset_name}"
                if catalog.table_exists(silver_table_id):
                    rows = len(catalog.load_table(silver_table_id).scan().to_pandas())
                    run_info = _last_task_run(cur, "silver_to_iceberg", dataset_name) or {}
                    zones["silver"] = {
                        "tables": 1,
                        "rows": rows,
                        "last_run_at": run_info.get("last_run_at"),
                        "last_state": run_info.get("last_state"),
                        "freshness": _freshness_label(run_info.get("last_run_at")),
                    }
                else:
                    zones["silver"] = {"tables": 0, "rows": 0, "last_run_at": None, "freshness": "never run"}

                gold_rows_total = 0
                gold_table_count = 0
                for suffix in ("_ndi", "_ifrs9"):
                    tid = f"gold.{dataset_name}{suffix}"
                    if catalog.table_exists(tid):
                        gold_table_count += 1
                        gold_rows_total += len(catalog.load_table(tid).scan().to_pandas())
                run_info = _last_task_run(cur, "gold_compute", dataset_name) or {}
                zones["gold"] = {
                    "tables": gold_table_count,
                    "rows": gold_rows_total,
                    "last_run_at": run_info.get("last_run_at"),
                    "last_state": run_info.get("last_state"),
                    "freshness": _freshness_label(run_info.get("last_run_at")),
                }
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        zones.setdefault("silver", {"error": str(e)})
        zones.setdefault("gold", {"error": str(e)})

    return {"configured": True, "zones": zones}


def get_pipeline_runs(dataset_name: str | None, limit: int = 10) -> dict:
    """Real DAG run history + per-task status for ONE dataset, straight
    from Airflow's own Postgres metadata tables -- filtered by run_id
    prefix, see MULTI-DATASET in the module docstring."""
    if not is_configured():
        return {"configured": False, "runs": []}
    if not dataset_name:
        return {"configured": True, "runs": [], "error": "dataset_name is required"}

    try:
        conn = _pg_conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT run_id, state, execution_date, start_date, end_date
                    FROM dag_run
                    WHERE dag_id = %s AND run_id LIKE %s
                    ORDER BY execution_date DESC
                    LIMIT %s
                    """,
                    (DAG_ID, f"{dataset_name}__%", limit),
                )
                runs = [dict(r) for r in cur.fetchall()]

                for run in runs:
                    cur.execute(
                        """
                        SELECT task_id, state, start_date, end_date, try_number
                        FROM task_instance
                        WHERE dag_id = %s AND run_id = %s
                        ORDER BY start_date
                        """,
                        (DAG_ID, run["run_id"]),
                    )
                    tasks = [dict(t) for t in cur.fetchall()]
                    for t in tasks:
                        if t["start_date"] and t["end_date"]:
                            t["duration_s"] = round((t["end_date"] - t["start_date"]).total_seconds(), 2)
                        else:
                            t["duration_s"] = None
                    run["tasks"] = tasks

                for run in runs:
                    for key in ("execution_date", "start_date", "end_date"):
                        if run.get(key):
                            run[key] = run[key].isoformat()
                    for t in run["tasks"]:
                        for key in ("start_date", "end_date"):
                            if t.get(key):
                                t[key] = t[key].isoformat()
        finally:
            conn.close()
        return {"configured": True, "runs": runs}
    except Exception as e:  # noqa: BLE001
        return {"configured": True, "runs": [], "error": str(e)}


def get_task_log(run_id: str, task_id: str, try_number: int = 1) -> dict:
    """Reads actual log content from SeaweedFS, at the path Airflow's
    S3TaskHandler writes to. Unaffected by the multi-dataset change --
    run_id (now dataset-prefixed) already uniquely identifies the run,
    same as before."""
    if not is_configured():
        return {"configured": False, "log": None}

    key = f"{LOGS_PREFIX}/dag_id={DAG_ID}/run_id={run_id}/task_id={task_id}/attempt={try_number}.log"
    try:
        s3 = _s3_client()
        obj = s3.get_object(Bucket=APP_DATA_BUCKET, Key=key)
        return {"configured": True, "log": obj["Body"].read().decode("utf-8", errors="replace")}
    except Exception as e:  # noqa: BLE001
        return {"configured": True, "log": None, "error": str(e)}


def trigger_dag_run(dataset_name: str) -> dict:
    """Manually triggers the Lakehouse pipeline DAG for one dataset via
    Airflow's REST API -- deliberately the ONLY way this DAG ever runs.
    Per Khurram's explicit instruction (2026-08-18): auto-triggering on
    every dataset upload would add load and page-load delay for a
    pipeline stage most uploads don't need run immediately -- this is a
    real user action (a button on the dashboard), never a side effect
    of an unrelated request.

    dag_run_id is generated here as "{dataset_name}__{timestamp}" --
    every read function in this module (get_zone_stats,
    get_pipeline_runs) filters by this run_id PREFIX via plain SQL
    LIKE, deliberately not by querying Airflow's internal `conf`
    column's JSON contents directly (its on-disk storage format isn't
    a stable external contract, and varies across Airflow versions).
    conf is still passed for the DAG's OWN internal use -- read
    correctly via Airflow's own context/ORM inside the running task,
    where that concern doesn't apply."""
    if not is_trigger_configured():
        raise ValueError(
            "Airflow trigger isn't configured on this service -- set "
            "AIRFLOW_API_BASE_URL, AIRFLOW_API_USERNAME, and AIRFLOW_API_PASSWORD."
        )
    if not dataset_name:
        raise ValueError("dataset_name is required to trigger the pipeline.")

    run_id = f"{dataset_name}__{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')}Z"
    url = f"{AIRFLOW_API_BASE_URL.rstrip('/')}/api/v1/dags/{DAG_ID}/dagRuns"
    try:
        resp = requests.post(
            url,
            json={"dag_run_id": run_id, "conf": {"dataset_name": dataset_name}},
            auth=(AIRFLOW_API_USERNAME, AIRFLOW_API_PASSWORD),
            timeout=15,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        detail = getattr(e.response, "text", "") if getattr(e, "response", None) is not None else ""
        raise ValueError(f"Failed to trigger the Lakehouse pipeline: {e} {detail}".strip())

    return {"dataset_name": dataset_name, "run_id": run_id, "triggered": True}


def debug_catalog_scan(dataset_name: str | None = None) -> dict:
    """Diagnostic (2026-08-18) -- added after a real DAG run showed
    'success' in Airflow's own UI, but get_zone_stats() kept reporting
    Silver/Gold as 'never run' for that same dataset. Bypasses the
    PostgresIcebergCatalog abstraction entirely and queries
    iceberg_tables directly with raw SQL, over THIS app's own
    LAKEHOUSE_DB_URI connection -- the exact same underlying data
    table_exists()/load_table() read, but with every row visible, not
    just a yes/no for one name. Isolates two genuinely different
    failure modes:
      1. This app's LAKEHOUSE_DB_URI points at a DIFFERENT Postgres
         instance/database than the one the DAG's ICEBERG_CATALOG_DB_URI
         writes to -- in which case NO rows show up here at all, for
         ANY dataset, not just this one.
      2. Both point at the same database, but the row exists under a
         different table_namespace/table_name than expected (a real
         naming-convention mismatch) -- in which case rows DO show up,
         just not under the exact name get_zone_stats() is looking for.
    Deliberately unauthenticated, same pattern as every other
    /api/debug/* endpoint. The Postgres host/db name are shown (not
    the full credentialed URI) -- enough to compare against the other
    service's connection string without exposing the password."""
    result: dict = {"configured": is_configured()}
    if not LAKEHOUSE_DB_URI:
        result["error"] = "LAKEHOUSE_DB_URI is not set on this service."
        return result

    try:
        from urllib.parse import urlparse
        parsed = urlparse(LAKEHOUSE_DB_URI)
        result["connected_to_host"] = parsed.hostname
        result["connected_to_db"] = (parsed.path or "").lstrip("/")
    except Exception:  # noqa: BLE001 -- masking is best-effort, never block the real diagnostic on it
        pass

    try:
        conn = _pg_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT catalog_name, table_namespace, table_name, "
                    "(metadata_location IS NOT NULL) AS has_metadata "
                    "FROM iceberg_tables ORDER BY table_namespace, table_name"
                )
                all_rows = cur.fetchall()
                result["all_iceberg_tables_visible_to_this_app"] = [
                    {"catalog_name": r[0], "namespace": r[1], "table_name": r[2], "has_metadata": r[3]}
                    for r in all_rows
                ]
                result["total_rows"] = len(all_rows)

                if dataset_name:
                    cur.execute(
                        "SELECT catalog_name, table_namespace, table_name, metadata_location "
                        "FROM iceberg_tables WHERE table_namespace IN ('silver', 'gold') "
                        "AND table_name LIKE %s",
                        (f"%{dataset_name}%",),
                    )
                    matches = cur.fetchall()
                    result["rows_matching_dataset_name"] = [
                        {"catalog_name": r[0], "namespace": r[1], "table_name": r[2], "metadata_location": r[3]}
                        for r in matches
                    ]

                cur.execute(
                    "SELECT run_id, dag_id, state FROM dag_run "
                    "WHERE run_id LIKE %s ORDER BY execution_date DESC LIMIT 5",
                    (f"%{dataset_name}%" if dataset_name else "%",),
                )
                result["matching_dag_runs_visible_to_this_app"] = [
                    {"run_id": r[0], "dag_id": r[1], "state": r[2]} for r in cur.fetchall()
                ]
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        result["query_error"] = f"{type(e).__name__}: {e}"

    return result


def debug_metadata_read(namespace: str = "silver", table_name: str = "ifrs9_portfolio") -> dict:
    """Diagnostic that found the pyarrow-vs-boto3 bug (kept for future
    diagnosis if this ever regresses -- e.g. a pyiceberg/pyarrow version
    bump). Fetches the SAME Iceberg metadata JSON file three ways:
      1. plain boto3 GetObject (known-working)
      2. pyarrow's raw S3FileSystem read (pyiceberg's old default --
         confirmed broken on this connection, 2026-08-17)
      3. the catalog's CONFIGURED file reader (Boto3FileIO)."""
    result: dict = {"namespace": namespace, "table_name": table_name}

    try:
        conn = _pg_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT metadata_location FROM iceberg_tables "
                    "WHERE table_namespace = %s AND table_name = %s",
                    (namespace, table_name),
                )
                row = cur.fetchone()
        finally:
            conn.close()
        if not row:
            result["error"] = f"No catalog row for {namespace}.{table_name}"
            return result
        metadata_location = row[0]
        result["metadata_location"] = metadata_location
    except Exception as e:  # noqa: BLE001
        result["error"] = f"Postgres lookup failed: {e}"
        return result

    without_scheme = metadata_location.removeprefix("s3://")
    bucket, _, key = without_scheme.partition("/")

    try:
        s3 = _s3_client()
        obj = s3.get_object(Bucket=bucket, Key=key)
        body = obj["Body"].read()
        result["boto3_read"] = {
            "bucket": bucket,
            "key": key,
            "content_length_bytes": len(body),
            "first_100_chars": body[:100].decode("utf-8", errors="replace"),
        }
    except Exception as e:  # noqa: BLE001
        result["boto3_read"] = {"error": str(e)}

    try:
        from pyiceberg.io.pyarrow import PyArrowFileIO

        pa_io = PyArrowFileIO({
            "s3.endpoint": _s3_endpoint(),
            "s3.access-key-id": SEAWEEDFS_ACCESS_KEY,
            "s3.secret-access-key": SEAWEEDFS_SECRET_KEY,
            "s3.region": SEAWEEDFS_S3_REGION,
            "s3.path-style-access": "true",
        })
        input_file = pa_io.new_input(metadata_location)
        with input_file.open() as f:
            content = f.read()
        result["pyarrow_read"] = {
            "content_length_bytes": len(content),
            "first_100_chars": content[:100].decode("utf-8", errors="replace") if content else "",
        }
    except Exception as e:  # noqa: BLE001
        result["pyarrow_read"] = {"error": f"{type(e).__name__}: {e}"}

    try:
        from pyiceberg.io import load_file_io

        catalog = _iceberg_catalog()
        io = load_file_io(properties=catalog.properties, location=metadata_location)
        input_file = io.new_input(metadata_location)
        with input_file.open() as f:
            content = f.read()
        result["configured_reader_read"] = {
            "reader_class": type(io).__name__,
            "content_length_bytes": len(content),
            "first_100_chars": content[:100].decode("utf-8", errors="replace") if content else "",
        }
    except Exception as e:  # noqa: BLE001
        result["configured_reader_read"] = {"error": f"{type(e).__name__}: {e}"}

    return result
