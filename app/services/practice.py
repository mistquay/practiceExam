"""练习专用服务：独立 DTO（可含参考答案/解析）+ 独立题目池，绝不写 attempts。

池隔离：练习池排除「考试严格题目池」(person_scope ∩ 通用/自选专业)，
因此练习接口无法批量取到考试卷可能使用的题目答案。
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from app.config import MAX_PRACTICE_COUNT
from app.models import QTYPE_SHORT
from app.rules.scoring import grade
from app.rules.templates import required_19
from app.services.errors import bad_request, not_found
from app.services.util import now_iso

PRACTICE_DTO_KEYS = (
    "stable_id", "bank_id", "qtype", "stem", "options", "score", "answer",
    "analysis", "basis", "outline_l1", "outline_l2", "specialty_name", "is_required",
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


def exam_pool_ids(conn: sqlite3.Connection, bank_version_id: int, person_category: str | None) -> set[str]:
    sql = """
        SELECT qv.stable_id,
               MAX(CASE WHEN qt.tag_type = 'person_scope' AND qt.tag_value = ? THEN 1 ELSE 0 END) AS p,
               MAX(CASE WHEN qt.tag_type = 'specialty' AND qt.tag_value IN ('pd_general', 'pd_live') THEN 1 ELSE 0 END) AS s
        FROM question_versions qv
        JOIN question_tags qt ON qt.bank_version_id = qv.bank_version_id AND qt.stable_id = qv.stable_id
        WHERE qv.bank_version_id = ? AND qv.retired = 0
        GROUP BY qv.stable_id
        HAVING p = 1 AND s = 1
    """
    return {r["stable_id"] for r in conn.execute(sql, (person_category or "", bank_version_id)).fetchall()}


def questions(conn: sqlite3.Connection, user: dict | sqlite3.Row, *, bank_id: Any = None,
              person_category: str | None = None, specialty_code: str | None = None,
              qtype: str | None = None, count: Any = 20, order: str = "sequence",
              source: str = "all") -> list[dict[str, Any]]:
    bank = active_bank_id(conn, bank_id)
    try:
        limit = int(count)
    except (TypeError, ValueError):
        limit = 20
    limit = max(1, min(MAX_PRACTICE_COUNT, limit))
    excluded = exam_pool_ids(conn, bank, person_category) | set(required_19())
    if source == "wrong":
        # 错题重练：本人在考试中答错、已出分的题可以回练（不再隔离，成绩已定）
        excluded = set(required_19())
    params: list[Any] = [bank]
    sql = """
        SELECT qv.*, sp.name AS specialty_name
        FROM question_versions qv
        LEFT JOIN question_tags qt ON qt.bank_version_id = qv.bank_version_id AND qt.stable_id = qv.stable_id
        LEFT JOIN specialties sp ON sp.code = qt.tag_value AND qt.tag_type = 'specialty'
        WHERE qv.bank_version_id = ? AND qv.retired = 0
    """
    def tag_exists(tag_type: str, value: str) -> str:
        return ("EXISTS (SELECT 1 FROM question_tags t WHERE t.bank_version_id = qv.bank_version_id "
                "AND t.stable_id = qv.stable_id AND t.tag_type = '" + tag_type + "' AND t.tag_value = ?)")

    if specialty_code and person_category:
        # 两个维度是「范围」不是「交集」：题目属于该专业或该人员公共题范围即可
        sql += " AND (" + tag_exists("specialty", specialty_code) + " OR " + tag_exists("person_scope", person_category) + ")"
        params.extend([specialty_code, person_category])
    elif specialty_code:
        sql += " AND " + tag_exists("specialty", specialty_code)
        params.append(specialty_code)
    elif person_category:
        sql += " AND " + tag_exists("person_scope", person_category)
        params.append(person_category)
    if qtype:
        sql += " AND qv.qtype = ?"
        params.append(qtype)
    if source == "wrong":
        sql += (" AND qv.stable_id IN (SELECT stable_id FROM wrong_book WHERE user_id = ?)")
        params.append(user["id"])
    elif source == "unpracticed":
        sql += (" AND qv.stable_id NOT IN (SELECT stable_id FROM practice_records WHERE user_id = ?)")
        params.append(user["id"])
    sql += " GROUP BY qv.id ORDER BY " + ("RANDOM()" if order == "random" else "qv.id")
    rows = [dict(r) for r in conn.execute(sql, params).fetchall() if r["stable_id"] not in excluded]
    out = []
    seen = set()
    for row in rows:
        if row["stable_id"] in seen:
            continue
        seen.add(row["stable_id"])
        out.append({
            "stable_id": row["stable_id"], "bank_id": bank, "qtype": row["qtype"],
            "stem": row["stem"], "options": _loads(row["options"], {}),
            "score": row["score"], "answer": row["answer"], "analysis": row["analysis"],
            "basis": row["basis"], "outline_l1": row["outline_l1"], "outline_l2": row["outline_l2"],
            "specialty_name": row["specialty_name"], "is_required": int(row["is_required"]),
        })
        if len(out) >= limit:
            break
    return out


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
