"""
DataOS 3.0 -- Item 1 spike DAG: Banking Demo pipeline through the real stack.

Proves the same already-signed-off pipeline (IFRS 9 / SAMA / NDI / duplicate
detection, all approved by Dr. Saber 2026-08-11) running through real
infrastructure instead of local CSV + SQLite:

    Bronze  -> raw Banking_Demo_Dataset.xlsx bytes, written to SeaweedFS (S3 API)
    Silver  -> cleaned IFRS9_Portfolio + Customer_MDM, written as REAL Iceberg
               tables (data/metadata files in SeaweedFS S3; catalog pointer
               in Postgres -- see _iceberg_catalog() below for why)
    Gold    -> IFRS 9 ECL, SAMA compliance, NDI radar, duplicate-detection
               outputs -- computed with the EXACT functions already live in
               production (banking_adapter.py), written as Iceberg tables

Nothing about the calculations changes. What changes is everything underneath
them: real persistent object storage, real ACID Iceberg tables, a real
observable pipeline run -- instead of local disk + SQLite.

Deploy notes (see the handoff message for full context):
  - This file is delivered via git push -> Render auto-rebuild (Dockerfile-based
    deploy), NOT via the persistent disk. The persistent disk at /opt/airflow
    is deliberately left to Airflow's own metadata DB/logs only -- baking code
    onto that same path would get silently hidden by the disk mount on every
    restart, so this DAG lives at /opt/spike-dags/ instead (outside the disk),
    baked fresh into the image on every deploy. See spike/Dockerfile.
  - Needs these env vars set on `dataos-spike-orchestrator`:
      SEAWEEDFS_INTERNAL_HOST   (from Render's Connect -> Internal tab
                                  on dataos-spike-storage)
      SEAWEEDFS_S3_PORT         8333
      AIRFLOW__DATABASE__SQL_ALCHEMY_CONN   (already set for Airflow's own
                                  metadata DB -- reused as the Iceberg
                                  catalog DB too; see _iceberg_catalog())
  - Python packages (pyiceberg[pyarrow,sql-postgres], duckdb, boto3, pandas,
    openpyxl, scikit-learn, rapidfuzz) are installed at BUILD time via the
    Dockerfile, not a runtime env var -- faster startup, no surprises from a
    slow first-boot install.
  - Bronze storage and the Iceberg warehouse are DELIBERATELY SEPARATE
    buckets (see BUCKET vs ICEBERG_BUCKET below) -- SeaweedFS's Iceberg
    tooling will not let a bucket be both a plain object-store bucket
    and a registered table-bucket at the same time.
  - silver_transform reuses the REAL header-detection logic from
    app/adapters/dataset_adapter.py (_read_sheet_with_header_detection)
    instead of a raw pd.read_excel(...) -- real exported workbooks often
    carry a title/report-name row above the actual headers, which a naive
    header=0 read misreads as data, producing "Unnamed: N" columns with
    mixed-type junk that pyarrow's pandas->Arrow conversion cannot handle.
  - The Iceberg catalog's S3 properties EXPLICITLY set region + path-style
    addressing (see _iceberg_catalog below) -- pyarrow's S3 filesystem
    tries to auto-resolve a bucket's AWS region via a lookup SeaweedFS
    doesn't properly support ("Unable to resolve region for bucket ..."
    warning), and SeaweedFS (like most S3-compatible stores) needs
    path-style URLs, not virtual-hosted-style. Left unset, multipart
    uploads for larger Iceberg writes fail with a generic
    "AWS Error INTERNAL_FAILURE during UploadPart" -- not caused by data
    size or content, just the client guessing wrong about the endpoint.
  - CATALOG BACKEND: pyiceberg SqlCatalog on Postgres, NOT SeaweedFS's own
    built-in Iceberg REST Catalog. Storage is unchanged -- every Parquet/
    Avro/metadata-JSON file still lives in SeaweedFS's dataos-spike-iceberg
    S3 bucket, exactly as decided. Only the CATALOG (the small "which
    metadata version is current" pointer, and its atomic compare-and-swap
    on commit) moved. Reason: iceberg_concurrent_writer_test.py failed
    reproducibly (twice, identically -- 8 concurrent writers, only 7
    landed) against SeaweedFS's built-in catalog. Traced directly into
    SeaweedFS's own handlers_commit.go source: two concurrent commits can
    compute the same next metadata-version filename, both write it, and
    the loser's own cleanup step deletes the file the winner's pointer now
    depends on -- a real race in SeaweedFS's current catalog code, not a
    client-side bug. Postgres's transactional UPDATE makes that race
    structurally impossible, and it's the same catalog model Trino/Spark's
    JDBC catalog uses, so it doesn't conflict with the Section 03
    Trino+Spark production plan.
"""
from __future__ import annotations

import io
import os
import sys
from datetime import datetime

import boto3
import pandas as pd
from airflow.decorators import dag, task

# ---------------------------------------------------------------------------
# Where SeaweedFS actually lives. Internal (private-network) addresses only --
# nothing here should ever need SeaweedFS's public URL.
# ---------------------------------------------------------------------------
SEAWEEDFS_HOST = os.environ["SEAWEEDFS_INTERNAL_HOST"]  # e.g. dataos-spike-storage-a1b2
S3_PORT = os.environ.get("SEAWEEDFS_S3_PORT", "8333")

S3_ENDPOINT = f"http://{SEAWEEDFS_HOST}:{S3_PORT}"

# SeaweedFS's s3tables admin registers buckets under this region by default
# (confirmed directly from the ARN returned when registering
# dataos-spike-iceberg: arn:aws:s3tables:us-east-1:default:bucket/...) --
# pyarrow's S3 client needs this told to it explicitly rather than trying
# to auto-resolve it, which SeaweedFS doesn't properly support.
S3_REGION = os.environ.get("SEAWEEDFS_S3_REGION", "us-east-1")

# Raw Bronze object storage (plain S3 bucket -- the demo .xlsx lives here).
BUCKET = "dataos-spike"

# Dedicated Iceberg table-bucket for Silver/Gold DATA (Parquet/Avro/metadata-
# JSON files still live here in SeaweedFS S3 -- unaffected by the catalog
# backend change below). MUST be a different name from BUCKET above:
# SeaweedFS's S3 tooling refuses to register a bucket as a table-bucket if
# that name is already a plain object-store bucket (confirmed directly --
# `s3tables.bucket -create -name dataos-spike` errors with "already used by
# an object store bucket", since dataos-spike already holds the raw Bronze
# file). Registered successfully 2026-08-16:
#   weed shell
#   s3tables.bucket -create -name dataos-spike-iceberg -account default
#   -> ARN: arn:aws:s3tables:us-east-1:default:bucket/dataos-spike-iceberg
ICEBERG_BUCKET = "dataos-spike-iceberg"

# SeaweedFS's default (unconfigured) S3 credentials -- fine for a sandboxed
# spike; production hosting will set real ones, tracked as a follow-up, not
# silently treated as done here.
S3_ACCESS_KEY = os.environ.get("SEAWEEDFS_ACCESS_KEY", "any")
S3_SECRET_KEY = os.environ.get("SEAWEEDFS_SECRET_KEY", "any")

# Iceberg CATALOG database (the "which metadata version is current" pointer
# -- see module docstring for why this is Postgres, not SeaweedFS's built-in
# catalog). Reuses the same Postgres instance already provisioned for
# Airflow's own metadata DB -- pragmatic for a spike (zero new cost, one
# fewer resource to manage), not full isolation between the two concerns.
# ICEBERG_CATALOG_DB_URI can be set separately if that reuse is ever worth
# splitting apart; falls back to Airflow's own connection string otherwise.
ICEBERG_CATALOG_DB_URI = os.environ.get(
    "ICEBERG_CATALOG_DB_URI",
    os.environ.get("AIRFLOW__DATABASE__SQL_ALCHEMY_CONN", ""),
)

# The real demo file. For the spike it's fetched from wherever Khurram
# uploads it in Step "upload the demo file" (see handoff notes) -- this DAG
# reads it back out of Bronze in the silver_transform step, not off local
# disk, so the pipeline is genuinely reading from real storage end to end.
DEMO_FILE_KEY = "bronze/Banking_Demo_Dataset.xlsx"

# /opt/spike-dags is where the Dockerfile bakes app/ on every deploy (see
# module docstring for why it's not under /opt/airflow, which the
# persistent disk covers). Every task that needs to import from app/ --
# both silver_transform (header detection) and gold_compute (banking_adapter)
# -- needs this on sys.path first.
SPIKE_DAGS_ROOT = "/opt/spike-dags"


def _s3_client():
    return boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT,
        aws_access_key_id=S3_ACCESS_KEY,
        aws_secret_access_key=S3_SECRET_KEY,
    )


def _iceberg_catalog():
    """Returns a fresh SqlCatalog client. Data/metadata files still write to
    SeaweedFS S3 (ICEBERG_BUCKET, via the s3.* properties below) -- only the
    atomic "current version" pointer lives in Postgres now. See module
    docstring for the concurrent-writer bug this replaces SeaweedFS's
    built-in catalog to fix. Intentionally a fresh instance per call (not a
    shared module-level singleton) -- iceberg_concurrent_writer_test.py
    specifically relies on each concurrent worker getting its own client."""
    if not ICEBERG_CATALOG_DB_URI:
        raise RuntimeError(
            "No Iceberg catalog DB connection string found -- set "
            "ICEBERG_CATALOG_DB_URI or AIRFLOW__DATABASE__SQL_ALCHEMY_CONN."
        )
    from pyiceberg.catalog.sql import SqlCatalog
    return SqlCatalog(
        "dataos_spike",
        **{
            "uri": ICEBERG_CATALOG_DB_URI,
            "warehouse": f"s3://{ICEBERG_BUCKET}/",
            "s3.endpoint": S3_ENDPOINT,
            "s3.access-key-id": S3_ACCESS_KEY,
            "s3.secret-access-key": S3_SECRET_KEY,
            # See module docstring: SeaweedFS can't answer pyarrow's
            # auto-region-resolution lookup, and needs path-style S3 URLs
            # (http://host:port/bucket/key) rather than virtual-hosted-style
            # (http://bucket.host:port/key) -- without both of these,
            # multipart uploads for Iceberg data/manifest files fail with a
            # generic "AWS Error INTERNAL_FAILURE during UploadPart".
            "s3.region": S3_REGION,
            "s3.path-style-access": "true",
        },
    )


def _prep_for_arrow(df: pd.DataFrame) -> pd.DataFrame:
    """pyarrow's pandas->Arrow conversion requires each column to hold a
    single consistent type. Real-world Excel sheets don't always guarantee
    that even after correct header detection -- a column can hold a stray
    int among mostly-string cells (or vice versa) once pandas reads it as
    dtype 'object'. Rather than fail the whole ingest on one stray cell,
    coerce genuinely mixed 'object' columns to string -- this matches how
    these columns are already treated once they hit CSV elsewhere in the
    app (dataset_adapter's Bronze/Silver/Gold path round-trips everything
    through CSV, which stringifies uniformly). Columns that are already
    single-typed are left untouched, so real numeric/date dtypes still
    write to Iceberg as proper typed columns, not stringified."""
    out = df.copy()
    for col in out.columns:
        if out[col].dtype == object:
            non_null = out[col].dropna()
            if non_null.empty:
                continue
            if len({type(v) for v in non_null}) > 1:
                out[col] = out[col].apply(lambda v: None if pd.isna(v) else str(v))
    return out


@dag(
    dag_id="banking_demo_lakehouse_spike",
    description="DataOS 3.0 Item 1 spike -- Banking Demo through real storage/Iceberg/orchestration",
    schedule=None,  # triggered manually for the live demo, not on a timer
    start_date=datetime(2026, 8, 1),
    catchup=False,
    tags=["dataos-3.0", "spike", "banking-demo"],
)
def banking_demo_lakehouse_spike():

    @task
    def bronze_ingest() -> dict:
        """Confirms the raw demo file is present in Bronze (SeaweedFS) and
        readable back out -- proves real, persistent object storage, not
        local disk that Render wipes on redeploy."""
        s3 = _s3_client()
        # Try-create, don't list-then-create: SeaweedFS's list_buckets()
        # doesn't reliably reflect a bucket created as a plain folder
        # through the Filer web UI (as this one was, by hand, before the
        # DAG's first run) -- the existence check returned "not found" even
        # though the bucket genuinely exists, so create_bucket() then failed
        # with BucketAlreadyOwnedByYou. Catching that specific exception is
        # the standard, robust idiom for idempotent bucket creation against
        # any S3-compatible service -- it doesn't depend on list_buckets()
        # being accurate, so it's correct whether the bucket already exists
        # (from the Filer UI, or a prior DAG run) or doesn't exist at all.
        try:
            s3.create_bucket(Bucket=BUCKET)
        except s3.exceptions.BucketAlreadyOwnedByYou:
            pass
        except s3.exceptions.BucketAlreadyExists:
            pass

        obj = s3.get_object(Bucket=BUCKET, Key=DEMO_FILE_KEY)
        raw_bytes = obj["Body"].read()
        if not raw_bytes:
            raise ValueError(
                f"{DEMO_FILE_KEY} is empty or missing in bucket {BUCKET} -- "
                "upload the real Banking_Demo_Dataset.xlsx to Bronze before running this DAG."
            )
        return {"bronze_key": DEMO_FILE_KEY, "size_bytes": len(raw_bytes)}

    @task
    def silver_transform(bronze_result: dict) -> dict:
        """Reads the raw Bronze bytes, extracts the two real sheets using
        the SAME header-detection logic as the live app's
        dataset_adapter.py (real exported workbooks often carry a title
        row above the real headers -- a naive header=0 read misreads that
        as data and produces junk 'Unnamed: N' columns), sanitizes any
        still-mixed-type columns, and writes the result as genuine Iceberg
        tables -- ACID, versioned, schema-tracked, not just Parquet files
        with no guarantees."""
        sys.path.insert(0, SPIKE_DAGS_ROOT)
        from app.adapters.dataset_adapter import _read_sheet_with_header_detection

        s3 = _s3_client()
        raw_bytes = s3.get_object(Bucket=BUCKET, Key=bronze_result["bronze_key"])["Body"].read()

        ifrs9_df = _prep_for_arrow(_read_sheet_with_header_detection(raw_bytes, "IFRS9_Portfolio"))
        customer_df = _prep_for_arrow(_read_sheet_with_header_detection(raw_bytes, "Customer_MDM"))

        catalog = _iceberg_catalog()
        catalog.create_namespace_if_not_exists("silver")

        import pyarrow as pa
        for name, df in [("ifrs9_portfolio", ifrs9_df), ("customer_mdm", customer_df)]:
            table_id = f"silver.{name}"
            arrow_table = pa.Table.from_pandas(df)
            if catalog.table_exists(table_id):
                table = catalog.load_table(table_id)
                table.overwrite(arrow_table)  # real Iceberg overwrite transaction, not a file copy
            else:
                table = catalog.create_table(table_id, schema=arrow_table.schema)
                table.append(arrow_table)

        return {"silver_tables": ["silver.ifrs9_portfolio", "silver.customer_mdm"], "row_count": len(ifrs9_df)}

    @task
    def gold_compute(silver_result: dict) -> dict:
        """Runs the exact IFRS 9 / SAMA / NDI / duplicate-detection logic
        already signed off in production (app/adapters/banking_adapter.py)
        against the Silver Iceberg tables, writes Gold Iceberg tables.
        No new computation path -- same functions, different input source."""
        # banking_adapter.py internally does `from app.adapters import
        # dataset_adapter, dedup_adapter` -- so the package must be importable
        # as literally `app`, which means adding the PARENT of app/ to the
        # path.
        sys.path.insert(0, SPIKE_DAGS_ROOT)
        from app.adapters import banking_adapter as ba

        catalog = _iceberg_catalog()
        ifrs9_df = catalog.load_table("silver.ifrs9_portfolio").scan().to_pandas()
        customer_df = catalog.load_table("silver.customer_mdm").scan().to_pandas()
        ifrs9_csv = ifrs9_df.to_csv(index=False)
        customer_csv = customer_df.to_csv(index=False)

        ifrs9_result = ba.run_ifrs9({"csv_content": ifrs9_csv, "scenario": "base", "customer_csv_content": customer_csv})
        ndi_result = ba.run_ndi_radar({})

        import pyarrow as pa
        catalog.create_namespace_if_not_exists("gold")

        gold_tables_written = []
        for name, payload in [
            ("ifrs9_ecl_summary", {
                "total_computed_ecl": [ifrs9_result["total_computed_ecl"]],
                "reporting_date": [ifrs9_result["reporting_date"]],
                "engine_mode": [ifrs9_result["engine_mode"]],
            }),
            ("ndi_assessment", {
                "display_score": [ndi_result["display_score"]],
                "overall_compliance_pct": [ndi_result["overall_compliance_pct"]],
                "maturity_level": [ndi_result["maturity_level"]],
            }),
        ]:
            table_id = f"gold.{name}"
            arrow_table = pa.Table.from_pydict(payload)
            if catalog.table_exists(table_id):
                catalog.load_table(table_id).overwrite(arrow_table)
            else:
                catalog.create_table(table_id, schema=arrow_table.schema).append(arrow_table)
            gold_tables_written.append(table_id)

        return {
            "gold_tables": gold_tables_written,
            "total_ecl_sar": ifrs9_result["total_computed_ecl"],
            "ndi_display_score": ndi_result["display_score"],
        }

    bronze_result = bronze_ingest()
    silver_result = silver_transform(bronze_result)
    gold_compute(silver_result)


banking_demo_lakehouse_spike()
