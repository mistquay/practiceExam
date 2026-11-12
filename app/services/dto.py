"""考试 DTO 白名单 + 答案泄漏防护。

只有 Service 层能决定「答案是否下发」：/api/exams/* 的响应由 exam_view()/result_view()
按白名单字段构造，再由 main.py 的中间件对最终字节做一次黑名单扫描（含答案字符串）。
"""
from __future__ import annotations

import re
from typing import Any

# 字段名黑名单（子串、大小写不敏感）
FORBIDDEN_KEY_PARTS = ("answer", "correct", "is_correct", "unanswered", "正确答案", "参考答案", "评分要点")
# 原始字节黑名单：这些词出现在 /api/exams/* 响应体里即判定为答案下发
FORBIDDEN_TOKENS = (
    b"answer", b"rawAnswer", b"answer_key", b"is_correct", b"unanswered",
    "正确答案".encode("utf-8"), "参考答案".encode("utf-8"),
)
_KEY_RE = re.compile("|".join(re.escape(p.lower()) for p in FORBIDDEN_KEY_PARTS), re.IGNORECASE)


def forbidden_keys(value: Any, path: str = "$") -> list[str]:
    """递归收集可疑字段名（含 dict 的 key，数组下标不记）。"""
    found: list[str] = []
    if isinstance(value, dict):
        for key, sub in value.items():
            here = f"{path}.{key}"
            if isinstance(key, str) and _KEY_RE.search(key):
                found.append(here)
            found.extend(forbidden_keys(sub, here))
    elif isinstance(value, (list, tuple)):
        for i, sub in enumerate(value):
            found.extend(forbidden_keys(sub, f"{path}[{i}]"))
    return found


def forbidden_tokens(body: bytes) -> list[str]:
    lowered = body.lower()
    hits = []
    for token in FORBIDDEN_TOKENS:
        needle = token.lower()
        if needle in lowered:
            hits.append(token.decode("utf-8", errors="replace"))
    return hits


def safe_analysis(text: str | None) -> str | None:
    """解析文本若含答案类关键词则不下发（题干/选项本身已确认 0 命中）。"""
    if not text:
        return None
    if _KEY_RE.search(text) or any(t.decode("utf-8", errors="ignore") in text for t in FORBIDDEN_TOKENS[5:]):
        return None
    return text
