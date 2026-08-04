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
    "assess_ndi_readiness": {
        "capability": "assess_data_governance_readiness",
        "tool": "ndi_scorecard_engine",
        "adapter": "app.adapters.banking_adapter.run_ndi",
    },
    "compute_ifrs9_ecl": {
        "capability": "compute_expected_credit_loss",
        "tool": "ifrs9_ecl_engine",
        "adapter": "app.adapters.banking_adapter.run_ifrs9",
    },
    "find_duplicate_candidates": {
        "capability": "detect_entity_duplicates",
        "tool": "rapidfuzz_dob_clustering",
        "adapter": "app.adapters.dedup_adapter.find_duplicate_candidates",
    },
}
