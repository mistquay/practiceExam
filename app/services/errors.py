"""Service 层错误：由 main.py 统一转成 HTTP 状态码。"""
from __future__ import annotations


class ApiError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def unauthorized(detail: str = "请先登录") -> ApiError:
    return ApiError(401, detail)


def forbidden(detail: str = "无权限") -> ApiError:
    return ApiError(403, detail)


def not_found(detail: str = "资源不存在") -> ApiError:
    return ApiError(404, detail)


def conflict(detail: str = "操作冲突") -> ApiError:
    return ApiError(409, detail)


def bad_request(detail: str = "参数错误") -> ApiError:
    return ApiError(422, detail)
