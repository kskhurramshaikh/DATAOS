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
    "confirm_high_confidence_duplicates": {
        "capability": "bulk_confirm_duplicate_clusters",
        "tool": "rapidfuzz_dob_clustering",
        "adapter": "app.adapters.dedup_adapter.confirm_high_confidence",
    },
    "confirm_all_duplicates": {
        "capability": "bulk_confirm_duplicate_clusters",
        "tool": "rapidfuzz_dob_clustering",
        "adapter": "app.adapters.dedup_adapter.confirm_all_pending",
    },
    # -----------------------------------------------------------------
    # Inline demo components on the typed-chat path -- the fix for Dr.
    # Saber's Finding #1 (2026-08-09). These three views were only
    # reachable via the recommendation chips in main.py before; the
    # natural-language interpreter had no registered intent for any of
    # them, so a user typing "Show SAMA compliance" got a denial that a
    # shipped feature existed. Intent keys match the chip path's action
    # naming where one exists (main.py's intent_map).
    # -----------------------------------------------------------------
    "assess_sama_compliance": {
        "capability": "assess_sama_compliance_status",
        "tool": "sama_compliance_scorer",
        "adapter": "app.adapters.banking_adapter.run_sama_compliance",
    },
    "assess_customer_360": {
        "capability": "assess_customer_data_quality",
        "tool": "customer_360_analyzer",
        "adapter": "app.adapters.banking_adapter.run_customer_360",
    },
    "show_ndi_radar": {
        "capability": "assess_data_governance_readiness",
        "tool": "ndi_scorecard_engine",
        "adapter": "app.adapters.banking_adapter.run_ndi_radar",
    },
}
