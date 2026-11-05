"""Assign imports created before user accounts existed (user_id IS NULL) to a user.

Usage, from the backend directory (uses DATAFORGE_DATA_DIR like the server):

    python -m app.assign_legacy --email owner@example.com --dry-run
    python -m app.assign_legacy --email owner@example.com

The user must already be registered. Imports that already have an owner are
never changed.
"""
from __future__ import annotations

import argparse
import sys

from .db import db, init_db

class AssignError(Exception):
    pass

def assign_legacy_imports(email: str, dry_run: bool = False) -> int:
    init_db()  # make sure the user_id column exists on an old database
    with db() as conn:
        user = conn.execute("SELECT id FROM users WHERE email = ?", (email.strip().lower(),)).fetchone()
        if user is None:
            raise AssignError(f"No registered user with email {email!r}. Register the account first.")
        if dry_run:
            return conn.execute("SELECT COUNT(*) FROM imports WHERE user_id IS NULL").fetchone()[0]
        return conn.execute("UPDATE imports SET user_id = ? WHERE user_id IS NULL", (user["id"],)).rowcount

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Assign legacy imports (no owner) to a registered user.")
    parser.add_argument("--email", required=True, help="email of the registered user who will own the imports")
    parser.add_argument("--dry-run", action="store_true", help="only report how many imports would be assigned")
    args = parser.parse_args(argv)
    try:
        count = assign_legacy_imports(args.email, dry_run=args.dry_run)
    except AssignError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    verb = "would be assigned" if args.dry_run else "assigned"
    print(f"{count} legacy import(s) {verb} to {args.email.strip().lower()}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
