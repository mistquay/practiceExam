"""个人记录：错题本 / 练习记录（服务端持久化，按 user_id 严格隔离）。"""
from __future__ import annotations

import sqlite3
from typing import Any

from app.services.errors import not_found
from app.services.db import write_tx


def list_wrong(conn: sqlite3.Connection, user: dict | sqlite3.Row,
               bank_id: Any = None) -> list[dict[str, Any]]:
    params: list[Any] = [user["id"]]
    sql = """
        SELECT w.*, qv.stem, qv.qtype FROM wrong_book w
        LEFT JOIN question_versions qv
               ON qv.stable_id = w.stable_id AND qv.bank_version_id = w.bank_version_id AND qv.retired = 0
        WHERE w.user_id = ?
    """
    if bank_id not in (None, ""):
        sql += " AND w.bank_version_id = ?"
        params.append(int(bank_id))
    sql += " ORDER BY w.last_wrong_at DESC, w.stable_id"
    return [
        {
            "stable_id": r["stable_id"], "stem": r["stem"], "qtype": r["qtype"],
            "wrong_count": r["wrong_count"], "last_wrong_at": r["last_wrong_at"],
            "last_answer": r["last_answer"], "mastered": bool(r["mastered"]),
            "bank_version_id": r["bank_version_id"],
        }
        for r in conn.execute(sql, params).fetchall()
    ]


def remove_wrong(conn: sqlite3.Connection, user: dict | sqlite3.Row, stable_id: str) -> None:
    with write_tx(conn):
        cur = conn.execute(
            "DELETE FROM wrong_book WHERE user_id = ? AND stable_id = ?", (user["id"], stable_id)
        )
    if cur.rowcount == 0:
        raise not_found("错题记录不存在")


def clear_wrong(conn: sqlite3.Connection, user: dict | sqlite3.Row) -> int:
    with write_tx(conn):
        cur = conn.execute("DELETE FROM wrong_book WHERE user_id = ?", (user["id"],))
    return cur.rowcount


def list_practice(conn: sqlite3.Connection, user: dict | sqlite3.Row, *,
                  limit: int = 50, offset: int = 0) -> dict[str, Any]:
    limit = max(1, min(200, int(limit)))
    offset = max(0, int(offset))
    total = conn.execute(
        "SELECT COUNT(*) FROM practice_records WHERE user_id = ?", (user["id"],)
    ).fetchone()[0]
    rows = conn.execute(
        "SELECT p.*, qv.stem, qv.qtype FROM practice_records p "
        "LEFT JOIN question_versions qv ON qv.id = p.question_version_id "
        "WHERE p.user_id = ? ORDER BY p.answered_at DESC, p.id DESC LIMIT ? OFFSET ?",
        (user["id"], limit, offset),
    ).fetchall()
    records = [
        {
            "id": r["id"], "stable_id": r["stable_id"], "stem": r["stem"], "qtype": r["qtype"],
            "user_answer": r["user_answer"],
            "is_correct": None if r["is_correct"] is None else bool(r["is_correct"]),
            "answered_at": r["answered_at"],
        }
        for r in rows
    ]
    return {"records": records, "total": total, "limit": limit, "offset": offset}
