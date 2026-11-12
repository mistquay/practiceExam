"""真实 TCP/Uvicorn 回归；只使用临时种子库的副本，不访问 data/exam.db。"""
from __future__ import annotations

import os
import secrets
import socket
import sqlite3
import subprocess
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.db import connect  # noqa: E402
from app.db.migrate import upgrade  # noqa: E402
from app.routers.deps import get_conn  # noqa: E402
from app.services.db import write_tx  # noqa: E402


def test_request_connections_allow_sequential_worker_handoff(tmp_path, monkeypatch):
    db = tmp_path / "handoff.db"
    monkeypatch.setenv("EXAM_DB_PATH", str(db))
    first, second = get_conn(), get_conn()
    conn, other = next(first), next(second)
    try:
        assert conn is not other
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        # TEMP 表只属于单一连接，证明不是共享的全局连接。
        conn.execute("CREATE TEMP TABLE request_only (value INTEGER)")
        assert not other.execute(
            "SELECT name FROM sqlite_temp_master WHERE name = 'request_only'"
        ).fetchall()

        def use_on_another_worker():
            with write_tx(conn):
                conn.execute("INSERT INTO request_only VALUES (1)")
            with pytest.raises(ValueError, match="rollback"):
                with write_tx(conn):
                    conn.execute("INSERT INTO request_only VALUES (2)")
                    raise ValueError("rollback")
            return conn.execute("SELECT SUM(value) FROM request_only").fetchone()[0]

        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(use_on_another_worker).result() == 1
            # yield 依赖的 finally 也可以在非创建线程执行。
            pool.submit(first.close).result()
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            conn.execute("SELECT 1")
        assert other.execute("SELECT 1").fetchone()[0] == 1
    finally:
        first.close()
        second.close()


@pytest.fixture
def live_server(tmp_path):
    seed = tmp_path / "seed.db"
    upgrade(seed)
    with connect(seed) as conn:
        conn.execute("INSERT INTO bank_versions (code, name, status) "
                     "VALUES ('thread-regression', '回归题库', 'active')")
    conn.close()
    db = tmp_path / "server-copy.db"
    with sqlite3.connect(seed) as source, sqlite3.connect(db) as target:
        source.backup(target)
    source.close()
    target.close()

    # 使用独立随机端口，绝不连接或停止用户的 8792 服务。
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    assert port != 8792
    url = f"http://127.0.0.1:{port}"
    employee_no = "uvicorn-regression"
    password = secrets.token_urlsafe(24)
    env = dict(os.environ, EXAM_DB_PATH=str(db), EXAM_ADMIN_NO=employee_no,
               EXAM_ADMIN_PASSWORD=password, EXAM_COOKIE_SECURE="0",
               EXAM_SESSION_COOKIE="exam_session")
    log_path = tmp_path / "uvicorn.log"
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [sys.executable, "-X", "utf8", "-m", "uvicorn", "app.main:app",
             "--host", "127.0.0.1", "--port", str(port), "--no-access-log"],
            cwd=REPO_ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 30
            with httpx.Client(base_url=url, timeout=1, trust_env=False) as client:
                while time.monotonic() < deadline:
                    assert process.poll() is None, "测试 Uvicorn 提前退出"
                    try:
                        if client.get("/").status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(0.1)
                else:
                    pytest.fail("测试 Uvicorn 启动超时")
            yield url, employee_no, password
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
    output = log_path.read_text(encoding="utf-8")
    assert "ProgrammingError" not in output, "Uvicorn 日志含 SQLite ProgrammingError"
    assert "Exception in ASGI application" not in output, "Uvicorn 日志含 ASGI 异常"


def test_real_uvicorn_repeated_login_and_concurrent_reads(live_server):
    url, employee_no, password = live_server
    workers, rounds = 12, 4
    barrier = Barrier(workers)

    def exercise(synchronize=False):
        statuses = Counter()
        with httpx.Client(base_url=url, timeout=60, trust_env=False) as client:
            if synchronize:
                barrier.wait(timeout=30)
            for _ in range(rounds):
                response = client.post("/api/auth/login", json={
                    "employee_no": employee_no, "password": password,
                })
                # 不输出请求体、Cookie 或登录响应，避免测试失败时泄漏凭据。
                assert response.status_code == 200, ("login", response.status_code)
                statuses[("login", response.status_code)] += 1
                for path, key in (("/api/banks", "banks"), ("/api/auth/me", "id"),
                                  ("/api/exams", "exams")):
                    response = client.get(path)
                    assert response.status_code == 200, (path, response.status_code)
                    assert key in response.json(), path
                    if path == "/api/banks":
                        assert response.json()[key], "回归题库未加载"
                    statuses[(path, response.status_code)] += 1
                response = client.post("/api/auth/logout")
                assert response.status_code == 200, ("logout", response.status_code)
                statuses[("logout", response.status_code)] += 1
                response = client.get("/api/auth/me")
                assert response.status_code == 401, ("logged-out me", response.status_code)
                statuses[("logged-out me", response.status_code)] += 1
        return statuses

    total = exercise()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for result in pool.map(exercise, [True] * workers):
            total.update(result)
    assert total == Counter({
        ("login", 200): 52, ("/api/banks", 200): 52, ("/api/auth/me", 200): 52,
        ("/api/exams", 200): 52, ("logout", 200): 52, ("logged-out me", 401): 52,
    })
