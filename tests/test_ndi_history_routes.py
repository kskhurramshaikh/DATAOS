# HTTP-level tests for the NDI History period-comparison and CSV
# export routes, wired 2026-08-19. The main thing these prove that
# tests/test_ndi_history.py's direct-function tests can't: real route
# resolution through FastAPI/Starlette. /api/governance/ndi/history/
# export and /api/governance/ndi/history/{snapshot_id} share the same
# path prefix -- registering /export AFTER the {snapshot_id} route
# would make "export" get swallowed by int-conversion and 422 instead
# of ever reaching the export handler. This test would have caught
# that class of bug; a direct-function test never would, since it
# doesn't go through routing at all.

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _record(recorded_by: str, note: str | None = None) -> dict:
    r = client.post("/api/governance/ndi/snapshot", json={"recorded_by": recorded_by, "note": note})
    assert r.status_code == 200, r.text
    return r.json()


def test_export_route_does_not_collide_with_snapshot_id_route():
    _record("route-test@example.com")
    r = client.get("/api/governance/ndi/history/export")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment; filename=ndi_history.csv" in r.headers["content-disposition"]
    assert "id,recorded_at,recorded_by" in r.text


def test_snapshot_export_route():
    snap = _record("snapshot-export@example.com", note="route test")
    r = client.get(f"/api/governance/ndi/history/{snap['id']}/export")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert f"filename=ndi_snapshot_{snap['id']}.csv" in r.headers["content-disposition"]
    assert f"NDI assessment #{snap['id']}" in r.text


def test_snapshot_export_404_for_unknown_id():
    r = client.get("/api/governance/ndi/history/999999999/export")
    assert r.status_code == 404


def test_compare_route():
    a = _record("compare-a@example.com")
    b = _record("compare-b@example.com")
    r = client.get(f"/api/governance/ndi/compare?a={a['id']}&b={b['id']}")
    assert r.status_code == 200
    data = r.json()
    assert data["from"]["id"] == a["id"]
    assert data["to"]["id"] == b["id"]
    assert len(data["domains"]) == 14


def test_compare_route_rejects_same_id():
    a = _record("compare-same@example.com")
    r = client.get(f"/api/governance/ndi/compare?a={a['id']}&b={a['id']}")
    assert r.status_code == 400
