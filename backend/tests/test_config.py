"""Startup configuration: JWT secret source and token lifetime.

These settings are read when the app module is imported, so each case runs in
a fresh Python process with its own environment and data directory.
"""
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]

def run_app(data_dir: Path, **env: str) -> subprocess.CompletedProcess:
    base = {k: v for k, v in os.environ.items() if not k.startswith("DATAFORGE_")}
    base.update({"DATAFORGE_DATA_DIR": str(data_dir), **env})
    code = "from app import auth; print(auth.JWT_SECRET); print(auth.TOKEN_MINUTES)"
    return subprocess.run([sys.executable, "-c", code], cwd=BACKEND, env=base, capture_output=True, text=True, timeout=60)

def test_generated_secret_is_saved_and_reused_across_restarts(tmp_path):
    first = run_app(tmp_path)
    assert first.returncode == 0, first.stderr
    assert "generated a signing secret" in first.stderr
    secret = first.stdout.splitlines()[0]
    assert len(secret) >= 32
    assert (tmp_path / "jwt_secret").read_text().strip() == secret

    second = run_app(tmp_path)  # e.g. uvicorn --reload restarting
    assert second.returncode == 0, second.stderr
    assert second.stdout.splitlines()[0] == secret
    assert "generated" not in second.stderr

def test_processes_starting_together_share_one_secret(tmp_path):
    # Like uvicorn --workers N: every worker must sign and verify with the same key.
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: run_app(tmp_path), range(6)))
    assert all(r.returncode == 0 for r in results), [r.stderr for r in results]
    assert len({r.stdout.splitlines()[0] for r in results}) == 1

def test_env_secret_takes_priority_and_no_file_is_written(tmp_path):
    result = run_app(tmp_path, DATAFORGE_JWT_SECRET="e" * 40)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[0] == "e" * 40
    assert not (tmp_path / "jwt_secret").exists()

def test_short_env_secret_rejected(tmp_path):
    result = run_app(tmp_path, DATAFORGE_JWT_SECRET="short")
    assert result.returncode != 0
    assert "at least 32 characters" in result.stderr

def test_corrupt_secret_file_gives_clear_error(tmp_path):
    (tmp_path / "jwt_secret").write_text("")
    result = run_app(tmp_path)
    assert result.returncode != 0
    assert "empty or too short" in result.stderr

def test_token_minutes_default_and_custom(tmp_path):
    assert run_app(tmp_path).stdout.splitlines()[1] == "60"
    assert run_app(tmp_path, DATAFORGE_TOKEN_MINUTES="15").stdout.splitlines()[1] == "15"

def test_invalid_token_minutes_rejected(tmp_path):
    for bad in ("0", "-5", "abc", "1.5", ""):
        result = run_app(tmp_path, DATAFORGE_TOKEN_MINUTES=bad)
        assert result.returncode != 0, bad
        assert "positive whole number" in result.stderr, bad
