"""开放练习、真实多来源、组合筛选与正式考试权限回归（仅隔离副本）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services import testkit as T
from app.services.dto import FORBIDDEN_TOKENS

pytestmark = pytest.mark.skipif(not T.SOURCE_DB.is_file(), reason="缺少题库")


@pytest.fixture
def setup(tmp_path, monkeypatch):
    db = T.prepare(tmp_path, monkeypatch)
    admin = T.client()
    T.admin(admin)
    T.import_users(admin, [{"employee_no": "practice_only", "name": "练习用户", "role": "examinee",
                            "initial_password": "Practice@123"}])
    user = T.client()
    T.login(user, "practice_only", "Practice@123")
    conn = T.connect(db)
    bank = T.active_bank_id(conn)
    conn.close()
    return db, admin, user, bank


def test_no_authorization_can_browse_practice_submit_and_start_exam(setup):
    db, admin, user, bank = setup
    assert user.get("/api/auth/me").json()["authorization"] is None
    assert user.get("/api/banks").json()["banks"]
    assert user.get(f"/api/banks/{bank}/dimension").status_code == 200
    conn = T.connect(db)
    expected = conn.execute("SELECT COUNT(*) FROM question_versions WHERE bank_version_id = ? AND retired = 0", (bank,)).fetchone()[0]
    conn.close()
    r = user.get("/api/practice/questions", params={"bank_id": bank, "count": 3,
                 "specialty_code": "pd_live"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["total"] == expected and data["returned"] == len(data["questions"]) == 3
    q = data["questions"][0]
    r = user.post("/api/practice/submit", json={"bank_id": bank, "stable_id": q["stable_id"], "user_answer": q["answer"]})
    assert r.status_code == 200 and r.json()["answer"] == q["answer"]
    T.activate_templates(admin)
    batch = T.open_batch(admin, 1, bank, "PRACTICE-NO-AUTH", examinee_list=["practice_only"])
    assert any(item["batch_id"] == batch for item in user.get("/api/exams/available").json()["items"])
    r = user.post("/api/exams/start", json={"batch_id": batch, "template_id": 1, "specialty_code": "pd_live"})
    assert r.status_code == 200, r.text
    conn = T.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM practice_records").fetchone()[0] == 1
    conn.close()
    anonymous = T.client()
    for path in ("/api/banks", "/api/practice/questions", "/api/practice/filters"):
        assert anonymous.get(path).status_code == 401


def test_tags_sources_combinations_and_empty_results(setup):
    db, _, user, bank = setup
    conn = T.connect(db)
    for tag, column in (("protected", "is_protected"), ("required", "is_required")):
        expected = {r[0] for r in conn.execute(
            f"SELECT stable_id FROM question_versions WHERE bank_version_id = ? AND retired = 0 AND {column} = 1", (bank,))}
        params = [("bank_id", bank), ("tags", tag), ("count", 200)]
        data = user.get("/api/practice/questions", params=params).json()
        assert data["total"] == len(expected) > 0
        assert {q["stable_id"] for q in data["questions"]} <= expected
        assert all(q[column] == 1 for q in data["questions"])
    # 真实多来源题：至少一个来源不是代表版本的 source_file_id。
    row = conn.execute(
        "SELECT q.stable_id, q.qtype, q.source_file_id, GROUP_CONCAT(qs.source_file_id) AS sources "
        "FROM question_versions q JOIN question_sources qs ON qs.bank_version_id=q.bank_version_id "
        "AND qs.stable_id=q.stable_id WHERE q.bank_version_id=? AND q.retired=0 AND q.is_required=1 "
        "GROUP BY q.stable_id HAVING COUNT(*) > 1 LIMIT 1", (bank,)).fetchone()
    assert row is not None
    ids = [int(i) for i in row["sources"].split(",")]
    for fid in ids:
        data = user.get("/api/practice/questions", params={"bank_id": bank, "source_file_ids": fid,
                        "tags": "required", "qtype": row["qtype"], "count": 200, "order": "random"}).json()
        assert row["stable_id"] in {q["stable_id"] for q in data["questions"]}
        assert all(q["is_required"] == 1 and q["qtype"] == row["qtype"] for q in data["questions"])
    params = [("bank_id", bank), ("tags", "required"), ("tags", "protected"), ("count", 200)]
    params.extend(("source_file_ids", i) for i in ids)
    data = user.get("/api/practice/questions", params=params).json()
    selected = [q["stable_id"] for q in data["questions"]]
    assert row["stable_id"] in selected and len(selected) == len(set(selected))
    facet_params = [(k, v) for k, v in params if k != "count"]
    facets = user.get("/api/practice/filters", params=facet_params).json()
    assert facets["total"] == data["total"]
    assert all("rel_path" in s and "sheet" in s for s in facets["sources"])
    # 匹配数与实际 SQL 交集一致，非仅校验请求走了哪一分支。
    marks = ",".join("?" for _ in ids)
    expected = conn.execute(
        "SELECT COUNT(*) FROM question_versions q WHERE q.bank_version_id=? AND q.retired=0 "
        "AND q.is_required=1 AND q.is_protected=1 AND EXISTS (SELECT 1 FROM question_sources qs "
        "WHERE qs.bank_version_id=q.bank_version_id AND qs.stable_id=q.stable_id "
        f"AND qs.source_file_id IN ({marks}))", (bank, *ids)).fetchone()[0]
    assert data["total"] == expected
    empty_params = {"bank_id": bank, "tags": "required", "qtype": "简答题"}
    empty = user.get("/api/practice/questions", params=empty_params).json()
    assert empty == {"questions": [], "total": 0, "returned": 0}
    assert user.get("/api/practice/filters", params=empty_params).json()["total"] == 0
    for token in FORBIDDEN_TOKENS:
        assert token.lower() not in json.dumps(facets, ensure_ascii=False).encode("utf-8").lower()
    conn.close()


def test_invalid_filters_are_rejected_and_inactive_bank_is_hidden(setup):
    db, _, user, bank = setup
    invalid = [{"tags": "invented"}, {"tags": "protected' OR 1=1--"},
               {"tags": "color_group:not-real"}, {"source_file_ids": -1},
               {"source_file_ids": "not-an-id"}, {"qtype": "invalid"},
               {"source": "invalid"}, {"order": "invalid"}]
    for endpoint in ("questions", "filters"):
        for params in invalid:
            r = user.get(f"/api/practice/{endpoint}", params={"bank_id": bank, **params})
            assert r.status_code in (400, 422), (params, r.text)
    conn = T.connect(db)
    conn.execute("UPDATE bank_versions SET status='archived' WHERE id=?", (bank,))
    conn.commit()
    conn.close()
    assert user.get("/api/practice/questions", params={"bank_id": bank}).status_code == 404
    assert user.get("/api/practice/filters", params={"bank_id": bank}).status_code == 404
    assert user.get("/api/banks").json()["banks"] == []


def test_seed_matches_all_original_source_relationships_and_is_idempotent(setup):
    db, _, _, bank = setup
    fixture = json.loads((ROOT / "fixtures/question_sources_2026-11.json").read_text(encoding="utf-8"))
    conn = T.connect(db)
    mapping = {(r["rel_path"], r["sheet"]): r["id"] for r in conn.execute(
        "SELECT * FROM source_files WHERE bank_version_id=?", (bank,))}
    expected = {(sid, mapping[(s["path"], s["sheet"])]) for s in fixture["sources"] for sid in s["ids"]}
    actual = {(r[0], r[1]) for r in conn.execute(
        "SELECT stable_id, source_file_id FROM question_sources WHERE bank_version_id=?", (bank,))}
    assert actual == expected
    assert len(actual) == 30969
    from app.services.question_sources import seed_legacy_sources
    seed_legacy_sources(conn)
    assert conn.execute("SELECT COUNT(*) FROM question_sources WHERE bank_version_id=?", (bank,)).fetchone()[0] == len(actual)
    conn.close()


def test_user_visible_copy_and_practice_entry_are_independent():
    for directory in (ROOT / "static", ROOT / "app"):
        for path in directory.rglob("*"):
            if path.suffix in (".js", ".html", ".py"):
                assert "工号" not in path.read_text(encoding="utf-8"), str(path)
    home = (ROOT / "static/views/home.js").read_text(encoding="utf-8")
    picker = (ROOT / "static/views/practice-picker.js").read_text(encoding="utf-8")
    practice = (ROOT / "static/views/practice.js").read_text(encoding="utf-8")
    assert "renderPracticePicker(practiceBox" in home
    assert "auth.person_category" not in picker and "specialtyCode" not in picker
    assert 'query.get("specialty"))' not in practice
    assert 'role: "status"' in picker and '"aria-live": "polite"' in picker
    assert '"用户名"' in (ROOT / "static/views/login.js").read_text(encoding="utf-8")


def test_real_tags_personal_ranges_and_bounded_queries(setup):
    db, _, user, bank = setup
    facets = user.get("/api/practice/filters", params={"bank_id": bank}).json()
    conn = T.connect(db)
    for tag in (t["code"] for t in facets["tags"] if ":" in t["code"]):
        kind, value = tag.split(":", 1)
        expected = conn.execute(
            "SELECT COUNT(*) FROM question_versions q WHERE q.bank_version_id=? AND q.retired=0 "
            "AND EXISTS (SELECT 1 FROM question_tags t WHERE t.bank_version_id=q.bank_version_id "
            "AND t.stable_id=q.stable_id AND t.tag_type=? AND t.tag_value=?)", (bank, kind, value)).fetchone()[0]
        response = user.get("/api/practice/questions", params={"bank_id": bank, "tags": tag, "count": 1})
        assert response.status_code == 200
        assert response.json()["total"] == expected > 0
    q = user.get("/api/practice/questions", params={"bank_id": bank, "count": 1}).json()["questions"][0]
    user.post("/api/practice/submit", json={"bank_id": bank, "stable_id": q["stable_id"], "user_answer": ""})
    wrong = user.get("/api/practice/questions", params={"bank_id": bank, "source": "wrong"}).json()
    assert wrong["total"] == 1 and wrong["questions"][0]["stable_id"] == q["stable_id"]
    all_count = user.get("/api/practice/filters", params={"bank_id": bank}).json()["total"]
    unpracticed = user.get("/api/practice/questions", params={"bank_id": bank, "source": "unpracticed"}).json()
    assert unpracticed["total"] == all_count - 1
    assert q["stable_id"] not in {x["stable_id"] for x in unpracticed["questions"]}
    from app.services.practice import questions
    current = dict(conn.execute("SELECT * FROM users WHERE employee_no=?", ("practice_only",)).fetchone())
    traced = []
    conn.set_trace_callback(traced.append)
    result = questions(conn, current, bank_id=bank, count=200, source_file_ids=[facets["sources"][0]["id"]])
    assert result["questions"]
    assert len([s for s in traced if s.lstrip().upper().startswith("SELECT")]) <= 5
    conn.close()
