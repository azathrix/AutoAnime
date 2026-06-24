from __future__ import annotations

import sqlite3
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import DATA_DIR, DB_PATH
from .database import connect
from .db import get_settings, log
from .library import expected_local_episode_path, local_library_root, render_series_dir
from .parser import parse_episode
from .sync_service import local_episode_path


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


def _remove_empty_parents(path: Path, stop: Path) -> int:
    removed = 0
    current = path.parent
    stop_resolved = stop.resolve(strict=False)
    while current != current.parent:
        try:
            current_resolved = current.resolve(strict=False)
        except OSError:
            break
        if current_resolved == stop_resolved or stop_resolved not in current_resolved.parents:
            break
        try:
            current.rmdir()
            removed += 1
        except OSError:
            break
        current = current.parent
    return removed


def _move_existing_file(old_path: str, expected: str, library_root: Path) -> dict[str, int]:
    result = {"moved_files": 0, "move_conflicts": 0, "removed_dirs": 0}
    if not old_path or not expected or old_path == expected:
        return result
    source = Path(old_path)
    target = Path(expected)
    if not source.exists() or not source.is_file():
        return result
    if target.exists():
        result["move_conflicts"] += 1
        return result
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(target))
    result["moved_files"] += 1
    result["removed_dirs"] += _remove_empty_parents(source, library_root)
    return result


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


def backup_database(reason: str) -> str:
    backup_dir = DATA_DIR / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    safe_reason = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in reason).strip("-") or "backup"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    target = backup_dir / f"autoanime-{safe_reason}-{stamp}.db"
    shutil.copy2(DB_PATH, target)
    return str(target)


def _video_suffix(*values: str) -> str:
    for value in values:
        suffix = Path(str(value or "")).suffix
        if suffix.lower() in {".mkv", ".mp4", ".avi", ".mov", ".wmv", ".ts", ".m2ts", ".flv", ".webm"}:
            return suffix
    text = " ".join(str(value or "").lower() for value in values)
    for suffix in (".mkv", ".mp4", ".avi", ".mov", ".wmv", ".ts", ".m2ts", ".flv", ".webm"):
        if suffix in text:
            return suffix
    return ".mkv"


def migrate_episode_model() -> dict[str, Any]:
    backup_path = backup_database("before-episode-model")
    summary = {"episodes": 0, "updated": 0, "watchable": 0, "backup_path": backup_path}
    settings = get_settings()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT ep.*,
                   e.display_title, e.title_raw, e.title_cn, e.bangumi_id, e.tmdb_id,
                   e.year, e.season_number, e.media_type, e.target_library_id,
                   er.id AS resource_id,
                   er.release_id AS resource_release_id,
                   er.title AS resource_title,
                   er.source_ref AS resource_ref,
                   er.torrent_url,
                   er.magnet,
                   er.subtitle_group,
                   er.resolution,
                   er.language,
                   er.subtitle_format,
                   er.local_path AS resource_local_path,
                   es.subtitle_url,
                   es.subtitle_path,
                   la.local_path AS asset_local_path,
                   la.status AS asset_status,
                   dj.id AS download_job_id
            FROM episodes ep
            JOIN entries e ON e.id=ep.entry_id
            LEFT JOIN episode_resources er ON er.id=(
              SELECT id FROM episode_resources
              WHERE entry_id=ep.entry_id AND episode_number=ep.episode_number
              ORDER BY selected DESC, id DESC
              LIMIT 1
            )
            LEFT JOIN episode_subtitles es ON es.id=(
              SELECT id FROM episode_subtitles
              WHERE entry_id=ep.entry_id AND episode_number=ep.episode_number
              ORDER BY selected DESC, id DESC
              LIMIT 1
            )
            LEFT JOIN local_assets la ON la.id=(
              SELECT id FROM local_assets
              WHERE entry_id=ep.entry_id AND episode_number=ep.episode_number
              ORDER BY CASE status WHEN 'synced' THEN 0 ELSE 1 END, updated_at DESC, id DESC
              LIMIT 1
            )
            LEFT JOIN download_jobs dj ON dj.id=(
              SELECT id FROM download_jobs
              WHERE entry_id=ep.entry_id AND episode_number=ep.episode_number
              ORDER BY updated_at DESC, id DESC
              LIMIT 1
            )
            WHERE ep.episode_number > 0
            ORDER BY ep.entry_id, ep.episode_number
            """
        ).fetchall()
        ts = _now()
        for row in rows:
            summary["episodes"] += 1
            entry = dict(row)
            source_ref = str(row["magnet"] or row["torrent_url"] or row["resource_ref"] or row["resource_ref"] or "")
            local_path = str(row["asset_local_path"] or row["resource_local_path"] or row["local_path"] or "")
            suffix = _video_suffix(local_path, str(row["resource_title"] or ""), source_ref)
            expected_path = local_path or expected_local_episode_path(entry, int(row["episode_number"] or 0), suffix, settings)
            watchable = bool(expected_path and Path(expected_path).exists())
            if watchable:
                summary["watchable"] += 1
            conn.execute(
                """
                UPDATE episodes
                SET resource_ref=CASE WHEN resource_ref='' THEN ? ELSE resource_ref END,
                    subtitle_ref=CASE WHEN subtitle_ref='' THEN ? ELSE subtitle_ref END,
                    local_path=CASE WHEN local_path='' THEN ? ELSE local_path END,
                    subtitle_path=CASE WHEN subtitle_path='' THEN ? ELSE subtitle_path END,
                    watchable=?,
                    subtitle_group=CASE WHEN subtitle_group='' THEN ? ELSE subtitle_group END,
                    resolution=CASE WHEN resolution='' THEN ? ELSE resolution END,
                    language=CASE WHEN language='' THEN ? ELSE language END,
                    subtitle_format=CASE WHEN subtitle_format='' THEN ? ELSE subtitle_format END,
                    source_title=CASE WHEN source_title='' THEN ? ELSE source_title END,
                    source_type='magnet',
                    release_id=CASE WHEN release_id=0 THEN ? ELSE release_id END,
                    last_download_job_id=CASE WHEN last_download_job_id=0 THEN ? ELSE last_download_job_id END,
                    status=CASE WHEN ?=1 THEN 'downloaded' WHEN status='missing' THEN 'available' ELSE status END,
                    updated_at=?
                WHERE id=?
                """,
                (
                    source_ref,
                    str(row["subtitle_url"] or ""),
                    expected_path,
                    str(row["subtitle_path"] or ""),
                    1 if watchable else 0,
                    str(row["subtitle_group"] or ""),
                    str(row["resolution"] or ""),
                    str(row["language"] or ""),
                    str(row["subtitle_format"] or ""),
                    str(row["resource_title"] or ""),
                    int(row["resource_release_id"] or row["release_id"] or 0),
                    int(row["download_job_id"] or 0),
                    1 if watchable else 0,
                    ts,
                    int(row["id"] or 0),
                ),
            )
            summary["updated"] += 1
    message = f"集数模型迁移完成: 集数 {summary['episodes']} 条，更新 {summary['updated']} 条，可观看 {summary['watchable']} 条，备份 {backup_path}"
    log("info", message)
    return {"status": "completed", "message": message, **summary}


def _candidate_existing_path(*values: str) -> str:
    for value in values:
        text = str(value or "").strip()
        if text and Path(text).exists():
            return text
    return ""


VIDEO_SUFFIXES = (".mkv", ".mp4", ".avi", ".mov", ".wmv", ".ts", ".m2ts", ".flv", ".webm")


def _expected_existing_path(entry: dict[str, Any], episode_number: int, suffix: str, settings: dict[str, str]) -> str:
    suffixes = [suffix] if suffix else []
    for candidate in VIDEO_SUFFIXES:
        if candidate not in suffixes:
            suffixes.append(candidate)
    for candidate in suffixes:
        path = expected_local_episode_path(entry, episode_number, candidate, settings)
        if path and Path(path).exists():
            return path
    return ""


def _refresh_episode_row(conn: sqlite3.Connection, row: Any, settings: dict[str, str], ts: str) -> bool:
    entry = dict(row)
    current_path = str(row["local_path"] or "")
    asset_path = str(row["asset_local_path"] or "")
    resource_path = str(row["resource_local_path"] or "")
    episode_id = int(row["id"] or row["episode_id"] or 0)
    entry_id = int(row["entry_id"] or 0)
    episode_number = int(row["episode_number"] or 0)
    existing_path = _candidate_existing_path(current_path, asset_path, resource_path)
    suffix = _video_suffix(existing_path, current_path, asset_path, resource_path, str(row["source_title"] or ""), str(row["resource_ref"] or ""))
    expected_path = _expected_existing_path(entry, episode_number, suffix, settings)
    final_path = existing_path or expected_path
    exists = bool(final_path)
    conn.execute(
        """
        UPDATE episodes
        SET local_path=?, watchable=?, status=CASE WHEN ?=1 THEN 'downloaded' ELSE 'available' END, updated_at=?
        WHERE id=?
        """,
        (final_path, 1 if exists else 0, 1 if exists else 0, ts, episode_id),
    )
    conn.execute(
        """
        UPDATE episode_resources
        SET downloaded=?, local_path=CASE WHEN ?=1 THEN ? ELSE '' END,
            status=CASE WHEN ?=1 THEN 'downloaded' ELSE 'available' END,
            updated_at=?
        WHERE entry_id=? AND episode_number=?
        """,
        (1 if exists else 0, 1 if exists else 0, final_path, 1 if exists else 0, ts, entry_id, episode_number),
    )
    if exists:
        conn.execute(
            """
            UPDATE local_assets
            SET status='removed', updated_at=?
            WHERE entry_id=? AND episode_number=? AND status='synced' AND local_path != ?
            """,
            (ts, entry_id, episode_number, final_path),
        )
        conn.execute(
            """
            INSERT INTO local_assets
              (download_artifact_id, release_id, series_id, entry_id, episode_number, local_path,
               nfo_status, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'disabled', 'synced', ?, ?)
            ON CONFLICT(download_artifact_id) DO UPDATE SET
              local_path=excluded.local_path,
              status='synced',
              updated_at=excluded.updated_at
            """,
            (
                -abs(episode_id),
                int(row["release_id"] or 0),
                int(row["series_id"] or 0),
                entry_id,
                episode_number,
                final_path,
                ts,
                ts,
            ),
        )
    else:
        conn.execute(
            """
            UPDATE local_assets
            SET status='removed', updated_at=?
            WHERE entry_id=? AND episode_number=? AND status='synced'
            """,
            (ts, entry_id, episode_number),
        )
    return exists


def refresh_local_status(entry_id: int = 0, episode_id: int = 0) -> dict[str, Any]:
    summary = {"checked": 0, "watchable": 0, "missing": 0}
    settings = get_settings()
    with connect() as conn:
        params: tuple[Any, ...]
        where = "WHERE ep.episode_number > 0 AND COALESCE(e.hidden, 0)=0"
        params = ()
        if entry_id > 0:
            where += " AND ep.entry_id=?"
            params = (entry_id,)
        if episode_id > 0:
            where += " AND ep.id=?"
            params = (*params, episode_id)
        rows = conn.execute(
            f"""
            SELECT ep.*, e.display_title, e.title_raw, e.title_cn, e.bangumi_id, e.tmdb_id,
                   e.year, e.season_number, e.media_type, e.target_library_id,
                   la.local_path AS asset_local_path,
                   er.local_path AS resource_local_path
            FROM episodes ep
            JOIN entries e ON e.id=ep.entry_id
            LEFT JOIN local_assets la ON la.id=(
              SELECT id FROM local_assets
              WHERE entry_id=ep.entry_id AND episode_number=ep.episode_number
              ORDER BY CASE status WHEN 'synced' THEN 0 ELSE 1 END, updated_at DESC, id DESC
              LIMIT 1
            )
            LEFT JOIN episode_resources er ON er.id=(
              SELECT id FROM episode_resources
              WHERE entry_id=ep.entry_id AND episode_number=ep.episode_number
              ORDER BY selected DESC, id DESC
              LIMIT 1
            )
            {where}
            ORDER BY ep.entry_id, ep.episode_number
            """,
            params,
        ).fetchall()
        ts = _now()
        for row in rows:
            summary["checked"] += 1
            if _refresh_episode_row(conn, row, settings, ts):
                summary["watchable"] += 1
            else:
                summary["missing"] += 1
    if episode_id > 0:
        scope = f"episode_id={episode_id}"
    else:
        scope = f"entry_id={entry_id}" if entry_id > 0 else "全部"
    message = f"本地状态刷新完成({scope}): 检查 {summary['checked']} 集，可观看 {summary['watchable']} 集，缺失 {summary['missing']} 集"
    log("info", message)
    return {"status": "completed", "message": message, **summary}


def _video_files_under(path: Path) -> list[Path]:
    if path.is_file():
        return [path] if path.suffix.lower() in VIDEO_SUFFIXES else []
    return [item for item in path.rglob("*") if item.is_file() and item.suffix.lower() in VIDEO_SUFFIXES]


def _bind_episode_local_path(
    conn: sqlite3.Connection,
    episode: Any,
    entry: dict[str, Any],
    local_path: str,
    ts: str,
) -> None:
    episode_id = int(episode["id"] or 0)
    entry_id = int(episode["entry_id"] or 0)
    episode_number = int(episode["episode_number"] or 0)
    release_id = int(episode["release_id"] or 0)
    series_id = int(episode["series_id"] or 0) or entry_id
    conn.execute(
        """
        UPDATE episodes
        SET local_path=?, watchable=1, status='downloaded', updated_at=?
        WHERE id=?
        """,
        (local_path, ts, episode_id),
    )
    conn.execute(
        """
        UPDATE episode_resources
        SET downloaded=1, local_path=?, status='downloaded', updated_at=?
        WHERE entry_id=? AND episode_number=?
        """,
        (local_path, ts, entry_id, episode_number),
    )
    conn.execute(
        """
        UPDATE local_assets
        SET status='removed', updated_at=?
        WHERE entry_id=? AND episode_number=? AND status='synced' AND local_path != ?
        """,
        (ts, entry_id, episode_number, local_path),
    )
    conn.execute(
        """
        INSERT INTO local_assets
          (download_artifact_id, release_id, series_id, entry_id, episode_number, local_path,
           nfo_status, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, 'disabled', 'synced', ?, ?)
        ON CONFLICT(download_artifact_id) DO UPDATE SET
          local_path=excluded.local_path,
          status='synced',
          updated_at=excluded.updated_at
        """,
        (-abs(episode_id), release_id, series_id, entry_id, episode_number, local_path, ts, ts),
    )


def match_entry_local_files(entry_id: int, path: str) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        return {"status": "failed", "message": "路径不存在", "matched": 0, "unmatched": 0}
    files = _video_files_under(target)
    summary = {"matched": 0, "unmatched": 0, "checked": len(files)}
    ts = _now()
    with connect() as conn:
        entry = conn.execute("SELECT * FROM entries WHERE id=? AND COALESCE(hidden, 0)=0", (entry_id,)).fetchone()
        if not entry:
            return {"status": "not_found", "message": "媒体条目不存在", **summary}
        entry_data = dict(entry)
        media_type = str(entry["media_type"] or "").lower()
        for file_path in files:
            episode_number = 1 if media_type == "movie" else parse_episode(file_path.name)
            if episode_number <= 0:
                summary["unmatched"] += 1
                continue
            episode = conn.execute(
                "SELECT * FROM episodes WHERE entry_id=? AND episode_number=? ORDER BY id DESC LIMIT 1",
                (entry_id, episode_number),
            ).fetchone()
            if not episode:
                summary["unmatched"] += 1
                continue
            _bind_episode_local_path(conn, episode, entry_data, str(file_path), ts)
            summary["matched"] += 1
    message = f"本地资源批量匹配完成: 检查 {summary['checked']} 个，匹配 {summary['matched']} 个，未匹配 {summary['unmatched']} 个"
    log("info", f"{message} entry_id={entry_id} path={path}")
    return {"status": "completed", "message": message, **summary}


def organize_local_files(entry_id: int = 0) -> dict[str, Any]:
    summary = {"checked": 0, "moved": 0, "missing": 0, "skipped": 0}
    settings = get_settings()
    ts = _now()
    with connect() as conn:
        params: tuple[Any, ...] = ()
        where = "WHERE ep.episode_number > 0 AND COALESCE(e.hidden, 0)=0"
        if entry_id > 0:
            where += " AND ep.entry_id=?"
            params = (entry_id,)
        rows = conn.execute(
            f"""
            SELECT ep.*, e.display_title, e.title_raw, e.title_cn, e.bangumi_id, e.tmdb_id,
                   e.year, e.season_number, e.media_type, e.target_library_id,
                   la.local_path AS asset_local_path,
                   er.local_path AS resource_local_path
            FROM episodes ep
            JOIN entries e ON e.id=ep.entry_id
            LEFT JOIN local_assets la ON la.id=(
              SELECT id FROM local_assets
              WHERE entry_id=ep.entry_id AND episode_number=ep.episode_number AND status='synced'
              ORDER BY updated_at DESC, id DESC
              LIMIT 1
            )
            LEFT JOIN episode_resources er ON er.id=(
              SELECT id FROM episode_resources
              WHERE entry_id=ep.entry_id AND episode_number=ep.episode_number
              ORDER BY selected DESC, id DESC
              LIMIT 1
            )
            {where}
            ORDER BY ep.entry_id, ep.episode_number
            """,
            params,
        ).fetchall()
        for row in rows:
            summary["checked"] += 1
            entry = dict(row)
            source = _candidate_existing_path(str(row["local_path"] or ""), str(row["asset_local_path"] or ""), str(row["resource_local_path"] or ""))
            if not source:
                summary["missing"] += 1
                continue
            expected = expected_local_episode_path(entry, int(row["episode_number"] or 0), Path(source).suffix or ".mkv", settings)
            if not expected:
                summary["skipped"] += 1
                continue
            source_path = Path(source)
            expected_path = Path(expected)
            if source_path.resolve() != expected_path.resolve():
                expected_path.parent.mkdir(parents=True, exist_ok=True)
                if expected_path.exists():
                    expected_path.unlink()
                shutil.move(str(source_path), str(expected_path))
                summary["moved"] += 1
            else:
                summary["skipped"] += 1
            _bind_episode_local_path(conn, row, entry, str(expected_path), ts)
    scope = f"entry_id={entry_id}" if entry_id > 0 else "全部"
    message = f"本地资源整理完成({scope}): 检查 {summary['checked']} 集，移动 {summary['moved']} 个，缺失 {summary['missing']} 个"
    log("info", message)
    return {"status": "completed", "message": message, **summary}


def repair_local_paths() -> dict[str, Any]:
    settings = get_settings()
    summary = {
        "checked": 0,
        "rewritten": 0,
        "available": 0,
        "missing": 0,
        "moved_files": 0,
        "move_conflicts": 0,
        "removed_dirs": 0,
    }
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT ep.id AS episode_id,
                   ep.entry_id,
                   ep.episode_number,
                   e.*,
                   la.id AS local_asset_id,
                   la.local_path AS local_asset_path,
                   er.local_path AS resource_local_path,
                   da.artifact_name AS artifact_name
            FROM episodes ep
            JOIN entries e ON e.id=ep.entry_id
            LEFT JOIN local_assets la
              ON la.entry_id=ep.entry_id
             AND la.episode_number=ep.episode_number
            LEFT JOIN episode_resources er
              ON er.id=(
                SELECT id FROM episode_resources
                WHERE entry_id=ep.entry_id AND episode_number=ep.episode_number
                ORDER BY selected DESC, id DESC
                LIMIT 1
              )
            LEFT JOIN download_artifacts da
              ON da.id=(
                SELECT id FROM download_artifacts
                WHERE entry_id=ep.entry_id AND episode_number=ep.episode_number
                ORDER BY updated_at DESC, id DESC
                LIMIT 1
              )
            WHERE ep.episode_number > 0 AND COALESCE(e.hidden, 0)=0
            ORDER BY ep.entry_id, ep.episode_number
            """
        ).fetchall()
        ts = _now()
        for row in rows:
            summary["checked"] += 1
            entry = dict(row)
            old_path = str(row["local_asset_path"] or row["resource_local_path"] or "")
            artifact_name = str(row["artifact_name"] or Path(old_path).name or "")
            expected = local_episode_path(
                {"artifact_name": artifact_name, "episode_number": int(row["episode_number"] or 0)},
                entry,
                settings,
            )
            if not expected:
                summary["missing"] += 1
                continue
            if old_path and old_path != expected and Path(old_path).exists() and not Path(expected).exists():
                move_result = _move_existing_file(old_path, expected, Path(local_library_root(entry, settings)))
                summary["moved_files"] += move_result["moved_files"]
                summary["move_conflicts"] += move_result["move_conflicts"]
                summary["removed_dirs"] += move_result["removed_dirs"]
            expected_exists = Path(expected).exists()
            if expected != old_path:
                summary["rewritten"] += 1
            if expected_exists:
                summary["available"] += 1
                if row["local_asset_id"]:
                    conn.execute(
                        """
                        UPDATE local_assets
                        SET local_path=?, status='synced', updated_at=?
                        WHERE id=?
                        """,
                        (expected, ts, row["local_asset_id"]),
                    )
                conn.execute(
                    """
                    UPDATE episode_resources
                    SET downloaded=1, local_path=?, status='downloaded', updated_at=?
                    WHERE entry_id=? AND episode_number=?
                    """,
                    (expected, ts, row["entry_id"], row["episode_number"]),
                )
                continue
            summary["missing"] += 1
            if row["local_asset_id"]:
                conn.execute(
                    "UPDATE local_assets SET local_path=?, status='removed', updated_at=? WHERE id=?",
                    (expected, ts, row["local_asset_id"]),
                )
            conn.execute(
                """
                UPDATE episode_resources
                SET downloaded=0, local_path=?, status='available', updated_at=?
                WHERE entry_id=? AND episode_number=?
                """,
                (expected, ts, row["entry_id"], row["episode_number"]),
            )
    message = (
        f"本地路径修复完成: 检查 {summary['checked']} 集，"
        f"移动文件 {summary['moved_files']} 个，重写路径 {summary['rewritten']} 条，"
        f"可观看 {summary['available']} 条，缺失 {summary['missing']} 条，冲突 {summary['move_conflicts']} 个"
    )
    log("info", message)
    return {"status": "completed", "message": message, **summary}
