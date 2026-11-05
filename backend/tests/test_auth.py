import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

import jwt
from fastapi.testclient import TestClient

from app.auth import JWT_ALGORITHM, JWT_SECRET, create_access_token
from app.db import DB_PATH
from app.main import app

client = TestClient(app)
PASSWORD = "correct horse battery"

def new_email() -> str:
    return f"user-{uuid.uuid4().hex[:10]}@example.com"

def register(email: str, password: str = PASSWORD):
    return client.post("/api/auth/register", json={"email": email, "password": password})

def login(email: str, password: str = PASSWORD):
    return client.post("/api/auth/login", data={"username": email, "password": password})

def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}

# ---- Registration ------------------------------------------------------------

def test_register_returns_user_without_password():
    email = new_email()
    res = register(email)
    assert res.status_code == 201, res.text
    body = res.json()
    assert set(body) == {"id", "email", "created_at"}
    assert body["email"] == email

def test_password_is_stored_as_argon2_hash():
    email = new_email()
    register(email)
    with sqlite3.connect(DB_PATH) as conn:
        (stored,) = conn.execute("SELECT password_hash FROM users WHERE email = ?", (email,)).fetchone()
    assert stored != PASSWORD
    assert PASSWORD not in stored
    assert stored.startswith("$argon2id$")

def test_same_password_gets_different_hashes():
    a, b = new_email(), new_email()
    register(a)
    register(b)
    with sqlite3.connect(DB_PATH) as conn:
        hashes = [r[0] for r in conn.execute("SELECT password_hash FROM users WHERE email IN (?, ?)", (a, b))]
    assert len(hashes) == 2 and hashes[0] != hashes[1]

def test_email_is_normalised_to_lowercase():
    local = f"Mixed-{uuid.uuid4().hex[:8]}"
    res = register(f"{local}@Example.COM")
    assert res.status_code == 201
    assert res.json()["email"] == f"{local.lower()}@example.com"

def test_duplicate_email_rejected_case_insensitively():
    email = new_email()
    assert register(email).status_code == 201
    assert register(email).status_code == 409
    assert register(email.upper()).status_code == 409

def test_invalid_email_rejected():
    assert register("not-an-email").status_code == 422

def test_short_password_rejected():
    assert register(new_email(), "short").status_code == 422

def test_overlong_password_rejected():
    assert register(new_email(), "x" * 129).status_code == 422

def test_missing_fields_rejected():
    assert client.post("/api/auth/register", json={"email": new_email()}).status_code == 422

# ---- Login --------------------------------------------------------------------

def test_login_returns_bearer_token():
    email = new_email()
    register(email)
    res = login(email)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 60 * 60
    claims = jwt.decode(body["access_token"], JWT_SECRET, algorithms=[JWT_ALGORITHM])
    assert claims["sub"] == register_id(email)

def register_id(email: str) -> str:
    with sqlite3.connect(DB_PATH) as conn:
        return conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()[0]

def test_login_email_is_case_insensitive():
    email = new_email()
    register(email)
    assert login(f"  {email.upper()} ").status_code == 200

def test_wrong_password_and_unknown_email_give_same_error():
    email = new_email()
    register(email)
    wrong = login(email, "wrong password!")
    unknown = login(new_email())
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json() == {"detail": "Incorrect email or password"}
    assert wrong.headers["www-authenticate"] == "Bearer"

def test_login_rejects_json_body():
    # Login uses the standard OAuth2 form so Swagger's Authorize button works.
    email = new_email()
    register(email)
    assert client.post("/api/auth/login", json={"username": email, "password": PASSWORD}).status_code == 422

# ---- Token-protected /me -----------------------------------------------------

def test_me_with_valid_token():
    email = new_email()
    register(email)
    token = login(email).json()["access_token"]
    res = client.get("/api/auth/me", headers=bearer(token))
    assert res.status_code == 200
    assert res.json()["email"] == email
    assert "password_hash" not in res.json()

def test_me_without_token():
    res = client.get("/api/auth/me")
    assert res.status_code == 401
    assert res.headers["www-authenticate"] == "Bearer"

def test_me_with_garbage_token():
    assert client.get("/api/auth/me", headers=bearer("not.a.jwt")).status_code == 401

def test_me_with_expired_token():
    email = new_email()
    register(email)
    past = datetime.now(timezone.utc) - timedelta(hours=2)
    token = jwt.encode({"sub": register_id(email), "iat": past, "exp": past + timedelta(minutes=5)}, JWT_SECRET, algorithm=JWT_ALGORITHM)
    res = client.get("/api/auth/me", headers=bearer(token))
    assert res.status_code == 401
    assert res.json()["detail"] == "Token has expired"

def test_me_with_token_signed_by_other_secret():
    email = new_email()
    register(email)
    now = datetime.now(timezone.utc)
    forged = jwt.encode({"sub": register_id(email), "iat": now, "exp": now + timedelta(minutes=5)}, "attacker-secret-" + "y" * 40, algorithm=JWT_ALGORITHM)
    assert client.get("/api/auth/me", headers=bearer(forged)).status_code == 401

def test_me_rejects_unsigned_alg_none_token():
    email = new_email()
    register(email)
    now = datetime.now(timezone.utc)
    unsigned = jwt.encode({"sub": register_id(email), "iat": now, "exp": now + timedelta(minutes=5)}, None, algorithm="none")
    assert client.get("/api/auth/me", headers=bearer(unsigned)).status_code == 401

def test_me_rejects_token_without_expiry():
    email = new_email()
    register(email)
    token = jwt.encode({"sub": register_id(email), "iat": datetime.now(timezone.utc)}, JWT_SECRET, algorithm=JWT_ALGORITHM)
    assert client.get("/api/auth/me", headers=bearer(token)).status_code == 401

def test_me_rejects_token_for_deleted_user():
    email = new_email()
    register(email)
    user_id = register_id(email)
    token = create_access_token(user_id)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    assert client.get("/api/auth/me", headers=bearer(token)).status_code == 401
