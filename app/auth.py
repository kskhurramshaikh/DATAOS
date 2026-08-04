# Auth -- signup, login, JWT.
#
# Stdlib password hashing (PBKDF2-HMAC-SHA256) rather than bcrypt, so
# there's no C-extension build step to worry about on top of everything
# else in this deploy. Good enough for a demo/testing population; worth
# revisiting alongside the DB swap when this goes past testing.

import hashlib
import os
import secrets
import time

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.db import get_conn

JWT_SECRET = os.environ.get("JWT_SECRET", "dev-only-insecure-secret-change-me")
JWT_ALGO = "HS256"
JWT_TTL_SECONDS = 7 * 24 * 60 * 60  # 7 days

_bearer = HTTPBearer(auto_error=False)


def _hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000).hex()


def create_user(email: str, name: str, password: str) -> dict:
    email = email.strip().lower()
    salt = secrets.token_hex(16)
    password_hash = _hash_password(password, salt)

    with get_conn() as conn:
        existing = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="An account with this email already exists.")

        cur = conn.execute(
            "INSERT INTO users (email, name, password_hash, salt) VALUES (?, ?, ?, ?)",
            (email, name, password_hash, salt),
        )
        conn.commit()
        user_id = cur.lastrowid

    return {"id": user_id, "email": email, "name": name}


def authenticate_user(email: str, password: str) -> dict:
    email = email.strip().lower()
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()

    if row is None:
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    if _hash_password(password, row["salt"]) != row["password_hash"]:
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    return {"id": row["id"], "email": row["email"], "name": row["name"]}


def issue_token(user: dict) -> str:
    payload = {
        "sub": str(user["id"]),
        "email": user["email"],
        "name": user["name"],
        "exp": int(time.time()) + JWT_TTL_SECONDS,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)


def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(_bearer)) -> dict:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token. Log in and pass the token as 'Authorization: Bearer <token>'.",
        )
    try:
        payload = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=[JWT_ALGO])
    except jwt.PyJWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token.")

    return {"id": int(payload["sub"]), "email": payload["email"], "name": payload["name"]}
