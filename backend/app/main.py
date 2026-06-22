from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import APP_DIR, MEDIA_ROOT
from .database import connect
from .db import clear_runtime_data, diagnostics, get_runtime_generation, get_settings, init_db, log, merge_duplicate_series, now, save_settings
from .downloader_service import SUPPORTED_DOWNLOADER_TYPES
from .queue_bridge import register_queue_trigger
from .runtime_store import runtime_store
from .library import bool_setting
from .metadata import refresh_entry_metadata
from .pipeline_orchestrator import run_ready_tasks, start_pipeline
from .pipeline_runtime import finish_pipeline_run, pipeline_overview, start_pipeline_run, update_pipeline_run
from .processors import register_builtin_processors
from .parser import fingerprint, parse_episode
from .scanner import language_tokens, priority_match, priority_pick


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
    queue_dispatch_enabled: bool = True
    queue_dispatch_interval_minutes: int = 1
    auto_generate_nfo: bool = True
    backfill_current_season: bool = False
    subtitle_priority: list[str] = Field(default_factory=list)
    resolution_priority: list[str] = Field(default_factory=list)
    language_priority: list[str] = Field(default_factory=list)
    secondary_language_priority: list[str] = Field(default_factory=list)
    downloaders: list[dict[str, Any]] = Field(default_factory=list)
    episode_name_template: str = ""
    movie_name_template: str = ""
    tv_name_template: str = ""
    movie_quality_priority: list[str] = Field(default_factory=list)
    movie_source_priority: list[str] = Field(default_factory=list)
    movie_subtitle_priority: list[str] = Field(default_factory=list)
    tv_quality_priority: list[str] = Field(default_factory=list)
    tv_source_priority: list[str] = Field(default_factory=list)
    tv_subtitle_priority: list[str] = Field(default_factory=list)


class EntryPayload(BaseModel):
    title_cn: str = ""
    bangumi_id: str = ""
    tmdb_id: str = ""
    year: int = 0
    month: int = 0
    season_number: int = 1
    media_type: str = "anime"
    region: str = "jp"
    title_romaji: str = ""
    title_raw: str = ""
    poster_url: str = ""
    summary: str = ""
    genres_json: str = "[]"
    tags_json: str = "[]"


class MetadataFetchPayload(BaseModel):
    bangumi_id: str = ""
    tmdb_id: str = ""
    provider: str = "bangumi"


class MediaCreatePayload(BaseModel):
    mode: str = "add"
    title: str = ""
    bangumi_id: str = ""
    tmdb_id: str = ""
    year: int = 0
    month: int = 0
    season_number: int = 1
    region: str = "jp"
    episode_number: int = 0
    resource_title: str = ""
    source_ref: str = ""
    subtitle_group: str = ""
    resolution: str = ""
    language: str = ""
    subtitle_format: str = ""
    subtitle_path: str = ""
    subtitle_url: str = ""
    subtitle_file_name: str = ""


class RssSubscriptionPayload(BaseModel):
    name: str = ""
    url: str = ""
    kind: str = "mikan"
    enabled: bool = True


class EpisodeResourcePayload(BaseModel):
    resource_id: int = 0
    title: str = ""
    subtitle_group: str = ""
    resolution: str = ""
    language: str = ""
    subtitle_format: str = ""
    selected: bool = True


class EpisodeSubtitlePayload(BaseModel):
    subtitle_id: int = 0
    language: str = ""
    subtitle_format: str = ""
    subtitle_path: str = ""
    subtitle_url: str = ""
    file_name: str = ""
    selected: bool = True


class EpisodeImportPayload(BaseModel):
    resources_text: str = ""
    subtitles_text: str = ""
    subtitle_format: str = "external"
    language: str = ""


class BatchSubtitlePayload(BaseModel):
    subtitles_text: str = ""
    file_names: list[str] = Field(default_factory=list)
    subtitle_format: str = "external"
    language: str = ""


class ScheduledJobPayload(BaseModel):
    enabled: bool = True
    interval_minutes: int = 1


class PipelineStartPayload(BaseModel):
    trigger_source: str = "manual"
    first_step_key: str = ""
    subject_type: str = ""
    subject_id: int = 0
    payload: dict[str, Any] = Field(default_factory=dict)
    message: str = ""


def row_to_dict(row: Any) -> dict[str, Any]:
    return dict(row) if row is not None else {}


def normalize_json_list_text(value: str) -> str:
    if not value:
        return "[]"
    raw = str(value).strip()
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return json.dumps([str(item).strip() for item in parsed if str(item).strip()], ensure_ascii=False)
    except Exception:
        pass
    items = [item.strip() for item in raw.replace(",", "\n").splitlines() if item.strip()]
    return json.dumps(items, ensure_ascii=False)


def subtitle_embedded_value(format_value: str) -> int:
    return 1 if str(format_value or "").strip().lower() in {"embedded", "hardsub", "burned"} else 0


def split_input_lines(value: str) -> list[str]:
    return [line.strip() for line in str(value or "").splitlines() if line.strip()]


def is_valid_resource_reference(value: str) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return False
    return text.startswith(("magnet:?", "http://", "https://", "ftp://", "thunder://", "ed2k://"))


def is_valid_subtitle_reference(value: str) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return False
    if text.startswith(("http://", "https://")):
        return True
    return text.endswith((".ass", ".srt", ".ssa", ".vtt", ".sup", ".sub"))


def parsed_episode_or_fallback(text: str, fallback: int) -> int:
    parsed = parse_episode(text)
    return parsed if parsed > 0 else max(1, fallback)


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


def clean_downloader_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for index, item in enumerate(items or []):
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type") or "").strip().lower()
        if item_type not in SUPPORTED_DOWNLOADER_TYPES:
            continue
        try:
            max_attempts = max(1, int(item.get("max_attempts") or 3))
        except (TypeError, ValueError):
            max_attempts = 3
        enabled_value = item.get("enabled", True)
        enabled = bool_setting(str(enabled_value).lower()) if isinstance(enabled_value, str) else bool(enabled_value)
        row: dict[str, Any] = {
            "id": str(item.get("id") or f"downloader-{index + 1}"),
            "name": str(item.get("name") or item_type).strip() or item_type,
            "type": item_type,
            "remote_dir": str(item.get("remote_dir") or "/Temp").strip() or "/Temp",
            "enabled": enabled,
            "max_attempts": max_attempts,
        }
        for key in (
            "rpc_url",
            "url",
            "token",
            "secret",
            "username",
            "password",
            "auth_mode",
            "access_token",
            "refresh_token",
            "proxy",
            "rclone_command",
            "rclone_config_path",
            "rclone_remote",
        ):
            if key in item:
                row[key] = str(item.get(key) or "").strip()
        cleaned.append(row)
    return cleaned


def first_enabled_downloader(downloaders: list[dict[str, Any]]) -> dict[str, Any]:
    return next((item for item in downloaders if item.get("enabled", True)), downloaders[0] if downloaders else {})


def derived_downloader_settings(downloaders: list[dict[str, Any]], previous: dict[str, str]) -> dict[str, str]:
    active = first_enabled_downloader(downloaders)
    backend = SUPPORTED_DOWNLOADER_TYPES.get(str(active.get("type") or "").strip().lower(), previous.get("download_backend") or "rclone")
    result = {
        "downloaders_json": json.dumps(downloaders, ensure_ascii=False),
        "download_backend": backend,
        "library_root": str(active.get("remote_dir") or previous.get("library_root") or "/Temp").strip() or "/Temp",
        "local_downloader_root": previous.get("local_downloader_root") or "/data/local-downloader",
        "rclone_command": previous.get("rclone_command") or "rclone",
        "rclone_config_path": previous.get("rclone_config_path") or "/data/rclone/rclone.conf",
        "rclone_remote": previous.get("rclone_remote") or "pikpak",
        "pikpak_auth_mode": previous.get("pikpak_auth_mode") or "token",
        "pikpak_username": previous.get("pikpak_username") or "",
        "pikpak_password": previous.get("pikpak_password") or "",
        "pikpak_access_token": previous.get("pikpak_access_token") or "",
        "pikpak_refresh_token": previous.get("pikpak_refresh_token") or "",
        "pikpak_proxy": previous.get("pikpak_proxy") or "",
    }
    if backend == "rclone":
        result["rclone_command"] = str(active.get("rclone_command") or result["rclone_command"]).strip() or "rclone"
        result["rclone_config_path"] = str(active.get("rclone_config_path") or result["rclone_config_path"]).strip()
        result["rclone_remote"] = str(active.get("rclone_remote") or result["rclone_remote"]).strip() or "pikpak"
        result["pikpak_username"] = str(active.get("username") or result["pikpak_username"]).strip()
        result["pikpak_password"] = str(active.get("password") or result["pikpak_password"])
    if backend == "api":
        result["pikpak_auth_mode"] = str(active.get("auth_mode") or result["pikpak_auth_mode"]).strip() or "token"
        result["pikpak_username"] = str(active.get("username") or result["pikpak_username"]).strip()
        result["pikpak_password"] = str(active.get("password") or result["pikpak_password"])
        result["pikpak_access_token"] = str(active.get("access_token") or result["pikpak_access_token"]).strip()
        result["pikpak_refresh_token"] = str(active.get("refresh_token") or result["pikpak_refresh_token"]).strip()
        result["pikpak_proxy"] = str(active.get("proxy") or result["pikpak_proxy"]).strip()
    return result


def split_candidate_values(value: Any) -> list[str]:
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


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
    result["work_display_title"] = work_display_title
    result["entry_scope_label"] = scope_label
    result["entry_badge_text"] = entry_badge_text(result)
    result["entry_display_title"] = str(result.get("display_title") or result.get("title_cn") or work_display_title).strip()
    result["entry_secondary_title"] = scope_label or work_display_title
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


def summarize_seasonal_entry(item: dict[str, Any]) -> dict[str, Any]:
    return dict(item)


def settings_response() -> dict[str, Any]:
    settings = get_settings()
    try:
        downloaders = json.loads(settings.get("downloaders_json", "[]") or "[]")
    except json.JSONDecodeError:
        downloaders = []
    return {
        "rss_url": settings.get("rss_url", ""),
        "rss_proxy": settings.get("rss_proxy", ""),
        "scan_interval_minutes": int(settings.get("scan_interval_minutes") or 60),
        "auto_scan": bool_setting(settings.get("auto_scan", "false")),
        "queue_dispatch_enabled": bool_setting(settings.get("queue_dispatch_enabled", "true")),
        "queue_dispatch_interval_minutes": int(settings.get("queue_dispatch_interval_minutes") or 1),
        "auto_generate_nfo": bool_setting(settings.get("auto_generate_nfo", "true")),
        "backfill_current_season": bool_setting(settings.get("backfill_current_season", "false")),
        "subtitle_priority": split_setting(settings.get("subtitle_priority", "")),
        "resolution_priority": split_setting(settings.get("resolution_priority", "")),
        "language_priority": split_setting(settings.get("language_priority", "")),
        "secondary_language_priority": split_setting(settings.get("secondary_language_priority", "")),
        "downloaders": clean_downloader_items(downloaders if isinstance(downloaders, list) else []),
        "episode_name_template": settings.get("episode_name_template", ""),
        "movie_name_template": settings.get("movie_name_template", ""),
        "tv_name_template": settings.get("tv_name_template", ""),
        "movie_quality_priority": split_setting(settings.get("movie_quality_priority", "")),
        "movie_source_priority": split_setting(settings.get("movie_source_priority", "")),
        "movie_subtitle_priority": split_setting(settings.get("movie_subtitle_priority", "")),
        "tv_quality_priority": split_setting(settings.get("tv_quality_priority", "")),
        "tv_source_priority": split_setting(settings.get("tv_source_priority", "")),
        "tv_subtitle_priority": split_setting(settings.get("tv_subtitle_priority", "")),
    }


def normalize_api_media_type(value: str) -> str:
    key = str(value or "anime").strip().lower()
    if key in {"anime", "movie", "tv"}:
        return key
    raise HTTPException(status_code=404, detail="未知媒体类型")


def media_items_response(media_type: str) -> dict[str, Any]:
    media_type = normalize_api_media_type(media_type)
    rows = dashboard_data().get("library_items", [])
    items = [
        item
        for item in rows
        if str(item.get("media_type") or "anime").lower() == media_type
    ]
    return {"type": media_type, "items": items}


def build_media_entry_response(media_type: str, entry_id: int) -> dict[str, Any]:
    media_type = normalize_api_media_type(media_type)
    detail = build_entry_response(entry_id)
    entry = detail.get("entry") or {}
    if not entry:
        raise HTTPException(status_code=404, detail="媒体条目不存在")
    entry_media_type = normalize_api_media_type(str(entry.get("media_type") or "anime"))
    if entry_media_type != media_type:
        raise HTTPException(status_code=404, detail="媒体条目类型不匹配")
    return detail


def media_library_key(media_type: str) -> str:
    return {
        "anime": "anime_library",
        "movie": "movies",
        "tv": "tv",
    }.get(media_type, "anime_library")


def create_media_entry(media_type: str, payload: MediaCreatePayload) -> dict[str, Any]:
    media_type = normalize_api_media_type(media_type)
    title = payload.title.strip() or payload.resource_title.strip() or payload.source_ref.strip() or "未命名媒体"
    bangumi_id = payload.bangumi_id.strip()
    tmdb_id = payload.tmdb_id.strip()
    season_number = max(1, int(payload.season_number or 1))
    year = max(0, int(payload.year or 0))
    month = max(0, min(12, int(payload.month or 0)))
    region = payload.region.strip() or "jp"
    source_ref = payload.source_ref.strip()
    ts = now()
    work_key = fingerprint(title, bangumi_id or tmdb_id)
    entry_key = fingerprint(f"{media_type}:{bangumi_id or tmdb_id or title}:S{season_number}", "")
    release_id = 0
    with connect() as conn:
        target_library = conn.execute("SELECT id FROM media_libraries WHERE key=?", (media_library_key(media_type),)).fetchone()
        target_library_id = int(target_library["id"] or 0) if target_library else 0
        conn.execute(
            """
            INSERT INTO works
              (root_key, title_root, title_root_raw, bangumi_id, metadata_source, hidden, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'manual', 0, ?, ?)
            ON CONFLICT(root_key) DO UPDATE SET
              title_root=excluded.title_root,
              title_root_raw=excluded.title_root_raw,
              bangumi_id=CASE WHEN works.bangumi_id='' THEN excluded.bangumi_id ELSE works.bangumi_id END,
              updated_at=excluded.updated_at
            """,
            (work_key, title, title, bangumi_id, ts, ts),
        )
        work = conn.execute("SELECT id FROM works WHERE root_key=?", (work_key,)).fetchone()
        work_id = int(work["id"] or 0)
        conn.execute(
            """
            INSERT INTO entries
              (work_id, fingerprint, domain_kind, media_type, region, source_provider, metadata_provider,
               external_id, target_library_id, display_title, title_root, title_raw, title_cn,
               bangumi_id, tmdb_id, year, month, season_number, created_at, updated_at)
            VALUES (?, ?, 'library', ?, ?, ?, 'manual', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(fingerprint) DO UPDATE SET
              media_type=excluded.media_type,
              region=excluded.region,
              target_library_id=excluded.target_library_id,
              display_title=excluded.display_title,
              title_root=excluded.title_root,
              title_cn=excluded.title_cn,
              bangumi_id=CASE WHEN entries.bangumi_id='' THEN excluded.bangumi_id ELSE entries.bangumi_id END,
              tmdb_id=CASE WHEN entries.tmdb_id='' THEN excluded.tmdb_id ELSE entries.tmdb_id END,
              year=CASE WHEN excluded.year>0 THEN excluded.year ELSE entries.year END,
              month=CASE WHEN excluded.month>0 THEN excluded.month ELSE entries.month END,
              updated_at=excluded.updated_at
            """,
            (
                work_id,
                entry_key,
                media_type,
                region,
                payload.mode.strip() or "manual",
                bangumi_id or tmdb_id,
                target_library_id,
                title,
                title,
                title,
                title,
                bangumi_id,
                tmdb_id,
                year,
                month,
                season_number,
                ts,
                ts,
            ),
        )
        entry = conn.execute("SELECT * FROM entries WHERE fingerprint=?", (entry_key,)).fetchone()
        entry_id = int(entry["id"] or 0)
        conn.execute(
            """
            INSERT INTO library_entries (entry_id, source_type, source_ref, wanted, archived, created_at, updated_at)
            VALUES (?, ?, ?, 1, 0, ?, ?)
            ON CONFLICT(entry_id) DO UPDATE SET
              source_type=excluded.source_type,
              source_ref=excluded.source_ref,
              wanted=1,
              archived=0,
              updated_at=excluded.updated_at
            """,
            (entry_id, payload.mode.strip() or "manual", source_ref, ts, ts),
        )
        episode_number = max(0, int(payload.episode_number or 0))
        if episode_number > 0 or payload.resource_title.strip() or source_ref:
            episode_number = episode_number or 1
            conn.execute(
                """
                INSERT INTO episodes (series_id, entry_id, episode_number, title, status, created_at, updated_at)
                VALUES (?, ?, ?, '', 'configured', ?, ?)
                ON CONFLICT(series_id, episode_number) DO UPDATE SET
                  entry_id=excluded.entry_id,
                  status=excluded.status,
                  updated_at=excluded.updated_at
                """,
                (entry_id, entry_id, episode_number, ts, ts),
            )
            episode = conn.execute(
                "SELECT id FROM episodes WHERE entry_id=? AND episode_number=? ORDER BY id DESC LIMIT 1",
                (entry_id, episode_number),
            ).fetchone()
            episode_id = int(episode["id"] or 0) if episode else 0
            resource_ref = source_ref or payload.resource_title.strip() or f"manual:{entry_id}:{episode_number}"
            torrent_url = source_ref if source_ref.startswith("http") else ""
            magnet = source_ref if source_ref.startswith("magnet:") else ""
            if torrent_url or magnet:
                digest = hashlib.sha1(resource_ref.encode("utf-8", errors="ignore")).hexdigest()[:20]
                guid = f"manual:{entry_id}:{episode_number}:{digest}"
                conn.execute(
                    "UPDATE releases SET selected=0 WHERE entry_id=? AND episode_number=?",
                    (entry_id, episode_number),
                )
                conn.execute(
                    """
                    INSERT INTO releases
                      (series_id, entry_id, episode_number, guid, title, subtitle_group, resolution,
                       language, subtitle_format, torrent_url, magnet, published_at, selected, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                    ON CONFLICT(guid) DO UPDATE SET
                      series_id=excluded.series_id,
                      entry_id=excluded.entry_id,
                      episode_number=excluded.episode_number,
                      title=excluded.title,
                      subtitle_group=excluded.subtitle_group,
                      resolution=excluded.resolution,
                      language=excluded.language,
                      subtitle_format=excluded.subtitle_format,
                      torrent_url=excluded.torrent_url,
                      magnet=excluded.magnet,
                      selected=1,
                      updated_at=excluded.updated_at
                    """,
                    (
                        entry_id,
                        entry_id,
                        episode_number,
                        guid,
                        payload.resource_title.strip() or resource_ref,
                        payload.subtitle_group.strip(),
                        payload.resolution.strip(),
                        payload.language.strip(),
                        payload.subtitle_format.strip(),
                        torrent_url,
                        magnet,
                        ts,
                        ts,
                        ts,
                    ),
                )
                release = conn.execute("SELECT id FROM releases WHERE guid=?", (guid,)).fetchone()
                release_id = int(release["id"] or 0) if release else 0
                conn.execute(
                    "UPDATE episode_resources SET selected=0 WHERE entry_id=? AND episode_number=?",
                    (entry_id, episode_number),
                )
            conn.execute(
                """
                INSERT INTO episode_resources
                  (entry_id, episode_id, episode_number, source_type, source_ref, release_id, title,
                   subtitle_group, resolution, language, subtitle_format, torrent_url, magnet,
                   selected, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 'available', ?, ?)
                ON CONFLICT(entry_id, episode_number, source_type, source_ref) DO UPDATE SET
                  episode_id=excluded.episode_id,
                  release_id=excluded.release_id,
                  title=excluded.title,
                  subtitle_group=excluded.subtitle_group,
                  resolution=excluded.resolution,
                  language=excluded.language,
                  subtitle_format=excluded.subtitle_format,
                  torrent_url=excluded.torrent_url,
                  magnet=excluded.magnet,
                  selected=1,
                  status='available',
                  updated_at=excluded.updated_at
                """,
                (
                    entry_id,
                    episode_id,
                    episode_number,
                    payload.mode.strip() or "manual",
                    resource_ref,
                    release_id,
                    payload.resource_title.strip() or resource_ref,
                    payload.subtitle_group.strip(),
                    payload.resolution.strip(),
                    payload.language.strip(),
                    payload.subtitle_format.strip(),
                    torrent_url,
                    magnet,
                    ts,
                    ts,
                ),
            )
            resource_row = conn.execute(
                """
                SELECT id FROM episode_resources
                WHERE entry_id=? AND episode_number=? AND source_type=? AND source_ref=?
                """,
                (entry_id, episode_number, payload.mode.strip() or "manual", resource_ref),
            ).fetchone()
            episode_resource_id = int(resource_row["id"] or 0) if resource_row else 0
            if payload.subtitle_path.strip() or payload.subtitle_url.strip() or payload.subtitle_file_name.strip():
                conn.execute(
                    """
                    INSERT INTO episode_subtitles
                      (episode_id, episode_resource_id, entry_id, episode_number, language, subtitle_format,
                       subtitle_path, subtitle_url, file_name, embedded, selected, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        episode_id,
                        episode_resource_id,
                        entry_id,
                        episode_number,
                        payload.language.strip(),
                        payload.subtitle_format.strip(),
                        payload.subtitle_path.strip(),
                        payload.subtitle_url.strip(),
                        payload.subtitle_file_name.strip(),
                        subtitle_embedded_value(payload.subtitle_format),
                        ts,
                        ts,
                    ),
                )
    run_id = 0
    if release_id > 0:
        run_id = start_pipeline(
            "library_backfill",
            trigger_source="media_wizard",
            first_step_key="download",
            subject_type="release",
            subject_id=release_id,
            payload={
                "_dedupe_key": f"download:entry:{entry_id}:episode:{int(payload.episode_number or 0)}",
                "entry_id": entry_id,
                "release_id": release_id,
                "episode_number": int(payload.episode_number or 0),
                "domain_kind": "library",
            },
            message=f"媒体向导收录后下载: {title}",
        )
    log("info", f"媒体条目已收录: type={media_type} entry_id={entry_id} release_id={release_id} title={title}")
    detail = build_entry_response(entry_id)
    detail["download_run_id"] = run_id
    return detail


def empty_entry_response() -> dict[str, Any]:
    return {
        "entry": None,
        "episodes": [],
        "episode_resources": [],
        "episode_subtitles": [],
        "groups": [],
        "resolutions": [],
        "languages": [],
    }


def build_entry_response(entry_id: int) -> dict[str, Any]:
    with connect() as conn:
        entry = conn.execute("SELECT * FROM entries WHERE id=?", (entry_id,)).fetchone()
        if not entry:
            return empty_entry_response()
        episodes = conn.execute(
            "SELECT * FROM episodes WHERE entry_id=? ORDER BY episode_number ASC, id ASC",
            (entry_id,),
        ).fetchall()
        episode_resources = conn.execute(
            """
            SELECT er.*,
              dj.id AS download_job_id,
              dj.status AS download_status,
              dj.retry_after AS download_retry_after,
              dj.last_error AS download_error,
              la.id AS local_asset_id,
              la.nfo_status AS local_nfo_status
            FROM episode_resources er
            LEFT JOIN download_jobs dj ON dj.id=(
              SELECT id
              FROM download_jobs
              WHERE entry_id=er.entry_id
                AND episode_number=er.episode_number
              ORDER BY CASE status
                WHEN 'running' THEN 0
                WHEN 'submitted' THEN 1
                WHEN 'pending' THEN 2
                WHEN 'paused' THEN 3
                WHEN 'failed' THEN 4
                WHEN 'cancelled' THEN 5
                WHEN 'completed' THEN 6
                ELSE 7
              END, updated_at DESC, id DESC
              LIMIT 1
            )
            LEFT JOIN local_assets la
              ON la.entry_id=er.entry_id
             AND la.episode_number=er.episode_number
             AND la.status='synced'
            WHERE er.entry_id=?
            ORDER BY er.episode_number ASC, er.selected DESC, er.id DESC
            """,
            (entry_id,),
        ).fetchall()
        episode_subtitles = conn.execute(
            "SELECT * FROM episode_subtitles WHERE entry_id=? ORDER BY episode_number ASC, selected DESC, id DESC",
            (entry_id,),
        ).fetchall()
    groups = sorted({r["subtitle_group"] for r in episode_resources if r["subtitle_group"]})
    resolutions = sorted({r["resolution"] for r in episode_resources if r["resolution"]})
    languages = sorted({r["language"] for r in episode_resources if r["language"]})
    entry_payload = enrich_catalog_entry({**row_to_dict(entry), "domain_kind": entry["domain_kind"]})
    for legacy_key in ("auto_download", "selected_group", "selected_resolution", "backfill_mode"):
        entry_payload.pop(legacy_key, None)
    return {
        "entry": entry_payload,
        "episodes": rows_to_dicts(episodes),
        "episode_resources": rows_to_dicts(episode_resources),
        "episode_subtitles": rows_to_dicts(episode_subtitles),
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
            SET title_cn=?,
                bangumi_id=?,
                tmdb_id=?,
                year=?,
                month=?,
                season_number=?,
                media_type=?,
                region=?,
                title_romaji=?,
                title_raw=?,
                poster_url=?,
                summary=?,
                genres_json=?,
                tags_json=?,
                updated_at=?
            WHERE id=?
            """,
            (
                payload.title_cn.strip(),
                payload.bangumi_id.strip(),
                payload.tmdb_id.strip(),
                payload.year,
                max(0, min(12, int(payload.month or 0))),
                payload.season_number,
                normalize_api_media_type(payload.media_type),
                payload.region.strip() or "jp",
                payload.title_romaji.strip(),
                payload.title_raw.strip(),
                payload.poster_url.strip(),
                payload.summary.strip(),
                normalize_json_list_text(payload.genres_json),
                normalize_json_list_text(payload.tags_json),
                now(),
                entry_id,
            ),
        )
        should_refresh_seasonal = domain_kind == "seasonal"
        if domain_kind == "seasonal":
            conn.execute(
                """
                UPDATE series
                SET title_cn=?, bangumi_id=?, tmdb_id=?, year=?, month=?, season_number=?, updated_at=?
                WHERE bangumi_id=?
                """,
                (
                    payload.title_cn.strip(),
                    payload.bangumi_id.strip(),
                    payload.tmdb_id.strip(),
                    payload.year,
                    max(0, min(12, int(payload.month or 0))),
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


def archive_seasonal_entry(entry_id: int) -> dict[str, str]:
    ts = now()
    with connect() as conn:
        entry = conn.execute(
            "SELECT display_title, domain_kind FROM entries WHERE id=?",
            (entry_id,),
        ).fetchone()
        if not entry:
            return {"status": "not_found", "message": "番剧不存在"}
        seasonal = conn.execute("SELECT id FROM seasonal_entries WHERE entry_id=?", (entry_id,)).fetchone()
        if not seasonal:
            return {"status": "invalid_domain", "message": "该条目不属于新番追番"}
        conn.execute(
            """
            UPDATE seasonal_entries
            SET following=0, archived=1, updated_at=?
            WHERE entry_id=?
            """,
            (ts, entry_id),
        )
        conn.execute(
            """
            INSERT INTO library_entries (entry_id, source_type, source_ref, wanted, archived, created_at, updated_at)
            VALUES (?, 'seasonal_archive', '', 1, 0, ?, ?)
            ON CONFLICT(entry_id) DO UPDATE SET
              wanted=1,
              archived=0,
              updated_at=excluded.updated_at
            """,
            (entry_id, ts, ts),
        )
        conn.execute(
            """
            UPDATE entries
            SET domain_kind='library',
                hidden=0,
                updated_at=?
            WHERE id=?
            """,
            (ts, entry_id),
        )
    log("info", f"新番已归档到番剧库: {entry['display_title']}")
    return {"status": "completed", "message": "已归档到番剧库"}


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
    queue_minutes = max(1, int(settings.get("queue_dispatch_interval_minutes") or 1))
    rss_enabled = bool_setting(settings.get("auto_scan", "false"))
    queue_dispatch_enabled = bool_setting(settings.get("queue_dispatch_enabled", "true"))
    runtime_store.set_scheduler_sync(
        "rss_scan",
        interval_minutes=minutes,
        enabled=int(rss_enabled),
        updated_at=now(),
    )
    runtime_store.set_scheduler_sync(
        "queue_dispatch",
        interval_minutes=queue_minutes,
        enabled=int(queue_dispatch_enabled),
        debounce_seconds=int(QUEUE_DEBOUNCE_SECONDS),
        updated_at=now(),
    )
    for name in queue_handlers:
        runtime_store.set_scheduler_sync(queue_job_key(name), debounce_seconds=int(QUEUE_DEBOUNCE_SECONDS), updated_at=now())
    if rss_enabled:
        scheduler.add_job(lambda: asyncio.create_task(scheduled_scan()), "interval", minutes=minutes, id="rss_scan")
    if queue_dispatch_enabled:
        scheduler.add_job(lambda: asyncio.create_task(dispatch_ready_queues()), "interval", minutes=queue_minutes, id="queue_dispatch")


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


app = FastAPI(title="AniTrack", lifespan=lifespan)


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
              e.month,
              e.season_number,
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
              COUNT(DISTINCT la.id) AS local_asset_count
            FROM entries e
            JOIN seasonal_entries se ON se.entry_id=e.id
            JOIN works w ON w.id=e.work_id
            LEFT JOIN episodes ep ON ep.entry_id=e.id
            LEFT JOIN releases r ON r.entry_id=e.id
            LEFT JOIN download_jobs cs ON cs.release_id=r.id
            LEFT JOIN download_artifacts ca ON ca.release_id=r.id
            LEFT JOIN local_assets la ON la.release_id=r.id AND la.status='synced'
            WHERE COALESCE(e.hidden, 0)=0
              AND e.bangumi_id != ''
              AND COALESCE(se.following, 1)=1
              AND COALESCE(se.archived, 0)=0
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
              e.month,
              e.season_number,
              w.title_root AS work_title,
              COUNT(DISTINCT ep.id) AS episode_count,
              COUNT(DISTINCT r.id) AS release_count,
              COUNT(DISTINCT ca.id) AS download_artifact_count,
              COUNT(DISTINCT la.id) AS local_asset_count
            FROM entries e
            LEFT JOIN library_entries le ON le.entry_id=e.id
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
        seasonal_sync_calendar = conn.execute(
            """
            SELECT la.id,
              e.id AS entry_id,
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
            WHERE la.status='synced'
              AND COALESCE(e.hidden, 0)=0
              AND COALESCE(se.following, 1)=1
              AND COALESCE(se.archived, 0)=0
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
            LEFT JOIN download_artifacts ca ON ca.release_id=r.id
            LEFT JOIN local_assets la ON la.release_id=r.id
            WHERE COALESCE(e.hidden, 0)=0
              AND COALESCE(se.following, 1)=1
              AND COALESCE(se.archived, 0)=0
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
              AND COALESCE(se.following, 1)=1
              AND COALESCE(se.archived, 0)=0
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
    operations_list = [
        dict(item)
        for item in runtime_snapshot.get("operations", [])
        if str(item.get("status") or "") in {"running", "failed", "cancelled"}
    ][:20]
    queue_details = queue_detail_map()
    seasonal_rows = [
        enrich_catalog_entry(summarize_seasonal_entry(row))
        for row in rows_to_dicts(seasonal_items)
    ]
    library_rows = [enrich_catalog_entry(row) for row in rows_to_dicts(library_items)]
    seasonal_calendar_rows = [enrich_catalog_entry(row) for row in rows_to_dicts(seasonal_sync_calendar)]
    seasonal_update_rows = [enrich_catalog_entry(row) for row in rows_to_dicts(seasonal_update_calendar)]
    recent_synced_rows = [enrich_catalog_entry(row) for row in rows_to_dicts(recent_synced_entries)]
    return {
        "seasonal_items": seasonal_rows,
        "library_items": library_rows,
        "seasonal_sync_calendar": seasonal_calendar_rows,
        "seasonal_update_calendar": seasonal_update_rows,
        "recent_synced_seasonal_entries": recent_synced_rows,
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
    downloaders = clean_downloader_items(payload.downloaders)
    downloader_settings = derived_downloader_settings(downloaders, previous)
    save_settings(
        {
            "rss_url": payload.rss_url.strip(),
            "rss_proxy": payload.rss_proxy.strip(),
            "scan_interval_minutes": payload.scan_interval_minutes,
            "auto_scan": str(payload.auto_scan).lower(),
            "queue_dispatch_enabled": str(payload.queue_dispatch_enabled).lower(),
            "queue_dispatch_interval_minutes": payload.queue_dispatch_interval_minutes,
            "auto_download_unique": "true",
            "auto_download_by_priority": "true",
            "auto_generate_nfo": str(payload.auto_generate_nfo).lower(),
            "backfill_current_season": str(payload.backfill_current_season).lower(),
            "default_backfill": "season" if payload.backfill_current_season else "none",
            "subtitle_priority": "\n".join(payload.subtitle_priority),
            "resolution_priority": "\n".join(payload.resolution_priority),
            "language_priority": "\n".join(payload.language_priority),
            "secondary_language_priority": "\n".join(payload.secondary_language_priority),
            **downloader_settings,
            "local_library_root": str(MEDIA_ROOT),
            "auto_sync_following": "true",
            "nfo_output_root": "",
            "episode_name_template": payload.episode_name_template.strip(),
            "movie_name_template": payload.movie_name_template.strip(),
            "tv_name_template": payload.tv_name_template.strip(),
            "movie_quality_priority": "\n".join(payload.movie_quality_priority),
            "movie_source_priority": "\n".join(payload.movie_source_priority),
            "movie_subtitle_priority": "\n".join(payload.movie_subtitle_priority),
            "tv_quality_priority": "\n".join(payload.tv_quality_priority),
            "tv_source_priority": "\n".join(payload.tv_source_priority),
            "tv_subtitle_priority": "\n".join(payload.tv_subtitle_priority),
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
            "backfill_current_season",
            "episode_name_template",
            "movie_name_template",
            "tv_name_template",
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


@app.put("/api/scheduled-jobs/{job_key}")
async def api_update_scheduled_job(job_key: str, payload: ScheduledJobPayload) -> dict[str, Any]:
    interval = max(1, int(payload.interval_minutes or 1))
    enabled = str(bool(payload.enabled)).lower()
    if job_key == "rss_scan":
        save_settings({
            "auto_scan": enabled,
            "scan_interval_minutes": str(interval),
        })
    elif job_key == "queue_dispatch":
        save_settings({
            "queue_dispatch_enabled": enabled,
            "queue_dispatch_interval_minutes": str(interval),
        })
    else:
        raise HTTPException(status_code=404, detail="定时任务不存在")
    reschedule()
    log("info", f"定时任务已更新: job_key={job_key} enabled={enabled} interval={interval}m")
    return {"status": "saved", "settings": settings_response()}


@app.get("/api/rss-subscriptions")
async def api_rss_subscriptions() -> dict[str, Any]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM rss_subscriptions ORDER BY enabled DESC, id ASC"
        ).fetchall()
    return {"items": rows_to_dicts(rows)}


@app.post("/api/rss-subscriptions")
async def api_create_rss_subscription(payload: RssSubscriptionPayload) -> dict[str, Any]:
    url = payload.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="RSS 地址不能为空")
    kind = payload.kind.strip() or "mikan"
    if kind != "mikan":
        raise HTTPException(status_code=400, detail="当前只支持 Mikan RSS")
    ts = now()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO rss_subscriptions (name, url, kind, enabled, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(url) DO UPDATE SET
              name=excluded.name,
              kind=excluded.kind,
              enabled=excluded.enabled,
              updated_at=excluded.updated_at
            """,
            (payload.name.strip() or "Mikan RSS", url, kind, int(payload.enabled), ts, ts),
        )
        row = conn.execute("SELECT * FROM rss_subscriptions WHERE url=?", (url,)).fetchone()
    if payload.enabled:
        save_settings({"rss_url": url})
    log("info", f"RSS 订阅已保存: kind={kind} url={url}")
    return {"status": "saved", "item": row_to_dict(row)}


@app.put("/api/rss-subscriptions/{subscription_id}")
async def api_update_rss_subscription(subscription_id: int, payload: RssSubscriptionPayload) -> dict[str, Any]:
    url = payload.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="RSS 地址不能为空")
    kind = payload.kind.strip() or "mikan"
    if kind != "mikan":
        raise HTTPException(status_code=400, detail="当前只支持 Mikan RSS")
    ts = now()
    with connect() as conn:
        existing = conn.execute("SELECT id FROM rss_subscriptions WHERE id=?", (subscription_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="RSS 订阅不存在")
        conn.execute(
            """
            UPDATE rss_subscriptions
            SET name=?, url=?, kind=?, enabled=?, updated_at=?
            WHERE id=?
            """,
            (payload.name.strip() or "Mikan RSS", url, kind, int(payload.enabled), ts, subscription_id),
        )
        row = conn.execute("SELECT * FROM rss_subscriptions WHERE id=?", (subscription_id,)).fetchone()
    if payload.enabled:
        save_settings({"rss_url": url})
    return {"status": "saved", "item": row_to_dict(row)}


@app.delete("/api/rss-subscriptions/{subscription_id}")
async def api_delete_rss_subscription(subscription_id: int) -> dict[str, str]:
    with connect() as conn:
        row = conn.execute("SELECT id FROM rss_subscriptions WHERE id=?", (subscription_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="RSS 订阅不存在")
        conn.execute("DELETE FROM rss_subscriptions WHERE id=?", (subscription_id,))
    return {"status": "deleted", "message": "RSS 订阅已删除"}


@app.get("/api/media/{media_type}")
async def api_media_items(media_type: str) -> dict[str, Any]:
    return media_items_response(media_type)


@app.post("/api/media/{media_type}")
async def api_create_media_entry(media_type: str, payload: MediaCreatePayload) -> dict[str, Any]:
    return create_media_entry(media_type, payload)


@app.get("/api/media/{media_type}/{entry_id}")
async def api_media_entry(media_type: str, entry_id: int) -> dict[str, Any]:
    return build_media_entry_response(media_type, entry_id)


@app.put("/api/media/{media_type}/{entry_id}")
async def api_update_media_entry(media_type: str, entry_id: int, payload: EntryPayload) -> dict[str, Any]:
    normalize_api_media_type(media_type)
    return save_entry_payload(entry_id, payload, expected_domain=None)


@app.post("/api/media/{media_type}/{entry_id}/metadata/fetch")
async def api_fetch_media_metadata(media_type: str, entry_id: int, payload: MetadataFetchPayload) -> dict[str, Any]:
    normalize_api_media_type(media_type)
    bangumi_id = payload.bangumi_id.strip()
    tmdb_id = payload.tmdb_id.strip()
    if bangumi_id or tmdb_id:
        with connect() as conn:
            exists = conn.execute("SELECT id FROM entries WHERE id=?", (entry_id,)).fetchone()
            if not exists:
                raise HTTPException(status_code=404, detail="媒体条目不存在")
            conn.execute(
                "UPDATE entries SET bangumi_id=CASE WHEN ?='' THEN bangumi_id ELSE ? END, tmdb_id=CASE WHEN ?='' THEN tmdb_id ELSE ? END, updated_at=? WHERE id=?",
                (bangumi_id, bangumi_id, tmdb_id, tmdb_id, now(), entry_id),
            )
    with connect() as conn:
        entry = conn.execute("SELECT bangumi_id FROM entries WHERE id=?", (entry_id,)).fetchone()
    if not entry:
        raise HTTPException(status_code=404, detail="媒体条目不存在")
    if not str(entry["bangumi_id"] or "").strip():
        raise HTTPException(status_code=400, detail="当前扒信息需要先填写 Bangumi ID")
    await refresh_entry_metadata(entry_id, get_settings().get("rss_proxy", ""))
    log("info", f"媒体元数据已刷新: entry_id={entry_id} provider=bangumi")
    return build_media_entry_response(media_type, entry_id)


@app.get("/api/entries/{entry_id}/episodes")
async def api_entry_episodes(entry_id: int) -> dict[str, Any]:
    detail = build_entry_response(entry_id)
    if not detail.get("entry"):
        raise HTTPException(status_code=404, detail="媒体条目不存在")
    return {
        "entry": detail.get("entry"),
        "episodes": detail.get("episodes", []),
        "episode_resources": detail.get("episode_resources", []),
        "episode_subtitles": detail.get("episode_subtitles", []),
    }


@app.post("/api/entries/{entry_id}/resources/import")
async def api_import_entry_resources(entry_id: int, payload: EpisodeImportPayload) -> dict[str, Any]:
    resource_lines = split_input_lines(payload.resources_text)
    subtitle_lines = split_input_lines(payload.subtitles_text)
    if not resource_lines:
        raise HTTPException(status_code=400, detail="请至少填写一个资源链接")
    invalid_resources = [line for line in resource_lines if not is_valid_resource_reference(line)]
    if invalid_resources:
        raise HTTPException(status_code=400, detail=f"资源链接格式无效: {invalid_resources[0]}")
    invalid_subtitles = [line for line in subtitle_lines if not is_valid_subtitle_reference(line)]
    if invalid_subtitles:
        raise HTTPException(status_code=400, detail=f"字幕链接或文件名格式无效: {invalid_subtitles[0]}")
    ts = now()
    created = 0
    with connect() as conn:
        entry = conn.execute("SELECT * FROM entries WHERE id=?", (entry_id,)).fetchone()
        if not entry:
            raise HTTPException(status_code=404, detail="媒体条目不存在")
        for index, line in enumerate(resource_lines, start=1):
            episode_number = parsed_episode_or_fallback(line, index)
            conn.execute(
                """
                INSERT INTO episodes (series_id, entry_id, episode_number, title, status, created_at, updated_at)
                VALUES (?, ?, ?, '', 'configured', ?, ?)
                ON CONFLICT(series_id, episode_number) DO UPDATE SET
                  entry_id=excluded.entry_id,
                  status=excluded.status,
                  updated_at=excluded.updated_at
                """,
                (entry_id, entry_id, episode_number, ts, ts),
            )
            episode = conn.execute(
                "SELECT id FROM episodes WHERE entry_id=? AND episode_number=? ORDER BY id DESC LIMIT 1",
                (entry_id, episode_number),
            ).fetchone()
            episode_id = int(episode["id"] or 0) if episode else 0
            torrent_url = line if line.startswith("http") else ""
            magnet = line if line.startswith("magnet:") else ""
            digest = hashlib.sha1(line.encode("utf-8", errors="ignore")).hexdigest()[:20]
            guid = f"manual:{entry_id}:{episode_number}:{digest}"
            release_id = 0
            if torrent_url or magnet:
                conn.execute("UPDATE releases SET selected=0 WHERE entry_id=? AND episode_number=?", (entry_id, episode_number))
                conn.execute(
                    """
                    INSERT INTO releases
                      (series_id, entry_id, episode_number, guid, title, language, subtitle_format,
                       torrent_url, magnet, published_at, selected, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                    ON CONFLICT(guid) DO UPDATE SET
                      title=excluded.title,
                      torrent_url=excluded.torrent_url,
                      magnet=excluded.magnet,
                      selected=1,
                      updated_at=excluded.updated_at
                    """,
                    (entry_id, entry_id, episode_number, guid, line, payload.language.strip(), payload.subtitle_format.strip(), torrent_url, magnet, ts, ts, ts),
                )
                release = conn.execute("SELECT id FROM releases WHERE guid=?", (guid,)).fetchone()
                release_id = int(release["id"] or 0) if release else 0
            conn.execute("UPDATE episode_resources SET selected=0 WHERE entry_id=? AND episode_number=?", (entry_id, episode_number))
            conn.execute(
                """
                INSERT INTO episode_resources
                  (entry_id, episode_id, episode_number, source_type, source_ref, release_id, title,
                   language, subtitle_format, torrent_url, magnet, selected, status, created_at, updated_at)
                VALUES (?, ?, ?, 'manual', ?, ?, ?, ?, ?, ?, ?, 1, 'available', ?, ?)
                ON CONFLICT(entry_id, episode_number, source_type, source_ref) DO UPDATE SET
                  episode_id=excluded.episode_id,
                  release_id=excluded.release_id,
                  title=excluded.title,
                  language=excluded.language,
                  subtitle_format=excluded.subtitle_format,
                  torrent_url=excluded.torrent_url,
                  magnet=excluded.magnet,
                  selected=1,
                  updated_at=excluded.updated_at
                """,
                (entry_id, episode_id, episode_number, line, release_id, line, payload.language.strip(), payload.subtitle_format.strip(), torrent_url, magnet, ts, ts),
            )
            created += 1
        for index, line in enumerate(subtitle_lines, start=1):
            episode_number = parsed_episode_or_fallback(line, index)
            episode = conn.execute(
                "SELECT id FROM episodes WHERE entry_id=? AND episode_number=? ORDER BY id DESC LIMIT 1",
                (entry_id, episode_number),
            ).fetchone()
            if not episode:
                continue
            conn.execute("UPDATE episode_subtitles SET selected=0 WHERE entry_id=? AND episode_number=?", (entry_id, episode_number))
            conn.execute(
                """
                INSERT INTO episode_subtitles
                  (episode_id, entry_id, episode_number, language, subtitle_format, subtitle_url,
                   file_name, embedded, selected, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    int(episode["id"]),
                    entry_id,
                    episode_number,
                    payload.language.strip(),
                    payload.subtitle_format.strip() or "external",
                    line if line.startswith("http") else "",
                    "" if line.startswith("http") else Path(line).name,
                    subtitle_embedded_value(payload.subtitle_format),
                    ts,
                    ts,
                ),
            )
    log("info", f"手动导入集数资源: entry_id={entry_id} count={created}")
    return {"status": "saved", "count": created, "detail": build_entry_response(entry_id)}


@app.post("/api/entries/{entry_id}/subtitles/batch")
async def api_batch_entry_subtitles(entry_id: int, payload: BatchSubtitlePayload) -> dict[str, Any]:
    subtitle_items = split_input_lines(payload.subtitles_text) + [item.strip() for item in payload.file_names if item.strip()]
    if not subtitle_items:
        raise HTTPException(status_code=400, detail="请填写字幕链接或选择字幕文件")
    invalid_subtitles = [item for item in subtitle_items if not is_valid_subtitle_reference(item)]
    if invalid_subtitles:
        raise HTTPException(status_code=400, detail=f"字幕链接或文件名格式无效: {invalid_subtitles[0]}")
    ts = now()
    saved = 0
    with connect() as conn:
        entry = conn.execute("SELECT id FROM entries WHERE id=?", (entry_id,)).fetchone()
        if not entry:
            raise HTTPException(status_code=404, detail="媒体条目不存在")
        for index, item in enumerate(subtitle_items, start=1):
            episode_number = parsed_episode_or_fallback(item, index)
            episode = conn.execute(
                "SELECT id FROM episodes WHERE entry_id=? AND episode_number=? ORDER BY id DESC LIMIT 1",
                (entry_id, episode_number),
            ).fetchone()
            if not episode:
                continue
            conn.execute("UPDATE episode_subtitles SET selected=0 WHERE entry_id=? AND episode_number=?", (entry_id, episode_number))
            conn.execute(
                """
                INSERT INTO episode_subtitles
                  (episode_id, entry_id, episode_number, language, subtitle_format, subtitle_url,
                   file_name, embedded, selected, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    int(episode["id"]),
                    entry_id,
                    episode_number,
                    payload.language.strip(),
                    payload.subtitle_format.strip() or "external",
                    item if item.startswith("http") else "",
                    "" if item.startswith("http") else Path(item).name,
                    subtitle_embedded_value(payload.subtitle_format),
                    ts,
                    ts,
                ),
            )
            saved += 1
    log("info", f"批量配置字幕: entry_id={entry_id} count={saved}")
    return {"status": "saved", "count": saved, "detail": build_entry_response(entry_id)}


@app.put("/api/episodes/{episode_id}/resource")
async def api_update_episode_resource(episode_id: int, payload: EpisodeResourcePayload) -> dict[str, Any]:
    ts = now()
    with connect() as conn:
        episode = conn.execute("SELECT * FROM episodes WHERE id=?", (episode_id,)).fetchone()
        if not episode:
            raise HTTPException(status_code=404, detail="集数不存在")
        if payload.selected:
            conn.execute(
                "UPDATE episode_resources SET selected=0 WHERE entry_id=? AND episode_number=?",
                (episode["entry_id"], episode["episode_number"]),
            )
            conn.execute(
                "UPDATE releases SET selected=0 WHERE entry_id=? AND episode_number=?",
                (episode["entry_id"], episode["episode_number"]),
            )
        if payload.resource_id:
            conn.execute(
                """
                UPDATE episode_resources
                SET title=CASE WHEN ?='' THEN title ELSE ? END,
                    subtitle_group=CASE WHEN ?='' THEN subtitle_group ELSE ? END,
                    resolution=CASE WHEN ?='' THEN resolution ELSE ? END,
                    language=CASE WHEN ?='' THEN language ELSE ? END,
                    subtitle_format=CASE WHEN ?='' THEN subtitle_format ELSE ? END,
                    selected=?,
                    updated_at=?
                WHERE id=? AND entry_id=?
                """,
                (
                    payload.title.strip(),
                    payload.title.strip(),
                    payload.subtitle_group.strip(),
                    payload.subtitle_group.strip(),
                    payload.resolution.strip(),
                    payload.resolution.strip(),
                    payload.language.strip(),
                    payload.language.strip(),
                    payload.subtitle_format.strip(),
                    payload.subtitle_format.strip(),
                    int(payload.selected),
                    ts,
                    payload.resource_id,
                    episode["entry_id"],
                ),
            )
            row = conn.execute("SELECT * FROM episode_resources WHERE id=?", (payload.resource_id,)).fetchone()
            if payload.selected and row and int(row["release_id"] or 0) > 0:
                conn.execute("UPDATE releases SET selected=1 WHERE id=?", (int(row["release_id"]),))
        else:
            conn.execute(
                """
                INSERT INTO episode_resources
                  (entry_id, episode_id, episode_number, source_type, source_ref, title,
                   subtitle_group, resolution, language, subtitle_format, selected, created_at, updated_at)
                VALUES (?, ?, ?, 'manual', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    episode["entry_id"],
                    episode_id,
                    episode["episode_number"],
                    f"manual:{episode_id}:{ts}",
                    payload.title.strip(),
                    payload.subtitle_group.strip(),
                    payload.resolution.strip(),
                    payload.language.strip(),
                    payload.subtitle_format.strip(),
                    int(payload.selected),
                    ts,
                    ts,
                ),
            )
            row = conn.execute("SELECT * FROM episode_resources WHERE id=last_insert_rowid()").fetchone()
    return {"status": "saved", "item": row_to_dict(row)}


@app.put("/api/episodes/{episode_id}/subtitle")
async def api_update_episode_subtitle(episode_id: int, payload: EpisodeSubtitlePayload) -> dict[str, Any]:
    ts = now()
    with connect() as conn:
        episode = conn.execute("SELECT * FROM episodes WHERE id=?", (episode_id,)).fetchone()
        if not episode:
            raise HTTPException(status_code=404, detail="集数不存在")
        if payload.selected:
            conn.execute(
                "UPDATE episode_subtitles SET selected=0 WHERE entry_id=? AND episode_number=?",
                (episode["entry_id"], episode["episode_number"]),
            )
        if payload.subtitle_id:
            conn.execute(
                """
                UPDATE episode_subtitles
                SET language=?,
                    subtitle_format=?,
                    subtitle_path=?,
                    subtitle_url=?,
                    file_name=?,
                    embedded=?,
                    selected=?,
                    updated_at=?
                WHERE id=? AND entry_id=?
                """,
                (
                    payload.language.strip(),
                    payload.subtitle_format.strip(),
                    payload.subtitle_path.strip(),
                    payload.subtitle_url.strip(),
                    payload.file_name.strip(),
                    subtitle_embedded_value(payload.subtitle_format),
                    int(payload.selected),
                    ts,
                    payload.subtitle_id,
                    episode["entry_id"],
                ),
            )
            row = conn.execute("SELECT * FROM episode_subtitles WHERE id=?", (payload.subtitle_id,)).fetchone()
        else:
            conn.execute(
                """
                INSERT INTO episode_subtitles
                  (episode_id, episode_resource_id, entry_id, episode_number, language,
                   subtitle_format, subtitle_path, subtitle_url, file_name, embedded, selected, created_at, updated_at)
                VALUES (?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    episode_id,
                    episode["entry_id"],
                    episode["episode_number"],
                    payload.language.strip(),
                    payload.subtitle_format.strip(),
                    payload.subtitle_path.strip(),
                    payload.subtitle_url.strip(),
                    payload.file_name.strip(),
                    subtitle_embedded_value(payload.subtitle_format),
                    int(payload.selected),
                    ts,
                    ts,
                ),
            )
            row = conn.execute("SELECT * FROM episode_subtitles WHERE id=last_insert_rowid()").fetchone()
    return {"status": "saved", "item": row_to_dict(row)}


@app.post("/api/episodes/{episode_id}/refresh")
async def api_refresh_episode_resource_state(episode_id: int) -> dict[str, Any]:
    run_id = 0
    refreshed = 0
    with connect() as conn:
        episode = conn.execute("SELECT * FROM episodes WHERE id=?", (episode_id,)).fetchone()
        if not episode:
            raise HTTPException(status_code=404, detail="集数不存在")
        resources = conn.execute(
            "SELECT * FROM episode_resources WHERE episode_id=? OR (entry_id=? AND episode_number=?)",
            (episode_id, episode["entry_id"], episode["episode_number"]),
        ).fetchall()
        for resource in resources:
            local_path = str(resource["local_path"] or "")
            exists = bool(local_path and Path(local_path).exists())
            conn.execute(
                "UPDATE episode_resources SET downloaded=?, updated_at=? WHERE id=?",
                (1 if exists else int(resource["downloaded"] or 0), now(), resource["id"]),
            )
            refreshed += 1
        selected = next((resource for resource in resources if int(resource["selected"] or 0)), resources[0] if resources else None)
        if selected and not int(selected["downloaded"] or 0) and int(selected["release_id"] or 0) > 0:
            run_id = start_pipeline(
                "library_backfill",
                trigger_source="episode_refresh",
                first_step_key="download",
                subject_type="release",
                subject_id=int(selected["release_id"]),
                payload={
                    "_dedupe_key": f"download:entry:{int(episode['entry_id'])}:episode:{int(episode['episode_number'])}",
                    "entry_id": int(episode["entry_id"]),
                    "release_id": int(selected["release_id"]),
                    "episode_number": int(episode["episode_number"]),
                    "domain_kind": "library",
                },
                message=f"刷新集数资源后补下载: entry_id={episode['entry_id']} episode={episode['episode_number']}",
            )
            trigger_queue("processor", delay=0)
    return {"status": "refreshed", "count": refreshed, "download_run_id": run_id}


@app.post("/api/episodes/{episode_id}/download")
async def api_download_episode_resource(episode_id: int) -> dict[str, Any]:
    with connect() as conn:
        episode = conn.execute("SELECT * FROM episodes WHERE id=?", (episode_id,)).fetchone()
        if not episode:
            raise HTTPException(status_code=404, detail="集数不存在")
        selected = conn.execute(
            """
            SELECT *
            FROM episode_resources
            WHERE episode_id=? OR (entry_id=? AND episode_number=?)
            ORDER BY selected DESC, id DESC
            LIMIT 1
            """,
            (episode_id, episode["entry_id"], episode["episode_number"]),
        ).fetchone()
        if not selected or int(selected["release_id"] or 0) <= 0:
            raise HTTPException(status_code=400, detail="该集没有可下载资源")
        ts = now()
        conn.execute(
            """
            UPDATE episode_resources
            SET status='queued', updated_at=?
            WHERE entry_id=? AND episode_number=? AND selected=1
            """,
            (ts, episode["entry_id"], episode["episode_number"]),
        )
        conn.execute(
            """
            UPDATE download_jobs
            SET status='pending', retry_after='', last_error='', updated_at=?
            WHERE entry_id=? AND episode_number=? AND status IN ('cancelled','paused','failed')
            """,
            (ts, episode["entry_id"], episode["episode_number"]),
        )
    run_id = start_pipeline(
        "library_backfill",
        trigger_source="episode_download",
        first_step_key="download",
        subject_type="release",
        subject_id=int(selected["release_id"]),
        payload={
            "_dedupe_key": f"download:entry:{int(episode['entry_id'])}:episode:{int(episode['episode_number'])}",
            "entry_id": int(episode["entry_id"]),
            "release_id": int(selected["release_id"]),
            "episode_number": int(episode["episode_number"]),
            "domain_kind": "library",
        },
        message=f"手动下载集数: entry_id={episode['entry_id']} episode={episode['episode_number']}",
    )
    trigger_queue("processor", delay=0)
    return {"status": "started", "download_run_id": run_id, "message": "已加入下载队列"}


@app.post("/api/episodes/{episode_id}/download/cancel")
async def api_cancel_episode_download(episode_id: int) -> dict[str, Any]:
    with connect() as conn:
        episode = conn.execute("SELECT * FROM episodes WHERE id=?", (episode_id,)).fetchone()
        if not episode:
            raise HTTPException(status_code=404, detail="集数不存在")
        ts = now()
        conn.execute(
            """
            UPDATE download_jobs
            SET status='cancelled', retry_after='', last_error='用户取消下载', updated_at=?
            WHERE entry_id=? AND episode_number=? AND status IN ('pending','running','submitted','failed','paused')
            """,
            (ts, episode["entry_id"], episode["episode_number"]),
        )
        conn.execute(
            """
            UPDATE episode_resources
            SET status='cancelled', updated_at=?
            WHERE entry_id=? AND episode_number=? AND selected=1 AND downloaded=0
            """,
            (ts, episode["entry_id"], episode["episode_number"]),
        )
    cancelled = await runtime_store.cancel_episode_tasks(
        int(episode["entry_id"]),
        int(episode["episode_number"]),
        {"download", "nfo"},
    )
    return {"status": "cancelled", "runtime_cancelled": cancelled, "message": "已取消该集下载任务"}


@app.post("/api/episodes/{episode_id}/download/pause")
async def api_pause_episode_download(episode_id: int) -> dict[str, Any]:
    with connect() as conn:
        episode = conn.execute("SELECT * FROM episodes WHERE id=?", (episode_id,)).fetchone()
        if not episode:
            raise HTTPException(status_code=404, detail="集数不存在")
        ts = now()
        conn.execute(
            """
            UPDATE download_jobs
            SET status='paused', retry_after='', last_error='用户暂停下载', updated_at=?
            WHERE entry_id=? AND episode_number=? AND status IN ('pending','running','submitted')
            """,
            (ts, episode["entry_id"], episode["episode_number"]),
        )
        conn.execute(
            """
            UPDATE episode_resources
            SET status='paused', updated_at=?
            WHERE entry_id=? AND episode_number=? AND selected=1 AND downloaded=0
            """,
            (ts, episode["entry_id"], episode["episode_number"]),
        )
    cancelled = await runtime_store.cancel_episode_tasks(
        int(episode["entry_id"]),
        int(episode["episode_number"]),
        {"download"},
    )
    return {"status": "paused", "runtime_cancelled": cancelled, "message": "已暂停该集本地下载流程"}


@app.post("/api/entries/{entry_id}/refresh-resources")
async def api_refresh_entry_resource_state(entry_id: int) -> dict[str, Any]:
    with connect() as conn:
        episodes = conn.execute("SELECT id FROM episodes WHERE entry_id=? ORDER BY episode_number", (entry_id,)).fetchall()
    count = 0
    runs: list[int] = []
    for episode in episodes:
        result = await api_refresh_episode_resource_state(int(episode["id"]))
        count += int(result.get("count") or 0)
        if int(result.get("download_run_id") or 0):
            runs.append(int(result["download_run_id"]))
    return {"status": "refreshed", "count": count, "download_run_ids": runs}


@app.delete("/api/seasonal/{entry_id}")
async def api_delete_seasonal_entry(entry_id: int) -> dict[str, str]:
    return archive_seasonal_entry(entry_id)


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
        "任务队列立即处理" if force else "任务队列处理",
        run,
        "正在立即推进任务队列" if force else "正在推进任务队列",
    )
    return {"status": "started", "operation_id": str(operation_id), "message": "任务队列已立即触发" if force else "队列处理已启动"}


@app.post("/api/tasks/poll")
async def api_poll_tasks() -> dict[str, str]:
    async def run() -> str:
        trigger_queue("processor", delay=0)
        return "已触发 Runtime 处理器；等待中的下载状态到期后会继续推进"

    operation_id = run_operation("刷新下载状态", run, "正在刷新下载器任务状态")
    return {"status": "started", "operation_id": str(operation_id), "message": "状态刷新已启动"}


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


@app.api_route("/api/{full_path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def api_not_found(full_path: str) -> None:
    raise HTTPException(status_code=404, detail="API 不存在")


frontend_dir = APP_DIR.parent / "frontend_dist"
if frontend_dir.exists():
    app.mount("/assets", StaticFiles(directory=frontend_dir / "assets"), name="assets")


def frontend_asset_response(filename: str) -> FileResponse:
    asset_file = frontend_dir / filename
    if not asset_file.exists():
        raise HTTPException(status_code=404, detail="前端静态资源不存在")
    return FileResponse(asset_file)


@app.get("/anitrack-icon.png", include_in_schema=False)
async def anitrack_icon() -> FileResponse:
    return frontend_asset_response("anitrack-icon.png")


@app.get("/anitrack-logo.png", include_in_schema=False)
async def anitrack_logo() -> FileResponse:
    return frontend_asset_response("anitrack-logo.png")


@app.get("/{full_path:path}")
async def spa(full_path: str) -> FileResponse:
    index_file = frontend_dir / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    fallback = APP_DIR / "static" / "missing-frontend.html"
    return FileResponse(fallback)



