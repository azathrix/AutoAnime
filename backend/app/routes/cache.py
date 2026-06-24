from __future__ import annotations

from ..db import log
from ..processing_cache import clear_expired_processing_cache, clear_processing_cache, clear_processing_cache_type

from fastapi import APIRouter


router = APIRouter()


@router.post("/api/cache/rss/clear")
async def api_clear_rss_cache() -> dict[str, int | str]:
    total = clear_processing_cache_type("rss_resource_processed")
    log("info", f"RSS 缓存已清理: {total} 条")
    return {"status": "cleared", "count": total, "message": f"RSS 缓存已清理: {total} 条"}


@router.post("/api/cache/expired/clear")
async def api_clear_expired_cache() -> dict[str, int | str]:
    total = clear_expired_processing_cache()
    log("info", f"过期缓存已清理: {total} 条")
    return {"status": "cleared", "count": total, "message": f"过期缓存已清理: {total} 条"}


@router.post("/api/cache/clear")
async def api_clear_all_cache() -> dict[str, int | str]:
    total = clear_processing_cache()
    log("info", f"全部处理缓存已清理: {total} 条")
    return {"status": "cleared", "count": total, "message": f"全部处理缓存已清理: {total} 条"}
