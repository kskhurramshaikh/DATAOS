"""
Same-region SeaweedFS write relay -- deploy this as its OWN Render web
service in Singapore, alongside dataos-spike-storage/dataos-spike-
orchestrator. Exists solely because of a confirmed, reproducible
Render-platform-edge restriction (2026-08-17): boto3 PutObject to
SeaweedFS's public URL, path-style addressing, identical code and
credentials -- succeeds from Singapore, gets a 403 from Oregon. Proven
with spike/dags/test_seaweed_write.py, run directly on the orchestrator's
Shell tab; see that script's own docstring and the same-day changelog
for the full evidence trail (ruled out: boto3 vs pyarrow, addressing
style, payload size, a manually-configured WAF -- it's specifically
request ORIGIN region). dataos-2-0-pipeline (Oregon) genuinely cannot
write to SeaweedFS directly; this relay can, over the same internal
networking the orchestrator already uses successfully every DAG run.

Deliberately the smallest possible surface: one authenticated write
endpoint, nothing else. Does NOT touch dataos-spike-orchestrator or
Airflow at all -- a brand new, fully isolated service, specifically so
nothing here can destabilize the already-signed-off Item 1 pipeline
(committed date 2026-08-18). Reads still go directly from Oregon to
SeaweedFS's public URL -- already proven working today and well before
today (Item 2's Lakehouse dashboard has read this way from Oregon since
it shipped) -- no reason to relay those too.

AUTH: a single shared-secret header (RELAY_SHARED_SECRET). This service
IS reachable from the public internet (Render services in different
regions can't reach each other over internal/private networking, which
is the entire reason this exists) -- unlike SeaweedFS itself, which
sits behind Render's own network boundary with auth disabled, THIS
service is a new, directly internet-facing surface and needs a real
gate. Generate the secret yourself (e.g. `openssl rand -hex 32`, or
Render's own env var "Generate" button) -- never hardcode one here.

DEPLOYMENT (Render, manual setup -- not covered by render.yaml, same as
how dataos-spike-storage/-orchestrator were originally provisioned):
  1. New Web Service, region = Singapore (must match dataos-spike-
     storage's region -- internal networking only resolves within a
     region, confirmed repeatedly today and during Item 2).
  2. Root/build context: this "relay/" directory. Docker runtime
     (Dockerfile in this same folder).
  3. Env vars needed on THIS service:
       SEAWEEDFS_INTERNAL_HOST   (same value dataos-spike-orchestrator
                                   already uses -- Render's Connect ->
                                   Internal tab on dataos-spike-storage)
       SEAWEEDFS_S3_PORT         8333
       SEAWEEDFS_ACCESS_KEY      any   (SeaweedFS auth is disabled;
       SEAWEEDFS_SECRET_KEY      any    matches every other service)
       RELAY_SHARED_SECRET       <a real generated secret>
  4. Env vars needed on dataos-2-0-pipeline (Oregon), added separately:
       SEAWEEDFS_WRITE_RELAY_URL      <this service's public URL>
       SEAWEEDFS_WRITE_RELAY_SECRET   <the SAME secret from step 3>
  5. Smoke-test THIS service in isolation before wiring up
     dataos-2-0-pipeline -- e.g.:
       curl https://<this-service>.onrender.com/health
       curl -X POST https://<this-service>.onrender.com/write \\
         -H "x-relay-secret: <secret>" -H "Content-Type: application/json" \\
         -d '{"bucket":"dataos-app-datasets","key":"_relay_smoke_test.txt","content":"ok"}'
     Confirms the relay itself works before dataos-2-0-pipeline ever
     depends on it -- catches deploy-specific issues (build, PORT,
     wrong region, wrong internal host) in isolation, not mixed in with
     "does the whole upload flow work end to end."
"""
import os

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

SEAWEEDFS_INTERNAL_HOST = os.environ["SEAWEEDFS_INTERNAL_HOST"]
SEAWEEDFS_S3_PORT = os.environ.get("SEAWEEDFS_S3_PORT", "8333")
SEAWEEDFS_ACCESS_KEY = os.environ.get("SEAWEEDFS_ACCESS_KEY", "any")
SEAWEEDFS_SECRET_KEY = os.environ.get("SEAWEEDFS_SECRET_KEY", "any")
SEAWEEDFS_S3_REGION = os.environ.get("SEAWEEDFS_S3_REGION", "us-east-1")
RELAY_SHARED_SECRET = os.environ["RELAY_SHARED_SECRET"]

ENDPOINT = f"http://{SEAWEEDFS_INTERNAL_HOST}:{SEAWEEDFS_S3_PORT}"

app = FastAPI(title="SeaweedFS write relay (Singapore, internal-only writes)")

_verified_buckets: set[str] = set()


def _client():
    # Same exact construction proven working in
    # spike/dags/test_seaweed_write.py's "internal" + "path" case --
    # not a new guess, the one combination already confirmed to succeed.
    return boto3.client(
        "s3",
        endpoint_url=ENDPOINT,
        aws_access_key_id=SEAWEEDFS_ACCESS_KEY,
        aws_secret_access_key=SEAWEEDFS_SECRET_KEY,
        region_name=SEAWEEDFS_S3_REGION,
        config=Config(s3={"addressing_style": "path"}),
    )


def _ensure_bucket(s3, bucket: str) -> None:
    """Per-bucket ensure, cached in memory for this process's lifetime --
    same pattern app/object_storage.py already uses, moved server-side
    here since this relay -- not Oregon -- is what can actually reach
    SeaweedFS's admin operations reliably."""
    if bucket in _verified_buckets:
        return
    try:
        s3.head_bucket(Bucket=bucket)
    except ClientError:
        s3.create_bucket(Bucket=bucket)
    _verified_buckets.add(bucket)


class WriteRequest(BaseModel):
    bucket: str
    key: str
    content: str  # UTF-8 text -- matches object_storage.put_text()'s own contract exactly


@app.get("/health")
def health():
    return {"status": "ok", "endpoint": ENDPOINT}


@app.post("/write")
def write(req: WriteRequest, x_relay_secret: str = Header(...)):
    if x_relay_secret != RELAY_SHARED_SECRET:
        raise HTTPException(status_code=401, detail="Invalid relay secret.")

    s3 = _client()
    try:
        _ensure_bucket(s3, req.bucket)
        s3.put_object(Bucket=req.bucket, Key=req.key, Body=req.content.encode("utf-8"))
    except (ClientError, BotoCoreError) as e:
        # 502, not 500 -- this failure is genuinely "the upstream (SeaweedFS)
        # rejected or was unreachable," not a bug in this relay itself.
        raise HTTPException(status_code=502, detail=f"SeaweedFS write failed: {e}")

    return {"uri": f"s3://{req.bucket}/{req.key}"}
