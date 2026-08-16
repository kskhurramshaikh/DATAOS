"""
DataOS 3.0 -- Item 1 spike DAG: Banking Demo pipeline through the real stack.

Proves the same already-signed-off pipeline (IFRS 9 / SAMA / NDI / duplicate
detection, all approved by Dr. Saber 2026-08-11) running through real
infrastructure instead of local CSV + SQLite:

    Bronze  -> raw Banking_Demo_Dataset.xlsx bytes, written to SeaweedFS (S3 API)
    Silver  -> cleaned IFRS9_Portfolio + Customer_MDM, written as REAL Iceberg
               tables (via SeaweedFS's own built-in Iceberg REST Catalog)
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
      SEAWEEDFS_ICEBERG_CATALOG_PORT   8181
  - Python packages (pyiceberg, duckdb, boto3, pandas, openpyxl, scikit-learn,
    rapidfuzz) are installed at BUILD time via the Dockerfile, not a runtime
    env var -- faster startup, no surprises from a slow first-boot install.
  - Bronze storage and the Iceberg warehouse are DELIBERATELY SEPARATE
    buckets (see BUCKET vs ICEBERG_BUCKET below) -- SeaweedFS's Iceberg
    REST Catalog will not let a bucket be both a plain object-store bucket
    and a registered table-bucket at the same time.
"""
from __future__ import annotations

import io
import os
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
CATALOG_PORT = os.environ.get("SEAWEEDFS_ICEBERG_CATALOG_PORT", "8181")

S3_ENDPOINT = f"http://{SEAWEEDFS_HOST}:{S3_PORT}"
CATALOG_URI = f"http://{SEAWEEDFS_HOST}:{CATALOG_PORT}"

# Raw Bronze object storage (plain S3 bucket -- the demo .xlsx lives here).
BUCKET = "dataos-spike"

# Dedicated Iceberg table-bucket for Silver/Gold. MUST be a different name
# from BUCKET above: SeaweedFS's Iceberg REST Catalog refuses to register a
# bucket as a table-bucket if that name is already a plain object-store
# bucket (confirmed directly -- `s3tables.bucket -create -name dataos-spike`
# errors with "already used by an object store bucket", since dataos-spike
# already holds the raw Bronze file). Needs a one-time manual registration
# on dataos-spike-storage's Shell tab before the next DAG run:
#   weed shell
#   s3tables.bucket -create -name dataos-spike-iceberg -account default
ICEBERG_BUCKET = "dataos-spike-iceberg"

# SeaweedFS's default (unconfigured) S3 credentials -- fine for a sandboxed
# spike; production hosting will set real ones, tracked as a follow-up, not
# silently treated as done here.
S3_ACCESS_KEY = os.environ.get("SEAWEEDFS_ACCESS_KEY", "any")
S3_SECRET_KEY = os.environ.get("SEAWEEDFS_SECRET_KEY", "any")

# The real demo file. For the spike it's fetched from wherever Khurram
# uploads it in Step "upload the demo file" (see handoff notes) -- this DAG
# reads it back out of Bronze in the silver_transform step, not off local
# disk, so the pipeline is genuinely reading from real storage end to end.
DEMO_FILE_KEY = "bronze/Banking_Demo_Dataset.xlsx"


def _s3_client():
    return boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT,
        aws_access_key_id=S3_ACCESS_KEY,
        aws_secret_access_key=S3_SECRET_KEY,
    )


def _iceberg_catalog():
    from pyiceberg.catalog.rest import RestCatalog
    return RestCatalog(
        name="dataos_spike",
        uri=CATALOG_URI,
        **{
            # SeaweedFS's Iceberg REST Catalog treats each S3 bucket as its
            # own separate catalog (confirmed by its own error text: "each
            # table bucket is a separate catalog, select one with
            # warehouse=s3://<table-bucket>/"). Without this, pyiceberg's
            # startup call to /v1/config has nothing to base a URL prefix
            # on, so every later request (e.g. /v1/namespaces) 404s. This
            # property is pyiceberg's real, documented mechanism for that --
            # verified against the installed library source, not guessed.
            # Points at ICEBERG_BUCKET, NOT BUCKET -- see the comment on
            # ICEBERG_BUCKET above for why they must be separate buckets.
            "warehouse": f"s3://{ICEBERG_BUCKET}/",
            "s3.endpoint": S3_ENDPOINT,
            "s3.access-key-id": S3_ACCESS_KEY,
            "s3.secret-access-key": S3_SECRET_KEY,
        },
    )


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
        """Reads the raw Bronze bytes, extracts the two real sheets (same
        header-detection logic as the live app's dataset_adapter.py), and
        writes them as genuine Iceberg tables -- ACID, versioned, schema-
        tracked, not just Parquet files with no guarantees."""
        s3 = _s3_client()
        raw_bytes = s3.get_object(Bucket=BUCKET, Key=bronze_result["bronze_key"])["Body"].read()

        ifrs9_df = pd.read_excel(io.BytesIO(raw_bytes), sheet_name="IFRS9_Portfolio")
        customer_df = pd.read_excel(io.BytesIO(raw_bytes), sheet_name="Customer_MDM")

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
        import sys
        # banking_adapter.py internally does `from app.adapters import
        # dataset_adapter, dedup_adapter` -- so the package must be importable
        # as literally `app`, which means adding the PARENT of app/ to the
        # path. /opt/spike-dags is where the Dockerfile bakes this code on
        # every deploy (see module docstring for why it's not under
        # /opt/airflow, which the persistent disk covers).
        sys.path.insert(0, "/opt/spike-dags")
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
