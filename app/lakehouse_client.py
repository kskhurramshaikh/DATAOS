"""
Read-only data access for the Lakehouse dashboard (Item 2 of the DataOS
3.0 Development Queue) -- reads REAL data from the spike infrastructure:

  - Bronze zone stats: direct S3 listing against SeaweedFS via boto3.
  - Silver/Gold zone stats: real Iceberg table row counts via
    PostgresIcebergCatalog (same module the spike DAG uses).
  - Pipeline run history/status: Airflow's own Postgres metadata tables
    (dag_run, task_instance), queried directly with plain SQL -- Airflow
    doesn't expose a lighter-weight read path for this than its own DB,
    and this app already needs a Postgres connection for the Iceberg
    catalog above, so it's the same connection, no new dependency.
  - Task log content: read from SeaweedFS, at the path Airflow's own
    S3TaskHandler writes to (see spike/entrypoint.sh's remote-logging
    setup) -- s3://dataos-spike/airflow-logs/dag_id=X/run_id=Y/
    task_id=Z/attempt=N.log

CROSS-REGION NOTE (2026-08-17): this app (dataos-2-0-pipeline) runs in
Oregon; the spike services (dataos-spike-orchestrator/-storage, and
their Postgres) run in Singapore. Render's private networking -- the
internal hostnames like SEAWEEDFS_INTERNAL_HOST and Postgres's
dpg-...-a short host -- only resolves WITHIN a region (confirmed
directly: cross-region requests to those hostnames fail with "Name or
service not known", not a permissions/timeout error). So this module
talks to both services over their PUBLIC endpoints instead:
  - LAKEHOUSE_DB_URI must be Postgres's *External* Database URL (from
    the Render Postgres dashboard), not the internal one used by
    Airflow/the spike orchestrator.
  - SEAWEEDFS_PUBLIC_URL is the storage service's public HTTPS URL
    (https://dataos-spike-storage.onrender.com). Render only exposes
    ONE port publicly per service, chosen by that service's own PORT
    env var -- dataos-spike-storage's PORT was changed from 8888
    (SeaweedFS's Filer UI) to 8333 (the S3 API) specifically so this
    app can reach it. If PORT ever gets changed back, this stops
    working with a connection-refused-style error, not a silent one.
  - Falls back to the internal-hostname construction if
    SEAWEEDFS_PUBLIC_URL isn't set, so this still works unmodified if
    this module is ever reused by something running in the same region
    as the spike services.

Every function here degrades gracefully (returns an explicit
"not configured" / empty result) rather than raising when the required
env vars aren't set yet, so the rest of the app keeps working before
this cross-service wiring is completed.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import boto3
import psycopg2
import psycopg2.extras

LAKEHOUSE_DB_URI = os.environ.get("LAKEHOUSE_DB_URI", "")
SEAWEEDFS_PUBLIC_URL = os.environ.get("SEAWEEDFS_PUBLIC_URL", "")
SEAWEEDFS_INTERNAL_HOST = os.environ.get("SEAWEEDFS_INTERNAL_HOST", "")
SEAWEEDFS_S3_PORT = os.environ.get("SEAWEEDFS_S3_PORT", "8333")
SEAWEEDFS_ACCESS_KEY = os.environ.get("SEAWEEDFS_ACCESS_KEY", "any")
SEAWEEDFS_SECRET_KEY = os.environ.get("SEAWEEDFS_SECRET_KEY", "any")
SEAWEEDFS_S3_REGION = os.environ.get("SEAWEEDFS_S3_REGION", "us-east-1")

BRONZE_BUCKET = "dataos-spike"
ICEBERG_BUCKET = "dataos-spike-iceberg"
LOGS_PREFIX = "airflow-logs"
DAG_ID = "banking_demo_lakehouse_spike"

ZONE_NAMESPACES = {"bronze": None, "silver": "silver", "gold": "gold"}


def _s3_endpoint() -> str:
    """Public HTTPS endpoint if set (the cross-region path this app
    actually needs), else falls back to the internal host:port form."""
    if SEAWEEDFS_PUBLIC_URL:
        return SEAWEEDFS_PUBLIC_URL.rstrip("/")
    return f"http://{SEAWEEDFS_INTERNAL_HOST}:{SEAWEEDFS_S3_PORT}"


def is_configured() -> bool:
    return bool(LAKEHOUSE_DB_URI and (SEAWEEDFS_PUBLIC_URL or SEAWEEDFS_INTERNAL_HOST))


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
            "warehouse": f"s3://{ICEBERG_BUCKET}/",
            "s3.endpoint": _s3_endpoint(),
            "s3.access-key-id": SEAWEEDFS_ACCESS_KEY,
            "s3.secret-access-key": SEAWEEDFS_SECRET_KEY,
            "s3.region": SEAWEEDFS_S3_REGION,
            "s3.path-style-access": "true",
        },
    )


def _last_task_run(cur, task_id: str) -> dict | None:
    cur.execute(
        """
        SELECT end_date, state
        FROM task_instance
        WHERE dag_id = %s AND task_id = %s AND end_date IS NOT NULL
        ORDER BY end_date DESC
        LIMIT 1
        """,
        (DAG_ID, task_id),
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


def get_zone_stats() -> dict:
    """Live Bronze/Silver/Gold stats. Bronze comes from a plain S3 listing
    (it's raw files, not Iceberg tables); Silver/Gold come from real
    Iceberg table scans via the Postgres catalog."""
    if not is_configured():
        return {"configured": False, "zones": {}}

    zones: dict = {}

    # -- Bronze: plain S3 object listing --
    try:
        s3 = _s3_client()
        resp = s3.list_objects_v2(Bucket=BRONZE_BUCKET, Prefix="bronze/")
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

    # -- Silver / Gold: real Iceberg table row counts --
    try:
        catalog = _iceberg_catalog()
        conn = _pg_conn()
        try:
            with conn.cursor() as cur:
                for zone_key, namespace, task_id in [
                    ("silver", "silver", "silver_transform"),
                    ("gold", "gold", "gold_compute"),
                ]:
                    try:
                        table_ids = catalog.list_tables(namespace)
                    except Exception:
                        table_ids = []
                    total_rows = 0
                    for tid in table_ids:
                        try:
                            table = catalog.load_table(tid)
                            total_rows += len(table.scan().to_pandas())
                        except Exception:
                            pass
                    run_info = _last_task_run(cur, task_id) or {}
                    zones[zone_key] = {
                        "tables": len(table_ids),
                        "rows": total_rows,
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


def get_pipeline_runs(limit: int = 10) -> dict:
    """Real DAG run history + per-task status, straight from Airflow's own
    Postgres metadata tables."""
    if not is_configured():
        return {"configured": False, "runs": []}

    try:
        conn = _pg_conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT run_id, state, execution_date, start_date, end_date
                    FROM dag_run
                    WHERE dag_id = %s
                    ORDER BY execution_date DESC
                    LIMIT %s
                    """,
                    (DAG_ID, limit),
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
    S3TaskHandler writes to (see spike/entrypoint.sh)."""
    if not is_configured():
        return {"configured": False, "log": None}

    key = f"{LOGS_PREFIX}/dag_id={DAG_ID}/run_id={run_id}/task_id={task_id}/attempt={try_number}.log"
    try:
        s3 = _s3_client()
        obj = s3.get_object(Bucket=BRONZE_BUCKET, Key=key)
        return {"configured": True, "log": obj["Body"].read().decode("utf-8", errors="replace")}
    except Exception as e:  # noqa: BLE001
        return {"configured": True, "log": None, "error": str(e)}
