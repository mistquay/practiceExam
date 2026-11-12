"""P3/P5 集成测试：组卷引擎、锁卷可复现、并发交卷、报表、迁移与回溯。

运行：/d/services/mamba_envs/ame/python.exe -m pytest tests/test_integration.py -q
"""
from __future__ import annotations

import csv
import io
import json
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.db.migrate import downgrade, status as migration_status, upgrade  # noqa: E402
from app.rules.paper import compose_paper, load_pool, template_for  # noqa: E402
from app.rules.templates import exclude_b_ids, required_19  # noqa: E402
from app.services import testkit as T  # noqa: E402

pytestmark = pytest.mark.skipif(not T.SOURCE_DB.is_file(), reason="缺少 data/exam.db")


@pytest.fixture
def env(tmp_path, monkeypatch):
    return T.prepare(tmp_path, monkeypatch)


@pytest.fixture
def scenario(env):
    admin = T.client()
    T.admin(admin)
    templates = T.activate_templates(admin)
    T.import_users(admin, [
        {"employee_no": "E001", "name": "张三", "team": "配电一班", "role": "examinee", "initial_password": "Pass@001"},
        {"employee_no": "E002", "name": "李四", "team": "配电二班", "role": "examinee", "initial_password": "Pass@002"},
    ])
    by_no = {u["employee_no"]: u for u in admin.get("/api/admin/users").json()["users"]}
    T.authorize(admin, by_no["E001"]["id"], "重点人员", "pd_live")
    T.authorize(admin, by_no["E002"]["id"], "一线人员", "pd_live")
    conn = T.connect(env)
    bank = T.active_bank_id(conn)
    conn.close()
    u1 = T.client()
    T.login(u1, "E001", "Pass@001")
    return {"db": env, "admin": admin, "u1": u1, "users": by_no,
            "bank": bank, "templates": templates}


def open_batch(scenario, code="T-INT", max_attempts=200, template_id=None):
    return T.open_batch(scenario["admin"], template_id or scenario["templates"][0],
                        scenario["bank"], code, max_attempts=max_attempts,
                        examinee_list=["E001", "E002"])


def start(c, scenario, batch, seed, template_id=None):
    r = c.post("/api/exams/start", json={
        "batch_id": batch, "template_id": template_id or scenario["templates"][0],
        "specialty_code": "pd_live", "seed": seed,
    })
    assert r.status_code == 200, (r.status_code, r.text[:400])
    return r.json()


def snapshot_rows(db: Path, paper_id: int) -> list[tuple[str, bytes]]:
    conn = T.connect(db)
    rows = conn.execute(
        "SELECT stable_id, snapshot_json FROM paper_questions WHERE paper_id = ? ORDER BY seq", (paper_id,)
    ).fetchall()
    conn.close()
    return [(r[0], r[1].encode("utf-8") if isinstance(r[1], str) else r[1]) for r in rows]


def correct_responses_from(db: Path, paper_id: int) -> list[dict]:
    return [{"stable_id": sid, "choice": json.loads(raw.decode("utf-8"))["answer"]}
            for sid, raw in snapshot_rows(db, paper_id)]


# ───────────────────────── 组卷引擎 ─────────────────────────

def test_100_seeds_keep_19_required_and_all_deterministic_rules(env):
    conn = T.connect(env)
    bank = T.active_bank_id(conn)
    required = set(required_19())
    excluded = set(exclude_b_ids())
    color_group = {r[0] for r in conn.execute(
        "SELECT DISTINCT stable_id FROM question_tags WHERE tag_type = 'color_group'")}
    legacy_yellow = {r[0] for r in conn.execute(
        "SELECT DISTINCT stable_id FROM question_tags WHERE tag_type = 'data_issue' AND tag_value = '遗留黄色标记'")}
    db_required = {r[0] for r in conn.execute(
        "SELECT stable_id FROM question_versions WHERE bank_version_id = ? AND is_required = 1 AND retired = 0",
        (bank,))}
    all_ids = {r[0] for r in conn.execute("SELECT stable_id FROM questions")}
    assert len(required) == 19 and db_required == required
    assert not (required & color_group) and not (required & legacy_yellow)

    hashes = set()
    checked = 0
    for tid in (1, 2):
        template = template_for(conn, tid)
        for specialty in ("pd_general", "pd_live"):
            pool = load_pool(conn, template, template["person_category"], specialty, strict=True)
            by_id = {q["stable_id"]: q for q in pool}
            seeds = [f"seed-{tid}-{specialty}-{i}" for i in range(25)]
            if tid == 1 and specialty == "pd_live":
                seeds = [f"seed-main-{i}" for i in range(100)]  # 100 个不同 seed
            for seed in seeds:
                draft = compose_paper(conn, template, seed, template["person_category"],
                                      specialty, pool_rows=pool)
                ids = [i["stable_id"] for i in draft.items]
                got_required = {i["stable_id"] for i in draft.items if i["is_required"]}
                assert got_required == required, (seed, len(got_required))
                assert len(ids) == 100 and len(set(ids)) == 100
                assert draft.total_score == 100.0
                assert not (set(ids) & excluded), f"{seed} 命中口径 B 排除集合"
                assert not any(i["qtype"] == "简答题" for i in draft.items)
                assert set(ids) <= all_ids
                for i in draft.items:
                    q = by_id[i["stable_id"]]
                    assert q["has_person"] and q["has_specialty"]
                hashes.add(draft.snapshot_hash)
                checked += 1
            # 同 seed 重放逐字节一致
            a = compose_paper(conn, template, "replay-seed", template["person_category"],
                              specialty, pool_rows=pool)
            b = compose_paper(conn, template, "replay-seed", template["person_category"],
                              specialty, pool_rows=pool)
            assert a.snapshot_hash == b.snapshot_hash
            assert json.dumps(a.items, ensure_ascii=False, sort_keys=True) == json.dumps(
                b.items, ensure_ascii=False, sort_keys=True)
    conn.close()
    assert checked >= 100
    assert len(hashes) == checked, "不同 seed 必须生成不同试卷"


def test_enumerable_exclude_sets_a_b_c_have_zero_hits(env):
    """A / B / C 三份可枚举 stable_id 集合：本次启用 B，断言卷内 0 命中。"""
    conn = T.connect(env)
    bank = T.active_bank_id(conn)
    exclude_b = set(exclude_b_ids())
    exclude_a = {r[0] for r in conn.execute(
        "SELECT DISTINCT stable_id FROM question_versions WHERE bank_version_id = ? AND retired = 0 "
        "AND outline_l1 IN ('通信安规', '信息安规')", (bank,))}
    exclude_c: set[str] = set()   # 口径 C = 人员维度整体不参加，不改题目集合
    assert len(exclude_b) == 566 and exclude_a
    assert exclude_a <= exclude_b, "口径 A 的题目应被口径 B 覆盖"
    template = template_for(conn, 1)
    pool = load_pool(conn, template, "重点人员", "pd_live")
    for seed in (f"exclude-{i}" for i in range(20)):
        ids = {i["stable_id"] for i in compose_paper(
            conn, template, seed, "重点人员", "pd_live", pool_rows=pool).items}
        assert not (ids & exclude_b), "B"
        assert not (ids & exclude_a), "A"
        assert not (ids & exclude_c), "C"
    conn.close()


# ───────────────────────── 锁卷 / 勘误 ─────────────────────────

def test_locked_snapshot_survives_answer_update_and_seed_replay(scenario):
    batch = open_batch(scenario, "LOCK-1")
    c = scenario["u1"]
    a = start(c, scenario, batch, "lock-seed-1")
    conn = T.connect(scenario["db"])
    paper_id = conn.execute("SELECT paper_id FROM attempts WHERE id = ?", (a["attempt_id"],)).fetchone()[0]
    original = snapshot_rows(scenario["db"], paper_id)
    target_sid, raw = original[0]
    snap = json.loads(raw.decode("utf-8"))
    old_answer = snap["answer"]
    responses = correct_responses_from(scenario["db"], paper_id)
    r = c.post(f"/api/exams/{a['attempt_id']}/submit", json={"responses": responses})
    assert r.status_code == 200 and r.json()["score"] == 100

    # 模拟题库答案勘误：直接 UPDATE 正在被历史卷使用的题
    new_answer = "Z" if old_answer != "Z" else "Y"
    conn.execute("UPDATE question_versions SET answer = ? WHERE bank_version_id = ? AND stable_id = ? "
                 "AND retired = 0", (new_answer, scenario["bank"], target_sid))
    conn.commit()
    conn.close()

    # 历史卷面逐字节不变
    assert snapshot_rows(scenario["db"], paper_id) == original
    # 同 seed 重放复用已锁卷 papers 行，hash / 卷面 / 分数不变
    replay = start(c, scenario, batch, "lock-seed-1")
    conn = T.connect(scenario["db"])
    replay_paper_id = conn.execute("SELECT paper_id FROM attempts WHERE id = ?",
                                   (replay["attempt_id"],)).fetchone()[0]
    conn.close()
    assert replay_paper_id == paper_id
    assert replay["snapshot_hash"] == a["snapshot_hash"]
    assert snapshot_rows(scenario["db"], paper_id) == original
    r2 = c.post(f"/api/exams/{replay['attempt_id']}/submit", json={"responses": responses})
    assert r2.json()["score"] == 100, "历史卷仍按旧快照答案判分"
    conn = T.connect(scenario["db"])
    assert conn.execute("SELECT score FROM attempts WHERE id = ?", (a["attempt_id"],)).fetchone()[0] == 100
    conn.close()


def test_new_bank_version_does_not_change_historical_scores(scenario):
    batch = open_batch(scenario, "VER-1")
    a = start(scenario["u1"], scenario, batch, "version-seed")
    conn = T.connect(scenario["db"])
    paper_id = conn.execute("SELECT paper_id FROM attempts WHERE id = ?", (a["attempt_id"],)).fetchone()[0]
    before = snapshot_rows(scenario["db"], paper_id)
    conn.close()
    scenario["u1"].post(f"/api/exams/{a['attempt_id']}/submit",
                        json={"responses": correct_responses_from(scenario["db"], paper_id)})
    # 发版：新增 1 个版本 / 退役旧版（publish 会把旧版归档）
    conn = T.connect(scenario["db"])
    conn.execute("INSERT INTO bank_versions (code, name, status) VALUES ('2027-01', '2027 版', 'draft')")
    conn.commit()
    new_id = conn.execute("SELECT id FROM bank_versions WHERE code='2027-01'").fetchone()[0]
    conn.close()
    r = scenario["admin"].post(f"/api/admin/banks/{new_id}/publish")
    assert r.status_code == 200
    assert snapshot_rows(scenario["db"], paper_id) == before
    conn = T.connect(scenario["db"])
    assert conn.execute("SELECT score FROM attempts WHERE id = ?", (a["attempt_id"],)).fetchone()[0] == 100
    # 运行库只保留当日 2026-11 版；legacy 已移到 archive/，不再出现在运行库
    assert conn.execute("SELECT COUNT(*) FROM bank_versions WHERE code IN ('2026-11','2027-01')").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM bank_versions WHERE code='legacy'").fetchone()[0] == 0
    conn.close()


# ───────────────────────── 50 路并发交卷 ─────────────────────────

def test_50_concurrent_submits_no_lock_errors_no_lost_scores(scenario):
    batch = open_batch(scenario, "CONC-50", max_attempts=200)
    seed = "concurrent-shared-paper"
    attempts = [start(scenario["u1"], scenario, batch, seed)["attempt_id"] for _ in range(50)]
    conn = T.connect(scenario["db"])
    paper_id = conn.execute("SELECT paper_id FROM attempts WHERE id = ?", (attempts[0],)).fetchone()[0]
    conn.close()
    conn = T.connect(scenario["db"])
    assert conn.execute("SELECT COUNT(DISTINCT paper_id) FROM attempts WHERE id BETWEEN ? AND ?",
                        (attempts[0], attempts[-1])).fetchone()[0] == 1
    conn.close()
    right = correct_responses_from(scenario["db"], paper_id)
    wrong = [{"stable_id": x["stable_id"], "choice": "Z"} for x in right]

    def worker(args):
        idx, attempt_id = args
        c = T.client()
        T.login(c, "E001", "Pass@001")
        r = c.post(f"/api/exams/{attempt_id}/submit",
                   json={"responses": right if idx % 2 == 0 else wrong,
                         "score": 999, "is_correct": True})
        return r.status_code, r.text

    with ThreadPoolExecutor(max_workers=50) as pool:
        results = list(pool.map(worker, enumerate(attempts)))
    assert all(s == 200 for s, _ in results), [x for x in results if x[0] != 200][:3]
    assert not any("database is locked" in text.lower() for _, text in results)
    conn = T.connect(scenario["db"])
    rows = conn.execute("SELECT id, score, status FROM attempts WHERE id BETWEEN ? AND ? ORDER BY id",
                        (attempts[0], attempts[-1])).fetchall()
    answer_rows = conn.execute(
        "SELECT COUNT(*) FROM attempt_answers WHERE attempt_id BETWEEN ? AND ?",
        (attempts[0], attempts[-1])).fetchone()[0]
    conn.close()
    assert len(rows) == 50 and answer_rows == 50 * 100
    assert all(r[2] == "submitted" for r in rows)
    assert sum(r[1] for r in rows) == 25 * 100, "0 丢分"
    assert sorted({r[1] for r in rows}) == [0, 100.0]


# ───────────────────────── 报表 ─────────────────────────

def test_export_100_attempts_matches_db_aggregation_field_by_field(scenario):
    batch = open_batch(scenario, "EXPORT-100", max_attempts=200)
    seed = "export-shared-paper"
    attempts = [start(scenario["u1"], scenario, batch, seed)["attempt_id"] for _ in range(100)]
    conn = T.connect(scenario["db"])
    paper_id = conn.execute("SELECT paper_id FROM attempts WHERE id = ?", (attempts[0],)).fetchone()[0]
    conn.close()
    right = correct_responses_from(scenario["db"], paper_id)
    for i, aid in enumerate(attempts[:40]):
        scenario["u1"].post(f"/api/exams/{aid}/submit",
                            json={"responses": right if i % 2 == 0 else []})
    r = scenario["admin"].get("/api/reports/export", params={"batch_id": batch, "format": "json"})
    assert r.status_code == 200
    payload = r.json()
    rows = payload["rows"]
    assert len(rows) == 100
    assert payload["graded_count"] == 40 and payload["passed_count"] == 20
    assert payload["pass_rate"] == 0.5
    assert abs(sum(1 for x in rows if x["passed"] == "是") / 40 - payload["pass_rate"]) < 1e-9

    conn = T.connect(scenario["db"])
    by_id = {x["attempt_id"]: x for x in rows}
    for row in conn.execute(
        "SELECT a.id, u.employee_no, u.name, u.team, a.person_category_snapshot, a.specialty_snapshot, "
        "bv.code, t.code, b.code, a.started_at, a.submitted_at, a.score, p.total_score, t.pass_score, "
        "a.passed, a.status, p.id AS paper_id FROM attempts a "
        "JOIN users u ON u.id=a.user_id JOIN papers p ON p.id=a.paper_id "
        "JOIN paper_templates t ON t.id=p.template_id JOIN bank_versions bv ON bv.id=p.bank_version_id "
        "LEFT JOIN exam_batches b ON b.id=a.batch_id WHERE a.batch_id = ?", (batch,)
    ).fetchall():
        exp = by_id[row["id"]]
        assert exp["employee_no"] == row["employee_no"]
        assert exp["name"] == row["name"] and exp["team"] == row["team"]
        assert exp["person_category"] == row["person_category_snapshot"]
        assert exp["specialty_code"] == row["specialty_snapshot"]
        assert exp["bank_version"] == row["code"]
        assert exp["attempt_id"] == row["id"]
        assert exp["started_at"] == row["started_at"]
        assert exp["submitted_at"] == (row["submitted_at"] or "")
        assert exp["score"] == ("" if row["score"] is None else str(int(row["score"])))
        assert exp["total_score"] == str(int(row["total_score"]))
        assert exp["pass_score"] == str(int(row["pass_score"]))
        assert exp["passed"] == ("" if row["passed"] is None else ("是" if row["passed"] else "否"))
        required_score = sum(x[0] or 0 for x in conn.execute(
            "SELECT aa.score FROM attempt_answers aa JOIN paper_questions pq "
            "ON pq.paper_id=? AND pq.stable_id=aa.stable_id "
            "WHERE aa.attempt_id=? AND pq.is_required=1", (row["paper_id"], row["id"])))
        assert exp["required_score"] == (str(int(required_score)) if required_score else "0")
    conn.close()

    csv_r = scenario["admin"].get("/api/reports/export", params={"batch_id": batch, "format": "csv"})
    reader = list(csv.DictReader(io.StringIO(csv_r.content.decode("utf-8-sig"))))
    assert len(reader) == 100
    assert reader[0]["用户名"] == rows[0]["employee_no"]
    assert reader[0]["得分"] == rows[0]["score"]


# ───────────────────────── 人工阅卷 ─────────────────────────

def test_short_answer_manual_grading_queue_and_audit(scenario):
    batch = open_batch(scenario, "GRADE-1")
    a = start(scenario["u1"], scenario, batch, "grade-seed")
    conn = T.connect(scenario["db"])
    paper_id = conn.execute("SELECT paper_id FROM attempts WHERE id = ?", (a["attempt_id"],)).fetchone()[0]
    sid, raw = snapshot_rows(scenario["db"], paper_id)[0]
    snap = json.loads(raw.decode("utf-8"))
    snap["qtype"] = "简答题"
    snap["answer"] = "评分要点：应写明停电、验电、挂接地线三项措施。"
    conn.execute("UPDATE paper_questions SET snapshot_json = ? WHERE paper_id = ? AND stable_id = ?",
                 (json.dumps(snap, ensure_ascii=False, sort_keys=True), paper_id, sid))
    conn.commit()
    conn.close()
    r = scenario["u1"].post(f"/api/exams/{a['attempt_id']}/submit", json={
        "responses": [{"stable_id": sid, "choice": "停电、验电、挂接地线"}]})
    assert r.status_code == 200 and r.json()["status"] == "pending_review"
    pending = scenario["admin"].get("/api/admin/grading/pending").json()["items"]
    assert len(pending) == 1 and pending[0]["stable_id"] == sid
    assert "评分要点" in pending[0]["reference"]
    g = scenario["admin"].post("/api/admin/grading", json={
        "attempt_id": a["attempt_id"], "stable_id": sid, "score": 1.0, "comment": "措施齐全"})
    assert g.status_code == 200
    conn = T.connect(scenario["db"])
    assert conn.execute("SELECT COUNT(*) FROM grading_reviews WHERE attempt_id = ?",
                        (a["attempt_id"],)).fetchone()[0] == 1
    assert conn.execute("SELECT status FROM attempts WHERE id = ?", (a["attempt_id"],)).fetchone()[0] == "graded"
    assert conn.execute("SELECT COUNT(*) FROM audit_logs WHERE action='grading.manual'").fetchone()[0] == 1
    conn.close()
    assert scenario["admin"].get("/api/admin/grading/pending").json()["items"] == []


# ───────────────────────── 迁移 / 回溯 ─────────────────────────

def test_migration_up_down_up_on_db_copy(tmp_path):
    """在 db 副本上正向 → 回滚 → 正向；回滚快照保留旧数据可查。"""
    db = tmp_path / "mig" / "roundtrip.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    assert upgrade(db) == ["0001", "0002", "0003", "0004"]
    conn = sqlite3.connect(db)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "attempts" in tables and "schema_migrations" in tables
    conn.execute("INSERT INTO bank_versions (code, name, status) VALUES ('legacy', '旧题库', 'archived')")
    conn.execute("INSERT INTO audit_logs (actor, action, at, after_value) VALUES ('etl', 'legacy.score', "
                 "'2020-01-01T00:00:00+00:00', '{\"score\": 88}')")
    conn.commit()
    conn.close()

    assert downgrade(db) == ["0004", "0003", "0002", "0001"]
    # 第一轮回滚（最新迁移 0004）的归档才包含回滚前业务数据。
    archive = sorted((db.parent / "backups").glob("roundtrip.rollback-from-0004.*.db"))
    assert archive, "回滚前必须 VACUUM INTO 归档"
    conn = sqlite3.connect(archive[-1])
    assert conn.execute("SELECT COUNT(*) FROM bank_versions WHERE code='legacy'").fetchone()[0] == 1
    assert conn.execute("SELECT after_value FROM audit_logs WHERE action='legacy.score'").fetchone()[0] == '{"score": 88}'
    conn.close()

    assert upgrade(db) == ["0001", "0002", "0003", "0004"]
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM bank_versions WHERE code='legacy'").fetchone()[0] == 0
    conn.close()
    assert migration_status(db)[0]["applied"] is True


def test_upgrade_idempotent_on_real_db_and_bank_still_queryable(scenario):
    """升级（重复 upgrade / 发布新版本）后当日题库版本与历史成绩仍可查。"""
    db = scenario["db"]
    assert upgrade(db) == []                     # 幂等，不重复执行迁移
    batch = open_batch(scenario, "MIG-1")
    a = start(scenario["u1"], scenario, batch, "mig-seed")
    conn = T.connect(db)
    paper_id = conn.execute("SELECT paper_id FROM attempts WHERE id = ?", (a["attempt_id"],)).fetchone()[0]
    conn.close()
    scenario["u1"].post(f"/api/exams/{a['attempt_id']}/submit",
                        json={"responses": correct_responses_from(db, paper_id)})
    conn = T.connect(db)
    bank = conn.execute("SELECT status FROM bank_versions WHERE code='2026-11'").fetchone()
    active_questions = conn.execute(
        "SELECT COUNT(*) FROM question_versions qv JOIN bank_versions b ON b.id=qv.bank_version_id "
        "WHERE b.code='2026-11' AND qv.retired=0").fetchone()[0]
    conn.close()
    assert bank is not None and bank[0] == "active" and active_questions == 11450
    conn = T.connect(db)
    conn.execute("INSERT INTO bank_versions (code, name, status) VALUES ('2027-06', '新版', 'draft')")
    conn.commit()
    new_id = conn.execute("SELECT id FROM bank_versions WHERE code='2027-06'").fetchone()[0]
    conn.close()
    assert scenario["admin"].post(f"/api/admin/banks/{new_id}/publish").status_code == 200
    assert upgrade(db) == []
    conn = T.connect(db)
    assert conn.execute("SELECT score FROM attempts WHERE id = ?", (a["attempt_id"],)).fetchone()[0] == 100
    assert conn.execute("SELECT COUNT(*) FROM bank_versions WHERE status='active'").fetchone()[0] == 1
    conn.close()
