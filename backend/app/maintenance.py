from __future__ import annotations

import sqlite3
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import DATA_DIR, DB_PATH
from .database import connect
from .db import get_settings, log
from .library import local_library_root, render_series_dir


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


LEGACY_SERIES_DIR_TEMPLATES = [
    "{title_base} ({year}) [bangumi-{bangumi_id}]",
    "{title_cn} ({year}) [bangumi-{bangumi_id}]",
    "{title_base} ({year}) [tmdb-{tmdb_id}]",
    "{title_cn} ({year}) [tmdb-{tmdb_id}]",
    "{title_base} ({year})",
    "{title_cn} ({year})",
]


def table_count(conn: sqlite3.Connection, table: str) -> int:
    try:
        row = conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()
    except sqlite3.Error:
        return -1
    return int(row["count"]) if row else -1


def table_count_visible_series(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute("SELECT COUNT(*) AS count FROM series WHERE COALESCE(hidden, 0)=0").fetchone()
    except sqlite3.Error:
        return 0
    return int(row["count"]) if row else 0


def diagnostics() -> dict[str, Any]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db_exists = DB_PATH.exists()
    db_size = DB_PATH.stat().st_size if db_exists else 0
    result: dict[str, Any] = {
        "data_dir": str(DATA_DIR),
        "db_path": str(DB_PATH),
        "db_exists": db_exists,
        "db_size": db_size,
        "data_dir_exists": DATA_DIR.exists(),
        "data_dir_writable": False,
        "tables": {},
        "settings_sample": {},
    }
    probe = DATA_DIR / ".write-test"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        result["data_dir_writable"] = True
    except OSError as exc:
        result["write_error"] = str(exc)
    with connect() as conn:
        tables = [
            "settings",
            "pipelines",
            "pipeline_steps",
            "pipeline_transitions",
            "media_libraries",
            "works",
            "entries",
            "seasonal_entries",
            "library_entries",
            "series",
            "episodes",
            "releases",
            "episode_resources",
            "episode_subtitles",
            "rss_candidates",
            "rss_subscriptions",
            "processing_cache",
            "download_jobs",
            "download_artifacts",
            "sync_rules",
            "local_assets",
        ]
        result["tables"] = {table: table_count(conn, table) for table in tables}
        total_series = max(0, table_count(conn, "series"))
        result["hidden_series"] = max(0, total_series - table_count_visible_series(conn))
        result["hidden_legacy_series"] = result["hidden_series"]
        result["tables"]["legacy_series"] = result["tables"].get("series", 0)
        for key in ["rss_url", "library_root", "local_library_root", "auto_scan", "auto_sync_following"]:
            row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            result["settings_sample"][key] = row["value"] if row else None
    return result


def clear_runtime_data() -> None:
    next_generation = _now()
    with connect() as conn:
        for table in [
            "local_assets",
            "sync_rules",
            "download_artifacts",
            "download_jobs",
            "episode_subtitles",
            "episode_resources",
            "rss_candidates",
            "library_entries",
            "seasonal_entries",
            "entries",
            "works",
            "releases",
            "episodes",
            "series",
        ]:
            conn.execute(f"DELETE FROM {table}")
        try:
            conn.execute("DELETE FROM processing_cache")
        except sqlite3.Error:
            pass
        conn.execute(
            "DELETE FROM sqlite_sequence WHERE name IN ('local_assets','sync_rules','download_artifacts','download_jobs','episode_subtitles','episode_resources','rss_candidates','processing_cache','library_entries','seasonal_entries','entries','works','releases','episodes','series')"
        )
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('runtime_generation', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (next_generation,),
        )


def _merge_directory(source, target) -> dict[str, int]:
    result = {"renamed_dirs": 0, "moved_items": 0, "conflicts": 0, "removed_dirs": 0}
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        shutil.move(str(source), str(target))
        result["renamed_dirs"] += 1
        return result
    if not source.is_dir() or not target.is_dir():
        result["conflicts"] += 1
        return result
    target.mkdir(parents=True, exist_ok=True)
    for child in list(source.iterdir()):
        destination = target / child.name
        if not destination.exists():
            shutil.move(str(child), str(destination))
            result["moved_items"] += 1
            continue
        if child.is_dir() and destination.is_dir():
            nested = _merge_directory(child, destination)
            for key, value in nested.items():
                result[key] += value
            continue
        result["conflicts"] += 1
    try:
        source.rmdir()
        result["removed_dirs"] += 1
    except OSError:
        pass
    return result


def _replace_prefix(value: str, old_root, new_root) -> str:
    if not value:
        return ""
    old_text = str(old_root)
    new_text = str(new_root)
    if value == old_text:
        return new_text
    for separator in ("/", "\\"):
        prefix = f"{old_text}{separator}"
        if value.startswith(prefix):
            return f"{new_text}{value[len(old_text):]}"
    normalized_value = value.replace("\\", "/")
    normalized_old = old_text.replace("\\", "/")
    normalized_new = new_text.replace("\\", "/")
    if normalized_value == normalized_old:
        return normalized_new
    prefix = f"{normalized_old}/"
    if normalized_value.startswith(prefix):
        return f"{normalized_new}{normalized_value[len(normalized_old):]}"
    return value


def _update_entry_path_prefix(entry_id: int, old_root, new_root) -> int:
    updates = 0
    with connect() as conn:
        for table, column in [
            ("local_assets", "local_path"),
            ("episode_resources", "local_path"),
            ("episode_subtitles", "subtitle_path"),
        ]:
            rows = conn.execute(
                f"SELECT id, {column} FROM {table} WHERE entry_id=? AND {column} != ''",
                (entry_id,),
            ).fetchall()
            for row in rows:
                current = str(row[column] or "")
                relocated = _replace_prefix(current, old_root, new_root)
                if relocated == current:
                    continue
                conn.execute(
                    f"UPDATE {table} SET {column}=?, updated_at=? WHERE id=?",
                    (relocated, _now(), row["id"]),
                )
                updates += 1
    return updates


def _legacy_dir_names(entry: dict[str, Any], settings: dict[str, str]) -> list[str]:
    names: list[str] = []
    current = render_series_dir(entry, settings)
    for template in LEGACY_SERIES_DIR_TEMPLATES:
        legacy_settings = dict(settings)
        legacy_settings["series_dir_template"] = template
        try:
            name = render_series_dir(entry, legacy_settings)
        except (KeyError, ValueError):
            continue
        if name and name != current and name not in names:
            names.append(name)
    return names


def migrate_media_folders() -> dict[str, Any]:
    settings = get_settings()
    with connect() as conn:
        rows = conn.execute("SELECT * FROM entries WHERE COALESCE(hidden, 0)=0").fetchall()
    entries = [dict(row) for row in rows]
    summary: dict[str, Any] = {
        "entries": len(entries),
        "migrated_dirs": 0,
        "merged_items": 0,
        "conflicts": 0,
        "path_updates": 0,
        "skipped": 0,
        "details": [],
    }
    for entry in entries:
        entry_id = int(entry.get("id") or 0)
        root = local_library_root(entry, settings)
        new_dir = Path(root) / render_series_dir(entry, settings)
        legacy_names = _legacy_dir_names(entry, settings)
        handled = False
        for legacy_name in legacy_names:
            old_dir = Path(root) / legacy_name
            if old_dir == new_dir:
                continue
            if old_dir.exists():
                move_result = _merge_directory(old_dir, new_dir)
                summary["migrated_dirs"] += move_result["renamed_dirs"] + move_result["removed_dirs"]
                summary["merged_items"] += move_result["moved_items"]
                summary["conflicts"] += move_result["conflicts"]
                summary["path_updates"] += _update_entry_path_prefix(entry_id, old_dir, new_dir)
                summary["details"].append(
                    {
                        "entry_id": entry_id,
                        "title": entry.get("display_title") or entry.get("title_cn") or entry.get("title_raw") or "",
                        "from": str(old_dir),
                        "to": str(new_dir),
                        "conflicts": move_result["conflicts"],
                    }
                )
                handled = True
                continue
            if new_dir.exists():
                updates = _update_entry_path_prefix(entry_id, old_dir, new_dir)
                if updates:
                    summary["path_updates"] += updates
                    handled = True
        if not handled:
            summary["skipped"] += 1
    message = (
        f"媒体目录迁移完成: 目录 {summary['migrated_dirs']} 个，"
        f"合并 {summary['merged_items']} 项，路径更新 {summary['path_updates']} 条，"
        f"冲突 {summary['conflicts']} 个"
    )
    log("info", message)
    summary["status"] = "completed"
    summary["message"] = message
    return summary


def cleanup_invalid_episode_data() -> dict[str, Any]:
    summary = {
        "episode_resources": 0,
        "episode_subtitles": 0,
        "releases": 0,
        "episodes": 0,
        "download_jobs": 0,
        "download_artifacts": 0,
        "local_assets": 0,
        "rss_candidates": 0,
    }

    def count_changes(cursor) -> int:
        return max(0, int(cursor.rowcount or 0))

    with connect() as conn:
        summary["episode_subtitles"] += count_changes(
            conn.execute(
                """
                DELETE FROM episode_subtitles
                WHERE episode_number <= 0
                   OR NOT EXISTS (
                     SELECT 1 FROM episodes ep
                     WHERE ep.entry_id=episode_subtitles.entry_id
                       AND ep.episode_number=episode_subtitles.episode_number
                   )
                """
            )
        )
        summary["episode_resources"] += count_changes(
            conn.execute(
                """
                DELETE FROM episode_resources
                WHERE episode_number <= 0
                   OR NOT EXISTS (
                     SELECT 1 FROM episodes ep
                     WHERE ep.entry_id=episode_resources.entry_id
                       AND ep.episode_number=episode_resources.episode_number
                   )
                """
            )
        )
        summary["download_jobs"] += count_changes(conn.execute("DELETE FROM download_jobs WHERE episode_number <= 0"))
        summary["download_artifacts"] += count_changes(conn.execute("DELETE FROM download_artifacts WHERE episode_number <= 0"))
        summary["local_assets"] += count_changes(conn.execute("DELETE FROM local_assets WHERE episode_number <= 0"))
        summary["releases"] += count_changes(conn.execute("DELETE FROM releases WHERE episode_number <= 0"))
        summary["episodes"] += count_changes(conn.execute("DELETE FROM episodes WHERE episode_number <= 0"))
        summary["rss_candidates"] += count_changes(
            conn.execute(
                """
                UPDATE rss_candidates
                SET status='ignored', reason='未识别集数，已跳过入库', updated_at=?
                WHERE episode_number <= 0 AND status != 'ignored'
                """,
                (_now(),),
            )
        )
    message = (
        f"无效集数数据清理完成: 资源 {summary['episode_resources']} 条，"
        f"发布 {summary['releases']} 条，字幕 {summary['episode_subtitles']} 条，"
        f"下载记录 {summary['download_jobs'] + summary['download_artifacts'] + summary['local_assets']} 条"
    )
    log("info", message)
    return {"status": "completed", "message": message, **summary}
