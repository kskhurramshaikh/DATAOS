# Intent Interpreter
#
# This is the new layer, sitting in front of the existing rails. It does
# not change or duplicate the compliance agent / router / adapters --
# it calls them exactly as the raw POST /intent path always has. Its
# only job is translation: natural language in, either a clarifying
# question back, or a call into the existing pipeline followed by a
# plain-English explanation of the result.
#
# interpret_stream() is the real implementation -- a generator that
# yields status/tool_call/tool_result events as each step of real work
# actually starts, so the chat UI can show live progress instead of a
# single spinner. interpret() is a thin wrapper around it for callers
# (tests, programmatic use) that just want the end result.
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
run it directly using sensible defaults unless they've specified particular values.

The "add_dataset" intent requires an actual file, uploaded through the "+" button next to
the chat input -- it is never something you can call yourself from a text-only message,
because there's no file content to work with. If a user asks about adding, uploading, or
registering a dataset without having attached one, tell them (briefly, warmly) to use the
"+" button next to the message box and choose "Add dataset" -- don't call the tool and
don't pretend to have run it.

When a dataset is added, it goes through Bronze (raw landing), Silver (duplicates removed,
missing values reported honestly), then Gold (curated for business use -- any column that's
mostly empty gets dropped rather than kept unreliable) -- UNLESS a column in Silver is more
than 10% null, in which case the dataset is deliberately HELD at Silver rather than
auto-promoted, pending a decision. If you see stage "silver_held" in a result, explain
clearly why it's being held and mention the "promote_dataset" intent as the way to push it
through anyway if the user wants to.

The "list_datasets" intent (payload: optional dataset_name) shows what's been added and its
current stage -- use it when a user asks what data exists, or the status of a specific one.

The "promote_dataset" intent (payload: dataset_name, required) forces a dataset held at
Silver through to Gold. Only relevant for datasets already flagged "silver_held".

Rules:
1. If the user's request clearly maps to a registered intent and you have enough info
   (or reasonable defaults suffice), call the call_intent tool. Don't interrogate the user
   for parameters they haven't offered an opinion on -- use defaults.
2. If the user asks for something that maps to a capability that ISN'T registered yet,
   say plainly that it's not built yet and briefly what's coming, without inventing a
   fake result.
3. If the user's message is genuinely ambiguous about WHICH registered intent they mean,
   ask ONE short clarifying question -- don't call the tool speculatively.
4. Keep replies short, warm, and non-technical. No JSON, no code blocks, no tool names.
"""


def explain_result(user_message: str, intent: str, raw_result: dict) -> str:
    """
    Turn a raw pipeline result into a plain-English reply. Public because
    deterministic UI actions (like a file upload through the "+" button)
    call the pipeline directly, bypassing tool-selection, but still want
    the same natural-language explanation step this module provides.
    """
    client = _get_client()
    prompt = f"""The user asked: "{user_message}"

You ran the "{intent}" capability on their behalf. Here is the raw result from the system:

{json.dumps(raw_result, indent=2)}

Explain this back to the user in 2-4 friendly, plain-English sentences. Mention the
concrete numbers that matter. If stage is "silver_held", clearly explain why it's being
held and that they can ask to promote it anyway. No JSON, no code, no tool/library names
unless the user already used that name themselves."""

    response = client.chat.completions.create(
        model=MODEL,
        max_tokens=400,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content.strip()


def interpret_stream(conversation_history: list[dict], user_message: str):
    """
    Generator: yields dicts describing each real step as it starts --
    {"type": "status", "stage": ..., "label": ...}
    {"type": "tool_call", "name": ..., "input": ...}
    {"type": "tool_result", "data": ...}
    -- and finally exactly one:
    {"type": "final", "reply": ..., "ran_intent": ...}

    conversation_history: list of {"role": "user"|"assistant", "content": str}, oldest first.
    """
    client = _get_client()

    messages = (
        [{"role": "system", "content": _system_prompt()}]
        + conversation_history
        + [{"role": "user", "content": user_message}]
    )

    yield {"type": "status", "stage": "interpreting", "label": "Understanding your request..."}

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
        reply = (message.content or "").strip()
        yield {"type": "final", "reply": reply, "ran_intent": None}
        return

    call = tool_calls[0]
    tool_input = json.loads(call.function.arguments)
    intent = tool_input.get("intent")
    context = tool_input.get("context") or {}
    payload = tool_input.get("payload") or {}

    yield {"type": "tool_call", "name": intent, "input": {"context": context, "payload": payload}}

    yield {"type": "status", "stage": "compliance", "label": "Checking compliance rules..."}
    decision = evaluate(intent, context)

    if not decision.allowed:
        raw_result = {"status": "blocked", "compliance": decision.to_dict()}
    else:
        yield {"type": "status", "stage": "routing", "label": f"Running {intent}..."}
        try:
            routed = route(intent, payload)
            raw_result = {
                "status": "completed",
                "compliance": decision.to_dict(),
                "routing": {"capability": routed["capability"], "tool": routed["tool"]},
                "output": routed["result"],
            }
        except (NoCapabilityRegisteredError, ValueError) as e:
            raw_result = {"status": "error", "compliance": decision.to_dict(), "error": str(e)}

    yield {"type": "tool_result", "data": raw_result}

    yield {"type": "status", "stage": "explaining", "label": "Writing a plain-English summary..."}
    reply = explain_result(user_message, intent, raw_result)

    yield {"type": "final", "reply": reply, "ran_intent": intent}


def interpret(conversation_history: list[dict], user_message: str) -> dict:
    """Non-streaming convenience wrapper around interpret_stream(), for
    callers (tests, programmatic use) that just want the end result."""
    final = {"reply": "", "ran_intent": None}
    for event in interpret_stream(conversation_history, user_message):
        if event["type"] == "final":
            final = {"reply": event["reply"], "ran_intent": event["ran_intent"]}
    return final
