# DataOS 2.0 -- Pipeline Rails, Phase One Test
#
# This is the single intent-capture surface: the only thing any client
# (the DataOS 2.0 portal, eventually) ever talks to. Everything past this
# point -- compliance, routing, the tool doing the actual work -- is
# invisible on the other side of this one endpoint.

from fastapi import FastAPI
from pydantic import BaseModel

from app.compliance_agent import evaluate
from app.router import route, NoCapabilityRegisteredError

app = FastAPI(title="DataOS 2.0 -- Pipeline Rails (Phase One Test)")


class IntentRequest(BaseModel):
    intent: str
    context: dict = {}
    payload: dict = {}


@app.get("/health")
def health():
    return {"status": "ok"}


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
