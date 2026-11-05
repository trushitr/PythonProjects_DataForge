"""Per-user ownership of imports, legacy (ownerless) imports and the schema migration."""
import json
import sqlite3
import uuid

import pytest
from fastapi.testclient import TestClient

import app.db
from app.assign_legacy import AssignError, assign_legacy_imports, main as assign_main
from app.db import init_db
from app.main import app as fastapi_app

CSV = b"name,email\nAlice,a@example.com\nBob,b@example.com\n"

def make_user(email: str | None = None) -> TestClient:
    """A TestClient logged in as a newly registered user."""
    email = email or f"user-{uuid.uuid4().hex[:10]}@example.com"
    c = TestClient(fastapi_app)
    assert c.post("/api/auth/register", json={"email": email, "password": "password123"}).status_code == 201
    token = c.post("/api/auth/login", data={"username": email, "password": "password123"}).json()["access_token"]
    c.headers["Authorization"] = f"Bearer {token}"
    return c

def upload(c: TestClient, name: str = "people.csv") -> dict:
    res = c.post("/api/imports", files={"file": (name, CSV, "text/csv")})
    assert res.status_code == 201, res.text
    return res.json()

def user_id_of(c: TestClient) -> str:
    return c.get("/api/auth/me").json()["id"]

def insert_legacy_import() -> str:
    """Insert a row the way the pre-auth version did: no user_id."""
    import_id = str(uuid.uuid4())
    results = [{"row_number": 2, "values": {"name": "Old"}, "errors": [], "valid": True, "duplicate": False}]
    with app.db.db() as conn:
        conn.execute("""INSERT INTO imports (id, filename, created_at, total_rows, valid_rows, invalid_rows,
                        duplicate_rows, columns_json, results_json) VALUES (?, 'legacy.csv', '2020-01-01T00:00:00+00:00',
                        1, 1, 0, 0, ?, ?)""", (import_id, json.dumps(["name"]), json.dumps(results)))
    return import_id

@pytest.fixture
def alice() -> TestClient:
    return make_user()

@pytest.fixture
def bob() -> TestClient:
    return make_user()

# ---- Authentication is required ---------------------------------------------

@pytest.mark.parametrize("method,path", [
    ("get", "/api/imports"),
    ("get", "/api/imports/some-id"),
    ("get", "/api/imports/some-id/report.csv"),
])
def test_import_reads_require_login(method, path):
    res = getattr(TestClient(fastapi_app), method)(path)
    assert res.status_code == 401
    assert res.headers["www-authenticate"] == "Bearer"

def test_upload_requires_login():
    anon = TestClient(fastapi_app)
    before = app.db.count_legacy_imports()
    res = anon.post("/api/imports", files={"file": ("people.csv", CSV, "text/csv")})
    assert res.status_code == 401
    assert app.db.count_legacy_imports() == before  # nothing stored without an owner

def test_health_stays_public():
    assert TestClient(fastapi_app).get("/health").status_code == 200

# ---- Ownership is recorded -----------------------------------------------------

def test_upload_stores_uploader_user_id(alice):
    summary = upload(alice)
    with app.db.db() as conn:
        (owner,) = conn.execute("SELECT user_id FROM imports WHERE id = ?", (summary["id"],)).fetchone()
    assert owner == user_id_of(alice)

def test_user_id_not_exposed_in_responses(alice):
    summary = upload(alice)
    assert "user_id" not in summary
    assert all("user_id" not in item for item in alice.get("/api/imports").json())
    assert "user_id" not in alice.get(f"/api/imports/{summary['id']}").json()

def test_response_shapes_unchanged(alice):
    summary = upload(alice)
    keys = {"id", "filename", "created_at", "total_rows", "valid_rows", "invalid_rows", "duplicate_rows"}
    assert set(summary) == keys
    assert set(alice.get("/api/imports").json()[0]) == keys
    assert set(alice.get(f"/api/imports/{summary['id']}").json()) == keys | {"columns", "results"}

# ---- Isolation between two users ---------------------------------------------

def test_history_only_shows_own_imports(alice, bob):
    a1, a2 = upload(alice, "a1.csv"), upload(alice, "a2.csv")
    b1 = upload(bob, "b1.csv")
    alice_ids = {i["id"] for i in alice.get("/api/imports").json()}
    bob_ids = {i["id"] for i in bob.get("/api/imports").json()}
    assert alice_ids == {a1["id"], a2["id"]}
    assert bob_ids == {b1["id"]}

def test_new_user_sees_empty_history(alice):
    upload(alice)
    assert make_user().get("/api/imports").json() == []

def test_owner_can_view_and_download(alice):
    summary = upload(alice)
    assert alice.get(f"/api/imports/{summary['id']}").status_code == 200
    report = alice.get(f"/api/imports/{summary['id']}/report.csv")
    assert report.status_code == 200
    assert report.text.startswith("source_row,name,email,valid,errors")

def test_other_user_cannot_view_details(alice, bob):
    summary = upload(alice)
    res = bob.get(f"/api/imports/{summary['id']}")
    assert res.status_code == 404

def test_other_user_cannot_download_report(alice, bob):
    summary = upload(alice)
    res = bob.get(f"/api/imports/{summary['id']}/report.csv")
    assert res.status_code == 404
    assert "Alice" not in res.text

def test_foreign_import_looks_identical_to_missing_one(alice, bob):
    # Bob must not be able to tell whether an ID exists and belongs to someone else.
    summary = upload(alice)
    foreign = bob.get(f"/api/imports/{summary['id']}")
    missing = bob.get(f"/api/imports/{uuid.uuid4()}")
    assert (foreign.status_code, foreign.json()) == (missing.status_code, missing.json())
    foreign_r = bob.get(f"/api/imports/{summary['id']}/report.csv")
    missing_r = bob.get(f"/api/imports/{uuid.uuid4()}/report.csv")
    assert (foreign_r.status_code, foreign_r.json()) == (missing_r.status_code, missing_r.json())

def test_injection_style_import_id_does_not_leak(alice, bob):
    upload(alice)
    for bad in ["' OR '1'='1", "%' OR user_id IS NOT NULL --"]:
        assert bob.get(f"/api/imports/{bad}").status_code == 404

def test_access_is_isolated_both_ways(alice, bob):
    a, b = upload(alice), upload(bob)
    assert alice.get(f"/api/imports/{b['id']}").status_code == 404
    assert bob.get(f"/api/imports/{a['id']}").status_code == 404
    assert alice.get(f"/api/imports/{a['id']}").status_code == 200
    assert bob.get(f"/api/imports/{b['id']}").status_code == 200

def test_deleted_user_token_cannot_upload_or_read():
    carol = make_user()
    summary = upload(carol)
    with app.db.db() as conn:
        conn.execute("DELETE FROM imports WHERE user_id = ?", (user_id_of(carol),))
        conn.execute("DELETE FROM users WHERE id = ?", (user_id_of(carol),))
    assert carol.post("/api/imports", files={"file": ("x.csv", CSV, "text/csv")}).status_code == 401
    assert carol.get("/api/imports").status_code == 401
    assert carol.get(f"/api/imports/{summary['id']}").status_code == 401

def test_import_owner_must_be_a_real_user():
    with pytest.raises(sqlite3.IntegrityError):
        with app.db.db() as conn:
            conn.execute("""INSERT INTO imports (id, filename, created_at, total_rows, valid_rows, invalid_rows,
                            duplicate_rows, columns_json, results_json, user_id)
                            VALUES ('x', 'x.csv', 'now', 0, 0, 0, 0, '[]', '[]', 'no-such-user')""")

# ---- Legacy imports (no owner) ------------------------------------------------

def test_legacy_imports_hidden_from_everyone(alice, bob):
    legacy_id = insert_legacy_import()
    for c in (alice, bob):
        assert legacy_id not in {i["id"] for i in c.get("/api/imports").json()}
        assert c.get(f"/api/imports/{legacy_id}").status_code == 404
        assert c.get(f"/api/imports/{legacy_id}/report.csv").status_code == 404

@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    """Point the app at a separate, empty database for this test only."""
    monkeypatch.setattr(app.db, "DB_PATH", tmp_path / "dataforge.db")
    init_db()
    return tmp_path / "dataforge.db"

def test_assign_legacy_gives_ownerless_imports_to_user(fresh_db, capsys):
    legacy_ids = {insert_legacy_import(), insert_legacy_import()}
    owner, other = make_user("owner@example.com"), make_user()
    owned_by_other = upload(other)

    assert assign_main(["--email", "owner@example.com", "--dry-run"]) == 0
    assert "2 legacy import(s) would be assigned" in capsys.readouterr().out
    assert app.db.count_legacy_imports() == 2  # dry run changed nothing

    assert assign_main(["--email", "  OWNER@example.com "]) == 0
    assert "2 legacy import(s) assigned to owner@example.com" in capsys.readouterr().out
    assert app.db.count_legacy_imports() == 0

    assert {i["id"] for i in owner.get("/api/imports").json()} == legacy_ids
    for legacy_id in legacy_ids:
        assert owner.get(f"/api/imports/{legacy_id}/report.csv").status_code == 200
        assert other.get(f"/api/imports/{legacy_id}").status_code == 404
    # Imports that already had an owner are untouched.
    assert {i["id"] for i in other.get("/api/imports").json()} == {owned_by_other["id"]}
    # Running again is harmless.
    assert assign_legacy_imports("owner@example.com") == 0

def test_assign_legacy_unknown_email(fresh_db, capsys):
    insert_legacy_import()
    with pytest.raises(AssignError):
        assign_legacy_imports("nobody@example.com")
    assert assign_main(["--email", "nobody@example.com"]) == 1
    assert "No registered user" in capsys.readouterr().err
    assert app.db.count_legacy_imports() == 1

# ---- Migration of a database created before accounts existed ---------------

BASELINE_SCHEMA = """CREATE TABLE imports (
    id TEXT PRIMARY KEY, filename TEXT NOT NULL, created_at TEXT NOT NULL,
    total_rows INTEGER NOT NULL, valid_rows INTEGER NOT NULL,
    invalid_rows INTEGER NOT NULL, duplicate_rows INTEGER NOT NULL,
    columns_json TEXT NOT NULL, results_json TEXT NOT NULL)"""

def test_migration_upgrades_baseline_database(tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as conn:
        conn.execute(BASELINE_SCHEMA)
        conn.execute("INSERT INTO imports VALUES ('old-1', 'old.csv', '2020-01-01', 1, 1, 0, 0, '[\"a\"]', '[]')")
    monkeypatch.setattr(app.db, "DB_PATH", path)

    init_db()
    init_db()  # idempotent: running on every startup must be safe

    with sqlite3.connect(path) as conn:
        columns = [r[1] for r in conn.execute("PRAGMA table_info(imports)")]
        row = conn.execute("SELECT id, filename, total_rows, columns_json, user_id FROM imports").fetchall()
        indexes = [r[1] for r in conn.execute("PRAGMA index_list(imports)")]
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "user_id" in columns
    assert row == [("old-1", "old.csv", 1, '["a"]', None)]  # data intact, no owner
    assert "idx_imports_user_created" in indexes
    assert "users" in tables
    assert app.db.count_legacy_imports() == 1
