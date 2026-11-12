"""通用无状态工具：时间、JSON、哈希。"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return utcnow().isoformat(timespec="seconds")


def add_minutes(moment: datetime, minutes: float) -> str:
    return (moment + timedelta(minutes=minutes)).isoformat(timespec="seconds")


def parse_instant(value: str | None, *, end_of_day: bool = False) -> datetime | None:
    """把 ISO 时间 / 纯日期解析成带 UTC 时区的 datetime。

    授权生效期由管理员按「日期」录入（前端 `<input type=date>`），纯日期的
    effective_to 必须算到当天 23:59:59，否则当天就会被判为过期。
    """
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) == 10:
        text = text + ("T23:59:59" if end_of_day else "T00:00:00")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def instant_le(value: str | None, moment: datetime, *, end_of_day: bool = False) -> bool:
    """value <= moment（无法解析时按「未生效」处理，不放大权限）。"""
    parsed = parse_instant(value, end_of_day=end_of_day)
    return parsed is not None and parsed <= moment


def instant_ge(value: str | None, moment: datetime, *, end_of_day: bool = False) -> bool:
    parsed = parse_instant(value, end_of_day=end_of_day)
    return parsed is None or parsed >= moment


def json_dumps(value: Any) -> str:
    """确定性 JSON：排序键、无多余空白、不转义中文。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def fmt_num(value: float | int | None) -> str:
    """导出 / 报表统一数字格式：整数不带 .0，其它保留两位小数。"""
    if value is None:
        return ""
    number = float(value)
    if number == int(number):
        return str(int(number))
    return f"{number:.2f}".rstrip("0").rstrip(".")
