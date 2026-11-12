"""练习筛选：固定白名单 + 参数化查询，与考试授权无关。"""
from __future__ import annotations

import sqlite3
from typing import Any

from app.models import QTYPES
from app.services.errors import bad_request

TAG_TYPES = {"color_group": "颜色分组", "data_issue": "数据标记"}


def tag_facets(conn: sqlite3.Connection, bank: int) -> list[dict]:
    items = [{"code": "protected", "name": "仅保命题"},
             {"code": "required", "name": "仅必答题"}]
    rows = conn.execute(
        "SELECT DISTINCT t.tag_type, t.tag_value FROM question_tags t "
        "JOIN question_versions q ON q.bank_version_id = t.bank_version_id "
        "AND q.stable_id = t.stable_id AND q.retired = 0 "
        "WHERE t.bank_version_id = ? AND t.tag_type IN ('color_group', 'data_issue') "
        "ORDER BY t.tag_type, t.tag_value", (bank,))
    items.extend({"code": r["tag_type"] + ":" + r["tag_value"],
                  "name": TAG_TYPES[r["tag_type"]] + "：" + r["tag_value"]} for r in rows)
    return items


def source_facets(conn: sqlite3.Connection, bank: int) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT sf.id, sf.rel_path, sf.sheet, COUNT(DISTINCT q.stable_id) AS question_count "
        "FROM source_files sf JOIN question_sources qs ON qs.source_file_id = sf.id "
        "AND qs.bank_version_id = sf.bank_version_id "
        "JOIN question_versions q ON q.bank_version_id = qs.bank_version_id "
        "AND q.stable_id = qs.stable_id AND q.retired = 0 "
        "WHERE sf.bank_version_id = ? GROUP BY sf.id ORDER BY sf.rel_path, sf.sheet", (bank,))]


def selection(conn: sqlite3.Connection, user, *, bank: int,
              person_category: str | None = None, specialty_code: str | None = None,
              qtype: str | None = None, source: str = "all", order: str = "sequence",
              tags: list[str] | None = None, source_file_ids: list[int] | None = None):
    # 人员类别由用户选择，绝不读取当前用户授权。专业旧参数仍保持兼容。
    from app.models import PERSON_CATEGORIES
    if person_category and person_category not in PERSON_CATEGORIES:
        raise bad_request("无效人员类别")
    if order not in ("sequence", "random") or source not in ("all", "wrong", "unpracticed"):
        raise bad_request("无效的练习顺序或题目范围")
    if qtype and qtype not in QTYPES:
        raise bad_request("无效题型")
    tags = list(dict.fromkeys(tags or []))
    source_file_ids = list(dict.fromkeys(source_file_ids or []))
    if not set(tags) <= {t["code"] for t in tag_facets(conn, bank)}:
        raise bad_request("无效标签")
    if source_file_ids:
        valid = {r[0] for r in conn.execute("SELECT id FROM source_files WHERE bank_version_id = ?", (bank,))}
        if not set(source_file_ids) <= valid:
            raise bad_request("来源不存在或不属于当前题库")
    sql = " FROM question_versions qv WHERE qv.bank_version_id = ? AND qv.retired = 0"
    params: list[Any] = [bank]
    if person_category:
        sql += (" AND EXISTS (SELECT 1 FROM question_tags t WHERE "
                "t.bank_version_id = qv.bank_version_id AND t.stable_id = qv.stable_id "
                "AND t.tag_type = 'person_scope' AND t.tag_value = ?)")
        params.append(person_category)
    for tag in tags:
        if tag in ("protected", "required"):
            sql += " AND qv.is_" + tag + " = 1"  # 固定白名单列名
        else:
            kind, value = tag.split(":", 1)
            sql += (" AND EXISTS (SELECT 1 FROM question_tags t WHERE "
                    "t.bank_version_id = qv.bank_version_id AND t.stable_id = qv.stable_id "
                    "AND t.tag_type = ? AND t.tag_value = ?)")
            params.extend([kind, value])
    if source_file_ids:
        marks = ",".join("?" for _ in source_file_ids)
        sql += (" AND EXISTS (SELECT 1 FROM question_sources qs WHERE "
                "qs.bank_version_id = qv.bank_version_id AND qs.stable_id = qv.stable_id "
                "AND qs.source_file_id IN (" + marks + "))")
        params.extend(source_file_ids)
    if qtype:
        sql += " AND qv.qtype = ?"
        params.append(qtype)
    if source == "wrong":
        sql += " AND EXISTS (SELECT 1 FROM wrong_book w WHERE w.user_id = ? AND w.stable_id = qv.stable_id)"
        params.append(user["id"])
    elif source == "unpracticed":
        sql += " AND NOT EXISTS (SELECT 1 FROM practice_records p WHERE p.user_id = ? AND p.stable_id = qv.stable_id)"
        params.append(user["id"])
    return sql, params


def matching_count(conn, sql, params) -> int:
    return conn.execute("SELECT COUNT(*)" + sql, params).fetchone()[0]
