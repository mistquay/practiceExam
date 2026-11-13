"""可移植题库格式：只含当前题目、来源、标签与专业，不含用户数据。"""
from __future__ import annotations

import io
import json
from collections import Counter
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.models import ETL_TAG_TYPES, PERSON_CATEGORIES, QTYPE_SHORT
from app.services.errors import bad_request
from etl.normalize import identity_input, make_stable_id

FORMAT = "practice-exam-bank"
CONTENT_FIELDS = (
    "qtype", "stem", "options", "answer", "answer_raw", "basis", "score", "score_raw", "score_rule",
    "outline_l1", "outline_l2", "question_class", "code_id", "remark", "note", "analysis", "raw_option",
    "fill_color", "is_protected", "is_required", "color_group", "auto_gradable",
)
HEADERS = {
    "stable_id": "题目标识", "qtype": "题型", "stem": "题干", "options": "选项", "answer": "答案",
    "answer_raw": "原始答案", "basis": "题目依据", "score": "分值", "score_raw": "原始分值", "score_rule": "分值规则",
    "outline_l1": "一级纲要", "outline_l2": "二级纲要", "question_class": "题目分类", "code_id": "试题编码",
    "remark": "备注", "note": "说明", "analysis": "解析", "raw_option": "原始选项", "fill_color": "原始底色",
    "is_protected": "保命题", "is_required": "必答题", "color_group": "颜色分组", "auto_gradable": "客观题",
    "sources": "来源索引", "tags": "标签", "rel_path": "来源路径", "sheet": "工作表", "md5": "文件校验值",
    "real_format": "文件格式", "person_scope": "人员题库", "specialty_code": "专业代码",
    "is_professional_optional": "专业补充题库",
}


class Source(BaseModel):
    rel_path: str = Field(min_length=1, max_length=1000)
    sheet: str = Field(default="", max_length=200)
    md5: str = ""
    real_format: str = "package"
    person_scope: str | None = None
    specialty_code: str | None = None
    is_professional_optional: bool = False


class Question(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    stable_id: str = ""
    qtype: Literal["单选题", "多选题", "判断题", "简答题"]
    stem: str = Field(min_length=1)
    options: dict[str, str] = Field(default_factory=dict)
    answer: str
    answer_raw: str | None = None
    basis: str | None = None
    score: float | None = Field(default=None, ge=0)
    score_raw: str | None = None
    score_rule: str | None = None
    outline_l1: str | None = None
    outline_l2: str | None = None
    question_class: str | None = None
    code_id: str | None = None
    remark: str | None = None
    note: str | None = None
    analysis: str | None = None
    raw_option: str | None = None
    fill_color: str | None = None
    is_protected: bool = False
    is_required: bool = False
    color_group: list[str] | None = None
    auto_gradable: bool = True
    sources: list[int] = Field(default_factory=list)
    tags: list[tuple[str, str]] = Field(default_factory=list)

    @model_validator(mode="after")
    def identity(self):
        if not self.stem.strip():
            raise ValueError("题干不能为空")
        canonical = identity_input(self.stem, self.options)
        sid = make_stable_id(*canonical)
        if self.stable_id and self.stable_id != sid:
            raise ValueError("题目标识与题干、选项不一致")
        self.stable_id = sid
        self.auto_gradable = self.qtype != QTYPE_SHORT
        if any(kind not in ETL_TAG_TYPES for kind, _ in self.tags):
            raise ValueError("包含不支持的题目标签")
        if any(kind == "person_scope" and value not in PERSON_CATEGORIES for kind, value in self.tags):
            raise ValueError("人员题库名称无效")
        return self


class Package(BaseModel):
    format: Literal["practice-exam-bank"] = FORMAT
    format_version: Literal[1] = 1
    sources: list[Source] = Field(max_length=2000)
    questions: list[Question] = Field(min_length=1, max_length=100000)
    specialties: list[dict[str, str]] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def relations(self):
        identities = [q.stable_id for q in self.questions]
        if len(set(identities)) != len(identities):
            raise ValueError("题库包中存在重复题目标识")
        paths = [(s.rel_path, s.sheet) for s in self.sources]
        if len(set(paths)) != len(paths):
            raise ValueError("来源路径与工作表重复")
        for q in self.questions:
            if any(i < 0 or i >= len(self.sources) for i in q.sources):
                raise ValueError("题目引用的来源不存在")
        return self


def validate(data) -> Package:
    try:
        return Package.model_validate(data)
    except ValidationError as exc:
        errors = [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()[:5]]
        raise bad_request("题库格式错误；" + "；".join(errors)) from exc


def json_bytes(package: Package) -> bytes:
    return package.model_dump_json().encode("utf-8")


def from_database(conn, bank_id: int) -> Package:
    sources = [dict(r) for r in conn.execute(
        "SELECT * FROM source_files WHERE bank_version_id=? ORDER BY id", (bank_id,))]
    index = {s["id"]: i for i, s in enumerate(sources)}
    tags = {}
    for r in conn.execute("SELECT * FROM question_tags WHERE bank_version_id=?", (bank_id,)):
        tags.setdefault(r["stable_id"], []).append((r["tag_type"], r["tag_value"]))
    links = {}
    for r in conn.execute("SELECT * FROM question_sources WHERE bank_version_id=? ORDER BY source_file_id", (bank_id,)):
        links.setdefault(r["stable_id"], []).append(index[r["source_file_id"]])
    questions = []
    for row in conn.execute("SELECT * FROM question_versions WHERE bank_version_id=? AND retired=0 ORDER BY id", (bank_id,)):
        item = {key: row[key] for key in CONTENT_FIELDS}
        item.update(stable_id=row["stable_id"], options=json.loads(row["options"]),
                    color_group=json.loads(row["color_group"] or "null"),
                    tags=tags.get(row["stable_id"], []), sources=links.get(row["stable_id"], []))
        questions.append(item)
    specialties = [dict(r) for r in conn.execute("SELECT code, name FROM specialties ORDER BY code")]
    return validate(dict(sources=sources, questions=questions, specialties=specialties))


def from_scan(scan, questions, specialty_by_file, specialties) -> Package:
    from etl.loader import question_tags
    sources = []
    index = {}
    for r in scan.records:
        key = (r.rel_path, r.sheet)
        if key in index:
            continue
        index[key] = len(sources)
        sources.append(dict(rel_path=r.rel_path, sheet=r.sheet, real_format="excel",
                            person_scope=r.person_scope, specialty_code=specialty_by_file.get(r.basename),
                            is_professional_optional=r.optional))
    items = []
    for q in questions.values():
        r = q.chosen
        item = {key: r.fields.get(key) for key in CONTENT_FIELDS}
        item.update(qtype=r.qtype, stem=r.fields["stem"], options=r.options, answer=r.answer,
                    answer_raw=r.raw["answer"], score=r.score, score_raw=r.raw["score"], score_rule=r.score_rule,
                    raw_option=r.raw["options"], fill_color=r.fill, is_protected=q.is_protected,
                    is_required=q.is_required, color_group=sorted(q.color_groups), auto_gradable=q.auto_gradable,
                    tags=question_tags(q), sources=sorted({index[(rec.rel_path, rec.sheet)] for rec in q.records}))
        items.append(item)
    return validate(dict(sources=sources, questions=items,
                         specialties=[{k: s[k] for k in ("code", "name")} for s in specialties]))


def summary(package: Package) -> dict:
    warnings = []
    conflicts = 0
    for i, q in enumerate(package.questions, 1):
        if ("data_issue", "答案冲突") in q.tags:
            conflicts += 1
        if q.qtype != QTYPE_SHORT and (not q.answer or not q.options or not set(q.answer) <= set(q.options)):
            warnings.append(f"第{i}题：答案为空或与选项不匹配")
    return dict(total=len(package.questions), sources=len(package.sources),
                counts=dict(Counter(q.qtype for q in package.questions)),
                personnel=dict(Counter(value for q in package.questions for kind, value in q.tags if kind == "person_scope")),
                specialty_counts=dict(Counter(value for q in package.questions for kind, value in q.tags if kind == "specialty")),
                protected=sum(q.is_protected for q in package.questions), conflicts=conflicts,
                warning_count=len(warnings), warnings=warnings[:30])


def to_excel(package: Package) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    wb = Workbook()
    meta = wb.active
    meta.title = "题库信息"
    meta.append(["format", FORMAT])
    meta.append(["format_version", 1])
    meta.append(["specialties", json.dumps(package.specialties, ensure_ascii=False)])
    sheets = [("题目", ["stable_id", *CONTENT_FIELDS, "sources", "tags"], [q.model_dump() for q in package.questions]),
              ("来源", list(Source.model_fields), [s.model_dump() for s in package.sources])]
    for title, fields, records in sheets:
        ws = wb.create_sheet(title)
        ws.append([HEADERS[k] for k in fields])
        for record in records:
            values = [json.dumps(record[k], ensure_ascii=False) if isinstance(record[k], (dict, list)) else record[k] for k in fields]
            if any(isinstance(value, str) and len(value) > 32767 for value in values):
                raise bad_request("部分题目内容超过Excel单元格上限，请导出完整题库包")
            ws.append(values)
        # 题干、答案等始终写为文本，避免被 Excel 当作公式执行。
        for row in ws:
            for cell in row:
                if isinstance(cell.value, str):
                    cell.data_type = "s"
        for cell in ws[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="294C78")
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
        for column in ws.columns:
            ws.column_dimensions[column[0].column_letter].width = 24
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def from_excel(path) -> Package | None:
    from openpyxl import load_workbook
    with path.open("rb") as source:
        wb = load_workbook(source, read_only=True, data_only=False)
        try:
            if "题库信息" not in wb.sheetnames or wb["题库信息"].cell(1, 2).value != FORMAT:
                return None
            meta = dict(wb["题库信息"].values)
            def records(name):
                rows = iter(wb[name].values)
                reverse = {value: key for key, value in HEADERS.items()}
                fields = [reverse.get(value, value) for value in next(rows)]
                result = []
                for row in rows:
                    if not any(value is not None for value in row):
                        continue
                    item = dict(zip(fields, row))
                    for key in ("options", "color_group", "sources", "tags"):
                        if item.get(key) is not None:
                            item[key] = json.loads(item[key])
                    if name == "来源":
                        item["sheet"] = item.get("sheet") or ""
                        item["md5"] = item.get("md5") or ""
                    if name == "题目":
                        item["answer"] = item.get("answer") or ""
                        # Excel可编辑题干、选项；重导时重新计算身份，不复用旧身份。
                        item.pop("stable_id", None)
                    result.append(item)
                return result
            return validate(dict(format=meta["format"], format_version=meta["format_version"],
                                 specialties=json.loads(meta.get("specialties") or "[]"),
                                 sources=records("来源"), questions=records("题目")))
        finally:
            wb.close()
