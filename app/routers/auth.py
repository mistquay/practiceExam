from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from starlette.concurrency import run_in_threadpool

from app.services import registration

from app.config import COOKIE_NAME, COOKIE_SECURE, SESSION_TTL_SECONDS
from app.schemas import LoginIn
from app.services import auth as auth_service
from app.services.db import write_tx
from app.services.errors import unauthorized
from .deps import current_user, get_conn

router = APIRouter(prefix="/api/auth", tags=["auth"])


def registration_ip(request: Request) -> str:
    # 只使用 ASGI 的对端地址；不自行信任 X-Forwarded-For。
    return request.client.host if request.client else 'unknown'


@router.get('/captcha')
def captcha(request: Request, replace: str | None = None):
    if replace is not None and len(replace) > 128:
        raise HTTPException(400, '验证码标识无效', headers={'Cache-Control': 'no-store'})
    challenge_id, image = registration.guard.issue(registration_ip(request), replace)
    return Response(image, media_type='image/png', headers={
        'X-Captcha-ID': challenge_id, 'Cache-Control': 'no-store',
        'X-Content-Type-Options': 'nosniff',
    })


@router.post('/register', status_code=201)
async def register(request: Request, response: Response, conn: sqlite3.Connection = Depends(get_conn)):
    import json

    response.headers['Cache-Control'] = 'no-store'
    registration.guard.rate(registration_ip(request), 'register')
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > 4096:
            raise HTTPException(413, '注册表单过大')
        raw.extend(chunk)
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise HTTPException(400, '注册表单格式错误') from None
    return await run_in_threadpool(registration.register, conn, registration_ip(request), data)


@router.post("/login")
def login(body: LoginIn, response: Response, conn: sqlite3.Connection = Depends(get_conn)):
    with write_tx(conn):
        user = auth_service.login(conn, body.employee_no, body.password)
        if user is None:
            raise unauthorized("用户名或密码错误")
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
    """返回登录账号与角色；authorization 为 deprecated 历史兼容字段，非登录或考试前提。"""
    return auth_service.user_dto(conn, user)
