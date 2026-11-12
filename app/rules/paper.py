"""组卷引擎：19 必答 + 通用部分/1 自选专业 + 口径 B 排除 + 按题型比例凑分 + 锁卷快照。

硬约束：
  * 必答题集合固定为 fixtures 的 19 个 stable_id，0 缺 0 多；
  * 题目池 = person_scope 含该人员类别 ∩ specialty ∈ {通用部分, 1 个自选专业}；
    池子不足时按 person_scope ∪ specialty 兜底（记录 pool_rule），仍然断言专业范围；
  * 简答题（去重 191 道）与 auto_gradable=0 的题不进自动判分卷；
  * 排除集合为可枚举 stable_id（口径 B：一级纲要 ∈ {通信安规, 信息安规} 及末尾不着色保命题）；
  * 抽题用 seed 派生的伪随机数，池子按 stable_id 排序，故同 seed 重放逐字节一致；
  * paper_questions.snapshot_json 冻结题干/选项/答案/分值/顺序 + bank_version/template_version/seed，
    判分只读快照，不回查题库。
"""
from __future__ import annotations

import hashlib
import json
import random
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from app.models import QTYPE_SHORT
from app.rules.scoring import score_for
from app.rules.templates import expand_templates
from app.services.util import json_dumps, sha256_text

PAPER_QUESTION_KEYS = ("seq", "stable_id", "qtype", "stem", "options", "score", "is_required")


@dataclass
class PaperDraft:
    template_id: int
    template_code: str
    template_version: int
    bank_version_id: int
    seed: str
    person_category: str
    specialty_code: str
    items: list[dict[str, Any]] = field(default_factory=list)
    snapshot_hash: str = ""
    pool_rule: str = "strict"
    required_ids: list[str] = field(default_factory=list)
    question_count: int = 0
    total_score: float = 0.0


def _loads(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def template_for(conn: sqlite3.Connection, template_id: int) -> dict[str, Any]:
    """以 DB 行承载状态，以 fixtures/*.yaml 承载规则（代码读配置，不硬编码）。"""
    row = conn.execute("SELECT * FROM paper_templates WHERE id = ?", (template_id,)).fetchone()
    if row is None:
        raise KeyError(template_id)
    t = dict(row)
    bank = conn.execute("SELECT code FROM bank_versions WHERE id = ?", (t["bank_version_id"],)).fetchone()
    version = bank["code"] if bank else None
    cfg = next(
        (c for c in expand_templates(version) if c["code"] == t["code"] and c["version"] == t["version"]),
        None,
    )
    if cfg is None:
        raise ValueError(f"fixtures/paper_templates.yaml 缺少模板 {t['code']} v{t['version']}，拒绝用陈旧规则组卷")
    for key in (
        "specialty_rule", "required_question_ids", "exclude_question_ids", "exclude_rule",
        "type_ratio", "type_score", "question_count", "total_score", "pass_score",
        "duration_min", "random_policy", "short_answer_policy", "person_category", "paper_type",
    ):
        if key in cfg:
            t[key] = cfg[key]
    for key in (
        "specialty_rule", "required_question_ids", "exclude_question_ids", "exclude_rule",
        "type_ratio", "type_score",
    ):
        t[key] = _loads(t[key], {} if key != "required_question_ids" and key != "exclude_question_ids" else [])
    return t


def allowed_specialties(template: dict[str, Any]) -> list[str]:
    rule = template.get("specialty_rule") or {}
    return list(dict.fromkeys([*rule.get("required", []), *rule.get("choose_one_of", []),
                               *rule.get("default_allowed", [])]))


def _pool(conn: sqlite3.Connection, bank_version_id: int, person_category: str,
          specialty_codes: list[str], strict: bool) -> list[dict[str, Any]]:
    if not specialty_codes:
        return []
    marks = ",".join("?" for _ in specialty_codes)
    sql = f"""
        SELECT qv.id AS question_version_id, qv.stable_id, qv.qtype, qv.stem, qv.options,
               qv.answer, qv.answer_raw, qv.basis, qv.analysis, qv.outline_l1, qv.outline_l2,
               qv.is_required, qv.score AS native_score,
               MAX(CASE WHEN qt.tag_type = 'person_scope' AND qt.tag_value = ? THEN 1 ELSE 0 END) AS has_person,
               MAX(CASE WHEN qt.tag_type = 'specialty' AND qt.tag_value IN ({marks}) THEN 1 ELSE 0 END) AS has_specialty,
               GROUP_CONCAT(DISTINCT CASE WHEN qt.tag_type = 'specialty' THEN qt.tag_value END) AS specialty_tags
        FROM question_versions qv
        LEFT JOIN question_tags qt
               ON qt.bank_version_id = qv.bank_version_id AND qt.stable_id = qv.stable_id
        WHERE qv.bank_version_id = ? AND qv.retired = 0 AND qv.auto_gradable = 1
          AND qv.qtype != ?
        GROUP BY qv.id
    """
    params: list[Any] = [person_category, *specialty_codes, bank_version_id, QTYPE_SHORT]
    rows = conn.execute(sql, params).fetchall()
    rule = (lambda r: r["has_person"] and r["has_specialty"]) if strict else (
        lambda r: r["has_person"] or r["has_specialty"])
    return [dict(r) for r in rows if rule(r)]


def _targets(template: dict[str, Any]) -> dict[str, int]:
    count = int(template["question_count"])
    ratio = template.get("type_ratio") or {}
    targets = {qtype: int(round(float(weight) * count)) for qtype, weight in ratio.items()}
    # 四舍五入可能差 1 题，补到最后一个题型上，保证总分恒等于模板值
    delta = count - sum(targets.values())
    if delta and targets:
        last = sorted(targets)[-1]
        targets[last] += delta
    return targets


def _seed_rng(seed: str) -> random.Random:
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return random.Random(int(digest[:16], 16))


def load_pool(conn: sqlite3.Connection, template: dict[str, Any], person_category: str,
              specialty_code: str, strict: bool = True) -> list[dict[str, Any]]:
    """导出题目池，便于批量 seed 测试一次加载、多次组卷（生产路径每次都重查题库）。"""
    rule = template.get("specialty_rule") or {}
    specs = [s for s in dict.fromkeys([*rule.get("required", []), specialty_code]) if s]
    excluded = set(template["exclude_question_ids"])
    return [q for q in _pool(conn, int(template["bank_version_id"]), person_category, specs, strict)
            if q["stable_id"] not in excluded]


def compose_paper(conn: sqlite3.Connection, template: dict[str, Any], seed: str,
                  person_category: str, specialty_code: str,
                  *, pool_rows: list[dict[str, Any]] | None = None) -> PaperDraft:
    draft = PaperDraft(
        template_id=int(template["id"]), template_code=template["code"],
        template_version=int(template["version"]), bank_version_id=int(template["bank_version_id"]),
        seed=seed, person_category=person_category, specialty_code=specialty_code,
        required_ids=sorted(template["required_question_ids"]),
    )
    draft.items = []
    excluded = set(template["exclude_question_ids"])
    if pool_rows is None:
        pool = load_pool(conn, template, person_category, specialty_code, strict=True)
        draft.pool_rule = "strict"
        if not pool and not template.get("strict_person_scope"):
            pool = load_pool(conn, template, person_category, specialty_code, strict=False)
            draft.pool_rule = "union-fallback"
    else:
        pool = [q for q in pool_rows if q["stable_id"] not in excluded]
        draft.pool_rule = "preloaded"

    by_id = {q["stable_id"]: q for q in pool}
    missing = [sid for sid in draft.required_ids if sid not in by_id]
    if missing:
        raise ValueError(f"19 道必答题有 {len(missing)} 道不在题目池/被排除集合误伤：{missing[:3]}")

    targets = _targets(template)
    required_rows = [by_id[sid] for sid in draft.required_ids]
    for row in required_rows:
        targets[row["qtype"]] = targets.get(row["qtype"], 0) - 1
    if any(v < 0 for v in targets.values()):
        raise ValueError("必答题的题型分布超出模板 type_ratio，无法凑分")

    rng = _seed_rng(seed)
    chosen = list(required_rows)
    for qtype in sorted(targets):
        need = targets[qtype]
        candidates = sorted(
            (q for q in pool if q["qtype"] == qtype and q["stable_id"] not in draft.required_ids),
            key=lambda q: q["stable_id"],
        )
        rng.shuffle(candidates)
        if len(candidates) < need:
            raise ValueError(f"题型 {qtype} 题目池不足：需要 {need}，可用 {len(candidates)}")
        chosen.extend(candidates[:need])

    if len(chosen) != int(template["question_count"]):
        raise ValueError(f"组卷题量 {len(chosen)} != 模板 {template['question_count']}")
    if len({q["stable_id"] for q in chosen}) != len(chosen):
        raise ValueError("卷内出现重复 stable_id")
    if any(q["stable_id"] in excluded for q in chosen):
        raise ValueError("排除集合（口径 B）命中卷内题目")
    if any(q["qtype"] == QTYPE_SHORT for q in chosen):
        raise ValueError("简答题不得进入自动判分卷")

    rng.shuffle(chosen)
    type_score = template.get("type_score") or {}
    items: list[dict[str, Any]] = []
    for seq, q in enumerate(chosen, start=1):
        score = score_for(q["qtype"], type_score)
        items.append({
            "seq": seq,
            "stable_id": q["stable_id"],
            "question_version_id": q["question_version_id"],
            "qtype": q["qtype"],
            "stem": q["stem"],
            "options": _loads(q["options"], {}),
            "answer": q["answer"],
            "answer_raw": q["answer_raw"],
            "basis": q["basis"],
            "analysis": q["analysis"],
            "outline_l1": q["outline_l1"],
            "outline_l2": q["outline_l2"],
            "specialty_tags": q["specialty_tags"] or "",
            "score": score,
            "native_score": q["native_score"],
            "is_required": int(q["stable_id"] in set(draft.required_ids)),
            "bank_version_id": draft.bank_version_id,
            "template_version": draft.template_version,
            "seed": seed,
        })
    draft.items = items
    draft.question_count = len(items)
    draft.total_score = round(sum(float(i["score"]) for i in items), 6)
    payload = {
        "bank_version_id": draft.bank_version_id,
        "template_code": draft.template_code,
        "template_version": draft.template_version,
        "person_category": person_category,
        "specialty_code": specialty_code,
        "seed": seed,
        "items": items,
    }
    draft.snapshot_hash = sha256_text(json_dumps(payload))
    return draft


def find_paper(conn: sqlite3.Connection, template: dict[str, Any], seed: str,
               person_category: str, specialty_code: str) -> sqlite3.Row | None:
    """同 seed 重放：直接命中已锁卷的 papers 行，题库被 UPDATE 也不重算卷面。"""
    return conn.execute(
        "SELECT * FROM papers WHERE template_id = ? AND template_version = ? "
        "AND bank_version_id = ? AND seed = ? AND person_category IS ? AND specialty_code IS ?",
        (template["id"], template["version"], template["bank_version_id"], seed,
         person_category, specialty_code),
    ).fetchone()


def replay_paper(conn: sqlite3.Connection, template: dict[str, Any], seed: str,
                 person_category: str, specialty_code: str, *, at: str) -> tuple[int, str, PaperDraft | None]:
    """返回 (paper_id, snapshot_hash, 本次新建的 draft)。已锁卷则 draft=None。"""
    found = find_paper(conn, template, seed, person_category, specialty_code)
    if found is not None:
        return int(found["id"]), found["snapshot_hash"], None
    draft = compose_paper(conn, template, seed, person_category, specialty_code)
    paper_id, _ = store_paper(conn, draft, at=at)
    return paper_id, draft.snapshot_hash, draft


def store_paper(conn: sqlite3.Connection, draft: PaperDraft, *, at: str) -> tuple[int, bool]:
    """同 (模板, 版本, 题库, seed, 人员类别, 专业) 重放直接复用已锁卷的 papers 行。"""
    row = conn.execute(
        "SELECT id, snapshot_hash FROM papers WHERE template_id = ? AND template_version = ? "
        "AND bank_version_id = ? AND seed = ? AND person_category IS ? AND specialty_code IS ?",
        (draft.template_id, draft.template_version, draft.bank_version_id, draft.seed,
         draft.person_category, draft.specialty_code),
    ).fetchone()
    if row is not None:
        # 锁卷优先：即使题库答案在此期间被 UPDATE，也返回历史快照，不覆盖、不重算。
        return int(row["id"]), True
    cur = conn.execute(
        "INSERT INTO papers (template_id, template_version, bank_version_id, seed, person_category, "
        "specialty_code, question_count, total_score, snapshot_hash, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (draft.template_id, draft.template_version, draft.bank_version_id, draft.seed,
         draft.person_category, draft.specialty_code, draft.question_count, draft.total_score,
         draft.snapshot_hash, at),
    )
    paper_id = int(cur.lastrowid)
    conn.executemany(
        "INSERT INTO paper_questions (paper_id, seq, stable_id, question_version_id, score, "
        "is_required, snapshot_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (paper_id, i["seq"], i["stable_id"], i["question_version_id"], i["score"],
             i["is_required"], json_dumps(i))
            for i in draft.items
        ],
    )
    return paper_id, False


def read_snapshot(conn: sqlite3.Connection, paper_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT snapshot_json FROM paper_questions WHERE paper_id = ? ORDER BY seq", (paper_id,)
    ).fetchall()
    return [_loads(r["snapshot_json"], {}) for r in rows]


def question_dto(snapshot: dict[str, Any]) -> dict[str, Any]:
    """考试下发 DTO：只有这 7 个字段，绝不含任何判分依据。"""
    return {key: snapshot.get(key) for key in PAPER_QUESTION_KEYS}
