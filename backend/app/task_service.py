from __future__ import annotations

from pathlib import Path
from typing import Any

from .database import connect
from .db import log, now
from .download_task_service import list_download_tasks, queue_download_for_episode, queue_download_for_release
from .runtime_store import runtime_store


TASK_TYPE_LABELS = {
    "rss_scan": "RSS 扫描",
    "metadata": "刷新元数据",
    "download": "下载任务",
    "cache": "缓存清理",
    "local_status": "本地状态",
    "runtime": "后台任务",
}


def runtime_task_type(item: dict[str, Any]) -> str:
    processor = str(item.get("processor_key") or "")
    name = str(item.get("display_title") or item.get("message") or "")
    if processor in {"rss_fetch", "rss_candidate_persist", "mikan_match"} or "扫描" in name:
        return "rss_scan"
    if processor == "metadata":
        return "metadata"
    if processor in {"download", "download_submit", "download_poll", "local_sync"}:
        return "download"
    if "缓存" in name or processor == "cleanup":
        return "cache"
    if "local" in processor or "本地" in name:
        return "local_status"
    return "runtime"


def operation_task_type(item: dict[str, Any]) -> str:
    name = str(item.get("name") or "")
    if "扫描" in name or "RSS" in name:
        return "rss_scan"
    if "元数据" in name:
        return "metadata"
    if "缓存" in name:
        return "cache"
    if "本地" in name:
        return "local_status"
    return "runtime"


def task_status_text(status: str) -> str:
    return {
        "pending": "等待中",
        "running": "运行中",
        "waiting": "等待重试",
        "paused": "已暂停",
        "completed": "已完成",
        "failed": "失败",
        "cancelled": "已取消",
        "skipped": "已跳过",
    }.get(status, status or "-")


def list_tasks(task_type: str = "") -> list[dict[str, Any]]:
    selected_type = str(task_type or "").strip()
    snapshot = runtime_store.snapshot()
    rows: list[dict[str, Any]] = []
    for queue in snapshot.get("queues", []):
        for item in queue.get("items", []):
            item_type = runtime_task_type(item)
            if item_type == "download":
                continue
            if selected_type and item_type != selected_type:
                continue
            payload = item.get("payload") or {}
            rows.append(
                {
                    "id": f"runtime:{item['id']}",
                    "raw_id": item["id"],
                    "type": item_type,
                    "type_name": TASK_TYPE_LABELS.get(item_type, item_type),
                    "title": item.get("display_title") or item.get("release_title") or item.get("progress_text") or item.get("processor_key") or "-",
                    "status": item.get("status") or "",
                    "status_text": task_status_text(str(item.get("status") or "")),
                    "progress": int(item.get("progress") or 0),
                    "message": item.get("progress_text") or item.get("message") or "",
                    "updated_at": item.get("updated_at") or "",
                    "source": "runtime",
                    "entry_id": int(payload.get("entry_id") or item.get("entry_id") or 0),
                    "episode_number": payload.get("episode_number") or item.get("episode_number") or "",
                }
            )
    for item in snapshot.get("operations", []):
        item_type = operation_task_type(item)
        if selected_type and item_type != selected_type:
            continue
        rows.append(
            {
                "id": f"operation:{item['id']}",
                "raw_id": item["id"],
                "type": item_type,
                "type_name": TASK_TYPE_LABELS.get(item_type, item_type),
                "title": item.get("name") or "-",
                "status": item.get("status") or "",
                "status_text": task_status_text(str(item.get("status") or "")),
                "progress": int(item.get("progress") or 0),
                "message": item.get("message") or "",
                "updated_at": item.get("finished_at") or item.get("started_at") or "",
                "source": "operation",
            }
        )
    if not selected_type or selected_type == "download":
        for item in list_download_tasks():
            rows.append(
                {
                    "id": f"download:{item['id']}",
                    "raw_id": item["id"],
                    "type": "download",
                    "type_name": TASK_TYPE_LABELS["download"],
                    "title": f"{item.get('display_title') or '-'} 第{item.get('episode_number') or '-'}集",
                    "status": item.get("status") or "",
                    "status_text": item.get("status_text") or "",
                    "progress": int(item.get("progress") or 0),
                    "message": item.get("progress_text") or "",
                    "updated_at": item.get("updated_at") or "",
                    "source": "download",
                    "entry_id": int(item.get("entry_id") or 0),
                    "episode_number": item.get("episode_number") or "",
                }
            )
        from .resource_package_service import list_resource_package_tasks

        rows.extend(list_resource_package_tasks())
    rows.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
    return rows[:500]


def task_overview(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[str, dict[str, Any]] = {}
    for item in items:
        key = str(item.get("type") or "runtime")
        row = counts.setdefault(
            key,
            {"type": key, "name": TASK_TYPE_LABELS.get(key, key), "total": 0, "running": 0, "failed": 0, "pending": 0},
        )
        row["total"] += 1
        status = str(item.get("status") or "")
        if status == "running":
            row["running"] += 1
        elif status == "failed":
            row["failed"] += 1
        elif status in {"pending", "waiting", "paused"}:
            row["pending"] += 1
    return list(counts.values())


async def cancel_task(task_id: str) -> bool:
    kind, _, raw = task_id.partition(":")
    if kind == "runtime" and raw.isdigit():
        return await runtime_store.cancel_task(int(raw))
    if kind == "operation" and raw.isdigit():
        from .runtime_service import cancel_operation

        return await cancel_operation(int(raw))
    if kind == "download" and raw.isdigit():
        from .download_worker_service import cancel_download_job_worker
        from .pipeline_orchestrator import cancel_active_processor_tasks
        from .runtime_service import DOWNLOAD_RUNTIME_PROCESSORS

        job_id = int(raw)
        cancel_download_job_worker(job_id)
        with connect() as conn:
            row = conn.execute("SELECT * FROM download_jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                return False
            entry_id = int(row["entry_id"] or 0)
            episode_number = int(row["episode_number"] or 0)
            target_local_path = str(row["target_local_path"] or "")
            conn.execute(
                """
                UPDATE download_jobs
                SET status='cancelled', phase='cancelled', last_error='用户取消任务', retry_after='', updated_at=?
                WHERE id=?
                """,
                (now(), job_id),
            )
            conn.execute(
                """
                UPDATE episode_resources
                SET status='cancelled', updated_at=?
                WHERE entry_id=? AND episode_number=? AND selected=1 AND downloaded=0
                """,
                (now(), entry_id, episode_number),
            )
            conn.execute(
                "UPDATE episodes SET status_note='下载任务已取消', updated_at=? WHERE entry_id=? AND episode_number=?",
                (now(), entry_id, episode_number),
            )
        await runtime_store.cancel_episode_tasks(entry_id, episode_number, DOWNLOAD_RUNTIME_PROCESSORS)
        await cancel_active_processor_tasks(
            DOWNLOAD_RUNTIME_PROCESSORS,
            entry_id=entry_id,
            episode_number=episode_number,
        )
        if target_local_path:
            partial_path = Path(target_local_path).with_name(f"{Path(target_local_path).name}.anitrack.part")
            try:
                if partial_path.exists() and partial_path.is_file():
                    partial_path.unlink()
            except OSError as exc:
                log("warn", f"下载临时文件清理失败: task_id={job_id} path={partial_path} error={str(exc)[:500]}")
        log(
            "warn",
            f"下载任务已取消: task_id={job_id} entry_id={entry_id} episode={episode_number}",
        )
        return True
    return False


async def pause_task(task_id: str) -> bool:
    kind, _, raw = task_id.partition(":")
    if kind == "runtime" and raw.isdigit():
        return await runtime_store.pause_task(int(raw))
    if kind == "download" and raw.isdigit():
        with connect() as conn:
            conn.execute(
                "UPDATE download_jobs SET status='paused', phase='paused', progress_text='已暂停', updated_at=? WHERE id=?",
                (now(), int(raw)),
            )
        return True
    return False


async def resume_task(task_id: str) -> bool:
    kind, _, raw = task_id.partition(":")
    if kind == "runtime" and raw.isdigit():
        return await runtime_store.resume_task(int(raw))
    if kind == "download" and raw.isdigit():
        with connect() as conn:
            conn.execute(
                """
                UPDATE download_jobs
                SET status='pending', phase='pending', progress=0, progress_text='排队中', updated_at=?
                WHERE id=? AND status='paused'
                """,
                (now(), int(raw)),
            )
        from .download_worker_service import trigger_download_worker

        trigger_download_worker(delay=0)
        return True
    return False


async def retry_task(task_id: str) -> bool:
    kind, _, raw = task_id.partition(":")
    if kind == "runtime" and raw.isdigit():
        return await runtime_store.retry_task(int(raw))
    if kind == "download" and raw.isdigit():
        from .download_worker_service import trigger_download_worker

        with connect() as conn:
            row = conn.execute("SELECT release_id, episode_id FROM download_jobs WHERE id=?", (int(raw),)).fetchone()
        if not row:
            return False
        release_id = int(row["release_id"] or 0)
        episode_id = int(row["episode_id"] or 0)
        queued = (
            queue_download_for_episode(episode_id, reset_cancelled=True)
            if episode_id > 0
            else queue_download_for_release(release_id, reset_cancelled=True)
        )
        if not queued.get("queued") and queued.get("reason") != "已有活跃下载任务":
            return False
        trigger_download_worker(delay=0)
        return True
    return False


async def delete_task(task_id: str) -> bool:
    kind, _, raw = task_id.partition(":")
    if kind == "runtime" and raw.isdigit():
        return await runtime_store.delete_task(int(raw))
    if kind == "operation" and raw.isdigit():
        return runtime_store.delete_operation_sync(int(raw))
    if kind == "download" and raw.isdigit():
        with connect() as conn:
            conn.execute("DELETE FROM download_jobs WHERE id=? AND status NOT IN ('submitting','remote_downloading','local_copying')", (int(raw),))
        return True
    return False


async def clear_completed_tasks() -> dict[str, int]:
    runtime_count = await runtime_store.clear_completed_tasks()
    operation_count = runtime_store.clear_finished_operations_sync()
    with connect() as conn:
        cursor = conn.execute("DELETE FROM download_jobs WHERE status IN ('completed', 'cancelled')")
        download_count = cursor.rowcount if cursor.rowcount is not None else 0
    total = runtime_count + operation_count + download_count
    log(
        "info",
        f"已清除完成任务: total={total} runtime={runtime_count} operation={operation_count} download={download_count}",
    )
    try:
        from .operation_service import record_operation_event

        record_operation_event("task", "任务记录已清理", f"共清理 {total} 条任务记录")
    except Exception:
        pass
    return {
        "total": total,
        "runtime": runtime_count,
        "operation": operation_count,
        "download": download_count,
    }


async def bulk_task_action(action: str, task_type: str = "") -> dict[str, int]:
    selected = str(task_type or "").strip()
    rows = list_tasks(selected)
    total = 0
    changed = 0
    action_key = str(action or "").strip()

    async def apply(row: dict[str, Any]) -> bool:
        task_id = str(row.get("id") or "")
        status = str(row.get("status") or "")
        if not task_id:
            return False
        if action_key == "cancel":
            if status in {"completed", "failed", "cancelled", "skipped"}:
                return False
            return await cancel_task(task_id)
        if action_key == "pause":
            if status not in {"pending", "running", "waiting", "submitting", "remote_downloading", "remote_completed", "local_copying", "downloading"}:
                return False
            return await pause_task(task_id)
        if action_key == "resume":
            if status != "paused":
                return False
            return await resume_task(task_id)
        if action_key == "retry":
            if status not in {"failed", "cancelled", "waiting", "paused"}:
                return False
            return await retry_task(task_id)
        return False

    for row in rows:
        total += 1
        if await apply(row):
            changed += 1
    try:
        from .operation_service import record_operation_event

        labels = {
            "cancel": "批量取消任务",
            "pause": "批量暂停任务",
            "resume": "批量继续任务",
            "retry": "批量重试任务",
        }
        record_operation_event("task", labels.get(action_key, "批量任务操作"), f"处理 {changed}/{total} 条任务")
    except Exception:
        pass
    return {"total": total, "changed": changed}
