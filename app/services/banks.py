"""题库版本与维度：前端下拉只从这里取值。"""
from __future__ import annotations

import sqlite3
from typing import Any

from app.models import PERSON_CATEGORIES, QTYPES
from app.rules.templates import expand_templates

PAPER_TYPES = [
    {"code": "practice", "name": "练习", "desc": "逐题即时反馈，可看解析"},
    {"code": "mock", "name": "模拟考试", "desc": "整卷计时，交卷后出分"},
    {"code": "formal", "name": "正式考试", "desc": "按场次参加，计入成绩"},
    {"code": "makeup", "name": "补考", "desc": "按场次补考"},
]


def list_banks(conn: sqlite3.Connection, *, all_statuses: bool = False) -> list[dict[str, Any]]:
    sql = """
        SELECT b.*, (SELECT COUNT(*) FROM question_versions qv
                     WHERE qv.bank_version_id = b.id AND qv.retired = 0) AS question_count
        FROM bank_versions b
    """
    if not all_statuses:
        sql += " WHERE b.status = 'active'"
    sql += " ORDER BY b.id"
    return [dict(r) for r in conn.execute(sql).fetchall()]


def dimension(conn: sqlite3.Connection, bank_id: Any) -> dict[str, Any]:
    try:
        bid = int(bank_id)
    except (TypeError, ValueError):
        from app.services.errors import not_found
        raise not_found("题库版本不存在")
    bank = conn.execute("SELECT * FROM bank_versions WHERE id = ?", (bid,)).fetchone()
    if bank is None:
        from app.services.errors import not_found
        raise not_found("题库版本不存在")

    counts = {r["qtype"]: r["n"] for r in conn.execute(
        "SELECT qtype, COUNT(*) AS n FROM question_versions "
        "WHERE bank_version_id = ? AND retired = 0 GROUP BY qtype", (bid,)
    ).fetchall()}
    specialties = [
        {
            "code": s["code"], "name": s["name"], "enabled": bool(s["enabled"]),
            "is_general": bool(s["is_general"]), "outline_l1": s["outline_l1"],
            "outline_l2": s["outline_l2"],
        }
        for s in conn.execute("SELECT * FROM specialties ORDER BY sort_order, code").fetchall()
    ]
    person_counts = {r["tag_value"]: r["n"] for r in conn.execute(
        "SELECT tag_value, COUNT(*) AS n FROM question_tags "
        "WHERE bank_version_id = ? AND tag_type = 'person_scope' GROUP BY tag_value", (bid,)
    ).fetchall()}
    templates = [
        {"id": t["id"], "code": t["code"], "person_category": t["person_category"],
         "paper_type": t["paper_type"], "status": t["status"]}
        for t in conn.execute(
            "SELECT * FROM paper_templates WHERE bank_version_id = ? AND substr(code,1,14) != '__system_free_' ORDER BY id", (bid,)
        ).fetchall()
    ]
    cfg = next((t for t in expand_templates(bank["code"])), {})
    return {
        "bank_id": bid,
        "code": bank["code"],
        "name": bank["name"],
        "status": bank["status"],
        "person_categories": [
            {"code": p, "name": p, "question_count": person_counts.get(p, 0)}
            for p in PERSON_CATEGORIES
        ],
        "specialties": specialties,
        "qtypes": [{"code": q, "name": q, "question_count": counts.get(q, 0)} for q in QTYPES],
        "protected": [
            {"code": "protected", "name": "保命题"},
            {"code": "required", "name": "标黄必考"},
        ],
        "paper_types": PAPER_TYPES,
        "templates": templates,
        "required_count": len(cfg.get("required_question_ids", [])),
        "exclude_count": len(cfg.get("exclude_question_ids", [])),
        "exclude_rule": cfg.get("exclude_rule") or {},
        "short_answer_policy": cfg.get("short_answer_policy", "excluded"),
        "question_count": sum(counts.values()),
    }
