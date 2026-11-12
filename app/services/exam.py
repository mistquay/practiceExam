"""考试过程：场次校验 → 锁卷 → 服务端时间 → 服务端判分 → 成绩留痕。

练习与考试严格隔离：本模块从不 import practice 模块的 DTO，且所有下发给
/api/exams/* 的字段都经过 app.rules.paper.question_dto / app.services.dto 白名单。
"""
from __future__ import annotations

import json
import secrets
import sqlite3
from typing import Any, Iterable

from app.config import EXAM_AUTO_SUBMIT_ON_DEADLINE
from decimal import Decimal

from app.models import PERSON_CATEGORIES, QTYPE_SHORT
from app.rules.paper import (
    allowed_specialties,
    question_dto,
    read_snapshot,
    replay_paper,
    template_for,
)
from app.rules.scoring import grade
from app.services import audit
from app.services.auth import actor_of
from app.services.db import write_tx
from app.services.dto import safe_analysis
from app.services.errors import bad_request, conflict, forbidden, not_found
from app.services.util import add_minutes, fmt_num, json_dumps, now_iso, parse_instant, utcnow


def _loads(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _specialty(conn: sqlite3.Connection, template: dict[str, Any], requested: str | None) -> str:
    allowed = allowed_specialties(template)
    if not requested:
        raise bad_request("请选择专业；当前模板未配置专业时无法开始")
    if requested not in allowed:
        raise bad_request(f"专业 {requested} 不在模板开放范围内：{allowed}")
    row = conn.execute("SELECT enabled FROM specialties WHERE code = ?", (requested,)).fetchone()
    if row is None or not row["enabled"]:
        raise bad_request(f"专业 {requested} 未启用")
    return requested


def self_service_templates(conn: sqlite3.Connection, *, bank_id=None,
                           person_category: str | None = None) -> list[dict[str, Any]]:
    if person_category and person_category not in PERSON_CATEGORIES:
        raise bad_request("无效人员类别")
    rows = conn.execute(
        "SELECT t.id FROM paper_templates t JOIN bank_versions b ON b.id=t.bank_version_id "
        "WHERE b.status='active' AND t.status IN ('active', 'draft') ORDER BY t.id"
    ).fetchall()
    items = []
    for row in rows:
        try:
            t = template_for(conn, row["id"])
        except (KeyError, ValueError):
            continue  # 没有受信 fixtures 的模板不能自助组卷
        if bank_id is not None and str(t["bank_version_id"]) != str(bank_id):
            continue
        if person_category and t["person_category"] != person_category:
            continue
        items.append({"template_id": t["id"], "name": t["name"],
                      "person_category": t["person_category"], "status": t["status"],
                      "specialty_codes": allowed_specialties(t), "question_count": t["question_count"],
                      "duration_min": t["duration_min"], "total_score": t["total_score"],
                      "pass_score": t["pass_score"], "exam_mode": "self_service",
                      "notice": "自主模拟，非正式成绩；草案参数仅用于模拟，不代表正式考试规则。"})
    return items


def _batch_row(conn: sqlite3.Connection, batch_id: Any) -> sqlite3.Row | None:
    if batch_id in (None, "", 0, "0"):
        return None
    try:
        bid = int(batch_id)
    except (TypeError, ValueError):
        raise bad_request("batch_id 必须是整数")
    row = conn.execute("SELECT * FROM exam_batches WHERE id = ?", (bid,)).fetchone()
    if row is None:
        raise not_found("场次不存在")
    return row


def _in_window(row: sqlite3.Row | None, moment) -> bool:
    if row is None:
        return True
    start = parse_instant(row["start_at"])
    end = parse_instant(row["end_at"], end_of_day=True)
    if start is not None and start > moment:
        return False
    if end is not None and end < moment:
        return False
    return True


def _in_list(row: sqlite3.Row | None, user: dict | sqlite3.Row) -> bool:
    if row is None:
        return True
    names = _loads(row["examinee_list"], [])
    if not names:
        return True
    allowed = {str(x) for x in names}
    return str(user["employee_no"]) in allowed or str(user["id"]) in allowed


def available(conn: sqlite3.Connection, user: dict | sqlite3.Row, *, bank_id: Any = None,
              specialty_code: str | None = None, paper_type: str | None = None,
              person_category: str | None = None) -> list[dict[str, Any]]:
    if person_category and person_category not in PERSON_CATEGORIES:
        raise bad_request("无效人员类别")
    person = person_category
    moment = utcnow()
    rows = conn.execute(
        "SELECT b.*, t.name AS template_name, t.code AS template_code, t.status AS template_status, "
        "t.person_category, t.paper_type, t.duration_min, t.total_score, t.pass_score, "
        "t.specialty_rule, bv.code AS bank_version_code, bv.status AS bank_status "
        "FROM exam_batches b JOIN paper_templates t ON t.id = b.template_id "
        "JOIN bank_versions bv ON bv.id = b.bank_version_id "
        "WHERE b.status = 'open' AND t.status = 'active' AND bv.status = 'active' "
        "ORDER BY b.start_at, b.id"
    ).fetchall()
    out = []
    for row in rows:
        if bank_id not in (None, "") and str(row["bank_version_id"]) != str(bank_id):
            continue
        if paper_type and row["paper_type"] != paper_type:
            continue
        if person and row["person_category"] and row["person_category"] != person:
            continue
        if specialty_code and specialty_code not in allowed_specialties(
            {"specialty_rule": _loads(row["specialty_rule"], {})}
        ):
            continue
        if not _in_window(row, moment) or not _in_list(row, user):
            continue
        used = conn.execute(
            "SELECT COUNT(*) FROM attempts WHERE user_id = ? AND batch_id = ?", (user["id"], row["id"])
        ).fetchone()[0]
        out.append({
            "batch_id": row["id"],
            "template_id": row["template_id"],
            "code": row["code"],
            "name": row["name"] or row["code"],
            "template_name": row["template_name"],
            "bank_version_code": row["bank_version_code"],
            "paper_type": row["paper_type"],
            "duration_min": row["duration_min"],
            "total_score": row["total_score"],
            "pass_score": row["pass_score"],
            "start_at": row["start_at"],
            "end_at": row["end_at"],
            "max_attempts": row["max_attempts"],
            "attempts_left": max(0, int(row["max_attempts"]) - used),
            "status": row["status"],
        })
    return out


def start(conn: sqlite3.Connection, user: dict | sqlite3.Row, body: Any) -> dict[str, Any]:
    moment = utcnow()
    at = moment.isoformat(timespec="seconds")
    self_service = bool(getattr(body, "self_service", False))
    if self_service and getattr(body, "batch_id", None) is not None:
        raise bad_request("自主模拟不绑定管理场次")
    batch = _batch_row(conn, getattr(body, "batch_id", None))
    template_id = getattr(body, "template_id", None) or (batch["template_id"] if batch else None)
    if template_id is None:
        raise bad_request("缺少 template_id")
    try:
        template = template_for(conn, int(template_id))
    except (KeyError, ValueError):
        raise not_found("试卷模板不存在")
    if template.get("status") not in (("active", "draft") if self_service else ("active",)):
        raise forbidden("试卷模板未启用")
    person = getattr(body, "person_category", None)
    if self_service and not person:
        raise bad_request("请选择人员类别")
    person = person or template.get("person_category")
    if person not in PERSON_CATEGORIES:
        raise bad_request("无效人员类别")
    if template.get("person_category") and template["person_category"] != person:
        raise bad_request("所选人员类别与模板不匹配，请重新选择模板")
    if batch is not None and batch["status"] != "open":
        raise forbidden("场次未开放")
    if batch is not None and int(batch["template_id"]) != int(template_id):
        raise bad_request("场次与模板不匹配")
    if batch is not None and not _in_window(batch, moment):
        raise forbidden("不在场次时间窗内")
    if batch is not None and not _in_list(batch, user):
        raise forbidden("不在本场次考生名单内")
    if batch is not None:
        previous = conn.execute(
            "SELECT COUNT(*) FROM attempts WHERE user_id = ? AND batch_id = ?",
            (user["id"], batch["id"]),
        ).fetchone()[0]
        if previous >= int(batch["max_attempts"]):
            raise forbidden("超过本场次重考次数上限")
        if previous and not batch["allow_retake"]:
            raise forbidden("本场次不允许补考")
    bank = conn.execute("SELECT status FROM bank_versions WHERE id = ?", (template["bank_version_id"],)).fetchone()
    if bank is None or bank["status"] != "active":
        raise forbidden("题库版本尚未发布")

    specialty_code = _specialty(conn, template, getattr(body, "specialty_code", None))
    seed = getattr(body, "seed", None) or secrets.token_hex(16)
    if self_service:
        template["strict_person_scope"] = True
        seed = "self-service:" + secrets.token_hex(16)  # 自主模拟不接受客户端指定重放种子
    deadline = add_minutes(moment, float(template["duration_min"]))
    if batch is not None and batch["end_at"]:
        end = parse_instant(batch["end_at"], end_of_day=True)
        if end is not None:
            deadline = min(deadline, end.isoformat(timespec="seconds"))

    with write_tx(conn):
        try:
            paper_id, snapshot_hash, _ = replay_paper(
                conn, template, seed, person, specialty_code, at=at
            )
        except ValueError as exc:
            raise bad_request(f"当前人员类别/专业无法组卷：{exc}") from exc
        cur = conn.execute(
            "INSERT INTO attempts (user_id, batch_id, paper_id, authorization_id, "
            "person_category_snapshot, specialty_snapshot, started_at, deadline_at, status, "
            "exam_mode, pass_score_snapshot, duration_min_snapshot) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'in_progress', ?, ?, ?)",
            (user["id"], batch["id"] if batch else None, paper_id, None,
             person, specialty_code, at, deadline,
             "self_service" if self_service else "managed", template["pass_score"], template["duration_min"]),
        )
        attempt_id = int(cur.lastrowid)
        audit.log(conn, actor_of(user), "exam.start", f"attempts:{attempt_id}", at=at,
                  after={"paper_id": paper_id, "seed": seed, "snapshot_hash": snapshot_hash,
                         "person_category": person, "specialty_code": specialty_code,
                         "deadline_at": deadline},
                  detail="考试时点冻结用户选择与锁卷快照")
    return view(conn, user, attempt_id)


def _attempt(conn: sqlite3.Connection, user: dict | sqlite3.Row, attempt_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM attempts WHERE id = ?", (attempt_id,)).fetchone()
    if row is None:
        raise not_found("考试记录不存在")
    if int(row["user_id"]) != int(user["id"]):
        # 不暴露他人考试是否存在
        raise not_found("考试记录不存在")
    return row


def _deadline_reached(row: sqlite3.Row, moment) -> bool:
    deadline = parse_instant(row["deadline_at"])
    return deadline is not None and moment >= deadline


def _finalize(conn: sqlite3.Connection, row: sqlite3.Row, answers: dict[str, str], *,
              at: str, expired: bool, actor: str) -> None:
    """按锁卷快照判分并落库；答案只来自快照，客户端任何 score/is_correct 都不参与。"""
    snapshots = read_snapshot(conn, row["paper_id"])
    paper = conn.execute("SELECT * FROM papers WHERE id = ?", (row["paper_id"],)).fetchone()
    bank_version_id = paper["bank_version_id"] if paper else 0
    total = 0.0
    pending = 0
    rows = []
    for snap in snapshots:
        sid = snap["stable_id"]
        choice = answers.get(sid, "")
        earned, result = grade(snap, choice)
        if row["exam_mode"] == "self_service" and snap["qtype"] == QTYPE_SHORT and not choice.strip():
            earned, result = 0.0, "blank"
        if result == "pending":
            pending += 1
        total = float(Decimal(str(total)) + Decimal(str(earned)))
        rows.append((row["id"], sid, choice or None, earned, result, "server", at))
        if result in ("wrong", "blank"):
            conn.execute(
                "INSERT INTO wrong_book (user_id, stable_id, bank_version_id, wrong_count, last_wrong_at, last_answer) "
                "VALUES (?, ?, ?, 1, ?, ?) ON CONFLICT(user_id, stable_id) DO UPDATE SET "
                "wrong_count = wrong_count + 1, last_wrong_at = excluded.last_wrong_at, "
                "last_answer = excluded.last_answer, mastered = 0",
                (row["user_id"], sid, bank_version_id, at, choice or None),
            )
    conn.executemany(
        "INSERT INTO attempt_answers (attempt_id, stable_id, user_answer, score, graded_result, graded_by, graded_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(attempt_id, stable_id) DO UPDATE SET "
        "score = excluded.score, graded_result = excluded.graded_result, "
        "graded_by = excluded.graded_by, graded_at = excluded.graded_at",
        rows,
    )
    template = conn.execute(
        "SELECT t.pass_score FROM paper_templates t JOIN papers p ON p.template_id = t.id WHERE p.id = ?",
        (row["paper_id"],),
    ).fetchone()
    pass_score = float(row["pass_score_snapshot"] if row["pass_score_snapshot"] is not None
                       else template["pass_score"] if template else 60.0)
    status = "expired" if expired else ("pending_review" if pending else "submitted")
    if row["exam_mode"] == "self_service" and pending:
        status = "pending_review"
    conn.execute(
        "UPDATE attempts SET score = ?, passed = ?, status = ?, submitted_at = ? WHERE id = ?",
        (round(total, 6), None if pending and row["exam_mode"] == "self_service" else int(total >= pass_score), status, at, row["id"]),
    )
    audit.log(conn, actor, "exam.submit", f"attempts:{row['id']}", at=at,
              after={"score": round(total, 6), "status": status, "pending": pending,
                     "graded_by": "server", "answers": len(answers)},
              detail="服务端按 paper_snapshot 判分；客户端上送的 score/elapsed/is_correct 一律忽略")


def _responses_map(responses: Iterable[Any], known: set[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in responses or []:
        if isinstance(item, dict):
            sid, choice = item.get("stable_id"), item.get("choice")
        else:
            sid, choice = getattr(item, "stable_id", None), getattr(item, "choice", None)
        if sid in known and choice is not None and str(sid) not in out:
            out[str(sid)] = str(choice)
    return out


def result_view(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    snapshots = read_snapshot(conn, row["paper_id"])
    graded = {r["stable_id"]: r for r in conn.execute(
        "SELECT * FROM attempt_answers WHERE attempt_id = ?", (row["id"],)
    ).fetchall()}
    paper = conn.execute("SELECT * FROM papers WHERE id = ?", (row["paper_id"],)).fetchone()
    tpl = conn.execute("SELECT * FROM paper_templates WHERE id = ?", (paper["template_id"],)).fetchone()
    items = []
    pending = 0
    for snap in snapshots:
        g = graded.get(snap["stable_id"])
        result = g["graded_result"] if g else "blank"
        if result == "pending":
            pending += 1
        items.append({
            "seq": snap["seq"],
            "stable_id": snap["stable_id"],
            "stem": snap["stem"],
            "score": snap["score"],
            "earned": float(g["score"]) if g and g["score"] is not None else 0.0,
            "result": result,
            "analysis": safe_analysis(snap.get("analysis")),
        })
    score = float(row["score"]) if row["score"] is not None else 0.0
    total = float(paper["total_score"])
    pass_score = float(row["pass_score_snapshot"] if row["pass_score_snapshot"] is not None else tpl["pass_score"])
    passed = None if row["passed"] is None else bool(row["passed"])
    objective_items = [s for s in snapshots if s["qtype"] != QTYPE_SHORT]
    objective_total = float(sum((Decimal(str(s["score"])) for s in objective_items), Decimal(0)))
    objective_score = float(sum((Decimal(str(graded[s["stable_id"]]["score"] or 0))
                                for s in objective_items if s["stable_id"] in graded), Decimal(0)))
    if pending and row["exam_mode"] == "self_service":
        score, passed = None, None
    result = {
        "objective_score": objective_score, "objective_total": objective_total,
        "score": score, "total_score": total, "pass_score": pass_score, "passed": passed,
        "pending_count": pending, "items": items,
    }
    responses = [
        {"stable_id": g["stable_id"], "choice": g["user_answer"] or ""}
        for g in graded.values()
        if row["exam_mode"] != "self_service" or (
            g["stable_id"] in {s["stable_id"] for s in objective_items}
            and safe_analysis(g["user_answer"]) is not None)
    ]
    return {**_base_view(conn, row, tpl, paper),
            "score": score, "passed": passed, "result": result, "responses": responses}


def _base_view(conn: sqlite3.Connection, row: sqlite3.Row, tpl: sqlite3.Row,
               paper: sqlite3.Row) -> dict[str, Any]:
    return {
        "attempt_id": row["id"],
        "id": row["id"],
        "name": tpl["name"],
        "template_name": tpl["name"],
        "paper_type": "mock" if row["exam_mode"] == "self_service" else tpl["paper_type"],
        "exam_mode": row["exam_mode"],
        "notice": "自主模拟，非正式成绩；简答题由本人自评，非自动评分。" if row["exam_mode"] == "self_service" else "管理场次考试",
        "status": row["status"],
        "person_category": row["person_category_snapshot"],
        "specialty_code": row["specialty_snapshot"],
        "started_at": row["started_at"],
        "deadline_at": row["deadline_at"],
        "submitted_at": row["submitted_at"],
        "server_now": now_iso(),
        "duration_min": row["duration_min_snapshot"] if row["duration_min_snapshot"] is not None else tpl["duration_min"],
        "total_score": float(paper["total_score"]),
        "pass_score": float(row["pass_score_snapshot"] if row["pass_score_snapshot"] is not None else tpl["pass_score"]),
        "snapshot_hash": paper["snapshot_hash"],
        "seed": paper["seed"],
        "questions": [question_dto(s) for s in read_snapshot(conn, row["paper_id"])],
    }


def view(conn: sqlite3.Connection, user: dict | sqlite3.Row, attempt_id: int) -> dict[str, Any]:
    row = _attempt(conn, user, attempt_id)
    moment = utcnow()
    if row["status"] == "in_progress" and EXAM_AUTO_SUBMIT_ON_DEADLINE and _deadline_reached(row, moment):
        with write_tx(conn):
            row = _attempt(conn, user, attempt_id)
            if row["status"] == "in_progress":
                _finalize(conn, row, {}, at=moment.isoformat(timespec="seconds"),
                          expired=True, actor=user["employee_no"])
            row = _attempt(conn, user, attempt_id)
    if row["status"] != "in_progress":
        return result_view(conn, row)
    paper = conn.execute("SELECT * FROM papers WHERE id = ?", (row["paper_id"],)).fetchone()
    tpl = conn.execute("SELECT * FROM paper_templates WHERE id = ?", (paper["template_id"],)).fetchone()
    return _base_view(conn, row, tpl, paper)


def submit(conn: sqlite3.Connection, user: dict | sqlite3.Row, attempt_id: int,
           responses: Iterable[Any]) -> dict[str, Any]:
    moment = utcnow()
    with write_tx(conn):
        row = _attempt(conn, user, attempt_id)
        if row["status"] != "in_progress":
            raise conflict("本场考试已交卷，不能重复提交")
        known = {s["stable_id"] for s in read_snapshot(conn, row["paper_id"])}
        answers = _responses_map(responses, known)
        expired = _deadline_reached(row, moment)
        # 到时自动交卷：不接受迟到作答，避免用本地时间换取额外答题时间
        _finalize(conn, row, {} if expired else answers, at=moment.isoformat(timespec="seconds"),
                  expired=expired, actor=user["employee_no"])
        row = _attempt(conn, user, attempt_id)
    return result_view(conn, row)


def list_my_exams(conn: sqlite3.Connection, user: dict | sqlite3.Row) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT a.*, p.total_score AS paper_total, t.name AS template_name, t.paper_type, "
        "b.code AS batch_code, bv.code AS bank_version_code "
        "FROM attempts a JOIN papers p ON p.id = a.paper_id "
        "JOIN paper_templates t ON t.id = p.template_id "
        "LEFT JOIN exam_batches b ON b.id = a.batch_id "
        "LEFT JOIN bank_versions bv ON bv.id = p.bank_version_id "
        "WHERE a.user_id = ? ORDER BY a.started_at DESC, a.id DESC",
        (user["id"],),
    ).fetchall()
    return [
        {
            "id": r["id"], "attempt_id": r["id"], "name": r["template_name"],
            "template_name": r["template_name"],
            "paper_type": "mock" if r["exam_mode"] == "self_service" else r["paper_type"],
            "exam_mode": r["exam_mode"],
            "batch_code": r["batch_code"], "bank_version_code": r["bank_version_code"],
            "started_at": r["started_at"], "submitted_at": r["submitted_at"],
            "deadline_at": r["deadline_at"], "status": r["status"],
            "score": None if r["exam_mode"] == "self_service" and r["status"] == "pending_review" else r["score"], "total_score": r["paper_total"],
            "passed": None if r["passed"] is None else bool(r["passed"]),
            "person_category": r["person_category_snapshot"],
            "specialty_code": r["specialty_snapshot"],
        }
        for r in rows
    ]


def recompute_score(conn: sqlite3.Connection, attempt_id: int, *, at: str | None = None) -> float:
    """人工阅卷后重算总分；只读 attempt_answers，不改锁卷快照。"""
    row = conn.execute("SELECT * FROM attempts WHERE id = ?", (attempt_id,)).fetchone()
    if row is None:
        raise not_found("考试记录不存在")
    total = float(sum((Decimal(str(r[0] or 0)) for r in conn.execute(
        "SELECT score FROM attempt_answers WHERE attempt_id = ?", (attempt_id,)
    )), Decimal(0)))
    pending = conn.execute(
        "SELECT COUNT(*) FROM attempt_answers WHERE attempt_id = ? AND graded_result = 'pending'",
        (attempt_id,),
    ).fetchone()[0]
    paper = conn.execute("SELECT * FROM papers WHERE id = ?", (row["paper_id"],)).fetchone()
    tpl = conn.execute("SELECT pass_score FROM paper_templates WHERE id = ?", (paper["template_id"],)).fetchone()
    pass_score = float(row["pass_score_snapshot"] if row["pass_score_snapshot"] is not None else tpl["pass_score"])
    status = "pending_review" if pending else "graded"
    conn.execute(
        "UPDATE attempts SET score = ?, passed = ?, status = ? WHERE id = ?",
        (round(total, 6), None if pending and row["exam_mode"] == "self_service" else int(total >= pass_score), status, attempt_id),
    )
    return round(total, 6)
