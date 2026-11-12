"""审计日志：任何管理动作 / 导入 / 出分 / 导出都必须有非空 actor。"""
from __future__ import annotations

import sqlite3
from typing import Any

from app.services.util import json_dumps, now_iso


def log(
    conn: sqlite3.Connection,
    actor: str,
    action: str,
    target: str | None = None,
    *,
    before: Any = None,
    after: Any = None,
    detail: str | None = None,
    at: str | None = None,
) -> int:
    if not actor or not str(actor).strip():
        raise ValueError("audit_logs.actor 不允许为空（0 条匿名写）")
    before_value = None if before is None else json_dumps(before)
    after_value = None if after is None else json_dumps(after)
    cur = conn.execute(
        "INSERT INTO audit_logs (actor, action, target, at, before_value, after_value, detail) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (str(actor), action, target, at or now_iso(), before_value, after_value, detail),
    )
    return int(cur.lastrowid)


def anonymous_count(conn: sqlite3.Connection) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM audit_logs WHERE actor IS NULL OR trim(actor) = ''"
    ).fetchone()[0]
