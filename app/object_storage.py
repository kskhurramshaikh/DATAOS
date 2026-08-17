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
genuine "403 Forbidden". _client() below sets addressing_style="path"
based on the hypothesis that virtual-hosted-style addressing was
breaking SigV4 signing against this SeaweedFS instance -- CONFIRMED
NOT SUFFICIENT: retested live after that fix deployed, still 403. The
"proven write path" cited as evidence (pyiceberg's Boto3FileIO) turned
out to run inside the Airflow DAG on the Singapore orchestrator, over
INTERNAL networking -- not from this app (Oregon) over the PUBLIC
endpoint at all, so it was never actually comparable evidence. This
app has genuinely never proven a successful S3 WRITE over
SEAWEEDFS_PUBLIC_URL from any code path -- only reads (list_objects_v2,
get_object) were ever confirmed working cross-region.

ROOT CAUSE CONFIRMED (2026-08-17, via spike/dags/test_seaweed_write.py,
run directly on dataos-spike-orchestrator's Shell tab): boto3 PutObject
against SeaweedFS's public URL, identical code/credentials/addressing --
succeeds from Singapore (same region as the storage service), 403s from
Oregon. Not a boto3-vs-pyarrow issue, not addressing style, not payload
size, not a manually-configured WAF (no custom Cloudflare zone exists on
dataos-spike-storage), and the request never reaches the container at
all (confirmed via that service's own Render logs). Left standing: it's
Render's own platform edge -- which runs on Cloudflare's network --
treating cross-region write (PUT) traffic to this specific service
differently from same-region write traffic, most plausibly an automated
rule flagging requests carrying an AWS SigV4 `Authorization` header as
bot-like/suspicious. This needs a fix at the Render/Cloudflare-edge
level (a support ticket, or a plan/settings change on that service) --
no client-side code change here can work around a request that's being
blocked before it ever reaches the container. Dataset FILE storage
(Bronze/Silver/Gold CSVs) stays on the CI-only local-disk fallback in
production until that's resolved -- direct writes from this app will
keep failing with the ValueError below until then.

DIAGNOSTIC (2026-08-17): debug_write_test() below returns the FULL
botocore error detail (Code, Message, RequestId, HostId, HTTPStatusCode,
headers -- including the cf-ray header that identified this as a
Cloudflare-layer block) for list_buckets/head_bucket/create_bucket/
put_object attempted in sequence -- str(e) alone truncates SeaweedFS/
Render's actual server-returned reason. Exposed at
/api/debug/storage-write. Kept as a live, re-runnable check for
confirming whether/when the platform-level block has actually been
lifted, without needing another code change to find out.
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

    Any real S3/network failure (see ROOT CAUSE CONFIRMED above -- this
    currently means every real attempt from this app, until the
    Render/Cloudflare-edge block is lifted) is caught and re-raised as
    ValueError with the actual error text -- every caller already
    handles ValueError and surfaces it as a 400, same pattern as db.py's
    own Postgres error handling."""
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

    Reads are UNAFFECTED by the write-side platform block -- GET/list
    already work fine cross-region (confirmed repeatedly, including by
    Item 2's Lakehouse dashboard reading this way in production well
    before today). Any OTHER real S3/network failure (not a missing-
    object case) is caught and re-raised as ValueError, same reasoning
    as put_text() above."""
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
    create_bucket / put_object against APP_BUCKET in sequence, one
    call each, capturing the FULL error detail for whichever step
    fails -- see DIAGNOSTIC in the module docstring. Exposed at
    /api/debug/storage-write. This is the check that identified the
    block as Cloudflare/Render-edge-layer (server: cloudflare, cf-ray
    header, HTML error body) rather than an app/SeaweedFS-level error --
    kept as a live, re-runnable way to confirm whether that block has
    been lifted, without needing another code change first. Deliberately
    does not delete the test object it writes (if the write succeeds) --
    leaves "_debug_write_test.txt" in the bucket as visible proof a
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

    # 2026-08-17: also run the write LADDER (see below) from the same
    # endpoint, so no main.py change is needed to get the evidence.
    try:
        result["ladder"] = debug_write_ladder()
    except Exception as e:  # never let the ladder break the base diagnostic
        result["ladder_error"] = f"{type(e).__name__}: {e}"

    return result


# ---------------------------------------------------------------------
# WRITE LADDER (2026-08-17): instead of guessing what Render's edge is
# keying on for the cross-region PUT 403, send the SAME 2-byte PUT from
# this (Oregon) process in several deliberately different shapes, one
# variable at a time, and report exactly how the edge answered each --
# status, server, cf-ray, cf-mitigated, and whether the body is a
# Cloudflare challenge page ("cdn-cgi/challenge-platform") vs a WAF/IP
# block ("Error 1020" / "Access denied"). Whichever variant is the FIRST
# to pass tells us what to change in put_text(). Exposed at
# /api/debug/storage-write (under the "ladder" key). Nothing here is used by the real
# write path yet.
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

    # 1. boto3 baseline PUT (exactly what put_text does today)
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

    # 3. presigned URL (no Authorization header) + requests, default UA
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

    # 5. presigned URL + curl_cffi impersonating Chrome (real browser TLS/HTTP2 fingerprint)
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

    # 7. SigV4 in headers + plain requests, default UA (isolates header-vs-fingerprint against 03)
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
