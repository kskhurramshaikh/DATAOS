# DataOS 2.0 -- Pipeline Rails + Conversational Interface
#
# The raw intent pipeline (compliance -> router -> adapter) is unchanged
# from Phase One -- POST /intent still exists exactly as before, for
# direct/programmatic use. What's new is the layer in front of it: sign
# up, log in, and talk to DataOS in plain English at "/". The chat
# endpoints call the exact same pipeline internally; nothing about the
# governed rails changes underneath it.
#
# /chat and /chat/upload are the original non-streaming endpoints, kept
# for programmatic use and tests. /chat/stream is what the UI actually
# uses now -- same logic, but reports real progress as it happens via
# Server-Sent Events, instead of one blocking request/response.
#
# IMPORTANT: the streaming work (OpenRouter calls, pandas, sqlite) runs
# in a background thread, not directly on the event loop. Render's
# health check hits this same process -- if a slow OpenRouter call had
# blocked the event loop for several seconds, Render would see /health
# time out, conclude the instance died, and restart it mid-request.
# That's a real failure that happened during testing, not a theoretical
# one -- see _run_in_thread() below.
#
# Item 2 (DataOS 3.0 Development Queue): Lakehouse Zones + Pipeline
# Monitoring. The new React dashboard (built by dashboard/, output baked
# into app/static/dashboard/ at Docker build time -- see the repo-root
# Dockerfile's frontend-build stage) is served at "/dashboard", reading
# live data from the spike infrastructure via app/lakehouse_client.py's
# three new /api/lakehouse/* and /api/pipeline/* endpoints below. The
# chat interface itself is untouched -- still "/app", same as before.
#
# Item 3: Golden Record Registry + Duplicate Queue (MDM). New
# /api/mdm/* endpoints below, dashboard pages under "/dashboard/mdm".
# IMPORTANT: the dashboard and chat are two INDEPENDENT, fully-working
# entry points onto the same governed pipeline (per the landing page's
# own "Open Dashboard" / "Talk to DataOS" framing) -- the MDM endpoints
# below therefore include the dashboard's OWN upload-dataset and
# detect-duplicates actions, not just read/decide on data that could
# only ever get there via chat. Same underlying dataset_adapter/
# dedup_adapter functions either path calls -- one shared data layer,
# two genuinely separate front doors. Bulk-confirm ("clear all high-
# confidence" / "clear all pending") is likewise carried over from
# chat's own smart-recommendation buttons, not left dashboard-only-
# missing.
#
# Item 4: SAMA Compliance Dashboard + Audit Log page. New
# /api/governance/* endpoints below, dashboard pages under
# "/dashboard/governance". Both wrap logic already computed and signed
# off (banking_adapter.run_sama_compliance, dedup_adapter.get_audit_log)
# -- presentation-only, same as Dev Queue item 4's own framing, no new
# computation path introduced.
#
# Item 5: NDI Assessment Dashboard + History, dashboard pages under
# "/dashboard/ndi". The assessment endpoint is presentation-only in the
# same sense as item 4 -- it wraps banking_adapter.compute_ndi_
# assessment(), the exact function the chat "assess_ndi" chip already
# renders as its ndi_assessment component. The History half is the one
# genuinely new piece: app/adapters/ndi_history.py stores each recorded
# assessment as a dated, attributed audit record. Per Dr. Saber's
# 2026-08-11 scoping answer this stays domain-level (14 domains,
# weights, maturity scale); the full 191-spec drill-down is deferred.
# NOTE, deliberately not hidden: NDI's per-domain inputs are his fixed
# BAJ demo baseline, so recorded snapshots read identically until those
# inputs change -- ndi_history reports that as a fact rather than
# manufacturing movement. See its module docstring.
#
# Item 6: Data Catalog + Field Lineage, dashboard pages under
# "/dashboard/catalog". New /api/catalog/* endpoints below, reading
# real data from Marquez (the actual OpenLineage reference
# implementation this stack sends every DAG run's lineage events to --
# see app/marquez_client.py's own module docstring for the full
# reasoning, including why field lineage is assembled from job run
# facets directly rather than Marquez's own /datasets endpoint, which
# was confirmed to stay empty in this setup).
#
# ERROR-HANDLING NOTE (2026-08-17): several /api/mdm/* routes below
# catch Exception broadly, not just ValueError -- a deliberate widening
# added mid-debugging the Postgres migration, after upload-dataset's
# ValueError-only handling turned out to hide a real, diagnosable
# Postgres error as an opaque 500 with zero detail (fixed separately by
# also wrapping db.py's own errors as ValueError -- see that file's
# docstring). Rather than assume every remaining DB-writing route in
# this file only ever raises ValueError, decide/bulk-confirm now also
# catch bare Exception and surface str(e) as a 500 -- still visible in
# the response body, not swallowed, just distinguished from an
# intentional 400 by status code. Narrow this back to ValueError-only
# once the Postgres migration has proven stable across these routes.

import asyncio
import json as json_lib
import os
import queue
import threading
import time

from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse, StreamingResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.compliance_agent import evaluate
from app.router import route, NoCapabilityRegisteredError
from app.capability_registry import CAPABILITY_REGISTRY
from app.db import init_db, storage_status
from app import auth, chat_store, field_lineage, lakehouse_client, marquez_client, object_storage
from app.adapters import dataset_adapter, banking_adapter, dedup_adapter, ndi_history, stewardship_adapter, classification_adapter, quality_adapter
from app.visualization import suggest_visualization
from app.interpreter import interpret, interpret_stream, explain_result

app = FastAPI(title="DataOS 2.0 -- Pipeline Rails")

init_db()

app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Dashboard's built static assets (JS/CSS bundle) -- only mounted if the
# frontend build actually produced output, so this app still boots fine
# in a dev/test environment that skipped the Node build stage.
_DASHBOARD_DIST = "app/static/dashboard"
if os.path.isdir(_DASHBOARD_DIST):
    app.mount("/dashboard/assets", StaticFiles(directory=f"{_DASHBOARD_DIST}/assets"), name="dashboard-assets")


class IntentRequest(BaseModel):
    intent: str
    context: dict = {}
    payload: dict = {}


class SignupRequest(BaseModel):
    name: str
    email: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str


class ChatRequest(BaseModel):
    message: str
    conversation_id: int | None = None


def _sse(event_type: str, data: dict) -> str:
    return f"event: {event_type}\ndata: {json_lib.dumps(data)}\n\n"


def _resolve_conversation(conversation_id: int | None, user_id: int) -> int:
    if conversation_id is None:
        return chat_store.create_conversation(user_id)
    owner_id = chat_store.get_conversation_owner(conversation_id)
    if owner_id != user_id:
        return chat_store.create_conversation(user_id)
    return conversation_id


async def _run_in_thread(sync_generator):
    """
    Runs a blocking sync generator (OpenRouter calls, pandas, sqlite --
    anything that isn't safe to await directly) on a background thread,
    yielding its items back asynchronously. Each wait for the next item
    goes through run_in_executor, so the event loop is genuinely free
    the whole time -- Render's health check and any other request keep
    getting served while this is in flight.
    """
    q: queue.Queue = queue.Queue()
    DONE = object()

    def worker():
        try:
            for item in sync_generator:
                q.put(("item", item))
        except Exception as e:  # noqa: BLE001 -- deliberately broad, re-raised below
            q.put(("error", e))
        finally:
            q.put(("done", DONE))

    threading.Thread(target=worker, daemon=True).start()

    loop = asyncio.get_event_loop()
    while True:
        kind, payload = await loop.run_in_executor(None, q.get)
        if kind == "done":
            return
        if kind == "error":
            raise payload
        yield payload


# ---------------------------------------------------------------------
# Landing page (item 9) -- the marketing/product landing page is now
# the actual front door at "/". The conversational chat UI (sign up,
# log in, talk to DataOS) is at "/app"; the new dashboard (item 2 on)
# is at "/dashboard" -- both linked from the landing page, letting the
# visitor choose their entry point up front, per the 2026-08-12
# meeting outcome.
# ---------------------------------------------------------------------

@app.get("/")
def root():
    return FileResponse("app/static/landing.html")


@app.get("/app")
def chat_app():
    return FileResponse("app/static/index.html")


@app.get("/dashboard")
@app.get("/dashboard/{full_path:path}")
def dashboard_app(full_path: str = ""):
    """Serves the dashboard's built index.html for every dashboard route
    -- a standard SPA catch-all, since React Router handles the actual
    path matching client-side. Falls back to a plain message if the
    frontend hasn't been built into this image (e.g. a Dockerfile that
    skipped the Node build stage)."""
    index_path = f"{_DASHBOARD_DIST}/index.html"
    if os.path.isfile(index_path):
        return FileResponse(index_path)
    return PlainTextResponse(
        "Dashboard build not found in this image -- check the Dockerfile's frontend-build stage.",
        status_code=503,
    )


@app.get("/api/info")
def api_info():
    return {
        "service": "DataOS 2.0 -- Pipeline Rails",
        "status": "ok",
        "docs": "/docs",
        "health": "/health",
        "available_intents": list(CAPABILITY_REGISTRY.keys()),
    }


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/debug/storage")
def debug_storage():
    """Which storage backend is actually running RIGHT NOW in this
    process (Postgres vs the ephemeral SQLite fallback), plus live
    golden_records/duplicate_clusters row counts -- added 2026-08-17
    after a Postgres-should-be-active redeploy came back with an empty
    Golden Record Registry and there was no way to tell from the
    outside whether that meant "nothing's been created since the fix"
    or "silently still on SQLite." See app/db.py's storage_status()
    docstring for the full reasoning. Deliberately unauthenticated,
    same pattern as /api/lakehouse/debug -- read-only, no secrets in
    the response (the Postgres URI is masked to host only)."""
    return storage_status()


@app.get("/api/debug/storage-write")
def debug_storage_write():
    """Diagnostic for the SeaweedFS write path specifically (2026-08-17)
    -- added after a real 403 on PutObject survived a first fix attempt
    (path-style addressing), to get the FULL server-returned error
    detail instead of guessing a second time. See
    object_storage.debug_write_test()'s own docstring for exactly what
    it attempts. Deliberately unauthenticated, same pattern as every
    other /api/debug/* and /api/lakehouse/debug endpoint -- read-only
    in intent (the one object it writes, if it gets that far, is
    harmless diagnostic proof left in the bucket on purpose)."""
    return object_storage.debug_write_test()


# ---------------------------------------------------------------------
# Lakehouse dashboard API (Item 2) -- reads REAL data from the spike
# infrastructure via app/lakehouse_client.py. See that module's own
# docstring for exactly what's queried and from where. Deliberately
# unauthenticated for now, matching the read-only, no-secrets-exposed
# nature of what these return (row counts, run status, log text) --
# revisit if/when this dashboard needs its own access control distinct
# from the chat app's user accounts.
# ---------------------------------------------------------------------

@app.get("/api/lakehouse/zones")
def lakehouse_zones(dataset_name: str | None = None):
    return lakehouse_client.get_zone_stats(dataset_name)


@app.get("/api/pipeline/runs")
def pipeline_runs(dataset_name: str | None = None, limit: int = 10):
    return lakehouse_client.get_pipeline_runs(dataset_name, limit=limit)


class LakehouseTriggerRequest(BaseModel):
    dataset_name: str


@app.post("/api/lakehouse/trigger")
def lakehouse_trigger(req: LakehouseTriggerRequest):
    """Manually triggers the Lakehouse (Silver/Gold Iceberg promotion)
    pipeline for one dataset -- see lakehouse_client.trigger_dag_run()'s
    docstring for why this is the ONLY way that DAG ever runs. Never
    called automatically on upload, per Khurram's explicit instruction
    (2026-08-18)."""
    try:
        return lakehouse_client.trigger_dag_run(req.dataset_name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/pipeline/logs/{run_id}/{task_id}")
def pipeline_task_log(run_id: str, task_id: str, try_number: int = 1):
    return lakehouse_client.get_task_log(run_id, task_id, try_number)


@app.get("/api/lakehouse/debug")
def lakehouse_debug(namespace: str = "silver", table_name: str = "ifrs9_portfolio"):
    """Temporary diagnostic endpoint (Item 2 rollout) -- compares a plain
    boto3 read against pyiceberg's own pyarrow-based read of the SAME
    Iceberg metadata file, to isolate why load_table() comes back with
    an empty body over the public SeaweedFS endpoint. See
    lakehouse_client.debug_metadata_read()'s docstring for why this
    exists instead of testing interactively (no Shell on this service's
    Render plan). Remove once the underlying read issue is fixed."""
    return lakehouse_client.debug_metadata_read(namespace=namespace, table_name=table_name)


@app.get("/api/lakehouse/debug-catalog")
def lakehouse_debug_catalog(dataset_name: str | None = None):
    """Diagnostic (2026-08-18) -- added after a DAG run showed 'success'
    in Airflow's own UI while the dashboard kept reporting that
    dataset's Silver/Gold as 'never run'. See
    lakehouse_client.debug_catalog_scan()'s own docstring for exactly
    what this compares and why."""
    return lakehouse_client.debug_catalog_scan(dataset_name)


# ---------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------

@app.post("/auth/signup")
def signup(req: SignupRequest):
    user = auth.create_user(req.email, req.name, req.password)
    token = auth.issue_token(user)
    return {"token": token, "user": {"id": user["id"], "name": user["name"], "email": user["email"]}}


@app.post("/auth/login")
def login(req: LoginRequest):
    user = auth.authenticate_user(req.email, req.password)
    token = auth.issue_token(user)
    return {"token": token, "user": {"id": user["id"], "name": user["name"], "email": user["email"]}}


# ---------------------------------------------------------------------
# Chat (non-streaming) -- kept for programmatic use and tests
# ---------------------------------------------------------------------

@app.post("/chat")
def chat(req: ChatRequest, user: dict = Depends(auth.get_current_user)):
    conversation_id = _resolve_conversation(req.conversation_id, user["id"])

    history = chat_store.get_history(conversation_id)
    try:
        result = interpret(history, req.message)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))

    chat_store.add_message(conversation_id, "user", req.message)
    chat_store.add_message(conversation_id, "assistant", result["reply"])

    return {
        "reply": result["reply"],
        "conversation_id": conversation_id,
        "ran_intent": result["ran_intent"],
    }


@app.post("/chat/upload")
async def chat_upload(
    file: UploadFile = File(...),
    dataset_name: str = Form(...),
    conversation_id: int | None = Form(None),
    user: dict = Depends(auth.get_current_user),
):
    conversation_id = _resolve_conversation(conversation_id, user["id"])

    raw_bytes = await file.read()
    try:
        csv_content, sheet_used = dataset_adapter.extract_csv_content(file.filename, raw_bytes)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    sheet_note = f' (sheet: "{sheet_used}")' if sheet_used else ""
    user_message = f'Uploaded a dataset file ({file.filename}{sheet_note}) to add as "{dataset_name}".'

    decision = evaluate("add_dataset", {})
    if not decision.allowed:
        raw_result = {"status": "blocked", "compliance": decision.to_dict()}
        ran_intent = None
    else:
        try:
            routed = route(
                "add_dataset",
                {"dataset_name": dataset_name, "csv_content": csv_content, "uploaded_by": user["id"]},
            )
            raw_result = {
                "status": "completed",
                "compliance": decision.to_dict(),
                "routing": {"capability": routed["capability"], "tool": routed["tool"]},
                "output": routed["result"],
            }
            ran_intent = "add_dataset"
        except (NoCapabilityRegisteredError, ValueError) as e:
            raw_result = {"status": "error", "compliance": decision.to_dict(), "error": str(e)}
            ran_intent = None

    try:
        reply = explain_result(user_message, "add_dataset", raw_result)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))

    chat_store.add_message(conversation_id, "user", user_message)
    chat_store.add_message(conversation_id, "assistant", reply)

    return {"reply": reply, "conversation_id": conversation_id, "ran_intent": ran_intent}


# ---------------------------------------------------------------------
# Chat (streaming) -- what the UI actually calls. Both branches below
# are plain sync generators (no "async def") -- they run entirely on
# the background thread via _run_in_thread(), never touching the event
# loop directly. That's what keeps /health responsive during a request.
# ---------------------------------------------------------------------

def _text_chat_events(history: list[dict], message: str, conv_id: int):
    reply = None
    ran_intent = None
    for event in interpret_stream(history, message):
        if event["type"] == "final":
            reply = event["reply"]
            ran_intent = event["ran_intent"]
        else:
            yield event

    chat_store.add_message(conv_id, "user", message)
    chat_store.add_message(conv_id, "assistant", reply)
    yield {"type": "final", "reply": reply, "conversation_id": conv_id, "ran_intent": ran_intent}


def _dataset_upload_events(dataset_name: str, raw_bytes: bytes, csv_content: str, filename: str, sheet_used: str | None, user_id: int, conv_id: int):
    sheet_note = f' (sheet: "{sheet_used}")' if sheet_used else ""
    user_message = f'Uploaded a dataset file ({filename}{sheet_note}) to add as "{dataset_name}".'
    _upload_start_time = time.time()

    yield {"type": "status", "stage": "compliance", "label": "Checking compliance rules..."}
    decision = evaluate("add_dataset", {})

    if not decision.allowed:
        raw_result = {"status": "blocked", "compliance": decision.to_dict()}
        ran_intent = None
    else:
        try:
            yield {"type": "status", "stage": "bronze", "label": "Landing raw data into Bronze..."}
            bronze = dataset_adapter.land_bronze(dataset_name, csv_content)

            yield {"type": "status", "stage": "silver", "label": "Cleaning and deduplicating into Silver..."}
            silver = dataset_adapter.clean_to_silver(bronze["safe_name"], bronze["df"])

            output = {
                "dataset_name": bronze["safe_name"],
                "rows": len(silver["silver_df"]),
                "columns": list(silver["silver_df"].columns),
                "duplicate_rows_removed": silver["duplicate_rows_removed"],
                "null_counts": silver["null_counts"],
                "storage": {"bronze": bronze["bronze_path"], "silver": silver["silver_path"], "gold": None},
            }

            # NOTE: unlike earlier versions, duplicate detection / NDI /
            # IFRS 9 are NOT auto-run here anymore -- they're offered as
            # recommendations below and only run when the user picks one.
            # That means Gold promotion is no longer gated on an
            # unresolved duplicate check the user never asked for --
            # only the null-rate quality gate (which is unconditional,
            # not something the user opts into) still blocks promotion.
            if silver["held"]:
                yield {"type": "status", "stage": "held", "label": "Data quality check: holding at Silver..."}
                output["stage"] = "silver_held"
                output["hold_reason"] = silver["hold_reason"]
                output["numeric_summary"] = {}
                output["top_categories"] = {}
                output["dropped_columns"] = []
                dataset_adapter._upsert_dataset_record(
                    bronze["safe_name"], bronze["display_name"], user_id, "silver_held",
                    output["rows"], output["columns"], [], output["duplicate_rows_removed"],
                    output["null_counts"], bronze["bronze_path"], silver["silver_path"], None,
                )
            else:
                yield {"type": "status", "stage": "gold", "label": "Promoting to Gold..."}
                gold = dataset_adapter.promote_to_gold(bronze["safe_name"], silver["silver_df"])
                output["stage"] = "gold"
                output["numeric_summary"] = gold["numeric_summary"]
                output["top_categories"] = gold["top_categories"]
                output["dropped_columns"] = gold["dropped_columns"]
                output["storage"]["gold"] = gold["gold_path"]
                dataset_adapter._upsert_dataset_record(
                    bronze["safe_name"], bronze["display_name"], user_id, "gold",
                    output["rows"], output["columns"], gold["dropped_columns"],
                    output["duplicate_rows_removed"], output["null_counts"],
                    bronze["bronze_path"], silver["silver_path"], gold["gold_path"],
                )

            raw_result = {
                "status": "completed",
                "compliance": decision.to_dict(),
                "routing": {"capability": "ingest_dataset_to_medallion", "tool": "pandas_medallion_pipeline"},
                "output": output,
            }
            ran_intent = "add_dataset"
        except ValueError as e:
            raw_result = {"status": "error", "compliance": decision.to_dict(), "error": str(e)}
            ran_intent = None

    yield {"type": "tool_result", "data": raw_result}

    if ran_intent == "add_dataset":
        out = raw_result["output"]
        email_col = next((c for c in out.get("null_counts", {}) if "email" in str(c).lower()), None)
        rows = out.get("rows", 0)
        email_null_count = out["null_counts"].get(email_col, 0) if email_col else 0
        yield {
            "type": "component",
            "component_type": "processing_summary",
            "component_data": {
                "total_records": rows,
                "gold_records": rows if out.get("stage") == "gold" else 0,
                "silver_held": rows if out.get("stage") == "silver_held" else 0,
                "email_null_pct": round(100 * email_null_count / rows, 1) if rows and email_col else None,
                "processing_time_s": round(time.time() - _upload_start_time, 2),
            },
        }

    # Detect which follow-up capabilities are actually relevant to what
    # was just uploaded, and offer them as recommendations instead of
    # running them automatically -- the user decides what happens next
    # with their own data, same principle as the review-before-Gold gate.
    if ran_intent == "add_dataset":
        recommendations = dataset_adapter.get_followup_recommendations(raw_result["output"]["dataset_name"], raw_bytes)
        if recommendations:
            yield {
                "type": "recommendations",
                "dataset_name": raw_result["output"]["dataset_name"],
                "filename": filename,
                "options": recommendations,
            }

    yield {"type": "status", "stage": "explaining", "label": "Writing a plain-English summary..."}

    charts = suggest_visualization("add_dataset", raw_result)
    if charts:
        yield {"type": "visualization", "charts": charts}

    reply = explain_result(user_message, "add_dataset", raw_result, has_visualization=bool(charts))  # may raise RuntimeError -- caught by the caller

    chat_store.add_message(conv_id, "user", user_message)
    chat_store.add_message(conv_id, "assistant", reply)
    yield {"type": "final", "reply": reply, "conversation_id": conv_id, "ran_intent": ran_intent}


# ---------------------------------------------------------------------
# Single follow-up analysis -- what actually runs when a user clicks one
# of the recommendation chips after an upload (assess NDI, compute
# IFRS 9, or find duplicates). Deliberately separate from the upload
# flow above: these are optional, on-demand actions, not automatic
# side effects of uploading a file.
# ---------------------------------------------------------------------

def _find_customer_sheet_csv(raw_bytes: bytes, sheet_names: list[str]) -> str | None:
    """Locates a customer/MDM-style sheet in the same workbook as the
    IFRS 9 sheet, for joining real names onto the top_5_risk table --
    the IFRS 9 sheet itself is loan-level (loan IDs, no customer name
    column). Best-effort: returns None if nothing matching is found,
    so run_ifrs9 falls back to its existing loan-ID-only display
    rather than guessing at a sheet that isn't there."""
    customer_sheet = next(
        (s for s in sheet_names if "customer" in s.lower() or "mdm" in s.lower()),
        None,
    )
    if not customer_sheet:
        return None
    try:
        return dataset_adapter.extract_specific_sheet_csv(raw_bytes, customer_sheet)
    except ValueError:
        return None


def _run_recommended_action_events(action: str, dataset_name: str, raw_bytes: bytes | None, user_id: int, conv_id: int, scenario: str = "base"):
    if action == "select_ifrs9_scenario":
        # No computation yet -- just let the user pick which macro
        # scenario to model. Nothing runs, no LLM call needed, until
        # they actually choose one of the three.
        scenario_labels = {"optimistic": "\U0001F4C8 Optimistic scenario", "base": "\U0001F4CA Base scenario", "adverse": "\U0001F4C9 Adverse scenario"}
        options = [
            {
                "action": "compute_ifrs9",
                "scenario": s,
                "label": scenario_labels.get(s, s.title()),
                "description": banking_adapter.MACRO_SCENARIOS[s]["description"],
            }
            for s in banking_adapter.MACRO_SCENARIOS
        ]
        yield {"type": "recommendations", "dataset_name": dataset_name, "options": options}
        reply = "Which macro scenario would you like to model for IFRS 9?"
        chat_store.add_message(conv_id, "user", f'Compute IFRS 9 on dataset "{dataset_name}".')
        chat_store.add_message(conv_id, "assistant", reply)
        yield {"type": "final", "reply": reply, "conversation_id": conv_id, "ran_intent": None}
        return

    intent_map = {
        "assess_ndi": ("assess_ndi_readiness", "assess_data_governance_readiness", "ndi_scorecard_engine"),
        "compute_ifrs9": ("compute_ifrs9_ecl", "compute_expected_credit_loss", "ifrs9_ecl_engine"),
        "find_duplicates": ("find_duplicate_candidates", "detect_entity_duplicates", "rapidfuzz_dob_clustering"),
        "sama_compliance": ("assess_sama_compliance", "assess_sama_compliance_status", "sama_compliance_scorer"),
        "customer_360": ("assess_customer_360", "assess_customer_data_quality", "customer_360_analyzer"),
    }
    if action not in intent_map:
        yield {"type": "final", "reply": f"Unknown action '{action}'.", "conversation_id": conv_id, "ran_intent": None}
        return

    intent, capability, tool = intent_map[action]
    scenario_note = f' (scenario: "{scenario}")' if action == "compute_ifrs9" else ""
    user_message = f'Run "{intent}" on dataset "{dataset_name}"{scenario_note}.'

    yield {"type": "status", "stage": "compliance", "label": "Checking compliance rules..."}
    decision = evaluate(intent, {})

    if not decision.allowed:
        raw_result = {"status": "blocked", "compliance": decision.to_dict()}
        ran_intent = None
    else:
        try:
            if action in ("assess_ndi", "compute_ifrs9"):
                if not raw_bytes:
                    raise ValueError("The original file wasn't available for this follow-up action -- please re-attach it.")
                sheet_names = dataset_adapter.list_excel_sheet_names(raw_bytes)
                keyword = "ndi" if action == "assess_ndi" else "ifrs"
                sheet = next((s for s in sheet_names if keyword in s.lower()), None)
                if not sheet:
                    raise ValueError(f"No sheet matching '{keyword}' found in the re-attached file.")
                yield {"type": "status", "stage": action, "label": f"Running {intent}..."}
                sheet_csv = dataset_adapter.extract_specific_sheet_csv(raw_bytes, sheet)
                output = banking_adapter.run_ndi({"csv_content": sheet_csv}) if action == "assess_ndi" \
                    else banking_adapter.run_ifrs9({
                        "csv_content": sheet_csv,
                        "scenario": scenario,
                        "customer_csv_content": _find_customer_sheet_csv(raw_bytes, sheet_names) if action == "compute_ifrs9" else None,
                    })
            elif action in ("sama_compliance", "customer_360"):
                yield {"type": "status", "stage": action, "label": f"Running {intent}..."}
                output = banking_adapter.compute_sama_compliance(dataset_name) if action == "sama_compliance" \
                    else banking_adapter.compute_customer_360(dataset_name)
            else:  # find_duplicates
                yield {"type": "status", "stage": "dedup", "label": "Checking for duplicate customer records..."}
                silver_csv = dataset_adapter.read_silver_csv(dataset_name)
                output = dedup_adapter.find_duplicate_candidates({"csv_content": silver_csv, "dataset_name": dataset_name})
                pending = output.get("total_clusters", 0) if output.get("applicable") else 0
                if pending > 0:
                    yield {"type": "duplicate_review", "dataset_name": dataset_name, "clusters": output["clusters"]}

            raw_result = {
                "status": "completed",
                "compliance": decision.to_dict(),
                "routing": {"capability": capability, "tool": tool},
                "output": output,
            }
            ran_intent = intent
        except ValueError as e:
            raw_result = {"status": "error", "compliance": decision.to_dict(), "error": str(e)}
            ran_intent = None

    # The duplicate-review cards (the "duplicate_review" event above) are
    # the full user-facing view for this action -- a bank exec should
    # never see the raw matching internals (min_name_similarity, the
    # methodology note, the literal tool name) that used to leak into
    # the "View details" panel below the cards on every run. Everyone
    # else's raw_result (NDI, IFRS 9) is untouched.
    #
    # IMPORTANT: this sanitizes a SEPARATE copy for display only.
    # raw_result itself is left alone, because suggest_visualization()
    # and explain_result() below both run on raw_result afterwards and
    # read the original field names (high_confidence_clusters, etc.) --
    # an earlier version of this fix mutated raw_result directly and
    # silently broke the duplicate-groups chart as a result.
    display_result = raw_result
    if action == "find_duplicates" and raw_result.get("status") == "completed" and raw_result["output"].get("applicable"):
        display_result = {
            **raw_result,
            "routing": {"capability": capability, "tool": "Entity duplicate detection"},
            "output": dedup_adapter.sanitize_clusters_output_for_display(raw_result["output"]),
        }

    yield {"type": "tool_result", "data": display_result}

    if action == "compute_ifrs9" and ran_intent:
        out = raw_result["output"]
        yield {
            "type": "component",
            "component_type": "ifrs9_ecl",
            "component_data": {
                "total_ecl_sar": out.get("total_computed_ecl"),
                "stage_1_count": out.get("stage_1_count"),
                "stage_2_count": out.get("stage_2_count"),
                "stage_3_count": out.get("stage_3_count"),
                "top_5_risk": out.get("top_5_risk", []),
                "pd_lgd_ead": out.get("pd_lgd_ead", {}),
                "reporting_date": out.get("reporting_date"),
            },
        }
    elif action == "find_duplicates" and ran_intent and raw_result["output"].get("applicable"):
        # Deliberately a summary only, not full cluster/member detail --
        # that already has a full, signed-off interactive UI via the
        # "duplicate_review" event above. Duplicating the same detail
        # into a second, differently-shaped payload risks the two
        # drifting apart the way tool_result sanitization once did.
        out = raw_result["output"]
        yield {
            "type": "component",
            "component_type": "duplicate_clusters",
            "component_data": {
                "total_clusters": out.get("total_clusters"),
                "high_confidence_clusters": out.get("high_confidence_clusters"),
                "needs_review_clusters": out.get("needs_review_clusters"),
                "note": "Full per-cluster detail and Confirm/Reject actions are in the review cards above.",
            },
        }
    elif action in ("sama_compliance", "customer_360") and ran_intent:
        # compute_sama_compliance / compute_customer_360 already return
        # exactly the component_data shape -- built that way deliberately,
        # so no remapping needed here.
        yield {
            "type": "component",
            "component_type": action,
            "component_data": raw_result["output"],
        }

    if action == "assess_ndi" and ran_intent:
        # Independent of the run_ndi output above (which still reads
        # whatever's in the uploaded NDI sheet, unchanged) -- the radar
        # view uses Dr. Saber's real SDAIA methodology applied to his
        # fixed BAJ demo baseline, per his explicit instruction, not
        # data from the uploaded file.
        yield {
            "type": "component",
            "component_type": "ndi_assessment",
            "component_data": banking_adapter.compute_ndi_assessment(),
        }

    # After ANY completed action, offer the standing "what next" menu --
    # the other capabilities, not just (for IFRS 9) the other scenarios
    # of the same one. Without this, clicking IFRS 9 from the initial
    # upload chips led to a dead end: only "optimistic"/"adverse" showed
    # up, with no way back to NDI or duplicates short of re-uploading.
    if ran_intent:
        recommendations = []
        if action == "compute_ifrs9":
            other_scenarios = [s for s in banking_adapter.MACRO_SCENARIOS if s != scenario]
            scenario_labels = {"optimistic": "\U0001F4C8 Optimistic scenario", "base": "\U0001F4CA Base scenario", "adverse": "\U0001F4C9 Adverse scenario"}
            recommendations.extend([
                {
                    "action": "compute_ifrs9",
                    "scenario": s,
                    "label": scenario_labels.get(s, s.title()),
                    "description": banking_adapter.MACRO_SCENARIOS[s]["description"],
                }
                for s in other_scenarios
            ])
            recommendations.extend(dataset_adapter.get_followup_recommendations(dataset_name, raw_bytes, exclude_action="select_ifrs9_scenario"))
        else:
            recommendations.extend(dataset_adapter.get_followup_recommendations(dataset_name, raw_bytes, exclude_action=action))
        if recommendations:
            yield {
                "type": "recommendations",
                "dataset_name": dataset_name,
                "options": recommendations,
            }

    charts = suggest_visualization(ran_intent or intent, raw_result)
    if charts:
        yield {"type": "visualization", "charts": charts}

    yield {"type": "status", "stage": "explaining", "label": "Writing a plain-English summary..."}
    try:
        reply = explain_result(user_message, ran_intent or intent, display_result, has_visualization=bool(charts))
    except RuntimeError:
        raise

    chat_store.add_message(conv_id, "user", user_message)
    chat_store.add_message(conv_id, "assistant", reply)
    yield {"type": "final", "reply": reply, "conversation_id": conv_id, "ran_intent": ran_intent}


@app.post("/chat/stream")
async def chat_stream(
    message: str | None = Form(None),
    file: UploadFile | None = File(None),
    dataset_name: str | None = Form(None),
    conversation_id: int | None = Form(None),
    action: str | None = Form(None),
    scenario: str = Form("base"),
    user: dict = Depends(auth.get_current_user),
):
    conv_id = _resolve_conversation(conversation_id, user["id"])
    csv_content = None
    filename = None
    sheet_used = None
    raw_bytes = None

    if file is not None:
        raw_bytes = await file.read()
        filename = file.filename
        if not action or action == "add_dataset":
            try:
                csv_content, sheet_used = dataset_adapter.extract_csv_content(filename, raw_bytes)
            except ValueError as e:
                async def error_only():
                    yield _sse("error", {"detail": str(e)})
                return StreamingResponse(error_only(), media_type="text/event-stream")

    if action and action != "add_dataset":
        # A recommendation chip was clicked -- run just that one
        # follow-up analysis, not the full upload flow again.
        sync_gen = _run_recommended_action_events(action, dataset_name, raw_bytes, user["id"], conv_id, scenario)
    elif csv_content is not None:
        sync_gen = _dataset_upload_events(dataset_name, raw_bytes, csv_content, filename, sheet_used, user["id"], conv_id)
    else:
        if not message:
            async def error_only2():
                yield _sse("error", {"detail": "No message or file provided."})
            return StreamingResponse(error_only2(), media_type="text/event-stream")
        history = chat_store.get_history(conv_id)
        sync_gen = _text_chat_events(history, message, conv_id)

    async def event_generator():
        try:
            async for event in _run_in_thread(sync_gen):
                yield _sse(event["type"], event)
        except RuntimeError as e:
            yield _sse("error", {"detail": str(e)})
        except Exception as e:  # noqa: BLE001 -- last-resort guard so a bug never hangs the UI silently
            yield _sse("error", {"detail": f"Unexpected error: {e}"})

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# ---------------------------------------------------------------------
# Raw intent pipeline -- unchanged from Phase One, kept for direct/
# programmatic access alongside the chat interface.
# ---------------------------------------------------------------------

# ---------------------------------------------------------------------
# Duplicate review -- the human-in-the-loop actions the Tabulator review
# table in chat calls. As of Item 3, confirming a cluster here now
# EXECUTES a real merge immediately (see dedup_adapter.py's
# decide_cluster() / _execute_merge()) -- no longer just a tracked
# decision with merge deferred to later.
# ---------------------------------------------------------------------

class DuplicateDecisionRequest(BaseModel):
    cluster_id: int
    status: str  # "confirmed_duplicate" | "not_duplicate"


@app.post("/duplicates/decide")
def decide_duplicate(req: DuplicateDecisionRequest, user: dict = Depends(auth.get_current_user)):
    try:
        result = dedup_adapter.decide_cluster(req.cluster_id, req.status, decided_by=user["email"])
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result


@app.get("/duplicates/pending")
def list_pending_duplicates(dataset_name: str, user: dict = Depends(auth.get_current_user)):
    return {"dataset_name": dataset_name, "clusters": dedup_adapter.get_pending_clusters(dataset_name)}


@app.get("/duplicates/audit-log")
def duplicate_audit_log(dataset_name: str | None = None, user: dict = Depends(auth.get_current_user)):
    """The durable compliance record for duplicate-review decisions --
    who decided what, and when -- independent of any one chat session.
    This is what a bank reviewer pulls up later, not the chat transcript."""
    return {"entries": dedup_adapter.get_audit_log(dataset_name)}


# ---------------------------------------------------------------------
# MDM dashboard API (Item 3) -- Golden Record Registry + Duplicate
# Queue pages. Deliberately unauthenticated, same call as the Lakehouse
# endpoints (Item 2): read-only data with no secrets, and the write
# actions (uploading a dataset, deciding a cluster) take plain
# identifying strings rather than requiring the chat app's own login --
# this dashboard doesn't have its own auth system yet.
#
# Includes the dashboard's OWN upload + detect actions (mdm_upload_
# dataset, mdm_detect_duplicates) -- these reuse the exact same
# dataset_adapter/dedup_adapter functions /chat/upload and the chat
# "find duplicates" chip already call, so a dataset uploaded here is
# immediately usable from chat too, and vice versa. One shared data
# layer underneath, two genuinely independent front doors -- neither
# is required to bootstrap the other.
#
# bulk_confirm below carries over chat's own two smart-recommendation
# actions ("Confirm all high-confidence matches" / "Confirm all
# pending") -- same dedup_adapter.confirm_high_confidence() /
# confirm_all_pending() functions, exposed here so the dashboard isn't
# missing a capability chat already has.
#
# mdm_delete_dataset (2026-08-18) -- the counterpart write path this
# group never had: every other route above ADDS or reads data, none
# removed a dataset. Added specifically to retire "Banking_Demo" (see
# dataset_adapter.delete_dataset()'s own docstring for the full
# reasoning) but kept as a real, reusable admin capability, not a
# one-off script -- same unauthenticated pattern as the rest of this
# group, since this dashboard has no auth system of its own yet.
#
# mdm_field_lineage (2026-08-18) -- see app/field_lineage.py's own
# docstring for the full reasoning. Item 3's 3rd of 4 required pages,
# reopened after being marked done prematurely.
# ---------------------------------------------------------------------

class MdmDecisionRequest(BaseModel):
    cluster_id: int
    status: str  # "confirmed_duplicate" | "not_duplicate"
    decided_by: str


class MdmDetectRequest(BaseModel):
    dataset_name: str


class MdmBulkConfirmRequest(BaseModel):
    dataset_name: str | None = None
    tier: str = "all"  # "high_confidence" | "all"
    decided_by: str


@app.get("/api/mdm/datasets")
def mdm_datasets():
    """Every dataset currently in the system, regardless of which front
    door it arrived through (chat upload or dashboard upload) -- powers
    the dashboard's own dataset picker so a user never has to leave it
    to see what's available to run detection against."""
    return dataset_adapter.list_datasets({})


@app.delete("/api/mdm/datasets/{dataset_name}")
def mdm_delete_dataset(dataset_name: str):
    """Permanently removes a dataset -- see
    dataset_adapter.delete_dataset()'s own docstring for exactly what
    this deletes (the dataset row, every duplicate_clusters/
    golden_records row tied to it, every Bronze/Silver/Gold object in
    SeaweedFS) and what it deliberately leaves alone (Airflow's own
    run history, Marquez's lineage records -- a real record of past
    attempts, not something removal should rewrite)."""
    try:
        return dataset_adapter.delete_dataset(dataset_name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/api/mdm/upload-dataset")
async def mdm_upload_dataset(file: UploadFile = File(...), dataset_name: str = Form(...)):
    """The dashboard's own ingest entry point -- same Bronze -> Silver ->
    (Gold or held) pipeline /chat/upload calls, just triggered from the
    dashboard directly instead of a chat message. uploaded_by is 0
    (no dashboard login yet) rather than a real user id -- fine, SQLite
    doesn't enforce the FK to users here."""
    raw_bytes = await file.read()
    try:
        csv_content, sheet_used = dataset_adapter.extract_csv_content(file.filename, raw_bytes)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        bronze = dataset_adapter.land_bronze(dataset_name, csv_content)
        silver = dataset_adapter.clean_to_silver(bronze["safe_name"], bronze["df"])
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    result = {
        "dataset_name": bronze["safe_name"],
        "rows": len(silver["silver_df"]),
        "columns": list(silver["silver_df"].columns),
        "sheet_used": sheet_used,
    }

    # The two _upsert_dataset_record() calls below are the only DB writes
    # in this endpoint -- until 2026-08-17 they were NOT wrapped in a
    # try/except ValueError, unlike everything else in this file. That
    # gap is exactly why a genuine, now-correctly-raised Postgres error
    # (see db.py's _PgCursor.execute()) still came back as an opaque,
    # undiagnosable 500 with zero detail even after that fix landed --
    # confirmed directly by retesting against the live app. Wrapping it
    # here brings this in line with every other DB-writing site in this
    # file (auth.py's create_user, dedup_adapter's decide_cluster, etc.)
    # -- Bronze/Silver files are already on disk by this point regardless
    # of outcome, so this failure mode is specifically "the dataset
    # record itself couldn't be written," worth its own clear message.
    try:
        if silver["held"]:
            result["stage"] = "silver_held"
            result["hold_reason"] = silver["hold_reason"]
            dataset_adapter._upsert_dataset_record(
                bronze["safe_name"], bronze["display_name"], 0, "silver_held",
                result["rows"], result["columns"], [], silver["duplicate_rows_removed"],
                silver["null_counts"], bronze["bronze_path"], silver["silver_path"], None,
            )
        else:
            gold = dataset_adapter.promote_to_gold(bronze["safe_name"], silver["silver_df"])
            result["stage"] = "gold"
            result["dropped_columns"] = gold["dropped_columns"]
            dataset_adapter._upsert_dataset_record(
                bronze["safe_name"], bronze["display_name"], 0, "gold",
                result["rows"], result["columns"], gold["dropped_columns"],
                silver["duplicate_rows_removed"], silver["null_counts"],
                bronze["bronze_path"], silver["silver_path"], gold["gold_path"],
            )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return result


@app.post("/api/mdm/detect-duplicates")
def mdm_detect_duplicates(req: MdmDetectRequest):
    """The dashboard's own trigger for duplicate detection -- same
    find_duplicate_candidates() the chat "find duplicates" chip calls,
    reading the Silver data for a dataset already uploaded (via either
    front door)."""
    try:
        silver_csv = dataset_adapter.read_silver_csv(req.dataset_name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return dedup_adapter.find_duplicate_candidates({"csv_content": silver_csv, "dataset_name": req.dataset_name})


@app.get("/api/mdm/duplicate-queue")
def mdm_duplicate_queue(dataset_name: str | None = None):
    """Pending clusters awaiting a decision, plus the full decided
    history -- both halves of Section 04's "durable, sortable,
    searchable queue; Confirm/Reject retained here permanently" in one
    call. If no dataset_name is given, pending clusters are gathered
    across every dataset that has any (there's no dataset-agnostic
    "all pending" query in the adapter, since pending clusters are
    always scoped to one dataset at a time by detection). Each pending
    cluster is tagged with its own dataset_name here, since the
    underlying adapter's shape doesn't carry that -- the dashboard needs
    it to safely target a specific dataset for bulk-confirm when more
    than one dataset has pending clusters."""
    decided = dedup_adapter.get_audit_log(dataset_name)
    if dataset_name:
        pending = dedup_adapter.get_pending_clusters(dataset_name)
        for c in pending:
            c["dataset_name"] = dataset_name
    else:
        pending = []
        for d in dataset_adapter.list_datasets({})["datasets"]:
            for c in dedup_adapter.get_pending_clusters(d["dataset_name"]):
                c["dataset_name"] = d["dataset_name"]
                pending.append(c)
    return {"pending": pending, "decided": decided}


@app.post("/api/mdm/duplicate-queue/decide")
def mdm_decide(req: MdmDecisionRequest):
    try:
        return dedup_adapter.decide_cluster(req.cluster_id, req.status, decided_by=req.decided_by)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:  # noqa: BLE001 -- diagnostic widening (see module docstring's ERROR-HANDLING NOTE): a plain 500 here means something non-ValueError is being raised and swallowed by FastAPI's default handler with zero detail. Surface it the same way as the upload-dataset fix, instead of guessing again.
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")


@app.post("/api/mdm/duplicate-queue/bulk-confirm")
def mdm_bulk_confirm(req: MdmBulkConfirmRequest):
    """Carries over chat's two smart-recommendation bulk actions:
    tier="high_confidence" confirms every pending high-confidence
    cluster; tier="all" confirms every pending cluster regardless of
    tier. Each newly-confirmed cluster is merged immediately, same as
    a single decide -- see dedup_adapter._bulk_confirm()."""
    try:
        payload = {"dataset_name": req.dataset_name, "decided_by": req.decided_by}
        if req.tier == "high_confidence":
            return dedup_adapter.confirm_high_confidence(payload)
        return dedup_adapter.confirm_all_pending(payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:  # noqa: BLE001 -- same diagnostic widening as mdm_decide above.
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")


@app.get("/api/mdm/golden-records")
def mdm_golden_records(dataset_name: str | None = None):
    return {"golden_records": dedup_adapter.get_golden_records(dataset_name)}


@app.get("/api/mdm/golden-records/{golden_record_id}")
def mdm_golden_record_detail(golden_record_id: int):
    try:
        return dedup_adapter.get_golden_record_detail(golden_record_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/api/mdm/field-lineage")
def mdm_field_lineage(dataset_name: str):
    """Field-Level Lineage (Item 3's 3rd of 4 required MDM pages,
    reopened 2026-08-18 -- see app/field_lineage.py's own docstring for
    the full reasoning). Traces ECL_SAR, NATIONAL_ID, DUPLICATE_FLAG,
    NDI_SCORE through whichever real system actually produces each one
    for the given dataset -- deliberately dataset-scoped (unlike Item
    6's catalog/lineage, which is browsed across everything), since
    these are per-record fields on one specific dataset's data."""
    try:
        return field_lineage.get_field_lineage_for_dataset(dataset_name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


class StewardshipAssignRequest(BaseModel):
    dataset_name: str
    role: str
    assignee_name: str
    assignee_email: str | None = None
    assigned_by: str
    note: str | None = None


class StewardshipUnassignRequest(BaseModel):
    dataset_name: str
    role: str


@app.get("/api/mdm/stewardship")
def mdm_stewardship(dataset_name: str):
    """Data Stewardship (Item 3's 4th and last required MDM page,
    scoped and confirmed with Khurram 2026-08-19 -- see
    app/adapters/stewardship_adapter.py's own docstring). Real,
    human-entered role assignments for one dataset -- always returns
    all 5 roles, unassigned ones shown honestly as unassigned rather
    than omitted."""
    try:
        return stewardship_adapter.get_assignments(dataset_name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/api/mdm/stewardship/coverage")
def mdm_stewardship_coverage():
    """Across every dataset, how many of the 5 roles are assigned --
    a small at-a-glance summary computed from real assignment rows."""
    return stewardship_adapter.get_coverage_summary()


@app.post("/api/mdm/stewardship/assign")
def mdm_stewardship_assign(req: StewardshipAssignRequest):
    try:
        return stewardship_adapter.assign_role(req.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:  # noqa: BLE001 -- same diagnostic widening as the other MDM write routes above.
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")


@app.post("/api/mdm/stewardship/unassign")
def mdm_stewardship_unassign(req: StewardshipUnassignRequest):
    try:
        return stewardship_adapter.unassign_role(req.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:  # noqa: BLE001 -- same diagnostic widening as the other MDM write routes above.
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------------
# Governance dashboard API (Item 7 -- Classification & PDPL + Data
# Quality Rules, Section 04's 6th page group). Same unauthenticated,
# read-only pattern as every dashboard endpoint above.
#
# Classification & PDPL wraps app/adapters/classification_adapter.py --
# see that module's own docstring for the full OPA-deferral reasoning
# (real column classification now, policy enforcement deferred until
# Item 9/RBAC exists).
#
# Data Quality Rules wraps app/adapters/quality_adapter.py -- a real
# Great Expectations suite run against a dataset's actual Silver data,
# using the exact same null-rate thresholds dataset_adapter.py's own
# promotion gate uses (imported, not restated).
# ---------------------------------------------------------------------

@app.get("/api/governance/classification")
def governance_classification(dataset_name: str):
    """Per-column sensitivity classification (PUBLIC/INTERNAL/
    CONFIDENTIAL/RESTRICTED) + PDPL completeness detail for one
    dataset. See classification_adapter.py's module docstring for what
    this does and does not cover (classification is real; OPA policy
    enforcement is deferred and disclosed, not built)."""
    try:
        return classification_adapter.classify_dataset(dataset_name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/api/governance/classification/coverage")
def governance_classification_coverage():
    """Across every dataset, how many RESTRICTED/CONFIDENTIAL columns
    exist -- a small at-a-glance summary."""
    return classification_adapter.get_coverage_summary()


@app.get("/api/governance/quality-rules")
def governance_quality_rules(dataset_name: str):
    """Runs a real Great Expectations suite against one dataset's
    Silver data and returns pass/fail per rule. See
    quality_adapter.py's module docstring."""
    try:
        return quality_adapter.run_quality_rules(dataset_name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ---------------------------------------------------------------------
# Governance dashboard API (Item 4 -- SAMA Compliance Dashboard + Audit
# Log page). Same unauthenticated, read-only pattern as the Lakehouse
# and MDM dashboard endpoints above -- no new computation, both wrap
# logic that's already built and signed off:
#   - SAMA: banking_adapter.run_sama_compliance(), the exact function
#     the chat "sama_compliance" chip and the typed-chat
#     "assess_sama_compliance" intent already call. Its dataset
#     resolution (payload.get("dataset_name") -> single-dataset
#     auto-resolve -> honest "no dataset yet" state) is reused as-is,
#     so this page never needs a dataset picker for the common case of
#     one active dataset, and degrades honestly (not_measured, not a
#     fabricated 100%) when there's more than one or none at all.
#   - Audit Log: dedup_adapter.get_audit_log(None) -- the same durable
#     decided-cluster history already exposed (behind auth) at
#     /duplicates/audit-log for the chat app. This is a second,
#     unauthenticated read of the identical data for the dashboard's
#     own routed page, per Dev Queue item 4's "no new backend work,
#     presentation-only" framing -- the capability itself was already
#     built and closed 2026-08-06.
# ---------------------------------------------------------------------

@app.get("/api/governance/sama")
def governance_sama(dataset_name: str | None = None):
    try:
        return banking_adapter.run_sama_compliance({"dataset_name": dataset_name} if dataset_name else {})
    except ValueError as e:
        # Only real failure mode here: more than one dataset exists and
        # none was specified -- run_sama_compliance's own ambiguity
        # message, surfaced as a 400 instead of an opaque 500.
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/governance/audit-log")
def governance_audit_log(dataset_name: str | None = None):
    return {"entries": dedup_adapter.get_audit_log(dataset_name, limit=500)}


# ---------------------------------------------------------------------
# NDI dashboard API (Item 5 -- NDI Assessment Dashboard + History).
# Same unauthenticated, read-only pattern as everything above, with one
# write (record_snapshot).
#
# NO DATASET PICKER HERE, deliberately, and this is the one page where
# that's correct rather than an oversight: compute_ndi_assessment()
# takes no dataset at all -- it applies Dr. Saber's real SDAIA NDI v1.1
# methodology (14 domains, official weights, 6-level maturity scale) to
# his fixed BAJ demo baseline, per his explicit instruction for this
# component. A picker here would switch between options that produce
# byte-identical output. The 2026-08-18 standing rule is "wherever the
# page's data is scoped to a dataset"; this one's genuinely isn't.
#
# Scope: domain-level only, per Dr. Saber's 2026-08-11 answer (14-domain
# view + compliance %); the full 191-spec drill-down is deferred, not
# forgotten.
# ---------------------------------------------------------------------

class NdiSnapshotRequest(BaseModel):
    recorded_by: str
    note: str | None = None


@app.get("/api/governance/ndi")
def governance_ndi():
    """The current NDI assessment. Presentation-only in the same sense
    as the SAMA endpoint above: this wraps
    banking_adapter.compute_ndi_assessment(), the exact function the
    chat "assess_ndi" chip already renders as its ndi_assessment
    component, and the typed-chat "show_ndi_radar" intent already
    routes to. One implementation, three entrances, no drift."""
    return banking_adapter.compute_ndi_assessment()


@app.get("/api/governance/ndi/history")
def governance_ndi_history(limit: int = 100):
    """Every recorded assessment, newest first, with movement against
    the previous record. See app/adapters/ndi_history.py's module
    docstring for why the response also reports all_identical plainly
    instead of drawing a trend that isn't there yet."""
    return ndi_history.list_snapshots(limit=limit)


@app.get("/api/governance/ndi/history/{snapshot_id}")
def governance_ndi_snapshot(snapshot_id: int):
    """One recorded assessment in full, including the 14-domain
    breakdown exactly as stored at record time -- not recomputed on
    read, so an old record keeps showing what was actually assessed
    then even if the baseline or methodology later changes."""
    try:
        return ndi_history.get_snapshot(snapshot_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/api/governance/ndi/snapshot")
def governance_ndi_record_snapshot(req: NdiSnapshotRequest):
    """Records the current assessment as a dated, attributed audit
    record. recorded_by is required and never defaulted -- see
    ndi_history.record_snapshot()."""
    try:
        return ndi_history.record_snapshot(req.recorded_by, req.note)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:  # noqa: BLE001 -- same diagnostic widening as the MDM write routes above; a bare 500 on a DB write cost several rounds to diagnose during the Postgres migration.
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------------
# Catalog dashboard API (Item 6 -- Data Catalog + Field Lineage). Same
# unauthenticated, read-only pattern as every dashboard endpoint above.
# All three wrap app/marquez_client.py -- see that module's own
# docstring for what each reads and from where, and for why field
# lineage is assembled from job run facets directly rather than
# Marquez's own /datasets endpoint (confirmed empty in this setup).
# ---------------------------------------------------------------------

@app.get("/api/catalog/jobs")
def catalog_jobs():
    return marquez_client.list_jobs()


@app.get("/api/catalog/jobs/{job_name:path}/runs")
def catalog_job_runs(job_name: str, limit: int = 10):
    return marquez_client.get_job_runs(job_name, limit=limit)


@app.get("/api/catalog/lineage")
def catalog_lineage():
    return marquez_client.get_field_lineage()


@app.post("/intent")
def handle_intent(req: IntentRequest):
    decision = evaluate(req.intent, req.context)

    if not decision.allowed:
        return {
            "status": "blocked",
            "compliance": decision.to_dict(),
        }

    try:
        routed = route(req.intent, req.payload)
    except NoCapabilityRegisteredError as e:
        return {
            "status": "error",
            "compliance": decision.to_dict(),
            "error": str(e),
        }

    return {
        "status": "completed",
        "compliance": decision.to_dict(),
        "routing": {"capability": routed["capability"], "tool": routed["tool"]},
        "output": routed["result"],
    }


# CANARY (2026-08-17): tiny no-op comment to trigger a redeploy and
# confirm both DB (already proven) and dataset FILES (via the fixed
# presigned-URL write path) survive a real redeploy together.
