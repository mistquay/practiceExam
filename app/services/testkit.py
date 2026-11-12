"""测试专用工具（生产代码不调用）：WAL 安全复制题库、建账号/授权/模板/场次。

放在 app/services/ 下是因为本次改动只允许新增 tests/test_api.py 与
tests/test_integration.py 两个测试文件，不能新增 tests/conftest.py。
"""
from __future__ import annotations

import csv
import io
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.db import REPO_ROOT

SOURCE_DB = REPO_ROOT / "data" / "exam.db"
ADMIN_NO = "admin"
ADMIN_PASSWORD = "Admin@12345"


def clone_db(destination: Path) -> Path:
    """用 sqlite3 backup API 复制，WAL 已 checkpoint，绝不直接 copy .db。"""
    if not SOURCE_DB.is_file():
        raise FileNotFoundError(f"缺少题库数据库 {SOURCE_DB}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(SOURCE_DB)
    dst = sqlite3.connect(destination)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return destination


def prepare(tmp_path: Path, monkeypatch: Any) -> Path:
    db = clone_db(tmp_path / "exam.db")
    monkeypatch.setenv("EXAM_DB_PATH", str(db))
    monkeypatch.setenv("EXAM_ADMIN_NO", ADMIN_NO)
    monkeypatch.setenv("EXAM_ADMIN_PASSWORD", ADMIN_PASSWORD)
    from app.services.bootstrap import bootstrap
    bootstrap()
    return db


def client():
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


def login(c, employee_no: str, password: str) -> dict:
    r = c.post("/api/auth/login", json={"employee_no": employee_no, "password": password})
    assert r.status_code == 200, (r.status_code, r.text[:300])
    return r.json()


def admin(c) -> dict:
    return login(c, ADMIN_NO, ADMIN_PASSWORD)


def csv_bytes(rows: list[dict[str, str]]) -> bytes:
    fields = ["employee_no", "name", "team", "role", "initial_password"]
    buf = io.StringIO(newline="")
    writer = csv.DictWriter(buf, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        writer.writerow({k: row.get(k, "") for k in fields})
    return buf.getvalue().encode("utf-8")


def import_users(c, rows: list[dict[str, str]]) -> dict:
    r = c.post("/api/admin/users/import",
               files={"file": ("users.csv", csv_bytes(rows), "text/csv")})
    assert r.status_code == 200, (r.status_code, r.text[:300])
    assert not r.json()["errors"], r.json()["errors"]
    return r.json()


def authorize(c, user_id: int, person: str = "重点人员", specialty: str = "pd_live",
              *, effective_from: str = "2020-01-01") -> dict:
    r = c.post("/api/admin/authorizations", json={
        "user_id": user_id, "person_category": person, "specialty_code": specialty,
        "effective_from": effective_from, "effective_to": None,
        "basis_doc": "安监〔2026〕11号岗位清单", "approver": "张工",
    })
    assert r.status_code == 200, (r.status_code, r.text[:300])
    return r.json()


def activate_templates(c) -> list[int]:
    r = c.get("/api/admin/templates")
    assert r.status_code == 200, r.text[:300]
    ids = []
    for t in r.json()["templates"]:
        p = c.patch(f"/api/admin/templates/{t['id']}", json={"status": "active"})
        assert p.status_code == 200, p.text[:300]
        ids.append(t["id"])
    return ids


def open_batch(c, template_id: int, bank_version_id: int, code: str = "T-BATCH",
               *, max_attempts: int = 3, allow_retake: bool = True,
               examinee_list: list[str] | None = None,
               start_minutes_ago: int = 60, end_minutes_ahead: int = 60 * 24) -> int:
    now = datetime.now(timezone.utc)
    payload = {
        "code": code, "template_id": template_id, "bank_version_id": bank_version_id,
        "start_at": (now - timedelta(minutes=start_minutes_ago)).isoformat(timespec="seconds"),
        "end_at": (now + timedelta(minutes=end_minutes_ahead)).isoformat(timespec="seconds"),
        "max_attempts": max_attempts, "allow_retake": allow_retake,
        "examinee_list": examinee_list or [], "status": "open",
    }
    r = c.post("/api/admin/batches", json=payload)
    assert r.status_code == 200, (r.status_code, r.text[:300])
    return int(r.json()["id"])


def connect(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    return conn


def active_bank_id(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT id FROM bank_versions WHERE status='active' ORDER BY id LIMIT 1").fetchone()[0]
