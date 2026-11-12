"""登录即可练习全部 active 题库；参考答案仅进入练习 DTO，绝不写 attempts。

练习与考试隔离的是权限、DTO 和作答记录，不再通过隐藏考试池限制练习。
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from app.config import MAX_PRACTICE_COUNT
from app.rules.scoring import grade
from app.services.errors import bad_request, not_found
from app.services.util import now_iso

PRACTICE_DTO_KEYS = (
    "stable_id", "bank_id", "qtype", "stem", "options", "score", "answer",
    "analysis", "basis", "outline_l1", "outline_l2", "specialty_name", "is_required", "is_protected",
)


def _loads(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def active_bank_id(conn: sqlite3.Connection, bank_id: Any = None) -> int:
    if bank_id not in (None, ""):
        row = conn.execute("SELECT id, status FROM bank_versions WHERE id = ?", (int(bank_id),)).fetchone()
    else:
        row = conn.execute("SELECT id, status FROM bank_versions WHERE status = 'active' ORDER BY id LIMIT 1").fetchone()
    if row is None or row["status"] != "active":
        raise not_found("题库版本不存在或未发布")
    return int(row["id"])


def filters(conn: sqlite3.Connection, user, *, bank_id=None, **kwargs) -> dict:
    from app.services.practice_selection import selection, matching_count, tag_facets, source_facets
    bank = active_bank_id(conn, bank_id)
    sql, params = selection(conn, user, bank=bank, **kwargs)
    return {"bank_id": bank, "tags": tag_facets(conn, bank), "sources": source_facets(conn, bank),
            "total": matching_count(conn, sql, params)}


def questions(conn: sqlite3.Connection, user, *, bank_id=None, count: Any = 20,
              order: str = "sequence", **kwargs) -> dict:
    from app.services.practice_selection import selection, matching_count
    bank = active_bank_id(conn, bank_id)
    sql, params = selection(conn, user, bank=bank, order=order, **kwargs)
    try:
        limit = int(count)
    except (TypeError, ValueError):
        raise bad_request("题目数量必须是整数")
    if not 1 <= limit <= MAX_PRACTICE_COUNT:
        raise bad_request(f"题目数量需为 1~{MAX_PRACTICE_COUNT} 的整数")
    total = matching_count(conn, sql, params)
    sort = "RANDOM()" if order == "random" else "qv.id"
    # active stable_id 唯一，来源使用 EXISTS，避免 JOIN 放大和重复出题。
    rows = conn.execute("SELECT qv.*" + sql + " ORDER BY " + sort + " LIMIT ?", (*params, limit)).fetchall()
    out = []
    for row in rows:
        out.append({
            "stable_id": row["stable_id"], "bank_id": bank, "qtype": row["qtype"],
            "stem": row["stem"], "options": _loads(row["options"], {}),
            "score": row["score"], "answer": row["answer"], "analysis": row["analysis"],
            "basis": row["basis"], "outline_l1": row["outline_l1"], "outline_l2": row["outline_l2"],
            "specialty_name": None, "is_required": int(row["is_required"]),
            "is_protected": int(row["is_protected"]),
        })
    return {"questions": out, "total": total, "returned": len(out)}


def submit(conn: sqlite3.Connection, user: dict | sqlite3.Row, *, bank_id: Any,
           stable_id: str, user_answer: str | None) -> dict[str, Any]:
    bank = active_bank_id(conn, bank_id)
    row = conn.execute(
        "SELECT * FROM question_versions WHERE bank_version_id = ? AND stable_id = ? AND retired = 0",
        (bank, stable_id),
    ).fetchone()
    if row is None:
        raise not_found("题目不存在")
    snapshot = {
        "qtype": row["qtype"], "answer": row["answer"],
        "score": row["score"] if row["score"] is not None else 0.0,
    }
    earned, result = grade(snapshot, user_answer)
    is_correct = None if result == "pending" else bool(result == "right")
    at = now_iso()
    from app.services.db import write_tx

    with write_tx(conn):
        conn.execute(
            "INSERT INTO practice_records (user_id, stable_id, bank_version_id, question_version_id, "
            "user_answer, is_correct, answered_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user["id"], stable_id, bank, row["id"], user_answer or None,
             None if is_correct is None else int(is_correct), at),
        )
        if result in ("wrong", "blank"):
            conn.execute(
                "INSERT INTO wrong_book (user_id, stable_id, bank_version_id, wrong_count, last_wrong_at, last_answer) "
                "VALUES (?, ?, ?, 1, ?, ?) ON CONFLICT(user_id, stable_id) DO UPDATE SET "
                "wrong_count = wrong_count + 1, last_wrong_at = excluded.last_wrong_at, "
                "last_answer = excluded.last_answer, mastered = 0",
                (user["id"], stable_id, bank, at, user_answer or None),
            )
    return {
        "stable_id": stable_id, "is_correct": is_correct, "graded_result": result,
        "correct_answer": row["answer"], "answer": row["answer"],
        "analysis": row["analysis"], "basis": row["basis"],
        "earned": earned, "score": snapshot["score"],
    }
