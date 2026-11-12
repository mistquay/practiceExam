"""字段级规范化：表头映射、占位符、题型、选项、答案、分值、颜色、stable_id。

规范化规则（golden fixture 见 fixtures/golden/dedup.json，测试见 tests/test_etl.py）：
  * 规范化题干 = 去掉全部空白字符（含全角空格、换行、NBSP），不做全/半角转换；
  * 规范化选项 = 解析为 {字母: 文本}（见 parse_options），按字母排序后拼成 `A-文本|B-文本`，文本同样去空白；
    判断题空选项补 {"A":"正确","B":"错误"}；简答题选项恒为空；
  * stable_id = sha1(规范化题干 + "\\x1f" + 规范化选项)，绝不含答案/分值/解析。
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime

from app.models import (
    AUTO_GRADABLE_QTYPES, JUDGE_DEFAULT_OPTIONS, QTYPE_JUDGE, QTYPE_MULTI, QTYPE_SHORT, QTYPE_SINGLE, QTYPES,
)

# ── 表头 ──────────────────────────────────────────────
STD_HEADERS = (
    "序号", "一级纲要", "二级纲要", "题目分类", "题型", "题干", "选项",
    "答案", "题目依据", "试题分数", "试题编码", "备注", "说明", "判断题解析",
)
FIELD_OF_HEADER = dict(zip(STD_HEADERS, (
    "seq", "outline_l1", "outline_l2", "question_class", "qtype", "stem", "options",
    "answer", "basis", "score", "code_id", "remark", "note", "analysis",
)))
FIELDS = tuple(FIELD_OF_HEADER.values())
# 已知表头变体：列名写法不同但语义相同
HEADER_ALIASES = {"判断解析": "判断题解析", "2": "试题分数"}
# 表头扫描列数：标准 14 列 + 少量冗余（有 sheet 重复「试题编码」列把「判断题解析」挤到第 15 列）
HEADER_SCAN_COLS = 16

PLACEHOLDER = "\\"
WS_RE = re.compile(r"\s+")


def cell_text(value) -> str:
    """单元格值转文本：None→''；整数值浮点→'1'；其余 str() 后 strip。"""
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else repr(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value).strip()


def map_header(cells: list) -> tuple[dict[str, int], str] | None:
    """识别标准表头。返回 (字段→列下标, 变体签名)；非标准表头（辅助 sheet）返回 None。"""
    names = [cell_text(c) for c in cells[:HEADER_SCAN_COLS]]
    if not names or names[0] != "序号":
        return None
    mapping: dict[str, int] = {}
    for idx, name in enumerate(names):
        header = HEADER_ALIASES.get(name, name)
        field = FIELD_OF_HEADER.get(header)
        if field and field not in mapping:  # 重复列名取第一列
            mapping[field] = idx
    while names and names[-1] == "":
        names.pop()
    return mapping, "|".join(names)


def clean(text: str) -> str:
    """`\\` 是占位符，归一为空。"""
    return "" if text == PLACEHOLDER else text


def squash(text: str) -> str:
    return WS_RE.sub("", text)


# ── 题型 ──────────────────────────────────────────────
_QTYPE_ALIASES = {
    "单选": QTYPE_SINGLE, "单项选择题": QTYPE_SINGLE, "单选题": QTYPE_SINGLE,
    "多选": QTYPE_MULTI, "多项选择题": QTYPE_MULTI, "多选题": QTYPE_MULTI,
    "判断": QTYPE_JUDGE, "判断题": QTYPE_JUDGE,
    "简答": QTYPE_SHORT, "简答题": QTYPE_SHORT, "问答题": QTYPE_SHORT,
}


def norm_qtype(raw: str) -> str | None:
    return _QTYPE_ALIASES.get(squash(raw))


# ── 选项 ──────────────────────────────────────────────
# 老模板 `A-内容|B-内容`。实测变体：竖线写成全角「｜」或「丨」(U+4E28)；标签写成 `A.`/`A．`/`A、`/`A. `；
# 个别行无竖线、用中文逗号分隔（`A-…，B-…`）；`||` 多打一个竖线。
_OPTION_SPLIT_RE = re.compile(r"[|｜丨]")
_OPTION_LABEL_RE = re.compile(r"^\s*([A-Za-z])\s*[-－.．、]\s*(.*)$", re.S)
_OPTION_COMMA_RE = re.compile(r"[，,]\s*(?=[A-Za-z]\s*[-－.．、])")


def parse_options(raw: str, qtype: str) -> tuple[dict[str, str], list[str]]:
    """解析选项。返回 (选项, 异常说明列表)。

    * 简答题：选项恒为 {}（源数据里 11 行把答案要点错放在选项列，原文仍存 raw_option，不进身份、不下发）；
    * 判断题空选项补 {"A":"正确","B":"错误"}；
    * 只接受「按字母顺序的下一个标签」为新选项，否则视为上一选项正文
      （源数据有 `inetd.conf`→`inet|D-conf`、`File.separator`→`Fil|E-separator` 之类的替换事故）。
    """
    text = clean(raw)
    if qtype == QTYPE_SHORT:
        return {}, (["short_answer_option_text_ignored"] if text else [])
    if not text:
        return (dict(JUDGE_DEFAULT_OPTIONS) if qtype == QTYPE_JUDGE else {}), []
    issues: list[str] = []
    parts = _OPTION_SPLIT_RE.split(text)
    if any(not p.strip() for p in parts):
        issues.append("empty_segment")
    parts = [p for p in parts if p.strip()]
    if len(parts) < 2:
        comma_parts = [p for p in _OPTION_COMMA_RE.split(text) if p.strip()]
        if len(comma_parts) >= 2:
            parts = comma_parts
            issues.append("comma_separated")
    options: dict[str, str] = {}
    last_key = None
    for part in parts:
        m = _OPTION_LABEL_RE.match(part)
        expected = chr(ord("A") + len(options))
        if m and m.group(1).upper() == expected:
            last_key = expected
            options[last_key] = m.group(2).strip()
        elif last_key is not None:
            options[last_key] += "|" + part
            issues.append("out_of_sequence_label" if m else "pipe_in_option_text")
        else:
            options["_"] = options.get("_", "") + part
            issues.append("unparsable_option")
    return options, issues


def canonical_options(options: dict[str, str]) -> str:
    return "|".join(f"{k}-{squash(v)}" for k, v in sorted(options.items()))


def options_json(options: dict[str, str]) -> str:
    return json.dumps(options, ensure_ascii=False, sort_keys=True)


# ── 答案 ──────────────────────────────────────────────
_ANSWER_SEP_RE = re.compile(r"[\s,，、;；]+")


def norm_answer(raw: str, qtype: str) -> str:
    """客观题：大写、去空白与分隔符；简答题：保留原文（仅去占位符与首尾空白）。"""
    text = clean(raw)
    if qtype in AUTO_GRADABLE_QTYPES:
        return _ANSWER_SEP_RE.sub("", text).upper()
    return text


# ── 分值 ──────────────────────────────────────────────
DEFAULT_SCORE = {QTYPE_SINGLE: 1.0, QTYPE_MULTI: 2.0, QTYPE_JUDGE: 0.5, QTYPE_SHORT: None}
SCORE_PLACEHOLDERS = {"\\", "/"}


def map_score(raw: str, qtype: str) -> tuple[float | None, str]:
    """分值映射：返回 (分值, 规则)。

    native         数值原样（含异常 5、单选 0.5 等，原值保留供人工复核）
    default_empty  空 → 题型默认分（单选 1 / 多选 2 / 判断 0.5）；简答为 None
    placeholder    `\\` 或 `/` → None（全部为简答题，不进自动判分）
    invalid        非数值 → None
    """
    text = raw.strip()
    if text == "":
        return DEFAULT_SCORE.get(qtype), "default_empty"
    if text in SCORE_PLACEHOLDERS:
        return None, "placeholder"
    try:
        return float(text), "native"
    except ValueError:
        return None, "invalid"


# ── 颜色 ──────────────────────────────────────────────
YELLOW = "FFFFFF00"
NO_FILL_TOKENS = {"FFFFFFFF", "theme0"}  # 白底/主题背景色，视同无底色
# 「标黄必」汇总文件中 6 种非黄底色 → 配电子专业分组（与该行二级纲要一一对应，测试中校验）
COLOR_GROUPS = {
    "FF00B0F0": "配电一次运检",
    "FF002060": "配电二次运检",
    "theme4": "配电带电",
    "FFFF0000": "配电施工",
    "FF7030A0": "配电电缆",
    "FF92D050": "配网调控",
}


def row_fill(fills: list[str | None]) -> str | None:
    """整行底色 = 出现次数最多的有效底色（并列取列序靠前者）。"""
    counts: dict[str, int] = {}
    for token in fills:
        if token and token not in NO_FILL_TOKENS:
            counts[token] = counts.get(token, 0) + 1
    if not counts:
        return None
    return max(counts, key=lambda t: (counts[t], -fills.index(t)))


# ── 身份 ──────────────────────────────────────────────
def identity_input(stem: str, options: dict[str, str]) -> tuple[str, str]:
    return squash(stem), canonical_options(options)


def make_stable_id(canon_stem: str, canon_options: str) -> str:
    return hashlib.sha1(f"{canon_stem}\x1f{canon_options}".encode("utf-8")).hexdigest()


def make_content_hash(canon_stem: str, canon_options: str) -> str:
    return hashlib.sha256(f"{canon_stem}\x1f{canon_options}".encode("utf-8")).hexdigest()


assert set(DEFAULT_SCORE) == set(QTYPES)
