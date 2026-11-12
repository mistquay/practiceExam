"""鉴权依赖：所有非登录接口都要先解析 HttpOnly Cookie Session。"""
from __future__ import annotations

import sqlite3
from typing import Iterator

from fastapi import Depends, Request

from app.config import COOKIE_NAME
from app.services.auth import user_by_token
from app.services.db import connection
from app.services.errors import forbidden, unauthorized


def get_conn() -> Iterator[sqlite3.Connection]:
    conn = connection()
    try:
        yield conn
    finally:
        conn.close()


def current_user(request: Request, conn: sqlite3.Connection = Depends(get_conn)) -> sqlite3.Row:
    user = user_by_token(conn, request.cookies.get(COOKIE_NAME))
    if user is None:
        raise unauthorized("登录已失效，请重新登录")
    return user


def admin_user(user: sqlite3.Row = Depends(current_user)) -> sqlite3.Row:
    if user["role"] != "admin":
        raise forbidden("仅管理员可执行该操作")
    return user
