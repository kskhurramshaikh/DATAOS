from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_unregistered_intent_returns_clean_error():
    r = client.post("/intent", json={"intent": "does_not_exist_yet", "context": {}})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "error"
    assert "No capability is registered" in body["error"]


def test_validate_drift_blocked_when_restricted_without_override():
    r = client.post(
        "/intent",
        json={
            "intent": "validate_drift",
            "context": {"dataset_classification": "RESTRICTED"},
            "payload": {},
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "blocked"
    assert body["compliance"]["allowed"] is False
    assert "restricted-data-requires-override" in body["compliance"]["applied_rules"]


def test_validate_drift_allowed_and_detects_real_drift():
    r = client.post(
        "/intent",
        json={
            "intent": "validate_drift",
            "context": {"dataset_classification": "INTERNAL"},
            "payload": {
                "drift_feature": "mean radius",
                "shift_multiplier": 1.6,
                "shift_offset": 3,
            },
        },
    )
    assert r.status_code == 200
    body = r.json()

    assert body["status"] == "completed"
    assert body["compliance"]["allowed"] is True
    assert "validate-drift-requires-audit-log" in body["compliance"]["applied_rules"]

    assert body["routing"]["capability"] == "compute_data_model_drift"
    assert body["routing"]["tool"] == "evidently_ai"

    output = body["output"]
    assert output["drift_feature_tested"] == "mean radius"
    assert output["metric_count"] > 0

    # Confirm real drift was actually computed, not stubbed: the shifted
    # feature should show up with a low p-value among the metrics.
    values = [m["value"] for m in output["metrics"] if isinstance(m["value"], float)]
    assert any(v < 0.001 for v in values), "expected at least one strongly-drifted metric"
