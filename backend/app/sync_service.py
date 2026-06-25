from __future__ import annotations

import re
import zlib
from pathlib import Path

from .database import connect
from .downloader_service import download_to_local, list_remote_files, provider_key, remote_file_id
from .db import log, now
from .library import expected_local_episode_path, media_root_for_type



def resolve_entry_series_id(conn, entry_id: int) -> int:
    row = conn.execute(
        "SELECT series_id FROM releases WHERE entry_id=? ORDER BY id ASC LIMIT 1",
        (entry_id,),
    ).fetchone()
    return int(row["series_id"] or 0) if row else 0


def task_retry_after_minutes(minutes: int) -> str:
    from datetime import datetime, timedelta, timezone

    return (datetime.now(timezone.utc) + timedelta(minutes=max(1, minutes))).isoformat()


def stale_running_cutoff(minutes: int = 10) -> str:
    from datetime import datetime, timedelta, timezone

    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()


def task_retry_after(settings: dict[str, str], attempts: int) -> str:
    default_minutes = max(5, 5 * max(1, attempts))
    minutes = min(180, default_minutes)
    return task_retry_after_minutes(minutes)


def ensure_sync_rule(entry_id: int, settings: dict[str, str], enabled: bool | None = None) -> None:
    ts = now()
    sync_enabled = True if enabled is None else enabled
    with connect() as conn:
        series_id = resolve_entry_series_id(conn, entry_id)
        entry_row = conn.execute(
            """
            SELECT e.media_type, ml.root_path
            FROM entries e
            LEFT JOIN media_libraries ml ON ml.id=e.target_library_id AND ml.enabled=1
            WHERE e.id=?
            """,
            (entry_id,),
        ).fetchone()
        local_root = media_root_for_type("anime")
        if entry_row:
            local_root = str(entry_row["root_path"] or "").strip() or media_root_for_type(str(entry_row["media_type"] or "anime"))
        conn.execute(
            """
            INSERT INTO sync_rules
              (series_id, entry_id, sync_enabled, auto_sync_following, local_root, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(entry_id) DO UPDATE SET
              series_id=excluded.series_id,
              sync_enabled=excluded.sync_enabled,
              auto_sync_following=excluded.auto_sync_following,
              local_root=excluded.local_root,
              updated_at=excluded.updated_at
            """,
            (
                series_id,
                entry_id,
                1 if sync_enabled else 0,
                1,
                local_root,
                ts,
                ts,
            ),
        )


def local_episode_path(download_artifact: dict, entry: dict, settings: dict[str, str]) -> str:
    suffix = Path(download_artifact.get("artifact_name") or "").suffix
    return expected_local_episode_path(entry, int(download_artifact.get("episode_number") or 0), suffix, settings)


def normalize_local_target_path(target_path: str, source_name: str = "") -> str:
    path = Path(target_path)
    suffix = path.suffix or Path(source_name).suffix
    stem = path.stem
    stem = re.sub(r"\(\d+\)$", "", stem).rstrip()
    normalized = path.with_name(f"{stem}{suffix}")
    return str(normalized)


VIDEO_SUFFIXES = {".mkv", ".mp4", ".avi", ".mov", ".wmv", ".ts", ".m2ts", ".flv", ".webm"}


async def find_existing_remote_episode(
    settings: dict[str, str],
    target_directory: str,
    expected_name: str,
    episode_number: int,
) -> dict | None:
    try:
        files = await list_remote_files(settings, target_directory, recursive=False)
    except Exception as exc:
        if "directory not found" in str(exc).lower():
            return None
        raise
    expected_basename = Path(str(expected_name or "")).name
    if Path(expected_basename).suffix.lower() not in VIDEO_SUFFIXES:
        log("warn", f"远端文件检测跳过: expected_name 缺少视频后缀 target_dir={target_directory} expected={expected_name}")
        return None
    for item in files:
        if item.get("is_dir"):
            continue
        name = str(item.get("name") or "")
        suffix = Path(name).suffix.lower()
        if suffix not in VIDEO_SUFFIXES:
            continue
        if name == expected_basename:
            return item
    return None


def download_file_id(item: dict) -> str:
    return remote_file_id(item)


def synthetic_task_id(file_id: str) -> int:
    return 0 - (zlib.crc32(file_id.encode("utf-8")) % 2147483647) - 1


def upsert_download_artifact_for_release(release_id: int, item: dict, settings: dict[str, str]) -> int | None:
    provider = provider_key(settings)
    file_id = download_file_id(item)
    name = str(item.get("name") or Path(str(item.get("remote_path") or "")).name)
    path = str(item.get("remote_path") or name)
    if not name:
        return None
    with connect() as conn:
        release = conn.execute("SELECT * FROM releases WHERE id=?", (release_id,)).fetchone()
        if not release:
            return None
        entry = conn.execute("SELECT * FROM entries WHERE id=?", (release["entry_id"],)).fetchone()
        if not entry:
            return None
        ts = now()
        series_id = resolve_entry_series_id(conn, int(release["entry_id"]))
        provider_file_id = file_id or f"rclone:{path}"
        task_id = synthetic_task_id(provider_file_id)
        existing = conn.execute(
            "SELECT id FROM download_artifacts WHERE provider=? AND provider_file_id=?",
            (provider, provider_file_id),
        ).fetchone()
        existing_by_episode = conn.execute(
            """
            SELECT id
            FROM download_artifacts
            WHERE entry_id=? AND episode_number=? AND provider=?
            ORDER BY id ASC
            LIMIT 1
            """,
            (release["entry_id"], release["episode_number"], provider),
        ).fetchone()
        existing_asset = existing or existing_by_episode
        if existing_asset:
            conn.execute(
                """
                UPDATE download_artifacts
                SET task_id=?, release_id=?, series_id=?, entry_id=?, episode_number=?, provider_file_id=?,
                    remote_path=?, artifact_name=?, status='available', updated_at=?
                WHERE id=?
                """,
                (
                    task_id,
                    release["id"],
                    series_id,
                    release["entry_id"],
                    release["episode_number"],
                    provider_file_id,
                    path,
                    name,
                    ts,
                    existing_asset["id"],
                ),
            )
            asset_id = int(existing_asset["id"])
            log(
                "info",
                f"下载产物登记更新: download_artifact_id={asset_id} release_id={release['id']} "
                f"entry_id={release['entry_id']} episode={release['episode_number']} "
                f"file_id={provider_file_id or '-'} artifact_name={name}",
            )
        else:
            conn.execute(
                """
                INSERT INTO download_artifacts
                  (task_id, release_id, series_id, entry_id, episode_number, provider, provider_file_id,
                   remote_path, artifact_name, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'available', ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                  release_id=excluded.release_id,
                  series_id=excluded.series_id,
                  entry_id=excluded.entry_id,
                  episode_number=excluded.episode_number,
                  provider_file_id=excluded.provider_file_id,
                  remote_path=excluded.remote_path,
                  artifact_name=excluded.artifact_name,
                  status='available',
                  updated_at=excluded.updated_at
                """,
                (
                    task_id,
                    release["id"],
                    series_id,
                    release["entry_id"],
                    release["episode_number"],
                    provider,
                    provider_file_id,
                    path,
                    name,
                    ts,
                    ts,
                ),
            )
            asset = conn.execute("SELECT id FROM download_artifacts WHERE task_id=?", (task_id,)).fetchone()
            asset_id = int(asset["id"]) if asset else 0
            if asset_id:
                log("info", f"下载产物已入库: {name}")
        if asset_id:
            return asset_id
    return None


async def download_remote_file_to_local(file_id: str, source: str, target: str, settings: dict[str, str], progress_cb=None) -> None:
    await download_to_local(settings, file_id, source, target, progress_cb=progress_cb)


def cancel_sync_for_entry(entry_id: int) -> tuple[int, str]:
    removed = 0
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM local_assets WHERE entry_id=? AND status='synced'",
            (entry_id,),
        ).fetchall()
        for row in rows:
            path = Path(row["local_path"])
            try:
                if path.exists():
                    path.unlink()
                nfo_path = path.with_suffix(".nfo")
                if nfo_path.exists():
                    nfo_path.unlink()
                removed += 1
            except Exception as exc:
                log("warn", f"删除本地文件失败: {path} - {exc}")
            conn.execute(
                "UPDATE local_assets SET status='removed', updated_at=? WHERE id=?",
                (now(), row["id"]),
            )
        conn.execute(
            "UPDATE sync_rules SET sync_enabled=0, updated_at=? WHERE entry_id=?",
            (now(), entry_id),
        )
    return removed, f"已取消同步并清理本地文件: {removed} 个"


