from __future__ import annotations

import csv
import io
import json
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from openpyxl import load_workbook

MAX_BYTES = 5 * 1024 * 1024
DATA_DIR = Path(os.getenv("DATAFORGE_DATA_DIR", Path(__file__).resolve().parents[1] / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "dataforge.db"

app = FastAPI(title="DataForge API", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"], allow_methods=["GET", "POST"], allow_headers=["*"])

@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
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

init_db()

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
async def create_import(file: UploadFile = File(...)):
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
        conn.execute("INSERT INTO imports VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (import_id, filename, now, summary["total_rows"], summary["valid_rows"],
                      summary["invalid_rows"], summary["duplicate_rows"], json.dumps(columns), json.dumps(results)))
    return summary

@app.get("/api/imports")
def list_imports():
    with db() as conn:
        rows = conn.execute("SELECT id, filename, created_at, total_rows, valid_rows, invalid_rows, duplicate_rows FROM imports ORDER BY created_at DESC LIMIT 100").fetchall()
        return [dict(r) for r in rows]

def get_import(import_id: str):
    with db() as conn:
        record = conn.execute("SELECT * FROM imports WHERE id = ?", (import_id,)).fetchone()
    if record is None:
        raise HTTPException(404, "Import not found")
    item = dict(record)
    item["columns"] = json.loads(item.pop("columns_json"))
    item["results"] = json.loads(item.pop("results_json"))
    return item

@app.get("/api/imports/{import_id}")
def import_detail(import_id: str):
    return get_import(import_id)

@app.get("/api/imports/{import_id}/report.csv")
def download_report(import_id: str):
    item = get_import(import_id)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["source_row", *item["columns"], "valid", "errors"])
    for row in item["results"]:
        writer.writerow([row["row_number"], *[row["values"].get(c, "") for c in item["columns"]], row["valid"], "; ".join(row["errors"])])
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="dataforge-{import_id}.csv"'})
