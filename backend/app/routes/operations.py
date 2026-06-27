from __future__ import annotations

from fastapi import APIRouter, Query

from ..operation_service import clear_recent_operations, list_recent_operations


router = APIRouter()


@router.get("/api/operations/recent")
async def api_recent_operations(limit: int = Query(20)) -> dict:
    return {"items": list_recent_operations(limit)}


@router.post("/api/operations/recent/clear")
async def api_clear_recent_operations() -> dict:
    total = clear_recent_operations()
    return {"status": "cleared", "count": total}
