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

CI / LOCAL DEV FALLBACK (added after almost shipping a CI break --
tests/test_dataset_adapter.py directly monkeypatches
dataset_adapter.DATA_ROOT and asserts real local files exist, same
load-bearing-test-fixture pattern db.py's DB_PATH monkeypatching
already established for the Postgres migration; SEAWEEDFS_PUBLIC_URL/
SEAWEEDFS_INTERNAL_HOST are also never set in GitHub Actions, same as
LAKEHOUSE_DB_URI). put_text()/get_text() below fall back to plain local
disk under a caller-supplied local_root when SeaweedFS isn't
configured -- production (where it IS configured) always uses S3;
CI/local dev transparently keeps working against local disk with the
exact same path shape the old direct-local-disk implementation used,
so no test file needed to change.

get_text() raises the STANDARD LIBRARY FileNotFoundError (not a
boto3/botocore-specific exception) on a missing object on EITHER
backend -- so callers that used to catch FileNotFoundError against
local disk (see dataset_adapter.py's read_silver_csv()) keep working
unchanged regardless of which backend is actually storing the bytes.

ERROR-SURFACING GAP (found live, same day, same class of mistake
already fixed once in db.py): a real S3/network failure inside
put_text() -- head_bucket, create_bucket, or put_object all talk to a
real service over the network -- raises a raw botocore exception
(ClientError, EndpointConnectionError, etc.), NOT a ValueError. Every
caller up the chain (dataset_adapter.py's land_bronze/clean_to_silver,
main.py's upload endpoints) only catches ValueError, exactly the same
gap that turned real Postgres errors into opaque 500s earlier today
before db.py's _PgCursor.execute() was fixed to re-raise as ValueError.
Confirmed directly: POST /api/mdm/upload-dataset -> another blank 500
on the very first real SeaweedFS write attempt. put_text()/get_text()
below now catch any storage-layer exception and re-raise as ValueError
with the real error text, matching db.py's fix exactly.

PATH-STYLE ADDRESSING (found live, same day): once the error-surfacing
fix above made the real error visible, PutObject came back with a
genuine "403 Forbidden" -- not a code bug, a real SeaweedFS-compat
issue. This codebase had never actually proven plain boto3 PutObject
against this instance: app/lakehouse_client.py's own boto3 S3 client
(the exact pattern _client() below was copied from) is READ-ONLY
(list_objects_v2, get_object) -- it never writes. The only WRITE path
ever proven working here is pyiceberg's Boto3FileIO (app/boto3_file_io.py),
which plugs into a catalog explicitly configured with
"s3.path-style-access": "true" (see app/lakehouse_client.py's
_iceberg_catalog()). Without that, boto3 defaults to virtual-hosted-
style bucket addressing (bucket.endpoint/key), which changes the Host
header used in SigV4 request signing -- a well-known failure mode
against self-hosted S3-compatible stores (SeaweedFS/MinIO/etc.) that
don't handle that addressing style the same way AWS does, and
manifests exactly as a signature-rejection 403. _client() below now
sets addressing_style="path" explicitly, matching the one write path
in this codebase already confirmed to work.
"""
import os

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

SEAWEEDFS_PUBLIC_URL = os.environ.get("SEAWEEDFS_PUBLIC_URL", "")
SEAWEEDFS_INTERNAL_HOST = os.environ.get("SEAWEEDFS_INTERNAL_HOST", "")
SEAWEEDFS_S3_PORT = os.environ.get("SEAWEEDFS_S3_PORT", "8333")
SEAWEEDFS_ACCESS_KEY = os.environ.get("SEAWEEDFS_ACCESS_KEY", "any")
SEAWEEDFS_SECRET_KEY = os.environ.get("SEAWEEDFS_SECRET_KEY", "any")
SEAWEEDFS_S3_REGION = os.environ.get("SEAWEEDFS_S3_REGION", "us-east-1")

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
        region_name=SEAWEEDFS_S3_REGION,
        # See PATH-STYLE ADDRESSING in the module docstring -- required
        # for writes against this SeaweedFS instance; matches the one
        # write path already proven working elsewhere in this codebase.
        config=Config(s3={"addressing_style": "path"}),
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


def put_text(key: str, content: str, local_root: str | None = None) -> str:
    """Writes UTF-8 text as an object under APP_BUCKET, returns its
    s3://bucket/key URI -- stored verbatim in datasets.bronze_path/
    silver_path/gold_path (already plain TEXT columns, no schema
    change needed to hold a URI instead of a local path).

    Falls back to plain local disk under local_root/key when SeaweedFS
    isn't configured (CI/local dev -- see module docstring); returns
    the local path in that case instead of an s3:// URI.

    Any real S3/network failure (see ERROR-SURFACING GAP above) is
    caught and re-raised as ValueError with the actual error text --
    every caller already handles ValueError and surfaces it as a 400,
    same pattern as db.py's own Postgres error handling."""
    if is_configured():
        try:
            s3 = _client()
            _ensure_bucket(s3)
            s3.put_object(Bucket=APP_BUCKET, Key=key, Body=content.encode("utf-8"))
        except (ClientError, BotoCoreError) as e:
            raise ValueError(f"Object storage write failed for {key!r}: {e}") from e
        return f"s3://{APP_BUCKET}/{key}"

    if local_root is None:
        raise ValueError(
            "Object storage isn't configured (SEAWEEDFS_PUBLIC_URL / "
            "SEAWEEDFS_INTERNAL_HOST not set) and no local_root fallback "
            "was given -- can't write dataset files."
        )
    path = os.path.join(local_root, key)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def get_text(uri_or_path: str) -> str:
    """Reads back whatever put_text() returned -- an s3://bucket/key
    URI (production) or a plain local path (CI/local dev fallback, or
    an explicit local_root call). Raises the plain stdlib
    FileNotFoundError on a missing object/file either way -- see this
    module's docstring for why that specific exception type matters to
    callers. No local_root needed here: the path/URI returned by
    put_text() is always already complete.

    Any OTHER real S3/network failure (not a missing-object case) is
    caught and re-raised as ValueError, same reasoning as put_text()
    above."""
    if uri_or_path.startswith("s3://"):
        s3 = _client()
        bucket, _, key = uri_or_path.removeprefix("s3://").partition("/")
        try:
            obj = s3.get_object(Bucket=bucket, Key=key)
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            if code in ("NoSuchKey", "404", "NoSuchBucket"):
                raise FileNotFoundError(f"No object at {uri_or_path}") from e
            raise ValueError(f"Object storage read failed for {uri_or_path}: {e}") from e
        except BotoCoreError as e:
            raise ValueError(f"Object storage read failed for {uri_or_path}: {e}") from e
        return obj["Body"].read().decode("utf-8")

    with open(uri_or_path, "r", encoding="utf-8") as f:
        return f.read()
