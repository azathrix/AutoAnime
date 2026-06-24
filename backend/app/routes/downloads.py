from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from ..database import connect
from ..db import log, now
from ..download_task_service import download_overview, list_download_tasks, queue_download_for_episode, queue_download_for_release
from ..pipeline_orchestrator import cancel_active_processor_tasks, start_pipeline
from ..runtime_service import DOWNLOAD_RUNTIME_PROCESSORS, trigger_queue
from ..runtime_store import runtime_store


router = APIRouter()


@router.get("/api/download-tasks")
async def api_download_tasks() -> dict[str, Any]:
    tasks = list_download_tasks()
    return {"items": tasks, "overview": download_overview(tasks)}


@router.post("/api/download-tasks/{task_id}/cancel")
async def api_cancel_download_task(task_id: int) -> dict[str, Any]:
    ts = now()
    with connect() as conn:
        row = conn.execute("SELECT * FROM download_jobs WHERE id=?", (task_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="下载任务不存在")
        entry_id = int(row["entry_id"] or 0)
        episode_number = int(row["episode_number"] or 0)
        conn.execute(
            """
            UPDATE download_jobs
            SET status='cancelled', phase='cancelled', retry_after='', last_error='用户取消下载', updated_at=?
            WHERE id=?
            """,
            (ts, task_id),
        )
        conn.execute(
            """
            UPDATE episode_resources
            SET status='cancelled', updated_at=?
            WHERE entry_id=? AND episode_number=? AND selected=1 AND downloaded=0
            """,
            (ts, entry_id, episode_number),
        )
        conn.execute(
            "UPDATE episodes SET status_note='下载任务已取消', updated_at=? WHERE entry_id=? AND episode_number=?",
            (ts, entry_id, episode_number),
        )
    runtime_cancelled = await runtime_store.cancel_episode_tasks(entry_id, episode_number, DOWNLOAD_RUNTIME_PROCESSORS)
    active_cancelled = await cancel_active_processor_tasks(
        DOWNLOAD_RUNTIME_PROCESSORS,
        entry_id=entry_id,
        episode_number=episode_number,
    )
    log("warn", f"下载任务已取消: task_id={task_id} entry_id={entry_id} episode={episode_number}")
    return {
        "status": "cancelled",
        "runtime_cancelled": runtime_cancelled,
        "active_cancelled": active_cancelled,
        "message": "下载任务已取消",
    }


@router.post("/api/download-tasks/{task_id}/retry")
async def api_retry_download_task(task_id: int) -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM download_jobs WHERE id=?", (task_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="下载任务不存在")
        release_id = int(row["release_id"] or 0)
        episode_id = int(row["episode_id"] or 0)
    queued = queue_download_for_episode(episode_id, reset_cancelled=True) if episode_id > 0 else queue_download_for_release(release_id, reset_cancelled=True)
    if not queued.get("queued") and queued.get("reason") != "已有活跃下载任务":
        return {"status": "skipped", "message": str(queued.get("reason") or "无法重试下载任务")}
    if episode_id > 0:
        with connect() as conn:
            refreshed = conn.execute("SELECT release_id FROM episodes WHERE id=?", (episode_id,)).fetchone()
        release_id = int(refreshed["release_id"] or release_id) if refreshed else release_id
    if release_id <= 0:
        return {"status": "skipped", "message": "该任务没有可下载资源"}
    run_id = start_pipeline(
        "library_backfill",
        trigger_source="download_task_retry",
        first_step_key="download",
        subject_type="release",
        subject_id=release_id,
        payload={
            "_dedupe_key": f"download-task:{task_id}",
            "release_id": release_id,
            "entry_id": int((queued.get("task") or {}).get("entry_id") or row["entry_id"] or 0),
            "episode_number": int((queued.get("task") or {}).get("episode_number") or row["episode_number"] or 0),
            "domain_kind": "library",
        },
        message=f"重试下载任务: task_id={task_id}",
    )
    trigger_queue("processor", delay=0)
    return {"status": "started", "download_run_id": run_id, "message": "下载任务已重新入队"}


@router.post("/api/download-tasks/clear-completed")
async def api_clear_completed_download_tasks() -> dict[str, Any]:
    with connect() as conn:
        cursor = conn.execute("DELETE FROM download_jobs WHERE status IN ('completed', 'cancelled')")
        count = cursor.rowcount if cursor.rowcount is not None else 0
    log("info", f"已清除完成/取消下载任务: {count} 条")
    return {"status": "deleted", "count": count, "message": f"已清除 {count} 条完成/取消任务"}


@router.delete("/api/download-tasks/{task_id}")
async def api_delete_download_task(task_id: int) -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM download_jobs WHERE id=?", (task_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="下载任务不存在")
        status = str(row["status"] or "")
        if status in {"pending", "submitting", "remote_downloading", "remote_completed", "local_copying", "running", "submitted", "downloading"}:
            raise HTTPException(status_code=400, detail="下载任务仍在运行，请先取消")
        conn.execute("DELETE FROM download_jobs WHERE id=?", (task_id,))
    return {"status": "deleted", "message": "下载任务已删除"}
