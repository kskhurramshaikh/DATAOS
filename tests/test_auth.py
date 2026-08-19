# Auth now calls out to real Keycloak (see app/auth.py's module
# docstring). The fake Keycloak transport (real logic, mocked network
# hop) lives in tests/conftest.py's autouse fake_keycloak fixture so
# it applies repo-wide, not just here -- see that file's docstring.

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.main import app
from app import auth

client = TestClient(app)


def test_signup_then_login():
    r = client.post(
        "/auth/signup",
        json={"name": "Ada Lovelace", "email": "ada@example.com", "password": "secret123"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user"]["email"] == "ada@example.com"
    assert body["token"]

    r = client.post(
        "/auth/login", json={"email": "ada@example.com", "password": "secret123"}
    )
    assert r.status_code == 200
    assert r.json()["token"]


def test_duplicate_signup_rejected():
    client.post(
        "/auth/signup",
        json={"name": "Grace Hopper", "email": "grace@example.com", "password": "secret123"},
    )
    r = client.post(
        "/auth/signup",
        json={"name": "Grace Hopper", "email": "grace@example.com", "password": "secret123"},
    )
    assert r.status_code == 409


def test_wrong_password_rejected():
    client.post(
        "/auth/signup",
        json={"name": "Alan Turing", "email": "alan@example.com", "password": "secret123"},
    )
    r = client.post(
        "/auth/login", json={"email": "alan@example.com", "password": "wrong"}
    )
    assert r.status_code == 401


def test_chat_requires_auth():
    r = client.post("/chat", json={"message": "hello"})
    assert r.status_code == 401


def test_chat_without_api_key_returns_clean_503():
    r = client.post(
        "/auth/signup",
        json={"name": "Chat Tester", "email": "chattester@example.com", "password": "secret123"},
    )
    token = r.json()["token"]

    r = client.post(
        "/chat",
        json={"message": "check for drift"},
        headers={"Authorization": f"Bearer {token}"},
    )
    # No OPENROUTER_API_KEY in the test environment -- this should fail
    # cleanly (503) rather than crash (500) or silently succeed.
    assert r.status_code == 503
    assert "OPENROUTER_API_KEY" in r.json()["detail"]


def test_require_role_admin_always_passes():
    """require_role()'s explicit contract: admin passes any role gate,
    regardless of which roles were listed -- Khurram's requirement for
    an admin role that can do anything."""
    dep = auth.require_role("data_owner", "data_steward")
    admin_user = {"id": 1, "email": "a@b.com", "name": "Admin", "roles": ["admin"]}
    assert dep(user=admin_user) == admin_user


def test_require_role_rejects_unlisted_role():
    dep = auth.require_role("data_owner", "data_steward")
    consumer_user = {"id": 2, "email": "c@d.com", "name": "Consumer", "roles": ["data_consumer"]}
    with pytest.raises(HTTPException) as exc_info:
        dep(user=consumer_user)
    assert exc_info.value.status_code == 403
