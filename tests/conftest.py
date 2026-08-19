# Shared test fixtures. auth.py now calls out to real Keycloak (see its
# module docstring) -- every test that touches /auth/signup or
# /auth/login (directly, or indirectly via any endpoint requiring
# login) needs Keycloak's HTTP transport faked, not just tests/test_
# auth.py's own tests. An autouse, session-independent fixture here
# (conftest.py fixtures apply repo-wide, no per-file import needed)
# keeps that fake in place for the whole suite, the same way signup
# worked transparently everywhere before this migration.

from unittest.mock import MagicMock

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app import auth, opa_client

_RSA_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def make_test_token(email: str, roles: list[str]) -> str:
    """Signs a real RS256 JWT against the same test RSA keypair the
    fake_keycloak fixture's JWKS mock verifies against -- for tests
    that need a specific realm role (e.g. checking the RBAC/OPA gates
    on classification/stewardship), rather than the fixed
    ["data_consumer"] role FakeKeycloak's own login flow issues."""
    return pyjwt.encode(
        {"email": email, "given_name": email, "realm_access": {"roles": roles}, "exp": 9999999999},
        _RSA_KEY,
        algorithm="RS256",
    )


class FakeKeycloak:
    """In-memory user store standing in for the real Keycloak realm --
    mirrors the handful of endpoints app/auth.py actually calls. Real
    logic on both sides of the request is exercised (real 201/409/200/
    401 status codes, a real Location header, a real RS256-signed JWT
    verified against a real RSA keypair) -- only the network hop
    itself is faked."""

    def __init__(self):
        self.users = {}  # email -> {"name": str, "password": str, "kc_id": str}
        self._next_id = 1

    def _issue_token(self, email: str) -> str:
        return pyjwt.encode(
            {
                "email": email,
                "given_name": self.users[email]["name"],
                "realm_access": {"roles": ["data_consumer"]},
                "exp": 9999999999,
            },
            _RSA_KEY,
            algorithm="RS256",
        )

    def post(self, url, data=None, json=None, headers=None, timeout=None):
        resp = MagicMock()
        if url == auth._MASTER_TOKEN_URL:
            resp.status_code = 200
            resp.json.return_value = {"access_token": "fake-admin-token", "expires_in": 300}
            return resp

        if url == f"{auth._ADMIN_BASE}/users":
            email = json["email"]
            if email in self.users:
                resp.status_code = 409
                return resp
            kc_id = f"kc-{self._next_id}"
            self._next_id += 1
            self.users[email] = {
                "name": json["firstName"],
                "password": json["credentials"][0]["value"],
                "kc_id": kc_id,
            }
            resp.status_code = 201
            resp.headers = {"Location": f"{auth._ADMIN_BASE}/users/{kc_id}"}
            return resp

        if url.endswith("/role-mappings/realm"):
            resp.status_code = 200
            return resp

        if url == auth._TOKEN_URL:
            email = data["username"]
            password = data["password"]
            user = self.users.get(email)
            if not user or user["password"] != password:
                resp.status_code = 401
                return resp
            resp.status_code = 200
            resp.json.return_value = {"access_token": self._issue_token(email)}
            return resp

        raise AssertionError(f"Unexpected POST to {url}")

    def get(self, url, headers=None, timeout=None):
        resp = MagicMock()
        if url == f"{auth._ADMIN_BASE}/roles/data_consumer":
            resp.status_code = 200
            resp.json.return_value = {"id": "role-data_consumer", "name": "data_consumer"}
            return resp
        raise AssertionError(f"Unexpected GET to {url}")

    def put(self, url, json=None, headers=None, timeout=None):
        resp = MagicMock()
        if url.endswith("/reset-password"):
            # Real endpoint added 2026-08-19 -- see app/auth.py's
            # create_user() docstring for why the inline "credentials"
            # array on creation isn't relied on anymore.
            kc_id = url.split("/users/")[1].split("/reset-password")[0]
            user = next((u for u in self.users.values() if u["kc_id"] == kc_id), None)
            if user is None:
                resp.status_code = 404
                return resp
            user["password"] = json["value"]
            resp.status_code = 204
            return resp
        raise AssertionError(f"Unexpected PUT to {url}")


@pytest.fixture(autouse=True)
def fake_keycloak(monkeypatch):
    fake = FakeKeycloak()
    monkeypatch.setattr(auth.requests, "post", fake.post)
    monkeypatch.setattr(auth.requests, "get", fake.get)
    monkeypatch.setattr(auth.requests, "put", fake.put)
    monkeypatch.setattr(auth, "_admin_token_cache", {"token": None, "expires_at": 0.0})

    fake_signing_key = MagicMock()
    fake_signing_key.key = _RSA_KEY.public_key()
    fake_jwks_client = MagicMock()
    fake_jwks_client.get_signing_key_from_jwt.return_value = fake_signing_key
    monkeypatch.setattr(auth, "_jwk_client", fake_jwks_client)

    yield fake


# Real deployed OPA policy (see /areas/onetech-dataos-rbac-opa-keycloak.md
# and app/opa_client.py's module docstring): classification_allow grants
# admin/data_owner/data_steward; stewardship_assign_allow grants
# admin/data_owner. Mirrored here exactly rather than a generic "allow
# everything" stub, so tests exercise the same role logic that's live.
_OPA_POLICY = {
    "classification_allow": {"admin", "data_owner", "data_steward"},
    "stewardship_assign_allow": {"admin", "data_owner"},
}


@pytest.fixture(autouse=True)
def fake_opa(monkeypatch):
    def fake_query_opa(rule, role):
        return role in _OPA_POLICY.get(rule, set())

    monkeypatch.setattr(opa_client, "_query_opa", fake_query_opa)
    yield fake_query_opa


@pytest.fixture
def test_token_factory():
    """Fixture wrapper around make_test_token(), so callers get the
    closure bound to THIS conftest module instance's _RSA_KEY -- a
    plain `from tests.conftest import make_test_token` in a test file
    can resolve to a second, separately-imported copy of this module
    (pytest's own conftest loading vs. a regular package import), each
    generating its own RSA key, which silently breaks signature
    verification against the fake_keycloak fixture's JWKS mock.
    Confirmed as the real cause of an InvalidSignatureError live during
    this file's own test development, not a hypothetical risk."""
    return make_test_token
