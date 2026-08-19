# Auth -- now backed by real Keycloak (2026-08-19), not the previous
# homegrown PBKDF2/JWT scheme.
#
# WHY: Khurram asked to close out RBAC/OPA fully by extending real
# login to the dashboard (which has never had any auth at all) so
# Classification & PDPL and Data Stewardship can both gate on real
# roles instead of staying honest-but-unenforced. That meant picking
# a real identity provider rather than bolting a `role` column onto
# the old scheme -- see /areas/onetech-dataos-rbac-opa-keycloak.md for
# the full build history (Keycloak realm `dataos`, 6 realm roles
# including `admin`, confidential client `dataos-app`, all confirmed
# live via direct Admin REST API calls before this file was touched).
#
# INTERFACE PRESERVED ON PURPOSE: create_user(), authenticate_user(),
# issue_token(), get_current_user() keep the exact same names and
# return shapes (dict with at least id/email/name; get_current_user's
# dict also gains a new "roles" list) that every existing caller
# already expects -- main.py's /auth/signup and /auth/login handlers,
# every Depends(auth.get_current_user) site, and the chat frontend's
# own token handling in app/static/index.html all needed ZERO changes
# for this migration. Only the internals changed: identity and
# password verification now live in Keycloak, not this file.
#
# LOCAL `users` TABLE: kept, but now only as an id mirror -- real
# credentials and roles live in Keycloak. It still exists because
# conversations.user_id and datasets.uploaded_by are real FOREIGN KEYs
# into it (see db.py's FIFTH BUG note on why that FK matters under
# Postgres). _upsert_local_user() keeps it in sync by email on every
# login/token-verify, rather than a one-time copy at signup that could
# drift.
#
# NEW: require_role() -- a FastAPI dependency factory for gating a
# route to specific Keycloak realm roles, with `admin` always passing
# regardless of which roles are listed (Khurram's explicit ask: an
# admin role that can do anything). Used by the two policy points this
# RBAC work exists for: viewing RESTRICTED/CONFIDENTIAL columns on the
# Classification page, and assigning/reassigning Data Stewardship
# roles -- see app/main.py's governance/stewardship routes.

import os
import time

import jwt
import requests
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.db import get_conn

KEYCLOAK_URL = os.environ.get("KEYCLOAK_URL", "https://dataos-keycloak.onrender.com")
KEYCLOAK_REALM = os.environ.get("KEYCLOAK_REALM", "dataos")
KEYCLOAK_CLIENT_ID = os.environ.get("KEYCLOAK_CLIENT_ID", "dataos-app")
KEYCLOAK_CLIENT_SECRET = os.environ.get("KEYCLOAK_CLIENT_SECRET", "")
# Bootstrap admin credentials, same ones used to stand up the realm/
# roles/client during the initial Keycloak build -- needed here only
# for the Admin REST API call self-signup makes to create a new
# Keycloak user. Not used for anything else; every real login still
# goes through the normal per-user password grant below.
KEYCLOAK_ADMIN_USER = os.environ.get("KEYCLOAK_ADMIN_USER", "admin")
KEYCLOAK_ADMIN_PASSWORD = os.environ.get("KEYCLOAK_ADMIN_PASSWORD", "")

_REALM_BASE = f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}"
_TOKEN_URL = f"{_REALM_BASE}/protocol/openid-connect/token"
_JWKS_URL = f"{_REALM_BASE}/protocol/openid-connect/certs"
_ADMIN_BASE = f"{KEYCLOAK_URL}/admin/realms/{KEYCLOAK_REALM}"
_MASTER_TOKEN_URL = f"{KEYCLOAK_URL}/realms/master/protocol/openid-connect/token"

_bearer = HTTPBearer(auto_error=False)

_jwk_client = None
# Admin (master-realm) tokens are short-lived -- confirmed directly
# during the Keycloak build: a second Admin API call issued ~60s after
# the first came back 401. Cached with a safety margin and refetched
# rather than reused across a long-lived process.
_admin_token_cache = {"token": None, "expires_at": 0.0}


def _jwks_client():
    global _jwk_client
    if _jwk_client is None:
        _jwk_client = jwt.PyJWKClient(_JWKS_URL)
    return _jwk_client


def _get_admin_token() -> str:
    now = time.time()
    if _admin_token_cache["token"] and now < _admin_token_cache["expires_at"]:
        return _admin_token_cache["token"]
    r = requests.post(
        _MASTER_TOKEN_URL,
        data={
            "client_id": "admin-cli",
            "username": KEYCLOAK_ADMIN_USER,
            "password": KEYCLOAK_ADMIN_PASSWORD,
            "grant_type": "password",
        },
        timeout=10,
    )
    if r.status_code != 200:
        raise HTTPException(status_code=503, detail="Could not reach the identity provider.")
    data = r.json()
    _admin_token_cache["token"] = data["access_token"]
    # expires_in is seconds; refresh 15s early rather than racing expiry.
    _admin_token_cache["expires_at"] = now + max(data.get("expires_in", 60) - 15, 5)
    return _admin_token_cache["token"]


def _upsert_local_user(email: str, name: str) -> int:
    """Keeps the local `users` row (needed only for FK integrity, see
    module docstring) in sync by email on every login/token-verify --
    not a one-time copy at signup, so a display-name change in
    Keycloak eventually reflects locally too."""
    email = email.strip().lower()
    with get_conn() as conn:
        existing = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if existing:
            if existing["id"]:
                conn.execute("UPDATE users SET name = ? WHERE email = ?", (name, email))
                conn.commit()
            return existing["id"]
        cur = conn.execute(
            "INSERT INTO users (email, name, password_hash, salt) VALUES (?, ?, ?, ?)",
            (email, name, "keycloak-managed", "keycloak-managed"),
        )
        conn.commit()
        return cur.lastrowid


def authenticate_user(email: str, password: str) -> dict:
    """Real Keycloak password-grant login. Returns the same
    id/email/name shape every existing caller expects, plus an
    internal _access_token that issue_token() reads -- the actual
    Keycloak-issued JWT, not a locally-minted one."""
    email = email.strip().lower()
    r = requests.post(
        _TOKEN_URL,
        data={
            "client_id": KEYCLOAK_CLIENT_ID,
            "client_secret": KEYCLOAK_CLIENT_SECRET,
            "username": email,
            "password": password,
            "grant_type": "password",
        },
        timeout=10,
    )
    if r.status_code != 200:
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    access_token = r.json()["access_token"]
    # Signature already verified by Keycloak having just issued this
    # token to us over TLS in direct response to correct credentials --
    # decoding without re-verification here only to read the display
    # name claim, not to establish trust.
    claims = jwt.decode(access_token, options={"verify_signature": False})
    name = claims.get("given_name") or claims.get("name") or claims.get("preferred_username") or email
    local_id = _upsert_local_user(email, name)

    return {"id": local_id, "email": email, "name": name, "_access_token": access_token}


def create_user(email: str, name: str, password: str) -> dict:
    """Self-signup: creates a real Keycloak user via the Admin REST
    API, assigns the least-privileged realm role (data_consumer) by
    default -- promotion to a higher role is a deliberate admin
    action via Keycloak directly, not something self-signup grants --
    then logs the new user in immediately via authenticate_user() so
    the response still carries a real usable token, same UX as
    before."""
    email = email.strip().lower()
    admin_token = _get_admin_token()

    r = requests.post(
        f"{_ADMIN_BASE}/users",
        json={
            "email": email,
            "username": email,
            "firstName": name,
            "enabled": True,
            "emailVerified": True,
            # Explicit empty list, not an omission -- confirmed live
            # (2026-08-19) that leaving this out lets the realm's own
            # default required actions apply regardless of the
            # emailVerified flag above, producing a real, confusing
            # failure: user creation succeeds (201), but the immediate
            # login this function does right after it fails with
            # Keycloak's own "invalid_grant: Account is not fully set
            # up" -- emailVerified and a pending VERIFY_EMAIL required
            # action are two separate fields Keycloak doesn't reconcile
            # for you. This account is created by an authenticated
            # signup flow that already collected and validated the
            # password directly, so no further required action makes
            # sense here.
            "requiredActions": [],
            "credentials": [{"type": "password", "value": password, "temporary": False}],
        },
        headers={"Authorization": f"Bearer {admin_token}"},
        timeout=10,
    )
    if r.status_code == 409:
        raise HTTPException(status_code=409, detail="An account with this email already exists.")
    if r.status_code != 201:
        raise HTTPException(status_code=400, detail="Could not create account.")

    user_location = r.headers.get("Location", "")
    kc_user_id = user_location.rstrip("/").split("/")[-1] if user_location else None
    if kc_user_id:
        role_r = requests.get(
            f"{_ADMIN_BASE}/roles/data_consumer",
            headers={"Authorization": f"Bearer {admin_token}"},
            timeout=10,
        )
        if role_r.status_code == 200:
            requests.post(
                f"{_ADMIN_BASE}/users/{kc_user_id}/role-mappings/realm",
                json=[role_r.json()],
                headers={"Authorization": f"Bearer {admin_token}"},
                timeout=10,
            )
        # A failure here isn't fatal to signup -- the account exists and
        # can log in; it just starts with no realm role until an admin
        # assigns one. Not raised, so signup doesn't fail over a
        # non-critical role-assignment hiccup.

    return authenticate_user(email, password)


def issue_token(user: dict) -> str:
    """Kept for interface compatibility with the existing signup/login
    call sites in main.py, which call `auth.issue_token(user)`
    immediately after create_user()/authenticate_user(). The real
    Keycloak-issued access token is already on the user dict (both
    functions above set _access_token) -- this just reads it back."""
    token = user.get("_access_token")
    if not token:
        raise RuntimeError("issue_token() called on a user dict with no _access_token.")
    return token


def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(_bearer)) -> dict:
    """Verifies a real Keycloak-issued JWT against the realm's JWKS
    (RS256, real signature verification -- unlike the display-name-only
    decode in authenticate_user() above). Returns id/email/name plus a
    new "roles" list (the token's realm_access.roles) -- every existing
    caller that only reads ["id"]/["email"] is unaffected; new callers
    (require_role() below) read ["roles"]."""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token. Log in and pass the token as 'Authorization: Bearer <token>'.",
        )
    try:
        signing_key = _jwks_client().get_signing_key_from_jwt(credentials.credentials)
        claims = jwt.decode(
            credentials.credentials,
            signing_key.key,
            algorithms=["RS256"],
            options={"verify_aud": False},
        )
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token.")

    email = (claims.get("email") or claims.get("preferred_username") or "").strip().lower()
    if not email:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token missing identity claim.")
    name = claims.get("given_name") or claims.get("name") or claims.get("preferred_username") or email
    roles = list((claims.get("realm_access") or {}).get("roles", []))
    local_id = _upsert_local_user(email, name)

    return {"id": local_id, "email": email, "name": name, "roles": roles}


def require_role(*allowed_roles: str):
    """FastAPI dependency factory: gates a route to a Keycloak realm
    role in allowed_roles. `admin` always passes regardless of which
    roles are listed -- Khurram's explicit requirement for an admin
    role that can do anything. Usage:
        @app.post(...)
        def route(user: dict = Depends(auth.require_role("data_owner", "data_steward"))):
    """
    def _dependency(user: dict = Depends(get_current_user)) -> dict:
        roles = set(user.get("roles", []))
        if "admin" in roles or (roles & set(allowed_roles)):
            return user
        raise HTTPException(
            status_code=403,
            detail=f"This action requires one of these roles: {', '.join(allowed_roles)}.",
        )
    return _dependency
