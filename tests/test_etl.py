"""P0 数据治理验收：ETL 解析 / 去重 / 标记 / 报告确定性 / 入库 / 迁移。

运行（需 ETL 依赖，见 requirements-etl.txt）：
    <venv>/Scripts/python -m pytest tests/test_etl.py

关于「唯一题 11,456」：方案中的 11,456 是按「题干 + 选项原文 + 答案」去重的结果，本测试原样复现该数字
（raw_key_variants）。但契约同时要求 stable_id 不含答案，因此身份层唯一题 = 11,456 − 5（同题同选项、仅答案
不同）− 1（33-3 第 16 行选项多打一个竖线 `||`，与 33-1 第 40 行是同一题）= 11,450；5 个少数派答案
以 question_versions.retired=1 / retire_reason=answer_variant 留档，入库版本行合计 11,455。
"""
from __future__ import annotations

import copy
import dataclasses
import json
import re
import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import yaml  # noqa: E402

from app.db import connect  # noqa: E402
from app.db.migrate import downgrade, upgrade  # noqa: E402
from app.models import AUTO_GRADABLE_QTYPES, QTYPE_SHORT  # noqa: E402
from etl import fixtures as F  # noqa: E402
from etl import normalize as N  # noqa: E402
from etl.cli import load_prepared, prepare, run_import  # noqa: E402
from etl.loader import load  # noqa: E402

SOURCE = REPO_ROOT / "2026-11-12"
LEGACY_DB = REPO_ROOT / "archive" / "questions.db"
VERSION = "2026-11"

pytestmark = pytest.mark.skipif(not SOURCE.is_dir(), reason="缺少资料目录 2026-11-12/")

ALL_TABLES = {
    "bank_versions", "source_files", "questions", "question_versions", "tags", "question_tags", "specialties",
    "person_authorizations", "users", "exam_batches", "paper_templates", "papers", "paper_questions", "attempts",
    "attempt_answers", "grading_reviews", "practice_records", "wrong_book", "audit_logs",
}


# ───────────────────────── fixtures ─────────────────────────

@pytest.fixture(scope="session")
def prep():
    return prepare(SOURCE, VERSION)


@pytest.fixture(scope="session")
def runs(tmp_path_factory):
    """两次独立的完整导入：第 1 次 dry-run，第 2 次带 --load；报告文件必须逐字节一致。"""
    d1, d2 = tmp_path_factory.mktemp("report1"), tmp_path_factory.mktemp("report2")
    db = tmp_path_factory.mktemp("db") / "exam.db"
    r1 = run_import(SOURCE, VERSION, report_dir=d1)
    r2 = run_import(SOURCE, VERSION, report_dir=d2, load_db=db)
    legacy = run_import(LEGACY_DB, "legacy", report_dir=d2, load_db=db)
    return {"r1": r1, "r2": r2, "d1": d1, "d2": d2, "db": db, "legacy": legacy}


@pytest.fixture(scope="session")
def report(runs):
    return runs["r1"]


@pytest.fixture(scope="session")
def db(runs):
    conn = sqlite3.connect(runs["db"])
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


def _bank_id(conn, code):
    return conn.execute("SELECT id FROM bank_versions WHERE code = ?", (code,)).fetchone()[0]


# ───────────────────────── 规范化单元 ─────────────────────────

@pytest.mark.parametrize("raw, qtype, expected", [
    ("A-甲|B-乙|C-丙", "单选题", {"A": "甲", "B": "乙", "C": "丙"}),
    ("A.1月底前|B.6月底前", "单选题", {"A": "1月底前", "B": "6月底前"}),
    ("A. 风险辨识 |B. 隐患整改", "多选题", {"A": "风险辨识", "B": "隐患整改"}),
    ("A-开灯丨B-关阀", "单选题", {"A": "开灯", "B": "关阀"}),
    ("A-宜配备急救箱，B-箱内配齐，C-专人检查", "多选题", {"A": "宜配备急救箱", "B": "箱内配齐", "C": "专人检查"}),
    ("A-升级或备份||B-升级或配置更改", "单选题", {"A": "升级或备份", "B": "升级或配置更改"}),
    ("A-Ps-ef|B-x|C-y|D-Cat/etc/inet|D-conf", "多选题", {"A": "Ps-ef", "B": "x", "C": "y", "D": "Cat/etc/inet|D-conf"}),
    ("", "判断题", {"A": "正确", "B": "错误"}),
    ("\\", "判断题", {"A": "正确", "B": "错误"}),
    ("\\", "简答题", {}),
    ("答：要点一；要点二", "简答题", {}),
])
def test_parse_options(raw, qtype, expected):
    assert N.parse_options(raw, qtype)[0] == expected


def test_stable_id_excludes_answer_score_analysis():
    stem, opts = N.identity_input(" 题 干\u3000\n", {"B": "乙 ", "A": "甲"})
    assert (stem, opts) == ("题干", "A-甲|B-乙")
    sid = N.make_stable_id(stem, opts)
    assert len(sid) == 40 and sid == N.make_stable_id("题干", "A-甲|B-乙")
    assert sid != N.make_stable_id("题干", "A-甲|B-丙")


def test_answer_and_qtype_normalization():
    assert N.norm_answer(" a, c ", "多选题") == "AC"
    assert N.norm_answer("\\", "单选题") == ""
    assert N.norm_qtype("单选") == "单选题" and N.norm_qtype("判断题") == "判断题" and N.norm_qtype("简答") == "简答题"


def test_score_mapping_is_total():
    assert N.map_score("1", "单选题") == (1.0, "native")
    assert N.map_score("0.5", "判断题") == (0.5, "native")
    assert N.map_score("2", "多选题") == (2.0, "native")
    assert N.map_score("5", "简答题") == (5.0, "native")
    assert N.map_score("\\", "简答题") == (None, "placeholder")
    assert N.map_score("", "单选题") == (1.0, "default_empty")
    assert N.map_score("", "多选题") == (2.0, "default_empty")
    assert N.map_score("", "判断题") == (0.5, "default_empty")


# ───────────────────────── 报告确定性 ─────────────────────────

def test_report_byte_identical_across_runs(runs):
    for name in (f"import_report_{VERSION}.json", f"import_report_{VERSION}.md"):
        assert (runs["d1"] / name).read_bytes() == (runs["d2"] / name).read_bytes(), name
    assert runs["r1"]["report_hash"] == runs["r2"]["report_hash"]
    text = (runs["d1"] / f"import_report_{VERSION}.json").read_text(encoding="utf-8")
    assert not re.search(r"20\d\d-\d\d-\d\dT\d\d:\d\d", text), "报告不得含时间戳"


# ───────────────────────── 文件 / sheet / 行 ─────────────────────────

def test_inventory(report):
    inv = report["inventory"]
    assert inv["total_files"] == 251
    assert inv["by_real_format"] == {"ole2": 102, "ooxml": 147, "pdf": 1, "rar": 1}
    assert inv["xls_by_real_format"] == {"ole2": 102, "ooxml": 147}
    assert inv["xls_parsed"] == 249 and inv["xls_skipped_by_extension"] == 0
    assert inv["unique_md5_xls"] == 103
    assert {s["real_format"] for s in inv["skipped_files"]} == {"pdf", "rar"}
    assert all(p["rows"] == 0 for p in report["per_file"] if p["real_format"] in ("pdf", "rar"))


def test_sheets_and_rows(report):
    sh, rows = report["sheets"], report["rows"]
    assert sh["total"] == 256 and sh["parsed"] == 251 and sh["header_row_1"] == 251
    assert len(sh["skipped_aux"]) == 5
    assert sum(sh["header_variants"].values()) == 251 and len(sh["header_variants"]) > 1
    assert rows["data_rows"] == 30989 and report["per_file_rows_sum"] == 30989
    assert rows["empty_rows"] == 995 and rows["invalid_rows"] == 0
    assert rows["data_rows_by_real_format"] == {"ole2": 8798, "ooxml": 22191}


def test_aux_sheets_contribute_no_records(prep):
    aux = {(f.rel_path, s.sheet) for f in prep.scan.files for s in f.sheets if s.status != "parsed"}
    assert len(aux) == 5
    assert not [r for r in prep.scan.records if (r.rel_path, r.sheet) in aux]


def test_wide_sheets_only_first_columns(prep):
    """宽表（255/257 列）只读取表头扫描范围内的列。"""
    assert all(set(r.raw) == set(N.FIELDS) for r in prep.scan.records)


# ───────────────────────── 去重与身份 ─────────────────────────

def test_dedup_counts_and_reconciliation(report):
    d = report["dedup"]
    assert d["raw_key_variants"] == 11456                         # 方案口径（题干+选项原文+答案）
    assert 30989 - 19533 == d["raw_key_variants"]
    assert d["unique_stable_ids"] == 11450                        # 身份口径（不含答案）
    assert d["stem_option_answer_variants"] == 11455
    assert d["distinct_canonical_stems"] == 11381
    rec = d["reconciliation"]
    assert rec["minus_answer_only_variants(同题同选项仅答案不同，不拆身份)"] == 5
    assert rec["minus_option_text_merged_by_parsing(选项原文不同但解析后相同)"] == 1
    assert 11456 - 5 - 1 == rec["equals_unique_stable_ids"] == 11450
    assert len(d["answer_conflicts"]) == 5


def test_stable_ids_valid_and_stable(prep, runs):
    ids = list(prep.questions)
    assert len(ids) == len(set(ids)) == 11450
    assert all(i and re.fullmatch(r"[0-9a-f]{40}", i) for i in ids)
    golden = json.loads(F.GOLDEN_PATH.read_text(encoding="utf-8"))
    assert F.ids_digest(ids) == golden["all_stable_ids_sha1"]
    assert runs["r1"]["dedup"]["stable_ids_sha1"] == runs["r2"]["dedup"]["stable_ids_sha1"]


def test_golden_fixture_matches(prep):
    golden = json.loads(F.GOLDEN_PATH.read_text(encoding="utf-8"))
    assert F.build_golden(prep.scan, prep.questions) == golden
    assert (golden["54-1"]["rows"], golden["54-1"]["unique"]) == (332, 181)
    assert (golden["标黄必"]["rows"], golden["标黄必"]["unique"]) == (119, 108)
    shared = golden["shared_stems"]
    # 同题干不同选项保留为不同题：68 个题干对应 137 个 stable_id（11450 − 11381 = 69 个额外身份）
    assert (shared["stems"], shared["stable_ids"]) == (68, 137)
    assert shared["stable_ids"] - shared["stems"] == 11450 - 11381
    for g in shared["groups"]:
        assert len(set(g["stable_ids"])) == len(g["stable_ids"]) > 1
    merges = golden["parsing_merges"]
    assert merges["stable_ids"] == 1
    files = {s[0] for s in merges["groups"][0]["samples"]}
    assert files == {"33-1-电力监控安规-通用部分.xls", "33-3-电力监控安规-调度自动化厂站专业.xls"}


def test_answer_variants_share_stable_id(prep):
    conflicts = [q for q in prep.questions.values() if q.variants]
    assert len(conflicts) == 5
    for q in conflicts:
        assert len({r.answer for r in q.records}) == 2
        assert len({r.stable_id for r in q.records}) == 1


# ───────────────────────── 去重后分项 ─────────────────────────

def test_dedup_breakdown_not_raw(report):
    b, raw = report["dedup_breakdown"], report["raw_breakdown"]
    assert raw["by_qtype"] == {"判断题": 10112, "单选题": 12331, "多选题": 7894, "简答题": 652}
    assert b["by_qtype"] == {"判断题": 3812, "单选题": 4591, "多选题": 2856, "简答题": 191}
    assert sum(b["by_qtype"].values()) == 11450
    assert b["by_question_class"] == {"专业题库": 10938, "通用题库": 512}
    assert b["by_person_scope_incl_optional"] == {"一线人员": 4246, "专业管理人员": 4600, "重点人员": 4267, "领导人员": 782}
    assert b["by_person_scope_direct"] == {"一线人员": 745, "专业管理人员": 1124, "重点人员": 766, "领导人员": 782}
    for person, by_q in b["by_qtype_x_person_incl_optional"].items():
        assert sum(by_q.values()) == b["by_person_scope_incl_optional"][person]
    assert b["by_specialty"]["pd_general 配电安规通用"] == 363
    assert b["by_specialty"]["pd_live 配电带电"] == 155
    assert len(b["by_specialty"]) == 22
    assert b["outline_l1_l2_combinations"] == 107


def test_person_dirs_reproduce_plan_numbers_with_answer_key(prep):
    """方案 2.3 的人员目录去重数（4249/4270/4603/782）按含答案口径计算；本导入身份口径各少 3。"""
    from etl.report import _person_dir
    keys = {}
    for r in prep.scan.records:
        p = _person_dir(r.rel_path)
        if p:
            keys.setdefault(p, set()).add((r.canon_stem, N.squash(N.clean(r.raw["options"])), r.answer))
    assert {p: len(v) for p, v in keys.items()} == {"一线人员": 4249, "重点人员": 4270, "专业管理人员": 4603, "领导人员": 782}


# ───────────────────────── 标记 ─────────────────────────

def test_protected_marks(report):
    m = report["markers"]
    assert (m["protected_rows"], m["protected_files"]) == (1578, 130)


def test_required_exactly_19_pd_general(prep, report):
    req = [q for q in prep.questions.values() if q.is_required]
    assert len(req) == report["markers"]["required_unique"] == 19
    for q in req:
        assert q.in_summary
        assert any(r.is_yellow and r.fill == "FFFFFF00" and r.is_protected for r in q.records)
        assert {(r.fields["outline_l1"], r.fields["outline_l2"]) for r in q.records if r.is_yellow} == {("配电安规", "通用部分")}


def test_color_groups(report, prep):
    m = report["markers"]
    assert m["color_group_unique"] == {"配电一次运检": 17, "配电二次运检": 10, "配电带电": 9,
                                       "配电施工": 15, "配电电缆": 11, "配网调控": 9}
    assert sum(m["color_group_unique"].values()) == 71
    assert m["summary_file"]["colored_rows_non_yellow"] == 71
    assert not [q for q in prep.questions.values() if q.color_groups and q.is_required]


def test_summary_uncolored_29_not_required(report):
    s = report["markers"]["summary_file"]
    assert (s["rows"], s["unique"], s["yellow_rows"]) == (119, 108, 19)
    assert s["uncolored_protected_rows"] == s["uncolored_protected_unique"] == 29
    assert s["uncolored_by_outline_l1"] == {"信息安规": 15, "通信安规": 14}
    assert s["uncolored_required"] == 0


def test_legacy_yellow_11_never_required(report, prep):
    lg = report["markers"]["legacy_yellow"]
    assert (lg["rows"], lg["files"], lg["unique"]) == (11, 6, 11)
    assert lg["remark_values"] == {"": 11}
    assert lg["in_required"] == 0
    ids = {r.stable_id for r in prep.scan.records if r.is_yellow and not r.is_protected}
    assert not [i for i in ids if prep.questions[i].is_required]


# ───────────────────────── 格式检查 ─────────────────────────

def test_format_checks(report):
    fc = report["format_checks"]
    assert fc["dollar_semicolon_hits"] == 0
    assert fc["pipe_option_rows"] == 30290
    assert fc["code_id_empty_rows"] == fc["code_id_total_rows"] == 30989
    assert fc["objective_answer_not_in_options"] == 0
    assert "unparsable_option" not in fc["option_issues"]
    assert fc["seq_formula_rows"] > 0


def test_score_mapping_on_data(report, prep):
    assert report["raw_breakdown"]["score_raw"] == {"": 16, "/": 3, "0.5": 10103, "1": 12332, "2": 7886, "5": 9, "\\": 640}
    assert {r.score_rule for r in prep.scan.records} <= {"native", "default_empty", "placeholder"}
    assert all(r.qtype == QTYPE_SHORT for r in prep.scan.records if r.score_rule == "placeholder")
    assert all(r.score is not None for r in prep.scan.records if r.qtype != QTYPE_SHORT)


def test_short_answers_out_of_auto_pool(prep, report):
    shorts = [r for r in prep.scan.records if r.qtype == QTYPE_SHORT]
    assert len(shorts) == 652
    ids = {r.stable_id for r in shorts}
    assert len(ids) == 191 == report["dedup_breakdown"]["excluded_from_auto_pool"]
    assert not [i for i in ids if prep.questions[i].auto_gradable]
    for pool in report["templates"]["pool_preview_auto_gradable"].values():
        assert QTYPE_SHORT not in pool and set(pool) <= set(AUTO_GRADABLE_QTYPES)


# ───────────────────────── fixtures：专业与模板 ─────────────────────────

def test_specialties_fixture():
    specs = F.load_specialties()
    assert len(specs) == 22 and len({s["code"] for s in specs}) == 22
    enabled = {s["code"]: s["source_file_pattern"] for s in specs if s["enabled"]}
    assert enabled == {"pd_general": "30-1-配电安规-通用部分.xls", "pd_live": "30-4-配电安规-配电带电专业.xls"}
    names = {p.name for p in SOURCE.rglob("*.xls")}
    assert all(s["source_file_pattern"] in names for s in specs)
    assert "待 dong 确认" in F.SPECIALTIES_PATH.read_text(encoding="utf-8")


def test_paper_templates_fixture(prep):
    text = F.TEMPLATES_PATH.read_text(encoding="utf-8")
    assert "待 dong 确认" in text and "占位" in text
    data = yaml.safe_load(text)
    sets = F.compute_id_sets(prep.questions)
    F.validate_templates(data, sets)
    required, exclude = set(sets["required_19"]), set(sets["exclude_b_net_info_security"])
    assert len(required) == 19 and not required & exclude
    summary_uncolored = {r.stable_id for r in prep.scan.records
                         if "标黄必" in r.rel_path and r.fill is None and r.is_protected}
    assert len(summary_uncolored) == 29 and summary_uncolored <= exclude
    by_outline = {q.stable_id for q in prep.questions.values() if q.outline_l1s & {"通信安规", "信息安规"}}
    assert exclude == by_outline | summary_uncolored
    tpls = F.expand_templates(data, VERSION)
    assert {t["person_category"] for t in tpls} == {"重点人员", "一线人员"}
    for t in tpls:
        assert set(t["required_question_ids"]) == required
        assert set(t["exclude_question_ids"]) == exclude
        assert (t["question_count"], t["total_score"], t["pass_score"], t["duration_min"]) == (100, 100, 60, 60)
        assert t["type_ratio"] == {"单选题": 0.4, "多选题": 0.3, "判断题": 0.3}
        assert t["short_answer_policy"] == "excluded" and t["status"] == "draft"
        assert t["specialty_rule"]["default_allowed"] == ["pd_general", "pd_live"]


# ───────────────────────── 入库 ─────────────────────────

def test_db_schema(db):
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert ALL_TABLES <= tables
    assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert db.execute("SELECT version FROM schema_migrations").fetchall()[0][0] == "0001"


def test_db_loaded_counts(db):
    bank = _bank_id(db, VERSION)
    one = lambda sql, *a: db.execute(sql, a).fetchone()[0]  # noqa: E731
    assert one("SELECT status FROM bank_versions WHERE code = ?", VERSION) == "active"
    assert one("SELECT count(*) FROM question_versions WHERE bank_version_id = ? AND retired = 0", bank) == 11450
    assert one("SELECT count(*) FROM question_versions WHERE bank_version_id = ? AND retire_reason = 'answer_variant'", bank) == 5
    assert one("SELECT count(*) FROM question_versions WHERE bank_version_id = ?", bank) == 11455
    assert one("SELECT count(*) FROM question_versions WHERE stable_id IS NULL OR stable_id = ''") == 0
    assert one("SELECT count(*) FROM question_versions WHERE bank_version_id = ? AND retired = 0 AND is_required = 1", bank) == 19
    assert one("SELECT count(*) FROM question_versions WHERE bank_version_id = ? AND retired = 0 AND qtype = '简答题' "
               "AND auto_gradable = 0", bank) == 191
    assert one("SELECT count(*) FROM question_versions WHERE qtype = '简答题' AND auto_gradable = 1") == 0
    assert one("SELECT count(*) FROM question_tags WHERE bank_version_id = ? AND tag_type = 'required'", bank) == 19
    assert one("SELECT count(*) FROM specialties WHERE enabled = 1") == 2
    assert one("SELECT count(*) FROM paper_templates") == 2
    assert one("SELECT sum(row_count) FROM source_files WHERE bank_version_id = ?", bank) == 30989


def test_db_legacy_bank(db, runs):
    legacy = _bank_id(db, "legacy")
    one = lambda sql, *a: db.execute(sql, a).fetchone()[0]  # noqa: E731
    assert one("SELECT status FROM bank_versions WHERE id = ?", legacy) == "archived"
    assert one("SELECT sum(row_count) FROM source_files WHERE bank_version_id = ?", legacy) == 1325
    assert runs["legacy"]["rows"]["data_rows"] == 1325
    active = one("SELECT count(*) FROM question_versions WHERE bank_version_id = ? AND retired = 0", legacy)
    assert active == runs["legacy"]["dedup"]["unique_stable_ids"]
    assert one("SELECT count(*) FROM bank_versions WHERE status = 'active'") == 1


def test_reload_idempotent_and_answer_correction(prep, tmp_path):
    """重跑不产生新版本；答案勘误 = 新增版本 + 旧行 retired，stable_id 不变。"""
    db = tmp_path / "exam.db"
    first = load_prepared(prep, db, "h1")
    assert first["versions_inserted"] == 11450 and first["questions_inserted"] == 11450
    again = load_prepared(prep, db, "h1")
    assert again["versions_unchanged"] == 11450 and again.get("versions_inserted", 0) == 0
    assert again["questions_inserted"] == 0

    sid = sorted(q.stable_id for q in prep.questions.values() if q.is_required)[0]
    questions = dict(prep.questions)
    q = copy.copy(questions[sid])
    new_answer = "B" if q.chosen.answer != "B" else "A"
    q.chosen = dataclasses.replace(q.chosen, answer=new_answer)
    questions[sid] = q
    stats = load(db, version=VERSION, name="t", source_label="t", scan=prep.scan, questions=questions,
                 report_hash="h2", initial_status="active", specialties=prep.specialties,
                 templates=prep.templates, specialty_by_file=prep.specialty_by_file)
    assert stats["versions_superseded"] == 1
    conn = connect(db)
    try:
        rows = conn.execute("SELECT answer, retired, retire_reason, supersedes_id, id FROM question_versions "
                            "WHERE stable_id = ? AND (retire_reason IS NULL OR retire_reason != 'answer_variant') "
                            "ORDER BY id", (sid,)).fetchall()
        assert [(r["retired"], r["retire_reason"]) for r in rows] == [(1, "superseded"), (0, None)]
        assert rows[1]["answer"] == new_answer and rows[1]["supersedes_id"] == rows[0]["id"]
        assert conn.execute("SELECT count(*) FROM questions WHERE stable_id = ?", (sid,)).fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM questions").fetchone()[0] == 11450
    finally:
        conn.close()


# ───────────────────────── 迁移 ─────────────────────────

def test_migration_upgrade_downgrade_upgrade(tmp_path):
    db = tmp_path / "m.db"
    assert upgrade(db) == ["0001"]
    conn = connect(db)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    conn.close()
    assert ALL_TABLES <= tables
    assert downgrade(db, "0000") == ["0001"]
    conn = connect(db)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    conn.close()
    assert not ALL_TABLES & tables
    assert list((tmp_path / "backups").glob("m.rollback-from-0001.*.db")), "回滚前当前库必须先归档"
    assert upgrade(db) == ["0001"]


def test_no_drop_or_to_sql_replace():
    files = list((REPO_ROOT / "migrations").glob("*.sql")) + list((REPO_ROOT / "etl").glob("*.py")) \
        + list((REPO_ROOT / "app" / "db").glob("*.py")) + [REPO_ROOT / "app" / "models.py"]
    for p in files:
        text = p.read_text(encoding="utf-8")
        assert not re.search(r"\bDROP\b", text, re.I), p
        assert "to_sql" not in text, p
