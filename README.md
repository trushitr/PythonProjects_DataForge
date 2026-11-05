# DataForge

A local-first data import and validation application. **Baseline v0.1** accepts CSV and XLSX files, checks required cells and exact duplicate rows, stores validation results, and generates downloadable CSV reports. It does **not** import data into a destination system yet.

## Prerequisites

- Python 3.11+ and Node.js 20+
- No paid services or external APIs required

## Backend (Windows PowerShell)

```powershell
cd backend
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000/docs for Swagger API documentation. The SQLite database is created in `backend/data/` by default. Set `DATAFORGE_DATA_DIR` to override it.

## Frontend (second PowerShell window)

```powershell
cd frontend
npm install
npm run dev
```

Open http://127.0.0.1:5173 and upload `samples/customers.csv`.

## Tests

From the `backend` directory with the virtual environment active:

```powershell
pytest -q
```

For frontend production build, run `npm run build` from `frontend`.

## Current behavior and limitations

- Maximum upload: 5 MB, 10,000 rows; CSV must be UTF-8.
- Header names must be unique and nonempty.
- Every column is treated as required; duplicate detection compares complete trimmed rows within a single file.
- Import history stores parsed values in SQLite. **Do not upload sensitive or confidential files** in this initial local development version; it has no authentication, encryption, retention controls, or multi-user isolation.
- The browser displays the first 100 result rows; download the report for all rows.
- XLSX reads the active worksheet only. It does not evaluate formulas.
- No destination-database import, background queue, or custom validation rules yet.

## Planned enhancements (not yet implemented)

Configurable schemas and column mapping, preview/approval, streaming large files, asynchronous jobs with retries, idempotent destination imports, role-based access, audit logs, and scheduled integrations. Choose future work based on actual use and observed needs.

## Repository hygiene

`.gitignore` excludes virtual environments, node_modules, local SQLite data, and build outputs. For an evaluation archive, include the project source and `.gitignore` but exclude `.git`, dependencies, databases, and secrets.
