# Evidently AI Adapter
#
# This is the only file in the codebase that knows Evidently AI exists.
# The router calls run(payload) and gets back a plain dict -- it never
# imports evidently itself, never sees Evidently's API shape, and would
# not need to change at all if Evidently were swapped for another
# drift-checking tool later. That's the invisibility contract every
# adapter in DataOS 2.0 has to hold.
#
# For this first rails test, the adapter uses a public reference dataset
# (sklearn's breast cancer dataset) rather than a live DataOS dataset --
# real dataset wiring comes once Bronze/Silver/Gold storage is connected.
# The drift itself is genuinely computed, not stubbed: one feature is
# artificially shifted and Evidently is asked to detect it for real,
# the same test proven during the tool's harness testing.

import pandas as pd
from sklearn.datasets import load_breast_cancer

from evidently import Report
from evidently.presets import DataDriftPreset


def run(payload: dict) -> dict:
    drift_feature = payload.get("drift_feature", "mean radius")
    shift_multiplier = payload.get("shift_multiplier", 1.6)
    shift_offset = payload.get("shift_offset", 3)

    data = load_breast_cancer(as_frame=True)
    df = data.frame
    reference = df.iloc[:300].copy()
    current = df.iloc[300:].copy()

    if drift_feature not in current.columns:
        raise ValueError(f"Unknown feature '{drift_feature}' for this dataset.")

    current[drift_feature] = current[drift_feature] * shift_multiplier + shift_offset

    report = Report(metrics=[DataDriftPreset()])
    snapshot = report.run(reference_data=reference, current_data=current)
    raw_result = snapshot.dict()

    metrics_summary = [
        {"metric_id": m.get("metric_id"), "value": m.get("value")}
        for m in raw_result.get("metrics", [])
    ]

    return {
        "dataset": "breast_cancer (sklearn reference dataset -- placeholder until real DataOS data is wired in)",
        "drift_feature_tested": drift_feature,
        "shift_applied": {"multiplier": shift_multiplier, "offset": shift_offset},
        "metric_count": len(metrics_summary),
        "metrics": metrics_summary,
    }
