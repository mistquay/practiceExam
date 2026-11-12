"""专用自主测试命名空间：参考答案仅本人已交卷后可见。"""
import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from app.services import self_tests
from .deps import current_user, get_conn

router = APIRouter(prefix='/api/self-tests', tags=['self-tests'])


class ScopeIn(BaseModel):
    model_config = ConfigDict(extra='ignore')
    bank_id: int | None = None
    person_category: str | None = None
    specialty_code: str | None = None
    tags: list[str] = Field(default_factory=list, max_length=100)
    source_file_ids: list[StrictInt] = Field(default_factory=list, max_length=100)


class StartIn(ScopeIn):
    counts: dict[str, Annotated[int, Field(strict=True, ge=0, le=200)]]
    duration_min: Annotated[int, Field(strict=True, ge=5, le=240)] = 60


class RateIn(BaseModel):
    stable_id: str
    score: Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]


@router.post('/inventory')
def inventory(body: ScopeIn, user=Depends(current_user), conn: sqlite3.Connection = Depends(get_conn)):
    return self_tests.inventory(conn, user, body)


@router.post('/start')
def start(body: StartIn, user=Depends(current_user), conn: sqlite3.Connection = Depends(get_conn)):
    return self_tests.start(conn, user, body)


@router.get('/{attempt_id}/review')
def review(attempt_id: int, user=Depends(current_user), conn: sqlite3.Connection = Depends(get_conn)):
    return self_tests.review(conn, user, attempt_id)


@router.post('/{attempt_id}/self-rating')
def rate(attempt_id: int, body: RateIn, user=Depends(current_user), conn: sqlite3.Connection = Depends(get_conn)):
    self_tests.rate(conn, user, attempt_id, body.stable_id, body.score)
    return self_tests.review(conn, user, attempt_id)
