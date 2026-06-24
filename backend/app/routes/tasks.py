from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from ..task_service import cancel_task, delete_task, list_tasks, pause_task, resume_task, task_overview


router = APIRouter()


@router.get("/api/tasks")
async def api_tasks(type: str = Query("")) -> dict[str, Any]:
    items = list_tasks(type)
    return {"items": items, "overview": task_overview(items)}


@router.post("/api/tasks/{task_id}/cancel")
async def api_cancel_task(task_id: str) -> dict[str, str]:
    if not await cancel_task(task_id):
        raise HTTPException(status_code=404, detail="任务不存在或不可取消")
    return {"status": "cancelled", "message": "任务已取消"}


@router.post("/api/tasks/{task_id}/pause")
async def api_pause_task(task_id: str) -> dict[str, str]:
    if not await pause_task(task_id):
        raise HTTPException(status_code=404, detail="任务不存在或不可暂停")
    return {"status": "paused", "message": "任务已暂停"}


@router.post("/api/tasks/{task_id}/resume")
async def api_resume_task(task_id: str) -> dict[str, str]:
    if not await resume_task(task_id):
        raise HTTPException(status_code=404, detail="任务不存在或不可继续")
    return {"status": "pending", "message": "任务已继续"}


@router.delete("/api/tasks/{task_id}")
async def api_delete_task(task_id: str) -> dict[str, str]:
    if not await delete_task(task_id):
        raise HTTPException(status_code=404, detail="任务不存在或不可清理")
    return {"status": "deleted", "message": "任务已清理"}

