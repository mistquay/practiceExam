"""自主练考回归：只修改仓库内 pytest 隔离副本。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.models import PERSON_CATEGORIES
from app.services import testkit as T
from app.services.dto import FORBIDDEN_TOKENS


@pytest.fixture
def scenario(tmp_path, monkeypatch):
    db = T.prepare(tmp_path, monkeypatch)
    admin = T.client()
    T.admin(admin)
    T.import_users(admin, [{"employee_no": "free_user", "name": "自主用户", "role": "examinee",
                            "initial_password": "Practice@123"}])
    user = T.client()
    T.login(user, "free_user", "Practice@123")
    conn = T.connect(db)
    bank = T.active_bank_id(conn)
    yield db, admin, user, conn, bank
    conn.close()


def test_person_scope_and_protected_source_intersection(scenario):
    _, _, user, conn, bank = scenario
    assert user.get("/api/auth/me").json()["authorization"] is None
    all_count = user.get("/api/practice/filters", params={"bank_id": bank}).json()["total"]
    counts = []
    for person in PERSON_CATEGORIES:
        expected = {r[0] for r in conn.execute(
            "SELECT q.stable_id FROM question_versions q WHERE q.bank_version_id=? AND q.retired=0 "
            "AND EXISTS (SELECT 1 FROM question_tags t WHERE t.bank_version_id=q.bank_version_id "
            "AND t.stable_id=q.stable_id AND t.tag_type='person_scope' AND t.tag_value=?)", (bank, person))}
        data = user.get("/api/practice/questions", params={"bank_id": bank, "person_category": person, "count": 200}).json()
        assert data["total"] == len(expected)
        assert {q["stable_id"] for q in data["questions"]} <= expected
        counts.append(data["total"])
        source = conn.execute(
            "SELECT qs.source_file_id FROM question_sources qs JOIN question_tags t "
            "ON t.bank_version_id=qs.bank_version_id AND t.stable_id=qs.stable_id "
            "WHERE qs.bank_version_id=? AND t.tag_type='person_scope' AND t.tag_value=? LIMIT 1", (bank, person)).fetchone()
        if not source:
            continue
        fid = source[0]
        params = {"bank_id": bank, "person_category": person, "tags": "protected", "source_file_ids": fid, "count": 200}
        expected = {r[0] for r in conn.execute(
            "SELECT q.stable_id FROM question_versions q WHERE q.bank_version_id=? AND q.retired=0 AND q.is_protected=1 "
            "AND EXISTS (SELECT 1 FROM question_tags t WHERE t.bank_version_id=q.bank_version_id AND t.stable_id=q.stable_id "
            "AND t.tag_type='person_scope' AND t.tag_value=?) AND EXISTS (SELECT 1 FROM question_sources qs "
            "WHERE qs.bank_version_id=q.bank_version_id AND qs.stable_id=q.stable_id AND qs.source_file_id=?)", (bank, person, fid))}
        data = user.get("/api/practice/questions", params=params).json()
        assert data["total"] == len(expected)
        assert {q["stable_id"] for q in data["questions"]} <= expected
        assert user.get("/api/practice/filters", params=params).json()["total"] == len(expected)
    assert any(0 < n < all_count for n in counts)
    for person in ("invalid", "重点人员' OR 1=1--"):
        for endpoint in ("questions", "filters"):
            assert user.get(f"/api/practice/{endpoint}", params={"person_category": person}).status_code == 400


def test_draft_free_start_selection_snapshot_grading_history_and_export(scenario):
    _, admin, user, conn, bank = scenario
    assert user.get("/api/auth/me").json()["authorization"] is None
    # 既没有授权，也没有场次；草案不能冒充正式模板启用。
    conn.execute("UPDATE paper_templates SET status='draft'")
    conn.commit()
    templates = user.get("/api/exams/self-service/templates", params={"bank_id": bank, "person_category": "一线人员"}).json()["items"]
    assert templates and all(t["person_category"] == "一线人员" for t in templates)
    template = templates[0]
    body = {"self_service": True, "template_id": template["template_id"], "person_category": "一线人员", "specialty_code": "pd_live"}
    r = user.post("/api/exams/start", json=body)
    assert r.status_code == 200, r.text
    attempt = r.json()
    assert attempt["exam_mode"] == "self_service" and attempt["paper_type"] == "mock"
    assert attempt["name"] == "模拟考试"
    assert "非正式" not in attempt["notice"]
    for token in FORBIDDEN_TOKENS:
        assert token.lower() not in r.content.lower(), token
    row = conn.execute("SELECT * FROM attempts WHERE id=?", (attempt["id"],)).fetchone()
    assert row["authorization_id"] is None and row["batch_id"] is None
    assert row["person_category_snapshot"] == "一线人员"
    paper = conn.execute("SELECT * FROM papers WHERE id=?", (row["paper_id"],)).fetchone()
    assert paper["person_category"] == "一线人员"
    assert conn.execute("SELECT status FROM paper_templates WHERE id=?", (template["template_id"],)).fetchone()[0] == "draft"
    snapshots = [json.loads(r[0]) for r in conn.execute("SELECT snapshot_json FROM paper_questions WHERE paper_id=?", (paper["id"],))]
    for q in snapshots:
        assert conn.execute("SELECT 1 FROM question_tags WHERE bank_version_id=? AND stable_id=? AND tag_type='person_scope' AND tag_value=?", (bank, q["stable_id"], "一线人员")).fetchone()
    # 修改授权为其他类别后仍可自由选择一线，且旧快照不变。
    uid = row["user_id"]
    T.authorize(admin, uid, "重点人员", "pd_live")
    assert user.post("/api/exams/start", json=body).status_code == 200
    assert user.get(f"/api/exams/{attempt['id']}").json()["person_category"] == "一线人员"
    # 模板阈值修改不能篡改本次模拟的及格线。
    conn.execute("UPDATE paper_templates SET pass_score=999 WHERE id=?", (template["template_id"],))
    conn.commit()
    answers = [{"stable_id": q["stable_id"], "choice": q["answer"], "score": -100} for q in snapshots]
    r = user.post(f"/api/exams/{attempt['id']}/submit", json={"responses": answers, "score": -100, "elapsed": 0})
    assert r.status_code == 200, r.text
    result = r.json()
    assert result["score"] == sum(q["score"] for q in snapshots)
    assert result["passed"] is True and result["result"]["pass_score"] == template["pass_score"]
    for token in FORBIDDEN_TOKENS:
        assert token.lower() not in r.content.lower(), token
    history = user.get("/api/exams").json()["items"]
    assert all(x["exam_mode"] == "self_service" and x["paper_type"] == "mock" for x in history)
    report = user.get("/api/reports/export", params={"format": "json"}).json()
    assert report["pass_rate"] is None and report["graded_count"] == 0
    assert report["passed_count"] == 0
    assert all(r["exam_mode"] == "模拟考试" for r in report["rows"])


def test_self_service_has_no_batch_gates_but_preserves_validation(scenario):
    _, admin, user, conn, bank = scenario
    template = user.get("/api/exams/self-service/templates", params={"person_category": "重点人员"}).json()["items"][0]
    tid = template["template_id"]
    batch = T.open_batch(admin, tid, bank, "CLOSED-TO-USER", examinee_list=["someone_else"], start_minutes_ago=-60)
    body = {"self_service": True, "template_id": tid, "person_category": "重点人员", "specialty_code": "pd_live"}
    assert user.post("/api/exams/start", json=body).status_code == 200
    assert user.post("/api/exams/start", json={**body, "batch_id": batch}).status_code == 400
    for change in ({"person_category": "一线人员"}, {"person_category": "invalid"},
                   {"specialty_code": "missing"}, {"specialty_code": None}):
        assert user.post("/api/exams/start", json={**body, **change}).status_code == 400
    conn.execute("UPDATE question_versions SET retired=1 WHERE bank_version_id=?", (bank,))
    conn.commit()
    r = user.post("/api/exams/start", json=body)
    assert r.status_code == 400 and "无法组卷" in r.json()["detail"]
    conn.execute("UPDATE bank_versions SET status='archived' WHERE id=?", (bank,))
    conn.commit()
    assert user.post("/api/exams/start", json=body).status_code == 403
    assert user.get("/api/exams/self-service/templates").json()["items"] == []
    anonymous = T.client()
    assert anonymous.get("/api/exams/self-service/templates").status_code == 401
    assert anonymous.post("/api/exams/start", json=body).status_code == 401


@pytest.mark.parametrize("person", ["重点人员", "一线人员"])
@pytest.mark.parametrize("specialty", ["pd_general", "pd_live"])
def test_self_service_category_pool_and_server_deadline(scenario, person, specialty):
    from datetime import datetime, timedelta, timezone

    _, _, user, conn, bank = scenario
    template = user.get("/api/exams/self-service/templates", params={"person_category": person}).json()["items"][0]
    r = user.post("/api/exams/start", json={
        "self_service": True, "template_id": template["template_id"],
        "person_category": person, "specialty_code": specialty, "seed": "forged-seed",
    })
    assert r.status_code == 200, r.text
    attempt = r.json()
    assert attempt["seed"] != "forged-seed"
    row = conn.execute("SELECT * FROM attempts WHERE id=?", (attempt["id"],)).fetchone()
    snapshots = [json.loads(r[0]) for r in conn.execute(
        "SELECT snapshot_json FROM paper_questions WHERE paper_id=?", (row["paper_id"],))]
    assert len(snapshots) == template["question_count"]
    assert all(conn.execute(
        "SELECT 1 FROM question_tags WHERE bank_version_id=? AND stable_id=? "
        "AND tag_type='person_scope' AND tag_value=?", (bank, q["stable_id"], person)).fetchone() for q in snapshots)
    assert attempt["person_category"] == person and attempt["specialty_code"] == specialty
    started = datetime.fromisoformat(row["started_at"])
    deadline = datetime.fromisoformat(row["deadline_at"])
    assert (deadline - started).total_seconds() == template["duration_min"] * 60
    # 模板后续编辑不能改变已开考的时长/阈值；其他账号不能读取或提交本次模拟。
    conn.execute("UPDATE paper_templates SET duration_min=1, pass_score=999 WHERE id=?", (template["template_id"],))
    conn.commit()
    current = user.get(f"/api/exams/{attempt['id']}").json()
    assert current["duration_min"] == template["duration_min"]
    assert current["pass_score"] == template["pass_score"]
    assert current["deadline_at"] == attempt["deadline_at"]
    other = T.client()
    T.admin(other)
    assert other.get(f"/api/exams/{attempt['id']}").status_code == 404
    assert other.post(f"/api/exams/{attempt['id']}/submit", json={"responses": []}).status_code == 404
    expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(timespec="seconds")
    conn.execute("UPDATE attempts SET deadline_at=? WHERE id=?", (expired, attempt["id"]))
    conn.commit()
    result = user.post(f"/api/exams/{attempt['id']}/submit", json={
        "responses": [{"stable_id": q["stable_id"], "choice": q["answer"]} for q in snapshots],
        "score": 100, "elapsed": 0, "deadline_at": "2099-01-01T00:00:00+00:00",
    })
    assert result.status_code == 200, result.text
    assert result.json()["score"] == 0 and result.json()["status"] == "expired"
    assert result.json()["exam_mode"] == "self_service"
    for token in FORBIDDEN_TOKENS:
        assert token.lower() not in result.content.lower(), token
    assert user.post(f"/api/exams/{attempt['id']}/submit", json={"responses": []}).status_code == 409
    history = user.get("/api/exams").json()["items"]
    assert history[0]["person_category"] == person and history[0]["status"] == "expired"


def test_mixed_report_excludes_only_self_service_from_aggregates(scenario):
    _, admin, user, conn, bank = scenario
    template = user.get("/api/exams/self-service/templates", params={"person_category": "重点人员"}).json()["items"][0]
    body = {"self_service": True, "template_id": template["template_id"],
            "person_category": "重点人员", "specialty_code": "pd_live"}
    attempt = user.post("/api/exams/start", json=body).json()
    paper = conn.execute("SELECT paper_id FROM attempts WHERE id=?", (attempt["id"],)).fetchone()[0]
    snapshots = [json.loads(r[0]) for r in conn.execute("SELECT snapshot_json FROM paper_questions WHERE paper_id=?", (paper,))]
    assert user.post(f"/api/exams/{attempt['id']}/submit", json={"responses": [
        {"stable_id": q["stable_id"], "choice": q["answer"]} for q in snapshots]}).json()["passed"] is True
    T.activate_templates(admin)
    batch = T.open_batch(admin, template["template_id"], bank, "MIXED-REPORT")
    managed = user.post("/api/exams/start", json={**body, "self_service": False, "batch_id": batch})
    assert managed.status_code == 200, managed.text
    mid = managed.json()["id"]
    assert user.post(f"/api/exams/{mid}/submit", json={"responses": []}).json()["passed"] is False
    report = user.get("/api/reports/export", params={"format": "json"}).json()
    assert report["total"] == 2 and len(report["rows"]) == 2
    assert report["graded_count"] == 1 and report["passed_count"] == 0 and report["pass_rate"] == 0
    assert {r["exam_mode"] for r in report["rows"]} == {"管理场次", "模拟考试"}
