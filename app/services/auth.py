"""工号 + bcrypt 密码 + HttpOnly Cookie Session；当前有效授权查询。"""
from __future__ import annotations

import secrets
import sqlite3
from datetime import timedelta
from typing import Any

import bcrypt

from app.config import SESSION_TTL_SECONDS
from app.services.util import (
    add_minutes,
    instant_ge,
    instant_le,
    now_iso,
    parse_instant,
    utcnow,
)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=10)).decode("ascii")


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("ascii"))
    except (ValueError, TypeError):
        return False


def create_session(conn: sqlite3.Connection, user_id: int) -> tuple[str, str]:
    token = secrets.token_urlsafe(36)
    now = utcnow()
    expires = add_minutes(now, SESSION_TTL_SECONDS / 60)
    conn.execute(
        "INSERT INTO app_sessions (token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (token, user_id, now_iso(), expires),
    )
    return token, expires


def revoke_session(conn: sqlite3.Connection, token: str) -> None:
    conn.execute("UPDATE app_sessions SET revoked_at = ? WHERE token = ?", (now_iso(), token))


def user_by_token(conn: sqlite3.Connection, token: str | None) -> sqlite3.Row | None:
    if not token:
        return None
    now = utcnow()
    row = conn.execute(
        "SELECT u.*, s.token AS session_token, s.expires_at FROM app_sessions s "
        "JOIN users u ON u.id = s.user_id "
        "WHERE s.token = ? AND s.revoked_at IS NULL AND u.active = 1",
        (token,),
    ).fetchone()
    if row is None:
        return None
    expires = parse_instant(row["expires_at"])
    if expires is not None and expires <= now:
        return None
    return row


def login(conn: sqlite3.Connection, employee_no: str, password: str) -> sqlite3.Row | None:
    row = conn.execute(
        "SELECT * FROM users WHERE employee_no = ? AND active = 1", (employee_no.strip(),)
    ).fetchone()
    if row is None or not verify_password(password, row["password_hash"]):
        return None
    return row


def authorization_at(conn: sqlite3.Connection, user_id: int, at=None) -> sqlite3.Row | None:
    """取 `at` 时点生效的最新一条授权；带生效期、依据与审批人。"""
    moment = at or utcnow()
    rows = conn.execute(
        "SELECT a.*, s.name AS specialty_name FROM person_authorizations a "
        "LEFT JOIN specialties s ON s.code = a.specialty_code "
        "WHERE a.user_id = ? AND a.revoked_at IS NULL ORDER BY a.effective_from DESC, a.id DESC",
        (user_id,),
    ).fetchall()
    for row in rows:
        if instant_le(row["effective_from"], moment) and instant_ge(
            row["effective_to"], moment, end_of_day=True
        ):
            return row
    return None


def authorization_dto(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    return {
        "id": row["id"],
        "person_category": row["person_category"],
        "specialty_code": row["specialty_code"],
        "specialty_name": row["specialty_name"],
        "effective_from": row["effective_from"],
        "effective_to": row["effective_to"],
        "basis_doc": row["basis_doc"],
        "approver": row["approver"],
        "decided_at": row["decided_at"],
        "revoked_at": row["revoked_at"],
    }


def user_dto(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    auth = authorization_at(conn, row["id"])
    return {
        "id": row["id"],
        "employee_no": row["employee_no"],
        "name": row["name"],
        "role": row["role"],
        "team": row["team"],
        "active": bool(row["active"]),
        "must_change_password": bool(row["must_change_password"]),
        # 当前有效授权（不是账号上的静态字段）
        "authorization": authorization_dto(auth),
    }


def actor_of(user: dict | sqlite3.Row) -> str:
    """审计 actor 一律用真实工号，禁止匿名。"""
    return str(user["employee_no"])
