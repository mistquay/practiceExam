"""旧库导入：archive/questions.db（单表 questions，1325 行）→ bank_version=legacy（archived，默认不激活）。

旧表字段 → 新字段：category→basis，explanation/analysis→analysis，codeId→code_id，remarks→remark，
Description→note；options 为 JSON 对象，复用同一套规范化与 stable_id 规则（可与新库按 stable_id 对账）。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

from etl import normalize as N
from etl.pipeline import FileReport, Record, Scan, SheetReport

LEGACY_SHEET = "questions"


def _text(v) -> str:
    return N.cell_text(v)


def scan_legacy(db_path: Path) -> Scan:
    db_path = Path(db_path)
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT * FROM questions ORDER BY id").fetchall()
    finally:
        conn.close()
    md5 = hashlib.md5(db_path.read_bytes()).hexdigest()
    records: list[Record] = []
    invalid: list[dict] = []
    per_source: dict[str, SheetReport] = defaultdict(lambda: SheetReport(LEGACY_SHEET, "parsed", header_row=1,
                                                                         header_variant="legacy.questions"))
    for row in rows:
        rel = f"{db_path.name}#{_text(row['source_file'])}"
        rep = per_source[rel]
        qtype = N.norm_qtype(_text(row["type"]))
        stem = _text(row["question"])
        if qtype is None or not stem:
            rep.invalid_rows += 1
            invalid.append({"rel_path": rel, "sheet": LEGACY_SHEET, "row": row["id"], "qtype": _text(row["type"]), "stem": stem[:40]})
            continue
        try:
            options = {k.upper(): _text(v) for k, v in json.loads(row["options"] or "{}").items()}
        except json.JSONDecodeError:
            options = {}
        if qtype == N.QTYPE_SHORT:
            options = {}
        elif not options and qtype == N.QTYPE_JUDGE:
            options = dict(N.JUDGE_DEFAULT_OPTIONS)
        raw = {f: "" for f in N.FIELDS}
        raw.update({
            "seq": str(row["id"]), "qtype": _text(row["type"]), "stem": stem, "options": _text(row["options"]),
            "answer": _text(row["answer"]), "basis": _text(row["category"]), "score": _text(row["score"]),
            "code_id": _text(row["codeId"]), "remark": _text(row["remarks"]), "note": _text(row["Description"]),
            "analysis": _text(row["explanation"]) or _text(row["analysis"]),
        })
        fields = {k: N.clean(v) for k, v in raw.items()}
        canon_stem, canon_opts = N.identity_input(stem, options)
        score, rule = N.map_score(raw["score"], qtype)
        records.append(Record(
            rel_path=rel, sheet=LEGACY_SHEET, row_no=row["id"], raw=raw, fields=fields, qtype=qtype,
            options=options, option_issues=[], answer=N.norm_answer(raw["answer"], qtype), score=score,
            score_rule=rule, fill=None, canon_stem=canon_stem, canon_options=canon_opts,
            stable_id=N.make_stable_id(canon_stem, canon_opts), person_scope=None, optional=False, is_master=True,
        ))
        rep.data_rows += 1
    files = []
    for rel in sorted(per_source):
        f = FileReport(rel, "sqlite", md5, db_path.stat().st_size, "parsed", is_master=True)
        f.sheets.append(per_source[rel])
        files.append(f)
    return Scan(files, records, invalid)
