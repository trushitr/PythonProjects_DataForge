# DataForge

A local-first data import and validation application. **Baseline v0.1** accepts CSV and XLSX files, checks required cells and exact duplicate rows, stores validation results, and generates downloadable CSV reports. It does **not** import data into a destination system yet.

## Prerequisites

- **Python 3.11 or newer** (64-bit, from python.org; keep "py launcher" ticked in the installer)
- **Node.js 20 or newer** (LTS installer from nodejs.org; it includes npm)
- **VS Code** with the **Python** extension (Microsoft)
- No paid services or external APIs required

Check in a terminal: `py --version` and `node --version`.

## Run on Windows in VS Code

### 1. Open the project

In VS Code choose **File → Open Folder…** and select the `DataForge` folder (the one containing `README.md`, `backend` and `frontend`). Open a terminal with **Terminal → New Terminal**. The commands below are for the default PowerShell terminal.

### 2. Allow PowerShell scripts (one time per Windows user)

Windows blocks the virtual-environment activation script and npm's PowerShell wrapper by default (error: *running scripts is disabled on this system*). Run once:

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

### 3. Backend: terminal 1

From the project folder:

```powershell
cd backend
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
python -m uvicorn app.main:app --reload
```

The prompt shows `(.venv)` once the environment is active. Leave this terminal running; it should end with `Uvicorn running on http://127.0.0.1:8000`. API docs: http://127.0.0.1:8000/docs.

On first start the server creates `backend\data\` containing the SQLite database and a generated token signing secret (`jwt_secret`). Nothing needs configuring for local use, and you stay signed in across `--reload` restarts.

### 4. Point VS Code at the virtual environment

Press **Ctrl+Shift+P → Python: Select Interpreter → Enter interpreter path…** and enter `backend\.venv\Scripts\python.exe`. This enables code navigation and the Testing panel, and new terminals activate the environment automatically.

### 5. Frontend: terminal 2

Open a second terminal (the **+** button in the terminal panel), then from the project folder:

```powershell
cd frontend
npm ci
npm run dev
```

`npm ci` installs the exact versions in `package-lock.json`, including the Windows build-tool binaries. Use `npm install` only when you intend to change dependencies.

### 6. Use the app

Open http://127.0.0.1:5173, choose **Create an account**, then upload `samples\customers.csv`. Your imports appear under **Recent imports**; **View** shows the results and **Download CSV report** saves the full report. **Log out** is in the top-right corner.

### Next time

Terminal 1:

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
python -m uvicorn app.main:app --reload
```

Terminal 2:

```powershell
cd frontend
npm run dev
```

Stop either server with **Ctrl+C**. To start over with no accounts or imports, stop the backend and delete the `backend\data` folder. This permanently deletes all accounts and imports.

### macOS / Linux

The same steps work with `python3 -m venv .venv` and `source .venv/bin/activate` instead of the `py` and `Activate.ps1` lines. Skip step 2.

## Tests

In a terminal with the virtual environment active:

```powershell
cd backend
python -m pytest -q
```

Tests use a temporary database and never touch `backend\data`. For the frontend production build, run `npm run build` in `frontend`.

**VS Code Testing panel:** after step 4, create `.vscode\settings.json` in the project folder (it is git-ignored) with:

```json
{
  "python.testing.pytestEnabled": true,
  "python.testing.unittestEnabled": false,
  "python.testing.pytestArgs": ["backend/tests"]
}
```

Then open the Testing view (flask icon) and refresh. All tests are discovered and can be run or debugged from there.

## Troubleshooting

| Problem | Fix |
|---|---|
| *running scripts is disabled on this system* | Step 2. |
| `py` is not recognized | Install Python from python.org with the py launcher, or use `python` instead of `py`. |
| *Cannot reach the DataForge server. Is the backend running?* | The backend is not running or has crashed. Check terminal 1, start it again, and reload the page. The frontend always calls `http://127.0.0.1:8000`. |
| `[WinError 10048]` / address already in use | Another process is using port 8000 (often a second uvicorn). Stop it, then start the backend again. |
| `Cannot find module @rollup/rollup-win32-x64-msvc` | `node_modules` came from an older install. In `frontend`, run `Remove-Item -Recurse -Force node_modules`, then `npm ci`. |
| Signed out after an hour | Expected: tokens last 60 minutes. Set `$env:DATAFORGE_TOKEN_MINUTES = "240"` in terminal 1 before starting uvicorn. |
| Forgot a password | There is no password reset yet. Register a new account, or delete `backend\data` to start over (deletes everything). |
| Tests missing from the Testing panel | Check the interpreter (step 4) and the settings above. See **View → Output → Python** for errors. |

## Authentication and user accounts

Users register with an email and password (minimum 8 characters) and log in to receive a JWT bearer token. Passwords are hashed with Argon2id; plain-text passwords are never stored.

| Variable | Default | Purpose |
|---|---|---|
| `DATAFORGE_JWT_SECRET` | generated once, saved to `backend/data/jwt_secret` | Token signing key, minimum 32 characters. The generated key survives restarts (including `--reload`) and is shared by all workers, so nothing needs configuring for local use. Set the variable for shared or production deployments. Changing or deleting the key signs everyone out. |
| `DATAFORGE_TOKEN_MINUTES` | `60` | Access token lifetime (positive whole number). |
| `DATAFORGE_DATA_DIR` | `backend/data` | Where the database and generated secret are stored. Never commit or share this folder. |

To supply your own secret, set it before starting uvicorn:

```powershell
$env:DATAFORGE_JWT_SECRET = python -c "import secrets; print(secrets.token_urlsafe(64))"
```

Auth endpoints: `POST /api/auth/register` (JSON `email`, `password`), `POST /api/auth/login` (form fields `username` = email, `password`), `GET /api/auth/me`. In Swagger, use the **Authorize** button to log in.

In the browser the token is kept in `localStorage`, so a page refresh keeps you signed in. When the token expires, or the API rejects it, you are returned to the sign-in page. **Log out** signs out every open tab. Report downloads are fetched with the token, so they only work while signed in.

All `/api/imports` endpoints require a bearer token. Each import is linked to the user who uploaded it; history, details and report downloads only ever return the caller's own imports (another user's import ID behaves exactly like a nonexistent one: `404`). `/health` is public.

### Upgrading a database from before user accounts

On startup the server adds an owner column to existing databases automatically. Imports created before accounts existed have no owner: they are kept, but hidden from every user, and the server logs how many there are. To give them to an account, register it first, then from the `backend` directory:

```powershell
python -m app.assign_legacy --email you@example.com --dry-run   # show how many would move
python -m app.assign_legacy --email you@example.com
```

Only ownerless imports are changed; imports that already belong to someone are never reassigned.

## Current behavior and limitations

- Maximum upload: 5 MB, 10,000 rows; CSV must be UTF-8.
- Header names must be unique and nonempty.
- Every column is treated as required; duplicate detection compares complete trimmed rows within a single file.
- Import history stores parsed values in SQLite. **Do not upload sensitive or confidential files** in this initial local development version; imports are isolated per user, but data is not encrypted at rest and there are no retention controls, admin roles, password reset, or login rate limiting. Logging out removes the token from the browser, but a copied token stays valid until it expires.
- The browser displays the first 100 result rows; download the report for all rows.
- XLSX reads the active worksheet only. It does not evaluate formulas.
- No destination-database import, background queue, or custom validation rules yet.

## Planned enhancements (not yet implemented)

Configurable schemas and column mapping, preview/approval, streaming large files, asynchronous jobs with retries, idempotent destination imports, role-based access, audit logs, and scheduled integrations. Choose future work based on actual use and observed needs.

## Repository hygiene

`.gitignore` excludes virtual environments, node_modules, `backend/data/` (SQLite database and the generated signing secret), editor settings, and build outputs. For an evaluation archive, include the project source and `.gitignore` but exclude `.git`, dependencies, databases, and secrets.
