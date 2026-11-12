"""dry-run / load 报告：确定性 JSON + Markdown（不含时间戳，两次运行逐字节一致）。"""
from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath

from app.models import PERSON_CATEGORIES, QTYPES
from etl.normalize import clean, squash
from etl.pipeline import SUMMARY_FILE_MARK, Question, Scan, field_conflicts

REPORT_SCHEMA = 1
GOLDEN_FILES = {"54-1": "54-1-风电安规-风电运维.xls", "标黄必": SUMMARY_FILE_MARK}


def _sorted_counter(c: Counter) -> dict:
    return {str(k): c[k] for k in sorted(c, key=lambda x: (str(x)))}


def _person_dir(rel: str) -> str | None:
    top = PurePosixPath(rel).parts[0]
    return top if top in PERSON_CATEGORIES else None


def build_report(version: str, source_label: str, scan: Scan, questions: dict[str, Question],
                 specialties: list[dict] | None = None, extra: dict | None = None) -> dict:
    recs = scan.records
    files = scan.files
    specialties = specialties or []
    qs = list(questions.values())

    # ── 清单 ──
    xls = [f for f in files if f.rel_path.lower().endswith(".xls")]
    inventory = {
        "total_files": len(files),
        "total_bytes": sum(f.size for f in files),
        "by_real_format": _sorted_counter(Counter(f.real_format for f in files)),
        "xls_by_real_format": _sorted_counter(Counter(f.real_format for f in xls)),
        "xls_parsed": sum(1 for f in xls if f.status == "parsed"),
        "xls_skipped_by_extension": sum(1 for f in xls if f.status != "parsed"),
        "unique_md5_xls": len({f.md5 for f in xls}),
        "skipped_files": [{"rel_path": f.rel_path, "real_format": f.real_format, "reason": f.skip_reason}
                          for f in files if f.status != "parsed"],
    }

    # ── sheet 与行 ──
    sheets = [(f, s) for f in files for s in f.sheets]
    parsed_sheets = [(f, s) for f, s in sheets if s.status == "parsed"]
    sheet_stats = {
        "total": len(sheets),
        "parsed": len(parsed_sheets),
        "header_row_1": sum(1 for _, s in parsed_sheets if s.header_row == 1),
        "skipped_aux": [{"rel_path": f.rel_path, "sheet": s.sheet} for f, s in sheets if s.status != "parsed"],
        "header_variants": _sorted_counter(Counter(s.header_variant for _, s in parsed_sheets)),
    }
    rows = {
        "data_rows": len(recs),
        "empty_rows": sum(s.empty_rows for _, s in sheets),
        "invalid_rows": len(scan.invalid),
        "invalid_samples": scan.invalid[:20],
        "data_rows_by_real_format": _sorted_counter(Counter(
            f.real_format for f in files for s in f.sheets for _ in range(s.data_rows))),
    }

    # ── 去重 ──
    stems = defaultdict(set)
    for q in qs:
        stems[q.canon_stem].add(q.stable_id)
    multi_stems = {k: v for k, v in stems.items() if len(v) > 1}
    variants_total = len(qs) + sum(len(q.variants) for q in qs)
    # 侦察口径：规范化题干 + 选项原文（仅去空白，不解析）+ 答案 —— 用于对账方案中的 11,456
    raw_keys = {(r.canon_stem, squash(clean(r.raw["options"])), r.answer) for r in recs}
    merged_by_parsing = len({(r.canon_stem, squash(clean(r.raw["options"]))) for r in recs}) - len(qs)
    dedup = {
        "unique_stable_ids": len(qs),
        "stable_ids_sha1": hashlib.sha1("\n".join(sorted(questions)).encode("utf-8")).hexdigest(),
        "raw_key_variants": len(raw_keys),
        "reconciliation": {
            "raw_key_variants(题干+选项原文+答案)": len(raw_keys),
            "minus_answer_only_variants(同题同选项仅答案不同，不拆身份)": len(raw_keys) - len(
                {(k[0], k[1]) for k in raw_keys}),
            "minus_option_text_merged_by_parsing(选项原文不同但解析后相同)": merged_by_parsing,
            "equals_unique_stable_ids": len(qs),
        },
        "stem_option_answer_variants": variants_total,
        "answer_variant_rows_extra": variants_total - len(qs),
        "distinct_canonical_stems": len(stems),
        "duplicate_rows_vs_stable_ids": len(recs) - len(qs),
        "duplicate_rows_vs_answer_variants": len(recs) - variants_total,
        "stems_with_multiple_stable_ids": len(multi_stems),
        "stable_ids_under_shared_stems": sum(len(v) for v in multi_stems.values()),
        "answer_conflicts": [
            {
                "stable_id": q.stable_id,
                "qtype": q.qtype,
                "stem": q.chosen.fields["stem"][:60],
                "chosen_answer": q.chosen.answer[:80],
                "answers": {a[:80]: n for a, n in sorted(Counter(r.answer for r in q.records).items())},
            }
            for q in qs if q.variants
        ],
        "field_conflicts": field_conflicts(questions),
    }

    # ── 去重后分项（不是原始行数） ──
    person_incl = Counter()
    for q in qs:
        for p in {_person_dir(r.rel_path) for r in q.records} - {None}:
            person_incl[p] += 1
    spec_names = {s["code"]: s["name"] for s in specialties}
    breakdown = {
        "by_qtype": _sorted_counter(Counter(q.qtype for q in qs)),
        "by_question_class": _sorted_counter(Counter(q.chosen.fields["question_class"] for q in qs)),
        "by_person_scope_direct": _sorted_counter(Counter(p for q in qs for p in q.person_scopes)),
        "by_person_scope_incl_optional": _sorted_counter(person_incl),
        "by_specialty": {f"{code} {spec_names.get(code, '')}".strip(): n for code, n in sorted(
            Counter(s for q in qs for s in q.specialties).items())},
        "by_outline_l1": _sorted_counter(Counter(q.chosen.fields["outline_l1"] for q in qs)),
        "outline_l1_l2_combinations": len({(q.chosen.fields["outline_l1"], q.chosen.fields["outline_l2"]) for q in qs}),
        "auto_gradable": sum(q.auto_gradable for q in qs),
        "excluded_from_auto_pool": sum(not q.auto_gradable for q in qs),
        "by_qtype_x_person_direct": {
            p: _sorted_counter(Counter(q.qtype for q in qs if p in q.person_scopes)) for p in PERSON_CATEGORIES},
        "by_qtype_x_person_incl_optional": {
            p: _sorted_counter(Counter(q.qtype for q in qs if p in {_person_dir(r.rel_path) for r in q.records}))
            for p in PERSON_CATEGORIES},
        "by_qtype_x_specialty": {
            f"{code} {spec_names.get(code, '')}".strip(): _sorted_counter(Counter(q.qtype for q in qs if code in q.specialties))
            for code in sorted({s for q in qs for s in q.specialties})},
    }
    raw = {
        "by_qtype": _sorted_counter(Counter(r.qtype for r in recs)),
        "by_question_class": _sorted_counter(Counter(r.fields["question_class"] for r in recs)),
        "score_raw": _sorted_counter(Counter(r.raw["score"] for r in recs)),
        "score_rule": _sorted_counter(Counter(r.score_rule for r in recs)),
        "score_rule_x_qtype": _sorted_counter(Counter(f"{r.qtype}/{r.score_rule}/{r.raw['score']}" for r in recs)),
    }

    # ── 标记 ──
    summary = [r for r in recs if SUMMARY_FILE_MARK in r.rel_path]
    legacy_rows = [r for r in recs if r.is_yellow and not r.is_protected]
    required = [q for q in qs if q.is_required]
    markers = {
        "protected_rows": sum(r.is_protected for r in recs),
        "protected_files": len({r.rel_path for r in recs if r.is_protected}),
        "protected_unique": sum(q.is_protected for q in qs),
        "remark_values": _sorted_counter(Counter(r.fields["remark"] for r in recs)),
        "yellow_rows": sum(r.is_yellow for r in recs),
        "required_unique": len(required),
        "required": [{"stable_id": q.stable_id, "outline": f"{q.chosen.fields['outline_l1']}/{q.chosen.fields['outline_l2']}",
                      "stem": q.chosen.fields["stem"][:40]} for q in required],
        "color_group_unique": _sorted_counter(Counter(g for q in qs for g in q.color_groups)),
        "color_group_questions_distinct": sum(1 for q in qs if q.color_groups),
        "color_group_multi_group_questions": sum(1 for q in qs if len(q.color_groups) > 1),
        "color_group_rows": _sorted_counter(Counter(r.color_group for r in recs if r.color_group)),
        "summary_file": {
            "rows": len(summary),
            "unique": len({r.stable_id for r in summary}),
            "yellow_rows": sum(r.is_yellow for r in summary),
            "colored_rows_non_yellow": sum(1 for r in summary if r.fill and not r.is_yellow),
            "uncolored_protected_rows": sum(1 for r in summary if r.fill is None and r.is_protected),
            "uncolored_protected_unique": len({r.stable_id for r in summary if r.fill is None and r.is_protected}),
            "uncolored_by_outline_l1": _sorted_counter(Counter(r.fields["outline_l1"] for r in summary if r.fill is None)),
            "uncolored_required": sum(1 for r in summary if r.fill is None and questions[r.stable_id].is_required),
        },
        "legacy_yellow": {
            "rows": len(legacy_rows),
            "files": len({r.rel_path for r in legacy_rows}),
            "unique": len({r.stable_id for r in legacy_rows}),
            "remark_values": _sorted_counter(Counter(r.fields["remark"] for r in legacy_rows)),
            "in_required": sum(1 for sid in {r.stable_id for r in legacy_rows} if questions[sid].is_required),
            "locations": [f"{r.rel_path}#{r.sheet}!{r.row_no}" for r in legacy_rows],
        },
        "fill_tokens_rows": _sorted_counter(Counter(r.fill for r in recs if r.fill)),
    }

    # ── 格式检查 ──
    option_issue = Counter(i for r in recs for i in r.option_issues)
    fmt = {
        "dollar_semicolon_hits": sum(1 for r in recs for v in r.raw.values() if "$;$" in v),
        "pipe_option_rows": sum(1 for r in recs if "|" in r.raw["options"]),
        "option_issues": _sorted_counter(option_issue),
        "code_id_empty_rows": sum(1 for r in recs if not r.raw["code_id"]),
        "code_id_total_rows": len(recs),
        "placeholder_backslash_by_field": {k: sum(1 for r in recs if r.raw[k] == "\\") for k in sorted(recs[0].raw)} if recs else {},
        "seq_formula_rows": sum(1 for r in recs if r.raw["seq"].startswith("=")),
        "judge_default_options_filled": sum(1 for r in recs if r.qtype == "判断题" and not r.raw["options"]),
        "objective_answer_not_in_options": sum(
            1 for r in recs if r.qtype != "简答题" and any(ch not in r.options for ch in r.answer)),
        "empty_answer_rows": sum(1 for r in recs if not r.answer),
    }

    # ── golden ──
    golden = {}
    for key, mark in GOLDEN_FILES.items():
        sel = [r for r in recs if mark in r.rel_path]
        golden[key] = {"files": len({r.rel_path for r in sel}), "rows": len(sel), "unique": len({r.stable_id for r in sel})}

    # ── 逐文件 ──
    first_seen: dict[str, str] = {}
    for r in recs:
        first_seen.setdefault(r.stable_id, r.rel_path)
    new_unique = Counter(first_seen.values())
    recs_by_file = defaultdict(list)
    for r in recs:
        recs_by_file[r.rel_path].append(r)
    per_file = []
    for f in files:
        frecs = recs_by_file.get(f.rel_path, [])
        uniq = len({r.stable_id for r in frecs})
        per_file.append({
            "rel_path": f.rel_path,
            "real_format": f.real_format,
            "md5": f.md5,
            "status": f.status,
            "skip_reason": f.skip_reason,
            "person_scope": f.person_scope,
            "optional": f.optional,
            "sheets_parsed": sum(1 for s in f.sheets if s.status == "parsed"),
            "sheets_skipped": sum(1 for s in f.sheets if s.status != "parsed"),
            "rows": len(frecs),
            "empty_rows": sum(s.empty_rows for s in f.sheets),
            "unique": uniq,
            "dup_in_file": len(frecs) - uniq,
            "new_unique": new_unique.get(f.rel_path, 0),
            "yellow_rows": sum(s.yellow_rows for s in f.sheets),
            "protected_rows": sum(s.protected_rows for s in f.sheets),
            "colored_rows": sum(s.colored_rows for s in f.sheets),
        })
    per_file_rows_sum = sum(p["rows"] for p in per_file)

    report = {
        "schema": REPORT_SCHEMA,
        "bank_version": version,
        "source": source_label,
        "inventory": inventory,
        "sheets": sheet_stats,
        "rows": rows,
        "per_file_rows_sum": per_file_rows_sum,
        "dedup": dedup,
        "dedup_breakdown": breakdown,
        "raw_breakdown": raw,
        "markers": markers,
        "format_checks": fmt,
        "golden": golden,
        "per_file": per_file,
    }
    if extra:
        report.update(extra)
    report["report_hash"] = report_hash(report)
    return report


def report_hash(report: dict) -> str:
    body = {k: v for k, v in report.items() if k != "report_hash"}
    return hashlib.sha1(json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def write_report(report: dict, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"import_report_{report['bank_version']}"
    jpath, mpath = out_dir / f"{stem}.json", out_dir / f"{stem}.md"
    jpath.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=1) + "\n", encoding="utf-8", newline="\n")
    mpath.write_text(render_markdown(report), encoding="utf-8", newline="\n")
    return jpath, mpath


# ───────────────────────── Markdown ─────────────────────────

def _kv_table(d: dict, k_title="项", v_title="数量") -> list[str]:
    lines = [f"| {k_title} | {v_title} |", "|---|---:|"]
    lines += [f"| {k or '(空)'} | {v} |" for k, v in d.items()]
    return lines


def _cross_table(title: str, data: dict[str, dict]) -> list[str]:
    lines = [f"{title}：", "", "| 维度 | " + " | ".join(QTYPES) + " | 合计 |", "|---|" + "---:|" * (len(QTYPES) + 1)]
    for k, row in data.items():
        lines.append(f"| {k} | " + " | ".join(str(row.get(t, 0)) for t in QTYPES) + f" | {sum(row.values())} |")
    return lines + [""]


def render_markdown(r: dict) -> str:
    inv, rows, dd, bd, mk, fc = r["inventory"], r["rows"], r["dedup"], r["dedup_breakdown"], r["markers"], r["format_checks"]
    L = [f"# 导入报告：bank_version = `{r['bank_version']}`", "",
         f"- 来源：`{r['source']}`",
         f"- report_hash：`{r['report_hash']}`（不含时间戳，两次运行应逐字节一致）", ""]
    if "inventory" in r and inv["total_files"]:
        L += ["## 1. 文件清单", "",
              f"- 文件总数 {inv['total_files']}，字节 {inv['total_bytes']}",
              f"- 真实格式：{inv['by_real_format']}",
              f"- `.xls` 按文件头分流：{inv['xls_by_real_format']}；解析成功 {inv['xls_parsed']}；**因扩展名跳过 {inv['xls_skipped_by_extension']}**",
              f"- `.xls` 唯一 MD5：{inv['unique_md5_xls']}", ""]
        L += ["跳过文件："] + [f"- `{s['rel_path']}`（{s['real_format']}）：{s['reason']}" for s in inv["skipped_files"]] + [""]
        if "rar_check" in r:
            L += [f"RAR 校验：{r['rar_check']}", ""]
    L += ["## 2. Sheet 与行", "",
          f"- sheet 总数 {r['sheets']['total']}，解析 {r['sheets']['parsed']}（表头在第 1 行 {r['sheets']['header_row_1']}），"
          f"跳过辅助 sheet {len(r['sheets']['skipped_aux'])}",
          f"- 题目行 **{rows['data_rows']}**（逐文件求和 {r['per_file_rows_sum']}），空行 {rows['empty_rows']}，无效行 {rows['invalid_rows']}",
          f"- 按真实格式：{rows['data_rows_by_real_format']}", ""]
    L += ["辅助 sheet："] + [f"- `{s['rel_path']}` / `{s['sheet']}`" for s in r["sheets"]["skipped_aux"]] + [""]
    L += ["表头变体（sheet 数）：", ""] + _kv_table(r["sheets"]["header_variants"], "表头", "sheet") + [""]
    L += ["## 3. 去重", "",
          f"- 唯一题（stable_id = 题干+选项，**不含答案**）：**{dd['unique_stable_ids']}**",
          f"- 题干+选项+答案 变体数：{dd['stem_option_answer_variants']}（= {dd['unique_stable_ids']} + {dd['answer_variant_rows_extra']} 个少数派答案变体）",
          f"- 不同规范化题干：{dd['distinct_canonical_stems']}；共享题干的题干数 {dd['stems_with_multiple_stable_ids']}，涉及 stable_id {dd['stable_ids_under_shared_stems']}",
          f"- 重复行：{rows['data_rows']} − {dd['unique_stable_ids']} = {dd['duplicate_rows_vs_stable_ids']}"
          f"（按答案变体口径 {rows['data_rows']} − {dd['stem_option_answer_variants']} = {dd['duplicate_rows_vs_answer_variants']}）",
          f"- 同 stable_id 字段取值不一致的题数：{dd['field_conflicts']}",
          f"- stable_id 集合 sha1：`{dd['stable_ids_sha1']}`", "",
          "与方案「11,456」的对账（方案数字 = 题干+选项原文+答案 去重；本导入的身份不含答案、选项经解析规范化）：", ""]
    L += _kv_table(dd["reconciliation"], "口径", "数量") + [""]
    L += [
          "答案冲突（同题干同选项、答案不同；启用多数票答案，其余作为 retired/answer_variant 留档）：", ""]
    L += [f"- `{c['stable_id']}` {c['qtype']}「{c['stem']}」答案分布 {c['answers']}" for c in dd["answer_conflicts"]] + [""]
    L += ["## 4. 去重后分项（不是原始行数）", "", "按题型：", ""] + _kv_table(bd["by_qtype"]) + [""]
    L += ["按题目分类：", ""] + _kv_table(bd["by_question_class"]) + [""]
    L += ["按人员目录（直属文件 = 公共题范围）：", ""] + _kv_table(bd["by_person_scope_direct"]) + [""]
    L += ["按人员目录（含专业自选子目录）：", ""] + _kv_table(bd["by_person_scope_incl_optional"]) + [""]
    L += ["按专业（specialties.yaml 文件映射）：", ""] + _kv_table(bd["by_specialty"]) + [""]
    L += _cross_table("题型 × 人员目录（直属文件，去重）", bd["by_qtype_x_person_direct"])
    L += _cross_table("题型 × 人员目录（含专业自选，去重）", bd["by_qtype_x_person_incl_optional"])
    L += _cross_table("题型 × 专业（去重）", bd["by_qtype_x_specialty"])
    L += [f"- 一级纲要 × 二级纲要组合：{bd['outline_l1_l2_combinations']}",
          f"- 可自动判分：{bd['auto_gradable']}；**不进自动抽题池（简答）：{bd['excluded_from_auto_pool']}**", ""]
    L += ["原始行数（仅供对照）：", ""] + _kv_table(r["raw_breakdown"]["by_qtype"]) + [""]
    L += ["分值映射（原始值 → 行数）：", ""] + _kv_table(r["raw_breakdown"]["score_raw"], "原始分值") + [""]
    L += _kv_table(r["raw_breakdown"]["score_rule"], "映射规则") + [""]
    L += ["分值映射规则：native=数值原样保留（含异常 5、判断 1/2 等，供人工复核）；default_empty=空 → 题型默认分"
          "（单选 1 / 多选 2 / 判断 0.5）；placeholder=`\\` 或 `/` → NULL（均为简答题，不进自动判分）。", ""]
    L += _kv_table(r["raw_breakdown"]["score_rule_x_qtype"], "题型/规则/原值", "行数") + [""]
    L += ["## 5. 标记", "",
          f"- 备注=保命题：{mk['protected_rows']} 行 / {mk['protected_files']} 文件 / 去重 {mk['protected_unique']} 题",
          f"- 纯黄行 {mk['yellow_rows']}；**is_required=1：{mk['required_unique']} 道**",
          f"- color_group（各组去重题数）：{mk['color_group_unique']}，合计 {sum(mk['color_group_unique'].values())}；"
          f"跨组去重 {mk['color_group_questions_distinct']} 题（其中 {mk['color_group_multi_group_questions']} 题同属多组）",
          f"- 标黄必汇总：{mk['summary_file']}",
          f"- 遗留黄题（备注非保命题）：{mk['legacy_yellow']['rows']} 行 / {mk['legacy_yellow']['files']} 文件 / "
          f"去重 {mk['legacy_yellow']['unique']}，进必答 {mk['legacy_yellow']['in_required']}", ""]
    L += ["必答题（19）："] + [f"- `{q['stable_id']}` {q['outline']}「{q['stem']}」" for q in mk["required"]] + [""]
    L += ["## 6. 格式检查", "",
          f"- `$;$` 命中：{fc['dollar_semicolon_hits']}；含 `|` 选项行：{fc['pipe_option_rows']}；选项解析异常：{fc['option_issues']}",
          f"- 试题编码为空：{fc['code_id_empty_rows']} / {fc['code_id_total_rows']}",
          f"- `\\` 占位（按字段）：{fc['placeholder_backslash_by_field']}",
          f"- 公式序号行：{fc['seq_formula_rows']}；判断题补默认选项：{fc['judge_default_options_filled']}",
          f"- 客观题答案字母不在选项内：{fc['objective_answer_not_in_options']}；答案为空：{fc['empty_answer_rows']}", ""]
    L += ["## 7. Golden", ""] + [f"- {k}：{v}" for k, v in r["golden"].items()] + [""]
    for key in ("templates", "legacy"):
        if key in r:
            L += [f"## {key}", "", "```json", json.dumps(r[key], ensure_ascii=False, indent=1, sort_keys=True), "```", ""]
    L += ["## 8. 逐文件", "",
          "| 文件 | 格式 | 状态 | 行 | 空行 | 唯一 | 文件内重复 | 首见 | 黄 | 保命 | 着色 |",
          "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for p in r["per_file"]:
        L.append(f"| {p['rel_path']} | {p['real_format']} | {p['status']} | {p['rows']} | {p['empty_rows']} | {p['unique']} | "
                 f"{p['dup_in_file']} | {p['new_unique']} | {p['yellow_rows']} | {p['protected_rows']} | {p['colored_rows']} |")
    return "\n".join(L) + "\n"
