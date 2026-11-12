from __future__ import annotations

import sqlite3
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from app.services import reports as report_service
from .deps import current_user, get_conn

router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.get("/export")
def export(batch_id: int | None = None, user_id: int | None = None,
           format: str = Query("csv", pattern="^(csv|json)$"),
           user=Depends(current_user), conn: sqlite3.Connection = Depends(get_conn)):
    body, media_type = report_service.export(
        conn, user, batch_id=batch_id, user_id=user_id, fmt=format
    )
    filename = f"成绩导出_{batch_id or 'all'}.{format}"
    headers = {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"}
    return Response(content=body, media_type=media_type, headers=headers)
