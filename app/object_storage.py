"""
Shared S3-compatible object storage helper -- reuses the SAME SeaweedFS
instance already provisioned for Item 1/2 (dataos-spike-storage), same
public endpoint and credentials app/lakehouse_client.py already uses to
read Iceberg/Airflow-log data. No new Render resource, same reasoning
as db.py reusing the existing Postgres instance: this app already talks
to this exact storage service, so bolt onto it rather than add a new
one.

Uses its OWN bucket (APP_BUCKET, default "dataos-app-datasets"), never
"dataos-spike" -- the same separation-of-concerns choice db.py made
with its own Postgres schema ("dataos_app"). This app's own Bronze/
Silver/Gold dataset files must never be able to collide with or
accidentally touch the real spike pipeline's Airflow-orchestrated
Bronze data living in "dataos-spike". Auto-creates the bucket on first
use if it doesn't exist yet (SeaweedFS auth is disabled on this
instance -- confirmed during Item 1 -- so a plain CreateBucket call is
all that's needed, no Iceberg-table-bucket registration like
"dataos-spike-iceberg" required, since this is plain object storage,
not an Iceberg warehouse).

Factored out into its own module (not added to lakehouse_client.py)
because that module is a read-only dashboard-data layer, a different
concern than this app's own write-path dataset storage -- keeping them
separate means app/adapters/dataset_adapter.py doesn't need to import
a dashboard-specific module to do its own file I/O.

get_text() deliberately raises the STANDARD LIBRARY FileNotFoundError
(not a boto3/botocore-specific exception) on a missing object -- so
callers that used to catch FileNotFoundError against local disk (see
dataset_adapter.py's read_silver_csv(), fixed 2026-08-17 for exactly
this exception type) keep working unchanged regardless of which
backend is actually storing the bytes.
"""
import os

import boto3
from botocore.exceptions import ClientError

SEAWEEDFS_PUBLIC_URL = os.environ.get("SEAWEEDFS_PUBLIC_URL", "")
SEAWEEDFS_INTERNAL_HOST = os.environ.get("SEAWEEDFS_INTERNAL_HOST", "")
SEAWEEDFS_S3_PORT = os.environ.get("SEAWEEDFS_S3_PORT", "8333")
SEAWEEDFS_ACCESS_KEY = os.environ.get("SEAWEEDFS_ACCESS_KEY", "any")
SEAWEEDFS_SECRET_KEY = os.environ.get("SEAWEEDFS_SECRET_KEY", "any")

APP_BUCKET = os.environ.get("DATAOS_APP_BUCKET", "dataos-app-datasets")

_bucket_ready = False


def _endpoint() -> str:
    """Same public-vs-internal fallback as lakehouse_client.py's
    _s3_endpoint() -- this app runs cross-region from the storage
    service, so the public HTTPS URL is what actually works."""
    if SEAWEEDFS_PUBLIC_URL:
        return SEAWEEDFS_PUBLIC_URL.rstrip("/")
    return f"http://{SEAWEEDFS_INTERNAL_HOST}:{SEAWEEDFS_S3_PORT}"


def is_configured() -> bool:
    return bool(SEAWEEDFS_PUBLIC_URL or SEAWEEDFS_INTERNAL_HOST)


def _client():
    return boto3.client(
        "s3",
        endpoint_url=_endpoint(),
        aws_access_key_id=SEAWEEDFS_ACCESS_KEY,
        aws_secret_access_key=SEAWEEDFS_SECRET_KEY,
    )


def _ensure_bucket(s3) -> None:
    global _bucket_ready
    if _bucket_ready:
        return
    try:
        s3.head_bucket(Bucket=APP_BUCKET)
    except ClientError:
        s3.create_bucket(Bucket=APP_BUCKET)
    _bucket_ready = True


def put_text(key: str, content: str) -> str:
    """Writes UTF-8 text as an object under APP_BUCKET, returns its
    s3://bucket/key URI -- stored verbatim in datasets.bronze_path/
    silver_path/gold_path (already plain TEXT columns, no schema
    change needed to hold a URI instead of a local path)."""
    if not is_configured():
        raise ValueError(
            "Object storage isn't configured (SEAWEEDFS_PUBLIC_URL / "
            "SEAWEEDFS_INTERNAL_HOST not set) -- can't write dataset files."
        )
    s3 = _client()
    _ensure_bucket(s3)
    s3.put_object(Bucket=APP_BUCKET, Key=key, Body=content.encode("utf-8"))
    return f"s3://{APP_BUCKET}/{key}"


def get_text(uri: str) -> str:
    """Reads back a s3://bucket/key URI written by put_text(). Raises
    the plain stdlib FileNotFoundError on a missing object -- see this
    module's docstring for why that specific exception type matters."""
    if not uri.startswith("s3://"):
        raise ValueError(f"Not an s3:// URI: {uri!r}")
    s3 = _client()
    bucket, _, key = uri.removeprefix("s3://").partition("/")
    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("NoSuchKey", "404", "NoSuchBucket"):
            raise FileNotFoundError(f"No object at {uri}") from e
        raise
    return obj["Body"].read().decode("utf-8")
