from __future__ import annotations

from typing import Any

from .database import connect
from .db import now
from .download_task_service import list_download_tasks
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
            if selected_type and item_type != selected_type:
                continue
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
                "progress": 0,
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
                }
            )
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
    if kind == "download" and raw.isdigit():
        with connect() as conn:
            conn.execute(
                """
                UPDATE download_jobs
                SET status='cancelled', phase='cancelled', last_error='用户取消任务', retry_after='', updated_at=?
                WHERE id=?
                """,
                (now(), int(raw)),
            )
        return True
    return False


async def pause_task(task_id: str) -> bool:
    kind, _, raw = task_id.partition(":")
    if kind == "runtime" and raw.isdigit():
        return await runtime_store.pause_task(int(raw))
    if kind == "download" and raw.isdigit():
        with connect() as conn:
            conn.execute("UPDATE download_jobs SET phase='paused', progress_text='已暂停', updated_at=? WHERE id=?", (now(), int(raw)))
        return True
    return False


async def resume_task(task_id: str) -> bool:
    kind, _, raw = task_id.partition(":")
    if kind == "runtime" and raw.isdigit():
        return await runtime_store.resume_task(int(raw))
    if kind == "download" and raw.isdigit():
        with connect() as conn:
            conn.execute("UPDATE download_jobs SET phase=status, progress_text='', updated_at=? WHERE id=?", (now(), int(raw)))
        return True
    return False


async def delete_task(task_id: str) -> bool:
    kind, _, raw = task_id.partition(":")
    if kind == "runtime" and raw.isdigit():
        return await runtime_store.delete_task(int(raw))
    if kind == "download" and raw.isdigit():
        with connect() as conn:
            conn.execute("DELETE FROM download_jobs WHERE id=? AND status NOT IN ('submitting','remote_downloading','local_copying')", (int(raw),))
        return True
    return False
