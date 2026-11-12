from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Query

from app.services import records as record_service
from .deps import current_user, get_conn

router = APIRouter(prefix="/api/records", tags=["records"])


@router.get("/wrong")
def list_wrong(bank_id: int | None = None, user=Depends(current_user),
               conn: sqlite3.Connection = Depends(get_conn)):
    items = record_service.list_wrong(conn, user, bank_id)
    return {"items": items, "wrong": items}


@router.delete("/wrong")
def clear_wrong(user=Depends(current_user), conn: sqlite3.Connection = Depends(get_conn)):
    return {"deleted": record_service.clear_wrong(conn, user)}


@router.delete("/wrong/{stable_id}")
def remove_wrong(stable_id: str, user=Depends(current_user),
                 conn: sqlite3.Connection = Depends(get_conn)):
    record_service.remove_wrong(conn, user, stable_id)
    return {"ok": True}


@router.get("/practice")
def list_practice(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                  user=Depends(current_user), conn: sqlite3.Connection = Depends(get_conn)):
    return record_service.list_practice(conn, user, limit=limit, offset=offset)
