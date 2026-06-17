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
from .db import LOG_PATH, cleanup_operations, clear_runtime_data, connect, diagnostics, finish_operation, finish_scheduled_job_run, get_runtime_generation, get_settings, init_db, log, mark_scheduled_job, merge_duplicate_series, now, read_server_logs, run_cleanup_tasks, save_settings, start_operation, start_scheduled_job_run, update_operation
from .queue_bridge import register_queue_trigger
from .library import bool_setting
from .metadata import generate_nfo_for_entry, refresh_entry_metadata
from .scanner import enqueue_backfill_task, enqueue_missing_mikan_match_tasks, enqueue_selection_task, mark_selected_releases, poll_submitted_tasks, process_backfill_tasks, process_cloud_presence_tasks, process_download_enqueue_tasks, process_metadata_tasks, process_mikan_match_tasks, process_selection_tasks, process_tasks, queue_release, reclaim_mikan_match_tasks, repair_series_mikan_ids, resolve_entry_choice, scan_and_queue
from .sync_service import backfill_cloud_assets_from_completed_tasks, cancel_sync_for_series, enqueue_sync_plan_tasks, process_cloud_asset_tasks, process_local_presence_tasks, process_nfo_tasks, process_sync_plan_tasks, process_sync_tasks, queue_sync_for_series, reconcile_rclone_submitted_tasks, scan_cloud_library


scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")
QUEUE_DEBOUNCE_SECONDS = 10.0
QueueHandler = Callable[[], Awaitable[None]]
queue_handlers: dict[str, QueueHandler] = {}
queue_debounce_tasks: dict[str, asyncio.Task] = {}
queue_running: set[str] = set()
queue_rerun_requested: set[str] = set()
QUEUE_KEY_ALIASES = {
    "cloud": "download",
    "cloud_assets": "cloud_asset",
}


def canonical_queue_key(name: str) -> str:
    return QUEUE_KEY_ALIASES.get(name, name)


def queue_job_key(name: str) -> str:
    return f"{canonical_queue_key(name)}_dispatch"


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


class LibraryImportPayload(BaseModel):
    source_type: str = "cloud_scan"
    query: str = ""
    magnet: str = ""
    source_ref: str = ""


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
        row["display_title"] = (
            row.get("title_cn")
            or row.get("series_title")
            or row.get("release_title")
            or row.get("cloud_name")
            or row.get("title")
            or ""
        )
        if row.get("progress_text"):
            row["display_reason"] = row.get("progress_text")
        elif row.get("reason"):
            row["display_reason"] = row.get("reason")
        elif row["waiting_retry"]:
            row["display_reason"] = "等待重试"
        elif row.get("last_error"):
            row["display_reason"] = row.get("last_error")
        else:
            row["display_reason"] = ""
    return result


def enrich_retry_rows(rows: list[Any]) -> list[dict[str, Any]]:
    result = rows_to_dicts(rows)
    for row in result:
        retry_seconds = seconds_until(str(row.get("retry_after") or ""))
        row["retry_seconds"] = retry_seconds
        row["waiting_retry"] = row.get("status") == "pending" and retry_seconds > 0
        row["display_title"] = (
            row.get("title_cn")
            or row.get("series_title")
            or row.get("release_title")
            or row.get("local_path")
            or row.get("cloud_name")
            or row.get("title")
            or ""
        )
        if row.get("progress_text"):
            row["display_reason"] = row.get("progress_text")
        elif row.get("reason"):
            row["display_reason"] = row.get("reason")
        elif row["waiting_retry"]:
            row["display_reason"] = f"等待重试，剩余 {retry_seconds} 秒"
        elif row.get("last_error"):
            row["display_reason"] = row.get("last_error")
        else:
            row["display_reason"] = ""
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


def empty_entry_response() -> dict[str, Any]:
    return {
        "series": None,
        "releases": [],
        "tasks": [],
        "cloud_assets": [],
        "local_assets": [],
        "groups": [],
        "resolutions": [],
        "languages": [],
    }


def build_entry_response(entry_id: int) -> dict[str, Any]:
    with connect() as conn:
        entry = conn.execute("SELECT * FROM entries WHERE id=?", (entry_id,)).fetchone()
        if not entry:
            return empty_entry_response()
        releases = conn.execute(
            "SELECT * FROM releases WHERE entry_id=? ORDER BY episode_number ASC, id DESC",
            (entry_id,),
        ).fetchall()
        tasks = conn.execute(
            """
            SELECT *
            FROM download_tasks
            WHERE entry_id=?
              AND status IN ('pending', 'running', 'submitted', 'failed')
            ORDER BY id DESC
            """,
            (entry_id,),
        ).fetchall()
        cloud_assets = conn.execute(
            "SELECT * FROM cloud_assets WHERE entry_id=? ORDER BY episode_number ASC, id DESC",
            (entry_id,),
        ).fetchall()
        local_assets = conn.execute(
            "SELECT * FROM local_assets WHERE entry_id=? ORDER BY episode_number ASC, id DESC",
            (entry_id,),
        ).fetchall()
    groups = sorted({r["subtitle_group"] for r in releases if r["subtitle_group"]})
    resolutions = sorted({r["resolution"] for r in releases if r["resolution"]})
    languages = sorted({r["language"] for r in releases if r["language"]})
    return {
        "series": {**row_to_dict(entry), "domain_kind": entry["domain_kind"]},
        "releases": rows_to_dicts(releases),
        "tasks": enrich_download_tasks(tasks),
        "cloud_assets": rows_to_dicts(cloud_assets),
        "local_assets": rows_to_dicts(local_assets),
        "groups": groups,
        "resolutions": resolutions,
        "languages": languages,
    }


def save_entry_payload(entry_id: int, payload: SeriesPayload, *, expected_domain: str | None = None) -> dict[str, Any]:
    with connect() as conn:
        entry = conn.execute("SELECT * FROM entries WHERE id=?", (entry_id,)).fetchone()
        if not entry:
            return empty_entry_response()
        domain_kind = str(entry["domain_kind"] or "")
        if expected_domain and domain_kind != expected_domain:
            return empty_entry_response()
        conn.execute(
            """
            UPDATE entries
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
                entry_id,
            ),
        )
        ts = now()
        if domain_kind == "seasonal":
            conn.execute(
                """
                UPDATE series
                SET title_cn=?, bangumi_id=?, tmdb_id=?, year=?, season_number=?, updated_at=?
                WHERE bangumi_id=?
                """,
                (
                    payload.title_cn.strip(),
                    payload.bangumi_id.strip(),
                    payload.tmdb_id.strip(),
                    payload.year,
                    payload.season_number,
                    now(),
                    payload.bangumi_id.strip(),
                ),
            )
            series_row = conn.execute(
                "SELECT series_id FROM releases WHERE entry_id=? ORDER BY id ASC LIMIT 1",
                (entry_id,),
            ).fetchone()
            enqueue_selection_task(
                conn,
                int(series_row["series_id"] or 0) if series_row else 0,
                entry_id,
                ts,
                "番剧规则变更，重新计算自动选集",
            )
            enqueue_backfill_task(
                conn,
                int(series_row["series_id"] or 0) if series_row else 0,
                entry_id,
                get_settings(),
                ts,
            )
    if domain_kind == "seasonal":
        log("info", f"新番条目设置已保存: {payload.title_cn}")
        with connect() as conn:
            merge_duplicate_series(conn)
    else:
        log("info", f"番剧库条目已保存: {payload.title_cn}")
    return build_entry_response(entry_id)


def hide_entry(entry_id: int, *, expected_domain: str | None = None, success_message: str = "已隐藏条目，关联记录已保留", log_prefix: str = "已隐藏条目") -> dict[str, str]:
    with connect() as conn:
        entry = conn.execute(
            "SELECT display_title, domain_kind FROM entries WHERE id=?",
            (entry_id,),
        ).fetchone()
        if not entry:
            return {"status": "not_found", "message": "番剧不存在"}
        if expected_domain and entry["domain_kind"] != expected_domain:
            domain_label = "新番域" if expected_domain == "seasonal" else "番剧库"
            return {"status": "invalid_domain", "message": f"该条目不属于{domain_label}"}
        title = entry["display_title"]
        ts = now()
        conn.execute(
            "UPDATE entries SET hidden=1, updated_at=? WHERE id=?",
            (ts, entry_id),
        )
    log("warn", f"{log_prefix}: {title}")
    return {"status": "completed", "message": success_message}


def queue_entry_download(entry_id: int) -> dict[str, str]:
    settings = get_settings()
    with connect() as conn:
        entry = conn.execute("SELECT * FROM entries WHERE id=?", (entry_id,)).fetchone()
        if not entry:
            return {"status": "not_found"}
    ids, choice = resolve_entry_choice(entry_id, settings)
    mark_selected_releases(entry_id, ids)
    if not ids:
        message = choice["reason"] or "没有可入云盘发布"
        log("warn", f"手动入云盘跳过: {entry['display_title']} - {message}")
        return {"status": "skipped", "count": "0", "message": message}
    for release_id in ids:
        queue_release(release_id, settings)
    return {"status": "queued", "count": str(len(ids)), "message": f"已加入云盘队列: {len(ids)} 条"}


def start_entry_metadata_refresh(entry_id: int) -> dict[str, str]:
    settings = get_settings()
    asyncio.create_task(refresh_entry_metadata(entry_id, settings.get("rss_proxy", "")))
    return {"status": "started"}


def generate_entry_nfo(entry_id: int) -> dict[str, str]:
    generate_nfo_for_entry(entry_id, get_settings())
    return {"status": "generated"}


def queue_entry_sync(entry_id: int) -> dict[str, str]:
    settings = get_settings()
    count, message = queue_sync_for_series(entry_id, settings)
    if count > 0:
        return {"status": "queued", "count": str(count), "message": message}
    return {"status": "completed", "count": "0", "message": message}


def cancel_entry_sync(entry_id: int) -> dict[str, str]:
    count, message = cancel_sync_for_series(entry_id)
    return {"status": "completed", "count": str(count), "message": message}


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


def ready_count_cloud_presence() -> int:
    return count_ready(
        """
        SELECT COUNT(*) AS count
        FROM cloud_presence_tasks
        WHERE status IN ('pending', 'failed')
          AND (retry_after='' OR retry_after <= ?)
        """,
        (now(),),
    )


def ready_count_download_enqueue() -> int:
    return count_ready(
        """
        SELECT COUNT(*) AS count
        FROM download_enqueue_tasks
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
            JOIN entries e ON e.id=dt.entry_id
            WHERE dt.status='submitted'
              AND e.bangumi_id != ''
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
    return count_ready(
        """
        SELECT COUNT(*) AS count
        FROM sync_plan_tasks
        WHERE status IN ('pending', 'failed')
          AND (retry_after='' OR retry_after <= ?)
        """,
        (now(),),
    )


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


def ready_count_nfo() -> int:
    return count_ready(
        """
        SELECT COUNT(*) AS count
        FROM nfo_tasks
        WHERE status IN ('pending', 'failed')
          AND (retry_after='' OR retry_after <= ?)
        """,
        (now(),),
    )


def ready_count_local_presence() -> int:
    return count_ready(
        """
        SELECT COUNT(*) AS count
        FROM local_presence_tasks
        WHERE status IN ('pending', 'failed')
          AND (retry_after='' OR retry_after <= ?)
        """,
        (now(),),
    )


def ready_count_cleanup() -> int:
    return count_ready(
        """
        SELECT COUNT(*) AS count
        FROM cleanup_tasks
        WHERE status IN ('pending', 'failed')
          AND (retry_after='' OR retry_after <= ?)
        """,
        (now(),),
    )


def recoverable_queue_names() -> list[str]:
    checks = [
        ("mikan_match", ready_count_mikan_match),
        ("metadata", ready_count_metadata),
        ("selection", ready_count_selection),
        ("backfill", ready_count_backfill),
        ("cloud_presence", ready_count_cloud_presence),
        ("download_enqueue", ready_count_download_enqueue),
        ("download", ready_count_download),
        ("cloud_poll", ready_count_cloud_poll),
        ("cloud_asset", ready_count_cloud_asset),
        ("sync_plan", ready_count_sync_plan),
        ("sync", ready_count_sync),
        ("nfo", ready_count_nfo),
        ("local_presence", ready_count_local_presence),
        ("cleanup", ready_count_cleanup),
    ]
    names: list[str] = []
    for name, fn in checks:
        runtime_key = canonical_queue_key(name)
        if runtime_key in queue_running:
            continue
        pending_task = queue_debounce_tasks.get(runtime_key)
        if pending_task and not pending_task.done():
            continue
        if runtime_key in queue_rerun_requested:
            continue
        if fn() > 0:
            names.append(name)
    return names


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
    if ready_count_backfill() > 0:
        trigger_queue("backfill")


async def handle_cloud_presence_queue() -> None:
    generation = get_runtime_generation()
    for _ in range(12):
        done, failed = await process_cloud_presence_tasks(get_settings())
        if not runtime_generation_alive(generation):
            return
        if done == 0 and failed == 0:
            break
        if ready_count_cloud_presence() <= 0:
            break
    if ready_count_cloud_presence() > 0:
        trigger_queue("cloud_presence")


async def handle_download_enqueue_queue() -> None:
    generation = get_runtime_generation()
    for _ in range(12):
        done, failed = await process_download_enqueue_tasks(get_settings())
        if not runtime_generation_alive(generation):
            return
        if done == 0 and failed == 0:
            break
        if ready_count_download_enqueue() <= 0:
            break
    if ready_count_download_enqueue() > 0:
        trigger_queue("download_enqueue")


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
    if ready_count_cloud_asset() > 0:
        trigger_queue("cloud_asset")


async def handle_sync_plan_queue() -> None:
    settings = get_settings()
    completed, failed = await process_sync_plan_tasks(settings)
    if completed or failed:
        log("info", f"同步计划已更新: 完成 {completed} 个，失败 {failed} 个")
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


async def handle_nfo_queue() -> None:
    generation = get_runtime_generation()
    for _ in range(12):
        before = ready_count_nfo()
        await process_nfo_tasks(get_settings())
        if not runtime_generation_alive(generation):
            return
        after = ready_count_nfo()
        if before == 0 or after >= before:
            break
        if after <= 0:
            break
    if ready_count_nfo() > 0:
        trigger_queue("nfo")


async def handle_local_presence_queue() -> None:
    generation = get_runtime_generation()
    for _ in range(12):
        before = ready_count_local_presence()
        await process_local_presence_tasks(get_settings())
        if not runtime_generation_alive(generation):
            return
        after = ready_count_local_presence()
        if before == 0 or after >= before:
            break
        if after <= 0:
            break
    if ready_count_local_presence() > 0:
        trigger_queue("local_presence")


async def handle_cleanup_queue() -> None:
    generation = get_runtime_generation()
    for _ in range(4):
        with connect() as conn:
            conn.execute(
                """
                UPDATE cleanup_tasks
                SET status='running', attempts=attempts+1, updated_at=?
                WHERE task_scope='runtime'
                """,
                (now(),),
            )
        try:
            stats = run_cleanup_tasks()
            with connect() as conn:
                conn.execute(
                    """
                    UPDATE cleanup_tasks
                    SET status='pending', retry_after=?, last_error=?, updated_at=?
                    WHERE task_scope='runtime'
                    """,
                    ((datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(), f"已清理 operations {stats['deleted_operations']} 条，已裁剪完成任务 {stats['trimmed_task_rows']} 条", now()),
                )
            if not runtime_generation_alive(generation):
                return
            break
        except Exception as exc:
            with connect() as conn:
                conn.execute(
                    """
                    UPDATE cleanup_tasks
                    SET status='failed', retry_after=?, last_error=?, updated_at=?
                    WHERE task_scope='runtime'
                    """,
                    ((datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(), str(exc)[:2000], now()),
                )
            log("error", f"清理任务失败: {exc}")
            break


async def run_scan_source(settings: dict[str, str], operation_id: int | None = None) -> str:
    generation = get_runtime_generation()
    reclaimed_mikan = reclaim_mikan_match_tasks(now())
    if operation_id:
        update_operation(operation_id, "正在扫描 RSS 源并写入候选")
    log("info", "扫描全部: 开始扫描 RSS 源")
    scan_message = await scan_and_queue(settings)
    repaired_mikan = enqueue_missing_mikan_match_tasks(now())
    if not runtime_generation_alive(generation):
        return "运行数据已重置，本次扫描已中止"
    if operation_id:
        update_operation(operation_id, "RSS 已写入，后续由任务链自动推进")
    trigger_queue("cleanup", delay=0)
    message = f"{scan_message}；回收 Mikan 运行中任务 {reclaimed_mikan} 个；补排 Mikan 匹配 {repaired_mikan} 个；后续由任务链自动推进"
    log("info", f"扫描全部: RSS 完成，{message}")
    return message


def ensure_queue_handlers() -> None:
    queue_handlers.clear()
    queue_handlers.update(
        {
            "mikan_match": handle_mikan_match_queue,
            "metadata": handle_metadata_queue,
            "selection": handle_selection_queue,
            "backfill": handle_backfill_queue,
            "cloud_presence": handle_cloud_presence_queue,
            "download_enqueue": handle_download_enqueue_queue,
            "download": handle_download_queue,
            "cloud_poll": handle_cloud_poll_queue,
            "cloud_asset": handle_cloud_asset_queue,
            "sync_plan": handle_sync_plan_queue,
            "sync": handle_sync_queue,
            "nfo": handle_nfo_queue,
            "local_presence": handle_local_presence_queue,
            "cleanup": handle_cleanup_queue,
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
        mark_scheduled_job(queue_job_key(name), last_status="rerun_pending", updated_at=now())
        return
    queue_debounce_tasks.pop(name, None)
    queue_running.add(name)
    run_id = start_scheduled_job_run(queue_job_key(name), "event", f"执行队列 {name}")
    run_status = "completed"
    run_message = f"队列 {name} 执行完成"
    try:
        await handler()
    except Exception as exc:
        log("error", f"队列处理失败[{name}]: {exc}")
        run_status = "failed"
        run_message = str(exc)
    finally:
        queue_running.discard(name)
        if run_id:
            finish_scheduled_job_run(run_id, run_status, run_message)
        if name in queue_rerun_requested:
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
        mark_scheduled_job(queue_job_key(name), last_status="rerun_pending", debounce_seconds=int(QUEUE_DEBOUNCE_SECONDS), updated_at=now())
        return
    pending_task = queue_debounce_tasks.get(name)
    if pending_task and not pending_task.done():
        pending_task.cancel()
    actual_delay = QUEUE_DEBOUNCE_SECONDS if delay is None else max(0.0, delay)
    mark_scheduled_job(
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
            mark_scheduled_job(queue_job_key(name), last_status="debouncing", updated_at=now())
            return

    queue_debounce_tasks[name] = loop.create_task(runner())


def trigger_queues(names: list[str], delay: float | None = None) -> None:
    seen: set[str] = set()
    for name in names:
        name = canonical_queue_key(name)
        if name in seen:
            continue
        seen.add(name)
        trigger_queue(name, delay=delay)


async def dispatch_ready_queues() -> None:
    run_id = start_scheduled_job_run("queue_dispatch", "system", "恢复挂起队列")
    try:
        names = recoverable_queue_names()
        trigger_queues(names, delay=0)
        finish_scheduled_job_run(run_id, "completed", f"已恢复触发队列: {', '.join(names) if names else '无'}")
    except Exception as exc:
        finish_scheduled_job_run(run_id, "failed", str(exc))
        raise


def reschedule() -> None:
    scheduler.remove_all_jobs()
    ensure_queue_handlers()
    settings = get_settings()
    minutes = max(1, int(settings.get("scan_interval_minutes") or 60))
    mark_scheduled_job("rss_scan", interval_minutes=minutes, updated_at=now())
    mark_scheduled_job("queue_dispatch", interval_minutes=1, debounce_seconds=int(QUEUE_DEBOUNCE_SECONDS), updated_at=now())
    for name in queue_handlers:
        mark_scheduled_job(queue_job_key(name), debounce_seconds=int(QUEUE_DEBOUNCE_SECONDS), updated_at=now())
    scheduler.add_job(lambda: asyncio.create_task(scheduled_scan()), "interval", minutes=minutes, id="rss_scan")
    scheduler.add_job(lambda: asyncio.create_task(dispatch_ready_queues()), "interval", minutes=1, id="queue_dispatch")


def runtime_generation_alive(expected: str) -> bool:
    return get_runtime_generation() == expected


async def scheduled_scan() -> None:
    run_id = start_scheduled_job_run("rss_scan", "system", "定时 RSS 扫描")
    settings = get_settings()
    try:
        if bool_setting(settings.get("auto_scan", "false")):
            message = await scan_and_queue(settings)
            finish_scheduled_job_run(run_id, "completed", message)
        else:
            finish_scheduled_job_run(run_id, "completed", "已关闭自动 RSS 扫描")
    except Exception as exc:
        finish_scheduled_job_run(run_id, "failed", str(exc))
        raise


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
        cloud_presence_rows = {
            row["status"]: row["count"]
            for row in conn.execute(
                "SELECT status, COUNT(*) AS count FROM cloud_presence_tasks GROUP BY status"
            ).fetchall()
        }
        download_enqueue_rows = {
            row["status"]: row["count"]
            for row in conn.execute(
                "SELECT status, COUNT(*) AS count FROM download_enqueue_tasks GROUP BY status"
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
        cloud_presence_retry = conn.execute(
            """
            SELECT COUNT(*) AS count, MIN(retry_after) AS next_retry_after
            FROM cloud_presence_tasks
            WHERE status IN ('pending', 'failed') AND retry_after != '' AND retry_after > ?
            """,
            (now(),),
        ).fetchone()
        download_enqueue_retry = conn.execute(
            """
            SELECT COUNT(*) AS count, MIN(retry_after) AS next_retry_after
            FROM download_enqueue_tasks
            WHERE status IN ('pending', 'failed') AND retry_after != '' AND retry_after > ?
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
        nfo_retry = conn.execute(
            """
            SELECT COUNT(*) AS count, MIN(retry_after) AS next_retry_after
            FROM nfo_tasks
            WHERE status IN ('pending', 'failed') AND retry_after != '' AND retry_after > ?
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
        sync_plan_rows = {
            row["status"]: row["count"]
            for row in conn.execute(
                "SELECT status, COUNT(*) AS count FROM sync_plan_tasks GROUP BY status"
            ).fetchall()
        }
        nfo_rows = {
            row["status"]: row["count"]
            for row in conn.execute(
                "SELECT status, COUNT(*) AS count FROM nfo_tasks GROUP BY status"
            ).fetchall()
        }
        local_presence_rows = {
            row["status"]: row["count"]
            for row in conn.execute(
                "SELECT status, COUNT(*) AS count FROM local_presence_tasks GROUP BY status"
            ).fetchall()
        }
        cleanup_rows = {
            row["status"]: row["count"]
            for row in conn.execute(
                "SELECT status, COUNT(*) AS count FROM cleanup_tasks GROUP BY status"
            ).fetchall()
        }
        cleanup_retry = conn.execute(
            """
            SELECT COUNT(*) AS count, MIN(retry_after) AS next_retry_after
            FROM cleanup_tasks
            WHERE status IN ('pending', 'failed') AND retry_after != '' AND retry_after > ?
            """,
            (now(),),
        ).fetchone()
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
    items = [
        {
            "key": "rss",
            "name": "Mikan RSS",
            "pending": 1 if bool_setting(settings.get("auto_scan", "false")) else 0,
            "running": 0,
            "failed": 0,
            "description": "周期读取 RSS，新增发布后进入后续队列",
            "queue_state": "scheduled" if bool_setting(settings.get("auto_scan", "false")) else "disabled",
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
            "key": "cloud_presence",
            "name": "云盘存在性检查",
            "pending": cloud_presence_rows.get("pending", 0),
            "running": cloud_presence_rows.get("running", 0),
            "failed": cloud_presence_rows.get("failed", 0),
            "waiting": cloud_presence_retry["count"] if cloud_presence_retry else 0,
            "next_retry_after": cloud_presence_retry["next_retry_after"] if cloud_presence_retry else "",
            "next_retry_seconds": seconds_until(cloud_presence_retry["next_retry_after"] if cloud_presence_retry else ""),
            "description": "先检查云盘是否已有该集，避免重复提交下载",
        },
        {
            "key": "download_enqueue",
            "name": "下载准备",
            "pending": download_enqueue_rows.get("pending", 0),
            "running": download_enqueue_rows.get("running", 0),
            "failed": download_enqueue_rows.get("failed", 0),
            "waiting": download_enqueue_retry["count"] if download_enqueue_retry else 0,
            "next_retry_after": download_enqueue_retry["next_retry_after"] if download_enqueue_retry else "",
            "next_retry_seconds": seconds_until(download_enqueue_retry["next_retry_after"] if download_enqueue_retry else ""),
            "description": "在确认未存在后，生成 download_tasks 与 cloud_submissions",
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
            "description": "把完成的 PikPak 任务登记成云盘资源，并兼容补齐极少数漏登记项",
        },
        {
            "key": "sync_plan",
            "name": "同步计划",
            "pending": sync_plan_rows.get("pending", 0),
            "running": sync_plan_rows.get("running", 0),
            "failed": sync_plan_rows.get("failed", 0),
            "waiting": 0,
            "next_retry_after": "",
            "next_retry_seconds": 0,
            "description": "根据同步意图和云盘资源状态生成本地同步任务",
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
        {
            "key": "nfo",
            "name": "NFO",
            "pending": nfo_rows.get("pending", 0),
            "running": nfo_rows.get("running", 0),
            "failed": nfo_rows.get("failed", 0),
            "waiting": nfo_retry["count"] if nfo_retry else 0,
            "next_retry_after": nfo_retry["next_retry_after"] if nfo_retry else "",
            "next_retry_seconds": seconds_until(nfo_retry["next_retry_after"] if nfo_retry else ""),
            "description": "本地同步完成后独立生成 NFO，避免和同步执行器耦合",
        },
        {
            "key": "local_presence",
            "name": "本地存在性检查",
            "pending": local_presence_rows.get("pending", 0),
            "running": local_presence_rows.get("running", 0),
            "failed": local_presence_rows.get("failed", 0),
            "waiting": 0,
            "next_retry_after": "",
            "next_retry_seconds": 0,
            "description": "独立检查本地文件和 NFO 是否仍然存在，纠正绕过系统的删除",
        },
        {
            "key": "cleanup",
            "name": "清理",
            "pending": cleanup_rows.get("pending", 0),
            "running": cleanup_rows.get("running", 0),
            "failed": cleanup_rows.get("failed", 0),
            "waiting": cleanup_retry["count"] if cleanup_retry else 0,
            "next_retry_after": cleanup_retry["next_retry_after"] if cleanup_retry else "",
            "next_retry_seconds": seconds_until(cleanup_retry["next_retry_after"] if cleanup_retry else ""),
            "description": "独立清理已完成操作、历史队列项和运行期残留状态",
        },
    ]
    for item in items:
        key = str(item["key"])
        runtime_key = canonical_queue_key(key)
        running = int(item.get("running", 0) or 0)
        pending = int(item.get("pending", 0) or 0)
        failed = int(item.get("failed", 0) or 0)
        waiting = int(item.get("waiting", 0) or 0)
        if runtime_key in queue_running or running > 0:
            item["queue_state"] = "running"
            item["state_reason"] = "队列正在处理当前批次任务"
        elif runtime_key in queue_rerun_requested:
            item["queue_state"] = "rerun_pending"
            item["state_reason"] = "当前批次结束后会立刻重跑"
        elif pending > 0:
            task = queue_debounce_tasks.get(runtime_key)
            if task and not task.done():
                item["queue_state"] = "debouncing"
                item["state_reason"] = f"检测到新任务，等待 {int(QUEUE_DEBOUNCE_SECONDS)} 秒聚合后自动执行"
            else:
                item["queue_state"] = "ready"
                item["state_reason"] = "已有待处理任务，可立即执行"
        elif waiting > 0:
            item["queue_state"] = "cooldown"
            next_retry_seconds = int(item.get("next_retry_seconds", 0) or 0)
            item["state_reason"] = f"任务正在等待重试，最近一次将在 {next_retry_seconds} 秒后恢复"
        elif failed > 0:
            item["queue_state"] = "failed"
            item["state_reason"] = "存在失败任务，等待重试或人工处理"
        else:
            item["queue_state"] = item.get("queue_state", "idle")
            item["state_reason"] = "当前没有可处理任务"
        item["runtime_queue_key"] = runtime_key
        if item["queue_state"] == "ready" and pending > 0:
            item["state_detail"] = f"当前批次可执行 {pending} 个"
        elif item["queue_state"] == "debouncing":
            item["state_detail"] = "检测到连续入队，正在聚合这一批任务"
        elif item["queue_state"] == "cooldown":
            item["state_detail"] = f"等待中的任务 {waiting} 个"
        elif item["queue_state"] == "running":
            item["state_detail"] = f"当前运行 {running} 个，待处理 {pending} 个"
        elif item["queue_state"] == "failed":
            item["state_detail"] = f"失败任务 {failed} 个"
        else:
            item["state_detail"] = ""
    return items


def console_overview(
    queue_items: list[dict[str, Any]],
    scheduled_jobs: list[dict[str, Any]],
    operations: list[dict[str, Any]],
    server_logs: list[str],
) -> dict[str, Any]:
    queue_total = len(queue_items)
    running_queue_count = sum(1 for item in queue_items if item.get("queue_state") == "running" or int(item.get("running", 0) or 0) > 0)
    pending_queue_count = sum(1 for item in queue_items if int(item.get("pending", 0) or 0) > 0)
    failed_queue_count = sum(1 for item in queue_items if int(item.get("failed", 0) or 0) > 0)
    waiting_retry_count = sum(int(item.get("waiting", 0) or 0) for item in queue_items)
    pending_task_count = sum(int(item.get("pending", 0) or 0) for item in queue_items)
    running_task_count = sum(int(item.get("running", 0) or 0) for item in queue_items)
    failed_task_count = sum(int(item.get("failed", 0) or 0) for item in queue_items)
    running_operation_count = sum(1 for item in operations if str(item.get("status", "")) == "running")
    failed_operation_count = sum(1 for item in operations if str(item.get("status", "")) == "failed")
    scheduled_failed_count = sum(1 for item in scheduled_jobs if str(item.get("last_status", "")) == "failed")
    scheduled_running_count = sum(1 for item in scheduled_jobs if str(item.get("last_status", "")) == "running")
    recent_error_count = sum(1 for line in server_logs if "[ERROR]" in str(line))
    recent_warn_count = sum(1 for line in server_logs if "[WARN]" in str(line))
    active_queue_names = [
        str(item.get("name", ""))
        for item in queue_items
        if item.get("queue_state") in {"running", "debouncing", "rerun_pending", "cooldown"}
        or int(item.get("pending", 0) or 0) > 0
        or int(item.get("failed", 0) or 0) > 0
    ][:6]
    return {
        "queue_total": queue_total,
        "running_queue_count": running_queue_count,
        "pending_queue_count": pending_queue_count,
        "failed_queue_count": failed_queue_count,
        "waiting_retry_count": waiting_retry_count,
        "pending_task_count": pending_task_count,
        "running_task_count": running_task_count,
        "failed_task_count": failed_task_count,
        "running_operation_count": running_operation_count,
        "failed_operation_count": failed_operation_count,
        "scheduled_running_count": scheduled_running_count,
        "scheduled_failed_count": scheduled_failed_count,
        "recent_error_count": recent_error_count,
        "recent_warn_count": recent_warn_count,
        "active_queue_names": active_queue_names,
    }


def scheduled_jobs_summary() -> list[dict[str, Any]]:
    with connect() as conn:
        jobs = conn.execute(
            """
            SELECT *
            FROM scheduled_jobs
            ORDER BY job_key ASC
            """
        ).fetchall()
        latest_runs = conn.execute(
            """
            SELECT r.*
            FROM scheduled_job_runs r
            JOIN (
              SELECT job_id, MAX(id) AS max_id
              FROM scheduled_job_runs
              GROUP BY job_id
            ) latest ON latest.max_id=r.id
            ORDER BY r.id DESC
            """
        ).fetchall()
    run_map = {int(row["job_id"]): dict(row) for row in latest_runs}
    result = []
    for job in jobs:
        item = dict(job)
        latest = run_map.get(int(job["id"]), {})
        item["latest_run"] = latest
        result.append(item)
    return result


def queue_detail_map() -> dict[str, dict[str, Any]]:
    with connect() as conn:
        details: dict[str, dict[str, Any]] = {}
        details["mikan_match"] = {
            "items": enrich_retry_rows(
                conn.execute(
                    """
                    SELECT mt.*, rc.title, rc.series_title, rc.reason
                    FROM mikan_match_tasks mt
                    JOIN rss_candidates rc ON rc.id=mt.candidate_id
                    ORDER BY mt.updated_at DESC
                    LIMIT 120
                    """
                ).fetchall()
            )
        }
        details["metadata"] = {
            "items": enrich_retry_rows(
                conn.execute(
                    """
                    SELECT mt.*, rc.title, rc.series_title, rc.reason, rc.bangumi_id
                    FROM metadata_tasks mt
                    JOIN rss_candidates rc ON rc.id=mt.candidate_id
                    ORDER BY mt.updated_at DESC
                    LIMIT 120
                    """
                ).fetchall()
            )
        }
        details["selection"] = {
            "items": enrich_retry_rows(
                conn.execute(
                    """
                    SELECT st.*, e.display_title AS title_cn, e.selected_group, e.selected_resolution, e.domain_kind
                    FROM selection_tasks st
                    JOIN entries e ON e.id=st.entry_id
                    WHERE COALESCE(e.hidden, 0)=0
                    ORDER BY st.updated_at DESC
                    LIMIT 120
                    """
                ).fetchall()
            )
        }
        details["backfill"] = {
            "items": enrich_retry_rows(
                conn.execute(
                    """
                    SELECT bt.*, e.display_title AS title_cn, e.bangumi_id, e.mikan_bangumi_id, e.domain_kind
                    FROM backfill_tasks bt
                    JOIN entries e ON e.id=bt.entry_id
                    WHERE COALESCE(e.hidden, 0)=0
                    ORDER BY bt.updated_at DESC
                    LIMIT 120
                    """
                ).fetchall()
            )
        }
        details["cloud_presence"] = {"items": enrich_retry_rows(
            conn.execute(
                """
                SELECT cpt.*, e.display_title AS title_cn, e.domain_kind, r.title AS release_title
                FROM cloud_presence_tasks cpt
                JOIN releases r ON r.id=cpt.release_id
                JOIN entries e ON e.id=cpt.entry_id
                ORDER BY cpt.updated_at DESC
                LIMIT 120
                """
            ).fetchall()
        )}
        details["download_enqueue"] = {"items": enrich_retry_rows(
            conn.execute(
                """
                SELECT det.*, e.display_title AS title_cn, e.domain_kind, r.title AS release_title
                FROM download_enqueue_tasks det
                JOIN releases r ON r.id=det.release_id
                JOIN entries e ON e.id=det.entry_id
                ORDER BY det.updated_at DESC
                LIMIT 120
                """
            ).fetchall()
        )}
        details["cloud"] = {"items": enrich_download_tasks(
            conn.execute(
                """
                SELECT dt.*, e.display_title AS title_cn, e.domain_kind, r.episode_number, r.subtitle_group, r.resolution, r.language, r.title AS release_title
                FROM download_tasks dt
                JOIN entries e ON e.id=dt.entry_id
                JOIN releases r ON r.id=dt.release_id
                WHERE e.bangumi_id != ''
                ORDER BY dt.updated_at DESC
                LIMIT 120
                """
            ).fetchall()
        )}
        details["cloud_poll"] = {"items": enrich_retry_rows(
            conn.execute(
                """
                SELECT cpt.*, dt.series_id, dt.entry_id, dt.release_id, dt.pikpak_task_id, dt.pikpak_file_id,
                       e.display_title AS title_cn, e.domain_kind, r.episode_number, r.title AS release_title
                FROM cloud_poll_tasks cpt
                JOIN download_tasks dt ON dt.id=cpt.download_task_id
                JOIN entries e ON e.id=dt.entry_id
                JOIN releases r ON r.id=dt.release_id
                ORDER BY cpt.updated_at DESC
                LIMIT 120
                """
            ).fetchall()
        )}
        details["cloud_assets"] = {"items": enrich_retry_rows(
            conn.execute(
                """
                SELECT cat.*, dt.series_id, dt.entry_id, dt.release_id, dt.pikpak_file_id, e.display_title AS title_cn, e.domain_kind, r.episode_number, r.title AS release_title
                FROM cloud_asset_tasks cat
                JOIN download_tasks dt ON dt.id=cat.download_task_id
                JOIN entries e ON e.id=dt.entry_id
                JOIN releases r ON r.id=dt.release_id
                ORDER BY cat.updated_at DESC
                LIMIT 120
                """
            ).fetchall()
        )}
        details["sync_plan"] = {"items": enrich_retry_rows(
            conn.execute(
                """
                SELECT spt.*, e.display_title AS title_cn, e.domain_kind
                FROM sync_plan_tasks spt
                JOIN entries e ON e.id=spt.entry_id
                ORDER BY spt.updated_at DESC
                LIMIT 120
                """
            ).fetchall()
        )}
        details["sync"] = {"items": enrich_retry_rows(
            conn.execute(
                """
                SELECT st.*, e.display_title AS title_cn, e.domain_kind, ca.cloud_name, ca.provider_file_id
                FROM sync_tasks st
                JOIN entries e ON e.id=st.entry_id
                JOIN cloud_assets ca ON ca.id=st.cloud_asset_id
                WHERE e.bangumi_id != ''
                ORDER BY st.updated_at DESC
                LIMIT 120
                """
            ).fetchall()
        )}
        details["nfo"] = {"items": enrich_retry_rows(
            conn.execute(
                """
                SELECT nt.*, e.display_title AS title_cn, e.domain_kind, la.local_path
                FROM nfo_tasks nt
                JOIN entries e ON e.id=nt.entry_id
                JOIN local_assets la ON la.id=nt.local_asset_id
                ORDER BY nt.updated_at DESC
                LIMIT 120
                """
            ).fetchall()
        )}
        details["local_presence"] = {"items": enrich_retry_rows(
            conn.execute(
                """
                SELECT lpt.*, e.display_title AS title_cn, e.domain_kind, la.local_path, la.nfo_status
                FROM local_presence_tasks lpt
                JOIN entries e ON e.id=lpt.entry_id
                JOIN local_assets la ON la.id=lpt.local_asset_id
                ORDER BY lpt.updated_at DESC
                LIMIT 120
                """
            ).fetchall()
        )}
        details["cleanup"] = {"items": enrich_retry_rows(
            conn.execute(
                """
                SELECT *
                FROM cleanup_tasks
                ORDER BY updated_at DESC
                LIMIT 120
                """
            ).fetchall()
        )}
        details["rss"] = {"items": rows_to_dicts(
            conn.execute(
                """
                SELECT *
                FROM rss_candidates
                ORDER BY updated_at DESC
                LIMIT 120
                """
            ).fetchall()
        )}
    return details


def console_sections() -> list[dict[str, Any]]:
    return [
        {"key": "queues", "name": "队列", "kind": "group"},
        {"key": "queue:rss", "name": "RSS 扫描", "kind": "queue", "queue_key": "rss"},
        {"key": "queue:mikan_match", "name": "Mikan 匹配", "kind": "queue", "queue_key": "mikan_match"},
        {"key": "queue:metadata", "name": "元数据", "kind": "queue", "queue_key": "metadata"},
        {"key": "queue:selection", "name": "自动选集", "kind": "queue", "queue_key": "selection"},
        {"key": "queue:backfill", "name": "整季补全", "kind": "queue", "queue_key": "backfill"},
        {"key": "queue:cloud_presence", "name": "云盘存在性检查", "kind": "queue", "queue_key": "cloud_presence"},
        {"key": "queue:download_enqueue", "name": "下载准备", "kind": "queue", "queue_key": "download_enqueue"},
        {"key": "queue:cloud", "name": "PikPak 入库", "kind": "queue", "queue_key": "cloud"},
        {"key": "queue:cloud_poll", "name": "PikPak 状态", "kind": "queue", "queue_key": "cloud_poll"},
        {"key": "queue:cloud_assets", "name": "云盘资源登记", "kind": "queue", "queue_key": "cloud_assets"},
        {"key": "queue:sync_plan", "name": "同步计划", "kind": "queue", "queue_key": "sync_plan"},
        {"key": "queue:sync", "name": "本地同步", "kind": "queue", "queue_key": "sync"},
        {"key": "queue:nfo", "name": "NFO", "kind": "queue", "queue_key": "nfo"},
        {"key": "queue:local_presence", "name": "本地存在性检查", "kind": "queue", "queue_key": "local_presence"},
        {"key": "queue:cleanup", "name": "清理", "kind": "queue", "queue_key": "cleanup"},
        {"key": "scheduler", "name": "定时任务", "kind": "group"},
        {"key": "scheduler:rss_scan", "name": "RSS 定时扫描", "kind": "scheduled", "job_key": "rss_scan"},
        {"key": "scheduler:queue_dispatch", "name": "恢复调度", "kind": "scheduled", "job_key": "queue_dispatch"},
        {"key": "operations", "name": "运行操作", "kind": "operations"},
        {"key": "logs", "name": "服务日志", "kind": "logs"},
        {"key": "maintenance", "name": "维护", "kind": "maintenance"},
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
    recent_cutoff = datetime.now(timezone.utc).timestamp() - 7 * 24 * 60 * 60
    with connect() as conn:
        seasonal_items = conn.execute(
            """
            SELECT e.id,
              e.work_id,
              e.display_title,
              e.title_root,
              e.entry_kind,
              e.season_label,
              e.arc_label,
              e.part_label,
              e.special_label,
              e.title_cn,
              e.bangumi_id,
              e.year,
              e.season_number,
              w.title_root AS work_title,
              COUNT(DISTINCT ep.id) AS episode_count,
              COUNT(DISTINCT r.id) AS release_count,
              COUNT(DISTINCT r.subtitle_group) AS group_count,
              COUNT(DISTINCT r.resolution) AS resolution_count,
              COUNT(DISTINCT r.language) AS language_count,
              COUNT(DISTINCT CASE WHEN dt.status IN ('submitted','completed') THEN dt.id END) AS downloaded_count,
              COUNT(DISTINCT ca.id) AS cloud_asset_count,
              COUNT(DISTINCT la.id) AS local_asset_count,
              COALESCE(MAX(sr.sync_enabled), 0) AS sync_enabled
            FROM entries e
            JOIN seasonal_entries se ON se.entry_id=e.id
            JOIN works w ON w.id=e.work_id
            LEFT JOIN episodes ep ON ep.entry_id=e.id
            LEFT JOIN releases r ON r.entry_id=e.id
            LEFT JOIN download_tasks dt ON dt.release_id=r.id
            LEFT JOIN cloud_assets ca ON ca.release_id=r.id
            LEFT JOIN local_assets la ON la.release_id=r.id AND la.status='synced'
            LEFT JOIN sync_rules sr ON sr.entry_id=e.id
            WHERE COALESCE(e.hidden, 0)=0
              AND e.bangumi_id != ''
            GROUP BY e.id
            ORDER BY e.updated_at DESC
            """
        ).fetchall()
        library_items = conn.execute(
            """
            SELECT e.id,
              e.work_id,
              e.display_title,
              e.title_root,
              e.entry_kind,
              e.season_label,
              e.arc_label,
              e.part_label,
              e.special_label,
              e.title_cn,
              e.bangumi_id,
              e.year,
              e.season_number,
              w.title_root AS work_title,
              COUNT(DISTINCT ep.id) AS episode_count,
              COUNT(DISTINCT r.id) AS release_count,
              COUNT(DISTINCT ca.id) AS cloud_asset_count,
              COUNT(DISTINCT la.id) AS local_asset_count
            FROM entries e
            JOIN library_entries le ON le.entry_id=e.id
            JOIN works w ON w.id=e.work_id
            LEFT JOIN episodes ep ON ep.entry_id=e.id
            LEFT JOIN releases r ON r.entry_id=e.id
            LEFT JOIN cloud_assets ca ON ca.release_id=r.id
            LEFT JOIN local_assets la ON la.release_id=r.id AND la.status='synced'
            WHERE COALESCE(e.hidden, 0)=0
            GROUP BY e.id
            ORDER BY e.updated_at DESC
            """
        ).fetchall()
        library_summary_row = conn.execute(
            """
            SELECT
              COUNT(DISTINCT w.id) AS work_count,
              COUNT(DISTINCT e.id) AS entry_count,
              COUNT(DISTINCT CASE WHEN COALESCE(e.bangumi_id, '')='' THEN e.id END) AS unmatched_count,
              COUNT(DISTINCT ca.id) AS cloud_asset_count,
              COUNT(DISTINCT la.id) AS local_asset_count
            FROM entries e
            JOIN library_entries le ON le.entry_id=e.id
            JOIN works w ON w.id=e.work_id
            LEFT JOIN cloud_assets ca ON ca.entry_id=e.id
            LEFT JOIN local_assets la ON la.entry_id=e.id AND la.status='synced'
            WHERE COALESCE(e.hidden, 0)=0
            """
        ).fetchone()
        library_failed_row = conn.execute(
            """
            SELECT COUNT(DISTINCT entry_id) AS failed_entry_count
            FROM (
              SELECT dt.entry_id AS entry_id
              FROM download_tasks dt
              JOIN library_entries le ON le.entry_id=dt.entry_id
              WHERE dt.status='failed'
              UNION
              SELECT st.entry_id AS entry_id
              FROM sync_tasks st
              JOIN library_entries le ON le.entry_id=st.entry_id
              WHERE st.status='failed'
              UNION
              SELECT bt.entry_id AS entry_id
              FROM backfill_tasks bt
              JOIN library_entries le ON le.entry_id=bt.entry_id
              WHERE bt.status='failed'
            ) failed_entries
            """
        ).fetchone()
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
        scheduled_runs = conn.execute(
            """
            SELECT r.*, j.job_key, j.job_type
            FROM scheduled_job_runs r
            JOIN scheduled_jobs j ON j.id=r.job_id
            ORDER BY r.id DESC
            LIMIT 40
            """
        ).fetchall()
        sync_rules = conn.execute(
            """
            SELECT sr.*, e.display_title AS title_cn
            FROM sync_rules sr
            JOIN entries e ON e.id=sr.entry_id
            WHERE COALESCE(e.hidden, 0)=0
              AND e.bangumi_id != ''
            ORDER BY sr.updated_at DESC
            LIMIT 200
            """
        ).fetchall()
        seasonal_sync_calendar = conn.execute(
            """
            SELECT la.id,
              la.local_path,
              la.updated_at AS synced_at,
              ca.episode_number,
              e.display_title,
              e.title_root,
              e.entry_kind,
              e.season_label,
              e.arc_label,
              e.part_label,
              e.special_label,
              w.title_root AS work_title
            FROM local_assets la
            JOIN cloud_assets ca ON ca.id=la.cloud_asset_id
            JOIN releases r ON r.id=la.release_id
            JOIN entries e ON e.id=r.entry_id
            JOIN seasonal_entries se ON se.entry_id=e.id
            JOIN works w ON w.id=e.work_id
            WHERE la.status='synced'
              AND COALESCE(e.hidden, 0)=0
              AND strftime('%s', la.updated_at) >= ?
            ORDER BY la.updated_at DESC
            LIMIT 120
            """,
            (int(recent_cutoff),),
        ).fetchall()
    queue_items = queue_summary(settings)
    scheduled_jobs = scheduled_jobs_summary()
    server_logs = read_server_logs(160)
    operations_list = rows_to_dicts(operations)
    return {
        "seasonal_items": rows_to_dicts(seasonal_items),
        "library_items": rows_to_dicts(library_items),
        "library_summary": {
            "work_count": int((library_summary_row["work_count"] if library_summary_row else 0) or 0),
            "entry_count": int((library_summary_row["entry_count"] if library_summary_row else 0) or 0),
            "unmatched_count": int((library_summary_row["unmatched_count"] if library_summary_row else 0) or 0),
            "cloud_asset_count": int((library_summary_row["cloud_asset_count"] if library_summary_row else 0) or 0),
            "local_asset_count": int((library_summary_row["local_asset_count"] if library_summary_row else 0) or 0),
            "failed_entry_count": int((library_failed_row["failed_entry_count"] if library_failed_row else 0) or 0),
        },
        "seasonal_sync_calendar": rows_to_dicts(seasonal_sync_calendar),
        "sync_rules": rows_to_dicts(sync_rules),
        "operations": operations_list,
        "scheduled_jobs": scheduled_jobs,
        "scheduled_runs": rows_to_dicts(scheduled_runs),
        "queue_summary": queue_items,
        "queue_details": queue_detail_map(),
        "console_sections": console_sections(),
        "server_logs": server_logs,
        "console_overview": console_overview(queue_items, scheduled_jobs, operations_list, server_logs),
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
            entry_rows = conn.execute("SELECT id, (SELECT series_id FROM releases r WHERE r.entry_id=e.id ORDER BY id ASC LIMIT 1) AS series_id FROM entries e WHERE COALESCE(hidden, 0)=0 AND bangumi_id != ''").fetchall()
            for row in entry_rows:
                enqueue_selection_task(conn, int(row["series_id"] or 0), int(row["id"]), ts, "全局规则变更，重新计算自动选集")
                enqueue_backfill_task(conn, int(row["series_id"] or 0), int(row["id"]), current, ts)
    reschedule()
    log("info", "全局设置已保存")
    return settings_response()


@app.get("/api/series/{series_id}", deprecated=True)
async def api_series(series_id: int) -> dict[str, Any]:
    # Legacy compatibility alias for old clients; primary frontend now uses /api/seasonal/*.
    return build_entry_response(series_id)


@app.put("/api/series/{series_id}", deprecated=True)
async def api_update_series(series_id: int, payload: SeriesPayload) -> dict[str, Any]:
    # Legacy compatibility alias for old clients; primary frontend now uses /api/seasonal/*.
    return save_entry_payload(series_id, payload)


@app.get("/api/seasonal/{entry_id}")
async def api_seasonal_entry(entry_id: int) -> dict[str, Any]:
    return build_entry_response(entry_id)


@app.put("/api/seasonal/{entry_id}")
async def api_update_seasonal_entry(entry_id: int, payload: SeriesPayload) -> dict[str, Any]:
    return save_entry_payload(entry_id, payload, expected_domain="seasonal")


@app.get("/api/library/{entry_id}")
async def api_library_entry(entry_id: int) -> dict[str, Any]:
    return build_entry_response(entry_id)


@app.put("/api/library/{entry_id}")
async def api_update_library_entry(entry_id: int, payload: SeriesPayload) -> dict[str, Any]:
    return save_entry_payload(entry_id, payload, expected_domain="library")


@app.delete("/api/series/{series_id}", deprecated=True)
async def api_delete_series(series_id: int) -> dict[str, str]:
    # Legacy compatibility alias for old clients; primary frontend now uses /api/seasonal/*.
    return hide_entry(
        series_id,
        success_message="已隐藏误识别番剧，关联记录已保留",
        log_prefix="已隐藏误识别番剧",
    )


@app.delete("/api/seasonal/{entry_id}")
async def api_delete_seasonal_entry(entry_id: int) -> dict[str, str]:
    return hide_entry(
        entry_id,
        expected_domain="seasonal",
        success_message="已隐藏误识别条目，关联记录已保留",
        log_prefix="已隐藏新番条目",
    )


@app.delete("/api/library/{entry_id}")
async def api_delete_library_entry(entry_id: int) -> dict[str, str]:
    return hide_entry(
        entry_id,
        expected_domain="library",
        success_message="已隐藏番剧库条目，关联记录已保留",
        log_prefix="已隐藏番剧库条目",
    )


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
        lambda op_id: run_scan_source(get_settings(), op_id),
        "正在扫描 RSS，并触发 Mikan 匹配、元数据、入云盘和本地同步队列",
    )
    return {"status": "started", "operation_id": str(operation_id), "message": "扫描全部已启动"}


@app.post("/api/queues/{queue_name}/trigger")
async def api_trigger_queue(queue_name: str) -> dict[str, str]:
    requested_name = (queue_name or "").strip()
    if requested_name == "rss":
        return await api_scan()
    name = canonical_queue_key(requested_name)
    if name not in queue_handlers:
        return {"status": "invalid", "message": "不支持的队列"}
    trigger_queue(name, delay=0)
    return {"status": "started", "message": f"队列 {requested_name} 已立即触发"}


@app.post("/api/tasks/process")
async def api_process_tasks(force: bool = Query(False)) -> dict[str, str]:
    async def run() -> str:
        trigger_queue("download", delay=0)
        return "已触发云盘提交队列；后续状态、资源登记与同步计划会按任务链自动推进"

    operation_id = run_operation(
        "云盘队列立即处理" if force else "云盘队列处理",
        run,
        "正在立即提交 PikPak 云盘任务" if force else "正在提交 PikPak 云盘任务",
    )
    return {"status": "started", "operation_id": str(operation_id), "message": "云盘队列已立即触发" if force else "队列处理已启动"}


@app.post("/api/tasks/poll")
async def api_poll_tasks() -> dict[str, str]:
    async def run() -> str:
        trigger_queue("cloud_poll", delay=0)
        trigger_queue("cloud_asset", delay=0)
        return "已触发 PikPak 状态轮询与云盘资源登记；同步计划与本地同步会按任务链自动推进"

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
    return {"status": "started", "operation_id": str(operation_id), "message": "云盘库扫描已启动"}


@app.post("/api/library/import")
async def api_library_import(payload: LibraryImportPayload) -> dict[str, str]:
    source_type = (payload.source_type or "").strip() or "cloud_scan"
    if source_type == "cloud_scan":
        async def run() -> str:
            settings = get_settings()
            imported, skipped = await scan_cloud_library(settings)
            log("info", f"番剧库导入完成: 云盘扫描入库 {imported} 个，跳过 {skipped} 个")
            return f"云盘扫描入库 {imported} 个，跳过 {skipped} 个"

        operation_id = run_operation("番剧库导入", run, "正在扫描云盘并写入番剧库条目")
        return {"status": "started", "operation_id": str(operation_id), "message": "番剧库云盘导入已启动"}
    if source_type in {"search", "magnet", "manual"}:
        log("info", f"番剧库导入请求已记录: {source_type}")
        return {"status": "planned", "message": "番剧库导入入口已预留，搜索源与手动导入将在后续阶段接入"}
    return {"status": "invalid", "message": "不支持的番剧库导入类型"}


@app.post("/api/library/{entry_id}/backfill")
async def api_backfill_library_entry(entry_id: int) -> dict[str, str]:
    settings = get_settings()
    with connect() as conn:
        entry = conn.execute(
            "SELECT id, domain_kind FROM entries WHERE id=?",
            (entry_id,),
        ).fetchone()
        if not entry:
            return {"status": "not_found", "message": "条目不存在"}
        if entry["domain_kind"] != "library":
            return {"status": "invalid_domain", "message": "该条目不属于番剧库"}
        series_row = conn.execute(
            "SELECT series_id FROM releases WHERE entry_id=? ORDER BY id ASC LIMIT 1",
            (entry_id,),
        ).fetchone()
        enqueue_backfill_task(
            conn,
            int(series_row["series_id"] or 0) if series_row else 0,
            entry_id,
            settings,
            now(),
        )
    return {"status": "queued", "message": "番剧库补全任务已加入队列"}


@app.post("/api/sync/tasks/process")
async def api_process_sync_tasks() -> dict[str, str]:
    settings = get_settings()
    async def run() -> str:
        auto_sync = bool_setting(settings.get("auto_sync_following", "true"))
        with connect() as conn:
            entry_ids = [
                int(row["entry_id"])
                for row in conn.execute(
                    """
                    SELECT DISTINCT ca.entry_id
                    FROM cloud_assets ca
                    JOIN entries e ON e.id=ca.entry_id
                    LEFT JOIN sync_rules sr ON sr.entry_id=ca.entry_id
                    WHERE ca.status='available'
                      AND COALESCE(e.hidden, 0)=0
                      AND e.bangumi_id != ''
                      AND (COALESCE(sr.sync_enabled, 0)=1 OR ?=1)
                    """,
                    (1 if auto_sync else 0,),
                ).fetchall()
            ]
        planned = enqueue_sync_plan_tasks(entry_ids, now())
        trigger_queue("sync_plan", delay=0)
        return f"同步计划入队 {planned} 个；本地同步会按任务链自动推进"

    operation_id = run_operation("本地同步", run, "正在把云盘资源同步到本地")
    return {"status": "started", "operation_id": str(operation_id), "message": "本地同步处理已启动"}


@app.post("/api/tasks/retry-failed")
async def api_retry_failed() -> dict[str, str]:
    with connect() as conn:
        total = 0
        reset_tables = [
            "download_tasks",
            "cloud_poll_tasks",
            "cloud_asset_tasks",
            "sync_tasks",
            "selection_tasks",
            "backfill_tasks",
            "metadata_tasks",
            "mikan_match_tasks",
        ]
        for table in [
            *reset_tables,
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
    trigger_queues(
        [
            "mikan_match",
            "metadata",
            "selection",
            "backfill",
            "download",
            "cloud_poll",
            "cloud_asset",
            "sync",
        ],
        delay=0,
    )
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


@app.post("/api/series/{series_id}/download", deprecated=True)
async def api_download_series(series_id: int) -> dict[str, str]:
    # Legacy compatibility alias for old clients; primary frontend now uses /api/seasonal/*.
    return queue_entry_download(series_id)


@app.post("/api/releases/{release_id}/download")
async def api_download_release(release_id: int) -> dict[str, str]:
    queue_release(release_id, get_settings())
    return {"status": "queued"}


@app.post("/api/series/{series_id}/metadata", deprecated=True)
async def api_refresh_metadata(series_id: int) -> dict[str, str]:
    # Legacy compatibility alias for old clients; primary frontend now uses /api/seasonal/*.
    return start_entry_metadata_refresh(series_id)


@app.post("/api/series/{series_id}/nfo", deprecated=True)
async def api_generate_nfo(series_id: int) -> dict[str, str]:
    # Legacy compatibility alias for old clients; primary frontend now uses /api/seasonal/*.
    return generate_entry_nfo(series_id)


@app.post("/api/series/{series_id}/sync", deprecated=True)
async def api_sync_series(series_id: int) -> dict[str, str]:
    # Legacy compatibility alias for old clients; primary frontend now uses /api/seasonal/*.
    return queue_entry_sync(series_id)


@app.post("/api/series/{series_id}/sync/cancel", deprecated=True)
async def api_cancel_sync_series(series_id: int) -> dict[str, str]:
    # Legacy compatibility alias for old clients; primary frontend now uses /api/seasonal/*.
    return cancel_entry_sync(series_id)


@app.post("/api/seasonal/{entry_id}/download")
async def api_download_seasonal_entry(entry_id: int) -> dict[str, str]:
    return queue_entry_download(entry_id)


@app.post("/api/seasonal/{entry_id}/metadata")
async def api_refresh_seasonal_metadata(entry_id: int) -> dict[str, str]:
    return start_entry_metadata_refresh(entry_id)


@app.post("/api/seasonal/{entry_id}/nfo")
async def api_generate_seasonal_nfo(entry_id: int) -> dict[str, str]:
    return generate_entry_nfo(entry_id)


@app.post("/api/seasonal/{entry_id}/sync")
async def api_sync_seasonal_entry(entry_id: int) -> dict[str, str]:
    return queue_entry_sync(entry_id)


@app.post("/api/seasonal/{entry_id}/sync/cancel")
async def api_cancel_sync_seasonal_entry(entry_id: int) -> dict[str, str]:
    return cancel_entry_sync(entry_id)


@app.post("/api/library/{entry_id}/metadata")
async def api_refresh_library_metadata(entry_id: int) -> dict[str, str]:
    return start_entry_metadata_refresh(entry_id)


@app.post("/api/library/{entry_id}/nfo")
async def api_generate_library_nfo(entry_id: int) -> dict[str, str]:
    return generate_entry_nfo(entry_id)


@app.post("/api/library/{entry_id}/sync")
async def api_sync_library_entry(entry_id: int) -> dict[str, str]:
    return queue_entry_sync(entry_id)


@app.post("/api/library/{entry_id}/sync/cancel")
async def api_cancel_sync_library_entry(entry_id: int) -> dict[str, str]:
    return cancel_entry_sync(entry_id)


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
