"""fixtures/ 读写：专业代码表、试卷模板（含可枚举 stable_id 集合）、去重 golden。"""
from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

import yaml

from app.db import REPO_ROOT
from app.models import PERSON_KEY, PERSON_FRONTLINE
from etl.normalize import clean, squash
from etl.pipeline import Question, Scan

FIXTURES_DIR = REPO_ROOT / "fixtures"
SPECIALTIES_PATH = FIXTURES_DIR / "specialties.yaml"
TEMPLATES_PATH = FIXTURES_DIR / "paper_templates.yaml"
GOLDEN_PATH = FIXTURES_DIR / "golden" / "dedup.json"

# 口径 B：「网络信息安全除外」= 剔除题目
EXCLUDE_B_OUTLINES = ("通信安规", "信息安规")


# ───────────────────────── 专业 ─────────────────────────

def load_specialties(path: Path = SPECIALTIES_PATH) -> list[dict]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data["specialties"]


def specialty_by_file(specialties: list[dict]) -> dict[str, str]:
    return {s["source_file_pattern"]: s["code"] for s in specialties}


# ───────────────────────── 可枚举 ID 集合 ─────────────────────────

def compute_id_sets(questions: dict[str, Question]) -> dict[str, list[str]]:
    qs = questions.values()
    required = sorted(q.stable_id for q in qs if q.is_required)
    by_outline = {q.stable_id for q in qs if q.outline_l1s & set(EXCLUDE_B_OUTLINES)}
    summary_uncolored = {
        r.stable_id for q in qs for r in q.records
        if "标黄必" in r.rel_path and r.fill is None and r.is_protected
    }
    exclude_b = sorted(by_outline | summary_uncolored)
    if set(required) & set(exclude_b):
        raise ValueError("必答题与排除集合存在交集")
    return {"required_19": required, "exclude_b_net_info_security": exclude_b}


def ids_digest(ids: list[str]) -> str:
    return hashlib.sha1("\n".join(sorted(ids)).encode("utf-8")).hexdigest()


# ───────────────────────── 试卷模板 ─────────────────────────

_TEMPLATE_HEADER = """\
# 第一版试卷模板 —— 状态：draft，全部数字为【占位，待 dong 确认】
#
# 由 `python -m etl gen-fixtures --source 2026-11-12 --version {version}` 生成 id_sets；
# 模板字段可手改，id_sets 由导入器在 --load 时按规则重算并逐个比对，不一致即失败（防止静默漂移）。
#
# 已拍板的第一版默认决策（均待 dong 确认）：
#   1. 必答题 = 19 道标黄题（标黄必汇总文件中纯黄 FFFFFF00 且备注=保命题，全部属 配电安规/通用部分）；
#      其余保命题只作标签（question_tags.protected），不必抽。
#   2. 专业开放 = 配电安规通用(pd_general) + 配电带电(pd_live)，其余 20 个专业 enabled=false。
#   3. 「网络信息安全除外」按口径 B（剔除题目）：一级纲要 ∈ {{通信安规, 信息安规}} 的全部题
#      ∪ 标黄必汇总文件末尾 29 行不着色保命题（后者是前者子集），落成 id_sets.exclude_b_net_info_security。
#   4. 简答题全部排除出自动判分卷（short_answer_policy: excluded；question_versions.auto_gradable=0）。
#   5. 领导人员 / 专业管理人员暂不建模板，数据保留（question_tags.person_scope）。
#
# 【数字冲突，需 dong 拍板】按题目原始分值（单选 1 / 多选 2 / 判断 0.5）抽 40/30/30 题合计 115 分，
#   与 total_score=100 不符。这里 type_score 暂按每题 1 分（100 题 × 1 = 100）占位；
#   备选：改题量或比例使原始分值凑满 100。
"""


class _NoAliasDumper(yaml.SafeDumper):
    """模板会被人工编辑，不输出 &id001 / *id001 别名，避免改一处牵动多处。"""

    def ignore_aliases(self, data):
        return True


def render_templates_yaml(version: str, id_sets: dict[str, list[str]]) -> str:
    common = {
        "version": 1,
        "paper_type": "formal",
        "bank_version": version,
        "status": "draft",
        "specialty_rule": {"required": ["pd_general"], "choose_one_of": ["pd_live"],
                           "default_allowed": ["pd_general", "pd_live"]},
        "required_question_ids": {"ref": "required_19"},
        "exclude_question_ids": {"ref": "exclude_b_net_info_security"},
        "exclude_rule": {"口径": "B", "outline_l1_in": list(EXCLUDE_B_OUTLINES),
                         "plus": "标黄必汇总文件末尾不着色保命题（29 行）"},
        "question_count": 100,
        "type_ratio": {"单选题": 0.4, "多选题": 0.3, "判断题": 0.3},
        "type_score": {"单选题": 1.0, "多选题": 1.0, "判断题": 1.0},
        "total_score": 100,
        "pass_score": 60,
        "duration_min": 60,
        "random_policy": "per_session_seed",
        "short_answer_policy": "excluded",
        "pending_confirmation": [f"待 dong 确认：{item}" for item in (
            "question_count/type_ratio/type_score/total_score/pass_score/duration_min 全部为占位数字",
            "原始分值下 40/30/30 合计 115 分 ≠ 100，type_score 暂按每题 1 分",
            "必答题 = 19 道标黄题，其余保命题（汇总 108 题 / 全库 596 题）只作标签",
            "「网络信息安全除外」按口径 B 剔除题目（exclude_b_net_info_security）",
            "专业仅开放 pd_general + pd_live，其余 20 个 enabled=false",
            "简答题排除出自动判分卷；领导人员/专业管理人员暂不建模板",
        )],
    }
    templates = []
    for person in (PERSON_KEY, PERSON_FRONTLINE):
        templates.append({
            "code": f"{version}-配电-{person}",
            "name": f"{version} 配电安规（{person}）",
            "person_category": person,
            **common,
        })
    body = {"id_sets": {k: v for k, v in sorted(id_sets.items())},
            "id_set_digests": {k: ids_digest(v) for k, v in sorted(id_sets.items())},
            "templates": templates}
    text = yaml.dump(body, Dumper=_NoAliasDumper, allow_unicode=True, sort_keys=False, width=200,
                     default_flow_style=False)
    return _TEMPLATE_HEADER.format(version=version) + "\n" + text


def load_templates(path: Path = TEMPLATES_PATH) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def expand_templates(data: dict, version: str) -> list[dict]:
    """把 {ref: name} 展开成 id 列表，只返回属于该题库版本的模板。"""
    sets = data.get("id_sets", {})
    out = []
    for t in data.get("templates", []):
        if t.get("bank_version") != version:
            continue
        t = dict(t)
        for key in ("required_question_ids", "exclude_question_ids"):
            v = t[key]
            t[key] = sorted(sets[v["ref"]]) if isinstance(v, dict) else sorted(v)
        out.append(t)
    return out


def validate_templates(data: dict, id_sets: dict[str, list[str]]) -> None:
    """fixture 中的枚举集合必须与按规则重算的结果逐个相等。"""
    stored = data.get("id_sets", {})
    for name, ids in id_sets.items():
        if sorted(stored.get(name, [])) != ids:
            raise ValueError(f"paper_templates.yaml 的 id_sets.{name} 与当前题库按规则重算结果不一致，"
                             f"请复核后运行 `python -m etl gen-fixtures` 重新生成")


def pool_preview(questions: dict[str, Question], templates: list[dict]) -> dict:
    """每个模板 × 可选专业的自动抽题池规模（按题型），供 P3 组卷可行性参考。"""
    out = {}
    for t in templates:
        excl = set(t["exclude_question_ids"])
        rule = t["specialty_rule"]
        for choice in rule["choose_one_of"]:
            specs = set(rule["required"]) | {choice}
            pool = [q for q in questions.values()
                    if q.auto_gradable and q.stable_id not in excl
                    and (t["person_category"] in q.person_scopes or q.specialties & specs)]
            out[f"{t['code']}+{choice}"] = dict(sorted(Counter(q.qtype for q in pool).items()))
    return out


# ───────────────────────── golden ─────────────────────────

def build_golden(scan: Scan, questions: dict[str, Question]) -> dict:
    def file_set(mark: str) -> dict:
        recs = [r for r in scan.records if mark in r.rel_path]
        ids = sorted({r.stable_id for r in recs})
        return {"match": mark, "files": sorted({r.rel_path for r in recs}), "rows": len(recs),
                "unique": len(ids), "stable_ids": ids}

    stems = defaultdict(list)
    for q in questions.values():
        stems[q.canon_stem].append(q.stable_id)
    shared = [{"stem_sha1": hashlib.sha1(s.encode("utf-8")).hexdigest(), "stem_head": s[:30], "stable_ids": sorted(v)}
              for s, v in sorted(stems.items()) if len(v) > 1]
    all_ids = sorted(questions)
    parsing_merges = []
    for q in questions.values():
        raw_opts = sorted({squash(clean(r.raw["options"])) for r in q.records})
        if len(raw_opts) > 1:
            locs = sorted({(r.rel_path.split("/")[-1], r.row_no, squash(clean(r.raw["options"]))) for r in q.records})
            parsing_merges.append({"stable_id": q.stable_id, "canonical_options": q.canon_options,
                                   "raw_options": raw_opts, "samples": [list(x) for x in locs[:6]]})
    return {
        "rule": "stable_id = sha1(squash(题干) + '\\x1f' + 'A-squash(选项A)|B-...'), squash = 去全部空白；"
                "选项按 `| ｜ 丨` 切分、标签 `A- A. A． A、`、只认顺序字母；简答选项恒空；不含答案/分值/解析",
        "unique_stable_ids": len(all_ids),
        "all_stable_ids_sha1": ids_digest(all_ids),
        "distinct_canonical_stems": len(stems),
        "54-1": file_set("54-1-风电安规-风电运维.xls"),
        "标黄必": file_set("标黄必"),
        "shared_stems": {"stems": len(shared), "stable_ids": sum(len(x["stable_ids"]) for x in shared), "groups": shared},
        "parsing_merges": {"stable_ids": len(parsing_merges), "groups": parsing_merges},
        "answer_conflicts": [
            {"stable_id": q.stable_id, "chosen_answer": q.chosen.answer,
             "answers": dict(sorted(Counter(r.answer for r in q.records).items()))}
            for q in questions.values() if q.variants
        ],
        "required_19": sorted(q.stable_id for q in questions.values() if q.is_required),
    }


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def dump_json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=1) + "\n"
