from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import APP_DIR
from .db import LOG_PATH, cleanup_operations, clear_runtime_data, connect, diagnostics, finish_operation, get_runtime_generation, get_settings, init_db, log, merge_duplicate_series, now, read_server_logs, save_settings, start_operation, update_operation
from .library import bool_setting
from .metadata import generate_nfo_for_series, refresh_series_metadata
from .scanner import enqueue_backfill_task, enqueue_missing_mikan_match_tasks, enqueue_selection_task, mark_selected_releases, poll_submitted_tasks, process_backfill_tasks, process_metadata_tasks, process_mikan_match_tasks, process_selection_tasks, process_tasks, queue_release, reclaim_mikan_match_tasks, repair_series_mikan_ids, resolve_series_choice, scan_and_queue
from .sync_service import backfill_cloud_assets_from_completed_tasks, cancel_sync_for_series, process_cloud_asset_tasks, process_sync_tasks, queue_sync_for_series, reconcile_rclone_submitted_tasks, reconcile_sync_intents, scan_cloud_library


scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")
QUEUE_DEBOUNCE_SECONDS = 10.0
QueueHandler = Callable[[], Awaitable[None]]
queue_handlers: dict[str, QueueHandler] = {}
queue_debounce_tasks: dict[str, asyncio.Task] = {}
queue_running: set[str] = set()
queue_rerun_requested: set[str] = set()


class SettingsPayload(BaseModel):
    rss_url: str = ""
    rss_proxy: str = ""
    scan_interval_minutes: int = 60
    auto_scan: bool = False
    auto_download_unique: bool = True
    auto_download_by_priority: bool = True
    default_backfill: str = "none"
    subtitle_priority: list[str] = []
    resolution_priority: list[str] = []
    language_priority: list[str] = []
    secondary_language_priority: list[str] = []
    cloud_transfer_backend: str = "rclone"
    rclone_command: str = "rclone"
    rclone_config_path: str = "/data/rclone/rclone.conf"
    rclone_remote: str = "pikpak"
    pikpak_auth_mode: str = "token"
    pikpak_username: str = ""
    pikpak_password: str = ""
    pikpak_access_token: str = ""
    pikpak_refresh_token: str = ""
    pikpak_proxy: str = ""
    library_root: str = "/Anime"
    local_library_root: str = "/media/pikpak-anime"
    auto_sync_following: bool = True
    nfo_output_root: str = ""
    series_dir_template: str = ""
    season_dir_template: str = ""
    episode_name_template: str = ""


class SeriesPayload(BaseModel):
    title_cn: str = ""
    bangumi_id: str = ""
    tmdb_id: str = ""
    year: int = 0
    season_number: int = 1
    auto_download: str = "inherit"
    selected_group: str = ""
    selected_resolution: str = ""
    backfill_mode: str = "inherit"


def row_to_dict(row: Any) -> dict[str, Any]:
    return dict(row) if row is not None else {}


def rows_to_dicts(rows: list[Any]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def seconds_until(value: str) -> int:
    if not value:
        return 0
    try:
        target = datetime.fromisoformat(value)
    except ValueError:
        return 0
    if target.tzinfo is None:
        target = target.replace(tzinfo=timezone.utc)
    return max(0, int((target - datetime.now(timezone.utc)).total_seconds()))


def enrich_download_tasks(rows: list[Any]) -> list[dict[str, Any]]:
    result = rows_to_dicts(rows)
    for row in result:
        retry_seconds = seconds_until(str(row.get("retry_after") or ""))
        row["retry_seconds"] = retry_seconds
        row["waiting_retry"] = row.get("status") == "pending" and retry_seconds > 0
    return result


def enrich_retry_rows(rows: list[Any]) -> list[dict[str, Any]]:
    result = rows_to_dicts(rows)
    for row in result:
        retry_seconds = seconds_until(str(row.get("retry_after") or ""))
        row["retry_seconds"] = retry_seconds
        row["waiting_retry"] = row.get("status") == "pending" and retry_seconds > 0
    return result


def split_setting(value: str) -> list[str]:
    return [x.strip() for x in (value or "").splitlines() if x.strip()]


def settings_response() -> dict[str, Any]:
    settings = get_settings()
    result: dict[str, Any] = dict(settings)
    result["scan_interval_minutes"] = int(settings.get("scan_interval_minutes") or 60)
    result["auto_scan"] = bool_setting(settings.get("auto_scan", "false"))
    result["auto_download_unique"] = bool_setting(settings.get("auto_download_unique", "true"))
    result["auto_download_by_priority"] = bool_setting(settings.get("auto_download_by_priority", "true"))
    result["auto_sync_following"] = bool_setting(settings.get("auto_sync_following", "true"))
    result["subtitle_priority"] = split_setting(settings.get("subtitle_priority", ""))
    result["resolution_priority"] = split_setting(settings.get("resolution_priority", ""))
    result["language_priority"] = split_setting(settings.get("language_priority", ""))
    result["secondary_language_priority"] = split_setting(settings.get("secondary_language_priority", ""))
    return result


def count_ready(query: str, params: tuple[Any, ...] = ()) -> int:
    with connect() as conn:
        row = conn.execute(query, params).fetchone()
    return int(row["count"] or 0) if row else 0


def ready_count_mikan_match() -> int:
    ts = now()
    with connect() as conn:
        pending_tasks = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM mikan_match_tasks
            WHERE status IN ('pending', 'failed')
              AND (retry_after='' OR retry_after <= ?)
            """,
            (ts,),
        ).fetchone()["count"]
        missing_tasks = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM rss_candidates rc
            LEFT JOIN mikan_match_tasks mt ON mt.candidate_id=rc.id
            WHERE rc.page_url != ''
              AND (
                    rc.bangumi_id = ''
                 OR rc.mikan_bangumi_id = ''
                 OR mt.id IS NULL
                 OR mt.mikan_bangumi_id = ''
                 OR mt.bangumi_id = ''
                 OR mt.status IN ('failed', 'pending')
              )
            """,
        ).fetchone()["count"]
    return int(pending_tasks or 0) + int(missing_tasks or 0)


def ready_count_metadata() -> int:
    return count_ready(
        """
        SELECT COUNT(*) AS count
        FROM metadata_tasks
        WHERE status IN ('pending', 'failed')
          AND (retry_after='' OR retry_after <= ?)
        """,
        (now(),),
    )


def ready_count_selection() -> int:
    return count_ready(
        """
        SELECT COUNT(*) AS count
        FROM selection_tasks
        WHERE status IN ('pending', 'failed')
          AND (retry_after='' OR retry_after <= ?)
        """,
        (now(),),
    )


def ready_count_backfill() -> int:
    return count_ready(
        """
        SELECT COUNT(*) AS count
        FROM backfill_tasks
        WHERE status IN ('pending', 'failed')
          AND (retry_after='' OR retry_after <= ?)
        """,
        (now(),),
    )


def ready_count_download() -> int:
    return count_ready(
        """
        SELECT COUNT(*) AS count
        FROM download_tasks
        WHERE status IN ('pending', 'failed')
          AND (retry_after='' OR retry_after <= ?)
        """,
        (now(),),
    )


def ready_count_cloud_poll() -> int:
    ts = now()
    with connect() as conn:
        task_rows = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM cloud_poll_tasks
            WHERE status IN ('pending', 'failed')
              AND (retry_after='' OR retry_after <= ?)
            """,
            (ts,),
        ).fetchone()["count"]
        submitted_rows = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM download_tasks dt
            JOIN cloud_submissions cs ON cs.download_task_id=dt.id
            JOIN series s ON s.id=dt.series_id
            WHERE dt.status='submitted'
              AND s.bangumi_id != ''
              AND cs.provider='pikpak'
              AND cs.status='submitted'
              AND (dt.retry_after='' OR dt.retry_after <= ?)
            """,
            (ts,),
        ).fetchone()["count"]
    return int(task_rows or 0) + int(submitted_rows or 0)


def ready_count_cloud_asset() -> int:
    ts = now()
    with connect() as conn:
        task_rows = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM cloud_asset_tasks
            WHERE status IN ('pending', 'failed')
              AND (retry_after='' OR retry_after <= ?)
            """,
            (ts,),
        ).fetchone()["count"]
        completed_rows = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM download_tasks dt
            JOIN cloud_submissions cs ON cs.download_task_id=dt.id
            LEFT JOIN cloud_assets ca ON ca.task_id=dt.id
            WHERE dt.status='completed'
              AND dt.pikpak_file_id != ''
              AND cs.provider='pikpak'
              AND cs.status='completed'
              AND ca.id IS NULL
            """,
        ).fetchone()["count"]
    return int(task_rows or 0) + int(completed_rows or 0)


def ready_count_sync_plan() -> int:
    auto_sync = bool_setting(get_settings().get("auto_sync_following", "true"))
    with connect() as conn:
        row = conn.execute(
            """
            SELECT COUNT(DISTINCT ca.series_id) AS count
            FROM cloud_assets ca
            JOIN series s ON s.id=ca.series_id
            LEFT JOIN sync_rules sr ON sr.series_id=ca.series_id
            WHERE ca.status='available'
              AND COALESCE(s.hidden, 0)=0
              AND s.bangumi_id != ''
              AND (COALESCE(sr.sync_enabled, 0)=1 OR ?=1)
            """,
            (1 if auto_sync else 0,),
        ).fetchone()
    return int(row["count"] or 0) if row else 0


def ready_count_sync() -> int:
    return count_ready(
        """
        SELECT COUNT(*) AS count
        FROM sync_tasks
        WHERE status IN ('pending', 'failed')
          AND (retry_after='' OR retry_after <= ?)
        """,
        (now(),),
    )


def ready_queue_names() -> list[str]:
    checks = [
        ("mikan_match", ready_count_mikan_match),
        ("metadata", ready_count_metadata),
        ("selection", ready_count_selection),
        ("backfill", ready_count_backfill),
        ("download", ready_count_download),
        ("cloud_poll", ready_count_cloud_poll),
        ("cloud_asset", ready_count_cloud_asset),
        ("sync_plan", ready_count_sync_plan),
        ("sync", ready_count_sync),
    ]
    return [name for name, fn in checks if fn() > 0]


async def handle_mikan_match_queue() -> None:
    generation = get_runtime_generation()
    for _ in range(12):
        repaired = enqueue_missing_mikan_match_tasks(now())
        done, failed = await process_mikan_match_tasks(get_settings())
        repair_series_mikan_ids(now())
        if not runtime_generation_alive(generation):
            return
        if repaired == 0 and done == 0 and failed == 0:
            break
        if ready_count_mikan_match() <= 0:
            break
    if ready_count_metadata() > 0:
        trigger_queue("metadata")
    if ready_count_mikan_match() > 0:
        trigger_queue("mikan_match")


async def handle_metadata_queue() -> None:
    generation = get_runtime_generation()
    for _ in range(12):
        done, failed = await process_metadata_tasks(get_settings())
        if not runtime_generation_alive(generation):
            return
        if done == 0 and failed == 0:
            break
        if ready_count_metadata() <= 0:
            break
    if ready_count_selection() > 0:
        trigger_queue("selection")
    if ready_count_backfill() > 0:
        trigger_queue("backfill")
    if ready_count_metadata() > 0:
        trigger_queue("metadata")


async def handle_selection_queue() -> None:
    generation = get_runtime_generation()
    for _ in range(12):
        done, failed = await process_selection_tasks(get_settings())
        if not runtime_generation_alive(generation):
            return
        if done == 0 and failed == 0:
            break
        if ready_count_selection() <= 0:
            break
    if ready_count_download() > 0:
        trigger_queue("download")
    if ready_count_selection() > 0:
        trigger_queue("selection")


async def handle_backfill_queue() -> None:
    generation = get_runtime_generation()
    for _ in range(12):
        done, failed = await process_backfill_tasks(get_settings())
        if not runtime_generation_alive(generation):
            return
        if done == 0 and failed == 0:
            break
        if ready_count_backfill() <= 0:
            break
    if ready_count_mikan_match() > 0:
        trigger_queue("mikan_match")
    if ready_count_backfill() > 0:
        trigger_queue("backfill")


async def handle_download_queue() -> None:
    generation = get_runtime_generation()
    for _ in range(12):
        before = ready_count_download()
        await process_tasks(get_settings())
        if not runtime_generation_alive(generation):
            return
        after = ready_count_download()
        if before == 0 or after >= before:
            break
        if after <= 0:
            break
    if ready_count_cloud_poll() > 0:
        trigger_queue("cloud_poll")
    if ready_count_cloud_asset() > 0:
        trigger_queue("cloud_asset")
    if ready_count_download() > 0:
        trigger_queue("download")


async def handle_cloud_poll_queue() -> None:
    generation = get_runtime_generation()
    for _ in range(12):
        before = ready_count_cloud_poll()
        settings = get_settings()
        await reconcile_rclone_submitted_tasks(settings)
        await poll_submitted_tasks(settings)
        if not runtime_generation_alive(generation):
            return
        after = ready_count_cloud_poll()
        if before == 0 or after >= before:
            break
        if after <= 0:
            break
    if ready_count_cloud_asset() > 0:
        trigger_queue("cloud_asset")
    if ready_count_cloud_poll() > 0:
        trigger_queue("cloud_poll")


async def handle_cloud_asset_queue() -> None:
    generation = get_runtime_generation()
    for _ in range(12):
        before = ready_count_cloud_asset()
        settings = get_settings()
        await process_cloud_asset_tasks(settings)
        backfill_cloud_assets_from_completed_tasks(settings)
        if not runtime_generation_alive(generation):
            return
        after = ready_count_cloud_asset()
        if before == 0 or after >= before:
            break
        if after <= 0:
            break
    trigger_queue("sync_plan")
    if ready_count_cloud_asset() > 0:
        trigger_queue("cloud_asset")


async def handle_sync_plan_queue() -> None:
    settings = get_settings()
    reconciled, queued = reconcile_sync_intents(settings)
    if queued:
        log("info", f"同步计划已更新: {reconciled} 部番剧，新增同步任务 {queued} 个")
        trigger_queue("sync")
    if ready_count_sync_plan() > 0:
        trigger_queue("sync_plan")


async def handle_sync_queue() -> None:
    generation = get_runtime_generation()
    for _ in range(12):
        before = ready_count_sync()
        await process_sync_tasks(get_settings())
        if not runtime_generation_alive(generation):
            return
        after = ready_count_sync()
        if before == 0 or after >= before:
            break
        if after <= 0:
            break
    if ready_count_sync() > 0:
        trigger_queue("sync")


def ensure_queue_handlers() -> None:
    queue_handlers.clear()
    queue_handlers.update(
        {
            "mikan_match": handle_mikan_match_queue,
            "metadata": handle_metadata_queue,
            "selection": handle_selection_queue,
            "backfill": handle_backfill_queue,
            "download": handle_download_queue,
            "cloud_poll": handle_cloud_poll_queue,
            "cloud_asset": handle_cloud_asset_queue,
            "sync_plan": handle_sync_plan_queue,
            "sync": handle_sync_queue,
        }
    )


async def run_queue(name: str) -> None:
    handler = queue_handlers.get(name)
    if not handler:
        return
    if name in queue_running:
        queue_rerun_requested.add(name)
        return
    queue_debounce_tasks.pop(name, None)
    queue_running.add(name)
    try:
        await handler()
    except Exception as exc:
        log("error", f"队列处理失败[{name}]: {exc}")
    finally:
        queue_running.discard(name)
        if name in queue_rerun_requested:
            queue_rerun_requested.discard(name)
            trigger_queue(name)


def trigger_queue(name: str, delay: float | None = None) -> None:
    if name not in queue_handlers:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    if name in queue_running:
        queue_rerun_requested.add(name)
        return
    pending_task = queue_debounce_tasks.get(name)
    if pending_task and not pending_task.done():
        pending_task.cancel()
    actual_delay = QUEUE_DEBOUNCE_SECONDS if delay is None else max(0.0, delay)

    async def runner() -> None:
        try:
            if actual_delay > 0:
                await asyncio.sleep(actual_delay)
            await run_queue(name)
        except asyncio.CancelledError:
            return

    queue_debounce_tasks[name] = loop.create_task(runner())


def trigger_queues(names: list[str], delay: float | None = None) -> None:
    seen: set[str] = set()
    for name in names:
        if name in seen:
            continue
        seen.add(name)
        trigger_queue(name, delay=delay)


async def dispatch_ready_queues() -> None:
    trigger_queues(ready_queue_names(), delay=0)


def reschedule() -> None:
    scheduler.remove_all_jobs()
    ensure_queue_handlers()
    settings = get_settings()
    minutes = max(1, int(settings.get("scan_interval_minutes") or 60))
    scheduler.add_job(lambda: asyncio.create_task(scheduled_scan()), "interval", minutes=minutes, id="rss_scan")
    scheduler.add_job(lambda: asyncio.create_task(dispatch_ready_queues()), "interval", minutes=1, id="queue_dispatch")


def runtime_generation_alive(expected: str) -> bool:
    return get_runtime_generation() == expected


async def scheduled_scan() -> None:
    settings = get_settings()
    if bool_setting(settings.get("auto_scan", "false")):
        await scan_and_queue(settings)
    trigger_queue("mikan_match", delay=0)


async def run_full_refresh(settings: dict[str, str], operation_id: int | None = None) -> str:
    generation = get_runtime_generation()
    with connect() as conn:
        for table in ["mikan_match_tasks", "metadata_tasks", "selection_tasks", "backfill_tasks", "download_tasks", "cloud_poll_tasks", "cloud_asset_tasks", "sync_tasks"]:
            conn.execute(f"UPDATE {table} SET retry_after='', updated_at=?", (now(),))
    reclaimed_mikan = reclaim_mikan_match_tasks(now())
    if operation_id:
        update_operation(operation_id, "1/8 正在扫描 RSS")
    log("info", "扫描全部: 1/8 开始扫描 RSS")
    scan_message = await scan_and_queue(settings)
    repaired_mikan = enqueue_missing_mikan_match_tasks(now())
    log("info", f"扫描全部: RSS 完成，{scan_message}；回收 Mikan 运行中任务 {reclaimed_mikan} 个；补排 Mikan 匹配 {repaired_mikan} 个")
    if not runtime_generation_alive(generation):
        return "运行数据已重置，本次扫描已中止"
    if operation_id:
        update_operation(operation_id, "2/8 正在匹配 Mikan Bangumi")
    log("info", "扫描全部: 2/8 开始匹配 Mikan Bangumi")
    mikan_done, mikan_failed = await process_mikan_match_tasks(settings)
    repaired_series_mikan = repair_series_mikan_ids(now())
    log("info", f"扫描全部: Mikan 匹配完成，成功 {mikan_done} 个，失败 {mikan_failed} 个；回填番剧 Mikan ID {repaired_series_mikan} 个")
    if not runtime_generation_alive(generation):
        return "运行数据已重置，本次扫描已中止"
    if operation_id:
        update_operation(operation_id, "3/10 正在处理元数据队列")
    log("info", "扫描全部: 3/10 开始处理元数据队列")
    metadata_done, metadata_failed = await process_metadata_tasks(settings)
    log("info", f"扫描全部: 元数据完成，成功 {metadata_done} 个，失败 {metadata_failed} 个")
    if not runtime_generation_alive(generation):
        return "运行数据已重置，本次扫描已中止"
    if operation_id:
        update_operation(operation_id, "4/10 正在处理自动选集/补全")
    log("info", "扫描全部: 4/10 开始处理自动选集和整季补全")
    selection_done, selection_failed = await process_selection_tasks(settings)
    backfill_done, backfill_failed = await process_backfill_tasks(settings)
    log("info", f"扫描全部: 选集完成，成功 {selection_done} 个，失败 {selection_failed} 个；补全完成，成功 {backfill_done} 个，失败 {backfill_failed} 个")
    if not runtime_generation_alive(generation):
        return "运行数据已重置，本次扫描已中止"

    if operation_id:
        update_operation(operation_id, "5/10 正在处理补全后新增的 Mikan/元数据")
    log("info", "扫描全部: 5/10 开始处理补全后新增的 Mikan/元数据")
    replay_repaired_mikan = enqueue_missing_mikan_match_tasks(now())
    replay_mikan_done, replay_mikan_failed = await process_mikan_match_tasks(settings)
    replay_series_mikan = repair_series_mikan_ids(now())
    replay_metadata_done, replay_metadata_failed = await process_metadata_tasks(settings)
    log(
        "info",
        f"扫描全部: 补全回灌完成，补排 Mikan {replay_repaired_mikan} 个；"
        f"Mikan 成功 {replay_mikan_done} 个，失败 {replay_mikan_failed} 个；"
        f"回填番剧 Mikan ID {replay_series_mikan} 个；"
        f"元数据成功 {replay_metadata_done} 个，失败 {replay_metadata_failed} 个",
    )
    if not runtime_generation_alive(generation):
        return "运行数据已重置，本次扫描已中止"

    if operation_id:
        update_operation(operation_id, "6/10 正在重跑自动选集")
    log("info", "扫描全部: 6/10 开始重跑自动选集")
    replay_selection_done, replay_selection_failed = await process_selection_tasks(settings)
    log("info", f"扫描全部: 重跑选集完成，成功 {replay_selection_done} 个，失败 {replay_selection_failed} 个")
    if not runtime_generation_alive(generation):
        return "运行数据已重置，本次扫描已中止"
    if operation_id:
        update_operation(operation_id, "7/10 正在处理 PikPak 入库队列")
    log("info", "扫描全部: 7/10 开始处理 PikPak 入库队列")
    await process_tasks(settings)
    if not runtime_generation_alive(generation):
        return "运行数据已重置，本次扫描已中止"
    if operation_id:
        update_operation(operation_id, "8/10 正在刷新 PikPak 任务状态")
    log("info", "扫描全部: 8/10 开始刷新 PikPak 任务状态")
    rclone_done, rclone_missing = await reconcile_rclone_submitted_tasks(settings)
    poll_done, poll_failed = await poll_submitted_tasks(settings)
    log("info", f"扫描全部: 云盘状态刷新完成，rclone 已完成 {rclone_done} 个，未发现 {rclone_missing} 个；PikPak 完成 {poll_done} 个，失败 {poll_failed} 个")
    if not runtime_generation_alive(generation):
        return "运行数据已重置，本次扫描已中止"
    if operation_id:
        update_operation(operation_id, "9/10 正在登记云盘资源")
    log("info", "扫描全部: 9/10 开始登记云盘资源")
    cloud_done, cloud_failed = await process_cloud_asset_tasks(settings)
    cloud_count = backfill_cloud_assets_from_completed_tasks(settings)
    log("info", f"扫描全部: 云盘资源登记完成，登记 {cloud_done} 个，失败 {cloud_failed} 个，补齐 {cloud_count} 个")
    if not runtime_generation_alive(generation):
        return "运行数据已重置，本次扫描已中止"
    if operation_id:
        update_operation(operation_id, "10/10 正在调和本地同步")
    log("info", "扫描全部: 10/10 开始调和本地同步")
    reconciled, queued = reconcile_sync_intents(settings)
    if queued:
        if operation_id:
            update_operation(operation_id, "10/10 正在同步到本地")
        log("info", f"扫描全部: 本地同步排队 {queued} 个，开始执行同步")
        await process_sync_tasks(settings)
    trigger_queues(ready_queue_names(), delay=0)
    return f"{scan_message}；回收 Mikan 运行中任务 {reclaimed_mikan} 个；补排 Mikan 匹配 {repaired_mikan} 个；Mikan 匹配成功 {mikan_done} 个，失败 {mikan_failed} 个；回填番剧 Mikan ID {repaired_series_mikan} 个；元数据成功 {metadata_done} 个，失败 {metadata_failed} 个；选集成功 {selection_done} 个，失败 {selection_failed} 个；补全成功 {backfill_done} 个，失败 {backfill_failed} 个；补全回灌补排 Mikan {replay_repaired_mikan} 个；补全回灌 Mikan 成功 {replay_mikan_done} 个，失败 {replay_mikan_failed} 个；补全回灌回填番剧 Mikan ID {replay_series_mikan} 个；补全回灌元数据成功 {replay_metadata_done} 个，失败 {replay_metadata_failed} 个；重跑选集成功 {replay_selection_done} 个，失败 {replay_selection_failed} 个；rclone 发现已完成 {rclone_done} 个，未发现 {rclone_missing} 个；PikPak 完成 {poll_done} 个，轮询失败 {poll_failed} 个；云盘资源登记 {cloud_done} 个，失败 {cloud_failed} 个，补齐 {cloud_count} 个；调和同步 {reconciled} 部，排队 {queued} 个"


def queue_summary(settings: dict[str, str]) -> list[dict[str, Any]]:
    with connect() as conn:
        metadata_rows = {
            row["status"]: row["count"]
            for row in conn.execute(
                "SELECT status, COUNT(*) AS count FROM metadata_tasks GROUP BY status"
            ).fetchall()
        }
        selection_rows = {
            row["status"]: row["count"]
            for row in conn.execute(
                "SELECT status, COUNT(*) AS count FROM selection_tasks GROUP BY status"
            ).fetchall()
        }
        backfill_rows = {
            row["status"]: row["count"]
            for row in conn.execute(
                "SELECT status, COUNT(*) AS count FROM backfill_tasks GROUP BY status"
            ).fetchall()
        }
        mikan_rows = {
            row["status"]: row["count"]
            for row in conn.execute(
                "SELECT status, COUNT(*) AS count FROM mikan_match_tasks GROUP BY status"
            ).fetchall()
        }
        merge_pending = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM (
              SELECT bangumi_id
              FROM series
              WHERE bangumi_id != '' AND COALESCE(hidden, 0)=0
              GROUP BY bangumi_id
              HAVING COUNT(*) > 1
            )
            """
        ).fetchone()["count"]
        cloud_rows = {
            row["status"]: row["count"]
            for row in conn.execute(
                "SELECT status, COUNT(*) AS count FROM download_tasks GROUP BY status"
            ).fetchall()
        }
        cloud_poll_rows = {
            row["status"]: row["count"]
            for row in conn.execute(
                "SELECT status, COUNT(*) AS count FROM cloud_poll_tasks GROUP BY status"
            ).fetchall()
        }
        cloud_asset_task_rows = {
            row["status"]: row["count"]
            for row in conn.execute(
                "SELECT status, COUNT(*) AS count FROM cloud_asset_tasks GROUP BY status"
            ).fetchall()
        }
        selection_retry = conn.execute(
            """
            SELECT COUNT(*) AS count, MIN(retry_after) AS next_retry_after
            FROM selection_tasks
            WHERE status='pending' AND retry_after != '' AND retry_after > ?
            """,
            (now(),),
        ).fetchone()
        backfill_retry = conn.execute(
            """
            SELECT COUNT(*) AS count, MIN(retry_after) AS next_retry_after
            FROM backfill_tasks
            WHERE status='failed' AND retry_after != '' AND retry_after > ?
            """,
            (now(),),
        ).fetchone()
        cloud_asset_retry = conn.execute(
            """
            SELECT COUNT(*) AS count, MIN(retry_after) AS next_retry_after
            FROM cloud_asset_tasks
            WHERE status='pending' AND retry_after != '' AND retry_after > ?
            """,
            (now(),),
        ).fetchone()
        sync_retry = conn.execute(
            """
            SELECT COUNT(*) AS count, MIN(retry_after) AS next_retry_after
            FROM sync_tasks
            WHERE status='pending' AND retry_after != '' AND retry_after > ?
            """,
            (now(),),
        ).fetchone()
        cloud_retry = conn.execute(
            """
            SELECT COUNT(*) AS count, MIN(retry_after) AS next_retry_after
            FROM download_tasks
            WHERE status='pending' AND retry_after != '' AND retry_after > ?
            """,
            (now(),),
        ).fetchone()
        sync_rows = {
            row["status"]: row["count"]
            for row in conn.execute(
                "SELECT status, COUNT(*) AS count FROM sync_tasks GROUP BY status"
            ).fetchall()
        }
        cloud_assets_pending = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM download_tasks dt
            LEFT JOIN cloud_assets ca ON ca.task_id=dt.id
            JOIN cloud_submissions cs ON cs.download_task_id=dt.id
            WHERE dt.status='completed'
              AND dt.pikpak_file_id != ''
              AND cs.provider='pikpak'
              AND cs.status='completed'
              AND ca.id IS NULL
            """
        ).fetchone()["count"]
    return [
        {
            "key": "rss",
            "name": "Mikan RSS",
            "pending": 1 if bool_setting(settings.get("auto_scan", "false")) else 0,
            "running": 0,
            "failed": 0,
            "description": "周期读取 RSS，新增发布后进入后续队列",
        },
        {
            "key": "mikan_match",
            "name": "Mikan 匹配",
            "pending": mikan_rows.get("pending", 0),
            "running": mikan_rows.get("running", 0),
            "failed": mikan_rows.get("failed", 0),
            "description": "从 Mikan 条目页/番组页解析 bgm.tv subject ID",
        },
        {
            "key": "metadata",
            "name": "元数据",
            "pending": metadata_rows.get("pending", 0),
            "running": metadata_rows.get("running", 0),
            "failed": metadata_rows.get("failed", 0),
            "description": "候选必须获取完整元数据后才进入正式库",
        },
        {
            "key": "selection",
            "name": "自动选集",
            "pending": selection_rows.get("pending", 0),
            "running": selection_rows.get("running", 0),
            "failed": selection_rows.get("failed", 0),
            "waiting": selection_retry["count"] if selection_retry else 0,
            "next_retry_after": selection_retry["next_retry_after"] if selection_retry else "",
            "next_retry_seconds": seconds_until(selection_retry["next_retry_after"] if selection_retry else ""),
            "description": "根据字幕组、分辨率、主副字幕语言规则唯一选择发布",
        },
        {
            "key": "backfill",
            "name": "整季补全",
            "pending": backfill_rows.get("pending", 0),
            "running": backfill_rows.get("running", 0),
            "failed": backfill_rows.get("failed", 0),
            "waiting": backfill_retry["count"] if backfill_retry else 0,
            "next_retry_after": backfill_retry["next_retry_after"] if backfill_retry else "",
            "next_retry_seconds": seconds_until(backfill_retry["next_retry_after"] if backfill_retry else ""),
            "description": "按补全策略补抓当季历史条目并进入候选链路",
        },
        {
            "key": "merge",
            "name": "合并",
            "pending": merge_pending,
            "running": 0,
            "failed": 0,
            "description": "相同 Bangumi ID 的重复条目",
        },
        {
            "key": "cloud",
            "name": "PikPak 入库",
            "pending": cloud_rows.get("pending", 0),
            "running": cloud_rows.get("running", 0) + cloud_rows.get("submitted", 0),
            "failed": cloud_rows.get("failed", 0),
            "waiting": cloud_retry["count"] if cloud_retry else 0,
            "next_retry_after": cloud_retry["next_retry_after"] if cloud_retry else "",
            "next_retry_seconds": seconds_until(cloud_retry["next_retry_after"] if cloud_retry else ""),
            "description": "提交离线任务；submitted 表示已交给 PikPak/rclone 等待云盘完成",
        },
        {
            "key": "cloud_poll",
            "name": "PikPak 状态",
            "pending": cloud_poll_rows.get("pending", 0),
            "running": cloud_poll_rows.get("running", 0),
            "failed": cloud_poll_rows.get("failed", 0),
            "description": "独立轮询离线任务完成状态，不阻塞新的入库提交",
        },
        {
            "key": "cloud_assets",
            "name": "云盘资源登记",
            "pending": cloud_asset_task_rows.get("pending", 0) + cloud_assets_pending,
            "running": cloud_asset_task_rows.get("running", 0),
            "failed": cloud_asset_task_rows.get("failed", 0),
            "waiting": cloud_asset_retry["count"] if cloud_asset_retry else 0,
            "next_retry_after": cloud_asset_retry["next_retry_after"] if cloud_asset_retry else "",
            "next_retry_seconds": seconds_until(cloud_asset_retry["next_retry_after"] if cloud_asset_retry else ""),
            "description": "把完成的 PikPak 任务登记成云盘资源",
        },
        {
            "key": "sync",
            "name": "本地同步",
            "pending": sync_rows.get("pending", 0),
            "running": sync_rows.get("running", 0),
            "failed": sync_rows.get("failed", 0),
            "waiting": sync_retry["count"] if sync_retry else 0,
            "next_retry_after": sync_retry["next_retry_after"] if sync_retry else "",
            "next_retry_seconds": seconds_until(sync_retry["next_retry_after"] if sync_retry else ""),
            "description": "从云盘 API 下载到 NAS 本地目录",
        },
    ]


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    cleanup_operations()
    reschedule()
    scheduler.start()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="AutoAnime", lifespan=lifespan)


def run_operation(name: str, coro_factory, start_message: str = "") -> int:
    operation_id = start_operation(name, start_message)
    log("info", f"{name} 已启动: {start_message or '处理中'}")

    async def runner() -> None:
        try:
            message = await coro_factory()
        except Exception as exc:
            finish_operation(operation_id, "failed", str(exc))
            log("error", f"{name} 失败: {exc}")
            return
        finish_operation(operation_id, "completed", str(message or "完成"))
        log("info", f"{name} 完成: {message or '完成'}")

    asyncio.create_task(runner())
    return operation_id


def run_progress_operation(name: str, coro_factory, start_message: str = "") -> int:
    operation_id = start_operation(name, start_message)
    log("info", f"{name} 已启动: {start_message or '处理中'}")

    async def runner() -> None:
        try:
            message = await coro_factory(operation_id)
        except Exception as exc:
            finish_operation(operation_id, "failed", str(exc))
            log("error", f"{name} 失败: {exc}")
            return
        finish_operation(operation_id, "completed", str(message or "完成"))
        log("info", f"{name} 完成: {message or '完成'}")

    asyncio.create_task(runner())
    return operation_id


def dashboard_data() -> dict[str, Any]:
    settings = get_settings()
    with connect() as conn:
        series = conn.execute(
            """
            SELECT s.*,
              COUNT(DISTINCT e.id) AS episode_count,
              COUNT(DISTINCT r.id) AS release_count,
              COUNT(DISTINCT r.subtitle_group) AS group_count,
              COUNT(DISTINCT r.resolution) AS resolution_count,
              COUNT(DISTINCT r.language) AS language_count,
              GROUP_CONCAT(DISTINCT NULLIF(r.subtitle_group, '')) AS subtitle_groups,
              GROUP_CONCAT(DISTINCT NULLIF(r.resolution, '')) AS resolutions,
              GROUP_CONCAT(DISTINCT NULLIF(r.language, '')) AS languages,
              COUNT(DISTINCT CASE WHEN dt.status IN ('submitted','completed') THEN dt.id END) AS downloaded_count,
              COUNT(DISTINCT ca.id) AS cloud_asset_count,
              COUNT(DISTINCT la.id) AS local_asset_count,
              COALESCE(MAX(sr.sync_enabled), 0) AS sync_enabled,
              COALESCE(MAX(sr.auto_sync_following), 0) AS auto_sync_following
            FROM series s
            LEFT JOIN episodes e ON e.series_id=s.id
            LEFT JOIN releases r ON r.series_id=s.id
            LEFT JOIN download_tasks dt ON dt.series_id=s.id
            LEFT JOIN cloud_assets ca ON ca.series_id=s.id
            LEFT JOIN local_assets la ON la.series_id=s.id AND la.status='synced'
            LEFT JOIN sync_rules sr ON sr.series_id=s.id
            WHERE COALESCE(s.hidden, 0)=0
              AND s.bangumi_id != ''
            GROUP BY s.id
            ORDER BY s.updated_at DESC
            """
        ).fetchall()
        rss_candidates = conn.execute(
            """
            SELECT *
            FROM rss_candidates
            WHERE status IN ('pending', 'pending_metadata', 'failed')
            ORDER BY updated_at DESC
            LIMIT 120
            """
        ).fetchall()
        tasks = conn.execute(
            """
            SELECT dt.*, s.title_cn, r.episode_number, r.subtitle_group, r.resolution, r.language, r.title AS release_title
            FROM download_tasks dt
            JOIN series s ON s.id=dt.series_id
            JOIN releases r ON r.id=dt.release_id
            WHERE s.bangumi_id != ''
            ORDER BY dt.id DESC
            LIMIT 80
            """
        ).fetchall()
        selection_tasks = conn.execute(
            """
            SELECT st.*, s.title_cn
            FROM selection_tasks st
            JOIN series s ON s.id=st.series_id
            WHERE COALESCE(s.hidden, 0)=0
            ORDER BY st.updated_at DESC
            LIMIT 80
            """
        ).fetchall()
        backfill_tasks = conn.execute(
            """
            SELECT bt.*, s.title_cn
            FROM backfill_tasks bt
            JOIN series s ON s.id=bt.series_id
            WHERE COALESCE(s.hidden, 0)=0
            ORDER BY bt.updated_at DESC
            LIMIT 80
            """
        ).fetchall()
        logs = conn.execute("SELECT * FROM logs ORDER BY id DESC LIMIT 80").fetchall()
        cloud_assets = conn.execute(
            """
            SELECT ca.*, s.title_cn
            FROM cloud_assets ca
            JOIN series s ON s.id=ca.series_id
            WHERE s.bangumi_id != ''
            ORDER BY ca.updated_at DESC
            LIMIT 80
            """
        ).fetchall()
        sync_tasks = conn.execute(
            """
            SELECT st.*, s.title_cn
            FROM sync_tasks st
            JOIN series s ON s.id=st.series_id
            WHERE s.bangumi_id != ''
              AND st.status IN ('pending', 'running', 'failed')
            ORDER BY st.updated_at DESC
            LIMIT 80
            """
        ).fetchall()
        operations = conn.execute(
            """
            SELECT *
            FROM operations
            WHERE status IN ('running', 'failed')
            ORDER BY
              CASE status
                WHEN 'running' THEN 0
                WHEN 'failed' THEN 1
                ELSE 2
              END,
              id DESC
            LIMIT 20
            """
        ).fetchall()
        sync_rules = conn.execute(
            """
            SELECT sr.*, s.title_cn
            FROM sync_rules sr
            JOIN series s ON s.id=sr.series_id
            WHERE COALESCE(s.hidden, 0)=0
              AND s.bangumi_id != ''
            ORDER BY sr.updated_at DESC
            LIMIT 200
            """
        ).fetchall()
        task_counts = conn.execute(
            "SELECT status, COUNT(*) AS count FROM download_tasks GROUP BY status"
        ).fetchall()
        active_tasks = conn.execute(
            """
            SELECT dt.*, s.title_cn, r.episode_number, r.subtitle_group, r.resolution, r.language, r.title AS release_title
            FROM download_tasks dt
            JOIN series s ON s.id=dt.series_id
            JOIN releases r ON r.id=dt.release_id
            WHERE dt.status IN ('pending', 'running', 'submitted', 'failed')
              AND s.bangumi_id != ''
            ORDER BY
              CASE dt.status
                WHEN 'running' THEN 0
                WHEN 'pending' THEN 1
                WHEN 'submitted' THEN 2
                WHEN 'failed' THEN 3
                ELSE 4
              END,
              dt.updated_at DESC
            LIMIT 20
            """
        ).fetchall()
        calendar = conn.execute(
            """
            SELECT s.title_cn, e.episode_number, e.air_date, e.status
            FROM episodes e
            JOIN series s ON s.id=e.series_id
            WHERE e.air_date != ''
            ORDER BY e.air_date ASC
            LIMIT 80
            """
        ).fetchall()
    return {
        "series": rows_to_dicts(series),
        "rss_candidates": rows_to_dicts(rss_candidates),
        "tasks": enrich_download_tasks(tasks),
        "selection_tasks": enrich_retry_rows(selection_tasks),
        "backfill_tasks": enrich_retry_rows(backfill_tasks),
        "logs": rows_to_dicts(logs),
        "cloud_assets": rows_to_dicts(cloud_assets),
        "sync_rules": rows_to_dicts(sync_rules),
        "sync_tasks": enrich_retry_rows(sync_tasks),
        "operations": rows_to_dicts(operations),
        "calendar": rows_to_dicts(calendar),
        "task_counts": {row["status"]: row["count"] for row in task_counts},
        "active_tasks": enrich_download_tasks(active_tasks),
        "queue_summary": queue_summary(settings),
        "server_logs": read_server_logs(160),
    }


@app.get("/api/dashboard")
async def api_dashboard() -> dict[str, Any]:
    return dashboard_data()


@app.get("/api/settings")
async def api_settings() -> dict[str, Any]:
    return settings_response()


@app.get("/api/system/diagnostics")
async def api_system_diagnostics() -> dict[str, Any]:
    return diagnostics()


@app.put("/api/settings")
async def api_update_settings(payload: SettingsPayload) -> dict[str, Any]:
    previous = get_settings()
    save_settings(
        {
            "rss_url": payload.rss_url.strip(),
            "rss_proxy": payload.rss_proxy.strip(),
            "scan_interval_minutes": payload.scan_interval_minutes,
            "auto_scan": str(payload.auto_scan).lower(),
            "auto_download_unique": str(payload.auto_download_unique).lower(),
            "auto_download_by_priority": str(payload.auto_download_by_priority).lower(),
            "default_backfill": payload.default_backfill,
            "subtitle_priority": "\n".join(payload.subtitle_priority),
            "resolution_priority": "\n".join(payload.resolution_priority),
            "language_priority": "\n".join(payload.language_priority),
            "secondary_language_priority": "\n".join(payload.secondary_language_priority),
            "cloud_transfer_backend": payload.cloud_transfer_backend,
            "rclone_command": payload.rclone_command.strip() or "rclone",
            "rclone_config_path": payload.rclone_config_path.strip(),
            "rclone_remote": payload.rclone_remote.strip() or "pikpak",
            "pikpak_auth_mode": payload.pikpak_auth_mode,
            "pikpak_username": payload.pikpak_username.strip(),
            "pikpak_password": payload.pikpak_password,
            "pikpak_access_token": payload.pikpak_access_token.strip(),
            "pikpak_refresh_token": payload.pikpak_refresh_token.strip(),
            "pikpak_proxy": payload.pikpak_proxy.strip(),
            "library_root": payload.library_root.strip() or "/Anime",
            "local_library_root": payload.local_library_root.strip() or "/media/pikpak-anime",
            "auto_sync_following": str(payload.auto_sync_following).lower(),
            "nfo_output_root": payload.nfo_output_root.strip(),
            "series_dir_template": payload.series_dir_template.strip(),
            "season_dir_template": payload.season_dir_template.strip(),
            "episode_name_template": payload.episode_name_template.strip(),
        }
    )
    current = get_settings()
    if any(
        previous.get(key, "") != current.get(key, "")
        for key in [
            "subtitle_priority",
            "resolution_priority",
            "language_priority",
            "secondary_language_priority",
            "default_backfill",
            "series_dir_template",
            "season_dir_template",
            "episode_name_template",
            "local_library_root",
            "auto_sync_following",
        ]
    ):
        with connect() as conn:
            ts = now()
            series_rows = conn.execute("SELECT id FROM series WHERE COALESCE(hidden, 0)=0 AND bangumi_id != ''").fetchall()
            for row in series_rows:
                enqueue_selection_task(conn, int(row["id"]), ts, "全局规则变更，重新计算自动选集")
                enqueue_backfill_task(conn, int(row["id"]), current, ts)
    reschedule()
    trigger_queues(["selection", "backfill"])
    log("info", "全局设置已保存")
    return settings_response()


@app.get("/api/series/{series_id}")
async def api_series(series_id: int) -> dict[str, Any]:
    with connect() as conn:
        series = conn.execute("SELECT * FROM series WHERE id=?", (series_id,)).fetchone()
        releases = conn.execute(
            "SELECT * FROM releases WHERE series_id=? ORDER BY episode_number ASC, id DESC",
            (series_id,),
        ).fetchall()
        tasks = conn.execute(
            """
            SELECT *
            FROM download_tasks
            WHERE series_id=?
              AND status IN ('pending', 'running', 'submitted', 'failed')
            ORDER BY id DESC
            """,
            (series_id,),
        ).fetchall()
        cloud_assets = conn.execute(
            "SELECT * FROM cloud_assets WHERE series_id=? ORDER BY episode_number ASC, id DESC",
            (series_id,),
        ).fetchall()
        local_assets = conn.execute(
            "SELECT * FROM local_assets WHERE series_id=? ORDER BY episode_number ASC, id DESC",
            (series_id,),
        ).fetchall()
    if not series:
        return {"series": None, "releases": [], "tasks": [], "cloud_assets": [], "local_assets": [], "groups": [], "resolutions": [], "languages": []}
    groups = sorted({r["subtitle_group"] for r in releases if r["subtitle_group"]})
    resolutions = sorted({r["resolution"] for r in releases if r["resolution"]})
    languages = sorted({r["language"] for r in releases if r["language"]})
    return {
        "series": row_to_dict(series),
        "releases": rows_to_dicts(releases),
        "tasks": enrich_download_tasks(tasks),
        "cloud_assets": rows_to_dicts(cloud_assets),
        "local_assets": rows_to_dicts(local_assets),
        "groups": groups,
        "resolutions": resolutions,
        "languages": languages,
    }


@app.put("/api/series/{series_id}")
async def api_update_series(series_id: int, payload: SeriesPayload) -> dict[str, Any]:
    with connect() as conn:
        conn.execute(
            """
            UPDATE series
            SET title_cn=?, bangumi_id=?, tmdb_id=?, year=?, season_number=?,
                auto_download=?, selected_group=?, selected_resolution=?,
                backfill_mode=?, updated_at=?
            WHERE id=?
            """,
            (
                payload.title_cn.strip(),
                payload.bangumi_id.strip(),
                payload.tmdb_id.strip(),
                payload.year,
                payload.season_number,
                payload.auto_download,
                payload.selected_group.strip(),
                payload.selected_resolution.strip(),
                payload.backfill_mode,
                now(),
                series_id,
            ),
        )
        ts = now()
        enqueue_selection_task(conn, series_id, ts, "番剧规则变更，重新计算自动选集")
        enqueue_backfill_task(conn, series_id, get_settings(), ts)
    log("info", f"番剧设置已保存: {payload.title_cn}")
    with connect() as conn:
        merge_duplicate_series(conn)
    return await api_series(series_id)


@app.delete("/api/series/{series_id}")
async def api_delete_series(series_id: int) -> dict[str, str]:
    with connect() as conn:
        series = conn.execute("SELECT title_cn FROM series WHERE id=?", (series_id,)).fetchone()
        if not series:
            return {"status": "not_found", "message": "番剧不存在"}
        title = series["title_cn"]
        ts = now()
        conn.execute(
            "UPDATE series SET hidden=1, updated_at=? WHERE id=?",
            (ts, series_id),
        )
    log("warn", f"已隐藏误识别番剧: {title}")
    return {"status": "completed", "message": "已隐藏误识别番剧，关联记录已保留"}


@app.post("/api/scan")
async def api_scan() -> dict[str, str]:
    with connect() as conn:
        running = conn.execute(
            "SELECT id FROM operations WHERE name='扫描全部' AND status='running' LIMIT 1"
        ).fetchone()
    if running:
        return {"status": "running", "operation_id": str(running["id"]), "message": "扫描全部正在执行"}
    operation_id = run_progress_operation(
        "扫描全部",
        lambda op_id: run_full_refresh(get_settings(), op_id),
        "正在依次执行 RSS、Mikan 匹配、元数据、云盘入库、PikPak 状态、本地同步",
    )
    return {"status": "started", "operation_id": str(operation_id), "message": "扫描全部已启动"}


@app.post("/api/tasks/process")
async def api_process_tasks(force: bool = Query(False)) -> dict[str, str]:
    operation_id = run_operation(
        "云盘队列立即处理" if force else "云盘队列处理",
        lambda: process_tasks(get_settings(), force=force),
        "正在立即提交 PikPak 云盘任务" if force else "正在提交 PikPak 云盘任务",
    )
    trigger_queue("download", delay=0)
    return {"status": "started", "operation_id": str(operation_id), "message": "云盘队列已立即触发" if force else "队列处理已启动"}


@app.post("/api/tasks/poll")
async def api_poll_tasks() -> dict[str, str]:
    settings = get_settings()
    async def run() -> str:
        rclone_done, rclone_missing = await reconcile_rclone_submitted_tasks(settings)
        poll_done, poll_failed = await poll_submitted_tasks(settings, force=True)
        cloud_done, cloud_failed = await process_cloud_asset_tasks(settings, force=True)
        count = backfill_cloud_assets_from_completed_tasks(settings)
        reconciled, queued = reconcile_sync_intents(settings)
        if queued:
            await process_sync_tasks(settings)
        return f"rclone 发现已完成 {rclone_done} 个，未发现 {rclone_missing} 个；PikPak 完成 {poll_done} 个，轮询失败 {poll_failed} 个；云盘登记 {cloud_done} 个，失败 {cloud_failed} 个；补齐云盘 {count} 个，调和 {reconciled} 部，同步排队 {queued} 个"

    operation_id = run_operation("刷新云盘状态", run, "正在刷新 PikPak 任务和同步状态")
    return {"status": "started", "operation_id": str(operation_id), "message": "状态刷新已启动"}


@app.post("/api/cloud/scan")
async def api_scan_cloud() -> dict[str, str]:
    async def run() -> str:
        settings = get_settings()
        imported, skipped = await scan_cloud_library(settings)
        log("info", f"云盘库扫描完成: 入库 {imported} 个，跳过 {skipped} 个")
        return f"入库 {imported} 个，跳过 {skipped} 个"

    operation_id = run_operation("扫描云盘库", run, "正在扫描 PikPak 云盘库")
    trigger_queues(["sync_plan", "sync"])
    return {"status": "started", "operation_id": str(operation_id), "message": "云盘库扫描已启动"}


@app.post("/api/sync/tasks/process")
async def api_process_sync_tasks() -> dict[str, str]:
    settings = get_settings()
    async def run() -> str:
        reconciled, queued = reconcile_sync_intents(settings)
        await process_sync_tasks(settings)
        return f"调和 {reconciled} 部，同步排队 {queued} 个"

    operation_id = run_operation("本地同步", run, "正在把云盘资源同步到本地")
    trigger_queues(["sync_plan", "sync"], delay=0)
    return {"status": "started", "operation_id": str(operation_id), "message": "本地同步处理已启动"}


@app.post("/api/tasks/retry-failed")
async def api_retry_failed() -> dict[str, str]:
    with connect() as conn:
        total = 0
        for table in [
            "download_tasks",
            "cloud_poll_tasks",
            "cloud_asset_tasks",
            "sync_tasks",
            "selection_tasks",
            "backfill_tasks",
            "metadata_tasks",
            "mikan_match_tasks",
        ]:
            cursor = conn.execute(
                f"""
                UPDATE {table}
                SET status='pending', attempts=0, retry_after='', last_error='', updated_at=?
                WHERE status='failed'
                """,
                (now(),),
            )
            total += cursor.rowcount
        cursor = conn.execute(
            """
            UPDATE operations
            SET status='failed', finished_at=?
            WHERE status='running'
            """,
            (now(),),
        )
    log("info", f"已重置失败任务: {total} 个")
    trigger_queues(["mikan_match", "metadata", "selection", "backfill", "download", "cloud_poll", "cloud_asset", "sync_plan", "sync"], delay=0)
    return {"status": "started", "count": str(total), "message": f"失败任务已重新入队: {total} 个"}


@app.post("/api/operations/clear")
async def api_clear_operations() -> dict[str, str]:
    with connect() as conn:
        cursor = conn.execute(
            "DELETE FROM operations WHERE status IN ('completed', 'failed')"
        )
    return {"status": "completed", "count": str(cursor.rowcount), "message": "已清空已结束操作"}


@app.post("/api/logs/clear")
async def api_clear_logs() -> dict[str, str]:
    with connect() as conn:
        cursor = conn.execute("DELETE FROM logs")
    try:
        LOG_PATH.unlink(missing_ok=True)
    except OSError:
        pass
    return {"status": "completed", "count": str(cursor.rowcount), "message": "已清空日志"}


@app.post("/api/system/clear-data")
async def api_clear_data() -> dict[str, str]:
    clear_runtime_data()
    log("warn", "已清除所有运行数据")
    return {"status": "completed", "message": "已清除所有运行数据"}


@app.post("/api/series/{series_id}/download")
async def api_download_series(series_id: int) -> dict[str, str]:
    settings = get_settings()
    with connect() as conn:
        series = conn.execute("SELECT * FROM series WHERE id=?", (series_id,)).fetchone()
        if not series:
            return {"status": "not_found"}
    ids, choice = resolve_series_choice(series_id, settings)
    mark_selected_releases(series_id, ids)
    if not ids:
        message = choice["reason"] or "没有可入云盘发布"
        log("warn", f"手动入云盘跳过: {series['title_cn']} - {message}")
        return {"status": "skipped", "count": "0", "message": message}
    for release_id in ids:
        queue_release(release_id, settings)
    trigger_queue("download", delay=0)
    return {"status": "queued", "count": str(len(ids)), "message": f"已加入云盘队列: {len(ids)} 条"}


@app.post("/api/releases/{release_id}/download")
async def api_download_release(release_id: int) -> dict[str, str]:
    queue_release(release_id, get_settings())
    trigger_queue("download", delay=0)
    return {"status": "queued"}


@app.post("/api/series/{series_id}/metadata")
async def api_refresh_metadata(series_id: int) -> dict[str, str]:
    settings = get_settings()
    asyncio.create_task(refresh_series_metadata(series_id, settings.get("rss_proxy", "")))
    return {"status": "started"}


@app.post("/api/series/{series_id}/nfo")
async def api_generate_nfo(series_id: int) -> dict[str, str]:
    generate_nfo_for_series(series_id, get_settings())
    return {"status": "generated"}


@app.post("/api/series/{series_id}/sync")
async def api_sync_series(series_id: int) -> dict[str, str]:
    settings = get_settings()
    count, message = queue_sync_for_series(series_id, settings)
    if count > 0:
        trigger_queue("sync", delay=0)
        return {"status": "queued", "count": str(count), "message": message}
    return {"status": "completed", "count": "0", "message": message}


@app.post("/api/series/{series_id}/sync/cancel")
async def api_cancel_sync_series(series_id: int) -> dict[str, str]:
    count, message = cancel_sync_for_series(series_id)
    return {"status": "completed", "count": str(count), "message": message}


frontend_dir = APP_DIR.parent / "frontend_dist"
if frontend_dir.exists():
    app.mount("/assets", StaticFiles(directory=frontend_dir / "assets"), name="assets")


@app.get("/{full_path:path}")
async def spa(full_path: str) -> FileResponse:
    index_file = frontend_dir / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    fallback = APP_DIR / "static" / "missing-frontend.html"
    return FileResponse(fallback)
