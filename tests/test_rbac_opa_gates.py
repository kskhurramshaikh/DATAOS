# Tests for the two real RBAC/OPA policy points wired 2026-08-19 --
# see app/opa_client.py's module docstring for why these exist and
# what they replace (a previously-disclosed, honest deferral, now
# closed because Keycloak + OPA are both genuinely live). The fake_opa
# fixture in conftest.py mirrors the actual deployed .rego policy
# (classification_allow: admin/data_owner/data_steward;
# stewardship_assign_allow: admin/data_owner) rather than a generic
# allow-everything stub, so these tests exercise the same role logic
# that's live in production. test_token_factory (also from conftest.py)
# is used instead of importing make_test_token directly -- a direct
# `from tests.conftest import ...` can resolve to a second, separately
# -generated copy of that module's RSA key, breaking signature
# verification silently (confirmed live while writing this file).

import io

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

CSV = (
    "id,name,quantity\n"
    "1,Widget,3\n"
    "2,Gadget,5\n"
)


def _upload_dataset(name: str) -> str:
    r = client.post(
        "/api/mdm/upload-dataset",
        files={"file": ("t.csv", io.BytesIO(CSV.encode()), "text/csv")},
        data={"dataset_name": name},
    )
    assert r.status_code == 200, r.text
    return r.json()["dataset_name"]


def test_classification_requires_auth():
    r = client.get("/api/governance/classification?dataset_name=doesnotmatter")
    assert r.status_code == 401


def test_classification_denies_role_without_opa_grant(test_token_factory):
    ds = _upload_dataset("classif-deny")
    token = test_token_factory("consumer@example.com", ["data_consumer"])
    r = client.get(
        f"/api/governance/classification?dataset_name={ds}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 403


def test_classification_allows_data_steward(test_token_factory):
    ds = _upload_dataset("classif-allow")
    token = test_token_factory("steward@example.com", ["data_steward"])
    r = client.get(
        f"/api/governance/classification?dataset_name={ds}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    assert "columns" in r.json()


def test_classification_allows_admin_regardless_of_other_roles(test_token_factory):
    ds = _upload_dataset("classif-admin")
    token = test_token_factory("admin@example.com", ["admin"])
    r = client.get(
        f"/api/governance/classification?dataset_name={ds}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200


def test_stewardship_assign_requires_auth():
    r = client.post(
        "/api/mdm/stewardship/assign",
        json={
            "dataset_name": "doesnotmatter",
            "role": "data_owner",
            "assignee_name": "Someone",
            "assigned_by": "tester",
        },
    )
    assert r.status_code == 401


def test_stewardship_assign_denies_data_consumer(test_token_factory):
    ds = _upload_dataset("steward-deny")
    token = test_token_factory("consumer2@example.com", ["data_consumer"])
    r = client.post(
        "/api/mdm/stewardship/assign",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "dataset_name": ds,
            "role": "data_owner",
            "assignee_name": "Someone",
            "assigned_by": "consumer2@example.com",
        },
    )
    assert r.status_code == 403


def test_stewardship_assign_allows_data_owner(test_token_factory):
    ds = _upload_dataset("steward-allow")
    token = test_token_factory("owner@example.com", ["data_owner"])
    r = client.post(
        "/api/mdm/stewardship/assign",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "dataset_name": ds,
            "role": "data_steward",
            "assignee_name": "Real Steward",
            "assigned_by": "owner@example.com",
        },
    )
    assert r.status_code == 200
    assert r.json()["roles_assigned"] == 1


def test_stewardship_unassign_denies_data_steward_role_itself(test_token_factory):
    # data_steward is not in stewardship_assign_allow (only
    # admin/data_owner are) -- confirms the gate checks the REAL
    # policy, not just "any recognized stewardship role."
    ds = _upload_dataset("steward-unassign-deny")
    token = test_token_factory("steward2@example.com", ["data_steward"])
    r = client.post(
        "/api/mdm/stewardship/unassign",
        headers={"Authorization": f"Bearer {token}"},
        json={"dataset_name": ds, "role": "data_owner"},
    )
    assert r.status_code == 403
