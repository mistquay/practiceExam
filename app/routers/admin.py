from __future__ import annotations

import sqlite3
import zipfile
from urllib.parse import quote
from xml.etree.ElementTree import ParseError

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from app.schemas import (
    AuthorizationIn, BatchIn, BatchPatchIn, GradeIn, TemplatePatchIn, UserPatchIn,
)
from app.services import admin as admin_service
from app.services.auth import actor_of
from app.services import bank_import, bank_package, audit
from app.services.db import write_tx
from app.services.errors import bad_request, not_found
from .deps import admin_user, get_conn

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.post("/users/import")
def import_users(file: UploadFile = File(...), admin=Depends(admin_user),
                 conn: sqlite3.Connection = Depends(get_conn)):
    return admin_service.import_users(conn, actor_of(admin), file.file.read())


@router.get("/users")
def list_users(q: str | None = None, admin=Depends(admin_user),
               conn: sqlite3.Connection = Depends(get_conn)):
    return {"users": admin_service.list_users(conn, q)}


@router.post("/users/{user_id}/reset-password")
def reset_password(user_id: int, admin=Depends(admin_user),
                   conn: sqlite3.Connection = Depends(get_conn)):
    return admin_service.reset_password(conn, actor_of(admin), user_id)


@router.patch("/users/{user_id}")
def set_user_active(user_id: int, body: UserPatchIn, admin=Depends(admin_user),
                    conn: sqlite3.Connection = Depends(get_conn)):
    return admin_service.set_user_active(conn, actor_of(admin), user_id, body.active)


# Deprecated：仅兼容旧客户端与历史记录，无新管理界面依赖；仍要求管理员角色。
@router.get("/users/{user_id}/authorizations", deprecated=True)
def list_authorizations(user_id: int, admin=Depends(admin_user),
                        conn: sqlite3.Connection = Depends(get_conn)):
    return {"authorizations": admin_service.list_authorizations(conn, user_id)}


@router.post("/authorizations", deprecated=True)
def create_authorization(body: AuthorizationIn, admin=Depends(admin_user),
                         conn: sqlite3.Connection = Depends(get_conn)):
    return admin_service.create_authorization(conn, actor_of(admin), body)


@router.post("/authorizations/{auth_id}/revoke", deprecated=True)
def revoke_authorization(auth_id: int, admin=Depends(admin_user),
                         conn: sqlite3.Connection = Depends(get_conn)):
    return admin_service.revoke_authorization(conn, actor_of(admin), auth_id)


@router.get("/banks")
def list_bank_versions(admin=Depends(admin_user), conn: sqlite3.Connection = Depends(get_conn)):
    from app.services.banks import list_banks
    return {"banks": list_banks(conn, all_statuses=True)}


@router.post("/banks/{bank_id}/publish")
def publish_bank(bank_id: int, admin=Depends(admin_user),
                 conn: sqlite3.Connection = Depends(get_conn)):
    return admin_service.publish_bank(conn, actor_of(admin), bank_id)


@router.post("/banks/import/preview")
def preview_bank(files: list[UploadFile] = File(...), code: str = Form(...), name: str = Form(...),
                 person: str | None = Form(None), specialty: str | None = Form(None),
                 admin=Depends(admin_user), conn: sqlite3.Connection = Depends(get_conn)):
    if not 1 <= len(files) <= 1000:
        raise bad_request("一次最多上传1000个文件")
    uploads = []
    remaining = bank_import.MAX_UPLOAD
    for file in files:
        data = file.file.read(remaining + 1)
        remaining -= len(data)
        if remaining < 0:
            raise bad_request("上传文件合计不能超过50MB")
        uploads.append((file.filename or "", data))
    try:
        return bank_import.preview(conn, admin, files=uploads, code=code, name=name, person=person, specialty=specialty)
    except (ValueError, KeyError, zipfile.BadZipFile, ParseError, EOFError) as exc:
        raise bad_request("文件内容无法解析，请检查文件格式或重新导出") from exc


class ConfirmBankIn(BaseModel):
    token: str


@router.get("/banks/import/options")
def import_bank_options(admin=Depends(admin_user), conn: sqlite3.Connection = Depends(get_conn)):
    from app.models import PERSON_CATEGORIES
    return {"person_categories": list(PERSON_CATEGORIES),
            "specialties": [dict(r) for r in conn.execute("SELECT code,name FROM specialties ORDER BY sort_order,code")]}


@router.post("/banks/import/confirm")
def confirm_bank(body: ConfirmBankIn, admin=Depends(admin_user), conn: sqlite3.Connection = Depends(get_conn)):
    return bank_import.confirm(conn, admin, body.token)


@router.get("/banks/{bank_id}/export")
def export_bank(bank_id: int, format: str = Query("zip", pattern="^(zip|xlsx)$"),
                admin=Depends(admin_user), conn: sqlite3.Connection = Depends(get_conn)):
    from io import BytesIO
    bank = conn.execute("SELECT code FROM bank_versions WHERE id=?", (bank_id,)).fetchone()
    if not bank:
        raise not_found("题库不存在")
    # 一次读事务保证导出的题目与来源、标签属于同一数据库快照。
    conn.execute("BEGIN")
    try:
        package = bank_package.from_database(conn, bank_id)
    finally:
        conn.rollback()
    if format == "xlsx":
        body = bank_package.to_excel(package)
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("bank.json", bank_package.json_bytes(package))
        body, media_type = buffer.getvalue(), "application/zip"
    with write_tx(conn):
        audit.log(conn, actor_of(admin), "bank.export", f"bank_versions:{bank_id}", after={"format": format, "questions": len(package.questions)})
    return Response(body, media_type=media_type, headers={
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote('题库_' + bank['code'] + '.' + format)}",
        "Cache-Control": "no-store",
    })


@router.get("/templates")
def list_templates(admin=Depends(admin_user), conn: sqlite3.Connection = Depends(get_conn)):
    return {"templates": admin_service.list_templates(conn)}


@router.patch("/templates/{template_id}")
def set_template_status(template_id: int, body: TemplatePatchIn, admin=Depends(admin_user),
                        conn: sqlite3.Connection = Depends(get_conn)):
    return admin_service.set_template_status(conn, actor_of(admin), template_id, body.status)


@router.get("/batches")
def list_batches(admin=Depends(admin_user), conn: sqlite3.Connection = Depends(get_conn)):
    return {"batches": admin_service.list_batches(conn)}


@router.post("/batches")
def create_batch(body: BatchIn, admin=Depends(admin_user),
                 conn: sqlite3.Connection = Depends(get_conn)):
    return admin_service.create_batch(conn, actor_of(admin), body)


@router.patch("/batches/{batch_id}")
def set_batch_status(batch_id: int, body: BatchPatchIn, admin=Depends(admin_user),
                     conn: sqlite3.Connection = Depends(get_conn)):
    return admin_service.set_batch_status(conn, actor_of(admin), batch_id, body.status)


@router.get("/grading/pending")
def pending_grading(admin=Depends(admin_user), conn: sqlite3.Connection = Depends(get_conn)):
    return {"items": admin_service.pending_grading(conn)}


@router.post("/grading")
def grade_item(body: GradeIn, admin=Depends(admin_user),
               conn: sqlite3.Connection = Depends(get_conn)):
    return admin_service.grade_item(conn, actor_of(admin), body)


@router.get("/audit")
def list_audit(limit: int = Query(200, ge=1, le=1000), admin=Depends(admin_user),
               conn: sqlite3.Connection = Depends(get_conn)):
    return {"items": admin_service.list_audit(conn, limit)}
