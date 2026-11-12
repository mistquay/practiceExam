from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends

from app.services import banks as bank_service
from .deps import current_user, get_conn

router = APIRouter(prefix="/api/banks", tags=["banks"])


@router.get("")
def list_banks(_user=Depends(current_user), conn: sqlite3.Connection = Depends(get_conn)):
    return {"banks": bank_service.list_banks(conn)}


@router.get("/{bank_id}/dimension")
def dimension(bank_id: int, _user=Depends(current_user), conn: sqlite3.Connection = Depends(get_conn)):
    return bank_service.dimension(conn, bank_id)
