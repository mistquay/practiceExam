"""导入流水线：sniff → parse → normalize → dedupe（报告与入库见 report.py / loader.py）。"""
from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from app.models import AUTO_GRADABLE_QTYPES, PERSON_CATEGORIES
from etl import normalize as N
from etl.readers import read_workbook, sniff

MASTER_DIR_PREFIX = "题库终稿"
OPTIONAL_DIR_MARK = "专业自选"
SUMMARY_FILE_MARK = "标黄必"
PROTECTED_REMARK = "保命题"


# ───────────────────────── 数据结构 ─────────────────────────

@dataclass
class Record:
    """一行题目（已规范化）。"""
    rel_path: str
    sheet: str
    row_no: int                 # Excel 行号（1 起）
    raw: dict[str, str]         # 14 字段原文（strip 后）
    fields: dict[str, str]      # 14 字段清洗后（`\` → ''）
    qtype: str
    options: dict[str, str]
    option_issues: list[str]
    answer: str
    score: float | None
    score_rule: str
    fill: str | None
    canon_stem: str
    canon_options: str
    stable_id: str
    person_scope: str | None    # 人员目录直属文件 → 人员类别
    optional: bool              # 位于「专业自选」子目录
    is_master: bool

    @property
    def basename(self) -> str:
        return PurePosixPath(self.rel_path).name

    @property
    def is_yellow(self) -> bool:
        return self.fill == N.YELLOW

    @property
    def is_protected(self) -> bool:
        return self.fields["remark"] == PROTECTED_REMARK

    @property
    def color_group(self) -> str | None:
        return N.COLOR_GROUPS.get(self.fill) if self.fill else None

    @property
    def priority(self) -> tuple:
        """代表行优先级：主库目录优先，其次按路径/sheet/行号。"""
        return (0 if self.is_master else 1, self.rel_path, self.sheet, self.row_no)


@dataclass
class SheetReport:
    sheet: str
    status: str                 # parsed / skipped_aux
    header_row: int | None = None
    header_variant: str | None = None
    data_rows: int = 0
    empty_rows: int = 0
    invalid_rows: int = 0
    yellow_rows: int = 0
    protected_rows: int = 0
    colored_rows: int = 0


@dataclass
class FileReport:
    rel_path: str
    real_format: str
    md5: str
    size: int
    status: str                 # parsed / skipped
    skip_reason: str | None = None
    person_scope: str | None = None
    optional: bool = False
    is_master: bool = False
    sheets: list[SheetReport] = field(default_factory=list)

    @property
    def data_rows(self) -> int:
        return sum(s.data_rows for s in self.sheets)


@dataclass
class Scan:
    files: list[FileReport]
    records: list[Record]
    invalid: list[dict]         # 有内容但缺题干/题型无法识别的行


@dataclass
class Question:
    """按 stable_id 聚合后的唯一题。"""
    stable_id: str
    canon_stem: str
    canon_options: str
    records: list[Record]                      # 按 priority 排序
    chosen: Record                             # 启用版本的代表行
    variants: list[Record]                     # 其它答案变体的代表行（留档、不启用）
    is_protected: bool
    is_required: bool
    color_groups: set[str]                     # 可多值：同题出现在多个子专业分组
    legacy_yellow: bool                        # 备注非保命题的黄底行
    person_scopes: set[str]
    specialties: set[str]
    outline_l1s: set[str]
    in_summary: bool                           # 出现在「标黄必」汇总文件

    @property
    def qtype(self) -> str:
        return self.chosen.qtype

    @property
    def auto_gradable(self) -> bool:
        return self.qtype in AUTO_GRADABLE_QTYPES

    def data_issues(self) -> list[str]:
        issues = []
        if self.variants:
            issues.append("答案冲突")
        if self.legacy_yellow:
            issues.append("遗留黄色标记")
        return issues


# ───────────────────────── 扫描与解析 ─────────────────────────

def _md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def _classify_path(rel: str) -> tuple[str | None, bool, bool]:
    parts = PurePosixPath(rel).parts
    is_master = parts[0].startswith(MASTER_DIR_PREFIX)
    optional = any(OPTIONAL_DIR_MARK in p for p in parts[:-1])
    person = parts[0] if len(parts) == 2 and parts[0] in PERSON_CATEGORIES else None
    return person, optional, is_master


def iter_source_files(source: Path) -> list[tuple[Path, str]]:
    files = [p for p in source.rglob("*") if p.is_file()]
    return sorted(((p, p.relative_to(source).as_posix()) for p in files), key=lambda x: x[1])


def scan(source: Path) -> Scan:
    source = Path(source)
    files: list[FileReport] = []
    records: list[Record] = []
    invalid: list[dict] = []
    for path, rel in iter_source_files(source):
        fmt = sniff(path)
        person, optional, is_master = _classify_path(rel)
        report = FileReport(rel, fmt, _md5(path), path.stat().st_size, "parsed",
                            person_scope=person, optional=optional, is_master=is_master)
        files.append(report)
        if fmt not in ("ooxml", "ole2"):
            report.status = "skipped"
            report.skip_reason = {
                "pdf": "非题库文件（PDF）",
                "rar": "冗余压缩副本，仅用于校验",
            }.get(fmt, f"未知格式 {fmt}")
            continue
        for sheet in read_workbook(path, fmt):
            report.sheets.append(_parse_sheet(sheet, rel, person, optional, is_master, records, invalid))
    return Scan(files, records, invalid)


def _parse_sheet(sheet, rel, person, optional, is_master, records, invalid) -> SheetReport:
    header = N.map_header(sheet.rows[0][0]) if sheet.rows else None
    if header is None:
        return SheetReport(sheet.name, "skipped_aux")
    mapping, variant = header
    rep = SheetReport(sheet.name, "parsed", header_row=1, header_variant=variant)
    for idx, (values, fills) in enumerate(sheet.rows[1:], start=2):
        raw = {f: (N.cell_text(values[c]) if c < len(values) else "") for f, c in mapping.items()}
        for f in N.FIELDS:
            raw.setdefault(f, "")
        if not any(raw[f] for f in N.FIELDS if f != "seq"):
            rep.empty_rows += 1
            continue
        fields = {k: N.clean(v) for k, v in raw.items()}
        qtype = N.norm_qtype(fields["qtype"])
        if qtype is None or not fields["stem"]:
            rep.invalid_rows += 1
            invalid.append({"rel_path": rel, "sheet": sheet.name, "row": idx,
                            "qtype": raw["qtype"], "stem": raw["stem"][:40]})
            continue
        options, issues = N.parse_options(raw["options"], qtype)
        canon_stem, canon_opts = N.identity_input(fields["stem"], options)
        score, rule = N.map_score(raw["score"], qtype)
        row_fills = [fills[c] if c < len(fills) else None for c in mapping.values()]
        rec = Record(
            rel_path=rel, sheet=sheet.name, row_no=idx, raw=raw, fields=fields, qtype=qtype,
            options=options, option_issues=issues, answer=N.norm_answer(raw["answer"], qtype),
            score=score, score_rule=rule, fill=N.row_fill(row_fills),
            canon_stem=canon_stem, canon_options=canon_opts,
            stable_id=N.make_stable_id(canon_stem, canon_opts),
            person_scope=person, optional=optional, is_master=is_master,
        )
        records.append(rec)
        rep.data_rows += 1
        rep.yellow_rows += rec.is_yellow
        rep.protected_rows += rec.is_protected
        rep.colored_rows += rec.fill is not None
    return rep


# ───────────────────────── 去重聚合 ─────────────────────────

def build_questions(records: list[Record], specialty_by_file: dict[str, str] | None = None) -> dict[str, Question]:
    """按 stable_id 聚合。同一 stable_id 多个答案时：多数票为启用版本，并列取优先级最高行。"""
    specialty_by_file = specialty_by_file or {}
    groups: dict[str, list[Record]] = defaultdict(list)
    for rec in records:
        groups[rec.stable_id].append(rec)
    questions: dict[str, Question] = {}
    for sid in sorted(groups):
        recs = sorted(groups[sid], key=lambda r: r.priority)
        by_answer: dict[str, list[Record]] = defaultdict(list)
        for r in recs:
            by_answer[r.answer].append(r)
        ranked = sorted(by_answer.values(), key=lambda rs: (-len(rs), rs[0].priority))
        questions[sid] = Question(
            stable_id=sid,
            canon_stem=recs[0].canon_stem,
            canon_options=recs[0].canon_options,
            records=recs,
            chosen=ranked[0][0],
            variants=[rs[0] for rs in ranked[1:]],
            is_protected=any(r.is_protected for r in recs),
            is_required=any(r.is_yellow and r.is_protected for r in recs),
            color_groups={r.color_group for r in recs if r.color_group},
            legacy_yellow=any(r.is_yellow and not r.is_protected for r in recs),
            person_scopes={r.person_scope for r in recs if r.person_scope},
            specialties={specialty_by_file[r.basename] for r in recs if r.basename in specialty_by_file},
            outline_l1s={r.fields["outline_l1"] for r in recs if r.fields["outline_l1"]},
            in_summary=any(SUMMARY_FILE_MARK in r.rel_path for r in recs),
        )
    return questions


def field_conflicts(questions: dict[str, Question]) -> dict[str, int]:
    """同一 stable_id 下各字段出现多个不同取值的题数（数据质量旁证）。"""
    keys = ("qtype", "answer", "score", "outline_l1", "outline_l2", "basis", "analysis")
    out = Counter()
    for q in questions.values():
        for k in keys:
            vals = {getattr(r, k) if k in ("qtype", "answer", "score") else r.fields[k] for r in q.records}
            if len(vals) > 1:
                out[k] += 1
    return {k: out.get(k, 0) for k in keys}
