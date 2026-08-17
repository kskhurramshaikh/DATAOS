#!/usr/bin/env python3
"""
Comprehensive SeaweedFS write test -- run this directly on
dataos-spike-orchestrator's Shell tab, BEFORE building any relay/
architecture change:

    python3 /opt/spike-dags/test_seaweed_write.py

Per Khurram's explicit ask (2026-08-17): confirm exactly which
combination of (client library) x (endpoint) x (addressing style)
actually succeeds, so nothing gets built around a wrong assumption.

BACKGROUND: app/object_storage.py (dataos-2-0-pipeline, Oregon) gets a
real 403 on boto3 PutObject against SeaweedFS's PUBLIC URL -- confirmed
via /api/debug/storage-write to be a Cloudflare-branded block (server:
cloudflare, cf-ray header, 221KB HTML body) that never reaches the
container (confirmed via dataos-spike-storage's own Render logs showing
nothing at the failure timestamp). Path-style addressing did NOT fix
it. Region did NOT fix it (2-byte test body ruled out a size limit).

What's actually been PROVEN so far, precisely:
  - boto3 create_bucket over INTERNAL networking: WORKS (this DAG's own
    bronze_ingest task calls it every run, successfully).
  - boto3 GetObject / list_objects_v2 over the PUBLIC URL: WORKS (Item 2's
    Lakehouse dashboard reads Bronze/logs this way from Oregon, live).
  - boto3 PutObject over the PUBLIC URL: FAILS (403, confirmed above).
  - boto3 PutObject over INTERNAL networking: NEVER ACTUALLY TESTED.
    bronze_ingest only ever does create_bucket + get_object -- the raw
    Bronze .xlsx is uploaded by hand through SeaweedFS's own Filer UI,
    not by any DAG code.
  - pyiceberg's Iceberg table writes (silver_transform/gold_compute,
    table.append()/overwrite()): WORK, over internal networking -- but
    these go through pyiceberg's own file-writer, which uses PYARROW's
    S3FileSystem under the hood, NOT boto3/botocore. Different HTTP
    client, different underlying library (Arrow's C++ AWS SDK, not
    Python's), different request signing/User-Agent -- never actually
    comparable evidence for whether boto3 specifically can write here.

This script closes every one of those gaps in one run: boto3 PutObject
x {internal, public} x {path-style, virtual-hosted-style}, plus a
pyarrow-based write x {internal, public} using the exact same
S3FileSystem construction pyiceberg's own proven-working write path
uses. Writes tiny (4-byte) objects to the EXISTING "dataos-spike"
bucket under a disposable "_write_test/" prefix -- no new bucket
permissions needed, nothing touches bronze_ingest's real data -- reads
each back to confirm it round-tripped, deletes it, and reports the FULL
error detail (not just str(e)) for anything that fails, same as
object_storage.py's own debug_write_test().

Deliberately a standalone script, not a DAG task -- this needs to run
once, interactively, read its own output directly. Excluded from
Airflow's DAG scanner via .airflowignore in this same folder (same
pattern already used for pg_iceberg_catalog.py), so it adds no per-scan
overhead.
"""
import json
import os
import sys
import time
import traceback

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

BUCKET = "dataos-spike"  # already exists, already proven readable/writable-to for bronze data

INTERNAL_HOST = os.environ.get("SEAWEEDFS_INTERNAL_HOST", "")
S3_PORT = os.environ.get("SEAWEEDFS_S3_PORT", "8333")
PUBLIC_URL = os.environ.get("SEAWEEDFS_PUBLIC_URL", "https://dataos-spike-storage.onrender.com")
ACCESS_KEY = os.environ.get("SEAWEEDFS_ACCESS_KEY", "any")
SECRET_KEY = os.environ.get("SEAWEEDFS_SECRET_KEY", "any")
REGION = os.environ.get("SEAWEEDFS_S3_REGION", "us-east-1")

INTERNAL_ENDPOINT = f"http://{INTERNAL_HOST}:{S3_PORT}" if INTERNAL_HOST else None
ENDPOINTS = {
    "internal": INTERNAL_ENDPOINT,
    "public": PUBLIC_URL.rstrip("/"),
}


def _full_error(e: Exception) -> dict:
    if isinstance(e, ClientError):
        resp = e.response
        meta = resp.get("ResponseMetadata", {})
        headers = dict(meta.get("HTTPHeaders", {}))
        return {
            "type": "ClientError",
            "code": resp.get("Error", {}).get("Code"),
            "message": resp.get("Error", {}).get("Message"),
            "http_status": meta.get("HTTPStatusCode"),
            "server_header": headers.get("server"),
            "content_type": headers.get("content-type"),
            "content_length": headers.get("content-length"),
            "cf_ray": headers.get("cf-ray"),
            "raw": str(e)[:300],
        }
    return {
        "type": type(e).__name__,
        "raw": str(e)[:300],
        "traceback_tail": traceback.format_exc()[-600:],
    }


def test_boto3(endpoint_name: str, endpoint_url: str | None, addressing_style: str) -> dict:
    key = f"_write_test/boto3_{endpoint_name}_{addressing_style}_{int(time.time())}.txt"
    result = {"client": "boto3", "endpoint": endpoint_name, "addressing": addressing_style, "url": endpoint_url}
    if not endpoint_url:
        result["skipped"] = "endpoint not configured"
        return result
    try:
        s3 = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=ACCESS_KEY,
            aws_secret_access_key=SECRET_KEY,
            region_name=REGION,
            config=Config(s3={"addressing_style": addressing_style}),
        )
        s3.put_object(Bucket=BUCKET, Key=key, Body=b"test")
        result["put"] = "ok"
        body = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()
        result["get"] = "ok" if body == b"test" else f"content mismatch: {body!r}"
        s3.delete_object(Bucket=BUCKET, Key=key)
        result["cleanup"] = "ok"
    except (ClientError, BotoCoreError) as e:
        result["error"] = _full_error(e)
    except Exception as e:  # noqa: BLE001 -- comprehensive test, don't let one gap hide behind an uncaught exception
        result["error"] = _full_error(e)
    return result


def test_pyarrow(endpoint_name: str, endpoint_url: str | None) -> dict:
    """Mirrors _iceberg_catalog()'s own s3.* properties exactly (same
    endpoint/region/path-style-access) -- this is the ONLY write path in
    this codebase actually proven working, and it's pyarrow underneath,
    not boto3. If this succeeds where boto3 fails, that's a strong,
    concrete answer: something about boto3's request signature/headers
    specifically, not the endpoint or the operation itself."""
    key = f"_write_test/pyarrow_{endpoint_name}_{int(time.time())}.txt"
    result = {"client": "pyarrow", "endpoint": endpoint_name, "url": endpoint_url}
    if not endpoint_url:
        result["skipped"] = "endpoint not configured"
        return result
    try:
        from pyiceberg.io.pyarrow import PyArrowFileIO

        io_ = PyArrowFileIO({
            "s3.endpoint": endpoint_url,
            "s3.access-key-id": ACCESS_KEY,
            "s3.secret-access-key": SECRET_KEY,
            "s3.region": REGION,
            "s3.path-style-access": "true",
        })
        location = f"s3://{BUCKET}/{key}"
        out = io_.new_output(location)
        with out.create(overwrite=True) as f:
            f.write(b"test")
        result["put"] = "ok"
        inp = io_.new_input(location)
        with inp.open() as f:
            content = f.read()
        result["get"] = "ok" if content == b"test" else f"content mismatch: {content!r}"
    except Exception as e:  # noqa: BLE001
        result["error"] = _full_error(e)
    return result


def main():
    results = []
    for endpoint_name, endpoint_url in ENDPOINTS.items():
        for addressing in ("path", "virtual"):
            results.append(test_boto3(endpoint_name, endpoint_url, addressing))
        results.append(test_pyarrow(endpoint_name, endpoint_url))

    summary = {
        "bucket": BUCKET,
        "internal_host_configured": bool(INTERNAL_HOST),
        "internal_endpoint": INTERNAL_ENDPOINT,
        "public_endpoint": PUBLIC_URL,
        "results": results,
    }
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    sys.exit(main() or 0)
