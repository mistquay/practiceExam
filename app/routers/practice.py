from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Query

from app.schemas import PracticeSubmitIn
from app.services import practice as practice_service
from .deps import current_user, get_conn

router = APIRouter(prefix="/api/practice", tags=["practice"])


@router.get("/questions")
def questions(
    bank_id: int | None = None,
    person_category: str | None = None,
    specialty_code: str | None = None,
    qtype: str | None = None,
    count: int = 20,
    order: str = "sequence",
    source: str = "all",
    user=Depends(current_user),
    conn: sqlite3.Connection = Depends(get_conn),
):
    # 练习 DTO 允许含参考答案与解析；与 /api/exams/* 的白名单 DTO 完全分离
    items = practice_service.questions(
        conn, user, bank_id=bank_id, person_category=person_category,
        specialty_code=specialty_code, qtype=qtype, count=count, order=order, source=source,
    )
    return {"questions": items, "total": len(items)}


@router.post("/submit")
def submit(body: PracticeSubmitIn, user=Depends(current_user),
           conn: sqlite3.Connection = Depends(get_conn)):
    # 练习事件只写 practice_records / wrong_book，绝不写 attempts
    return practice_service.submit(
        conn, user, bank_id=body.bank_id, stable_id=body.stable_id, user_answer=body.user_answer
    )
