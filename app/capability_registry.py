# Capability Registry
#
# This is the register the smart router reads from. Each key is an intent
# (from the 32 intents in scope, DataOS 2.0 Capability Corpus & Tool
# Inventory doc, Section 5). Each entry names the capability it maps to,
# which tool delivers it, and which adapter function performs the actual
# work.
#
# Starting deliberately small: one intent, one capability, one tool. Every
# future intent gets added here the same way, one at a time, once its own
# adapter is built and tested -- not before.

CAPABILITY_REGISTRY = {
    "validate_drift": {
        "capability": "compute_data_model_drift",
        "tool": "evidently_ai",
        "adapter": "app.adapters.evidently_adapter.run",
    },
}
