from __future__ import annotations

import json
from typing import Any

from .database import connect
from .db import get_settings, now
from .downloader_service import SUPPORTED_DOWNLOADER_TYPES
from .library import bool_setting
from .utils import int_setting, split_setting

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

def sync_download_processor_concurrency(value: int) -> int:
    concurrency = int_setting(value, 2, 1, 12)
    with connect() as conn:
        conn.execute(
            """
            UPDATE pipeline_steps
            SET max_concurrency=?, updated_at=?
            WHERE processor_key='download'
            """,
            (concurrency, now()),
        )
    return concurrency

def settings_response() -> dict[str, Any]:
    settings = get_settings()
    try:
        downloaders = json.loads(settings.get("downloaders_json", "[]") or "[]")
    except json.JSONDecodeError:
        downloaders = []
    return {
        "rss_url": settings.get("rss_url", ""),
        "rss_proxy": settings.get("rss_proxy", ""),
        "scan_interval_minutes": int_setting(settings.get("scan_interval_minutes"), 60, 1),
        "auto_scan": bool_setting(settings.get("auto_scan", "false")),
        "queue_dispatch_enabled": bool_setting(settings.get("queue_dispatch_enabled", "true")),
        "queue_dispatch_interval_minutes": int_setting(settings.get("queue_dispatch_interval_minutes"), 1, 1),
        "auto_generate_nfo": bool_setting(settings.get("auto_generate_nfo", "false")),
        "backfill_current_season": bool_setting(settings.get("backfill_current_season", "false")),
        "subtitle_priority": split_setting(settings.get("subtitle_priority", "")),
        "resolution_priority": split_setting(settings.get("resolution_priority", "")),
        "language_priority": split_setting(settings.get("language_priority", "")),
        "secondary_language_priority": split_setting(settings.get("secondary_language_priority", "")),
        "download_concurrency": int_setting(settings.get("download_concurrency"), 2, 1, 12),
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
        "tmdb_token": settings.get("tmdb_token", ""),
    }
