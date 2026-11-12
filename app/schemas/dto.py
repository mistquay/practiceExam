"""请求 DTO：extra="ignore" 明确丢弃客户端伪造的 score / elapsed / is_correct 等字段。"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class StrictDto(BaseModel):
    model_config = ConfigDict(extra="ignore")


class LoginIn(StrictDto):
    employee_no: str
    password: str


class ExamStartIn(StrictDto):
    self_service: bool = False
    person_category: str | None = None
    batch_id: int | None = None
    template_id: int | None = None
    specialty_code: str | None = None
    seed: str | None = None


class ResponseItem(StrictDto):
    stable_id: str
    choice: str | None = ""


class ExamSubmitIn(StrictDto):
    responses: list[ResponseItem] = []


class PracticeSubmitIn(StrictDto):
    bank_id: int | None = None
    stable_id: str
    user_answer: str | None = ""


class AuthorizationIn(StrictDto):
    user_id: int
    person_category: str
    specialty_code: str | None = None
    effective_from: str
    effective_to: str | None = None
    basis_doc: str
    approver: str
    note: str | None = None


class UserPatchIn(StrictDto):
    active: bool


class TemplatePatchIn(StrictDto):
    status: str


class BatchIn(StrictDto):
    code: str
    template_id: int
    bank_version_id: int
    start_at: str | None = None
    end_at: str | None = None
    max_attempts: int = 1
    allow_retake: bool = False
    examinee_list: list[str] = []
    name: str | None = None
    status: str = "draft"


class BatchPatchIn(StrictDto):
    status: str


class GradeIn(StrictDto):
    attempt_id: int
    stable_id: str
    score: float
    comment: str | None = None
