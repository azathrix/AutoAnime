from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any
import hashlib

from .database import connect
from .db import get_settings, now
from .downloader_service import provider_key, settings_for_attempt
from .library import render_episode_file_name, target_dir
from .runtime_service import ACTIVE_DOWNLOAD_STATUSES
from .sync_service import local_episode_path
from .utils import row_to_dict


FINAL_DOWNLOAD_STATUSES = {"completed", "failed", "cancelled"}
DOWNLOAD_STATUS_MAP = {
    "running": "submitting",
    "submitted": "remote_downloading",
    "downloading": "remote_downloading",
}


def canonical_download_status(value: str) -> str:
    key = str(value or "pending").strip().lower()
    return DOWNLOAD_STATUS_MAP.get(key, key or "pending")


def download_phase(value: str) -> str:
    status = canonical_download_status(value)
    if status in {
        "pending",
        "submitting",
        "remote_downloading",
        "remote_completed",
        "local_copying",
        "completed",
        "failed",
        "cancelled",
    }:
        return status
    return "pending"


def download_status_text(value: str, provider: str = "") -> str:
    provider_key_text = str(provider or "").lower()
    remote_wait_text = "等待云存储" if "rclone" in provider_key_text or "pikpak" in provider_key_text else "等待下载器完成"
    return {
        "pending": "排队中",
        "submitting": "提交下载器",
        "remote_downloading": remote_wait_text,
        "remote_completed": "下载器已完成",
        "local_copying": "下载中",
        "completed": "可观看",
        "failed": "失败",
        "cancelled": "已取消",
    }.get(download_phase(value), value or "排队中")


def human_size(value: int | str | None) -> str:
    try:
        size = max(0, int(value or 0))
    except (TypeError, ValueError):
        size = 0
    if size <= 0:
        return ""
    units = ["B", "KB", "MB", "GB", "TB"]
    amount = float(size)
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{int(amount)} B" if unit == "B" else f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{size} B"


def provider_index_from_key(value: str) -> int:
    text = str(value or "")
    if "#" not in text:
        return 0
    try:
        return max(0, int(text.rsplit("#", 1)[1]) - 1)
    except ValueError:
        return 0


def active_download_exists(conn, entry_id: int, episode_number: int) -> bool:
    placeholders = ",".join("?" for _ in ACTIVE_DOWNLOAD_STATUSES)
    row = conn.execute(
        f"""
        SELECT 1
        FROM download_jobs
        WHERE entry_id=? AND episode_number=? AND status IN ({placeholders})
        LIMIT 1
        """,
        (entry_id, episode_number, *ACTIVE_DOWNLOAD_STATUSES),
    ).fetchone()
    return bool(row)


def selected_episode_resource(conn, entry_id: int, episode_number: int):
    return conn.execute(
        """
        SELECT *
        FROM episode_resources
        WHERE entry_id=? AND episode_number=?
        ORDER BY selected DESC, id DESC
        LIMIT 1
        """,
        (entry_id, episode_number),
    ).fetchone()


def _source_to_release_fields(source_ref: str) -> tuple[str, str]:
    text = str(source_ref or "").strip()
    if text.startswith("magnet:"):
        return "", text
    return text, ""


def ensure_release_for_episode(conn, episode) -> int:
    release_id = int(episode["release_id"] or 0)
    if release_id > 0:
        exists = conn.execute("SELECT id FROM releases WHERE id=?", (release_id,)).fetchone()
        if exists:
            return release_id
    source_ref = str(episode["resource_ref"] or "").strip()
    if not source_ref:
        return 0
    entry = conn.execute(
        """
        SELECT id, display_title, title_cn, title_raw, season_number, year, media_type
        FROM entries
        WHERE id=?
        """,
        (int(episode["entry_id"] or 0),),
    ).fetchone()
    if not entry:
        return 0
    torrent_url, magnet = _source_to_release_fields(source_ref)
    digest = hashlib.sha1(source_ref.encode("utf-8", errors="ignore")).hexdigest()[:24]
    guid = f"episode:{int(episode['entry_id'] or 0)}:{int(episode['episode_number'] or 0)}:{digest}"
    ts = now()
    title = str(episode["source_title"] or entry["display_title"] or entry["title_cn"] or entry["title_raw"] or source_ref)
    conn.execute(
        """
        INSERT INTO releases
          (series_id, entry_id, episode_number, guid, title, subtitle_group, resolution, language,
           subtitle_format, torrent_url, magnet, published_at, selected, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
        ON CONFLICT(guid) DO UPDATE SET
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
            int(episode["series_id"] or episode["entry_id"] or 0),
            int(episode["entry_id"] or 0),
            int(episode["episode_number"] or 0),
            guid,
            title,
            str(episode["subtitle_group"] or ""),
            str(episode["resolution"] or ""),
            str(episode["language"] or ""),
            str(episode["subtitle_format"] or ""),
            torrent_url,
            magnet,
            ts,
            ts,
            ts,
        ),
    )
    row = conn.execute("SELECT id FROM releases WHERE guid=?", (guid,)).fetchone()
    release_id = int(row["id"] or 0) if row else 0
    if release_id > 0:
        conn.execute(
            "UPDATE episodes SET release_id=?, updated_at=? WHERE id=?",
            (release_id, ts, int(episode["id"] or 0)),
        )
    return release_id


def queue_download_for_episode(episode_id: int, *, reset_cancelled: bool = False) -> dict[str, Any]:
    if episode_id <= 0:
        return {"queued": False, "reason": "缺少 episode_id"}
    with connect() as conn:
        episode = conn.execute("SELECT * FROM episodes WHERE id=?", (episode_id,)).fetchone()
        if not episode:
            return {"queued": False, "reason": "集数不存在"}
        if int(episode["watchable"] or 0) == 1:
            return {"queued": False, "reason": "本地文件已存在"}
        release_id = ensure_release_for_episode(conn, episode)
    return queue_download_for_release(release_id, reset_cancelled=reset_cancelled)


def queue_download_for_release(release_id: int, *, reset_cancelled: bool = False) -> dict[str, Any]:
    if release_id <= 0:
        return {"queued": False, "reason": "缺少 release_id"}
    settings = settings_for_attempt(get_settings(), 0)
    with connect() as conn:
        release = conn.execute(
            """
            SELECT r.*, e.display_title, e.title_raw, e.title_cn, e.bangumi_id, e.tmdb_id,
                   e.year, e.season_number, e.media_type, e.target_library_id
            FROM releases r
            JOIN entries e ON e.id=r.entry_id
            WHERE r.id=?
            """,
            (release_id,),
        ).fetchone()
        if not release:
            return {"queued": False, "reason": "发布不存在"}
        entry_id = int(release["entry_id"] or 0)
        episode_number = int(release["episode_number"] or 0)
        local_asset = conn.execute(
            """
            SELECT id
            FROM local_assets
            WHERE entry_id=? AND episode_number=? AND status='synced'
            LIMIT 1
            """,
            (entry_id, episode_number),
        ).fetchone()
        if local_asset:
            return {"queued": False, "reason": "本地文件已存在"}
        if active_download_exists(conn, entry_id, episode_number):
            return {"queued": False, "reason": "已有活跃下载任务"}
        resource = selected_episode_resource(conn, entry_id, episode_number)
        episode = conn.execute(
            "SELECT id FROM episodes WHERE entry_id=? AND episode_number=? ORDER BY id DESC LIMIT 1",
            (entry_id, episode_number),
        ).fetchone()
        source_ref = str((resource and resource["source_ref"]) or release["magnet"] or release["torrent_url"] or "")
        remote_target = target_dir(dict(release), settings)
        remote_name = render_episode_file_name(
            dict(release),
            episode_number,
            "",
            settings,
            str(release["title"] or ""),
            source_ref,
        )
        remote_path = str(PurePosixPath(remote_target) / remote_name)
        target_local_path = local_episode_path({"artifact_name": remote_name, "episode_number": episode_number}, dict(release), settings)
        provider = provider_key(settings)
        ts = now()
        existing = conn.execute(
            """
            SELECT id, status
            FROM download_jobs
            WHERE entry_id=? AND episode_number=?
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            """,
            (entry_id, episode_number),
        ).fetchone()
        if existing and canonical_download_status(str(existing["status"] or "")) == "cancelled" and not reset_cancelled:
            return {"queued": False, "reason": "该集下载已取消，需手动重试"}
        conn.execute(
            """
            INSERT INTO download_jobs
              (series_id, entry_id, episode_resource_id, episode_id, episode_number, release_id,
               provider, provider_index, provider_key, download_task_id, status, phase, attempts,
               source_ref, target_dir, remote_path, target_local_path, normalized_name,
               progress, progress_text, created_at, updated_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 'pending', 'pending', 0,
                    ?, ?, ?, ?, ?, 0, '排队中', ?, ?, ?)
            ON CONFLICT(entry_id, episode_number, provider) DO UPDATE SET
              release_id=excluded.release_id,
              series_id=excluded.series_id,
              episode_resource_id=excluded.episode_resource_id,
              episode_id=excluded.episode_id,
              provider_index=excluded.provider_index,
              provider_key=excluded.provider_key,
              status='pending',
              phase='pending',
              retry_after='',
              last_error='',
              source_ref=excluded.source_ref,
              target_dir=excluded.target_dir,
              remote_path=excluded.remote_path,
              target_local_path=excluded.target_local_path,
              normalized_name=excluded.normalized_name,
              progress=0,
              progress_text='排队中',
              updated_at=excluded.updated_at,
              last_seen_at=excluded.last_seen_at
            """,
            (
                int(release["series_id"] or 0),
                entry_id,
                int(resource["id"] or 0) if resource else 0,
                int(episode["id"] or 0) if episode else 0,
                episode_number,
                release_id,
                provider,
                provider_index_from_key(provider),
                provider,
                source_ref,
                remote_target,
                remote_path,
                target_local_path,
                remote_name,
                ts,
                ts,
                ts,
            ),
        )
        task = conn.execute(
            """
            SELECT *
            FROM download_jobs
            WHERE entry_id=? AND episode_number=? AND provider=?
            """,
            (entry_id, episode_number, provider),
        ).fetchone()
        conn.execute(
            """
            UPDATE episode_resources
            SET status='queued', updated_at=?
            WHERE entry_id=? AND episode_number=? AND selected=1
            """,
            (ts, entry_id, episode_number),
        )
    return {"queued": True, "task": row_to_dict(task), "reason": "已创建下载任务"}


def list_download_tasks(limit: int = 200) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT dj.*,
                   e.display_title,
                   e.title_cn,
                   e.title_raw,
                   e.media_type AS entry_media_type,
                   COALESCE(ep.source_title, er.title, '') AS resource_title,
                   COALESCE(ep.resource_ref, er.source_ref, '') AS resource_ref,
                   ep.watchable AS episode_watchable,
                   la.id AS local_asset_id,
                   la.local_path AS local_asset_path
            FROM download_jobs dj
            LEFT JOIN entries e ON e.id=dj.entry_id
            LEFT JOIN episodes ep ON ep.id=dj.episode_id
            LEFT JOIN episode_resources er ON er.id=dj.episode_resource_id
            LEFT JOIN local_assets la
              ON la.id=(
                SELECT id
                FROM local_assets
                WHERE entry_id=dj.entry_id
                  AND episode_number=dj.episode_number
                  AND status='synced'
                ORDER BY updated_at DESC, id DESC
                LIMIT 1
              )
            ORDER BY CASE dj.status
              WHEN 'local_copying' THEN 0
              WHEN 'remote_completed' THEN 1
              WHEN 'remote_downloading' THEN 2
              WHEN 'submitting' THEN 3
              WHEN 'pending' THEN 4
              WHEN 'failed' THEN 5
              WHEN 'cancelled' THEN 6
              WHEN 'completed' THEN 7
              ELSE 8
            END, dj.created_at ASC, dj.id ASC
            LIMIT ?
            """,
            (max(1, min(500, int(limit or 200))),),
        ).fetchall()
    tasks: list[dict[str, Any]] = []
    for row in rows:
        item = row_to_dict(row)
        status = canonical_download_status(str(item.get("status") or ""))
        item["status"] = status
        item["phase"] = download_phase(str(item.get("phase") or status))
        item["status_text"] = download_status_text(status, str(item.get("provider_key") or item.get("provider") or ""))
        stored_progress = max(0, min(100, int(item.get("progress") or 0)))
        stored_progress_text = str(item.get("progress_text") or "")
        target_path = str(item.get("target_local_path") or item.get("local_asset_path") or "")
        current_size = 0
        if target_path:
            try:
                path = Path(target_path)
                partial_path = path.with_name(f"{path.name}.anitrack.part")
                size_path = partial_path if status == "local_copying" and partial_path.exists() else path
                if size_path.exists() and size_path.is_file():
                    current_size = size_path.stat().st_size
            except OSError:
                current_size = 0
        total_size = int(item.get("total_size") or 0)
        downloaded_size = max(int(item.get("downloaded_size") or 0), current_size)
        item["downloaded_size"] = downloaded_size
        item["downloaded_size_text"] = human_size(downloaded_size)
        item["total_size_text"] = human_size(total_size)
        if status in ACTIVE_DOWNLOAD_STATUSES:
            if status == "local_copying" and total_size > 0 and downloaded_size > 0:
                item["progress"] = min(99, max(1, int(downloaded_size * 100 / total_size)))
                item["progress_text"] = f"{item['downloaded_size_text']} / {item['total_size_text']}"
            elif status == "local_copying" and downloaded_size > 0:
                item["progress"] = 0
                item["progress_text"] = f"已写入 {item['downloaded_size_text']}"
            elif status == "local_copying":
                item["progress"] = min(99, stored_progress)
                item["progress_text"] = stored_progress_text or "正在复制到本地"
            else:
                item["progress"] = 0
                item["progress_text"] = "-"
        if status == "completed":
            item["progress"] = 100
            item["progress_text"] = "可观看"
        elif status == "failed":
            item["progress"] = 0
            item["progress_text"] = item.get("last_error") or "失败"
        elif status == "cancelled":
            item["progress"] = 0
            item["progress_text"] = "已取消"
        item["display_title"] = item.get("display_title") or item.get("title_cn") or item.get("title_raw") or "未命名条目"
        item["resource_title"] = item.get("resource_title") or item.get("normalized_name") or item.get("source_ref") or "-"
        item["active"] = status in ACTIVE_DOWNLOAD_STATUSES
        tasks.append(item)
    return tasks


def download_overview(tasks: list[dict[str, Any]] | None = None) -> dict[str, int]:
    rows = tasks if tasks is not None else list_download_tasks()
    return {
        "total": len(rows),
        "active": sum(1 for item in rows if item.get("active")),
        "pending": sum(1 for item in rows if item.get("status") == "pending"),
        "failed": sum(1 for item in rows if item.get("status") == "failed"),
        "completed": sum(1 for item in rows if item.get("status") == "completed"),
    }
