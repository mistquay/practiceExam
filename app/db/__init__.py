"""SQLite 连接工厂（WAL + 外键 + busy_timeout）。

运行时与 ETL 共用；不依赖 ORM。数据库路径优先取参数，其次环境变量 EXAM_DB_PATH，
最后默认 <repo>/data/exam.db。
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = REPO_ROOT / "data" / "exam.db"


def resolve_db_path(path: str | os.PathLike | None = None) -> Path:
    if path:
        return Path(path)
    env = os.environ.get("EXAM_DB_PATH")
    return Path(env) if env else DEFAULT_DB_PATH


def connect(path: str | os.PathLike | None = None, *, busy_timeout_ms: int = 5000) -> sqlite3.Connection:
    """打开独立连接并设置 PRAGMA。返回的连接 row_factory=sqlite3.Row。

    同步 yield 依赖、路由和清理可能在不同线程池 worker 执行，因此允许
    连接在线程间顺序交接。连接仍由调用方独占并关闭，不能全局共享或并行使用；
    写操作继续通过 services.db.write_tx 串行化。
    """
    db_path = resolve_db_path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=busy_timeout_ms / 1000, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
    return conn
