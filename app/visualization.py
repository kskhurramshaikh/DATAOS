# Visualization suggestion -- deciding WHEN and WHAT to chart
#
# The plain-English reply always exists and always stands on its own --
# this module never replaces it, only adds to it. It looks at a
# capability's raw result and decides whether there's genuinely
# chart-worthy structured data in it (a ranked list, a categorical
# breakdown) -- not every result has this, and nothing here forces a
# chart where the data doesn't call for one.
#
# Returns a list of chart specs the frontend renders with Chart.js, or
# None if there's nothing worth charting. Each spec is intentionally
# simple (type/title/labels/values) -- the frontend decides how to draw
# it, this module only decides whether a chart earns its place.

def suggest_visualization(intent: str, raw_result: dict) -> list[dict] | None:
    if not isinstance(raw_result, dict):
        return None

    charts: list[dict] = []
    output = raw_result.get("output") if isinstance(raw_result.get("output"), dict) else {}

    if intent == "validate_drift" and output.get("metrics"):
        metrics = [m for m in output["metrics"] if isinstance(m.get("value"), (int, float))]
        top = sorted(metrics, key=lambda m: m["value"])[:5]
        if top:
            charts.append({
                "type": "bar",
                "title": "Strongest drift signals (lowest p-values)",
                "labels": [str(m.get("metric_id") or f"metric {i}") for i, m in enumerate(top)],
                "values": [m["value"] for m in top],
            })

    if intent == "add_dataset" and output.get("top_categories"):
        top_categories = output["top_categories"]
        first_col = next(iter(top_categories), None)
        if first_col:
            counts = top_categories[first_col]
            charts.append({
                "type": "bar",
                "title": f"Top values in '{first_col}'",
                "labels": list(counts.keys()),
                "values": list(counts.values()),
            })

    extra = raw_result.get("additional_analyses")
    if isinstance(extra, dict):
        ndi = extra.get("ndi_readiness")
        if isinstance(ndi, dict) and ndi.get("top_gap_domains"):
            charts.append({
                "type": "bar",
                "title": "NDI domain gaps (largest first)",
                "labels": [d["domain"] for d in ndi["top_gap_domains"]],
                "values": [d["gap"] for d in ndi["top_gap_domains"]],
            })

        ifrs9 = extra.get("ifrs9_ecl")
        if isinstance(ifrs9, dict) and ifrs9.get("loans_by_stage"):
            stages = ifrs9["loans_by_stage"]
            charts.append({
                "type": "bar",
                "title": "Loans by IFRS 9 stage",
                "labels": [f"Stage {k}" for k in stages.keys()],
                "values": list(stages.values()),
            })

    return charts or None
