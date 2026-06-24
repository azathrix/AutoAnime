from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import timezone
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .db import get_runtime_generation, get_settings, log, now
from .library import bool_setting
from .pipeline_orchestrator import cancel_active_processor_tasks, run_ready_tasks, start_pipeline
from .queue_bridge import register_queue_trigger
from .runtime_store import runtime_store
from .utils import int_setting

scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")
QUEUE_DEBOUNCE_SECONDS = 10.0
ACTIVE_DOWNLOAD_STATUSES = {
    "pending",
    "submitting",
    "remote_downloading",
    "remote_completed",
    "local_copying",
    "running",
    "submitted",
    "downloading",
}
DOWNLOAD_RUNTIME_PROCESSORS = {
    "download",
    "download_presence",
    "download_submit",
    "download_poll",
    "download_artifact_register",
    "sync_plan",
    "local_sync",
}
QueueHandler = Callable[[], Awaitable[None]]
queue_handlers: dict[str, QueueHandler] = {}
queue_debounce_tasks: dict[str, asyncio.Task] = {}
active_queue_tasks: set[asyncio.Task] = set()
queue_running: set[str] = set()
queue_rerun_requested: set[str] = set()
active_operation_tasks: set[asyncio.Task] = set()
QUEUE_KEY_ALIASES: dict[str, str] = {}

def canonical_queue_key(name: str) -> str:
    return QUEUE_KEY_ALIASES.get(name, name)

def queue_job_key(name: str) -> str:
    return f"{canonical_queue_key(name)}_dispatch"

def ready_count_runtime_processor() -> int:
    return runtime_store.ready_count()

def recoverable_queue_names() -> list[str]:
    if "processor" in queue_running:
        return []
    pending_task = queue_debounce_tasks.get("processor")
    if pending_task and not pending_task.done():
        return []
    if "processor" in queue_rerun_requested:
        return []
    return ["processor"] if runtime_store.ready_count() > 0 else []

async def handle_processor_queue() -> None:
    generation = get_runtime_generation()
    for _ in range(10):
        processed = await run_ready_tasks(limit=10)
        if not runtime_generation_alive(generation):
            return
        if processed:
            log("info", f"Processor 队列已处理: {processed} 个")
        if processed == 0:
            break
        if ready_count_runtime_processor() <= 0:
            break
        await asyncio.sleep(0)
    if ready_count_runtime_processor() > 0:
        trigger_queue("processor", delay=0)

async def run_scan_source(settings: dict[str, str], operation_id: int | None = None) -> str:
    if not settings.get("rss_url"):
        log("warn", "未配置 Mikan RSS")
        return "未配置 Mikan RSS"
    if operation_id:
        runtime_store.update_operation_sync(operation_id, "正在启动 Mikan 新番追更流水线")
    run_id = start_pipeline(
        "seasonal_mikan_tracking",
        trigger_source="manual",
        first_step_key="rss_fetch",
        subject_type="rss_source",
        subject_id=1,
        payload={"rss_url": settings.get("rss_url", ""), "domain_kind": "seasonal"},
        message="手动扫描启动",
    )
    if run_id <= 0:
        raise RuntimeError("Mikan 新番追更流水线启动失败")
    trigger_queue("processor", delay=0)
    message = f"已启动 Mikan 新番追更流水线 run_id={run_id}；后续由 processor 队列自动推进"
    log("info", f"扫描全部: {message}")
    return message

def ensure_queue_handlers() -> None:
    queue_handlers.clear()
    queue_handlers.update(
        {
            "processor": handle_processor_queue,
        }
    )
    register_queue_trigger(trigger_queue)

async def run_queue(name: str) -> None:
    name = canonical_queue_key(name)
    handler = queue_handlers.get(name)
    if not handler:
        return
    if name in queue_running:
        queue_rerun_requested.add(name)
        runtime_store.set_scheduler_sync(queue_job_key(name), last_status="rerun_pending", updated_at=now())
        return
    queue_debounce_tasks.pop(name, None)
    queue_running.add(name)
    run_id = runtime_store.start_scheduler_run_sync(queue_job_key(name), "event", f"执行队列 {name}")
    run_status = "completed"
    run_message = f"队列 {name} 执行完成"
    try:
        await handler()
    except asyncio.CancelledError:
        run_status = "cancelled"
        run_message = f"队列 {name} 已取消"
        log("warn", f"队列已取消[{name}]")
        raise
    except Exception as exc:
        log("error", f"队列处理失败[{name}]: {exc}")
        run_status = "failed"
        run_message = str(exc)
    finally:
        queue_running.discard(name)
        if run_id:
            runtime_store.finish_scheduler_run_sync(run_id, run_status, run_message)
        if run_status != "cancelled" and name in queue_rerun_requested:
            queue_rerun_requested.discard(name)
            trigger_queue(name)

def trigger_queue(name: str, delay: float | None = None) -> None:
    name = canonical_queue_key(name)
    if name not in queue_handlers:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    if name in queue_running:
        queue_rerun_requested.add(name)
        runtime_store.set_scheduler_sync(queue_job_key(name), last_status="rerun_pending", debounce_seconds=int(QUEUE_DEBOUNCE_SECONDS), updated_at=now())
        return
    pending_task = queue_debounce_tasks.get(name)
    if pending_task and not pending_task.done():
        pending_task.cancel()
    actual_delay = QUEUE_DEBOUNCE_SECONDS if delay is None else max(0.0, delay)
    runtime_store.set_scheduler_sync(
        queue_job_key(name),
        last_status="debouncing" if actual_delay > 0 else "queued",
        debounce_seconds=int(actual_delay),
        updated_at=now(),
    )

    async def runner() -> None:
        try:
            if actual_delay > 0:
                await asyncio.sleep(actual_delay)
            await run_queue(name)
        except asyncio.CancelledError:
            runtime_store.set_scheduler_sync(queue_job_key(name), last_status="debouncing", updated_at=now())
            return

    task = loop.create_task(runner())
    queue_debounce_tasks[name] = task
    active_queue_tasks.add(task)
    task.add_done_callback(lambda finished: active_queue_tasks.discard(finished))

def trigger_queues(names: list[str], delay: float | None = None) -> None:
    seen: set[str] = set()
    for name in names:
        name = canonical_queue_key(name)
        if name in seen:
            continue
        seen.add(name)
        trigger_queue(name, delay=delay)

async def cancel_runtime_activity() -> None:
    await runtime_store.cancel_all()
    await cancel_active_processor_tasks()
    for task in list(queue_debounce_tasks.values()):
        if task and not task.done():
            task.cancel()
    queue_debounce_tasks.clear()
    for task in list(active_queue_tasks):
        if task and not task.done():
            task.cancel()
    if active_queue_tasks:
        await asyncio.gather(*list(active_queue_tasks), return_exceptions=True)
    active_queue_tasks.clear()
    queue_running.clear()
    queue_rerun_requested.clear()

    for task in list(active_operation_tasks):
        if task and not task.done():
            task.cancel()

    if active_operation_tasks:
        await asyncio.gather(*list(active_operation_tasks), return_exceptions=True)
    active_operation_tasks.clear()

async def dispatch_ready_queues() -> None:
    run_id = runtime_store.start_scheduler_run_sync("queue_dispatch", "system", "恢复挂起队列")
    try:
        names = recoverable_queue_names()
        trigger_queues(names, delay=0)
        runtime_store.finish_scheduler_run_sync(run_id, "completed", f"已恢复触发队列: {', '.join(names) if names else '无'}")
    except Exception as exc:
        runtime_store.finish_scheduler_run_sync(run_id, "failed", str(exc))
        raise

def reschedule() -> None:
    scheduler.remove_all_jobs()
    ensure_queue_handlers()
    settings = get_settings()
    minutes = int_setting(settings.get("scan_interval_minutes"), 60, 1)
    rss_enabled = bool_setting(settings.get("auto_scan", "false"))
    runtime_store.set_scheduler_sync(
        "rss_scan",
        interval_minutes=minutes,
        enabled=int(rss_enabled),
        updated_at=now(),
    )
    runtime_store.set_scheduler_sync(
        "queue_dispatch",
        interval_minutes=0,
        enabled=0,
        debounce_seconds=int(QUEUE_DEBOUNCE_SECONDS),
        updated_at=now(),
    )
    for name in queue_handlers:
        runtime_store.set_scheduler_sync(queue_job_key(name), debounce_seconds=int(QUEUE_DEBOUNCE_SECONDS), updated_at=now())
    if rss_enabled:
        scheduler.add_job(lambda: asyncio.create_task(scheduled_scan()), "interval", minutes=minutes, id="rss_scan")

def runtime_generation_alive(expected: str) -> bool:
    return get_runtime_generation() == expected

async def scheduled_scan() -> None:
    run_id = runtime_store.start_scheduler_run_sync("rss_scan", "system", "定时 RSS 扫描")
    settings = get_settings()
    try:
        if bool_setting(settings.get("auto_scan", "false")):
            message = await run_scan_source(settings)
            runtime_store.finish_scheduler_run_sync(run_id, "completed", message)
        else:
            runtime_store.finish_scheduler_run_sync(run_id, "completed", "已关闭自动 RSS 扫描")
    except Exception as exc:
        runtime_store.finish_scheduler_run_sync(run_id, "failed", str(exc))
        raise

def run_operation(name: str, coro_factory, start_message: str = "") -> int:
    operation_id = runtime_store.start_operation_sync(name, start_message)
    log("info", f"{name} 已启动: {start_message or '处理中'}")

    async def runner() -> None:
        try:
            message = await coro_factory()
        except asyncio.CancelledError:
            runtime_store.finish_operation_sync(operation_id, "cancelled", "操作已取消")
            return
        except Exception as exc:
            runtime_store.finish_operation_sync(operation_id, "failed", str(exc))
            log("error", f"{name} 失败: {exc}")
            return
        runtime_store.finish_operation_sync(operation_id, "completed", str(message or "完成"))
        log("info", f"{name} 完成: {message or '完成'}")

    task = asyncio.create_task(runner())
    active_operation_tasks.add(task)
    task.add_done_callback(lambda finished: active_operation_tasks.discard(finished))
    return operation_id

def run_progress_operation(name: str, coro_factory, start_message: str = "") -> int:
    operation_id = runtime_store.start_operation_sync(name, start_message)
    log("info", f"{name} 已启动: {start_message or '处理中'}")

    async def runner() -> None:
        try:
            message = await coro_factory(operation_id)
        except asyncio.CancelledError:
            runtime_store.finish_operation_sync(operation_id, "cancelled", "操作已取消")
            return
        except Exception as exc:
            runtime_store.finish_operation_sync(operation_id, "failed", str(exc))
            log("error", f"{name} 失败: {exc}")
            return
        runtime_store.finish_operation_sync(operation_id, "completed", str(message or "完成"))
        log("info", f"{name} 完成: {message or '完成'}")

    task = asyncio.create_task(runner())
    active_operation_tasks.add(task)
    task.add_done_callback(lambda finished: active_operation_tasks.discard(finished))
    return operation_id
