# Tests for real user/role administration, wired 2026-08-19 -- see
# app/adapters/user_admin_adapter.py's module docstring for the full
# reasoning (closes the "no admin-promotion UI" gap the RBAC/OPA build
# left, without which the only way to promote a user off the
# self-signup default was Keycloak's own admin console). Admin-only,
# checked directly (not via opa_client), so these tests exercise the
# real 403/200 behavior of that direct role check, not a mocked
# policy decision.

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _signup(email: str, name: str = "Test User") -> str:
    r = client.post("/auth/signup", json={"name": name, "email": email, "password": "testpass123"})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def test_admin_list_users_requires_auth():
    r = client.get("/api/admin/users")
    assert r.status_code == 401


def test_admin_list_users_denies_non_admin(test_token_factory):
    _signup("nonadmin1@example.com")
    token = test_token_factory("nonadmin1@example.com", ["data_owner"])
    r = client.get("/api/admin/users", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403


def test_admin_list_users_allows_admin_and_includes_real_signed_up_user(test_token_factory):
    email = "listed-user@example.com"
    _signup(email, name="Listed User")
    admin_token = test_token_factory("admin1@example.com", ["admin"])
    r = client.get("/api/admin/users", headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code == 200
    emails = [u["email"] for u in r.json()["users"]]
    assert email in emails


def test_admin_list_roles_denies_non_admin(test_token_factory):
    token = test_token_factory("nonadmin2@example.com", ["data_steward"])
    r = client.get("/api/admin/roles", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403


def test_admin_list_roles_returns_managed_set(test_token_factory):
    admin_token = test_token_factory("admin2@example.com", ["admin"])
    r = client.get("/api/admin/roles", headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code == 200
    assert set(r.json()["roles"]) == {"admin", "business_owner", "data_owner", "data_steward", "data_custodian", "data_consumer"}


def test_admin_assign_role_requires_auth():
    r = client.post("/api/admin/users/some-id/roles", json={"role": "data_owner"})
    assert r.status_code == 401


def test_admin_assign_role_denies_non_admin(test_token_factory):
    token = test_token_factory("nonadmin3@example.com", ["data_owner"])
    r = client.post(
        "/api/admin/users/some-id/roles",
        headers={"Authorization": f"Bearer {token}"},
        json={"role": "admin"},
    )
    assert r.status_code == 403


def test_admin_can_promote_a_real_user_and_it_shows_in_the_listing(test_token_factory):
    # The actual real promotion flow, end to end: sign up a normal
    # user (lands as data_consumer by default -- see
    # auth.create_user()), have an admin grant them data_owner via
    # this endpoint, confirm the listing reflects both roles.
    email = "promote-me@example.com"
    _signup(email, name="Promote Me")
    admin_token = test_token_factory("admin3@example.com", ["admin"])

    users_before = client.get("/api/admin/users", headers={"Authorization": f"Bearer {admin_token}"}).json()["users"]
    target = next(u for u in users_before if u["email"] == email)
    assert target["roles"] == ["data_consumer"]

    r = client.post(
        f"/api/admin/users/{target['id']}/roles",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"role": "data_owner"},
    )
    assert r.status_code == 200
    updated = next(u for u in r.json()["users"] if u["email"] == email)
    assert "data_owner" in updated["roles"]
    assert "data_consumer" in updated["roles"]


def test_admin_rejects_unmanaged_role_name(test_token_factory):
    email = "reject-role@example.com"
    _signup(email)
    admin_token = test_token_factory("admin4@example.com", ["admin"])
    users = client.get("/api/admin/users", headers={"Authorization": f"Bearer {admin_token}"}).json()["users"]
    target = next(u for u in users if u["email"] == email)

    r = client.post(
        f"/api/admin/users/{target['id']}/roles",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"role": "offline_access"},  # a real Keycloak plumbing role, not a managed one
    )
    assert r.status_code == 400


def test_admin_can_revoke_a_role(test_token_factory):
    email = "revoke-me@example.com"
    _signup(email)
    admin_token = test_token_factory("admin5@example.com", ["admin"])
    users = client.get("/api/admin/users", headers={"Authorization": f"Bearer {admin_token}"}).json()["users"]
    target = next(u for u in users if u["email"] == email)

    client.post(
        f"/api/admin/users/{target['id']}/roles",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"role": "data_steward"},
    )
    r = client.delete(
        f"/api/admin/users/{target['id']}/roles/data_steward",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 200
    updated = next(u for u in r.json()["users"] if u["email"] == email)
    assert "data_steward" not in updated["roles"]
