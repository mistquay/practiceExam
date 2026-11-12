"""P1/P2/P3 API 契约测试：认证、授权、维度、练考隔离、越权矩阵、记录、导出、审计。

运行：/d/services/mamba_envs/ame/python.exe -m pytest tests/test_api.py -q
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.services import testkit as T  # noqa: E402
from app.services.dto import FORBIDDEN_TOKENS  # noqa: E402

pytestmark = pytest.mark.skipif(not T.SOURCE_DB.is_file(), reason="缺少 data/exam.db")


# ───────────────────────── fixtures ─────────────────────────

@pytest.fixture
def env(tmp_path, monkeypatch):
    return T.prepare(tmp_path, monkeypatch)


@pytest.fixture
def admin_client(env):
    c = T.client()
    T.admin(c)
    return c


@pytest.fixture
def scenario(env, admin_client):
    """2 名考生 + 1 管理员 + 启用模板 + 开放场次。"""
    T.activate_templates(admin_client)
    T.import_users(admin_client, [
        {"employee_no": "E001", "name": "张三", "team": "配电一班", "role": "examinee", "initial_password": "Pass@001"},
        {"employee_no": "E002", "name": "李四", "team": "配电二班", "role": "examinee", "initial_password": "Pass@002"},
    ])
    users = admin_client.get("/api/admin/users").json()["users"]
    by_no = {u["employee_no"]: u for u in users}
    T.authorize(admin_client, by_no["E001"]["id"], "重点人员", "pd_live")
    T.authorize(admin_client, by_no["E002"]["id"], "一线人员", "pd_live")
    conn = T.connect(env)
    bank = T.active_bank_id(conn)
    conn.close()
    batch = T.open_batch(admin_client, 1, bank, "T-API", max_attempts=3,
                         examinee_list=["E001", "E002"])
    u1 = T.client()
    T.login(u1, "E001", "Pass@001")
    u2 = T.client()
    T.login(u2, "E002", "Pass@002")
    return {"db": env, "admin": admin_client, "u1": u1, "u2": u2,
            "batch": batch, "template": 1, "bank": bank, "users": by_no}


def start_exam(c, scenario, *, seed=None, template_id=None):
    body = {"batch_id": scenario["batch"], "template_id": template_id or scenario["template"],
            "specialty_code": "pd_live"}
    if seed:
        body["seed"] = seed
    r = c.post("/api/exams/start", json=body)
    assert r.status_code == 200, (r.status_code, r.text[:400])
    return r.json()


def correct_responses(db: Path, attempt_id: int) -> list[dict]:
    """测试替身：直接从锁卷快照读答案构造「全对」作答（考生侧拿不到）。"""
    conn = T.connect(db)
    paper_id = conn.execute("SELECT paper_id FROM attempts WHERE id = ?", (attempt_id,)).fetchone()[0]
    rows = conn.execute("SELECT snapshot_json FROM paper_questions WHERE paper_id = ? ORDER BY seq",
                        (paper_id,)).fetchall()
    conn.close()
    return [{"stable_id": json.loads(r[0])["stable_id"], "choice": json.loads(r[0])["answer"]}
            for r in rows]


# ───────────────────────── 认证与授权 ─────────────────────────

def test_login_me_returns_current_effective_authorization(scenario):
    r = scenario["u1"].get("/api/auth/me")
    assert r.status_code == 200
    me = r.json()
    assert me["employee_no"] == "E001" and me["role"] == "examinee"
    auth = me["authorization"]
    assert auth and auth["person_category"] == "重点人员"
    assert auth["specialty_code"] == "pd_live"
    for key in ("effective_from", "effective_to", "basis_doc", "approver", "decided_at"):
        assert key in auth
    assert auth["basis_doc"] and auth["approver"]
    assert r.json()["id"] == scenario["users"]["E001"]["id"]


def test_authorization_change_keeps_attempt_snapshot(scenario):
    attempt = start_exam(scenario["u1"], scenario, seed="auth-change")
    old = (attempt["person_category"], attempt["specialty_code"])
    assert old == ("重点人员", "pd_live")
    T.authorize(scenario["admin"], scenario["users"]["E001"]["id"], "一线人员", "pd_general",
                effective_from="2030-01-01")
    conn = T.connect(scenario["db"])
    row = conn.execute("SELECT person_category_snapshot, specialty_snapshot FROM attempts WHERE id = ?",
                       (attempt["attempt_id"],)).fetchone()
    conn.close()
    assert tuple(row) == old
    # 新授权 2030 年才生效，当前有效授权仍是旧的那条
    assert scenario["u1"].get("/api/auth/me").json()["authorization"]["person_category"] == "重点人员"


def test_login_bad_password_and_logout(env):
    c = T.client()
    assert c.post("/api/auth/login", json={"employee_no": "admin", "password": "wrong"}).status_code == 401
    T.admin(c)
    assert c.get("/api/auth/me").status_code == 200
    assert c.post("/api/auth/logout").status_code == 200
    assert c.get("/api/auth/me").status_code == 401


def test_session_cookie_is_http_only(env):
    c = T.client()
    r = c.post("/api/auth/login", json={"employee_no": "admin", "password": T.ADMIN_PASSWORD})
    assert r.status_code == 200
    assert "httponly" in r.headers["set-cookie"].lower()
    assert "examsession" in r.headers["set-cookie"].lower().replace("_", "")


# ───────────────────────── 维度 API ─────────────────────────

def test_banks_and_dimension(scenario):
    banks = scenario["u1"].get("/api/banks")
    assert banks.status_code == 200
    assert all(b["status"] == "active" for b in banks.json()["banks"])
    dim = scenario["u1"].get(f"/api/banks/{scenario['bank']}/dimension")
    assert dim.status_code == 200
    d = dim.json()
    assert {p["code"] for p in d["person_categories"]} == {"重点人员", "一线人员"}
    enabled = {s["code"] for s in d["specialties"] if s["enabled"]}
    assert enabled == {"pd_general", "pd_live"}, enabled
    assert len(d["specialties"]) == 22
    assert d["required_count"] == 19 and d["exclude_count"] == 566
    assert d["short_answer_policy"] == "excluded"
    assert {t["code"] for t in d["paper_types"]} == {"practice", "mock", "formal", "makeup"}
    assert {q["code"] for q in d["qtypes"]} == {"单选题", "多选题", "判断题", "简答题"}


# ───────────────────────── 练考隔离 / 答案不下发 ─────────────────────────

def scan_exam_body(body: bytes) -> None:
    lowered = body.lower()
    for token in FORBIDDEN_TOKENS:
        assert token.lower() not in lowered, f"考试响应出现判分依据 token: {token!r}"


def test_exam_responses_never_contain_answer_data(scenario):
    c = scenario["u1"]
    r = c.get("/api/exams/available", params={"bank_id": scenario["bank"]})
    assert r.status_code == 200
    scan_exam_body(r.content)

    attempt = start_exam(c, scenario, seed="scan-1")
    scan_exam_body(json.dumps(attempt, ensure_ascii=False).encode("utf-8"))
    qkeys = set(attempt["questions"][0])
    assert qkeys == {"seq", "stable_id", "qtype", "stem", "options", "score", "is_required"}
    assert len(attempt["questions"]) == 100

    r = c.get(f"/api/exams/{attempt['attempt_id']}")
    scan_exam_body(r.content)
    r = c.get("/api/exams")
    scan_exam_body(r.content)
    r = c.post(f"/api/exams/{attempt['attempt_id']}/submit", json={"responses": []})
    assert r.status_code == 200
    scan_exam_body(r.content)
    assert r.json()["result"]["items"] and all(
        set(i) <= {"seq", "stable_id", "stem", "score", "earned", "result", "analysis"}
        for i in r.json()["result"]["items"]
    )
    r = c.get(f"/api/exams/{attempt['attempt_id']}")
    scan_exam_body(r.content)


def test_exam_body_has_no_required_or_short_answer_strings(scenario):
    """字节扫描：19 必答题答案 + 191 道简答答案字符串在考试响应中 0 命中。"""
    conn = T.connect(scenario["db"])
    required = [r[0] for r in conn.execute(
        "SELECT answer FROM question_versions WHERE bank_version_id = ? AND is_required = 1 AND retired = 0",
        (scenario["bank"],))]
    short = [r[0] for r in conn.execute(
        "SELECT answer FROM question_versions WHERE bank_version_id = ? AND qtype = '简答题' "
        "AND retired = 0 AND length(answer) >= 8", (scenario["bank"],))]
    conn.close()
    assert len(required) == 19 and len(short) > 100
    # 简答答案是长文本，做原文包含检查；
    # 必答题答案是字母串，单字母与选项键 "A" 无法区分，改成「独立 JSON 字符串值」检查。
    needles = [s.encode("utf-8") for s in short] + [
        ('"' + s + '"').encode("utf-8") for s in required if len(s) >= 2
    ]
    attempt = start_exam(scenario["u1"], scenario, seed="scan-bytes")
    bodies = [
        scenario["u1"].get(f"/api/exams/{attempt['attempt_id']}").content,
        scenario["u1"].post(f"/api/exams/{attempt['attempt_id']}/submit",
                            json={"responses": []}).content,
    ]
    for body in bodies:
        for needle in needles:
            assert needle not in body, f"考试响应泄漏答案字符串 {needle[:40]!r}"


def test_exam_related_static_files_have_no_answer_keywords():
    files = [
        "static/api/exam.js", "static/views/exam.js", "static/views/exam-history.js",
        "static/components/question-card.js", "static/components/countdown.js",
        "static/state/exam-draft.js", "static/index.html",
    ]
    for rel in files:
        body = (REPO_ROOT / rel).read_bytes()
        for token in FORBIDDEN_TOKENS:
            assert token.lower() not in body.lower(), f"{rel} 含 {token!r}"


def test_practice_dto_may_contain_answers_but_isolated_from_exam_pool(scenario):
    conn = T.connect(scenario["db"])
    from app.services.practice import exam_pool_ids
    exam_ids = exam_pool_ids(conn, scenario["bank"], "重点人员")
    conn.close()
    r = scenario["u1"].get("/api/practice/questions", params={
        "bank_id": scenario["bank"], "person_category": "重点人员",
        "specialty_code": "pd_live", "count": 30, "order": "sequence", "source": "all",
    })
    assert r.status_code == 200
    items = r.json()["questions"]
    assert len(items) == 30
    assert all("answer" in q and "analysis" in q for q in items)
    assert not ({q["stable_id"] for q in items} & exam_ids), "练习池与考试池未隔离"
    q = items[0]
    r = scenario["u1"].post("/api/practice/submit", json={
        "bank_id": scenario["bank"], "stable_id": q["stable_id"], "user_answer": q["answer"],
    })
    assert r.status_code == 200 and r.json()["is_correct"] is True
    # 练习事件绝不写 attempts
    conn = T.connect(scenario["db"])
    assert conn.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM practice_records").fetchone()[0] == 1
    conn.close()


# ───────────────────────── 判分 / 提交 / 时间 ─────────────────────────

def test_forged_score_and_client_time_are_ignored(scenario):
    attempt = start_exam(scenario["u1"], scenario, seed="forged-1")
    r = scenario["u1"].post(f"/api/exams/{attempt['attempt_id']}/submit", json={
        "responses": [], "score": 100, "elapsed": -9999, "is_correct": True,
        "server_now": "2000-01-01T00:00:00+00:00", "deadline_at": "2099-01-01T00:00:00+00:00",
    })
    assert r.status_code == 200
    assert r.json()["score"] == 0
    conn = T.connect(scenario["db"])
    row = conn.execute("SELECT score, deadline_at, passed FROM attempts WHERE id = ?",
                       (attempt["attempt_id"],)).fetchone()
    conn.close()
    assert row[0] == 0 and row[2] == 0
    assert row[1] == attempt["deadline_at"]


def test_full_correct_is_template_full_score_and_all_wrong_is_zero(scenario):
    a1 = start_exam(scenario["u1"], scenario, seed="all-right")
    responses = correct_responses(scenario["db"], a1["attempt_id"])
    r = scenario["u1"].post(f"/api/exams/{a1['attempt_id']}/submit",
                            json={"responses": responses, "score": 0, "is_correct": False})
    assert r.status_code == 200 and r.json()["score"] == 100
    assert r.json()["passed"] is True and r.json()["result"]["pending_count"] == 0
    a2 = start_exam(scenario["u1"], scenario, seed="all-wrong")
    r2 = scenario["u1"].post(f"/api/exams/{a2['attempt_id']}/submit", json={"responses": []})
    assert r2.json()["score"] == 0 and r2.json()["passed"] is False


def test_second_submit_is_409_and_score_unchanged(scenario):
    a = start_exam(scenario["u1"], scenario, seed="dup-submit")
    r = scenario["u1"].post(f"/api/exams/{a['attempt_id']}/submit",
                            json={"responses": correct_responses(scenario["db"], a["attempt_id"])})
    assert r.status_code == 200 and r.json()["score"] == 100
    again = scenario["u1"].post(f"/api/exams/{a['attempt_id']}/submit",
                                json={"responses": [], "score": 0})
    assert again.status_code == 409
    conn = T.connect(scenario["db"])
    assert conn.execute("SELECT score FROM attempts WHERE id = ?", (a["attempt_id"],)).fetchone()[0] == 100
    conn.close()


def test_deadline_auto_submit_and_reconnect_keeps_server_deadline(scenario):
    a = start_exam(scenario["u1"], scenario, seed="deadline-1")
    conn = T.connect(scenario["db"])
    conn.execute("UPDATE attempts SET deadline_at = '2020-01-01T00:00:00+00:00' WHERE id = ?",
                 (a["attempt_id"],))
    conn.commit()
    conn.close()
    r = scenario["u1"].get(f"/api/exams/{a['attempt_id']}")
    assert r.status_code == 200
    assert r.json()["status"] == "expired" and r.json()["score"] == 0
    assert r.json()["deadline_at"] == "2020-01-01T00:00:00+00:00"  # 断线重连不重置
    assert "server_now" in r.json()
    late = scenario["u1"].post(f"/api/exams/{a['attempt_id']}/submit",
                               json={"responses": correct_responses(scenario["db"], a["attempt_id"])})
    assert late.status_code == 409


def test_max_attempts_403(scenario):
    for i in range(3):
        start_exam(scenario["u1"], scenario, seed=f"attempts-{i}")
    r = scenario["u1"].post("/api/exams/start", json={
        "batch_id": scenario["batch"], "template_id": scenario["template"],
        "specialty_code": "pd_live", "seed": "attempts-over",
    })
    assert r.status_code == 403
    conn = T.connect(scenario["db"])
    assert conn.execute("SELECT COUNT(*) FROM attempts WHERE batch_id = ?",
                        (scenario["batch"],)).fetchone()[0] == 3
    conn.close()


# ───────────────────────── 越权矩阵 ─────────────────────────

def test_unauthenticated_all_non_login_apis_401(env):
    anon = T.client()
    paths = [
        ("get", "/api/auth/me"), ("get", "/api/banks"), ("get", "/api/banks/1/dimension"),
        ("get", "/api/practice/questions"), ("post", "/api/practice/submit"),
        ("get", "/api/records/wrong"), ("delete", "/api/records/wrong"),
        ("get", "/api/records/practice"), ("get", "/api/exams"),
        ("get", "/api/exams/available"), ("post", "/api/exams/start"),
        ("get", "/api/exams/1"), ("post", "/api/exams/1/submit"),
        ("get", "/api/admin/users"), ("post", "/api/admin/users/import"),
        ("post", "/api/admin/authorizations/1/revoke"),
        ("get", "/api/admin/banks"), ("get", "/api/admin/templates"),
        ("get", "/api/admin/batches"), ("get", "/api/admin/grading/pending"),
        ("get", "/api/admin/audit"), ("get", "/api/reports/export"),
    ]
    statuses = []
    for method, path in paths:
        kw = {"json": {}} if method == "post" else {}
        r = getattr(anon, method)(path, **kw)
        statuses.append((method, path, r.status_code))
    assert all(s == 401 for _, _, s in statuses), statuses


def test_cross_user_and_role_matrix_at_least_24_requests(scenario):
    """2 考生 + 1 管理员 × 敏感端点：≥24 个越权请求全部 401/403/404，且 0 字节含对方数据。"""
    a = start_exam(scenario["u1"], scenario, seed="matrix-owner")
    aid = a["attempt_id"]
    owner_stem = a["questions"][0]["stem"]
    sensitive = [
        ("get", f"/api/exams/{aid}", None),
        ("post", f"/api/exams/{aid}/submit", {"responses": []}),
        ("get", "/api/exams", None),
        ("get", "/api/records/wrong", None),
        ("get", "/api/records/practice", None),
        ("get", "/api/reports/export?format=json", None),
        ("get", "/api/admin/users", None),
        ("get", "/api/admin/templates", None),
        ("get", "/api/admin/batches", None),
        ("get", "/api/admin/audit", None),
        ("get", f"/api/admin/users/{scenario['users']['E001']['id']}/authorizations", None),
        ("get", "/api/reports/export?format=json&user_id=2", None),
    ]
    def expected(label: str, path: str) -> tuple[int, ...]:
        if label == "anon":
            return (401,)
        if path.startswith("/api/admin/"):
            return (403,) if label != "admin" else (200,)
        if "user_id=2" in path and label == "u2":
            return (403,)                       # 考生导出他人
        if f"/api/exams/{aid}" in path and label == "u2":
            return (403, 404, 409)              # 他人考试实例不可见 / 不可提交
        return (200,)

    requests = []
    for c, label in ((T.client(), "anon"), (scenario["u2"], "u2"), (scenario["u1"], "u1")):
        for method, path, body in sensitive:
            kw = {"json": body} if body is not None else {}
            r = getattr(c, method)(path, **kw)
            requests.append((label, method, path, r.status_code, r.content))
    assert len(requests) >= 24
    for label, method, path, status, body in requests:
        assert status in expected(label, path), (label, method, path, status, expected(label, path))
        if label != "u1":
            assert owner_stem.encode("utf-8") not in body, (label, path, "泄漏他人题干")
    # 管理员可读全部
    assert scenario["admin"].get("/api/exams").status_code == 200


def test_no_null_user_ids_and_no_anonymous_audit(scenario):
    a = start_exam(scenario["u1"], scenario, seed="null-users")
    scenario["u1"].post(f"/api/exams/{a['attempt_id']}/submit", json={"responses": []})
    q = scenario["u1"].get("/api/practice/questions", params={"bank_id": scenario["bank"], "count": 2})
    if q.json()["questions"]:
        item = q.json()["questions"][0]
        scenario["u1"].post("/api/practice/submit", json={
            "bank_id": scenario["bank"], "stable_id": item["stable_id"], "user_answer": "Z"})
    conn = T.connect(scenario["db"])
    for table in ("attempts", "practice_records", "wrong_book"):
        assert conn.execute(f"SELECT COUNT(*) FROM {table} WHERE user_id IS NULL").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM attempt_answers aa JOIN attempts a ON a.id = aa.attempt_id "
                        "WHERE a.user_id IS NULL").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM audit_logs WHERE actor IS NULL OR trim(actor) = ''").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM audit_logs").fetchone()[0] > 0
    conn.close()


def test_records_are_per_user_and_persist_across_clients(scenario):
    a = start_exam(scenario["u1"], scenario, seed="wrong-owner")
    scenario["u1"].post(f"/api/exams/{a['attempt_id']}/submit", json={"responses": []})
    own = scenario["u1"].get("/api/records/wrong").json()["items"]
    other = scenario["u2"].get("/api/records/wrong").json()["items"]
    assert len(own) == 100 and len(other) == 0
    # 换一个客户端重新登录（模拟换浏览器/清缓存），服务端记录仍在
    fresh = T.client()
    T.login(fresh, "E001", "Pass@001")
    assert len(fresh.get("/api/records/wrong").json()["items"]) == 100
    assert fresh.get("/api/records/practice").json()["total"] == 0


# ───────────────────────── 管理端 / 导出 ─────────────────────────

def test_admin_actions_all_leave_audit_trail(scenario):
    admin = scenario["admin"]
    r = admin.post(f"/api/admin/users/{scenario['users']['E002']['id']}/reset-password")
    assert r.status_code == 200 and r.json()["temp_password"]
    admin.patch(f"/api/admin/users/{scenario['users']['E002']['id']}", json={"active": False})
    admin.post(f"/api/admin/banks/{scenario['bank']}/publish")
    admin.patch("/api/admin/templates/2", json={"status": "disabled"})
    admin.patch(f"/api/admin/batches/{scenario['batch']}", json={"status": "closed"})
    r = admin.get("/api/admin/audit")
    assert r.status_code == 200
    items = r.json()["items"]
    actions = {i["action"] for i in items}
    assert {"user.import", "user.reset_password", "user.active", "bank.publish",
            "template.status", "batch.status", "authorization.create", "batch.create"} <= actions
    assert all(i["actor"] and i["at"] and
               (i["before_value"] is not None or i["after_value"] is not None) for i in items)


def test_export_scope_and_db_aggregation_consistency(scenario):
    a = start_exam(scenario["u1"], scenario, seed="export-1")
    scenario["u1"].post(f"/api/exams/{a['attempt_id']}/submit",
                        json={"responses": correct_responses(scenario["db"], a["attempt_id"])})
    r = scenario["u1"].get("/api/reports/export", params={"format": "json"})
    assert r.status_code == 200
    rows = r.json()["rows"]
    assert len(rows) == 1 and rows[0]["employee_no"] == "E001"
    assert rows[0]["score"] == "100" and rows[0]["passed"] == "是"
    assert r.json()["pass_rate"] == 1.0
    all_rows = scenario["admin"].get("/api/reports/export", params={"format": "json"}).json()["rows"]
    assert len(all_rows) == 1
    conn = T.connect(scenario["db"])
    db_row = conn.execute(
        "SELECT u.employee_no, a.score, a.passed, p.total_score, t.pass_score, a.person_category_snapshot "
        "FROM attempts a JOIN users u ON u.id = a.user_id JOIN papers p ON p.id = a.paper_id "
        "JOIN paper_templates t ON t.id = p.template_id WHERE a.id = ?", (a["attempt_id"],)
    ).fetchone()
    conn.close()
    assert (rows[0]["employee_no"], rows[0]["score"], rows[0]["passed"], rows[0]["total_score"],
            rows[0]["pass_score"], rows[0]["person_category"]) == (
        db_row[0], str(int(db_row[1])), "是", str(int(db_row[3])), str(int(db_row[4])), db_row[5])
    # 考生越权导出他人 403
    other_id = scenario["users"]["E002"]["id"]
    assert scenario["u1"].get("/api/reports/export", params={"user_id": other_id}).status_code == 403
    csv_r = scenario["u1"].get("/api/reports/export", params={"format": "csv"})
    assert csv_r.status_code == 200 and csv_r.content.startswith(b"\xef\xbb\xbf")
