"""管理员服务：用户/授权/版本/模板/场次/人工阅卷/审计，全部写 audit_logs。"""
from __future__ import annotations

import csv
import io
import json
import secrets
import sqlite3
from typing import Any

from app.models import BATCH_STATUSES, PERSON_CATEGORIES, ROLES, TEMPLATE_STATUSES
from app.rules.paper import read_snapshot
from app.services import audit, banks
from app.services.auth import actor_of, hash_password
from app.services.db import write_tx
from app.services.errors import bad_request, conflict, not_found
from app.services.exam import recompute_score
from app.services.util import fmt_num, json_dumps, now_iso

TEMP_PASSWORD_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"


def _row(conn: sqlite3.Connection, table: str, key: int) -> sqlite3.Row:
    row = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (key,)).fetchone()
    if row is None:
        raise not_found(f"{table} #{key} 不存在")
    return row


def _loads(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _user_payload(conn: sqlite3.Connection, user_id: int) -> dict[str, Any]:
    from app.services.auth import authorization_at, authorization_dto

    u = _row(conn, "users", user_id)
    return {
        "id": u["id"], "employee_no": u["employee_no"], "name": u["name"], "team": u["team"],
        "role": u["role"], "active": bool(u["active"]),
        "authorization": authorization_dto(authorization_at(conn, user_id)),
    }


def import_users(conn: sqlite3.Connection, actor: str, content: bytes) -> dict[str, Any]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise bad_request("CSV 必须是 UTF-8 编码")
    reader = csv.DictReader(io.StringIO(text))
    required = {"employee_no", "name"}
    if not reader.fieldnames or not required.issubset({f.strip() for f in reader.fieldnames if f}):
        raise bad_request("CSV 表头至少包含 employee_no,name（可选 team,role,initial_password）")
    created = updated = 0
    errors: list[dict[str, Any]] = []
    rows: list[tuple[int, dict[str, str]]] = []
    for line, raw in enumerate(reader, start=2):
        item = {str(k).strip(): (str(v).strip() if v is not None else "") for k, v in raw.items() if k}
        no, name = item.get("employee_no", ""), item.get("name", "")
        if not no or not name:
            errors.append({"line": line, "message": "employee_no / name 不能为空"})
            continue
        role = item.get("role") or "examinee"
        if role not in ROLES:
            errors.append({"line": line, "message": f"role 只能是 {ROLES}"})
            continue
        rows.append((line, {"employee_no": no, "name": name, "team": item.get("team", ""),
                            "role": role, "initial_password": item.get("initial_password", "")}))
    at = now_iso()
    with write_tx(conn):
        for line, item in rows:
            existing = conn.execute("SELECT * FROM users WHERE employee_no = ?", (item["employee_no"],)).fetchone()
            if existing is None and not item["initial_password"]:
                errors.append({"line": line, "message": "新用户缺少 initial_password"})
                continue
            pwd_hash = hash_password(item["initial_password"]) if item["initial_password"] else existing["password_hash"]
            if existing is None:
                conn.execute(
                    "INSERT INTO users (employee_no, name, password_hash, role, team, active, "
                    "must_change_password, created_at) VALUES (?, ?, ?, ?, ?, 1, 1, ?)",
                    (item["employee_no"], item["name"], pwd_hash, item["role"], item["team"], at),
                )
                created += 1
            else:
                conn.execute(
                    "UPDATE users SET name = ?, team = ?, role = ?, password_hash = ?, must_change_password = ?, "
                    "updated_at = ? WHERE id = ?",
                    (item["name"], item["team"], item["role"], pwd_hash,
                     1 if item["initial_password"] else existing["must_change_password"], at, existing["id"]),
                )
                updated += 1
        audit.log(conn, actor, "user.import", "users:csv", at=at,
                  after={"created": created, "updated": updated, "errors": len(errors)},
                  detail="CSV 批量导入用户名唯一账号")
    return {"created": created, "updated": updated, "errors": errors}


def list_users(conn: sqlite3.Connection, q: str | None = None) -> list[dict[str, Any]]:
    if q:
        rows = conn.execute(
            "SELECT id FROM users WHERE employee_no LIKE ? OR name LIKE ? ORDER BY id",
            (f"%{q}%", f"%{q}%"),
        ).fetchall()
    else:
        rows = conn.execute("SELECT id FROM users ORDER BY id").fetchall()
    return [_user_payload(conn, r["id"]) for r in rows]


def reset_password(conn: sqlite3.Connection, actor: str, user_id: int) -> dict[str, Any]:
    temp = "".join(secrets.choice(TEMP_PASSWORD_CHARS) for _ in range(12))
    at = now_iso()
    with write_tx(conn):
        before = dict(_row(conn, "users", user_id))
        conn.execute(
            "UPDATE users SET password_hash = ?, must_change_password = 1, updated_at = ? WHERE id = ?",
            (hash_password(temp), at, user_id),
        )
        audit.log(conn, actor, "user.reset_password", f"users:{user_id}", at=at,
                  before={"employee_no": before["employee_no"]},
                  after={"must_change_password": 1}, detail="临时密码仅本次返回，不落明文")
    return {"id": user_id, "temp_password": temp}


def set_user_active(conn: sqlite3.Connection, actor: str, user_id: int, active: Any) -> dict[str, Any]:
    value = int(bool(active))
    at = now_iso()
    with write_tx(conn):
        before = dict(_row(conn, "users", user_id))
        conn.execute("UPDATE users SET active = ?, updated_at = ? WHERE id = ?", (value, at, user_id))
        audit.log(conn, actor, "user.active", f"users:{user_id}", at=at,
                  before={"active": before["active"]}, after={"active": value})
    return _user_payload(conn, user_id)


def list_authorizations(conn: sqlite3.Connection, user_id: int) -> list[dict[str, Any]]:
    _row(conn, "users", user_id)
    return [dict(r) for r in conn.execute(
        "SELECT a.*, s.name AS specialty_name FROM person_authorizations a "
        "LEFT JOIN specialties s ON s.code = a.specialty_code "
        "WHERE a.user_id = ? ORDER BY a.effective_from DESC, a.id DESC", (user_id,)
    ).fetchall()]


def create_authorization(conn: sqlite3.Connection, actor: str, body: Any) -> dict[str, Any]:
    user_id = int(getattr(body, "user_id", 0) or 0)
    person = getattr(body, "person_category", None)
    basis = (getattr(body, "basis_doc", None) or "").strip()
    approver = (getattr(body, "approver", None) or "").strip()
    effective_from = (getattr(body, "effective_from", None) or "").strip()
    effective_to = getattr(body, "effective_to", None) or None
    specialty_code = getattr(body, "specialty_code", None) or None
    if not user_id or person not in PERSON_CATEGORIES:
        raise bad_request("user_id / person_category 不合法")
    if not basis or not approver or not effective_from:
        raise bad_request("授权必须填写依据文号(basis_doc)、审批人(approver)、生效期(effective_from)")
    if effective_to and str(effective_to) < effective_from:
        raise bad_request("effective_to 不能早于 effective_from")
    if specialty_code:
        if conn.execute("SELECT 1 FROM specialties WHERE code = ?", (specialty_code,)).fetchone() is None:
            raise bad_request("specialty_code 不存在")
    at = now_iso()
    # 时间衔接：旧授权截断到新授权生效日前一天，而不是撤销（历史授权仍可回溯）
    from datetime import date, timedelta
    try:
        cutoff = (date.fromisoformat(effective_from[:10]) - timedelta(days=1)).isoformat()
    except ValueError:
        raise bad_request("effective_from 必须是 YYYY-MM-DD 或 ISO 时间")
    with write_tx(conn):
        _row(conn, "users", user_id)
        previous = [dict(r) for r in conn.execute(
            "SELECT * FROM person_authorizations WHERE user_id = ? AND revoked_at IS NULL "
            "AND effective_from <= ? AND (effective_to IS NULL OR effective_to >= ?)",
            (user_id, effective_from, effective_from),
        ).fetchall()]
        if previous:
            conn.execute(
                "UPDATE person_authorizations SET effective_to = ? "
                "WHERE user_id = ? AND revoked_at IS NULL AND effective_from <= ? "
                "AND (effective_to IS NULL OR effective_to >= ?)",
                (cutoff, user_id, effective_from, effective_from),
            )
        cur = conn.execute(
            "INSERT INTO person_authorizations (user_id, person_category, specialty_code, effective_from, "
            "effective_to, basis_doc, approver, decided_at, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, person, specialty_code, effective_from, effective_to, basis, approver, at,
             getattr(body, "note", None)),
        )
        auth_id = int(cur.lastrowid)
        conn.execute("UPDATE users SET current_authorization_id = ?, updated_at = ? WHERE id = ?",
                     (auth_id, at, user_id))
        audit.log(conn, actor, "authorization.create", f"person_authorizations:{auth_id}", at=at,
                  before={"truncated": previous, "cutoff": cutoff},
                  after={"user_id": user_id, "person_category": person, "specialty_code": specialty_code,
                         "effective_from": effective_from, "effective_to": effective_to,
                         "basis_doc": basis, "approver": approver},
                  detail="改授权不改写历史成绩：attempts 已冻结人员类别/专业")
    return dict(_row(conn, "person_authorizations", auth_id))


def revoke_authorization(conn: sqlite3.Connection, actor: str, auth_id: int) -> dict[str, Any]:
    at = now_iso()
    with write_tx(conn):
        before = dict(_row(conn, "person_authorizations", auth_id))
        if before["revoked_at"]:
            raise conflict("该授权已撤销")
        conn.execute(
            "UPDATE person_authorizations SET revoked_at = ?, revoked_by = ? WHERE id = ?",
            (at, actor, auth_id),
        )
        audit.log(conn, actor, "authorization.revoke", f"person_authorizations:{auth_id}", at=at,
                  before={"revoked_at": None}, after={"revoked_at": at})
    return dict(_row(conn, "person_authorizations", auth_id))


def publish_bank(conn: sqlite3.Connection, actor: str, bank_id: int) -> dict[str, Any]:
    at = now_iso()
    with write_tx(conn):
        before = dict(_row(conn, "bank_versions", bank_id))
        others = [dict(r) for r in conn.execute(
            "SELECT id, code, status FROM bank_versions WHERE status = 'active' AND id != ?", (bank_id,)
        ).fetchall()]
        conn.execute("UPDATE bank_versions SET status = 'archived' WHERE status = 'active' AND id != ?", (bank_id,))
        conn.execute(
            "UPDATE bank_versions SET status = 'active', published_by = ? WHERE id = ?", (actor, bank_id)
        )
        audit.log(conn, actor, "bank.publish", f"bank_versions:{bank_id}", at=at,
                  before={"status": before["status"], "other_active": others},
                  after={"status": "active", "published_by": actor},
                  detail="历史 attempt 按锁卷快照判分，不随版本发布变化")
    return dict(_row(conn, "bank_versions", bank_id))


def list_templates(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute("SELECT * FROM paper_templates WHERE substr(code,1,14) != '__system_free_' ORDER BY id").fetchall()]


def set_template_status(conn: sqlite3.Connection, actor: str, template_id: int, status: str) -> dict[str, Any]:
    if status not in TEMPLATE_STATUSES:
        raise bad_request(f"status 只能是 {TEMPLATE_STATUSES}")
    at = now_iso()
    with write_tx(conn):
        before = dict(_row(conn, "paper_templates", template_id))
        if before["code"].startswith("__system_free_"):
            raise forbidden("自由组卷记录不能作为管理模板启用")
        conn.execute("UPDATE paper_templates SET status = ?, updated_at = ? WHERE id = ?",
                     (status, at, template_id))
        audit.log(conn, actor, "template.status", f"paper_templates:{template_id}", at=at,
                  before={"status": before["status"]}, after={"status": status},
                  detail="只影响新开考；已开考试卷按锁卷快照继续")
    return dict(_row(conn, "paper_templates", template_id))


def list_batches(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT b.*, t.name AS template_name, t.code AS template_code, bv.code AS bank_version_code "
        "FROM exam_batches b JOIN paper_templates t ON t.id = b.template_id "
        "JOIN bank_versions bv ON bv.id = b.bank_version_id ORDER BY b.id"
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["examinee_count"] = len(_loads(r["examinee_list"], []) or [])
        out.append(d)
    return out


def create_batch(conn: sqlite3.Connection, actor: str, body: Any) -> dict[str, Any]:
    code = (getattr(body, "code", None) or "").strip()
    template_id = int(getattr(body, "template_id", 0) or 0)
    bank_version_id = int(getattr(body, "bank_version_id", 0) or 0)
    start_at = getattr(body, "start_at", None)
    end_at = getattr(body, "end_at", None)
    max_attempts = int(getattr(body, "max_attempts", 1) or 1)
    allow_retake = int(bool(getattr(body, "allow_retake", False)))
    names = getattr(body, "examinee_list", None) or []
    status = getattr(body, "status", None) or "draft"
    if not code or not template_id or not bank_version_id:
        raise bad_request("code / template_id / bank_version_id 必填")
    if status not in BATCH_STATUSES:
        raise bad_request(f"status 只能是 {BATCH_STATUSES}")
    if max_attempts < 1:
        raise bad_request("max_attempts 至少为 1")
    if start_at and end_at and str(end_at) <= str(start_at):
        raise bad_request("end_at 必须晚于 start_at")
    if names and not isinstance(names, list):
        raise bad_request("examinee_list 必须是用户名数组")
    at = now_iso()
    with write_tx(conn):
        _row(conn, "paper_templates", template_id)
        _row(conn, "bank_versions", bank_version_id)
        cur = conn.execute(
            "INSERT INTO exam_batches (code, name, template_id, bank_version_id, start_at, end_at, "
            "max_attempts, allow_retake, examinee_list, status, created_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (code, getattr(body, "name", None) or code, template_id, bank_version_id, start_at, end_at,
             max_attempts, allow_retake, json_dumps(names), status, actor, at),
        )
        batch_id = int(cur.lastrowid)
        audit.log(conn, actor, "batch.create", f"exam_batches:{batch_id}", at=at,
                  after={"code": code, "template_id": template_id, "bank_version_id": bank_version_id,
                         "start_at": start_at, "end_at": end_at, "max_attempts": max_attempts,
                         "allow_retake": allow_retake, "examinee_list": names, "status": status})
    return dict(_row(conn, "exam_batches", batch_id))


def set_batch_status(conn: sqlite3.Connection, actor: str, batch_id: int, status: str) -> dict[str, Any]:
    if status not in BATCH_STATUSES:
        raise bad_request(f"status 只能是 {BATCH_STATUSES}")
    at = now_iso()
    with write_tx(conn):
        before = dict(_row(conn, "exam_batches", batch_id))
        conn.execute("UPDATE exam_batches SET status = ? WHERE id = ?", (status, batch_id))
        audit.log(conn, actor, "batch.status", f"exam_batches:{batch_id}", at=at,
                  before={"status": before["status"]}, after={"status": status})
    return dict(_row(conn, "exam_batches", batch_id))


def pending_grading(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT aa.*, a.submitted_at, a.paper_id, u.employee_no, u.name AS user_name "
        "FROM attempt_answers aa JOIN attempts a ON a.id = aa.attempt_id "
        "JOIN users u ON u.id = a.user_id WHERE aa.graded_result = 'pending' AND a.exam_mode = 'managed' "
        "ORDER BY a.submitted_at, aa.attempt_id, aa.stable_id"
    ).fetchall()
    cache: dict[int, dict[str, dict[str, Any]]] = {}
    out = []
    for r in rows:
        if r["paper_id"] not in cache:
            cache[r["paper_id"]] = {s["stable_id"]: s for s in read_snapshot(conn, r["paper_id"])}
        snap = cache[r["paper_id"]].get(r["stable_id"], {})
        out.append({
            "attempt_id": r["attempt_id"], "stable_id": r["stable_id"],
            "employee_no": r["employee_no"], "name": r["user_name"],
            "submitted_at": r["submitted_at"], "stem": snap.get("stem", ""),
            "user_text": r["user_answer"] or "", "reference": snap.get("answer", ""),
            "max_score": snap.get("score", 0), "score": r["score"],
        })
    return out


def grade_item(conn: sqlite3.Connection, actor: str, body: Any) -> dict[str, Any]:
    attempt_id = int(getattr(body, "attempt_id", 0) or 0)
    stable_id = getattr(body, "stable_id", None)
    score = float(getattr(body, "score", 0) or 0)
    comment = getattr(body, "comment", None)
    at = now_iso()
    with write_tx(conn):
        attempt = _row(conn, "attempts", attempt_id)
        if attempt["exam_mode"] == "self_service":
            raise conflict("自主测试由本人自评，不进入管理阅卷")
        snap = next((s for s in read_snapshot(conn, attempt["paper_id"]) if s["stable_id"] == stable_id), None)
        if snap is None:
            raise not_found("该考试卷内没有这道题")
        max_score = float(snap["score"])
        if score < 0 or score > max_score:
            raise bad_request(f"人工分必须在 0 ~ {fmt_num(max_score)} 之间")
        before = dict(conn.execute(
            "SELECT * FROM attempt_answers WHERE attempt_id = ? AND stable_id = ?", (attempt_id, stable_id)
        ).fetchone() or {})
        if before.get("graded_result") != "pending":
            raise conflict("该题不在待人工阅卷队列")
        audit_id = audit.log(conn, actor, "grading.manual", f"attempts:{attempt_id}/{stable_id}", at=at,
                             before={"score": before.get("score"), "result": before.get("graded_result")},
                             after={"score": score, "comment": comment},
                             detail="简答题人工阅卷 + 成绩更正审计")
        conn.execute(
            "UPDATE attempt_answers SET score = ?, graded_result = ?, graded_by = ?, graded_at = ? "
            "WHERE attempt_id = ? AND stable_id = ?",
            (score, "right" if score > 0 else "wrong", actor, at, attempt_id, stable_id),
        )
        conn.execute(
            "INSERT INTO grading_reviews (attempt_id, stable_id, reviewer, score, comment, corrected_at, audit_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (attempt_id, stable_id, actor, score, comment, at, audit_id),
        )
        total = recompute_score(conn, attempt_id, at=at)
    return {"attempt_id": attempt_id, "stable_id": stable_id, "score": score,
            "attempt_score": total, "reviewer": actor}


def list_audit(conn: sqlite3.Connection, limit: int = 200) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM audit_logs ORDER BY id DESC LIMIT ?", (max(1, min(1000, int(limit))),)
    ).fetchall()]
