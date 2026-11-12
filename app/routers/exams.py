from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends

from app.schemas import ExamStartIn, ExamSubmitIn
from app.services import exam as exam_service
from .deps import current_user, get_conn

router = APIRouter(prefix="/api/exams", tags=["exams"])


@router.get("/available")
def available(bank_id: int | None = None, specialty_code: str | None = None,
              paper_type: str | None = None, person_category: str | None = None, user=Depends(current_user),
              conn: sqlite3.Connection = Depends(get_conn)):
    batches = exam_service.available(conn, user, bank_id=bank_id,
                                     specialty_code=specialty_code, paper_type=paper_type,
                                     person_category=person_category)
    return {"batches": batches, "items": batches}


@router.get("/self-service/templates")
def self_service_templates(bank_id: int | None = None, person_category: str | None = None,
                           user=Depends(current_user), conn: sqlite3.Connection = Depends(get_conn)):
    items = exam_service.self_service_templates(conn, bank_id=bank_id, person_category=person_category)
    return {"items": items}


@router.post("/start")
def start(body: ExamStartIn, user=Depends(current_user),
          conn: sqlite3.Connection = Depends(get_conn)):
    return exam_service.start(conn, user, body)


@router.get("")
def list_my_exams(user=Depends(current_user), conn: sqlite3.Connection = Depends(get_conn)):
    exams = exam_service.list_my_exams(conn, user)
    return {"exams": exams, "items": exams}


@router.get("/{attempt_id}")
def get_attempt(attempt_id: int, user=Depends(current_user),
                conn: sqlite3.Connection = Depends(get_conn)):
    """断线重连：剩余时间按服务端 deadline_at，绝不重置。"""
    return exam_service.view(conn, user, attempt_id)


@router.post("/{attempt_id}/submit")
def submit(attempt_id: int, body: ExamSubmitIn, user=Depends(current_user),
           conn: sqlite3.Connection = Depends(get_conn)):
    # body 里伪造的 score / elapsed / is_correct 已被 DTO 的 extra="ignore" 丢弃
    return exam_service.submit(conn, user, attempt_id, body.responses)
