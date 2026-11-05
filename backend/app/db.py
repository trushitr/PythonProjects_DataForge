from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DATA_DIR = Path(os.getenv("DATAFORGE_DATA_DIR", Path(__file__).resolve().parents[1] / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "dataforge.db"

@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # Off by default in SQLite and must be set per connection, before any
    # transaction starts, for the imports.user_id -> users.id reference to apply.
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

def init_db():
    with db() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS imports (
            id TEXT PRIMARY KEY, filename TEXT NOT NULL, created_at TEXT NOT NULL,
            total_rows INTEGER NOT NULL, valid_rows INTEGER NOT NULL,
            invalid_rows INTEGER NOT NULL, duplicate_rows INTEGER NOT NULL,
            columns_json TEXT NOT NULL, results_json TEXT NOT NULL
        )""")
        # Emails are stored lower-cased by the API; COLLATE NOCASE is a second
        # guard so "A@x.com" and "a@x.com" can never become two accounts.
        conn.execute("""CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            email TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        )""")
        _add_imports_user_id(conn)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_imports_user_created ON imports (user_id, created_at)")

def _add_imports_user_id(conn: sqlite3.Connection) -> None:
    """Migration: link imports to their owner.

    `CREATE TABLE IF NOT EXISTS` never alters an existing table, so databases
    created before accounts existed need the column added explicitly. Rows that
    already exist get user_id = NULL ("legacy" imports). They stay in the
    database but are not visible to any user until assigned with
    `python -m app.assign_legacy --email <owner>`.
    """
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(imports)")}
    if "user_id" in columns:
        return
    try:
        conn.execute("ALTER TABLE imports ADD COLUMN user_id TEXT REFERENCES users (id)")
    except sqlite3.OperationalError as exc:
        # Another process (e.g. a second uvicorn worker) migrated first.
        if "duplicate column" not in str(exc).lower():
            raise

def count_legacy_imports() -> int:
    with db() as conn:
        return conn.execute("SELECT COUNT(*) FROM imports WHERE user_id IS NULL").fetchone()[0]
