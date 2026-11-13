from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Query

from app.schemas import PracticeSubmitIn
from app.services import practice as practice_service
from .deps import current_user, get_conn

router = APIRouter(prefix="/api/practice", tags=["practice"])


def practice_filters(
    bank_id: int | None = None,
    person_category: str | None = None,
    specialty_code: str | None = None,
    specialty_codes: list[str] = Query(default=[]),
    scope_mode: str = "custom",
    qtype: str | None = None,
    qtypes: list[str] = Query(default=[]),
    order: str = "sequence",
    source: str = "all",
    tags: list[str] = Query(default=[]),
    source_file_ids: list[int] = Query(default=[]),
):
    return dict(bank_id=bank_id, person_category=person_category, specialty_code=specialty_code,
                qtype=qtype, qtypes=qtypes, order=order, source=source, tags=tags, source_file_ids=source_file_ids,
                specialty_codes=specialty_codes, scope_mode=scope_mode)


@router.get("/filters")
def filters(options=Depends(practice_filters), user=Depends(current_user),
            conn: sqlite3.Connection = Depends(get_conn)):
    return practice_service.filters(conn, user, **options)


@router.get("/questions")
def questions(count: int = Query(default=20, ge=1, le=200),
              stable_ids: list[str] = Query(default=[], max_length=200), options=Depends(practice_filters),
              user=Depends(current_user), conn: sqlite3.Connection = Depends(get_conn)):
    # 练习 DTO 可含参考答案与解析；考试仍使用独立白名单 DTO。
    return practice_service.questions(conn, user, count=count, stable_ids=stable_ids, **options)


@router.post("/submit")
def submit(body: PracticeSubmitIn, user=Depends(current_user),
           conn: sqlite3.Connection = Depends(get_conn)):
    # 练习事件只写 practice_records / wrong_book，绝不写 attempts
    return practice_service.submit(
        conn, user, bank_id=body.bank_id, stable_id=body.stable_id, user_answer=body.user_answer
    )
