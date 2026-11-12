from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, File, Query, UploadFile

from app.schemas import (
    AuthorizationIn, BatchIn, BatchPatchIn, GradeIn, TemplatePatchIn, UserPatchIn,
)
from app.services import admin as admin_service
from app.services.auth import actor_of
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


@router.get("/users/{user_id}/authorizations")
def list_authorizations(user_id: int, admin=Depends(admin_user),
                        conn: sqlite3.Connection = Depends(get_conn)):
    return {"authorizations": admin_service.list_authorizations(conn, user_id)}


@router.post("/authorizations")
def create_authorization(body: AuthorizationIn, admin=Depends(admin_user),
                         conn: sqlite3.Connection = Depends(get_conn)):
    return admin_service.create_authorization(conn, actor_of(admin), body)


@router.post("/authorizations/{auth_id}/revoke")
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
