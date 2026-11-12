"""启动引导：迁移 → 运行时表 → 同步 fixtures 规则 → 空库建管理员。

幂等；不改动 `data/exam.db` 的既有题目/成绩，只新增运行时表与账号。
模板状态（启用/停用）由管理员通过 /api/admin/templates 控制，重跑 bootstrap 不覆盖。
"""
from __future__ import annotations

import sqlite3

import yaml

from app.config import (
    BOOTSTRAP_ADMIN_NAME,
    BOOTSTRAP_ADMIN_NO,
    BOOTSTRAP_ADMIN_PASSWORD,
    SPECIALTIES_YAML,
)
from app.db import connect, resolve_db_path
from app.db.migrate import upgrade
from app.rules.templates import expand_templates, template_config
from app.services import audit
from app.services.auth import hash_password
from app.services.db import write_tx
from app.services.util import json_dumps, now_iso

# 逐条执行（executescript 会隐式提交事务，不能放进 write_tx）
RUNTIME_DDL = [
    """CREATE TABLE IF NOT EXISTS app_sessions (
        token       TEXT PRIMARY KEY,
        user_id     INTEGER NOT NULL REFERENCES users(id),
        created_at  TEXT NOT NULL,
        expires_at  TEXT NOT NULL,
        revoked_at  TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS ix_app_sessions_user ON app_sessions (user_id)",
]


def ensure_schema() -> None:
    """先跑 migrations/*.sql（正向、幂等），再创建会话表。"""
    upgrade(resolve_db_path())
    conn = connect()
    try:
        with write_tx(conn):
            for statement in RUNTIME_DDL:
                conn.execute(statement)
    finally:
        conn.close()


def _sync_specialties(conn: sqlite3.Connection) -> int:
    data = yaml.safe_load(SPECIALTIES_YAML.read_text(encoding="utf-8"))
    rows = data.get("specialties", [])
    for s in rows:
        conn.execute(
            "INSERT INTO specialties (code, name, outline_l1, outline_l2, source_file_pattern, "
            "is_general, enabled, sort_order, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(code) DO UPDATE SET name = excluded.name, outline_l1 = excluded.outline_l1, "
            "outline_l2 = excluded.outline_l2, source_file_pattern = excluded.source_file_pattern, "
            "is_general = excluded.is_general, enabled = excluded.enabled, sort_order = excluded.sort_order",
            (s["code"], s["name"], s["outline_l1"], s["outline_l2"], s["source_file_pattern"],
             int(s["is_general"]), int(s["enabled"]), int(s.get("sort_order", 0)), s.get("note")),
        )
    return len(rows)


def _sync_templates(conn: sqlite3.Connection, at: str) -> int:
    data = template_config()
    pending = json_dumps(data.get("templates", [{}])[0].get("pending_confirmation", []))
    for t in expand_templates():
        bank = conn.execute("SELECT id FROM bank_versions WHERE code = ?", (t["bank_version"],)).fetchone()
        if bank is None:
            continue
        conn.execute(
            "INSERT INTO paper_templates (code, version, name, paper_type, bank_version_id, "
            "person_category, specialty_rule, required_question_ids, exclude_question_ids, exclude_rule, "
            "type_ratio, type_score, question_count, total_score, duration_min, pass_score, "
            "random_policy, short_answer_policy, status, pending_confirmation, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(code, version) DO UPDATE SET "
            "name = excluded.name, paper_type = excluded.paper_type, bank_version_id = excluded.bank_version_id, "
            "person_category = excluded.person_category, specialty_rule = excluded.specialty_rule, "
            "required_question_ids = excluded.required_question_ids, "
            "exclude_question_ids = excluded.exclude_question_ids, exclude_rule = excluded.exclude_rule, "
            "type_ratio = excluded.type_ratio, type_score = excluded.type_score, "
            "question_count = excluded.question_count, total_score = excluded.total_score, "
            "duration_min = excluded.duration_min, pass_score = excluded.pass_score, "
            "random_policy = excluded.random_policy, short_answer_policy = excluded.short_answer_policy, "
            "pending_confirmation = excluded.pending_confirmation",
            (
                t["code"], int(t["version"]), t["name"], t["paper_type"], bank["id"],
                t["person_category"], json_dumps(t["specialty_rule"]),
                json_dumps(t["required_question_ids"]), json_dumps(t["exclude_question_ids"]),
                json_dumps(t.get("exclude_rule") or {}), json_dumps(t["type_ratio"]),
                json_dumps(t["type_score"]), int(t["question_count"]), float(t["total_score"]),
                int(t["duration_min"]), float(t["pass_score"]), t["random_policy"],
                t["short_answer_policy"], t.get("status", "draft"), pending, at,
            ),
        )
    return len(expand_templates())


def ensure_admin(conn: sqlite3.Connection, at: str) -> int | None:
    if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
        return None
    cur = conn.execute(
        "INSERT INTO users (employee_no, name, password_hash, role, active, must_change_password, created_at) "
        "VALUES (?, ?, ?, 'admin', 1, 0, ?)",
        (BOOTSTRAP_ADMIN_NO, BOOTSTRAP_ADMIN_NAME, hash_password(BOOTSTRAP_ADMIN_PASSWORD), at),
    )
    audit.log(conn, BOOTSTRAP_ADMIN_NO, "user.bootstrap_admin", f"users:{cur.lastrowid}",
              after={"employee_no": BOOTSTRAP_ADMIN_NO, "role": "admin"}, at=at,
              detail="空库初始化；生产环境请用 EXAM_ADMIN_NO / EXAM_ADMIN_PASSWORD 覆盖")
    return int(cur.lastrowid)


def bootstrap() -> dict:
    ensure_schema()
    at = now_iso()
    conn = connect()
    try:
        with write_tx(conn):
            from app.services.question_sources import seed_legacy_sources
            seed_legacy_sources(conn)
            specialties = _sync_specialties(conn)
            templates = _sync_templates(conn, at)
            admin_id = ensure_admin(conn, at)
        return {"specialties": specialties, "templates": templates, "admin_id": admin_id, "at": at}
    finally:
        conn.close()
