from __future__ import annotations

import json
import time

from fastapi import APIRouter
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse

from ..dashboard_service import cached_dashboard_data, dashboard_cache, dashboard_cache_lock, dashboard_data
from ..runtime_store import runtime_store


router = APIRouter()


@router.get("/api/dashboard")
async def api_dashboard() -> dict:
    return await cached_dashboard_data()


@router.get("/api/dashboard/stream")
async def api_dashboard_stream() -> StreamingResponse:
    async def event_stream():
        version = -1
        while True:
            snapshot = runtime_store.snapshot()
            current_version = int(snapshot.get("version") or 0)
            if version == current_version:
                current_version = await runtime_store.wait_for_change(version, timeout=15.0)
            version = current_version
            data = await run_in_threadpool(dashboard_data)
            async with dashboard_cache_lock:
                dashboard_cache["data"] = data
                dashboard_cache["ts"] = time.monotonic()
            yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
