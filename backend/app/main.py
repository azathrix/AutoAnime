from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import APP_DIR
from .database import connect
from .db import clear_runtime_data, diagnostics, get_runtime_generation, get_settings, init_db, log, merge_duplicate_series, now, save_settings
from .episode_jobs import build_episode_jobs
from .import_service import commit_local_import, commit_torrent_import, preview_local_import, preview_torrent_import
from .queue_bridge import register_queue_trigger
from .runtime_store import runtime_store
from .library import bool_setting
from .metadata import generate_nfo_for_entry
from .pipeline_orchestrator import run_ready_tasks, start_pipeline
from .pipeline_runtime import finish_pipeline_run, pipeline_overview, start_pipeline_run, update_pipeline_run
from .processors import register_builtin_processors
from .scanner import language_tokens, mark_selected_releases, priority_match, priority_pick, resolve_entry_choice
from .sync_service import cancel_sync_for_entry, scan_remote_library


scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")
QUEUE_DEBOUNCE_SECONDS = 10.0
QueueHandler = Callable[[], Awaitable[None]]
queue_handlers: dict[str, QueueHandler] = {}
queue_debounce_tasks: dict[str, asyncio.Task] = {}
active_queue_tasks: set[asyncio.Task] = set()
queue_running: set[str] = set()
queue_rerun_requested: set[str] = set()
active_operation_tasks: set[asyncio.Task] = set()
DASHBOARD_CACHE_TTL = 1.0
dashboard_cache: dict[str, Any] = {"ts": 0.0, "data": None}
dashboard_cache_lock = asyncio.Lock()
QUEUE_KEY_ALIASES: dict[str, str] = {}


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
    download_backend: str = "rclone"
    local_downloader_root: str = "/data/local-downloader"
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
    local_library_root: str = "/media/autoanime"
    auto_sync_following: bool = False
    nfo_output_root: str = ""
    work_dir_template: str = ""
    season_dir_template: str = ""
    episode_name_template: str = ""


class EntryPayload(BaseModel):
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
    source_type: str = "remote_scan"
    query: str = ""
    magnet: str = ""
    source_ref: str = ""


class LocalImportPreviewPayload(BaseModel):
    root_path: str
    limit: int = 200


class TorrentImportPreviewPayload(BaseModel):
    title: str = ""
    magnet: str = ""
    torrent_url: str = ""
    page_url: str = ""


class ImportCommitPayload(BaseModel):
    items: list[dict[str, Any]] = Field(default_factory=list)
    item: dict[str, Any] = Field(default_factory=dict)
    title_cn: str = ""
    bangumi_id: str = ""
    tmdb_id: str = ""
    year: int = 0
    season_number: int = 1
    media_type: str = "anime"
    region: str = "jp"
    target_library_id: int = 0
    start_download: bool = True
    generate_nfo: bool = True


class MediaLibraryPayload(BaseModel):
    key: str = ""
    name: str = ""
    media_type: str = "anime"
    root_path: str = ""
    download_strategy: str = "download"
    metadata_provider_priority: str = "bangumi,tmdb,manual"
    naming_template: str = ""
    enabled: bool = True


class PipelineStartPayload(BaseModel):
    trigger_source: str = "manual"
    first_step_key: str = ""
    subject_type: str = ""
    subject_id: int = 0
    payload: dict[str, Any] = Field(default_factory=dict)
    message: str = ""


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


def seconds_since(value: str) -> int:
    if not value:
        return 0
    try:
        target = datetime.fromisoformat(value)
    except ValueError:
        return 0
    if target.tzinfo is None:
        target = target.replace(tzinfo=timezone.utc)
    return max(0, int((datetime.now(timezone.utc) - target).total_seconds()))


def enrich_retry_rows(rows: list[Any]) -> list[dict[str, Any]]:
    result = rows_to_dicts(rows)
    for row in result:
        retry_seconds = seconds_until(str(row.get("retry_after") or ""))
        row["retry_seconds"] = retry_seconds
        row["waiting_retry"] = row.get("status") == "waiting" or (row.get("status") == "pending" and retry_seconds > 0)
        row["display_title"] = (
            row.get("title_cn")
            or row.get("series_title")
            or row.get("release_title")
            or row.get("local_path")
            or row.get("artifact_name")
            or row.get("title")
            or ""
        )
        if row.get("progress_text"):
            row["display_reason"] = row.get("progress_text")
        elif row.get("reason"):
            row["display_reason"] = row.get("reason")
        elif row.get("status") == "running":
            row["display_reason"] = "worker 正在处理当前任务"
        elif row["waiting_retry"]:
            row["display_reason"] = f"等待重试，剩余 {retry_seconds} 秒"
        elif row.get("last_error"):
            row["display_reason"] = row.get("last_error")
        elif row.get("status") == "pending":
            row["display_reason"] = "已入队，等待当前批次执行"
        else:
            row["display_reason"] = ""
    return result


def split_setting(value: str) -> list[str]:
    return [x.strip() for x in (value or "").splitlines() if x.strip()]


def split_candidate_values(value: Any) -> list[str]:
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def normalize_media_library_key(value: str) -> str:
    key = re.sub(r"[^a-zA-Z0-9_]+", "_", (value or "").strip().lower()).strip("_")
    return key[:64]


def entry_scope_label(item: dict[str, Any]) -> str:
    for key in ("season_label", "arc_label", "part_label", "special_label"):
        value = str(item.get(key) or "").strip()
        if value:
            return value
    season_number = int(item.get("season_number") or 0)
    if season_number > 1:
        return f"Season {season_number:02d}"
    return ""


def entry_badge_text(item: dict[str, Any]) -> str:
    scope = entry_scope_label(item)
    if scope:
        return scope
    kind = str(item.get("entry_kind") or "").strip()
    if kind == "special":
        return "特别篇"
    if kind == "part":
        return "篇章"
    if kind == "arc":
        return "章节"
    return "Season 01"


def enrich_catalog_entry(item: dict[str, Any]) -> dict[str, Any]:
    result = dict(item)
    work_display_title = str(result.get("work_title") or result.get("title_root") or result.get("display_title") or result.get("title_cn") or "").strip()
    scope_label = entry_scope_label(result)
    local_count = int(result.get("local_asset_count") or 0)
    release_count = int(result.get("release_count") or 0)
    cloud_count = int(result.get("download_artifact_count") or 0)
    result["work_display_title"] = work_display_title
    result["entry_scope_label"] = scope_label
    result["entry_badge_text"] = entry_badge_text(result)
    result["entry_display_title"] = str(result.get("display_title") or result.get("title_cn") or work_display_title).strip()
    result["entry_secondary_title"] = scope_label or work_display_title
    if local_count > 0:
        result["watch_status"] = "ready"
        result["watch_status_label"] = f"可观看 {local_count} 集"
    elif result.get("has_failed_task"):
        result["watch_status"] = "warning"
        result["watch_status_label"] = "需要处理"
    elif release_count > 0 or cloud_count > 0:
        result["watch_status"] = "processing"
        result["watch_status_label"] = "处理中"
    else:
        result["watch_status"] = "unavailable"
        result["watch_status_label"] = "未缓存"
    return result


def can_resolve_priority(values: list[str], priority: list[str], field: str = "") -> bool:
    values_clean = sorted({value for value in values if value})
    if len(values_clean) <= 1:
        return True
    return bool(priority_pick(values_clean, priority, field))


def rank_subtitle_languages(values: list[str], priority: list[str], token_index: int) -> list[str]:
    values_clean = sorted({value for value in values if value})
    if not values_clean or not priority:
        return values_clean
    for preferred in priority:
        matched = [
            value
            for value in values_clean
            if len(language_tokens(value)) > token_index
            and priority_match(language_tokens(value)[token_index], preferred, "language")
        ]
        if matched:
            return matched
    return values_clean


def pick_subtitle_language(values: list[str], primary: list[str], secondary: list[str]) -> str:
    candidates = rank_subtitle_languages(values, primary, 0)
    if len(candidates) == 1:
        return candidates[0]
    candidates = rank_subtitle_languages(candidates, secondary, 1)
    if len(candidates) == 1:
        return candidates[0]
    return ""


SEASONAL_STATUS_QUEUE_ORDER = [
    "mikan_match",
    "metadata",
    "selection",
    "backfill",
    "download",
    "nfo",
    "local_presence",
]

SEASONAL_STATUS_QUEUE_NAMES = {
    "mikan_match": "Mikan 匹配",
    "metadata": "元数据",
    "selection": "自动选集",
    "backfill": "整季补全",
    "download": "下载到本地",
    "nfo": "NFO",
    "local_presence": "本地存在性检查",
}


def build_entry_queue_index(queue_details: dict[str, dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    result: dict[int, list[dict[str, Any]]] = {}
    for queue_key in SEASONAL_STATUS_QUEUE_ORDER:
        details = queue_details.get(queue_key, {})
        for item in details.get("items", []):
            entry_id = int(item.get("entry_id") or 0)
            if entry_id <= 0:
                continue
            row = dict(item)
            row["_queue_key"] = queue_key
            row["_queue_name"] = SEASONAL_STATUS_QUEUE_NAMES.get(queue_key, queue_key)
            result.setdefault(entry_id, []).append(row)
    return result


def compact_task_reason(item: dict[str, Any]) -> str:
    reason = str(item.get("display_reason") or item.get("reason") or item.get("last_error") or "").strip()
    return reason[:160] if reason else ""


def summarize_seasonal_entry(
    item: dict[str, Any],
    entry_tasks: list[dict[str, Any]],
    settings: dict[str, str],
) -> dict[str, Any]:
    result = dict(item)
    subtitle_priority = split_setting(settings.get("subtitle_priority", ""))
    resolution_priority = split_setting(settings.get("resolution_priority", ""))
    language_priority = split_setting(settings.get("language_priority", ""))
    secondary_language_priority = split_setting(settings.get("secondary_language_priority", ""))

    result["has_failed_task"] = False
    result["needs_attention"] = False
    result["status_category"] = "idle"
    result["status_level"] = "info"
    result["status_summary"] = ""

    for status, category, level, prefix in (
        ("failed", "failed", "danger", "失败"),
        ("running", "running", "warning", "处理中"),
    ):
        for task in entry_tasks:
            if str(task.get("status") or "") != status:
                continue
            reason = compact_task_reason(task) or "任务执行异常"
            result["has_failed_task"] = status == "failed"
            result["status_category"] = category
            result["status_level"] = level
            result["status_summary"] = f"{task['_queue_name']}{prefix}: {reason}"
            return result

    for task in entry_tasks:
        if str(task.get("status") or "") != "waiting" and not (
            str(task.get("status") or "") == "pending" and bool(task.get("waiting_retry"))
        ):
            continue
        reason = compact_task_reason(task) or f"剩余 {int(task.get('retry_seconds') or 0)} 秒"
        result["status_category"] = "cooldown"
        result["status_level"] = "warning"
        result["status_summary"] = f"{task['_queue_name']}等待重试: {reason}"
        return result

    for task in entry_tasks:
        if str(task.get("status") or "") != "pending":
            continue
        reason = compact_task_reason(task) or "已入队，等待处理"
        result["status_category"] = "pending"
        result["status_level"] = "primary"
        result["status_summary"] = f"{task['_queue_name']}待处理: {reason}"
        return result

    if not result.get("bangumi_id"):
        result["needs_attention"] = True
        result["status_category"] = "attention"
        result["status_level"] = "warning"
        result["status_summary"] = "缺少 Bangumi 关联，不能进入正式处理"
        return result

    auto_download_enabled = result.get("auto_download") == "on" or (
        result.get("auto_download") == "inherit" and bool_setting(settings.get("auto_download_unique", "true"))
    )
    if int(result.get("release_count") or 0) > 0 and not auto_download_enabled and int(result.get("download_artifact_count") or 0) <= 0:
        result["status_category"] = "paused"
        result["status_level"] = "info"
        result["status_summary"] = "自动下载已关闭，等待手动启用或调整规则"
        return result

    if int(result.get("local_asset_count") or 0) > 0:
        result["status_category"] = "ready_local"
        result["status_level"] = "success"
        result["status_summary"] = "本地文件已就绪"
        return result

    if int(result.get("download_artifact_count") or 0) > 0:
        result["status_category"] = "ready_download"
        result["status_level"] = "success"
        result["status_summary"] = "下载完成，等待本地整理"
        return result

    if int(result.get("downloaded_count") or 0) > 0:
        result["status_category"] = "submitted"
        result["status_level"] = "warning"
        result["status_summary"] = "下载任务已提交，等待完成"
        return result

    if int(result.get("release_count") or 0) > 0:
        result["status_category"] = "ready"
        result["status_level"] = "info"
        result["status_summary"] = "已入库，等待自动选择或下载"
        return result

    return result


def settings_response() -> dict[str, Any]:
    settings = get_settings()
    result: dict[str, Any] = dict(settings)
    result["scan_interval_minutes"] = int(settings.get("scan_interval_minutes") or 60)
    result["auto_scan"] = bool_setting(settings.get("auto_scan", "false"))
    result["auto_download_unique"] = bool_setting(settings.get("auto_download_unique", "true"))
    result["auto_download_by_priority"] = bool_setting(settings.get("auto_download_by_priority", "true"))
    result["auto_sync_following"] = bool_setting(settings.get("auto_sync_following", "false"))
    result["subtitle_priority"] = split_setting(settings.get("subtitle_priority", ""))
    result["resolution_priority"] = split_setting(settings.get("resolution_priority", ""))
    result["language_priority"] = split_setting(settings.get("language_priority", ""))
    result["secondary_language_priority"] = split_setting(settings.get("secondary_language_priority", ""))
    result["work_dir_template"] = settings.get("series_dir_template", "")
    return result


def empty_entry_response() -> dict[str, Any]:
    return {
        "entry": None,
        "releases": [],
        "tasks": [],
        "download_artifacts": [],
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
            FROM download_jobs
            WHERE entry_id=?
              AND status IN ('pending', 'running', 'submitted', 'failed')
            ORDER BY id DESC
            """,
            (entry_id,),
        ).fetchall()
        download_artifacts = conn.execute(
            "SELECT * FROM download_artifacts WHERE entry_id=? ORDER BY episode_number ASC, id DESC",
            (entry_id,),
        ).fetchall()
        local_assets = conn.execute(
            "SELECT * FROM local_assets WHERE entry_id=? ORDER BY episode_number ASC, id DESC",
            (entry_id,),
        ).fetchall()
    groups = sorted({r["subtitle_group"] for r in releases if r["subtitle_group"]})
    resolutions = sorted({r["resolution"] for r in releases if r["resolution"]})
    languages = sorted({r["language"] for r in releases if r["language"]})
    entry_payload = enrich_catalog_entry({**row_to_dict(entry), "domain_kind": entry["domain_kind"]})
    return {
        "entry": entry_payload,
        "releases": rows_to_dicts(releases),
        "tasks": rows_to_dicts(tasks),
        "download_artifacts": rows_to_dicts(download_artifacts),
        "local_assets": rows_to_dicts(local_assets),
        "groups": groups,
        "resolutions": resolutions,
        "languages": languages,
    }


def save_entry_payload(entry_id: int, payload: EntryPayload, *, expected_domain: str | None = None) -> dict[str, Any]:
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
        should_refresh_seasonal = domain_kind == "seasonal"
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
    if should_refresh_seasonal:
        start_pipeline(
            "seasonal_mikan_tracking",
            trigger_source="settings",
            first_step_key="release_selection",
            subject_type="entry",
            subject_id=entry_id,
            payload={"entry_id": entry_id, "domain_kind": "seasonal"},
            message="番剧规则变更，重新计算自动选集",
        )
        start_pipeline(
            "seasonal_mikan_tracking",
            trigger_source="settings",
            first_step_key="season_backfill",
            subject_type="entry",
            subject_id=entry_id,
            payload={"entry_id": entry_id, "domain_kind": "seasonal"},
            message="番剧规则变更，重新执行补全",
        )
        trigger_queue("processor", delay=0)
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
        message = choice["reason"] or "没有可下载发布"
        log("warn", f"手动下载跳过: {entry['display_title']} - {message}")
        return {"status": "skipped", "count": "0", "message": message}
    pipeline_key = "seasonal_mikan_tracking" if entry["domain_kind"] == "seasonal" else "library_backfill"
    for release_id in ids:
        run_id = start_pipeline(
            pipeline_key,
            trigger_source="manual",
            first_step_key="download",
            subject_type="release",
            subject_id=int(release_id),
            payload={"release_id": int(release_id), "entry_id": entry_id, "domain_kind": entry["domain_kind"]},
            message="手动下载",
        )
        log("info", f"手动下载流水线已启动: entry_id={entry_id} release_id={release_id} run_id={run_id}")
    trigger_queue("processor", delay=0)
    return {"status": "queued", "count": str(len(ids)), "message": f"已加入下载队列: {len(ids)} 条"}


def start_entry_metadata_refresh(entry_id: int) -> dict[str, str]:
    with connect() as conn:
        entry = conn.execute("SELECT domain_kind FROM entries WHERE id=?", (entry_id,)).fetchone()
    if not entry:
        return {"status": "not_found", "message": "条目不存在"}
    domain_kind = str(entry["domain_kind"] or "seasonal")
    pipeline_key = "seasonal_mikan_tracking" if domain_kind == "seasonal" else "library_backfill"
    run_id = start_pipeline(
        pipeline_key,
        trigger_source="manual",
        first_step_key="bangumi_metadata",
        subject_type="entry",
        subject_id=entry_id,
        payload={"entry_id": entry_id, "domain_kind": domain_kind},
        message="手动刷新元数据",
    )
    trigger_queue("processor", delay=0)
    return {"status": "queued", "run_id": str(run_id), "message": "元数据刷新已加入 Runtime 队列"}


def queue_entry_backfill(entry_id: int) -> dict[str, str]:
    with connect() as conn:
        entry = conn.execute("SELECT domain_kind FROM entries WHERE id=?", (entry_id,)).fetchone()
    if not entry:
        return {"status": "not_found", "message": "条目不存在"}
    domain_kind = str(entry["domain_kind"] or "seasonal")
    pipeline_key = "seasonal_mikan_tracking" if domain_kind == "seasonal" else "library_backfill"
    run_id = start_pipeline(
        pipeline_key,
        trigger_source="manual",
        first_step_key="season_backfill",
        subject_type="entry",
        subject_id=entry_id,
        payload={"entry_id": entry_id, "domain_kind": domain_kind},
        message="手动补全条目发布",
    )
    trigger_queue("processor", delay=0)
    if run_id <= 0:
        return {"status": "invalid", "message": "当前流水线不支持补全"}
    return {"status": "queued", "run_id": str(run_id), "message": "补全任务已加入 Runtime 队列"}


def generate_entry_nfo(entry_id: int) -> dict[str, str]:
    generate_nfo_for_entry(entry_id, get_settings())
    return {"status": "generated"}


def queue_entry_sync(entry_id: int) -> dict[str, str]:
    with connect() as conn:
        entry = conn.execute("SELECT domain_kind FROM entries WHERE id=?", (entry_id,)).fetchone()
        artifacts = conn.execute(
            """
            SELECT id
            FROM download_artifacts
            WHERE entry_id=? AND status='available'
            ORDER BY episode_number ASC, id ASC
            """,
            (entry_id,),
        ).fetchall()
    if not entry:
        return {"status": "not_found", "message": "条目不存在"}
    if not artifacts:
        return {"status": "skipped", "count": "0", "message": "暂无可整理的下载产物"}
    run_ids: list[int] = []
    for artifact in artifacts:
        run_id = start_pipeline(
            "media_import",
            trigger_source="manual",
            first_step_key="local_sync",
            subject_type="download_artifact",
            subject_id=int(artifact["id"]),
            payload={
                "download_artifact_id": int(artifact["id"]),
                "entry_id": entry_id,
                "domain_kind": entry["domain_kind"],
            },
            message="手动本地整理",
        )
        run_ids.append(run_id)
    trigger_queue("processor", delay=0)
    log("info", f"手动本地整理已启动: entry_id={entry_id} count={len(run_ids)} run_ids={','.join(str(item) for item in run_ids)}")
    return {"status": "queued", "count": str(len(run_ids)), "message": f"已启动本地整理: {len(run_ids)} 条"}


def cancel_entry_sync(entry_id: int) -> dict[str, str]:
    count, message = cancel_sync_for_entry(entry_id)
    return {"status": "completed", "count": str(count), "message": message}


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
    minutes = max(1, int(settings.get("scan_interval_minutes") or 60))
    runtime_store.set_scheduler_sync("rss_scan", interval_minutes=minutes, updated_at=now())
    runtime_store.set_scheduler_sync("queue_dispatch", interval_minutes=1, debounce_seconds=int(QUEUE_DEBOUNCE_SECONDS), updated_at=now())
    for name in queue_handlers:
        runtime_store.set_scheduler_sync(queue_job_key(name), debounce_seconds=int(QUEUE_DEBOUNCE_SECONDS), updated_at=now())
    scheduler.add_job(lambda: asyncio.create_task(scheduled_scan()), "interval", minutes=minutes, id="rss_scan")
    scheduler.add_job(lambda: asyncio.create_task(dispatch_ready_queues()), "interval", minutes=1, id="queue_dispatch")


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


def queue_summary(settings: dict[str, str]) -> list[dict[str, Any]]:
    snapshot = runtime_store.snapshot()
    runtime_queues = {str(item.get("key") or ""): item for item in snapshot.get("queues", [])}

    def runtime_item(key: str, name: str, description: str = "") -> dict[str, Any]:
        runtime_key = canonical_queue_key(key)
        row = runtime_queues.get(runtime_key, {})
        pending = int(row.get("pending", 0) or 0)
        running = int(row.get("running", 0) or 0)
        failed = int(row.get("failed", 0) or 0)
        waiting = int(row.get("waiting", 0) or 0)
        if running:
            queue_state = "running"
            state_reason = "队列正在处理当前批次任务"
        elif pending:
            queue_state = "ready"
            state_reason = "已有待处理任务，可立即执行"
        elif waiting:
            queue_state = "cooldown"
            state_reason = "任务正在等待重试"
        elif failed:
            queue_state = "failed"
            state_reason = "存在失败任务，等待重试或人工处理"
        else:
            queue_state = "idle"
            state_reason = "当前没有可处理任务"
        return {
            "key": key,
            "runtime_queue_key": runtime_key,
            "name": name,
            "pending": pending,
            "running": running,
            "failed": failed,
            "waiting": waiting,
            "next_retry_after": "",
            "next_retry_seconds": 0,
            "queue_state": queue_state,
            "state_reason": state_reason,
            "state_detail": f"当前运行 {running} 个，待处理 {pending} 个" if running else (f"当前批次可执行 {pending} 个" if pending else ""),
            "description": description,
        }

    return [
        {
            "key": "rss",
            "name": "Mikan RSS",
            "pending": 0,
            "running": 0,
            "failed": 0,
            "waiting": 0,
            "description": "周期读取 RSS，新增发布后进入 Runtime 流水线",
            "queue_state": "scheduled" if bool_setting(settings.get("auto_scan", "false")) else "disabled",
            "state_reason": "自动扫描已启用" if bool_setting(settings.get("auto_scan", "false")) else "自动扫描未启用",
            "system_queue": True,
            "runtime_queue_key": "rss",
        },
        runtime_item("processor", "流水线处理器", "统一 Runtime 处理器队列"),
        runtime_item("mikan_match", "Mikan 匹配", "解析 Mikan 与 Bangumi 对应关系"),
        runtime_item("metadata", "元数据", "刷新 Bangumi/条目元数据"),
        runtime_item("selection", "自动选集", "根据全局优先级选择唯一发布"),
        runtime_item("backfill", "整季补全", "补抓当季历史条目"),
        runtime_item("download", "下载到本地", "提交下载器、轮询完成并整理到本地媒体库"),
        runtime_item("nfo", "NFO", "本地整理完成后生成 NFO"),
        runtime_item("local_presence", "本地存在性检查", "检查本地最终文件状态"),
    ]


def console_overview(
    queue_items: list[dict[str, Any]],
    scheduled_jobs: list[dict[str, Any]],
    operations: list[dict[str, Any]],
    server_logs: list[str],
) -> dict[str, Any]:
    business_queue_items = [item for item in queue_items if not item.get("system_queue")]
    queue_total = len(business_queue_items)
    running_queue_count = sum(1 for item in business_queue_items if item.get("queue_state") == "running" or int(item.get("running", 0) or 0) > 0)
    pending_queue_count = sum(1 for item in business_queue_items if int(item.get("pending", 0) or 0) > 0)
    failed_queue_count = sum(1 for item in business_queue_items if int(item.get("failed", 0) or 0) > 0)
    waiting_retry_count = sum(int(item.get("waiting", 0) or 0) for item in business_queue_items)
    pending_task_count = sum(int(item.get("pending", 0) or 0) for item in business_queue_items)
    running_task_count = sum(int(item.get("running", 0) or 0) for item in business_queue_items)
    failed_task_count = sum(int(item.get("failed", 0) or 0) for item in business_queue_items)
    running_operation_count = sum(1 for item in operations if str(item.get("status", "")) == "running")
    failed_operation_count = sum(1 for item in operations if str(item.get("status", "")) == "failed")
    scheduled_failed_count = sum(1 for item in scheduled_jobs if str(item.get("last_status", "")) == "failed")
    scheduled_running_count = sum(1 for item in scheduled_jobs if str(item.get("last_status", "")) == "running")
    recent_error_count = sum(1 for line in server_logs if "[ERROR]" in str(line))
    recent_warn_count = sum(1 for line in server_logs if "[WARN]" in str(line))
    active_queue_names = [
        str(item.get("name", ""))
        for item in business_queue_items
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
    snapshot = runtime_store.snapshot()
    latest_runs: dict[str, dict[str, Any]] = {}
    for run in snapshot.get("scheduler_runs", []):
        job_key = str(run.get("job_key") or "")
        if job_key and job_key not in latest_runs:
            latest_runs[job_key] = dict(run)
    result = []
    for job in sorted(snapshot.get("scheduler", []), key=lambda item: str(item.get("job_key") or "")):
        item = dict(job)
        item.setdefault("id", 0)
        item.setdefault("job_type", "runtime")
        item.setdefault("enabled", 1)
        item.setdefault("last_status", "idle")
        item["latest_run"] = latest_runs.get(str(item.get("job_key") or ""), {})
        result.append(item)
    return result


def queue_detail_map() -> dict[str, dict[str, Any]]:
    snapshot = runtime_store.snapshot()
    details = dict(snapshot.get("queue_details") or {})
    for alias, canonical in QUEUE_KEY_ALIASES.items():
        if canonical in details and alias not in details:
            details[alias] = details[canonical]
    details.setdefault("rss", {"items": []})
    details.setdefault("processor", {"items": []})
    hydrate_queue_item_titles(details)
    return details


def hydrate_queue_item_titles(details: dict[str, dict[str, Any]]) -> None:
    items: list[dict[str, Any]] = []
    for detail in details.values():
        for item in detail.get("items", []):
            if isinstance(item, dict):
                items.append(item)
    if not items:
        return

    entry_ids = {int(item.get("entry_id") or 0) for item in items if int(item.get("entry_id") or 0) > 0}
    release_ids = {int(item.get("release_id") or 0) for item in items if int(item.get("release_id") or 0) > 0}
    candidate_ids = {int(item.get("candidate_id") or 0) for item in items if int(item.get("candidate_id") or 0) > 0}
    download_artifact_ids = {
        int(item.get("download_artifact_id") or item.get("subject_id") or 0)
        for item in items
        if item.get("subject_type") == "download_artifact" and int(item.get("download_artifact_id") or item.get("subject_id") or 0) > 0
    }
    local_asset_ids = {
        int(item.get("local_asset_id") or item.get("subject_id") or 0)
        for item in items
        if item.get("subject_type") == "local_asset" and int(item.get("local_asset_id") or item.get("subject_id") or 0) > 0
    }

    entry_titles: dict[int, dict[str, Any]] = {}
    release_titles: dict[int, dict[str, Any]] = {}
    candidate_titles: dict[int, dict[str, Any]] = {}
    download_artifact_titles: dict[int, dict[str, Any]] = {}
    local_asset_titles: dict[int, dict[str, Any]] = {}

    def placeholders(values: set[int]) -> str:
        return ",".join("?" for _ in values)

    with connect() as conn:
        if entry_ids:
            rows = conn.execute(
                f"""
                SELECT id, display_title, title_cn, title_root, domain_kind
                FROM entries
                WHERE id IN ({placeholders(entry_ids)})
                """,
                tuple(entry_ids),
            ).fetchall()
            entry_titles = {int(row["id"]): dict(row) for row in rows}
        if release_ids:
            rows = conn.execute(
                f"""
                SELECT r.id, r.entry_id, r.title AS release_title, r.episode_number,
                  e.display_title, e.title_cn, e.title_root, e.domain_kind
                FROM releases r
                JOIN entries e ON e.id=r.entry_id
                WHERE r.id IN ({placeholders(release_ids)})
                """,
                tuple(release_ids),
            ).fetchall()
            release_titles = {int(row["id"]): dict(row) for row in rows}
        if candidate_ids:
            rows = conn.execute(
                f"""
                SELECT id, title, series_title, episode_number, status
                FROM rss_candidates
                WHERE id IN ({placeholders(candidate_ids)})
                """,
                tuple(candidate_ids),
            ).fetchall()
            candidate_titles = {int(row["id"]): dict(row) for row in rows}
        if download_artifact_ids:
            rows = conn.execute(
                f"""
                SELECT ca.id, ca.release_id, ca.entry_id, ca.artifact_name, ca.episode_number,
                  e.display_title, e.title_cn, e.title_root, e.domain_kind
                FROM download_artifacts ca
                JOIN entries e ON e.id=ca.entry_id
                WHERE ca.id IN ({placeholders(download_artifact_ids)})
                """,
                tuple(download_artifact_ids),
            ).fetchall()
            download_artifact_titles = {int(row["id"]): dict(row) for row in rows}
        if local_asset_ids:
            rows = conn.execute(
                f"""
                SELECT la.id, la.release_id, la.entry_id, la.local_path, la.episode_number,
                  e.display_title, e.title_cn, e.title_root, e.domain_kind
                FROM local_assets la
                JOIN entries e ON e.id=la.entry_id
                WHERE la.id IN ({placeholders(local_asset_ids)})
                """,
                tuple(local_asset_ids),
            ).fetchall()
            local_asset_titles = {int(row["id"]): dict(row) for row in rows}

    for item in items:
        subject_type = str(item.get("subject_type") or "")
        entry_id = int(item.get("entry_id") or 0)
        release_id = int(item.get("release_id") or 0)
        candidate_id = int(item.get("candidate_id") or 0)
        row: dict[str, Any] = {}
        if release_id and release_id in release_titles:
            row = release_titles[release_id]
        elif entry_id and entry_id in entry_titles:
            row = entry_titles[entry_id]
        elif candidate_id and candidate_id in candidate_titles:
            row = candidate_titles[candidate_id]
        elif subject_type == "download_artifact":
            row = download_artifact_titles.get(int(item.get("download_artifact_id") or item.get("subject_id") or 0), {})
        elif subject_type == "local_asset":
            row = local_asset_titles.get(int(item.get("local_asset_id") or item.get("subject_id") or 0), {})
        if not row:
            continue
        title = str(row.get("display_title") or row.get("title_cn") or row.get("series_title") or row.get("title_root") or row.get("title") or "").strip()
        release_title = str(row.get("release_title") or row.get("artifact_name") or row.get("local_path") or row.get("title") or title).strip()
        item["display_title"] = title or item.get("display_title") or ""
        item["title_cn"] = title or item.get("title_cn") or ""
        item["release_title"] = release_title or item.get("release_title") or ""
        item["entry_id"] = int(row.get("entry_id") or entry_id or 0)
        item["episode_number"] = item.get("episode_number") or row.get("episode_number") or ""
        item["domain_kind"] = row.get("domain_kind") or item.get("domain_kind") or ""


def console_sections() -> list[dict[str, Any]]:
    return [
        {"key": "queues", "name": "队列", "kind": "group"},
        {"key": "queue:rss", "name": "RSS 扫描", "kind": "queue", "queue_key": "rss"},
        {"key": "queue:processor", "name": "流水线处理器", "kind": "queue", "queue_key": "processor"},
        {"key": "queue:mikan_match", "name": "Mikan 匹配", "kind": "queue", "queue_key": "mikan_match"},
        {"key": "queue:metadata", "name": "元数据", "kind": "queue", "queue_key": "metadata"},
        {"key": "queue:selection", "name": "自动选集", "kind": "queue", "queue_key": "selection"},
        {"key": "queue:backfill", "name": "整季补全", "kind": "queue", "queue_key": "backfill"},
        {"key": "queue:download", "name": "下载到本地", "kind": "queue", "queue_key": "download"},
        {"key": "queue:nfo", "name": "NFO", "kind": "queue", "queue_key": "nfo"},
        {"key": "queue:local_presence", "name": "本地存在性检查", "kind": "queue", "queue_key": "local_presence"},
        {"key": "queue:cleanup", "name": "清理", "kind": "queue", "queue_key": "cleanup"},
        {"key": "scheduler", "name": "定时任务", "kind": "group"},
        {"key": "scheduler:rss_scan", "name": "RSS 定时扫描", "kind": "scheduled", "job_key": "rss_scan"},
        {"key": "scheduler:queue_dispatch", "name": "恢复调度", "kind": "scheduled", "job_key": "queue_dispatch"},
        {"key": "logs", "name": "服务日志", "kind": "logs"},
        {"key": "maintenance", "name": "维护", "kind": "maintenance"},
    ]


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    register_builtin_processors()
    reschedule()
    scheduler.start()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="AutoAnime", lifespan=lifespan)


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


def dashboard_data() -> dict[str, Any]:
    settings = get_settings()
    recent_cutoff = datetime.now(timezone.utc).timestamp() - 7 * 24 * 60 * 60
    calendar_cutoff = datetime.now(timezone.utc).timestamp() - 28 * 24 * 60 * 60
    with connect() as conn:
        seasonal_items = conn.execute(
            """
            SELECT e.id,
              e.work_id,
              e.media_type,
              e.region,
              e.source_provider,
              e.metadata_provider,
              e.external_id,
              e.target_library_id,
              e.genres_json,
              e.tags_json,
              e.display_title,
              e.title_root,
              e.poster_url,
              e.entry_kind,
              e.season_label,
              e.arc_label,
              e.part_label,
              e.special_label,
              e.title_cn,
              e.poster_url,
              e.bangumi_id,
              e.year,
              e.season_number,
              e.auto_download,
              e.selected_group,
              e.selected_resolution,
              w.title_root AS work_title,
              COUNT(DISTINCT ep.id) AS episode_count,
              COUNT(DISTINCT r.id) AS release_count,
              COUNT(DISTINCT r.subtitle_group) AS group_count,
              COUNT(DISTINCT r.resolution) AS resolution_count,
              COUNT(DISTINCT r.language) AS language_count,
              GROUP_CONCAT(DISTINCT r.subtitle_group) AS subtitle_groups,
              GROUP_CONCAT(DISTINCT r.resolution) AS resolutions,
              GROUP_CONCAT(DISTINCT r.language) AS languages,
              COUNT(DISTINCT CASE WHEN cs.status IN ('submitted','running','completed') THEN cs.id END) AS downloaded_count,
              COUNT(DISTINCT ca.id) AS download_artifact_count,
              COUNT(DISTINCT la.id) AS local_asset_count,
              COALESCE(MAX(sr.sync_enabled), 0) AS sync_enabled
            FROM entries e
            JOIN seasonal_entries se ON se.entry_id=e.id
            JOIN works w ON w.id=e.work_id
            LEFT JOIN episodes ep ON ep.entry_id=e.id
            LEFT JOIN releases r ON r.entry_id=e.id
            LEFT JOIN download_jobs cs ON cs.release_id=r.id
            LEFT JOIN download_artifacts ca ON ca.release_id=r.id
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
              e.media_type,
              e.region,
              e.source_provider,
              e.metadata_provider,
              e.external_id,
              e.target_library_id,
              e.genres_json,
              e.tags_json,
              e.display_title,
              e.title_root,
              e.poster_url,
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
              COUNT(DISTINCT ca.id) AS download_artifact_count,
              COUNT(DISTINCT la.id) AS local_asset_count
            FROM entries e
            JOIN library_entries le ON le.entry_id=e.id
            JOIN works w ON w.id=e.work_id
            LEFT JOIN episodes ep ON ep.entry_id=e.id
            LEFT JOIN releases r ON r.entry_id=e.id
            LEFT JOIN download_artifacts ca ON ca.release_id=r.id
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
              COUNT(DISTINCT ca.id) AS download_artifact_count,
              COUNT(DISTINCT la.id) AS local_asset_count
            FROM entries e
            JOIN library_entries le ON le.entry_id=e.id
            JOIN works w ON w.id=e.work_id
            LEFT JOIN download_artifacts ca ON ca.entry_id=e.id
            LEFT JOIN local_assets la ON la.entry_id=e.id AND la.status='synced'
            WHERE COALESCE(e.hidden, 0)=0
            """
        ).fetchone()
        library_failed_row = {"failed_entry_count": 0}
        media_libraries = conn.execute(
            """
            SELECT *
            FROM media_libraries
            WHERE enabled=1
            ORDER BY
              CASE key
                WHEN 'seasonal_anime' THEN 0
                WHEN 'anime_library' THEN 1
                WHEN 'movies' THEN 2
                WHEN 'tv' THEN 3
                ELSE 9
              END,
              id ASC
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
              e.poster_url,
              e.entry_kind,
              e.season_label,
              e.arc_label,
              e.part_label,
              e.special_label,
              w.title_root AS work_title
            FROM local_assets la
            JOIN download_artifacts ca ON ca.id=la.download_artifact_id
            JOIN releases r ON r.id=la.release_id
            JOIN entries e ON e.id=r.entry_id
            JOIN seasonal_entries se ON se.entry_id=e.id
            JOIN works w ON w.id=e.work_id
            JOIN sync_rules sr ON sr.entry_id=e.id AND sr.sync_enabled=1
            WHERE la.status='synced'
              AND COALESCE(e.hidden, 0)=0
              AND strftime('%s', la.updated_at) >= ?
            ORDER BY la.updated_at DESC
            LIMIT 120
            """,
            (int(recent_cutoff),),
        ).fetchall()
        seasonal_update_calendar = conn.execute(
            """
            SELECT
              e.id AS entry_id,
              e.display_title,
              e.title_root,
              e.poster_url,
              e.entry_kind,
              e.season_label,
              e.arc_label,
              e.part_label,
              e.special_label,
              w.title_root AS work_title,
              r.episode_number,
              MAX(COALESCE(la.updated_at, ca.updated_at, r.updated_at, r.created_at)) AS updated_at,
              MAX(CASE WHEN la.status='synced' THEN 1 ELSE 0 END) AS synced
            FROM releases r
            JOIN entries e ON e.id=r.entry_id
            JOIN seasonal_entries se ON se.entry_id=e.id
            JOIN works w ON w.id=e.work_id
            JOIN sync_rules sr ON sr.entry_id=e.id AND sr.sync_enabled=1
            LEFT JOIN download_artifacts ca ON ca.release_id=r.id
            LEFT JOIN local_assets la ON la.release_id=r.id
            WHERE COALESCE(e.hidden, 0)=0
              AND la.status='synced'
              AND strftime('%s', COALESCE(la.updated_at, ca.updated_at, r.updated_at, r.created_at)) >= ?
            GROUP BY e.id, r.episode_number
            ORDER BY updated_at DESC
            LIMIT 160
            """,
            (int(calendar_cutoff),),
        ).fetchall()
        recent_synced_entries = conn.execute(
            """
            SELECT
              e.id AS entry_id,
              e.display_title,
              e.title_root,
              e.poster_url,
              e.entry_kind,
              e.season_label,
              e.arc_label,
              e.part_label,
              e.special_label,
              w.title_root AS work_title,
              MAX(la.updated_at) AS synced_at,
              COUNT(DISTINCT la.id) AS synced_count
            FROM local_assets la
            JOIN entries e ON e.id=la.entry_id
            JOIN seasonal_entries se ON se.entry_id=e.id
            JOIN works w ON w.id=e.work_id
            WHERE la.status='synced'
              AND COALESCE(e.hidden, 0)=0
              AND strftime('%s', la.updated_at) >= ?
            GROUP BY e.id
            ORDER BY synced_at DESC
            LIMIT 12
            """,
            (int(recent_cutoff),),
        ).fetchall()
    queue_items = queue_summary(settings)
    runtime_snapshot = runtime_store.snapshot()
    scheduled_jobs = scheduled_jobs_summary()
    scheduled_runs = list(runtime_snapshot.get("scheduler_runs") or [])[-40:]
    server_logs = list(runtime_snapshot.get("logs") or [])[-160:]
    episode_jobs = build_episode_jobs(runtime_snapshot, limit=240)
    operations_list = [
        dict(item)
        for item in runtime_snapshot.get("operations", [])
        if str(item.get("status") or "") in {"running", "failed", "cancelled"}
    ][:20]
    queue_details = queue_detail_map()
    seasonal_task_index = build_entry_queue_index(queue_details)
    seasonal_rows = [
        enrich_catalog_entry(
            summarize_seasonal_entry(row, seasonal_task_index.get(int(row.get("id") or 0), []), settings)
        )
        for row in rows_to_dicts(seasonal_items)
    ]
    library_rows = [enrich_catalog_entry(row) for row in rows_to_dicts(library_items)]
    seasonal_calendar_rows = [enrich_catalog_entry(row) for row in rows_to_dicts(seasonal_sync_calendar)]
    seasonal_update_rows = [enrich_catalog_entry(row) for row in rows_to_dicts(seasonal_update_calendar)]
    recent_synced_rows = [enrich_catalog_entry(row) for row in rows_to_dicts(recent_synced_entries)]
    return {
        "seasonal_items": seasonal_rows,
        "library_items": library_rows,
        "media_libraries": rows_to_dicts(media_libraries),
        "library_summary": {
            "work_count": int((library_summary_row["work_count"] if library_summary_row else 0) or 0),
            "entry_count": int((library_summary_row["entry_count"] if library_summary_row else 0) or 0),
            "unmatched_count": int((library_summary_row["unmatched_count"] if library_summary_row else 0) or 0),
            "download_artifact_count": int((library_summary_row["download_artifact_count"] if library_summary_row else 0) or 0),
            "local_asset_count": int((library_summary_row["local_asset_count"] if library_summary_row else 0) or 0),
            "failed_entry_count": int((library_failed_row["failed_entry_count"] if library_failed_row else 0) or 0),
        },
        "seasonal_sync_calendar": seasonal_calendar_rows,
        "seasonal_update_calendar": seasonal_update_rows,
        "recent_synced_seasonal_entries": recent_synced_rows,
        "episode_jobs": episode_jobs,
        "sync_rules": rows_to_dicts(sync_rules),
        "operations": operations_list,
        "scheduled_jobs": scheduled_jobs,
        "scheduled_runs": scheduled_runs,
        "queue_summary": queue_items,
        "queue_details": queue_details,
        "pipelines": pipeline_overview(),
        "console_sections": console_sections(),
        "server_logs": server_logs,
        "console_overview": console_overview(queue_items, scheduled_jobs, operations_list, server_logs),
    }


async def cached_dashboard_data() -> dict[str, Any]:
    async with dashboard_cache_lock:
        cached = dashboard_cache.get("data")
        ts = float(dashboard_cache.get("ts") or 0)
        if cached is not None and time.monotonic() - ts < DASHBOARD_CACHE_TTL:
            return cached
        data = await run_in_threadpool(dashboard_data)
        dashboard_cache["data"] = data
        dashboard_cache["ts"] = time.monotonic()
        return data


@app.get("/api/dashboard")
async def api_dashboard() -> dict[str, Any]:
    return await cached_dashboard_data()


@app.get("/api/episode-jobs")
async def api_episode_jobs(domain_kind: str = Query("")) -> list[dict[str, Any]]:
    return await run_in_threadpool(
        lambda: build_episode_jobs(runtime_store.snapshot(), domain_kind=domain_kind, limit=500)
    )


@app.post("/api/import/local/preview")
async def api_import_local_preview(payload: LocalImportPreviewPayload) -> dict[str, Any]:
    try:
        items = await run_in_threadpool(
            lambda: preview_local_import(payload.root_path.strip(), limit=payload.limit)
        )
    except FileNotFoundError as exc:
        return {"status": "not_found", "message": str(exc), "items": []}
    return {"status": "completed", "count": len(items), "items": items}


@app.post("/api/import/torrent/preview")
async def api_import_torrent_preview(payload: TorrentImportPreviewPayload) -> dict[str, Any]:
    try:
        item = preview_torrent_import(
            title=payload.title.strip(),
            magnet=payload.magnet.strip(),
            torrent_url=payload.torrent_url.strip(),
            page_url=payload.page_url.strip(),
        )
    except ValueError as exc:
        return {"status": "invalid", "message": str(exc), "item": {}}
    return {"status": "completed", "item": item}


def import_options(payload: ImportCommitPayload) -> dict[str, Any]:
    return {
        "title_cn": payload.title_cn.strip(),
        "bangumi_id": payload.bangumi_id.strip(),
        "tmdb_id": payload.tmdb_id.strip(),
        "year": payload.year,
        "season_number": payload.season_number,
        "media_type": payload.media_type.strip() or "anime",
        "region": payload.region.strip() or "jp",
        "target_library_id": payload.target_library_id,
    }


@app.post("/api/import/local/commit")
async def api_import_local_commit(payload: ImportCommitPayload) -> dict[str, Any]:
    result = await run_in_threadpool(lambda: commit_local_import(payload.items, import_options(payload)))
    queued = 0
    if payload.generate_nfo:
        for item in result.get("imported", []):
            local_asset_id = int(item.get("local_asset_id") or 0)
            entry_id = int(item.get("entry_id") or 0)
            if local_asset_id <= 0 or entry_id <= 0:
                continue
            run_id = start_pipeline(
                "media_import",
                trigger_source="import",
                first_step_key="nfo_generate",
                subject_type="local_asset",
                subject_id=local_asset_id,
                payload={"local_asset_id": local_asset_id, "entry_id": entry_id, "domain_kind": "library"},
                message="本地导入后生成 NFO",
            )
            if run_id > 0:
                queued += 1
        if queued:
            trigger_queue("processor", delay=0)
    log("info", f"本地导入完成: imported={result.get('imported_count', 0)} skipped={result.get('skipped_count', 0)} nfo_queued={queued}")
    return {"status": "completed", "nfo_queued": queued, **result}


@app.post("/api/import/torrent/commit")
async def api_import_torrent_commit(payload: ImportCommitPayload) -> dict[str, Any]:
    candidate = payload.item or (payload.items[0] if payload.items else {})
    try:
        imported = await run_in_threadpool(lambda: commit_torrent_import(candidate, import_options(payload)))
    except ValueError as exc:
        return {"status": "invalid", "message": str(exc), "imported": {}}
    run_id = 0
    if payload.start_download:
        release_id = int(imported.get("release_id") or 0)
        entry_id = int(imported.get("entry_id") or 0)
        if release_id > 0:
            run_id = start_pipeline(
                "library_backfill",
                trigger_source="import",
                first_step_key="download",
                subject_type="release",
                subject_id=release_id,
                payload={"release_id": release_id, "entry_id": entry_id, "domain_kind": "library"},
                message="磁链导入后启动下载",
            )
            trigger_queue("processor", delay=0)
    log("info", f"磁链导入完成: entry_id={imported.get('entry_id', 0)} release_id={imported.get('release_id', 0)} run_id={run_id}")
    return {"status": "completed", "imported": imported, "run_id": run_id}


@app.get("/api/dashboard/stream")
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


@app.get("/api/pipelines")
async def api_pipelines() -> list[dict[str, Any]]:
    return pipeline_overview()


@app.post("/api/pipelines/{pipeline_key}/start")
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


@app.post("/api/processors/tasks/run")
async def api_run_runtime_processor(limit: int = Query(20, ge=1, le=200), processor_key: str = "") -> dict[str, str]:
    processed = await run_ready_tasks(limit=limit, processor_key=processor_key.strip())
    return {"status": "completed", "count": str(processed), "message": f"已处理 processor task: {processed} 个"}


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
            "download_backend": payload.download_backend,
            "local_downloader_root": payload.local_downloader_root.strip() or "/data/local-downloader",
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
            "local_library_root": payload.local_library_root.strip() or "/media/autoanime",
            "auto_sync_following": str(payload.auto_sync_following).lower(),
            "nfo_output_root": payload.nfo_output_root.strip(),
            "series_dir_template": payload.work_dir_template.strip(),
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
            entry_rows = conn.execute(
                "SELECT id, domain_kind FROM entries e WHERE COALESCE(hidden, 0)=0 AND bangumi_id != ''"
            ).fetchall()
        for row in entry_rows:
            entry_id = int(row["id"])
            domain_kind = str(row["domain_kind"] or "seasonal")
            pipeline_key = "seasonal_mikan_tracking" if domain_kind == "seasonal" else "library_backfill"
            start_pipeline(
                pipeline_key,
                trigger_source="settings",
                first_step_key="release_selection",
                subject_type="entry",
                subject_id=entry_id,
                payload={"entry_id": entry_id, "domain_kind": domain_kind},
                message="全局规则变更，重新计算自动选集",
            )
            if domain_kind == "seasonal":
                start_pipeline(
                    "seasonal_mikan_tracking",
                    trigger_source="settings",
                    first_step_key="season_backfill",
                    subject_type="entry",
                    subject_id=entry_id,
                    payload={"entry_id": entry_id, "domain_kind": domain_kind},
                    message="全局规则变更，重新执行补全",
                )
        trigger_queue("processor", delay=0)
    reschedule()
    log("info", "全局设置已保存")
    return settings_response()


@app.get("/api/seasonal/{entry_id}")
async def api_seasonal_entry(entry_id: int) -> dict[str, Any]:
    return build_entry_response(entry_id)


@app.put("/api/seasonal/{entry_id}")
async def api_update_seasonal_entry(entry_id: int, payload: EntryPayload) -> dict[str, Any]:
    return save_entry_payload(entry_id, payload, expected_domain="seasonal")


@app.get("/api/library/{entry_id}")
async def api_library_entry(entry_id: int) -> dict[str, Any]:
    return build_entry_response(entry_id)


@app.put("/api/library/{entry_id}")
async def api_update_library_entry(entry_id: int, payload: EntryPayload) -> dict[str, Any]:
    return save_entry_payload(entry_id, payload, expected_domain="library")


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
    return {"status": "started", "operation_id": str(operation_id), "message": "扫描全部已启动"}


@app.post("/api/queues/{queue_name}/trigger")
async def api_trigger_queue(queue_name: str) -> dict[str, str]:
    requested_name = (queue_name or "").strip()
    if requested_name == "rss":
        return await api_scan()
    name = canonical_queue_key(requested_name)
    if name not in queue_handlers:
        trigger_queue("processor", delay=0)
        return {"status": "started", "message": f"已触发 Runtime 处理器处理 {requested_name}"}
    trigger_queue(name, delay=0)
    return {"status": "started", "message": f"队列 {requested_name} 已立即触发"}


@app.post("/api/tasks/process")
async def api_process_tasks(force: bool = Query(False)) -> dict[str, str]:
    async def run() -> str:
        trigger_queue("processor", delay=0)
        return "已触发 Runtime 处理器；下载器会负责提交、轮询并整理到本地"

    operation_id = run_operation(
        "下载队列立即处理" if force else "下载队列处理",
        run,
        "正在立即提交下载任务" if force else "正在提交下载任务",
    )
    return {"status": "started", "operation_id": str(operation_id), "message": "下载队列已立即触发" if force else "队列处理已启动"}


@app.post("/api/tasks/poll")
async def api_poll_tasks() -> dict[str, str]:
    async def run() -> str:
        trigger_queue("processor", delay=0)
        return "已触发 Runtime 处理器；等待中的下载任务到期后会继续推进"

    operation_id = run_operation("刷新下载任务", run, "正在刷新下载器任务状态")
    return {"status": "started", "operation_id": str(operation_id), "message": "状态刷新已启动"}


@app.post("/api/cloud/scan")
async def api_scan_cloud() -> dict[str, str]:
    async def run() -> str:
        settings = get_settings()
        imported, skipped = await scan_remote_library(settings)
        log("info", f"远端资源扫描完成: 入库 {imported} 个，跳过 {skipped} 个")
        return f"入库 {imported} 个，跳过 {skipped} 个"

    operation_id = run_operation("扫描远端资源", run, "正在扫描下载器远端资源")
    return {"status": "started", "operation_id": str(operation_id), "message": "远端资源扫描已启动"}


@app.post("/api/library/import")
async def api_library_import(payload: LibraryImportPayload) -> dict[str, str]:
    source_type = (payload.source_type or "").strip() or "remote_scan"
    if source_type == "remote_scan":
        async def run() -> str:
            settings = get_settings()
            imported, skipped = await scan_remote_library(settings)
            log("info", f"番剧库导入完成: 远端资源入库 {imported} 个，跳过 {skipped} 个")
            return f"远端资源入库 {imported} 个，跳过 {skipped} 个"

        operation_id = run_operation("番剧库导入", run, "正在扫描远端资源并写入番剧库条目")
        return {"status": "started", "operation_id": str(operation_id), "message": "番剧库远端资源导入已启动"}
    if source_type in {"search", "magnet", "manual"}:
        log("info", f"番剧库导入请求已记录: {source_type}")
        return {"status": "planned", "message": "番剧库导入入口已预留，搜索源与手动导入将在后续阶段接入"}
    return {"status": "invalid", "message": "不支持的番剧库导入类型"}


@app.post("/api/media-libraries")
async def api_create_media_library(payload: MediaLibraryPayload) -> dict[str, str]:
    key = normalize_media_library_key(payload.key or payload.name)
    name = payload.name.strip() or key
    root_path = payload.root_path.strip()
    if not key or not root_path:
        return {"status": "invalid", "message": "媒体库名称和本地目录不能为空"}
    ts = now()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO media_libraries
              (key, name, media_type, root_path, enabled, download_strategy,
               metadata_provider_priority, naming_template, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
              name=excluded.name,
              media_type=excluded.media_type,
              root_path=excluded.root_path,
              enabled=excluded.enabled,
              download_strategy=excluded.download_strategy,
              metadata_provider_priority=excluded.metadata_provider_priority,
              naming_template=excluded.naming_template,
              updated_at=excluded.updated_at
            """,
            (
                key,
                name,
                payload.media_type.strip() or "anime",
                root_path,
                1 if payload.enabled else 0,
                payload.download_strategy.strip() or "download",
                payload.metadata_provider_priority.strip() or "bangumi,tmdb,manual",
                payload.naming_template.strip(),
                ts,
                ts,
            ),
        )
    log("info", f"媒体库已保存: key={key} name={name} root={root_path}")
    return {"status": "saved", "message": "媒体库已保存"}


@app.delete("/api/media-libraries/{library_id}")
async def api_delete_media_library(library_id: int) -> dict[str, str]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM media_libraries WHERE id=?", (library_id,)).fetchone()
        if not row:
            return {"status": "not_found", "message": "媒体库不存在"}
        used = conn.execute("SELECT COUNT(*) AS total FROM entries WHERE target_library_id=?", (library_id,)).fetchone()
        if int(used["total"] or 0) > 0:
            conn.execute("UPDATE media_libraries SET enabled=0, updated_at=? WHERE id=?", (now(), library_id))
            log("warn", f"媒体库已禁用: id={library_id} reason=仍有关联条目")
            return {"status": "disabled", "message": "媒体库已有条目使用，已禁用但不删除"}
        conn.execute("DELETE FROM media_libraries WHERE id=?", (library_id,))
    log("info", f"媒体库已删除: id={library_id}")
    return {"status": "deleted", "message": "媒体库已删除"}


@app.post("/api/library/{entry_id}/backfill")
async def api_backfill_library_entry(entry_id: int) -> dict[str, str]:
    return queue_entry_backfill(entry_id)


@app.post("/api/seasonal/{entry_id}/backfill")
async def api_backfill_seasonal_entry(entry_id: int) -> dict[str, str]:
    return queue_entry_backfill(entry_id)


@app.post("/api/sync/tasks/process")
async def api_process_runtime_sync() -> dict[str, str]:
    async def run() -> str:
        with connect() as conn:
            entry_ids = [
                int(row["entry_id"])
                for row in conn.execute(
                    """
                    SELECT DISTINCT ca.entry_id
                    FROM download_artifacts ca
                    JOIN entries e ON e.id=ca.entry_id
                    WHERE ca.status='available'
                      AND COALESCE(e.hidden, 0)=0
                      AND e.bangumi_id != ''
                    """,
                ).fetchall()
            ]
        for entry_id in entry_ids:
            queue_entry_sync(entry_id)
        return f"已启动本地整理流水线 {len(entry_ids)} 个；整理完成后会自动生成 NFO"

    operation_id = run_operation("本地整理", run, "正在把下载产物整理到本地媒体库")
    return {"status": "started", "operation_id": str(operation_id), "message": "本地整理处理已启动"}


@app.post("/api/tasks/retry-failed")
async def api_retry_failed() -> dict[str, str]:
    total = 0
    for task in runtime_store.tasks.values():
        if task.status in {"failed", "waiting"}:
            task.status = "pending"
            task.attempts = 0
            task.retry_at = ""
            task.error = ""
            task.updated_at = now()
            total += 1
    await runtime_store.bump()
    log("info", f"已重置 Runtime 失败/等待任务: {total} 个")
    trigger_queue("processor", delay=0)
    return {"status": "started", "count": str(total), "message": f"失败/等待任务已重新入队: {total} 个"}


@app.post("/api/runtime/retry-failed")
async def api_runtime_retry_failed() -> dict[str, str]:
    return await api_retry_failed()


@app.post("/api/runtime/cancel")
async def api_runtime_cancel() -> dict[str, str]:
    await runtime_store.cancel_all()
    return {"status": "completed", "message": "已取消 Runtime 中的运行和任务"}


@app.post("/api/operations/clear")
async def api_clear_operations() -> dict[str, str]:
    count = runtime_store.clear_finished_operations_sync()
    return {"status": "completed", "count": str(count), "message": "已清空已结束操作"}


@app.post("/api/logs/clear")
async def api_clear_logs() -> dict[str, str]:
    count = await runtime_store.clear_logs()
    return {"status": "completed", "count": str(count), "message": "已清空日志"}


@app.post("/api/runtime/logs/clear")
async def api_runtime_clear_logs() -> dict[str, str]:
    return await api_clear_logs()


@app.post("/api/system/clear-data")
async def api_clear_data() -> dict[str, str]:
    await cancel_runtime_activity()
    await runtime_store.clear_all()
    clear_runtime_data()
    log("warn", "已清除所有运行数据")
    return {"status": "completed", "message": "已清除所有运行数据"}


@app.post("/api/releases/{release_id}/download")
async def api_download_release(release_id: int) -> dict[str, str]:
    with connect() as conn:
        release = conn.execute(
            "SELECT r.entry_id, e.domain_kind FROM releases r JOIN entries e ON e.id=r.entry_id WHERE r.id=?",
            (release_id,),
        ).fetchone()
    if not release:
        return {"status": "not_found", "message": "发布不存在"}
    pipeline_key = "seasonal_mikan_tracking" if release["domain_kind"] == "seasonal" else "library_backfill"
    run_id = start_pipeline(
        pipeline_key,
        trigger_source="manual",
        first_step_key="download",
        subject_type="release",
        subject_id=release_id,
        payload={"release_id": release_id, "entry_id": int(release["entry_id"]), "domain_kind": release["domain_kind"]},
        message="手动发布下载",
    )
    trigger_queue("processor", delay=0)
    return {"status": "queued", "run_id": str(run_id)}

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



