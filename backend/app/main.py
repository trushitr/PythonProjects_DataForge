from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from openpyxl import load_workbook

from .auth import CurrentUser, router as auth_router
from .db import DATA_DIR, DB_PATH, count_legacy_imports, db, init_db

MAX_BYTES = 5 * 1024 * 1024

app = FastAPI(title="DataForge API", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"], allow_methods=["GET", "POST"], allow_headers=["*"])
app.include_router(auth_router)

init_db()
if legacy := count_legacy_imports():
    logging.getLogger("dataforge").warning(
        "%d import(s) from before user accounts have no owner and are hidden from all users. "
        "Assign them with: python -m app.assign_legacy --email <owner email>", legacy)

# Columns returned to clients. user_id is deliberately excluded.
SUMMARY_COLUMNS = "id, filename, created_at, total_rows, valid_rows, invalid_rows, duplicate_rows"

def parse_file(filename: str, content: bytes) -> tuple[list[str], list[dict[str, Any]]]:
    ext = Path(filename).suffix.lower()
    if ext == ".csv":
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise HTTPException(400, "CSV must be UTF-8 encoded")
        try:
            reader = csv.DictReader(io.StringIO(text, newline=""))
            columns = reader.fieldnames or []
            rows = list(reader)
        except csv.Error as exc:
            raise HTTPException(400, f"Invalid CSV: {exc}")
    elif ext == ".xlsx":
        try:
            book = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
            try:
                sheet = book.active
                iterator = sheet.iter_rows(values_only=True)
                columns = [str(v).strip() if v is not None else "" for v in next(iterator, ())]
                rows = [dict(zip(columns, ["" if v is None else str(v) for v in values])) for values in iterator]
            finally:
                book.close()
        except Exception:
            raise HTTPException(400, "Could not read XLSX file")
    else:
        raise HTTPException(400, "Only .csv and .xlsx files are supported")
    if not columns or any(not c for c in columns) or len(set(columns)) != len(columns):
        raise HTTPException(400, "Column headers must be non-empty and unique")
    if len(rows) > 10000:
        raise HTTPException(413, "Maximum 10,000 rows per upload")
    return columns, rows

def validate(columns: list[str], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Baseline generic validation: empty cells, duplicate rows and malformed CSV records.
    seen: set[str] = set()
    results = []
    for index, row in enumerate(rows, start=2):
        errors = []
        if None in row or len(row) != len(columns):
            errors.append("Unexpected number of fields")
        normalized = {c: str(row.get(c) or "").strip() for c in columns}
        for column, value in normalized.items():
            if not value:
                errors.append(f"{column}: missing value")
        fingerprint = json.dumps(normalized, sort_keys=True)
        duplicate = fingerprint in seen
        if duplicate:
            errors.append("Duplicate row in this file")
        seen.add(fingerprint)
        results.append({"row_number": index, "values": normalized, "errors": errors, "valid": not errors, "duplicate": duplicate})
    return results

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/api/imports", status_code=201)
async def create_import(user: CurrentUser, file: UploadFile = File(...)):
    filename = Path(file.filename or "").name
    if Path(filename).suffix.lower() not in (".csv", ".xlsx"):
        raise HTTPException(400, "Only .csv and .xlsx files are supported")
    content = await file.read(MAX_BYTES + 1)
    if len(content) > MAX_BYTES:
        raise HTTPException(413, "Maximum file size is 5 MB")
    if not content:
        raise HTTPException(400, "File is empty")
    columns, rows = parse_file(filename, content)
    results = validate(columns, rows)
    import_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    summary = {"id": import_id, "filename": filename, "created_at": now,
               "total_rows": len(results), "valid_rows": sum(r["valid"] for r in results),
               "invalid_rows": sum(not r["valid"] for r in results),
               "duplicate_rows": sum(r["duplicate"] for r in results)}
    with db() as conn:
        conn.execute("""INSERT INTO imports (id, filename, created_at, total_rows, valid_rows, invalid_rows,
                                             duplicate_rows, columns_json, results_json, user_id)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                     (import_id, filename, now, summary["total_rows"], summary["valid_rows"],
                      summary["invalid_rows"], summary["duplicate_rows"], json.dumps(columns), json.dumps(results),
                      user["id"]))
    return summary

@app.get("/api/imports")
def list_imports(user: CurrentUser):
    with db() as conn:
        rows = conn.execute(f"SELECT {SUMMARY_COLUMNS} FROM imports WHERE user_id = ? ORDER BY created_at DESC LIMIT 100",
                            (user["id"],)).fetchall()
        return [dict(r) for r in rows]

def get_import(import_id: str, user_id: str):
    # Ownership is part of the query: another user's import (or a legacy import
    # with no owner) is indistinguishable from one that does not exist.
    with db() as conn:
        record = conn.execute(f"SELECT {SUMMARY_COLUMNS}, columns_json, results_json FROM imports WHERE id = ? AND user_id = ?",
                              (import_id, user_id)).fetchone()
    if record is None:
        raise HTTPException(404, "Import not found")
    item = dict(record)
    item["columns"] = json.loads(item.pop("columns_json"))
    item["results"] = json.loads(item.pop("results_json"))
    return item

@app.get("/api/imports/{import_id}")
def import_detail(import_id: str, user: CurrentUser):
    return get_import(import_id, user["id"])

@app.get("/api/imports/{import_id}/report.csv")
def download_report(import_id: str, user: CurrentUser):
    item = get_import(import_id, user["id"])
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["source_row", *item["columns"], "valid", "errors"])
    for row in item["results"]:
        writer.writerow([row["row_number"], *[row["values"].get(c, "") for c in item["columns"]], row["valid"], "; ".join(row["errors"])])
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="dataforge-{import_id}.csv"'})
