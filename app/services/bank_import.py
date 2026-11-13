"""管理员导入：临时预览 → 新草稿版本；事务内只写题库相关表。"""
from __future__ import annotations

import hashlib
import io
import json
import re
import secrets
import tempfile
import time
import zipfile
from pathlib import Path, PurePosixPath

from app.models import PERSON_CATEGORIES
from app.services import audit, bank_package as packages
from app.services.db import write_tx
from app.services.errors import bad_request, conflict, not_found
from app.services.util import json_dumps, now_iso
from etl.normalize import identity_input, make_content_hash

MAX_UPLOAD = 50 * 1024 * 1024
MAX_EXPANDED = 256 * 1024 * 1024
PREVIEW_TTL = 2 * 60 * 60
VERSION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
TOKEN_RE = re.compile(r"[0-9a-f]{48}\Z")


def _archive(blob):
    archive = zipfile.ZipFile(io.BytesIO(blob))
    entries = archive.infolist()
    if len(entries) > 2000 or sum(e.file_size for e in entries) > MAX_EXPANDED:
        archive.close()
        raise bad_request("压缩包过大，最多2000个文件、解压后256MB")
    return archive


def _source_path(filename):
    path = PurePosixPath(filename.replace("\\", "/"))
    if path.is_absolute() or any(p in ("..", ".") or ":" in p for p in path.parts) or not path.name:
        raise bad_request("文件路径无效")
    return path


def parse_uploads(files, person: str | None = None, specialty: str | None = None):
    """资料包复用 ETL 的解析和去重，不加载或校验正式试卷模板。"""
    from etl.fixtures import load_specialties, specialty_by_file
    from etl.pipeline import scan, build_questions
    from etl.readers import sniff
    from xlrd.biffh import XLRDError
    specialities = load_specialties()
    if person and person not in PERSON_CATEGORIES:
        raise bad_request("无效人员题库")
    if specialty and specialty not in {s["code"] for s in specialities}:
        raise bad_request("无效专业")
    if len(files) == 1 and files[0][0].lower().endswith(".json"):
        return packages.validate(json.loads(files[0][1])), {}
    with tempfile.TemporaryDirectory(prefix="exam-import-") as work:
        root = Path(work)
        entries = []
        for filename, blob in files:
            path = _source_path(filename)
            if path.suffix.lower() == ".zip":
                with _archive(blob) as archive:
                    for info in archive.infolist():
                        if not info.is_dir():
                            member = _source_path(info.filename)
                            if member.name == "bank.json":
                                if len(files) != 1:
                                    raise bad_request("完整题库包请单独上传")
                                return packages.validate(json.loads(archive.read(info))), {}
                            if member.suffix.lower() in (".xls", ".xlsx") and "__MACOSX" not in member.parts:
                                entries.append((member, archive.read(info)))
            elif path.suffix.lower() in (".xls", ".xlsx"):
                entries.append((path, blob))
            else:
                raise bad_request("请选择Excel、ZIP或完整题库JSON")
        if not entries or len(entries) > 1000 or sum(len(b) for _, b in entries) > MAX_EXPANDED:
            raise bad_request("没有可导入的Excel，或文件数量、大小超过限制")
        paths = [p for p, _ in entries]
        # 允许 ZIP 外层多包一层目录，保留人员目录及其专业自选子目录。
        while all(len(p.parts) > 1 for p in paths) and len({p.parts[0] for p in paths}) == 1:
            first = paths[0].parts[0]
            if first in PERSON_CATEGORIES or first.startswith("题库终稿"):
                break
            paths = [PurePosixPath(*p.parts[1:]) for p in paths]
        for path, (_, blob) in zip(paths, entries):
            if blob[:2] == b"PK":
                with _archive(blob):
                    pass
            target = root.joinpath(*path.parts)
            if target.exists():
                raise bad_request("存在重复文件路径，请分开导入")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(blob)
        if len(paths) == 1:
            path = root.joinpath(*paths[0].parts)
            if sniff(path) == "ooxml":
                exported = packages.from_excel(path)
                if exported:
                    return exported, {}
        try:
            scanned = scan(root)
        except XLRDError as exc:
            raise bad_request("Excel文件损坏或格式不受支持，请检查后重新上传") from exc
        mapping = specialty_by_file(specialities)
        if specialty:
            mapping.update({r.basename: specialty for r in scanned.records})
        if person:
            for r in scanned.records:
                parts = PurePosixPath(r.rel_path).parts
                relative = PurePosixPath(*parts[1:]) if parts[0] in PERSON_CATEGORIES else PurePosixPath(r.rel_path)
                if specialty and not r.optional:
                    relative = PurePosixPath("专业自选") / relative
                    r.optional = True
                r.person_scope = None if r.optional else person
                r.rel_path = person + "/" + relative.as_posix()
        questions = build_questions(scanned.records, mapping)
        if not questions:
            raise bad_request("未识别到题目，请使用原始题库表头或导出的Excel")
        return packages.from_scan(scanned, questions, mapping, specialities), {
            "raw_rows": len(scanned.records), "duplicates": len(scanned.records) - len(questions),
            "invalid_rows": len(scanned.invalid),
            "invalid_samples": scanned.invalid[:20],
            "skipped_sheets": sum(s.status != "parsed" for f in scanned.files for s in f.sheets),
        }


def _directory(conn):
    database = conn.execute("PRAGMA database_list").fetchone()[2]
    directory = Path(database).resolve().parent / "import-previews"
    directory.mkdir(parents=True, exist_ok=True)
    for path in directory.glob("*.json"):
        try:
            if time.time() - path.stat().st_mtime > PREVIEW_TTL:
                path.unlink(missing_ok=True)
        except FileNotFoundError:
            continue  # 另一导入请求可能已完成清理。
    return directory


def preview(conn, user, *, files, code, name, person=None, specialty=None):
    code, name = code.strip(), name.strip()
    if not VERSION_RE.fullmatch(code) or not name or len(name) > 100:
        raise bad_request("版本号限64位字母、数字、点、横线或下划线；题库名称限1到100字")
    if conn.execute("SELECT 1 FROM bank_versions WHERE code=?", (code,)).fetchone():
        raise conflict("版本号已存在，请填写新的版本号")
    package, details = parse_uploads(files, person, specialty)
    token = secrets.token_hex(24)
    directory = _directory(conn)
    if len(list(directory.glob("*.json"))) >= 20:
        raise bad_request("预览数量已达上限，请确认已有预览或两小时后重试")
    result = {**packages.summary(package), **details, "code": code, "name": name}
    stored = {"user_id": user["id"], "created_at": time.time(), "code": code, "name": name,
              "package": package.model_dump(), "summary": result}
    (directory / f"{token}.json").write_text(json_dumps(stored), encoding="utf-8")
    return {"token": token, **result}


def confirm(conn, user, token):
    if not TOKEN_RE.fullmatch(token):
        raise not_found("导入预览不存在")
    path = _directory(conn) / f"{token}.json"
    if not path.is_file():
        raise not_found("预览已过期，请重新上传")
    stored = json.loads(path.read_text(encoding="utf-8"))
    if stored["user_id"] != user["id"]:
        raise not_found("导入预览不存在")
    package = packages.validate(stored["package"])
    at = now_iso()
    with write_tx(conn):
        if conn.execute("SELECT 1 FROM bank_versions WHERE code=?", (stored["code"],)).fetchone():
            raise conflict("版本号已存在，本次导入未写入")
        bank = conn.execute(
            "INSERT INTO bank_versions(code,name,status,imported_at,source_dir,report_hash) VALUES (?,?,'draft',?,'upload',?)",
            (stored["code"], stored["name"], at, hashlib.sha256(packages.json_bytes(package)).hexdigest())).lastrowid
        source_ids = []
        for source in package.sources:
            fields = list(packages.Source.model_fields)
            values = source.model_dump()
            cur = conn.execute("INSERT INTO source_files(bank_version_id," + ",".join(fields) + ") VALUES ("
                               + ",".join("?" for _ in range(len(fields) + 1)) + ")",
                               [bank, *(values[key] for key in fields)])
            source_ids.append(cur.lastrowid)
        for specialty in package.specialties:
            if specialty.get("code") and specialty.get("name"):
                conn.execute("INSERT OR IGNORE INTO specialties(code,name,outline_l1,source_file_pattern) VALUES (?,?,'','')",
                             (specialty["code"], specialty["name"]))
        for q in package.questions:
            stem, options = identity_input(q.stem, q.options)
            content_hash = make_content_hash(stem, options)
            existing = conn.execute("SELECT content_hash FROM questions WHERE stable_id=?", (q.stable_id,)).fetchone()
            if existing and existing[0] != content_hash:
                raise bad_request("题目标识冲突，请检查题干与选项")
            conn.execute("INSERT OR IGNORE INTO questions VALUES (?,?,?,?,?,?)",
                         (q.stable_id, stem, options, content_hash, stored["code"], at))
            content = q.model_dump(include=set(packages.CONTENT_FIELDS))
            digest = hashlib.sha256(json_dumps(content).encode("utf-8")).hexdigest()
            for key in ("options", "color_group"):
                if content[key] is not None:
                    content[key] = json_dumps(content[key])
            fields = ["bank_version_id", "stable_id", "source_file_id", *packages.CONTENT_FIELDS, "version_hash", "created_at"]
            conn.execute("INSERT INTO question_versions(" + ",".join(fields) + ") VALUES (" + ",".join("?" for _ in fields) + ")",
                         [bank, q.stable_id, source_ids[q.sources[0]] if q.sources else None,
                          *(content[key] for key in packages.CONTENT_FIELDS), digest, at])
            conn.executemany("INSERT OR IGNORE INTO question_sources VALUES (?,?,?)",
                             [(bank, q.stable_id, source_ids[i]) for i in q.sources])
            for kind, value in set(q.tags):
                conn.execute("INSERT OR IGNORE INTO tags(tag_type,tag_value) VALUES (?,?)", (kind, value))
                conn.execute("INSERT INTO question_tags VALUES (?,?,?,?)", (bank, q.stable_id, kind, value))
        conn.execute("UPDATE source_files SET question_count=(SELECT COUNT(*) FROM question_sources qs "
                     "WHERE qs.source_file_id=source_files.id) WHERE bank_version_id=?", (bank,))
        audit.log(conn, user["employee_no"], "bank.import", f"bank_versions:{bank}", at=at, after=stored["summary"])
    # 数据已提交，临时文件清理失败不能把成功导入报告为失败。
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
    return {"bank_id": bank, "code": stored["code"], "status": "draft", "question_count": len(package.questions)}
