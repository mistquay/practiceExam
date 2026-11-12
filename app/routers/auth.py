from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Request, Response

from app.config import COOKIE_NAME, COOKIE_SECURE, SESSION_TTL_SECONDS
from app.schemas import LoginIn
from app.services import auth as auth_service
from app.services.db import write_tx
from app.services.errors import unauthorized
from .deps import current_user, get_conn

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login")
def login(body: LoginIn, response: Response, conn: sqlite3.Connection = Depends(get_conn)):
    with write_tx(conn):
        user = auth_service.login(conn, body.employee_no, body.password)
        if user is None:
            raise unauthorized("工号或密码错误")
        token, _expires = auth_service.create_session(conn, user["id"])
    response.set_cookie(
        COOKIE_NAME, token, max_age=SESSION_TTL_SECONDS, httponly=True,
        samesite="lax", secure=COOKIE_SECURE, path="/",
    )
    return auth_service.user_dto(conn, user)


@router.post("/logout")
def logout(request: Request, response: Response, conn: sqlite3.Connection = Depends(get_conn)):
    token = request.cookies.get(COOKIE_NAME)
    if token:
        with write_tx(conn):
            auth_service.revoke_session(conn, token)
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me")
def me(user: sqlite3.Row = Depends(current_user), conn: sqlite3.Connection = Depends(get_conn)):
    """返回当前有效授权（人员类别 / 专业 / 生效期 / 依据 / 审批人），不是账号静态字段。"""
    return auth_service.user_dto(conn, user)
