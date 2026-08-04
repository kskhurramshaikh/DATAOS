# DataOS 2.0 -- Pipeline Rails + Conversational Interface
#
# The raw intent pipeline (compliance -> router -> adapter) is unchanged
# from Phase One -- POST /intent still exists exactly as before, for
# direct/programmatic use. What's new is the layer in front of it: sign
# up, log in, and talk to DataOS in plain English at "/". The chat
# endpoint calls the exact same pipeline internally; nothing about the
# governed rails changes underneath it.

from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.compliance_agent import evaluate
from app.router import route, NoCapabilityRegisteredError
from app.capability_registry import CAPABILITY_REGISTRY
from app.db import init_db
from app import auth, chat_store
from app.interpreter import interpret, explain_result

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
# Chat -- natural language front door onto the existing pipeline
# ---------------------------------------------------------------------

@app.post("/chat")
def chat(req: ChatRequest, user: dict = Depends(auth.get_current_user)):
    if req.conversation_id is None:
        conversation_id = chat_store.create_conversation(user["id"])
    else:
        owner_id = chat_store.get_conversation_owner(req.conversation_id)
        if owner_id != user["id"]:
            conversation_id = chat_store.create_conversation(user["id"])
        else:
            conversation_id = req.conversation_id

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


# ---------------------------------------------------------------------
# Dataset upload -- the "+" button's "Add dataset" action. This bypasses
# the LLM's tool-selection step on purpose: attaching a file through a
# dedicated UI action is already an unambiguous, deterministic intent,
# so there's nothing for the interpreter to disambiguate. It still goes
# through the same compliance check as everything else, and reuses the
# same explain_result step to reply in plain English.
# ---------------------------------------------------------------------

@app.post("/chat/upload")
async def chat_upload(
    file: UploadFile = File(...),
    dataset_name: str = Form(...),
    conversation_id: int | None = Form(None),
    user: dict = Depends(auth.get_current_user),
):
    if conversation_id is None:
        conversation_id = chat_store.create_conversation(user["id"])
    else:
        owner_id = chat_store.get_conversation_owner(conversation_id)
        if owner_id != user["id"]:
            conversation_id = chat_store.create_conversation(user["id"])

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
            routed = route("add_dataset", {"dataset_name": dataset_name, "csv_content": csv_content})
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
