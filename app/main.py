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

import asyncio
import json as json_lib
import queue
import threading
import time

from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.compliance_agent import evaluate
from app.router import route, NoCapabilityRegisteredError
from app.capability_registry import CAPABILITY_REGISTRY
from app.db import init_db
from app import auth, chat_store
from app.adapters import dataset_adapter, banking_adapter, dedup_adapter
from app.visualization import suggest_visualization
from app.interpreter import interpret, interpret_stream, explain_result

app = FastAPI(title="DataOS 2.0 -- Pipeline Rails")

init_db()

app.mount("/static", StaticFiles(directory="app/static"), name="static")


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
# log in, talk to DataOS) has moved to "/app" -- linked from the
# landing page's "Sign up / Log in" button, top-right nav. Nothing
# about the chat UI itself changed; it's the same file, just served
# at a different route.
# ---------------------------------------------------------------------

@app.get("/")
def root():
    return FileResponse("app/static/landing.html")


@app.get("/app")
def chat_app():
    return FileResponse("app/static/index.html")


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
        scenario_labels = {"optimistic": "📈 Optimistic scenario", "base": "📊 Base scenario", "adverse": "📉 Adverse scenario"}
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
            scenario_labels = {"optimistic": "📈 Optimistic scenario", "base": "📊 Base scenario", "adverse": "📉 Adverse scenario"}
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
# table in chat calls. Recording a decision here does not merge or
# modify any record -- v1 tracks the decision only (see dedup_adapter.py
# module docstring for why merge execution is deliberately deferred).
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
