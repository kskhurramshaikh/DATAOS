# Capability Registry
#
# This is the register the smart router reads from. Each key is an intent
# (from the 32 intents in scope, DataOS 2.0 Capability Corpus & Tool
# Inventory doc, Section 5). Each entry names the capability it maps to,
# which tool delivers it, and which adapter function performs the actual
# work.

CAPABILITY_REGISTRY = {
    "validate_drift": {
        "capability": "compute_data_model_drift",
        "tool": "evidently_ai",
        "adapter": "app.adapters.evidently_adapter.run",
    },
    "add_dataset": {
        "capability": "ingest_dataset_to_medallion",
        "tool": "pandas_medallion_pipeline",
        "adapter": "app.adapters.dataset_adapter.run",
    },
    "list_datasets": {
        "capability": "list_registered_datasets",
        "tool": "dataset_metadata_store",
        "adapter": "app.adapters.dataset_adapter.list_datasets",
    },
    "promote_dataset": {
        "capability": "promote_dataset_to_gold",
        "tool": "pandas_medallion_pipeline",
        "adapter": "app.adapters.dataset_adapter.promote_dataset",
    },
}
