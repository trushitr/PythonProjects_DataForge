"""User accounts: registration, login, password hashing and JWT bearer tokens."""
from __future__ import annotations

import logging
import os
import secrets
import sqlite3
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr, Field

from .db import DATA_DIR, db

logger = logging.getLogger("dataforge.auth")

JWT_ALGORITHM = "HS256"
MIN_SECRET_LENGTH = 32
SECRET_FILE = DATA_DIR / "jwt_secret"

def _load_token_minutes() -> int:
    raw = os.getenv("DATAFORGE_TOKEN_MINUTES", "60")
    try:
        minutes = int(raw)
    except ValueError:
        minutes = 0
    if minutes <= 0:
        # 0 or negative would issue tokens that are already expired: login
        # "succeeds" and every following request fails with 401.
        raise RuntimeError(f"DATAFORGE_TOKEN_MINUTES must be a positive whole number of minutes, got {raw!r}")
    return minutes

def _load_secret() -> str:
    secret = os.getenv("DATAFORGE_JWT_SECRET")
    if secret is not None:
        if len(secret) < MIN_SECRET_LENGTH:
            raise RuntimeError(f"DATAFORGE_JWT_SECRET must be at least {MIN_SECRET_LENGTH} characters")
        return secret
    # No env var: generate a secret once and keep it in the data directory, so
    # it survives restarts (uvicorn --reload restarts on every code change) and
    # is shared by all worker processes. A per-process secret made tokens fail
    # at random with --workers > 1. Anyone who can read this file can forge
    # tokens, but they could also read the database next to it.
    try:
        fd = os.open(SECRET_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        # Another process may have created the file a moment ago and still be
        # writing it; wait briefly for the content.
        for _ in range(40):
            secret = SECRET_FILE.read_text(encoding="utf-8").strip()
            if len(secret) >= MIN_SECRET_LENGTH:
                return secret
            time.sleep(0.05)
        raise RuntimeError(f"{SECRET_FILE} is empty or too short. Delete it to generate a new one, "
                           "or set DATAFORGE_JWT_SECRET.")
    secret = secrets.token_urlsafe(64)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(secret)
    logger.warning("DATAFORGE_JWT_SECRET is not set; generated a signing secret and saved it to %s. "
                   "Set DATAFORGE_JWT_SECRET for shared or production deployments.", SECRET_FILE)
    return secret

TOKEN_MINUTES = _load_token_minutes()

JWT_SECRET = _load_secret()

# argon2id with the library's current recommended parameters.
password_hasher = PasswordHasher()
# Used when a login email does not exist, so that response time does not reveal
# whether an account is registered.
_DUMMY_HASH = password_hasher.hash(secrets.token_urlsafe(16))

def hash_password(password: str) -> str:
    return password_hasher.hash(password)

def verify_password(password_hash: str, password: str) -> bool:
    try:
        return password_hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False

def create_access_token(user_id: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {"sub": user_id, "iat": now, "exp": now + timedelta(minutes=TOKEN_MINUTES)}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

# ---- Schemas -------------------------------------------------------------

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)

class UserOut(BaseModel):
    id: str
    email: str
    created_at: str

class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int

# ---- Dependencies ----------------------------------------------------------

# tokenUrl lets the Swagger UI "Authorize" button log in against our endpoint.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

def _credentials_error(detail: str = "Could not validate credentials") -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, detail, headers={"WWW-Authenticate": "Bearer"})

def _user_by(column: str, value: str) -> dict[str, Any] | None:
    assert column in ("id", "email")
    with db() as conn:
        row = conn.execute(f"SELECT id, email, password_hash, created_at FROM users WHERE {column} = ?", (value,)).fetchone()
    return dict(row) if row else None

def get_current_user(token: Annotated[str, Depends(oauth2_scheme)]) -> dict[str, Any]:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM],
                             options={"require": ["exp", "iat", "sub"]})
    except jwt.ExpiredSignatureError:
        raise _credentials_error("Token has expired")
    except jwt.InvalidTokenError:
        raise _credentials_error()
    user = _user_by("id", payload["sub"])
    if user is None:  # account removed after the token was issued
        raise _credentials_error()
    user.pop("password_hash")
    return user

CurrentUser = Annotated[dict[str, Any], Depends(get_current_user)]

# ---- Routes ------------------------------------------------------------------
# Plain `def` (not async) so the CPU-heavy hashing runs in FastAPI's threadpool
# instead of blocking the event loop.

router = APIRouter(prefix="/api/auth", tags=["auth"])

@router.post("/register", status_code=201, response_model=UserOut)
def register(body: RegisterRequest):
    email = body.email.strip().lower()
    user = {"id": str(uuid.uuid4()), "email": email, "created_at": datetime.now(timezone.utc).isoformat()}
    password_hash = hash_password(body.password)
    try:
        with db() as conn:
            conn.execute("INSERT INTO users (id, email, password_hash, created_at) VALUES (?, ?, ?, ?)",
                         (user["id"], email, password_hash, user["created_at"]))
    except sqlite3.IntegrityError:
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with this email already exists")
    return user

@router.post("/login", response_model=TokenOut)
def login(form: Annotated[OAuth2PasswordRequestForm, Depends()]):
    # OAuth2 form field is called "username"; DataForge uses the email address.
    user = _user_by("email", form.username.strip().lower())
    if user is None:
        verify_password(_DUMMY_HASH, form.password)
        raise _credentials_error("Incorrect email or password")
    if not verify_password(user["password_hash"], form.password):
        raise _credentials_error("Incorrect email or password")
    if password_hasher.check_needs_rehash(user["password_hash"]):
        with db() as conn:
            conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(form.password), user["id"]))
    return {"access_token": create_access_token(user["id"]), "token_type": "bearer", "expires_in": TOKEN_MINUTES * 60}

@router.get("/me", response_model=UserOut)
def me(user: CurrentUser):
    return user
