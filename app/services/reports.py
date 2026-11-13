"""成绩导出：字段与库内聚合一一对应，及格率可复算；考生仅本人、管理员全体。"""
from __future__ import annotations

import csv
import io
import sqlite3
from typing import Any

from decimal import Decimal

from app.rules.paper import read_snapshot
from app.rules.templates import required_19
from app.services import audit
from app.services.auth import actor_of
from app.services.db import write_tx
from app.services.errors import bad_request, forbidden
from app.services.util import fmt_num, now_iso

EXPORT_FIELDS: list[tuple[str, str]] = [
    ("employee_no", "用户名"),
    ("name", "姓名"),
    ("team", "单位/班组"),
    ("person_category", "人员类别"),
    ("specialty_code", "专业"),
    ("bank_version", "题库版本"),
    ("template_code", "模板"),
    ("exam_mode", "成绩类型"),
    ("batch_code", "场次"),
    ("attempt_id", "卷号"),
    ("started_at", "开始时间"),
    ("submitted_at", "交卷时间"),
    ("score", "得分"),
    ("objective_score", "客观题得分"),
    ("objective_total", "客观部分满分"),
    ("self_score", "简答自评分"),
    ("total_score", "总分"),
    ("pass_score", "及格线"),
    ("passed", "是否及格"),
    ("required_score", "19必答得分"),
    ("manual_score", "简答人工分"),
    ("status", "状态"),
]


def rows_for(conn: sqlite3.Connection, user: dict | sqlite3.Row, *, batch_id: Any = None,
             user_id: Any = None) -> list[dict[str, Any]]:
    is_admin = user["role"] == "admin"
    if user_id not in (None, ""):
        try:
            uid = int(user_id)
        except (TypeError, ValueError):
            raise bad_request("user_id 必须是整数")
        if not is_admin and uid != int(user["id"]):
            raise forbidden("考生只能导出本人成绩")
    else:
        uid = None
    params: list[Any] = []
    sql = """
        SELECT a.*, u.employee_no, u.name AS user_name, u.team,
               p.total_score AS paper_total, p.snapshot_hash, t.code AS template_code,
               t.pass_score, bv.code AS bank_version, b.code AS batch_code
        FROM attempts a
        JOIN users u ON u.id = a.user_id
        JOIN papers p ON p.id = a.paper_id
        JOIN paper_templates t ON t.id = p.template_id
        JOIN bank_versions bv ON bv.id = p.bank_version_id
        LEFT JOIN exam_batches b ON b.id = a.batch_id
        WHERE 1 = 1
    """
    if not is_admin:
        sql += " AND a.user_id = ?"
        params.append(user["id"])
    if uid is not None:
        sql += " AND a.user_id = ?"
        params.append(uid)
    if batch_id not in (None, ""):
        sql += " AND a.batch_id = ?"
        params.append(int(batch_id))
    sql += " ORDER BY a.id"
    attempts = conn.execute(sql, params).fetchall()
    required = set(required_19())
    rows = []
    for a in attempts:
        ans = conn.execute(
            "SELECT aa.stable_id, aa.score, pq.is_required FROM attempt_answers aa "
            "JOIN paper_questions pq ON pq.paper_id = ? AND pq.stable_id = aa.stable_id "
            "WHERE aa.attempt_id = ?", (a["paper_id"], a["id"]),
        ).fetchall()
        required_score = sum(float(r["score"] or 0) for r in ans if r["is_required"] or r["stable_id"] in required)
        manual = conn.execute(
            "SELECT COALESCE(SUM(score), 0) FROM grading_reviews WHERE attempt_id = ?", (a["id"],)
        ).fetchone()[0]
        snaps = read_snapshot(conn, a["paper_id"])
        objective = {s["stable_id"] for s in snaps if s["qtype"] != "简答题"}
        objective_score = sum((Decimal(str(r["score"] or 0)) for r in ans if r["stable_id"] in objective), Decimal(0))
        objective_total = sum((Decimal(str(s["score"])) for s in snaps if s["stable_id"] in objective), Decimal(0))
        self_score = sum((Decimal(str(r[0] or 0)) for r in conn.execute(
            "SELECT score FROM attempt_answers WHERE attempt_id=? AND graded_by='self'", (a["id"],))), Decimal(0))
        rows.append({
            "objective_score": float(objective_score), "objective_total": float(objective_total),
            "self_score": float(self_score) if a["exam_mode"] == "self_service" else "",
            "employee_no": a["employee_no"], "name": a["user_name"], "team": a["team"] or "",
            "person_category": a["person_category_snapshot"] or "",
            "specialty_code": a["specialty_snapshot"] or "", "bank_version": a["bank_version"],
            "template_code": a["template_code"], "batch_code": a["batch_code"] or "",
            "attempt_id": a["id"], "started_at": a["started_at"],
            "submitted_at": a["submitted_at"] or "",
            "score": "" if a["exam_mode"] == "self_service" and a["status"] == "pending_review" else fmt_num(a["score"]), "total_score": fmt_num(a["paper_total"]),
            "exam_mode": "模拟考试" if a["exam_mode"] == "self_service" else "管理场次",
            "pass_score": fmt_num(a["pass_score_snapshot"] if a["pass_score_snapshot"] is not None else a["pass_score"]),
            "passed": "" if a["passed"] is None else ("是" if a["passed"] else "否"),
            "required_score": fmt_num(required_score), "manual_score": fmt_num(manual),
            "status": ("待评分" if a["status"] == "pending_review" else "已出分" if a["status"] == "graded" else a["status"]) if a["exam_mode"] == "self_service" else a["status"],
        })
    return rows


def pass_rate(rows: list[dict[str, Any]]) -> float | None:
    graded = [r for r in rows if r["passed"] in ("是", "否") and r.get("exam_mode") != "模拟考试"]
    if not graded:
        return None
    return round(sum(1 for r in graded if r["passed"] == "是") / len(graded), 6)


def csv_bytes(rows: list[dict[str, Any]]) -> bytes:
    buf = io.StringIO(newline="")
    writer = csv.writer(buf)
    writer.writerow([header for _, header in EXPORT_FIELDS])
    for row in rows:
        writer.writerow([row[key] for key, _ in EXPORT_FIELDS])
    # BOM 方便 Excel 直接打开 UTF-8
    return b"\xef\xbb\xbf" + buf.getvalue().encode("utf-8")


def export(conn: sqlite3.Connection, user: dict | sqlite3.Row, *, batch_id: Any = None,
           user_id: Any = None, fmt: str = "csv") -> tuple[bytes, str]:
    rows = rows_for(conn, user, batch_id=batch_id, user_id=user_id)
    with write_tx(conn):
        audit.log(conn, actor_of(user), "report.export", f"batch:{batch_id or 'all'}",
                  after={"rows": len(rows), "pass_rate": pass_rate(rows), "format": fmt},
                  detail="成绩导出留痕")
    if fmt == "json":
        from app.services.util import json_dumps
        payload = {"rows": rows, "total": len(rows), "pass_rate": pass_rate(rows),
                   "graded_count": sum(1 for r in rows if r["passed"] in ("是", "否") and r.get("exam_mode") != "模拟考试"),
                   "passed_count": sum(1 for r in rows if r["passed"] == "是" and r.get("exam_mode") != "模拟考试")}
        return json_dumps(payload).encode("utf-8"), "application/json; charset=utf-8"
    return csv_bytes(rows), "text/csv; charset=utf-8"
