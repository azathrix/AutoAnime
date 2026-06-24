from __future__ import annotations

from fastapi import APIRouter, Query

from ..db import log
from ..maintenance import (
    cleanup_invalid_episode_data,
    clear_runtime_data,
    migrate_episode_model,
    migrate_media_folders,
    organize_local_files,
    refresh_local_status,
    repair_local_paths,
)
from ..pipeline_orchestrator import run_ready_tasks, start_pipeline
from ..pipeline_runtime import pipeline_overview
from ..runtime_service import (
    cancel_runtime_activity,
    canonical_queue_key,
    queue_handlers,
    run_operation,
    run_progress_operation,
    run_scan_source,
    trigger_queue,
)
from ..runtime_store import runtime_store
from ..schemas import PipelineStartPayload


router = APIRouter()


@router.get("/api/pipelines")
async def api_pipelines() -> list[dict]:
    return pipeline_overview()


@router.post("/api/pipelines/{pipeline_key}/start")
async def api_start_pipeline(pipeline_key: str, payload: PipelineStartPayload) -> dict[str, str]:
    run_id = start_pipeline(
        pipeline_key,
        trigger_source=payload.trigger_source,
        first_step_key=payload.first_step_key,
        subject_type=payload.subject_type,
        subject_id=payload.subject_id,
        payload=payload.payload,
        message=payload.message,
    )
    if run_id <= 0:
        return {"status": "invalid", "message": "流水线或起始步骤不存在"}
    return {"status": "started", "run_id": str(run_id), "message": "流水线已启动"}


@router.post("/api/processors/tasks/run")
async def api_run_runtime_processor(limit: int = Query(20, ge=1, le=200), processor_key: str = "") -> dict[str, str]:
    processed = await run_ready_tasks(limit=limit, processor_key=processor_key.strip())
    return {"status": "completed", "count": str(processed), "message": f"已处理 processor task: {processed} 个"}


@router.post("/api/scan")
async def api_scan() -> dict[str, str]:
    from ..db import get_settings

    running = next(
        (
            item
            for item in runtime_store.snapshot().get("operations", [])
            if item.get("name") == "扫描全部" and item.get("status") == "running"
        ),
        None,
    )
    if running:
        return {"status": "running", "operation_id": str(running.get("id") or ""), "message": "扫描全部正在执行"}
    operation_id = run_progress_operation(
        "扫描全部",
        lambda op_id: run_scan_source(get_settings(), op_id),
        "正在扫描 RSS 源头，后续队列会自动推进",
    )
    return {"status": "started", "operation_id": str(operation_id), "message": "扫描已启动"}


@router.get("/api/scanner/status")
async def api_scanner_status() -> dict[str, str]:
    operations = runtime_store.snapshot().get("operations", [])
    running = next((item for item in operations if item.get("name") == "扫描全部" and item.get("status") == "running"), None)
    recent = next((item for item in operations if item.get("name") == "扫描全部"), None)
    item = running or recent or {}
    return {
        "status": str(item.get("status") or "idle"),
        "message": str(item.get("message") or ("正在扫描" if running else "空闲")),
        "operation_id": str(item.get("id") or ""),
        "updated_at": str(item.get("updated_at") or ""),
    }


@router.post("/api/scanner/run")
async def api_scanner_run() -> dict[str, str]:
    return await api_scan()


@router.post("/api/queues/{queue_name}/trigger")
async def api_trigger_queue(queue_name: str) -> dict[str, str]:
    requested_name = (queue_name or "").strip()
    if requested_name == "rss":
        return await api_scan()
    name = canonical_queue_key(requested_name)
    reset_count = await runtime_store.retry_queue_tasks("" if name == "processor" else name)
    if name not in queue_handlers:
        trigger_queue("processor", delay=0)
        if reset_count:
            log("info", f"已立即重试队列: queue={requested_name} tasks={reset_count}")
            return {"status": "started", "count": str(reset_count), "message": f"已立即重试该队列: {reset_count} 个等待任务"}
        ready = runtime_store.ready_count(name)
        if ready <= 0:
            return {"status": "idle", "count": "0", "message": "该队列没有到期可执行任务；等待重试任务需要点击重试或等待倒计时结束"}
        return {"status": "started", "count": str(ready), "message": f"已触发该队列: {ready} 个可执行任务"}
    trigger_queue(name, delay=0)
    if reset_count:
        log("info", f"已立即重试队列: queue={requested_name} tasks={reset_count}")
        return {"status": "started", "count": str(reset_count), "message": f"已立即重试该队列: {reset_count} 个等待任务"}
    ready = runtime_store.ready_count()
    if ready <= 0:
        return {"status": "idle", "count": "0", "message": "当前没有到期可执行任务"}
    return {"status": "started", "count": str(ready), "message": f"队列 {requested_name} 已立即触发"}


@router.post("/api/tasks/process")
async def api_process_tasks(force: bool = Query(False)) -> dict[str, str]:
    async def run() -> str:
        trigger_queue("processor", delay=0)
        return "已触发 Runtime 处理器；下载器会负责提交、轮询并整理到本地"

    operation_id = run_operation(
        "任务队列立即处理" if force else "任务队列处理",
        run,
        "正在立即推进任务队列" if force else "正在推进任务队列",
    )
    return {"status": "started", "operation_id": str(operation_id), "message": "任务队列已立即触发" if force else "队列处理已启动"}


@router.post("/api/tasks/poll")
async def api_poll_tasks() -> dict[str, str]:
    async def run() -> str:
        trigger_queue("processor", delay=0)
        return "已触发 Runtime 处理器；等待中的下载状态到期后会继续推进"

    operation_id = run_operation("刷新下载状态", run, "正在刷新下载器任务状态")
    return {"status": "started", "operation_id": str(operation_id), "message": "状态刷新已启动"}


@router.post("/api/tasks/retry-failed")
async def api_retry_failed() -> dict[str, str]:
    total = 0
    for task in runtime_store.tasks.values():
        if task.status in {"failed", "waiting"}:
            task.status = "pending"
            task.attempts = 0
            task.retry_at = ""
            task.error = ""
            from ..db import now

            task.updated_at = now()
            total += 1
    await runtime_store.bump()
    trigger_queue("processor", delay=0)
    log("info", f"已重置 Runtime 失败/等待任务: {total} 个")
    return {"status": "started", "count": str(total), "message": f"失败/等待任务已重新入队: {total} 个"}


@router.post("/api/runtime/retry-failed")
async def api_runtime_retry_failed() -> dict[str, str]:
    return await api_retry_failed()


@router.post("/api/runtime/cancel")
async def api_runtime_cancel() -> dict[str, str]:
    await runtime_store.cancel_all()
    return {"status": "completed", "message": "已取消 Runtime 中的运行和任务"}


@router.post("/api/operations/clear")
async def api_clear_operations() -> dict[str, str]:
    total = runtime_store.clear_finished_operations_sync()
    return {"status": "completed", "count": str(total), "message": "已清空已结束操作"}


@router.post("/api/logs/clear")
async def api_clear_logs() -> dict[str, str]:
    total = await runtime_store.clear_logs()
    return {"status": "cleared", "count": str(total), "message": f"已清理日志: {total} 条"}


@router.post("/api/runtime/logs/clear")
async def api_runtime_clear_logs() -> dict[str, str]:
    return await api_clear_logs()


@router.post("/api/maintenance/migrate-media-folders")
async def api_migrate_media_folders() -> dict:
    return migrate_media_folders()


@router.post("/api/maintenance/cleanup-invalid-episodes")
async def api_cleanup_invalid_episodes() -> dict:
    return cleanup_invalid_episode_data()


@router.post("/api/maintenance/repair-local-paths")
async def api_repair_local_paths() -> dict:
    return repair_local_paths()


@router.post("/api/maintenance/migrate-episode-model")
async def api_migrate_episode_model() -> dict:
    return migrate_episode_model()


@router.post("/api/maintenance/refresh-local-status")
async def api_refresh_all_local_status() -> dict:
    return refresh_local_status()


@router.post("/api/maintenance/organize-local-files")
async def api_organize_all_local_files() -> dict:
    return organize_local_files()


@router.post("/api/system/clear-data")
async def api_clear_data() -> dict[str, str]:
    await cancel_runtime_activity()
    await runtime_store.clear_all()
    clear_runtime_data()
    return {"status": "cleared", "message": "已清除所有运行数据"}
