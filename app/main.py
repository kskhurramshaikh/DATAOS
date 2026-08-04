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

from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.compliance_agent import evaluate
from app.router import route, NoCapabilityRegisteredError
from app.capability_registry import CAPABILITY_REGISTRY
from app.db import init_db
from app import auth, chat_store
from app.adapters import dataset_adapter
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
# Landing page -- the chat UI is the actual front door now.
# ---------------------------------------------------------------------

@app.get("/")
def root():
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


def _dataset_upload_events(dataset_name: str, csv_content: str, filename: str, sheet_used: str | None, user_id: int, conv_id: int):
    sheet_note = f' (sheet: "{sheet_used}")' if sheet_used else ""
    user_message = f'Uploaded a dataset file ({filename}{sheet_note}) to add as "{dataset_name}".'

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
    yield {"type": "status", "stage": "explaining", "label": "Writing a plain-English summary..."}
    reply = explain_result(user_message, "add_dataset", raw_result)  # may raise RuntimeError -- caught by the caller

    chat_store.add_message(conv_id, "user", user_message)
    chat_store.add_message(conv_id, "assistant", reply)
    yield {"type": "final", "reply": reply, "conversation_id": conv_id, "ran_intent": ran_intent}


@app.post("/chat/stream")
async def chat_stream(
    message: str | None = Form(None),
    file: UploadFile | None = File(None),
    dataset_name: str | None = Form(None),
    conversation_id: int | None = Form(None),
    user: dict = Depends(auth.get_current_user),
):
    conv_id = _resolve_conversation(conversation_id, user["id"])
    csv_content = None
    filename = None
    sheet_used = None

    if file is not None:
        raw_bytes = await file.read()
        filename = file.filename
        try:
            csv_content, sheet_used = dataset_adapter.extract_csv_content(filename, raw_bytes)
        except ValueError as e:
            async def error_only():
                yield _sse("error", {"detail": str(e)})
            return StreamingResponse(error_only(), media_type="text/event-stream")

    if csv_content is not None:
        sync_gen = _dataset_upload_events(dataset_name, csv_content, filename, sheet_used, user["id"], conv_id)
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
