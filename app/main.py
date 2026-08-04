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

import json as json_lib

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
        csv_content = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(
            status_code=400,
            detail="Could not read the file as UTF-8 text. Please upload a plain CSV file.",
        )

    user_message = f'Uploaded a dataset file ({file.filename}) to add as "{dataset_name}".'

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
# Chat (streaming) -- what the UI actually calls. Handles both a plain
# text message and a file upload through the same endpoint (the "+"
# button posts a file; the composer posts just a message) since both
# need the same staged-progress treatment.
#
# NOTE: the generator below makes blocking calls (OpenRouter, pandas,
# sqlite) directly on the event loop rather than in a worker thread.
# Accepted tradeoff for demo-scale traffic -- worth moving to a thread
# if this needs to handle concurrent users for real.
# ---------------------------------------------------------------------

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

    if file is not None:
        raw_bytes = await file.read()
        filename = file.filename
        try:
            csv_content = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            async def error_only():
                yield _sse("error", {"detail": "Could not read the file as UTF-8 text. Please upload a plain CSV file."})
            return StreamingResponse(error_only(), media_type="text/event-stream")

    async def event_generator():
        try:
            if csv_content is not None:
                # --- Dataset upload: deterministic, staged, real progress ---
                user_message = f'Uploaded a dataset file ({filename}) to add as "{dataset_name}".'

                yield _sse("status", {"stage": "compliance", "label": "Checking compliance rules..."})
                decision = evaluate("add_dataset", {})

                if not decision.allowed:
                    raw_result = {"status": "blocked", "compliance": decision.to_dict()}
                    ran_intent = None
                else:
                    try:
                        yield _sse("status", {"stage": "bronze", "label": "Landing raw data into Bronze..."})
                        bronze = dataset_adapter.land_bronze(dataset_name, csv_content)

                        yield _sse("status", {"stage": "silver", "label": "Cleaning and deduplicating into Silver..."})
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
                            yield _sse("status", {"stage": "held", "label": "Data quality check: holding at Silver..."})
                            output["stage"] = "silver_held"
                            output["hold_reason"] = silver["hold_reason"]
                            output["numeric_summary"] = {}
                            output["top_categories"] = {}
                            output["dropped_columns"] = []
                            dataset_adapter._upsert_dataset_record(
                                bronze["safe_name"], bronze["display_name"], user["id"], "silver_held",
                                output["rows"], output["columns"], [], output["duplicate_rows_removed"],
                                output["null_counts"], bronze["bronze_path"], silver["silver_path"], None,
                            )
                        else:
                            yield _sse("status", {"stage": "gold", "label": "Promoting to Gold..."})
                            gold = dataset_adapter.promote_to_gold(bronze["safe_name"], silver["silver_df"])
                            output["stage"] = "gold"
                            output["numeric_summary"] = gold["numeric_summary"]
                            output["top_categories"] = gold["top_categories"]
                            output["dropped_columns"] = gold["dropped_columns"]
                            output["storage"]["gold"] = gold["gold_path"]
                            dataset_adapter._upsert_dataset_record(
                                bronze["safe_name"], bronze["display_name"], user["id"], "gold",
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

                yield _sse("tool_result", {"data": raw_result})
                yield _sse("status", {"stage": "explaining", "label": "Writing a plain-English summary..."})
                try:
                    reply = explain_result(user_message, "add_dataset", raw_result)
                except RuntimeError as e:
                    yield _sse("error", {"detail": str(e)})
                    return

                chat_store.add_message(conv_id, "user", user_message)

            else:
                # --- Plain text message: goes through the LLM interpreter ---
                if not message:
                    yield _sse("error", {"detail": "No message or file provided."})
                    return

                history = chat_store.get_history(conv_id)
                reply = None
                ran_intent = None
                try:
                    for event in interpret_stream(history, message):
                        if event["type"] == "final":
                            reply = event["reply"]
                            ran_intent = event["ran_intent"]
                        else:
                            yield _sse(event["type"], event)
                except RuntimeError as e:
                    yield _sse("error", {"detail": str(e)})
                    return

                chat_store.add_message(conv_id, "user", message)

            chat_store.add_message(conv_id, "assistant", reply)
            yield _sse("final", {"reply": reply, "conversation_id": conv_id, "ran_intent": ran_intent})

        except Exception as e:
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
