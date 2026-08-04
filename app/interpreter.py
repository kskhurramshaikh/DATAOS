# Intent Interpreter
#
# This is the new layer, sitting in front of the existing rails. It does
# not change or duplicate the compliance agent / router / adapters --
# it calls them exactly as the raw POST /intent path always has. Its
# only job is translation: natural language in, either a clarifying
# question back, or a call into the existing pipeline followed by a
# plain-English explanation of the result.
#
# Routed through OpenRouter (OpenAI-compatible API) rather than calling
# Anthropic directly, so this reuses the same OpenRouter account/billing
# already used elsewhere -- one API key to manage, not two.
#
# Model: anthropic/claude-sonnet-5 via OpenRouter. Swap to a cheaper
# model slug here if cost matters more than interpretation quality once
# this is past the demo stage.

import json
import os

from openai import OpenAI

from app.capability_registry import CAPABILITY_REGISTRY
from app.compliance_agent import evaluate
from app.router import route, NoCapabilityRegisteredError

MODEL = "anthropic/claude-sonnet-5"

_client = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            raise RuntimeError(
                "OPENROUTER_API_KEY is not set. The chat interface needs this to interpret "
                "natural-language messages."
            )
        _client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key)
    return _client


CALL_INTENT_TOOL = {
    "type": "function",
    "function": {
        "name": "call_intent",
        "description": (
            "Call this once you have enough information to run a registered DataOS intent on "
            "the user's behalf. Only call it for intents that are actually registered -- if the "
            "user asks for something that isn't registered yet, say so in plain text instead."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "intent": {
                    "type": "string",
                    "description": "The exact registered intent key to run.",
                    "enum": list(CAPABILITY_REGISTRY.keys()),
                },
                "context": {
                    "type": "object",
                    "description": (
                        "Governance context for the compliance check, e.g. "
                        "{\"dataset_classification\": \"PUBLIC\"}. Default to an empty object "
                        "if the user hasn't said anything about data classification."
                    ),
                },
                "payload": {
                    "type": "object",
                    "description": "The parameters the intent's adapter needs, gathered from the conversation.",
                },
            },
            "required": ["intent"],
        },
    },
}


def _system_prompt() -> str:
    registry_desc = json.dumps(CAPABILITY_REGISTRY, indent=2)
    return f"""You are the conversational front door for DataOS 2.0. Users are not technical --
never mention tool names, JSON, or internal architecture to them. They just describe what
they want in plain language.

Currently registered intents (this is the ONLY thing you can actually run):
{registry_desc}

For the "validate_drift" intent specifically, the payload the adapter accepts is:
- drift_feature (string, default "mean radius"): which feature to check for drift
- shift_multiplier (number, default 1.6): how much to scale the feature by, to simulate a shift
- shift_offset (number, default 3): a flat amount to add on top of the multiplier

validate_drift currently runs against a placeholder reference dataset (breast cancer
diagnostic data), not a real DataOS dataset yet -- if a user asks to check drift, you can
run it directly using sensible defaults unless they've specified particular values. It's
fine to just run it with defaults if the user doesn't care about specifics.

Rules:
1. If the user's request clearly maps to a registered intent and you have enough info
   (or reasonable defaults suffice), call the call_intent tool. Don't interrogate the user
   for parameters they haven't offered an opinion on -- use defaults.
2. If the user asks for something that maps to a capability that ISN'T registered yet
   (e.g. adding a dataset, training a model), say plainly that it's not built yet and
   briefly what's coming, without inventing a fake result.
3. If the user's message is genuinely ambiguous about WHICH registered intent they mean,
   ask ONE short clarifying question -- don't call the tool speculatively.
4. Keep replies short, warm, and non-technical. No JSON, no code blocks, no tool names.
"""


def _explain_result(user_message: str, intent: str, raw_result: dict) -> str:
    """Second short call: turn the raw pipeline JSON into a plain-English reply."""
    client = _get_client()
    prompt = f"""The user asked: "{user_message}"

You ran the "{intent}" capability on their behalf. Here is the raw result from the system:

{json.dumps(raw_result, indent=2)}

Explain this back to the user in 2-4 friendly, plain-English sentences. Mention the
concrete numbers that matter (e.g. how many metrics, whether drift was found, whether
it was blocked by a compliance rule). No JSON, no code, no tool/library names unless the
user already used that name themselves."""

    response = client.chat.completions.create(
        model=MODEL,
        max_tokens=400,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content.strip()


def interpret(conversation_history: list[dict], user_message: str) -> dict:
    """
    conversation_history: list of {"role": "user"|"assistant", "content": str}, oldest first.
    Returns: {"reply": str, "ran_intent": str | None}
    """
    client = _get_client()

    messages = (
        [{"role": "system", "content": _system_prompt()}]
        + conversation_history
        + [{"role": "user", "content": user_message}]
    )

    response = client.chat.completions.create(
        model=MODEL,
        max_tokens=600,
        tools=[CALL_INTENT_TOOL],
        messages=messages,
    )

    message = response.choices[0].message
    tool_calls = message.tool_calls or []

    if not tool_calls:
        # No tool call -- Claude is asking a clarifying question or explaining a gap.
        return {"reply": (message.content or "").strip(), "ran_intent": None}

    call = tool_calls[0]
    tool_input = json.loads(call.function.arguments)
    intent = tool_input.get("intent")
    context = tool_input.get("context") or {}
    payload = tool_input.get("payload") or {}

    decision = evaluate(intent, context)
    if not decision.allowed:
        raw_result = {"status": "blocked", "compliance": decision.to_dict()}
    else:
        try:
            routed = route(intent, payload)
            raw_result = {
                "status": "completed",
                "compliance": decision.to_dict(),
                "routing": {"capability": routed["capability"], "tool": routed["tool"]},
                "output": routed["result"],
            }
        except NoCapabilityRegisteredError as e:
            raw_result = {"status": "error", "compliance": decision.to_dict(), "error": str(e)}

    reply = _explain_result(user_message, intent, raw_result)
    return {"reply": reply, "ran_intent": intent}
