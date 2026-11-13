"""FastAPI 入口：路由装配、统一错误语义、服务端时间源、考试响应答案黑名单。

启动：/d/services/mamba_envs/ame/python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from app.config import STATIC_DIR
from app.routers import admin, auth, banks, exams, practice, records, reports, self_tests
from app.services.bootstrap import bootstrap
from app.services.registration import cleanup_loop
from app.services.dto import forbidden_keys, forbidden_tokens
from app.services.errors import ApiError
from app.services.util import now_iso


@asynccontextmanager
async def lifespan(_app: FastAPI):
    bootstrap()
    cleanup_task = asyncio.create_task(cleanup_loop())
    try:
        yield
    finally:
        cleanup_task.cancel()
        with suppress(asyncio.CancelledError):
            await cleanup_task


app = FastAPI(title="题库练习与考试", version="1.0", lifespan=lifespan)


@app.exception_handler(ApiError)
async def api_error_handler(_request: Request, exc: ApiError):
    return JSONResponse(status_code=exc.status, content={"detail": exc.detail})


@app.middleware("http")
async def server_time_and_answer_guard(request: Request, call_next):
    """① 下发 Server-Now（Date 由 ASGI 服务器提供）② 对 /api/exams/* 响应字节做黑名单扫描。"""
    response = await call_next(request)
    response.headers["Server-Now"] = now_iso()

    path = request.url.path
    if not path.startswith("/api/"):
        # 前端是无构建链的原生 ESM，文件名不带版本哈希；强制按 ETag 重新验证（304 很便宜），
        # 避免发版后浏览器按启发式缓存混用新旧模块。
        response.headers.setdefault("Cache-Control", "no-cache")
        return response
    if not (path == "/api/exams" or path.startswith("/api/exams/")):
        return response

    body = b"".join([chunk async for chunk in response.body_iterator])
    hits = forbidden_tokens(body)
    if not hits:
        try:
            hits = forbidden_keys(json.loads(body.decode("utf-8")))
        except (ValueError, UnicodeDecodeError):
            hits = []
    if hits:
        # Fail closed：任何答案类字段/词出现在考试响应体里都不下发
        return JSONResponse(
            status_code=500,
            content={"detail": "考试接口响应含判分依据，已阻断下发"},
            headers={"Server-Now": now_iso()},
        )
    headers = {
        k: v for k, v in response.headers.items()
        if k.lower() not in ("content-length", "content-encoding", "transfer-encoding")
    }
    return Response(content=body, status_code=response.status_code,
                    headers=headers, media_type=response.media_type)


app.include_router(auth.router)
app.include_router(banks.router)
app.include_router(practice.router)
app.include_router(records.router)
app.include_router(exams.router)
app.include_router(self_tests.router)
app.include_router(admin.router)
app.include_router(reports.router)

# 静态资源最后挂载，/api/* 由上面的路由优先匹配
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static-assets")
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
