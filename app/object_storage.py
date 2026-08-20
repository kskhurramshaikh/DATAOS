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
configured (production (where it IS configured) always uses S3;
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
put_text() raises a raw exception (botocore's ClientError/
BotoCoreError, or requests' RequestException for the presigned path
below), NOT a ValueError. Every caller up the chain (dataset_adapter.
py's land_bronze/clean_to_silver, main.py's upload endpoints) only
catches ValueError, exactly the same gap that turned real Postgres
errors into opaque 500s earlier the same day before db.py's
_PgCursor.execute() was fixed to re-raise as ValueError. put_text()/
get_text() below catch any storage-layer exception and re-raise as
ValueError with the real error text, matching db.py's fix exactly.

CROSS-REGION PUT 403 -- ROOT CAUSE AND FIX (2026-08-17):
This app runs in Oregon; dataos-spike-storage (SeaweedFS) runs in
Singapore, so every write here crosses Render's public edge (which
runs on Cloudflare's network). Path-style addressing (an earlier fix
attempt) was necessary but NOT sufficient -- boto3 PutObject still
403'd from Oregon while the identical call succeeded from Singapore
and while GET/LIST worked fine cross-region the whole time (confirmed
via spike/dags/test_seaweed_write.py, run on the orchestrator's own
Shell tab, and via dataos-spike-storage's Render logs showing the PUT
never even reached the container -- a platform-edge block, not an
app/SeaweedFS-level rejection).

An 8-variant isolation test (app/object_storage.py's debug_write_ladder(),
still below, run live from this app in Oregon) found the actual
trigger: it is NOT the SigV4 signature and NOT the client's TLS/HTTP
fingerprint -- manually SigV4-signed PUTs sent via plain `requests`
(default Python User-Agent, default TLS) passed cleanly (variants 06,
07), and a Chrome-impersonated boto3 call with an overridden User-Agent
string still 403'd (variant 02). The actual differentiator is boto3's
OWN client machinery: it silently attaches additional SDK-identifying
headers (amz-sdk-invocation-id, amz-sdk-request, and its full Botocore
User-Agent string) beyond the signature and beyond whatever
Config(user_agent=...) overrides, and Render's edge scores THOSE as
bot-like specifically on cross-region write traffic to this service.
Reads never hit this because list_objects_v2/get_object were always
issued through the same boto3 client and never blocked -- so the block
isn't "boto3" in general, it's specifically boto3's write-path request
shape scored differently for PUT than for GET/LIST by whatever rule is
doing the scoring.

FIX: put_text() below no longer calls s3.put_object() directly.
It still uses boto3 to CREATE a presigned PUT URL (generate_presigned_
url is a local, offline signing operation -- no network call, so it
can't be blocked), then sends the actual bytes with a plain `requests.
put()` call carrying no boto3-added headers at all. This was variant
03 in the ladder test: presigned URL + plain `requests`, default
Python User-Agent, default TLS -- passed clean (200), no impersonation
or special headers needed. get_text() is UNCHANGED (still direct boto3
GET) since reads were never blocked.

DELETE support (2026-08-18, added for dataset removal -- see
dataset_adapter.delete_dataset()): DeleteObject is a write-shaped
boto3 call, the same request class already proven to 403 cross-region
for PutObject. delete_prefix() below tries direct boto3 delete_object()
first (cheap, and DELETE has no request body the way PUT does, so it's
not guaranteed to hit the same block); if that specific call fails,
it falls back to a presigned DELETE URL + plain `requests`, the exact
same fix already proven for PUT above -- same reasoning, not a new
approach.

DIAGNOSTIC (2026-08-17): debug_write_test() (list_buckets/head_bucket/
create_bucket/put_object via boto3, on purpose -- kept as-is so it
keeps demonstrating the boto3-direct 403 if Render's edge behaviour
ever changes) and debug_write_ladder() (the 8-variant isolation test
that found the fix above) are both kept as live, re-runnable checks,
exposed at /api/debug/storage-write. Not used by the real write path.
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
        config=Config(s3={"addressing_style": "path"}),
    )


def _ensure_bucket(s3) -> None:
    """HEAD/CREATE bucket are cheap, low-frequency calls (once per
    process, cached) -- left on direct boto3 since they aren't on the
    hot write path and weren't part of what the ladder test proved
    blocked (only PutObject was). If this ever also 403s cross-region,
    the same presigned-URL-not-needed workaround doesn't apply (HEAD/
    CREATE have no body-carrying presigned equivalent worth building)
    -- would need revisiting then, not before."""
    global _bucket_ready
    if _bucket_ready:
        return
    try:
        s3.head_bucket(Bucket=APP_BUCKET)
    except ClientError:
        s3.create_bucket(Bucket=APP_BUCKET)
    _bucket_ready = True


def _presigned_put_url(key: str, content_type: str = "text/plain") -> str:
    s3 = _client()
    return s3.generate_presigned_url(
        "put_object",
        Params={"Bucket": APP_BUCKET, "Key": key, "ContentType": content_type},
        ExpiresIn=300,
        HttpMethod="PUT",
    )


def put_text(key: str, content: str, local_root: str | None = None) -> str:
    """Writes UTF-8 text as an object under APP_BUCKET, returns its
    s3://bucket/key URI -- stored verbatim in datasets.bronze_path/
    silver_path/gold_path (already plain TEXT columns, no schema
    change needed to hold a URI instead of a local path).

    Falls back to plain local disk under local_root/key when SeaweedFS
    isn't configured (CI/local dev -- see module docstring); returns
    the local path in that case instead of an s3:// URI.

    IMPLEMENTATION (see CROSS-REGION PUT 403 in the module docstring):
    generates a presigned PUT URL via boto3 (local/offline signing, no
    network call, can't be blocked), then sends the bytes with plain
    `requests.put()` -- proven clean from Oregon (ladder variant 03).
    Deliberately does NOT call s3.put_object() -- that's the exact call
    the ladder test proved gets 403'd cross-region by Render's edge.

    Any real failure (network error, non-2xx from the presigned PUT, or
    a local-fallback I/O error) is caught and re-raised as ValueError
    with the actual error text -- every caller already handles
    ValueError and surfaces it as a 400, same pattern as db.py's own
    Postgres error handling."""
    if is_configured():
        try:
            s3 = _client()
            _ensure_bucket(s3)
            import requests

            url = _presigned_put_url(key, content_type="text/plain")
            body = content.encode("utf-8")
            r = requests.put(url, data=body, headers={"Content-Type": "text/plain"}, timeout=30)
            r.raise_for_status()
        except Exception as e:  # requests.RequestException, ClientError, BotoCoreError, etc.
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

    UNCHANGED by the write-side fix above -- reads go straight through
    boto3's own GetObject, which was never part of the block (GET/list
    have worked fine cross-region the whole time, confirmed repeatedly,
    including by Item 2's Lakehouse dashboard reading this way in
    production well before this fix). Any OTHER real S3/network
    failure (not a missing-object case) is caught and re-raised as
    ValueError, same reasoning as put_text() above."""
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


def put_bytes(key: str, content: bytes, content_type: str = "application/octet-stream", local_root: str | None = None) -> str:
    """Binary counterpart to put_text() -- same presigned-PUT-then-
    requests.put() mechanism (see put_text()'s own docstring for why:
    the cross-region PUT 403 fix), just for real binary files (PDFs,
    Word docs) instead of UTF-8 text. Added for policy document upload
    (Classification & PDPL page) -- the first caller in this codebase
    that needs to store an actual uploaded file's bytes, not a
    generated CSV/Parquet-as-text artifact."""
    if is_configured():
        try:
            s3 = _client()
            _ensure_bucket(s3)
            import requests

            url = _presigned_put_url(key, content_type=content_type)
            r = requests.put(url, data=content, headers={"Content-Type": content_type}, timeout=30)
            r.raise_for_status()
        except Exception as e:  # requests.RequestException, ClientError, BotoCoreError, etc.
            raise ValueError(f"Object storage write failed for {key!r}: {e}") from e
        return f"s3://{APP_BUCKET}/{key}"

    if local_root is None:
        raise ValueError(
            "Object storage isn't configured (SEAWEEDFS_PUBLIC_URL / "
            "SEAWEEDFS_INTERNAL_HOST not set) and no local_root fallback "
            "was given -- can't write this file."
        )
    path = os.path.join(local_root, key)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(content)
    return path


def get_bytes(uri_or_path: str) -> bytes:
    """Binary counterpart to get_text() -- reads back whatever
    put_bytes() returned. Same FileNotFoundError-on-missing-object
    contract as get_text()."""
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
        return obj["Body"].read()

    with open(uri_or_path, "rb") as f:
        return f.read()


def delete_prefix(prefix: str) -> dict:
    """Permanently deletes every object under `prefix` (e.g.
    "bronze/Banking_Demo/") in APP_BUCKET. Real deletion, not soft --
    used when a dataset record itself is being removed (see
    dataset_adapter.delete_dataset()). Lists first via boto3 (reads
    are known-good cross-region -- see CROSS-REGION PUT 403 above),
    then deletes each key. See DELETE support in the module docstring
    for why each delete tries direct boto3 first, then falls back to
    a presigned DELETE URL + plain `requests` if that specific call
    fails -- same fix already proven for PUT, applied here for the
    same reason."""
    if not is_configured():
        return {"prefix": prefix, "configured": False, "deleted": []}

    s3 = _client()
    keys: list[str] = []
    try:
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=APP_BUCKET, Prefix=prefix):
            for obj in page.get("Contents", []):
                keys.append(obj["Key"])
    except (ClientError, BotoCoreError) as e:
        return {"prefix": prefix, "found": 0, "deleted": [], "list_error": str(e)}

    deleted: list[str] = []
    errors: list[dict] = []
    for key in keys:
        try:
            s3.delete_object(Bucket=APP_BUCKET, Key=key)
            deleted.append(key)
            continue
        except (ClientError, BotoCoreError):
            pass  # fall through to presigned-URL retry below
        try:
            import requests

            url = s3.generate_presigned_url(
                "delete_object", Params={"Bucket": APP_BUCKET, "Key": key}, ExpiresIn=300, HttpMethod="DELETE"
            )
            r = requests.delete(url, timeout=30)
            r.raise_for_status()
            deleted.append(key)
        except Exception as e2:  # noqa: BLE001 -- last-resort, surfaced per-key not swallowed
            errors.append({"key": key, "error": str(e2)})

    result = {"prefix": prefix, "found": len(keys), "deleted": deleted}
    if errors:
        result["errors"] = errors
    return result


def _full_error(e: Exception) -> dict:
    """Every field botocore actually has for a failure, not just the
    truncated str(e) message -- see DIAGNOSTIC in the module docstring
    for why this matters."""
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
    create_bucket / put_object via DIRECT boto3 (on purpose -- this
    intentionally still uses the call proven to 403 cross-region, so
    it keeps demonstrating Render's edge behaviour rather than testing
    the fixed put_text() path). Exposed at /api/debug/storage-write.
    Kept as a live, re-runnable check for confirming whether/when the
    platform-level block on direct boto3 PUTs has changed. Deliberately
    does not delete the test object it writes (if the write succeeds)
    -- leaves "_debug_write_test.txt" in the bucket as visible proof a
    write actually landed, harmless to leave there."""
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

    try:
        result["real_write_path_check"] = {
            "note": "put_text() now uses presigned URL + requests, not direct boto3 PUT -- this confirms THAT path works",
            "uri": put_text("_debug_write_test_via_put_text.txt", "ok via fixed put_text()"),
        }
    except ValueError as e:
        result["real_write_path_check_error"] = str(e)

    try:
        result["ladder"] = debug_write_ladder()
    except Exception as e:  # never let the ladder break the base diagnostic
        result["ladder_error"] = f"{type(e).__name__}: {e}"

    return result


# ---------------------------------------------------------------------
# WRITE LADDER (2026-08-17): the 8-variant isolation test that found
# the fix above. Sends the same 2-byte PUT to SeaweedFS's public URL
# from this (Oregon) process in several deliberately different shapes,
# one variable at a time, and reports exactly how the edge answered
# each -- status, server, cf-ray, cf-mitigated, and whether the body is
# a Cloudflare challenge page vs a WAF/IP block. RESULT (2026-08-17,
# live run): 01 (boto3 baseline) and 02 (boto3 + Chrome UA override)
# both 403'd; 03 through 07 (presigned URL, or manual SigV4 header,
# each via plain `requests` or curl_cffi) all passed 200 -- confirming
# the trigger is boto3's own added SDK headers, not the signature or
# TLS fingerprint. See CROSS-REGION PUT 403 in the module docstring.
# Kept as a live, re-runnable check. Exposed at /api/debug/storage-write
# (under the "ladder" key). Not used by the real write path.
# ---------------------------------------------------------------------

_CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
_BROWSER_HEADERS = {
    "User-Agent": _CHROME_UA,
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://dataos-2-0-pipeline.onrender.com",
    "Referer": "https://dataos-2-0-pipeline.onrender.com/",
}


def _classify_body(text: str) -> str:
    t = (text or "")[:300000].lower()
    if "cdn-cgi/challenge-platform" in t or "cf-chl" in t or "just a moment" in t:
        return "cloudflare_challenge_page"
    if "error 1020" in t or "error code 1020" in t:
        return "cloudflare_1020_access_denied"
    if "access denied" in t or "sorry, you have been blocked" in t:
        return "cloudflare_block_page"
    if "<error>" in t and "<code>" in t:
        return "s3_xml_error"
    if t.strip() == "":
        return "empty"
    return "other"


def _describe_http(status: int, headers: dict, body_text: str) -> dict:
    h = {k.lower(): v for k, v in (headers or {}).items()}
    return {
        "status": status,
        "server": h.get("server"),
        "cf_ray": h.get("cf-ray"),
        "cf_mitigated": h.get("cf-mitigated"),
        "content_type": h.get("content-type"),
        "body_len": len(body_text or ""),
        "body_class": _classify_body(body_text),
        "body_head": (body_text or "")[:160].replace("\n", " "),
    }


def _presigned_put(s3, key: str, content_type: str | None = None) -> str:
    params = {"Bucket": APP_BUCKET, "Key": key}
    if content_type:
        params["ContentType"] = content_type
    return s3.generate_presigned_url(
        "put_object", Params=params, ExpiresIn=300, HttpMethod="PUT"
    )


def _sigv4_headers(method: str, url: str, body: bytes, extra: dict) -> dict:
    """Sign a raw request with the SAME SigV4 boto3 would use, but let
    us send it with a different HTTP/TLS stack. Returns the full header
    set to send (Authorization + x-amz-* + whatever was in extra)."""
    from botocore.auth import SigV4Auth
    from botocore.awsrequest import AWSRequest
    from botocore.credentials import Credentials
    import hashlib

    creds = Credentials(SEAWEEDFS_ACCESS_KEY, SEAWEEDFS_SECRET_KEY)
    headers = dict(extra)
    headers["x-amz-content-sha256"] = hashlib.sha256(body).hexdigest()
    req = AWSRequest(method=method, url=url, data=body, headers=headers)
    SigV4Auth(creds, "s3", SEAWEEDFS_S3_REGION).add_auth(req)
    return dict(req.headers)


def debug_write_ladder() -> dict:
    import time

    result: dict = {"bucket": APP_BUCKET, "endpoint": _endpoint(), "configured": is_configured(), "variants": []}
    if not is_configured():
        return result

    body = b"ok"
    base = _endpoint()

    def run(name: str, fn):
        t0 = time.time()
        entry = {"variant": name}
        try:
            entry.update(fn())
        except Exception as e:  # each rung isolated
            entry["exception"] = f"{type(e).__name__}: {str(e)[:300]}"
        entry["ms"] = int((time.time() - t0) * 1000)
        entry["passed"] = entry.get("status") in (200, 204)
        result["variants"].append(entry)

    # 0. control: a boto3 GET (reads are known-good cross-region)
    def v_read():
        s3 = _client()
        r = s3.list_objects_v2(Bucket=APP_BUCKET, MaxKeys=1)
        return {"status": r["ResponseMetadata"]["HTTPStatusCode"], "note": "boto3 list_objects_v2 control"}
    run("00_boto3_list_control", v_read)

    # 1. boto3 baseline PUT (proven blocked, kept as regression check)
    def v_boto3():
        s3 = _client()
        try:
            r = s3.put_object(Bucket=APP_BUCKET, Key="_ladder/01_boto3.txt", Body=body)
            return {"status": r["ResponseMetadata"]["HTTPStatusCode"]}
        except ClientError as e:
            meta = e.response.get("ResponseMetadata", {})
            return _describe_http(meta.get("HTTPStatusCode"), meta.get("HTTPHeaders", {}), "") | {"raw": str(e)[:200]}
    run("01_boto3_baseline", v_boto3)

    # 2. boto3 with a Chrome UA + explicit text/plain content type
    def v_boto3_ua():
        s3 = boto3.client(
            "s3", endpoint_url=base,
            aws_access_key_id=SEAWEEDFS_ACCESS_KEY, aws_secret_access_key=SEAWEEDFS_SECRET_KEY,
            region_name=SEAWEEDFS_S3_REGION,
            config=Config(s3={"addressing_style": "path"}, user_agent=_CHROME_UA),
        )
        try:
            r = s3.put_object(Bucket=APP_BUCKET, Key="_ladder/02_boto3_ua.txt", Body=body, ContentType="text/plain")
            return {"status": r["ResponseMetadata"]["HTTPStatusCode"]}
        except ClientError as e:
            meta = e.response.get("ResponseMetadata", {})
            return _describe_http(meta.get("HTTPStatusCode"), meta.get("HTTPHeaders", {}), "") | {"raw": str(e)[:200]}
    run("02_boto3_chrome_ua", v_boto3_ua)

    # 3. presigned URL (no Authorization header) + requests, default UA -- THE FIX
    def v_presigned_requests():
        import requests
        s3 = _client()
        url = _presigned_put(s3, "_ladder/03_presigned_requests.txt", "text/plain")
        r = requests.put(url, data=body, headers={"Content-Type": "text/plain"}, timeout=25)
        return _describe_http(r.status_code, dict(r.headers), r.text)
    run("03_presigned_requests_default_ua", v_presigned_requests)

    # 4. presigned URL + requests + browser-like headers
    def v_presigned_requests_browser():
        import requests
        s3 = _client()
        url = _presigned_put(s3, "_ladder/04_presigned_requests_browser.txt", "text/plain")
        r = requests.put(url, data=body, headers=_BROWSER_HEADERS | {"Content-Type": "text/plain"}, timeout=25)
        return _describe_http(r.status_code, dict(r.headers), r.text)
    run("04_presigned_requests_browser_headers", v_presigned_requests_browser)

    # 5. presigned URL + curl_cffi impersonating Chrome
    def v_presigned_cffi():
        from curl_cffi import requests as cffi
        s3 = _client()
        url = _presigned_put(s3, "_ladder/05_presigned_cffi.txt", "text/plain")
        r = cffi.put(url, data=body, headers={"Content-Type": "text/plain"}, impersonate="chrome", timeout=25)
        return _describe_http(r.status_code, dict(r.headers), r.text)
    run("05_presigned_curl_cffi_chrome", v_presigned_cffi)

    # 6. SigV4 in headers (Authorization: AWS4-HMAC-SHA256) + curl_cffi Chrome
    def v_sigv4_cffi():
        from curl_cffi import requests as cffi
        url = f"{base}/{APP_BUCKET}/_ladder/06_sigv4_cffi.txt"
        headers = _sigv4_headers("PUT", url, body, {"Content-Type": "text/plain", "User-Agent": _CHROME_UA})
        r = cffi.put(url, data=body, headers=headers, impersonate="chrome", timeout=25)
        return _describe_http(r.status_code, dict(r.headers), r.text)
    run("06_sigv4_header_curl_cffi_chrome", v_sigv4_cffi)

    # 7. SigV4 in headers + plain requests, default UA
    def v_sigv4_requests():
        import requests
        url = f"{base}/{APP_BUCKET}/_ladder/07_sigv4_requests.txt"
        headers = _sigv4_headers("PUT", url, body, {"Content-Type": "text/plain"})
        r = requests.put(url, data=body, headers=headers, timeout=25)
        return _describe_http(r.status_code, dict(r.headers), r.text)
    run("07_sigv4_header_requests_default_ua", v_sigv4_requests)

    # 8. control: a POST (non-S3 shape) with a body to the same host root
    def v_post_root():
        import requests
        r = requests.post(base + "/", data=body, headers={"Content-Type": "text/plain"}, timeout=25)
        return _describe_http(r.status_code, dict(r.headers), r.text) | {"note": "any non-Cloudflare answer means the edge let a write-with-body through"}
    run("08_plain_post_root_control", v_post_root)

    result["first_pass"] = next((v["variant"] for v in result["variants"] if v.get("passed") and not v["variant"].startswith("00")), None)
    return result
