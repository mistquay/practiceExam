"""API 请求 DTO。"""
from .dto import (
    AuthorizationIn, BatchIn, BatchPatchIn, ExamStartIn, ExamSubmitIn, GradeIn,
    LoginIn, PracticeSubmitIn, ResponseItem, TemplatePatchIn, UserPatchIn,
)

__all__ = [
    "AuthorizationIn", "BatchIn", "BatchPatchIn", "ExamStartIn", "ExamSubmitIn",
    "GradeIn", "LoginIn", "PracticeSubmitIn", "ResponseItem", "TemplatePatchIn", "UserPatchIn",
]
