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
Bronze data living in "dataos-spike".

Factored out into its own module (not added to lakehouse_client.py)
because that module is a read-only dashboard-data layer, a different
concern than this app's own write-path dataset storage -- keeping them
separate means app/adapters/dataset_adapter.py doesn't need to import
a dashboard-specific module to do its own file I/O.

CI / LOCAL DEV FALLBACK: put_text()/get_text() below fall back to plain
local disk under a caller-supplied local_root when SeaweedFS isn't
configured (SEAWEEDFS_PUBLIC_URL / SEAWEEDFS_INTERNAL_HOST both unset,
always true in GitHub Actions, same as LAKEHOUSE_DB_URI) -- production
always uses S3 (via the relay for writes, see below), CI/local dev
transparently keeps working against local disk with the exact same
path shape the old direct-local-disk implementation used, so no test
file needed to change.

get_text() raises the STANDARD LIBRARY FileNotFoundError (not a
boto3/botocore-specific exception) on a missing object on EITHER
backend -- so callers that used to catch FileNotFoundError against
local disk (see dataset_adapter.py's read_silver_csv()) keep working
unchanged regardless of which backend is actually storing the bytes.

WRITE RELAY (2026-08-17) -- ROOT CAUSE FOUND, CONFIRMED WITH A REAL
COMPARATIVE TEST, NOT A GUESS: boto3 PutObject against SeaweedFS's
public URL, path-style addressing, identical code/credentials --
succeeds when run from dataos-spike-orchestrator (Singapore, same
region as the storage service) and gets a genuine 403 when run from
this app (Oregon). Proven with spike/dags/test_seaweed_write.py, which
ran SIX combinations (boto3 vs pyarrow x internal vs public endpoint x
addressing style) directly on the orchestrator's Shell tab. Ruled out
along the way, with real evidence for each: boto3-vs-pyarrow as the
cause (pyarrow has its OWN separate, pre-existing 0-byte-read quirk,
unrelated); addressing style (path-style alone didn't fix it, tested
live); payload size (a 2-byte test body got the identical 403); a
manually-configured WAF (Khurram confirmed no custom Cloudflare zone on
dataos-spike-storage); the request never reaching the container at all
(confirmed via that service's own Render logs showing nothing at the
failure timestamp). What's left, and the only thing consistent with
every result: Render's platform edge treats cross-region write traffic
to this specific service differently from same-region write traffic --
regardless of client library or request shape.

Fix: SEAWEEDFS_WRITE_RELAY_URL, when set, routes every put_text() call
through relay/main.py -- a small, separate, single-purpose FastAPI
service deployed in Singapore (same region as SeaweedFS) whose only job
is receiving a write over the public internet (with its own shared-
secret auth) and performing it over INTERNAL networking, the one path
already proven to work. Deliberately NOT an Airflow plugin bolted onto
dataos-spike-orchestrator -- that service runs the already-signed-off
Item 1 pipeline (committed date 2026-08-18); this relay is a fully
separate, isolated service specifically so nothing about fixing dataset
file storage can put that pipeline at risk. Reads are NOT relayed --
get_text() below is completely unchanged, since GET/list already work
fine cross-region (confirmed in the same test, and by Item 2's
Lakehouse dashboard reading this way in production well before today).
If SEAWEEDFS_WRITE_RELAY_URL isn't set, put_text() falls back to the
direct-boto3 path below it -- which is now understood to be expected to
fail from Oregon specifically, not a bug, just documented rather than
silently masked.
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

# See WRITE RELAY in the module docstring. Both unset until Khurram
# deploys relay/ as its own Render service and wires these up -- until
# then put_text() falls back to the direct path (expected to fail from
# Oregon, per the same docstring section).
SEAWEEDFS_WRITE_RELAY_URL = os.environ.get("SEAWEEDFS_WRITE_RELAY_URL", "")
SEAWEEDFS_WRITE_RELAY_SECRET = os.environ.get("SEAWEEDFS_WRITE_RELAY_SECRET", "")

APP_BUCKET = os.environ.get("DATAOS_APP_BUCKET", "dataos-app-datasets")

_bucket_ready = False


def _endpoint() -> str:
    """Same public-vs-internal fallback as lakehouse_client.py's
    _s3_endpoint() -- this app runs cross-region from the storage
    service, so the public HTTPS URL is what actually works for reads
    (writes now go through the relay instead -- see module docstring)."""
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


def _put_via_relay(key: str, content: str) -> str:
    """Routes the write through relay/main.py (a separate Render
    service in Singapore) instead of writing to SeaweedFS directly --
    see WRITE RELAY in the module docstring for the full reasoning.
    httpx is already a dependency of this app (requirements.txt), no
    new package needed."""
    import httpx

    try:
        resp = httpx.post(
            f"{SEAWEEDFS_WRITE_RELAY_URL.rstrip('/')}/write",
            json={"bucket": APP_BUCKET, "key": key, "content": content},
            headers={"x-relay-secret": SEAWEEDFS_WRITE_RELAY_SECRET},
            timeout=30.0,
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise ValueError(
            f"Write relay rejected {key!r}: {e.response.status_code} {e.response.text[:300]}"
        ) from e
    except httpx.HTTPError as e:
        raise ValueError(f"Write relay request failed for {key!r}: {e}") from e
    return resp.json()["uri"]


def put_text(key: str, content: str, local_root: str | None = None) -> str:
    """Writes UTF-8 text as an object under APP_BUCKET, returns its
    s3://bucket/key URI -- stored verbatim in datasets.bronze_path/
    silver_path/gold_path (already plain TEXT columns, no schema
    change needed to hold a URI instead of a local path).

    When SEAWEEDFS_WRITE_RELAY_URL is set, the write goes through
    relay/main.py instead of straight to SeaweedFS -- see WRITE RELAY
    in the module docstring for why. Otherwise falls back to a direct
    boto3 PutObject (expected to fail specifically from Oregon, per the
    same docstring section -- not silently masked, still raises a clear
    ValueError either way).

    Falls back to plain local disk under local_root/key when SeaweedFS
    isn't configured at all (CI/local dev); returns the local path in
    that case instead of an s3:// URI."""
    if is_configured():
        if SEAWEEDFS_WRITE_RELAY_URL:
            return _put_via_relay(key, content)
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
    an explicit local_root call). UNCHANGED by the write-relay fix --
    reads already work fine cross-region, confirmed both by
    spike/dags/test_seaweed_write.py and by Item 2's Lakehouse
    dashboard, which has read this way from Oregon in production well
    before today. Raises the plain stdlib FileNotFoundError on a
    missing object/file either way -- see this module's docstring for
    why that specific exception type matters to callers. No local_root
    needed here: the path/URI returned by put_text() is always already
    complete.

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


def _full_error(e: Exception) -> dict:
    """Every field botocore actually has for a failure, not just the
    truncated str(e) message."""
    if isinstance(e, ClientError):
        resp = e.response
        meta = resp.get("ResponseMetadata", {})
        return {
            "type": "ClientError",
            "code": resp.get("Error", {}).get("Code"),
            "message": resp.get("Error", {}).get("Message"),
            "request_id": meta.get("RequestId"),
            "host_id": meta.get("HostId"),
            "http_status": meta.get("HTTPStatusCode"),
            "http_headers": dict(meta.get("HTTPHeaders", {})),
            "raw": str(e),
        }
    return {"type": type(e).__name__, "raw": str(e)}


def debug_write_test() -> dict:
    """Diagnostic (2026-08-17): attempts list_buckets / head_bucket /
    create_bucket / put_object DIRECTLY (bypassing the relay, even if
    configured) against APP_BUCKET in sequence, capturing full error
    detail for whichever step fails. This is what originally proved the
    write was a region-specific 403, not a code bug -- kept as-is
    (still bypassing the relay on purpose) so it can be re-run any time
    to confirm the direct path's status, independent of whether the
    relay happens to be working. Exposed at /api/debug/storage-write.
    Deliberately does not delete the test object it writes (if the
    write succeeds) -- leaves "_debug_write_test.txt" in the bucket as
    visible proof a write actually landed, harmless to leave there."""
    result: dict = {"bucket": APP_BUCKET, "endpoint": _endpoint(), "configured": is_configured()}
    if not is_configured():
        return result

    s3 = _client()

    try:
        resp = s3.list_buckets()
        result["list_buckets"] = [b["Name"] for b in resp.get("Buckets", [])]
    except (ClientError, BotoCoreError) as e:
        result["list_buckets_error"] = _full_error(e)

    try:
        s3.head_bucket(Bucket=APP_BUCKET)
        result["head_bucket"] = "exists"
    except ClientError as e:
        result["head_bucket_error"] = _full_error(e)
        try:
            s3.create_bucket(Bucket=APP_BUCKET)
            result["create_bucket"] = "created"
        except (ClientError, BotoCoreError) as ce:
            result["create_bucket_error"] = _full_error(ce)
    except BotoCoreError as e:
        result["head_bucket_error"] = _full_error(e)

    try:
        s3.put_object(Bucket=APP_BUCKET, Key="_debug_write_test.txt", Body=b"ok")
        result["put_object"] = "ok"
    except (ClientError, BotoCoreError) as e:
        result["put_object_error"] = _full_error(e)

    return result
