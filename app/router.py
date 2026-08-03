# Smart Agentic Router
#
# Takes an intent that has already cleared the compliance agent, looks it
# up in the capability registry, and dispatches to the adapter that
# actually performs the work. The router never talks to a tool directly
# -- it only ever calls an adapter, which is what keeps every underlying
# tool invisible to the end user (DataOS 2.0 doc, Section 1 & Section 2
# "White-label presentation").

import importlib

from app.capability_registry import CAPABILITY_REGISTRY


class NoCapabilityRegisteredError(Exception):
    pass


def route(intent: str, payload: dict) -> dict:
    entry = CAPABILITY_REGISTRY.get(intent)
    if entry is None:
        raise NoCapabilityRegisteredError(
            f"No capability is registered for intent '{intent}'. "
            f"Currently registered: {list(CAPABILITY_REGISTRY.keys())}"
        )

    module_path, func_name = entry["adapter"].rsplit(".", 1)
    module = importlib.import_module(module_path)
    adapter_func = getattr(module, func_name)

    result = adapter_func(payload)

    return {
        "capability": entry["capability"],
        "tool": entry["tool"],
        "result": result,
    }
