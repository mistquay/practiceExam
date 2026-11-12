"""--load：把去重结果写入 SQLite（幂等、可重跑、只增不删业务数据）。

重跑语义：
  * questions（身份层）INSERT OR IGNORE，stable_id 永不改写；
  * question_versions：内容 hash 相同 → 不动；不同（如答案勘误）→ 旧行 retired=superseded + 新增一行；
    本次源数据中已不存在的 stable_id → retired=removed；少数派答案变体 → retired=answer_variant 留档；
  * question_tags：仅重建本版本、ETL 管理的 tag_type；
  * 用户/成绩/试卷等业务表一概不碰。
"""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from app.db import connect
from app.db.migrate import upgrade
from app.models import (
    BANK_ACTIVE, ETL_TAG_TYPES, RETIRE_ANSWER_VARIANT, RETIRE_REMOVED, RETIRE_SUPERSEDED,
    TAG_COLOR_GROUP, TAG_DATA_ISSUE, TAG_PERSON_SCOPE, TAG_PROTECTED, TAG_REQUIRED, TAG_SPECIALTY,
)
from etl.normalize import make_content_hash, options_json
from etl.pipeline import Question, Record, Scan


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def version_hash(q: Question, rec: Record) -> str:
    payload = {
        "qtype": rec.qtype, "stem": rec.fields["stem"], "options": rec.options, "answer": rec.answer,
        "score": rec.score, "outline_l1": rec.fields["outline_l1"], "outline_l2": rec.fields["outline_l2"],
        "question_class": rec.fields["question_class"], "basis": rec.fields["basis"],
        "analysis": rec.fields["analysis"], "remark": rec.fields["remark"], "note": rec.fields["note"],
        "is_protected": q.is_protected, "is_required": q.is_required, "color_groups": sorted(q.color_groups),
    }
    return hashlib.sha1(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def question_tags(q: Question) -> list[tuple[str, str]]:
    tags = [(TAG_PERSON_SCOPE, p) for p in sorted(q.person_scopes)]
    tags += [(TAG_SPECIALTY, s) for s in sorted(q.specialties)]
    if q.is_protected:
        tags.append((TAG_PROTECTED, "保命题"))
    if q.is_required:
        tags.append((TAG_REQUIRED, "标黄必考"))
    tags += [(TAG_COLOR_GROUP, g) for g in sorted(q.color_groups)]
    tags += [(TAG_DATA_ISSUE, i) for i in q.data_issues()]
    return tags


def load(db_path: Path, *, version: str, name: str, source_label: str, scan: Scan,
         questions: dict[str, Question], report_hash: str, initial_status: str,
         specialties: list[dict] | None = None, templates: list[dict] | None = None,
         specialty_by_file: dict[str, str] | None = None) -> dict:
    upgrade(db_path)
    conn = connect(db_path)
    now = _now()
    stats = defaultdict(int)
    specialty_by_file = specialty_by_file or {}
    try:
        with conn:
            bank_id = _upsert_bank(conn, version, name, source_label, now, report_hash, initial_status, stats)
            if specialties:
                _upsert_specialties(conn, specialties)
            file_ids = _upsert_source_files(conn, bank_id, scan, specialty_by_file)
            before = conn.total_changes
            conn.executemany(
                "INSERT OR IGNORE INTO questions (stable_id, canonical_stem, canonical_options, content_hash, "
                "first_seen_version, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                [(q.stable_id, q.canon_stem, q.canon_options, _content_hash(q), version, now) for q in questions.values()],
            )
            stats["questions_inserted"] = conn.total_changes - before
            _sync_versions(conn, bank_id, questions, file_ids, now, stats)
            _sync_tags(conn, bank_id, questions, stats)
            if templates:
                _upsert_templates(conn, bank_id, templates, now, stats)
            conn.execute(
                "INSERT INTO audit_logs (actor, action, target, at, after_value, detail) VALUES (?, ?, ?, ?, ?, ?)",
                ("etl", "bank.import", f"bank_versions:{version}", now,
                 json.dumps(dict(sorted(stats.items())), ensure_ascii=False), f"report_hash={report_hash}"),
            )
        return dict(sorted(stats.items()))
    finally:
        conn.close()


def _content_hash(q: Question) -> str:
    return make_content_hash(q.canon_stem, q.canon_options)


def _upsert_bank(conn, version, name, source_label, now, report_hash, initial_status, stats) -> int:
    row = conn.execute("SELECT id FROM bank_versions WHERE code = ?", (version,)).fetchone()
    if row:
        conn.execute("UPDATE bank_versions SET imported_at = ?, report_hash = ?, source_dir = ? WHERE id = ?",
                     (now, report_hash, source_label, row["id"]))
        return row["id"]
    status = initial_status
    if status == BANK_ACTIVE and conn.execute("SELECT 1 FROM bank_versions WHERE status = 'active'").fetchone():
        status = "draft"  # 已有激活版本时新版本先进草稿，由管理员发布切换
    cur = conn.execute(
        "INSERT INTO bank_versions (code, name, source_dir, imported_at, status, published_by, report_hash) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (version, name, source_label, now, status, "etl" if status == BANK_ACTIVE else None, report_hash),
    )
    stats["bank_created"] = 1
    return cur.lastrowid


def _upsert_specialties(conn, specialties: list[dict]) -> None:
    conn.executemany(
        "INSERT INTO specialties (code, name, outline_l1, outline_l2, source_file_pattern, is_general, enabled, sort_order, note) "
        "VALUES (:code, :name, :outline_l1, :outline_l2, :source_file_pattern, :is_general, :enabled, :sort_order, :note) "
        "ON CONFLICT(code) DO UPDATE SET name = excluded.name, outline_l1 = excluded.outline_l1, "
        "outline_l2 = excluded.outline_l2, source_file_pattern = excluded.source_file_pattern, "
        "is_general = excluded.is_general, enabled = excluded.enabled, sort_order = excluded.sort_order, note = excluded.note",
        [{**s, "is_general": int(s["is_general"]), "enabled": int(s["enabled"]), "note": s.get("note")} for s in specialties],
    )


def _upsert_source_files(conn, bank_id, scan: Scan, specialty_by_file) -> dict[tuple[str, str], int]:
    per_sheet = defaultdict(set)
    for r in scan.records:
        per_sheet[(r.rel_path, r.sheet)].add(r.stable_id)
    rows = []
    for f in scan.files:
        base = PurePosixPath(f.rel_path).name
        common = (bank_id, f.rel_path, f.md5, f.real_format)
        tail = (f.person_scope, specialty_by_file.get(base), int(f.optional))
        if f.status != "parsed":
            rows.append((*common, "", None, None, *tail, 0, 0, 0, f.skip_reason))
            continue
        for s in f.sheets:
            reason = None if s.status == "parsed" else "辅助 sheet（无标准表头）"
            rows.append((*common, s.sheet, s.header_row, s.header_variant, *tail, s.data_rows,
                         len(per_sheet[(f.rel_path, s.sheet)]), s.empty_rows + s.invalid_rows, reason))
    conn.executemany(
        "INSERT INTO source_files (bank_version_id, rel_path, md5, real_format, sheet, header_row, header_variant, "
        "person_scope, specialty_code, is_professional_optional, row_count, question_count, skipped_rows, skip_reason) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(bank_version_id, rel_path, sheet) DO UPDATE SET md5 = excluded.md5, real_format = excluded.real_format, "
        "header_row = excluded.header_row, header_variant = excluded.header_variant, person_scope = excluded.person_scope, "
        "specialty_code = excluded.specialty_code, is_professional_optional = excluded.is_professional_optional, "
        "row_count = excluded.row_count, question_count = excluded.question_count, "
        "skipped_rows = excluded.skipped_rows, skip_reason = excluded.skip_reason",
        rows,
    )
    return {(r["rel_path"], r["sheet"]): r["id"] for r in conn.execute(
        "SELECT id, rel_path, sheet FROM source_files WHERE bank_version_id = ?", (bank_id,))}


def _color_json(q: Question) -> str | None:
    return json.dumps(sorted(q.color_groups), ensure_ascii=False) if q.color_groups else None


def _version_row(bank_id, q: Question, rec: Record, file_ids, now, *, retired=0, reason=None, supersedes=None):
    return (
        bank_id, q.stable_id, file_ids.get((rec.rel_path, rec.sheet)), rec.row_no, rec.raw["seq"],
        rec.fields["outline_l1"], rec.fields["outline_l2"], rec.fields["question_class"], rec.qtype,
        rec.fields["stem"], options_json(rec.options), rec.answer, rec.raw["answer"], rec.fields["basis"],
        rec.score, rec.raw["score"], rec.score_rule, rec.fields["code_id"], rec.fields["remark"],
        rec.fields["note"], rec.fields["analysis"], rec.raw["options"], rec.fill,
        int(q.is_protected), int(q.is_required), _color_json(q), int(q.auto_gradable),
        sum(1 for r in q.records if r.answer == rec.answer), version_hash(q, rec),
        retired, now if retired else None, reason, supersedes, now,
    )


_INSERT_VERSION = (
    "INSERT INTO question_versions (bank_version_id, stable_id, source_file_id, source_row, seq_in_file, "
    "outline_l1, outline_l2, question_class, qtype, stem, options, answer, answer_raw, basis, score, score_raw, "
    "score_rule, code_id, remark, note, analysis, raw_option, fill_color, is_protected, is_required, color_group, "
    "auto_gradable, occurrence_count, version_hash, retired, retired_at, retire_reason, supersedes_id, created_at) "
    "VALUES (" + ", ".join(["?"] * 34) + ")"
)


def _sync_versions(conn, bank_id, questions: dict[str, Question], file_ids, now, stats) -> None:
    active = {r["stable_id"]: (r["id"], r["version_hash"]) for r in conn.execute(
        "SELECT id, stable_id, version_hash FROM question_versions WHERE bank_version_id = ? AND retired = 0", (bank_id,))}
    variants = {(r["stable_id"], r["version_hash"]) for r in conn.execute(
        "SELECT stable_id, version_hash FROM question_versions WHERE bank_version_id = ? AND retire_reason = ?",
        (bank_id, RETIRE_ANSWER_VARIANT))}
    inserts = []
    for sid, q in questions.items():
        h = version_hash(q, q.chosen)
        old = active.get(sid)
        if old is None:
            inserts.append(_version_row(bank_id, q, q.chosen, file_ids, now))
            stats["versions_inserted"] += 1
        elif old[1] != h:
            conn.execute("UPDATE question_versions SET retired = 1, retired_at = ?, retire_reason = ? WHERE id = ?",
                         (now, RETIRE_SUPERSEDED, old[0]))
            inserts.append(_version_row(bank_id, q, q.chosen, file_ids, now, supersedes=old[0]))
            stats["versions_superseded"] += 1
        else:
            stats["versions_unchanged"] += 1
        for v in q.variants:
            if (sid, version_hash(q, v)) not in variants:
                inserts.append(_version_row(bank_id, q, v, file_ids, now, retired=1, reason=RETIRE_ANSWER_VARIANT))
                stats["answer_variants_inserted"] += 1
    gone = [old[0] for sid, old in active.items() if sid not in questions]
    conn.executemany("UPDATE question_versions SET retired = 1, retired_at = ?, retire_reason = ? WHERE id = ?",
                     [(now, RETIRE_REMOVED, i) for i in gone])
    stats["versions_removed"] += len(gone)
    conn.executemany(_INSERT_VERSION, inserts)


def _sync_tags(conn, bank_id, questions: dict[str, Question], stats) -> None:
    rows = [(bank_id, sid, t, v) for sid, q in questions.items() for t, v in question_tags(q)]
    conn.executemany("INSERT OR IGNORE INTO tags (tag_type, tag_value) VALUES (?, ?)", sorted({(t, v) for _, _, t, v in rows}))
    marks = ", ".join("?" * len(ETL_TAG_TYPES))
    conn.execute(f"DELETE FROM question_tags WHERE bank_version_id = ? AND tag_type IN ({marks})", (bank_id, *ETL_TAG_TYPES))
    conn.executemany("INSERT INTO question_tags (bank_version_id, stable_id, tag_type, tag_value) VALUES (?, ?, ?, ?)", rows)
    stats["question_tags"] = len(rows)


def _upsert_templates(conn, bank_id, templates: list[dict], now, stats) -> None:
    dump = lambda v: json.dumps(v, ensure_ascii=False, sort_keys=True)  # noqa: E731
    for t in templates:
        conn.execute(
            "INSERT INTO paper_templates (code, version, name, paper_type, bank_version_id, person_category, specialty_rule, "
            "required_question_ids, exclude_question_ids, exclude_rule, type_ratio, type_score, question_count, total_score, "
            "duration_min, pass_score, random_policy, short_answer_policy, status, pending_confirmation, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(code, version) DO UPDATE SET name = excluded.name, paper_type = excluded.paper_type, "
            "bank_version_id = excluded.bank_version_id, person_category = excluded.person_category, "
            "specialty_rule = excluded.specialty_rule, required_question_ids = excluded.required_question_ids, "
            "exclude_question_ids = excluded.exclude_question_ids, exclude_rule = excluded.exclude_rule, "
            "type_ratio = excluded.type_ratio, type_score = excluded.type_score, question_count = excluded.question_count, "
            "total_score = excluded.total_score, duration_min = excluded.duration_min, pass_score = excluded.pass_score, "
            "random_policy = excluded.random_policy, short_answer_policy = excluded.short_answer_policy, "
            "pending_confirmation = excluded.pending_confirmation, updated_at = excluded.updated_at",
            (t["code"], t["version"], t["name"], t["paper_type"], bank_id, t["person_category"], dump(t["specialty_rule"]),
             dump(t["required_question_ids"]), dump(t["exclude_question_ids"]), dump(t.get("exclude_rule")),
             dump(t["type_ratio"]), dump(t.get("type_score")), t["question_count"], t["total_score"], t["duration_min"],
             t["pass_score"], t["random_policy"], t["short_answer_policy"], t["status"],
             dump(t.get("pending_confirmation")), now, now),
        )
        stats["templates_upserted"] += 1
