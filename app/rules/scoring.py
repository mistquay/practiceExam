"""判分与分值规则（与 ETL 的答案规范化口径一致，但不依赖 ETL 依赖）。

★ 分值冲突与确定规则（待 dong 确认）
  原始题库分值：单选 1 / 多选 2 / 判断 0.5。模板按单选 40% / 多选 30% / 判断 30%
  抽 100 题，用原始分值合计 = 40×1 + 30×2 + 30×0.5 = 115 分，与 total_score=100 冲突。
  本版采用**确定规则**：卷面分值一律以模板 `type_score` 为准（当前三种题型均为 1.0 分占位），
  即 100 题 × 1 分 = 100 分；题库 `question_versions.score` 只留档，不参与组卷与判分。
  该规则已写进 `fixtures/paper_templates.yaml` 的 `type_score` 字段与 README，
  所有数字（100 题 / 100 分 / 60 及格 / 60 分钟 / 40-30-30）均为占位，待 dong 确认。
"""
from __future__ import annotations

import re
from typing import Any

from app.models import AUTO_GRADABLE_QTYPES, QTYPE_SHORT

SCORING_RULE = "type_score（每题 1 分占位）；题库原始分值不参与卷面计分 —— 待 dong 确认"
SCORING_RULE_PENDING_CONFIRMATION = True

_ANSWER_SEP_RE = re.compile(r"[\s,，、;；]+")
_PLACEHOLDERS = {"\\", "/", "-", "—", "无"}


def norm_answer(raw: str | None) -> str:
    text = "" if raw is None else str(raw).strip()
    if text in _PLACEHOLDERS:
        return ""
    return _ANSWER_SEP_RE.sub("", text).upper()


def is_blank_answer(raw: str | None) -> bool:
    return norm_answer(raw) == ""


def score_for(qtype: str, type_score: dict[str, Any]) -> float:
    """卷面分值只认模板 type_score；缺失时回退 1 分占位（不读题库原始分值）。"""
    try:
        return float(type_score.get(qtype, 1.0))
    except (TypeError, ValueError):
        return 1.0


def grade(snapshot: dict[str, Any], user_answer: str | None) -> tuple[float, str]:
    """按锁卷快照判一道题，返回 (得分, graded_result)。

    graded_result ∈ right / wrong / pending / blank；简答题永远进人工阅卷队列。
    """
    max_score = float(snapshot.get("score", 0) or 0)
    qtype = snapshot.get("qtype", "")
    if qtype == QTYPE_SHORT or qtype not in AUTO_GRADABLE_QTYPES:
        return 0.0, "pending"
    if is_blank_answer(user_answer):
        return 0.0, "blank"
    expected = norm_answer(snapshot.get("answer"))
    given = norm_answer(user_answer)
    # 多选题字母顺序无关（AC 与 CA 等价）；客观题答案都是字母串
    if expected and given and set(expected) == set(given) and len(expected) == len(given):
        return max_score, "right"
    return 0.0, "wrong"
