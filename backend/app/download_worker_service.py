from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from .database import connect
from .db import get_settings, log, now
from .download_task_service import FINAL_DOWNLOAD_STATUSES, canonical_download_status
from .pipeline_models import ProcessorContext, ProcessorResult
from .processors.download import process_download
from .runtime_service import ACTIVE_DOWNLOAD_STATUSES
from .sync_service import task_retry_after


_active_jobs: dict[int, asyncio.Task] = {}
_runner_task: asyncio.Task | None = None


def _parse_time(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _is_due(value: str) -> bool:
    parsed = _parse_time(value)
    if parsed is None:
        return True
    return parsed <= datetime.now(timezone.utc)


def download_concurrency() -> int:
    settings = get_settings()
    try:
        value = int(settings.get("download_concurrency") or 2)
    except (TypeError, ValueError):
        value = 2
    return max(1, min(12, value))


def trigger_download_worker(delay: float = 0) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    global _runner_task
    if _runner_task and not _runner_task.done():
        return

    async def runner() -> None:
        if delay > 0:
            await asyncio.sleep(delay)
        await run_download_worker()

    _runner_task = loop.create_task(runner())


def cancel_download_job_worker(task_id: int) -> bool:
    task = _active_jobs.get(task_id)
    if not task or task.done():
        return False
    task.cancel()
    return True


def cancel_all_download_workers() -> int:
    count = 0
    for task_id, task in list(_active_jobs.items()):
        if task.done():
            continue
        task.cancel()
        count += 1
    return count


async def run_download_worker(limit: int | None = None) -> int:
    capacity = max(1, int(limit or download_concurrency()))
    scheduled = 0
    free_slots = max(0, capacity - len(_active_jobs))
    if free_slots <= 0:
        return 0
    rows = _claimable_jobs(free_slots)
    for row in rows:
        task_id = int(row["id"] or 0)
        if task_id <= 0 or task_id in _active_jobs:
            continue
        task = asyncio.create_task(process_download_job(task_id))
        _active_jobs[task_id] = task
        task.add_done_callback(lambda finished, job_id=task_id: _download_job_done(job_id, finished))
        scheduled += 1
    await asyncio.sleep(0)
    return scheduled


def _download_job_done(task_id: int, task: asyncio.Task) -> None:
    _active_jobs.pop(task_id, None)
    try:
        task.result()
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        log("error", f"下载任务后台执行失败: task_id={task_id} error={str(exc)[:2000]}")
    trigger_download_worker(delay=0)


def _claimable_jobs(limit: int) -> list[Any]:
    placeholders = ",".join("?" for _ in ACTIVE_DOWNLOAD_STATUSES)
    active_ids = set(_active_jobs)
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT *
            FROM download_jobs
            WHERE status IN ({placeholders})
              AND status != 'paused'
            ORDER BY CASE status
              WHEN 'local_copying' THEN 0
              WHEN 'remote_completed' THEN 1
              WHEN 'remote_downloading' THEN 2
              WHEN 'submitting' THEN 3
              WHEN 'pending' THEN 4
              ELSE 5
            END, updated_at ASC, id ASC
            LIMIT ?
            """,
            (*ACTIVE_DOWNLOAD_STATUSES, max(1, int(limit) * 4)),
        ).fetchall()
    result = []
    for row in rows:
        task_id = int(row["id"] or 0)
        if task_id in active_ids:
            continue
        if not _is_due(str(row["retry_after"] or "")):
            continue
        result.append(row)
        if len(result) >= limit:
            break
    return result


def _job_row(task_id: int):
    with connect() as conn:
        return conn.execute("SELECT * FROM download_jobs WHERE id=?", (task_id,)).fetchone()


def _update_job(task_id: int, **fields: Any) -> None:
    if not fields:
        return
    assignments = ", ".join(f"{key}=?" for key in fields)
    values = list(fields.values())
    values.append(task_id)
    with connect() as conn:
        conn.execute(f"UPDATE download_jobs SET {assignments} WHERE id=?", values)


def _mark_retryable(task_id: int, row, result: ProcessorResult) -> None:
    message = (result.message or "等待后重试")[:500]
    current_status = canonical_download_status(str(row["status"] or "pending"))
    next_status = current_status
    if "远端" in message or "云存储" in message or "下载器" in message:
        next_status = "remote_downloading"
    retry_after = result.retry_after or task_retry_after(get_settings(), int(row["attempts"] or 0) + 1)
    _update_job(
        task_id,
        status=next_status,
        phase=next_status,
        retry_after=retry_after,
        progress=0,
        progress_text="-",
        last_error=message,
        updated_at=now(),
        last_seen_at=now(),
    )


def _mark_terminal(task_id: int, message: str) -> None:
    ts = now()
    _update_job(
        task_id,
        status="failed",
        phase="failed",
        retry_after="",
        progress=0,
        progress_text=(message or "下载失败")[:500],
        last_error=(message or "下载失败")[:2000],
        updated_at=ts,
        last_seen_at=ts,
    )


def _mark_completed_if_skipped(task_id: int, message: str) -> None:
    if "本地文件已存在" not in message:
        return
    ts = now()
    _update_job(
        task_id,
        status="completed",
        phase="completed",
        retry_after="",
        progress=100,
        progress_text="可观看",
        last_error="",
        updated_at=ts,
        last_seen_at=ts,
    )


async def process_download_job(task_id: int) -> ProcessorResult:
    row = _job_row(task_id)
    if not row:
        return ProcessorResult.skipped("下载任务不存在")
    status = canonical_download_status(str(row["status"] or "pending"))
    if status in FINAL_DOWNLOAD_STATUSES or status == "paused":
        return ProcessorResult.skipped("下载任务已结束")
    if not _is_due(str(row["retry_after"] or "")):
        return ProcessorResult.skipped("下载任务尚未到重试时间")
    release_id = int(row["release_id"] or 0)
    if release_id <= 0:
        _mark_terminal(task_id, "下载任务缺少 release_id")
        return ProcessorResult.terminal("下载任务缺少 release_id")
    ts = now()
    attempts = int(row["attempts"] or 0) + 1
    _update_job(task_id, attempts=attempts, updated_at=ts, last_seen_at=ts)
    context = ProcessorContext(
        task_id=0,
        pipeline_id=0,
        run_id=0,
        step_id=0,
        step_key="download",
        processor_key="download",
        domain_kind=str(row["media_type"] or "library"),
        subject_type="release",
        subject_id=release_id,
        attempts=attempts,
    )
    payload = {
        "entry_id": int(row["entry_id"] or 0),
        "release_id": release_id,
        "episode_number": int(row["episode_number"] or 0),
        "domain_kind": str(row["media_type"] or "library"),
    }
    try:
        result = await process_download(context, payload)
    except Exception as exc:
        message = str(exc)[:2000]
        log("error", f"下载任务执行异常: task_id={task_id} release_id={release_id} error={message}")
        _mark_terminal(task_id, message)
        return ProcessorResult.terminal(message)

    latest = _job_row(task_id) or row
    if result.status == "failed_retryable":
        _mark_retryable(task_id, latest, result)
    elif result.status in {"failed_terminal", "conflict"}:
        _mark_terminal(task_id, result.message)
    elif result.status == "skipped":
        _mark_completed_if_skipped(task_id, result.message)
    return result
