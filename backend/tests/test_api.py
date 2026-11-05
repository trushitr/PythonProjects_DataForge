import io
import os
import tempfile
from pathlib import Path

os.environ["DATAFORGE_DATA_DIR"] = tempfile.mkdtemp(prefix="dataforge-test-")
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_health():
    assert client.get("/health").json() == {"status": "ok"}

def test_csv_upload_and_report():
    content = b"name,email\nAlice,a@example.com\nBob,b@example.com\nAlice,a@example.com\n,missing@example.com\n"
    res = client.post("/api/imports", files={"file": ("people.csv", content, "text/csv")})
    assert res.status_code == 201, res.text
    summary = res.json()
    assert (summary["total_rows"], summary["valid_rows"], summary["invalid_rows"], summary["duplicate_rows"]) == (4, 2, 2, 1)
    detail = client.get(f'/api/imports/{summary["id"]}')
    assert detail.status_code == 200
    assert len(detail.json()["results"]) == 4
    report = client.get(f'/api/imports/{summary["id"]}/report.csv')
    assert report.status_code == 200
    assert "Duplicate row" in report.text

def test_bad_extension():
    res = client.post("/api/imports", files={"file": ("a.exe", b"bad", "application/octet-stream")})
    assert res.status_code == 400

def test_missing_import():
    assert client.get("/api/imports/unknown").status_code == 404

def test_bad_headers():
    res = client.post("/api/imports", files={"file": ("a.csv", b"name,name\na,b\n", "text/csv")})
    assert res.status_code == 400

def test_xlsx():
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.append(["product", "quantity"])
    ws.append(["P1", 12])
    data = io.BytesIO()
    wb.save(data)
    res = client.post("/api/imports", files={"file": ("stock.xlsx", data.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert res.status_code == 201, res.text
    assert res.json()["valid_rows"] == 1
