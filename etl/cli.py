"""命令行入口。

  python -m etl import --source 2026-11-12 --version 2026-11 [--dry-run] [--load] [--verify-rar]
  python -m etl import --version legacy [--source questions.db] [--load]
  python -m etl gen-fixtures --source 2026-11-12 --version 2026-11
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from app.db import REPO_ROOT, resolve_db_path
from app.models import BANK_ACTIVE, BANK_ARCHIVED, LEGACY_BANK_CODE
from etl import fixtures as F
from etl.legacy import scan_legacy
from etl.loader import load
from etl.pipeline import Question, Scan, build_questions, scan
from etl.report import build_report, write_report

DEFAULT_REPORT_DIR = REPO_ROOT / "data"
WINRAR_UNRAR = Path(r"C:\Program Files\WinRAR\UnRAR.exe")


def _resolve(path: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else (Path.cwd() / p)


@dataclass
class Prepared:
    """一次解析的全部产物（report 与 load 共用）。"""
    source: Path
    version: str
    scan: Scan
    questions: dict[str, Question]
    specialties: list[dict] = field(default_factory=list)
    specialty_by_file: dict[str, str] = field(default_factory=dict)
    templates: list[dict] = field(default_factory=list)
    extra: dict = field(default_factory=dict)

    @property
    def is_legacy(self) -> bool:
        return self.version == LEGACY_BANK_CODE


def prepare(source: Path, version: str, *, verify_rar: bool = False) -> Prepared:
    """sniff → parse → normalize → dedupe，并按 fixtures 校验模板 id 集合。"""
    if version == LEGACY_BANK_CODE:
        sc = scan_legacy(source)
        return Prepared(source, version, sc, build_questions(sc.records))
    sc = scan(source)
    specialties = F.load_specialties()
    spec_map = F.specialty_by_file(specialties)
    questions = build_questions(sc.records, spec_map)
    id_sets = F.compute_id_sets(questions)
    tpl_data = F.load_templates()
    F.validate_templates(tpl_data, id_sets)
    templates = F.expand_templates(tpl_data, version)
    extra = {"templates": {
        "id_sets": {k: {"count": len(v), "sha1": F.ids_digest(v)} for k, v in id_sets.items()},
        "codes": [t["code"] for t in templates],
        "pool_preview_auto_gradable": F.pool_preview(questions, templates),
    }}
    if verify_rar:
        extra["rar_check"] = verify_rar_copies(source, sc)
    return Prepared(source, version, sc, questions, specialties, spec_map, templates, extra)


def load_prepared(p: Prepared, db_path: Path, report_hash: str) -> dict:
    return load(
        db_path, version=p.version, name="旧题库（questions.db）" if p.is_legacy else f"{p.version} 安规题库",
        source_label=p.source.name, scan=p.scan, questions=p.questions, report_hash=report_hash,
        initial_status=BANK_ARCHIVED if p.is_legacy else BANK_ACTIVE,
        specialties=p.specialties, templates=p.templates, specialty_by_file=p.specialty_by_file,
    )


def run_import(source: Path, version: str, *, report_dir: Path, load_db: Path | None = None,
               verify_rar: bool = False) -> dict:
    """解析 + 报告（+ 入库）。返回报告 dict（load 时附带 load_stats，load_stats 不写入报告文件）。"""
    p = prepare(source, version, verify_rar=verify_rar)
    report = build_report(version, source.name, p.scan, p.questions, p.specialties, p.extra)
    write_report(report, report_dir)
    if load_db is not None:
        report["load_stats"] = load_prepared(p, load_db, report["report_hash"])
    return report


def verify_rar_copies(source: Path, sc: Scan) -> dict:
    """RAR 仅用于校验：解压到系统临时目录，逐个比对 MD5 是否能在磁盘副本中找到。"""
    unrar = shutil.which("UnRAR") or (str(WINRAR_UNRAR) if WINRAR_UNRAR.is_file() else None)
    rars = [f for f in sc.files if f.real_format == "rar"]
    if not unrar or not rars:
        return {"status": "skipped", "reason": "未找到 UnRAR 或无 RAR 文件"}
    disk = {f.md5 for f in sc.files if f.real_format in ("ooxml", "ole2")}
    entries = matched = 0
    unmatched: list[str] = []
    with tempfile.TemporaryDirectory(prefix="etl_rar_") as tmp:
        for f in rars:
            subprocess.run([unrar, "x", "-y", "-idq", str(source / f.rel_path), tmp + "\\"], check=True)
        for p in sorted(Path(tmp).rglob("*")):
            if p.is_file():
                entries += 1
                if hashlib.md5(p.read_bytes()).hexdigest() in disk:
                    matched += 1
                else:
                    unmatched.append(p.relative_to(tmp).as_posix())
    return {"status": "checked", "rar_files": len(rars), "entries": entries, "md5_matched_disk": matched,
            "unmatched": unmatched, "used_as_data_source": False}


def run_gen_fixtures(source: Path, version: str) -> None:
    sc = scan(source)
    questions = build_questions(sc.records, F.specialty_by_file(F.load_specialties()))
    id_sets = F.compute_id_sets(questions)
    F.write_text(F.TEMPLATES_PATH, F.render_templates_yaml(version, id_sets))
    F.write_text(F.GOLDEN_PATH, F.dump_json(F.build_golden(sc, questions)))
    print(f"written: {F.TEMPLATES_PATH}\nwritten: {F.GOLDEN_PATH}")


def _summary(report: dict) -> str:
    d, m, b = report["dedup"], report["markers"], report["dedup_breakdown"]
    lines = [
        f"bank_version={report['bank_version']} report_hash={report['report_hash']}",
        f"files={report['inventory']['total_files']} formats={report['inventory']['by_real_format']}",
        f"rows={report['rows']['data_rows']} empty={report['rows']['empty_rows']} invalid={report['rows']['invalid_rows']}",
        f"unique_stable_ids={d['unique_stable_ids']} answer_variants={d['stem_option_answer_variants']} "
        f"distinct_stems={d['distinct_canonical_stems']}",
        f"dedup_by_qtype={b['by_qtype']}",
        f"protected_rows={m['protected_rows']} required={m['required_unique']} color_groups={m['color_group_unique']}",
    ]
    if "load_stats" in report:
        lines.append(f"load_stats={report['load_stats']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m etl", description="题库导入流水线")
    sub = parser.add_subparsers(dest="command", required=True)

    imp = sub.add_parser("import", help="解析题库并生成报告，可选入库")
    imp.add_argument("--source", help="资料目录；--version legacy 时为旧 questions.db（默认根目录 questions.db）")
    imp.add_argument("--version", required=True, help="题库版本代码，如 2026-11；legacy 表示旧库")
    imp.add_argument("--dry-run", action="store_true", help="只解析与出报告（默认行为）")
    imp.add_argument("--load", action="store_true", help="解析后写入数据库")
    imp.add_argument("--db", help="数据库路径，默认 EXAM_DB_PATH 或 data/exam.db")
    imp.add_argument("--report-dir", default=str(DEFAULT_REPORT_DIR), help="报告输出目录，默认 data/")
    imp.add_argument("--verify-rar", action="store_true", help="解压 RAR 到临时目录比对 MD5（不作数据源）")

    gen = sub.add_parser("gen-fixtures", help="按规则重算并写出 fixtures/paper_templates.yaml 与 golden")
    gen.add_argument("--source", required=True)
    gen.add_argument("--version", required=True)

    args = parser.parse_args(argv)
    if args.command == "gen-fixtures":
        run_gen_fixtures(_resolve(args.source), args.version)
        return 0
    if args.dry_run and args.load:
        parser.error("--dry-run 与 --load 互斥")
    if args.source:
        source = _resolve(args.source)
    elif args.version == LEGACY_BANK_CODE:
        source = REPO_ROOT / "questions.db"
    else:
        parser.error("--source 必填")
    report = run_import(source, args.version, report_dir=_resolve(args.report_dir),
                        load_db=resolve_db_path(args.db) if args.load else None, verify_rar=args.verify_rar)
    print(_summary(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
