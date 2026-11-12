"""补全旧版导入未保存的来源关系；索引仅含身份和来源，不含题干/答案。"""
from __future__ import annotations

import json
import sqlite3

from app.config import FIXTURES_DIR


def seed_legacy_sources(conn: sqlite3.Connection) -> None:
    data = json.loads((FIXTURES_DIR / "question_sources_2026-11.json").read_text(encoding="utf-8"))
    bank = conn.execute("SELECT id FROM bank_versions WHERE code = ?", (data["bank_code"],)).fetchone()
    if bank is None:
        return
    bid = bank["id"]
    marker = f"question_sources:{bid}:{data['version']}"
    if conn.execute("SELECT 1 FROM audit_logs WHERE action = ? AND target = ?",
                    ("practice.sources.seed", marker)).fetchone():
        return
    files = {(r["rel_path"], r["sheet"], r["md5"]): r["id"] for r in conn.execute(
        "SELECT id, rel_path, sheet, md5 FROM source_files WHERE bank_version_id = ?", (bid,))}
    known = {r[0] for r in conn.execute(
        "SELECT stable_id FROM question_versions WHERE bank_version_id = ?", (bid,))}
    rows = []
    for source in data["sources"]:
        fid = files.get((source["path"], source["sheet"], source["md5"]))
        if fid is not None:
            rows.extend((bid, sid, fid) for sid in source["ids"] if sid in known)
    conn.executemany("INSERT OR IGNORE INTO question_sources VALUES (?, ?, ?)", rows)
    from app.services import audit
    audit.log(conn, "system", "practice.sources.seed", marker,
              after={"source_relationships": len(rows)}, detail="旧库练习来源关系补全")
