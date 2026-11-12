"""组卷规则来自 fixtures/，代码只读取，不硬编码。

`paper_templates.yaml` 里的 `id_sets` 是可枚举 stable_id 集合（19 必答 / 口径 B 排除集合）；
模板字段用 `{ref: <set name>}` 引用，这里展开成列表并校验 sha1 摘要。
"""
from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from app.config import TEMPLATES_YAML, SPECIALTIES_YAML


def _ids_digest(ids: list[str]) -> str:
    return hashlib.sha1("\n".join(sorted(ids)).encode("utf-8")).hexdigest()


def read_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def template_config() -> dict[str, Any]:
    return read_yaml(TEMPLATES_YAML)


@lru_cache(maxsize=1)
def specialty_config() -> list[dict[str, Any]]:
    return read_yaml(SPECIALTIES_YAML).get("specialties", [])


def expand_templates(version: str | None = None) -> list[dict[str, Any]]:
    data = template_config()
    sets: dict[str, list[str]] = data.get("id_sets", {})
    digests: dict[str, str] = data.get("id_set_digests", {})
    for name, ids in sets.items():
        if digests.get(name) and _ids_digest(list(ids)) != digests[name]:
            raise ValueError(f"fixtures/paper_templates.yaml 的 id_sets.{name} 摘要不一致")
    out = []
    for raw in data.get("templates", []):
        if version is not None and raw.get("bank_version") != version:
            continue
        t = dict(raw)
        for key in ("required_question_ids", "exclude_question_ids"):
            value = t[key]
            if isinstance(value, dict) and "ref" in value:
                value = sets[value["ref"]]
            t[key] = sorted(set(value))
        out.append(t)
    return out


def required_19(version: str | None = None) -> list[str]:
    templates = expand_templates(version)
    if not templates:
        raise ValueError("fixtures/paper_templates.yaml 没有可用模板")
    first = templates[0]["required_question_ids"]
    for t in templates[1:]:
        if t["required_question_ids"] != first:
            raise ValueError("各模板的 19 道必答题集合不一致")
    return first


def exclude_b_ids(version: str | None = None) -> list[str]:
    templates = expand_templates(version)
    if not templates:
        raise ValueError("fixtures/paper_templates.yaml 没有可用模板")
    first = templates[0]["exclude_question_ids"]
    for t in templates[1:]:
        if t["exclude_question_ids"] != first:
            raise ValueError("各模板的口径 B 排除集合不一致")
    return first
