"""
DataOS 3.0 -- Lakehouse pipeline DAG. Generalized 2026-08-18 from the
Item 1 spike, which proved this exact architecture (SeaweedFS + real
Iceberg tables + Airflow orchestration + Postgres catalog) against
exactly one hardcoded file. dag_id and filename are kept as
"banking_demo_lakehouse_spike" on purpose -- renaming either would
orphan the existing run history and the DAG_ID constant already wired
through app/lakehouse_client.py. The name is legacy; the DAG itself is
now generic to any dataset.

WHAT CHANGED, AND WHY (2026-08-18): the original version re-parsed a
hardcoded raw Excel file (Banking_Demo_Dataset.xlsx, sheets
"IFRS9_Portfolio"/"Customer_MDM") and unconditionally computed IFRS 9 +
NDI as Gold -- correct for proving the stack once, but specific to one
file's shape, not a real multi-dataset pipeline. This version instead:

  - Reads the ALREADY-CLEANED Silver CSV that app/adapters/
    dataset_adapter.py already produces for every dataset uploaded via
    chat or the dashboard (silver/{dataset_name}/cleaned.csv in the
    dataos-app-datasets bucket) -- the real generic Silver data for
    ANY dataset, not a re-derivation specific to the Banking Demo file.
  - Writes it as a genuine Iceberg table, namespaced per dataset
    (silver.{dataset_name}).
  - Computes NDI unconditionally as gold.{dataset_name}_ndi -- NDI is
    genuinely dataset-independent (Dr. Saber's fixed SDAIA baseline,
    not derived from the uploaded data at all), so this is correct for
    every dataset regardless of shape.
  - Computes IFRS 9 ECL ONLY when the dataset actually has the
    required modeling columns (RATING, FACILITY_TYPE, DPD, ORIGINATION,
    MATURITY, EAD) as gold.{dataset_name}_ifrs9 -- the SAME readiness
    check banking_adapter.py's SAMA view already uses for IFRS9
    readiness, reused here rather than re-invented, so "is this
    dataset IFRS9-ready" means the same thing everywhere in the
    codebase. Datasets without that shape simply don't get an IFRS 9
    Gold table -- not a failure, the same honesty principle already
    established for SAMA's not_measured domains.

TRIGGER: manually, per dataset, from the Lakehouse dashboard's "Run
pipeline" button (POST /api/lakehouse/trigger -- see
app/lakehouse_client.py's trigger_dag_run()). Deliberately NEVER
triggered automatically on upload -- per Khurram's explicit
instruction (2026-08-18): auto-triggering here would add load and
page-load delay to every single upload, for a pipeline stage most
uploads don't need run immediately. dataset_name is passed via
dag_run.conf when triggered.

RUN IDENTIFICATION: dag_run_id is generated as "{dataset_name}__
{timestamp}" at trigger time. Every OTHER read in lakehouse_client.py
(zone stats, pipeline runs, task logs) filters by this run_id PREFIX
via plain SQL LIKE -- deliberately NOT by querying Airflow's internal
`conf` column's JSON contents directly, since that column's on-disk
storage format (native JSON vs. a serialized-text TypeDecorator) isn't
a stable contract to depend on from outside Airflow's own ORM, and
varies across Airflow versions. run_id has always been a plain
queryable VARCHAR column.

STORAGE: reads/writes the SeaweedFS instance this orchestrator already
has PRIVATE, same-region network access to (SEAWEEDFS_INTERNAL_HOST)
-- this DAG runs in the same region (Singapore) as SeaweedFS, so none
of the cross-region PUT-403 issue documented in app/object_storage.py
applies here; that issue was specific to the Oregon-hosted chat/
dashboard app's PUBLIC-endpoint writes. Reads the Silver CSV from
dataos-app-datasets (the SAME bucket dataset_adapter.py writes to --
the app's real dataset storage, not the old spike-only bucket), and
writes Iceberg tables into the separate dataos-spike-iceberg
table-bucket, exactly as the original spike did.

CATALOG BACKEND: unchanged from the original spike -- PostgresIcebergCatalog
(pg_iceberg_catalog.py, this same folder), not SeaweedFS's own built-in
Iceberg REST Catalog (which failed a concurrent-writer test, see the
original spike's history) and not pyiceberg's own SqlCatalog (needs a
sqlalchemy version incompatible with this Airflow image). See the
original version of this file (git history) for the full investigation
if this ever needs revisiting.

CONNECTION LEAK FIX (2026-08-18): pg_iceberg_catalog.py's own docstring
has the full story -- it now holds ONE shared Postgres connection per
instance instead of opening a fresh one per method call, which means
every catalog instance created below MUST be closed when the task is
done with it. Both tasks below now use it as a context manager
(`with _iceberg_catalog() as catalog:`) rather than leaving it open.

FIELD LINEAGE (2026-08-18, Item 6 Step 3): silver_to_iceberg and
gold_compute now declare Airflow `inlets`/`outlets` using Jinja-
templated Dataset URIs (dataset_name is only known at trigger time via
dag_run.conf, not at DAG-parse time -- Airflow's Dataset URIs are
rendered at task-run time, same as any other templated field, which is
exactly what's needed here). This is deliberately NOT the OpenLineage
extractor path -- our tasks are plain TaskFlow/@task (i.e.
_PythonDecoratedOperator under the hood), and Airflow's OpenLineage
provider has no custom extractor for that operator class (confirmed
directly against the provider's own docs: Python operators are treated
as opaque, "black box" -- see Implementing OpenLineage in Operators /
Troubleshooting docs). The provider's documented, ALWAYS-applied
fallback for operators with no extractor is exactly inlets/outlets --
so this is the correct, not a partial or best-effort, mechanism for
this specific operator type, not a workaround. URIs point at the
Iceberg tables' and Silver CSV's REAL storage locations (s3://...),
not synthetic identifiers -- so a lineage consumer reading them can
trace straight back to the actual object in SeaweedFS. verify_silver_
ready deliberately has no inlets/outlets of its own -- it's a
readiness check, not a data-producing/consuming step; the Silver CSV
enters the lineage graph as silver_to_iceberg's inlet instead, which is
the first task that actually treats it as pipeline input.
"""
from __future__ import annotations

import io
import os
import sys
from datetime import datetime, timezone

import boto3
import pandas as pd
from airflow.datasets import Dataset
from airflow.decorators import dag, task
from airflow.operators.python import get_current_context

# ---------------------------------------------------------------------------
# SeaweedFS -- internal (private-network) address only, same-region.
# ---------------------------------------------------------------------------
SEAWEEDFS_HOST = os.environ["SEAWEEDFS_INTERNAL_HOST"]
S3_PORT = os.environ.get("SEAWEEDFS_S3_PORT", "8333")
S3_ENDPOINT = f"http://{SEAWEEDFS_HOST}:{S3_PORT}"
S3_REGION = os.environ.get("SEAWEEDFS_S3_REGION", "us-east-1")

# The app's REAL dataset storage (see app/object_storage.py) -- where
# dataset_adapter.py's land_bronze/clean_to_silver actually write. This
# DAG reads Silver from here now, NOT the old spike-only "dataos-spike"
# bucket, since that bucket only ever held the one hardcoded demo file.
APP_DATA_BUCKET = os.environ.get("DATAOS_APP_BUCKET", "dataos-app-datasets")

# Dedicated Iceberg table-bucket for Silver/Gold DATA -- unchanged from
# the original spike (must be a different bucket name from any plain
# object-store bucket; SeaweedFS's S3 tooling refuses to register a
# bucket as a table-bucket otherwise).
ICEBERG_BUCKET = "dataos-spike-iceberg"

S3_ACCESS_KEY = os.environ.get("SEAWEEDFS_ACCESS_KEY", "any")
S3_SECRET_KEY = os.environ.get("SEAWEEDFS_SECRET_KEY", "any")

ICEBERG_CATALOG_DB_URI = os.environ.get(
    "ICEBERG_CATALOG_DB_URI",
    os.environ.get("AIRFLOW__DATABASE__SQL_ALCHEMY_CONN", ""),
)

SPIKE_DAGS_ROOT = "/opt/spike-dags"

# Same readiness check banking_adapter.py's SAMA view already uses --
# reused, not re-invented, so "IFRS9-ready" means the same thing
# everywhere in the codebase.
IFRS9_MODELING_COLS = ["RATING", "FACILITY_TYPE", "DPD", "ORIGINATION", "MATURITY", "EAD"]


def _s3_client():
    return boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT,
        aws_access_key_id=S3_ACCESS_KEY,
        aws_secret_access_key=S3_SECRET_KEY,
    )


def _iceberg_catalog():
    """Returns a PostgresIcebergCatalog instance -- callers MUST use it
    as a context manager (`with _iceberg_catalog() as catalog:`) or call
    `.close()` explicitly when done. See CONNECTION LEAK FIX in the
    module docstring."""
    if not ICEBERG_CATALOG_DB_URI:
        raise RuntimeError(
            "No Iceberg catalog DB connection string found -- set "
            "ICEBERG_CATALOG_DB_URI or AIRFLOW__DATABASE__SQL_ALCHEMY_CONN."
        )
    sys.path.insert(0, SPIKE_DAGS_ROOT)
    from pg_iceberg_catalog import PostgresIcebergCatalog
    return PostgresIcebergCatalog(
        "dataos_spike",
        **{
            "uri": ICEBERG_CATALOG_DB_URI,
            "warehouse": f"s3://{ICEBERG_BUCKET}/",
            "s3.endpoint": S3_ENDPOINT,
            "s3.access-key-id": S3_ACCESS_KEY,
            "s3.secret-access-key": S3_SECRET_KEY,
            "s3.region": S3_REGION,
            "s3.path-style-access": "true",
        },
    )


def _prep_for_arrow(df: pd.DataFrame) -> pd.DataFrame:
    """pyarrow's pandas->Arrow conversion requires each column to hold a
    single consistent type -- coerce genuinely mixed 'object' columns to
    string, matching how these columns are already treated once they
    hit CSV elsewhere in the app. Unchanged from the original spike."""
    out = df.copy()
    for col in out.columns:
        if out[col].dtype == object:
            non_null = out[col].dropna()
            if non_null.empty:
                continue
            if len({type(v) for v in non_null}) > 1:
                out[col] = out[col].apply(lambda v: None if pd.isna(v) else str(v))
    return out


def _write_iceberg_table(catalog, namespace: str, table_name: str, df: pd.DataFrame) -> str:
    import pyarrow as pa
    catalog.create_namespace_if_not_exists(namespace)
    table_id = f"{namespace}.{table_name}"
    arrow_table = pa.Table.from_pandas(_prep_for_arrow(df))
    if catalog.table_exists(table_id):
        catalog.load_table(table_id).overwrite(arrow_table)
    else:
        catalog.create_table(table_id, schema=arrow_table.schema).append(arrow_table)
    return table_id


def _get_dataset_name() -> str:
    """dataset_name comes from dag_run.conf (set by trigger_dag_run()) --
    read via Airflow's own ORM/context here, which correctly
    deserializes conf regardless of its on-disk storage format (unlike
    a raw SQL read from outside Airflow -- see RUN IDENTIFICATION in
    the module docstring for why external reads use run_id instead)."""
    ctx = get_current_context()
    conf = ctx["dag_run"].conf or {}
    name = conf.get("dataset_name") or (ctx.get("params") or {}).get("dataset_name")
    if not name:
        raise ValueError(
            "No dataset_name provided -- trigger this DAG with "
            'conf={"dataset_name": "..."}  (see trigger_dag_run()).'
        )
    return name


@dag(
    dag_id="banking_demo_lakehouse_spike",  # legacy name, kept for run-history continuity -- see module docstring
    description="DataOS 3.0 Lakehouse pipeline -- Silver/Gold Iceberg promotion for any uploaded dataset",
    schedule=None,  # manually triggered only, per dataset -- never automatic, see module docstring
    start_date=datetime(2026, 8, 1),
    catchup=False,
    params={"dataset_name": ""},
    tags=["dataos-3.0", "lakehouse", "multi-dataset"],
)
def banking_demo_lakehouse_spike():

    @task
    def verify_silver_ready() -> dict:
        """Confirms this dataset's Silver CSV genuinely exists and is
        readable in the app's real dataset storage. Object storage
        itself is the source of truth for "is this dataset ready to
        promote" -- deliberately not a second read of dataset_adapter's
        own Postgres row, which would couple this DAG to that table's
        schema for no real benefit. No inlets/outlets here on purpose --
        see FIELD LINEAGE in the module docstring."""
        dataset_name = _get_dataset_name()
        s3 = _s3_client()
        key = f"silver/{dataset_name}/cleaned.csv"
        try:
            obj = s3.get_object(Bucket=APP_DATA_BUCKET, Key=key)
        except s3.exceptions.NoSuchKey:
            raise ValueError(
                f"No Silver data found for dataset '{dataset_name}' at "
                f"s3://{APP_DATA_BUCKET}/{key} -- upload and clean it first."
            )
        raw_bytes = obj["Body"].read()
        if not raw_bytes:
            raise ValueError(f"Silver file for '{dataset_name}' is empty.")
        return {"dataset_name": dataset_name, "silver_key": key, "size_bytes": len(raw_bytes)}

    @task(
        inlets=[Dataset("s3://{{ params.get('_bucket', 'dataos-app-datasets') }}/silver/{{ dag_run.conf['dataset_name'] }}/cleaned.csv")],
        outlets=[Dataset("s3://dataos-spike-iceberg/silver/{{ dag_run.conf['dataset_name'] }}/")],
    )
    def silver_to_iceberg(verify_result: dict) -> dict:
        """Reads the real, already-cleaned Silver CSV and writes it as a
        genuine Iceberg table -- ACID, versioned, schema-tracked. No
        re-derivation of Silver logic here -- dataset_adapter.py already
        did that once, correctly, for every dataset regardless of
        shape; this task's only job is the storage-format promotion.

        FIELD LINEAGE: inlets/outlets above use the SAME real storage
        locations this function actually reads/writes (see FIELD
        LINEAGE in the module docstring for why this is the correct
        mechanism, not a workaround, for a plain @task operator)."""
        dataset_name = verify_result["dataset_name"]
        s3 = _s3_client()
        raw_bytes = s3.get_object(Bucket=APP_DATA_BUCKET, Key=verify_result["silver_key"])["Body"].read()
        df = pd.read_csv(io.BytesIO(raw_bytes))

        # Context manager -- see CONNECTION LEAK FIX in the module
        # docstring: the catalog now holds one shared connection for
        # its lifetime and must be explicitly closed when done.
        with _iceberg_catalog() as catalog:
            table_id = _write_iceberg_table(catalog, "silver", dataset_name, df)

        return {"dataset_name": dataset_name, "silver_table": table_id, "row_count": len(df)}

    @task(
        inlets=[Dataset("s3://dataos-spike-iceberg/silver/{{ dag_run.conf['dataset_name'] }}/")],
        outlets=[
            Dataset("s3://dataos-spike-iceberg/gold/{{ dag_run.conf['dataset_name'] }}_ndi/"),
            Dataset("s3://dataos-spike-iceberg/gold/{{ dag_run.conf['dataset_name'] }}_ifrs9/"),
        ],
    )
    def gold_compute(silver_result: dict) -> dict:
        """NDI is unconditional (dataset-independent -- Dr. Saber's
        fixed baseline, not derived from this dataset at all). IFRS 9
        only runs when this dataset actually has the modeling columns
        it needs -- see IFRS9_MODELING_COLS above. No new computation
        path either way -- same banking_adapter.py functions already
        signed off in production.

        FIELD LINEAGE: the _ifrs9 outlet above is declared unconditionally
        even though this task doesn't always actually write that table
        (see ifrs9_ready below) -- Airflow's inlets/outlets are fixed at
        task-definition time, not conditionally per-run. For datasets
        that don't get an IFRS 9 table, this outlet simply never gets
        real data behind it; not hidden, just a known imprecision of
        this mechanism, worth remembering if the Field Lineage page
        ever needs to distinguish "declared" from "actually written."
        """
        sys.path.insert(0, SPIKE_DAGS_ROOT)
        from app.adapters import banking_adapter as ba

        dataset_name = silver_result["dataset_name"]

        # Same context-manager usage as silver_to_iceberg -- ONE
        # connection shared across load_table() plus one or two
        # _write_iceberg_table() calls below, closed once at the end.
        with _iceberg_catalog() as catalog:
            df = catalog.load_table(silver_result["silver_table"]).scan().to_pandas()

            gold_tables = []

            ndi_result = ba.run_ndi_radar({})
            ndi_table = _write_iceberg_table(
                catalog, "gold", f"{dataset_name}_ndi",
                pd.DataFrame([{
                    "display_score": ndi_result["display_score"],
                    "overall_compliance_pct": ndi_result["overall_compliance_pct"],
                    "maturity_level": ndi_result["maturity_level"],
                }]),
            )
            gold_tables.append(ndi_table)

            ifrs9_ready = all(c in df.columns for c in IFRS9_MODELING_COLS)
            total_ecl = None
            if ifrs9_ready:
                ifrs9_result = ba.run_ifrs9({"csv_content": df.to_csv(index=False), "scenario": "base"})
                ifrs9_table = _write_iceberg_table(
                    catalog, "gold", f"{dataset_name}_ifrs9",
                    pd.DataFrame([{
                        "total_computed_ecl": ifrs9_result["total_computed_ecl"],
                        "reporting_date": ifrs9_result["reporting_date"],
                        "engine_mode": ifrs9_result["engine_mode"],
                    }]),
                )
                gold_tables.append(ifrs9_table)
                total_ecl = ifrs9_result["total_computed_ecl"]

        return {
            "dataset_name": dataset_name,
            "gold_tables": gold_tables,
            "ndi_display_score": ndi_result["display_score"],
            "ifrs9_applicable": ifrs9_ready,
            "total_ecl_sar": total_ecl,
        }

    verify_result = verify_silver_ready()
    silver_result = silver_to_iceberg(verify_result)
    gold_compute(silver_result)


banking_demo_lakehouse_spike()
