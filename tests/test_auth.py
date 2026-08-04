from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_signup_then_login():
    r = client.post(
        "/auth/signup",
        json={"name": "Ada Lovelace", "email": "ada@example.com", "password": "secret123"},
    )
    assert r.status_code == 200
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
