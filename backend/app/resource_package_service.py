from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Any

from . import local_downloader_service, rclone_service
from .config import MEDIA_ROOT
from .database import connect
from .db import get_settings, log, now, upsert_calendar_entry
from .downloader_service import (
    backend_key,
    download_to_local,
    list_remote_files,
    provider_key,
    remote_file_id,
    settings_for_attempt,
    settings_for_provider,
    submit_download,
)
from .library import expected_local_episode_path
from .media_service import create_media_entry
from .nfo_service import generate_jellyfin_nfo_for_entry
from .parser import parse_episode
from .schemas import DiscoveryPackageDownloadPayload, MediaCreatePayload, ResourcePackageApplyPayload
from .sync_service import synthetic_task_id
from .utils import row_to_dict


VIDEO_SUFFIXES = {".mkv", ".mp4", ".avi", ".mov", ".wmv", ".ts", ".m2ts", ".flv", ".webm"}
SUBTITLE_SUFFIXES = {".ass", ".srt", ".ssa", ".vtt", ".sup", ".sub"}

_active_package_tasks: dict[int, asyncio.Task] = {}


def _resource_ref(row: Any) -> str:
    return str(row["resource_ref"] or "").strip()


def _file_kind(name: str) -> str:
    suffix = Path(name or "").suffix.lower()
    if suffix in VIDEO_SUFFIXES:
        return "video"
    if suffix in SUBTITLE_SUFFIXES:
        return "subtitle"
    return "other"


def _package_base_dir(settings: dict[str, str], entry_id: int, package_id: int) -> str:
    backend = backend_key(settings)
    if backend in {"aria2", "qb"}:
        return str((Path(MEDIA_ROOT) / ".anitrack-staging" / str(entry_id) / str(package_id)).resolve())
    return f"/.anitrack-staging/{entry_id}/{package_id}"


def _child_dir(base_dir: str, item_id: int) -> str:
    if Path(base_dir).is_absolute() and ":" in base_dir[:4]:
        return str(Path(base_dir) / f"resource-{item_id}")
    if Path(base_dir).is_absolute() and not base_dir.startswith("/.anitrack-staging"):
        return str(Path(base_dir) / f"resource-{item_id}")
    return f"{base_dir.rstrip('/')}/resource-{item_id}"


def _ensure_entry_for_result(result: dict[str, Any], payload: DiscoveryPackageDownloadPayload) -> int:
    entry_id = int(payload.entry_id or 0)
    if entry_id > 0:
        with connect() as conn:
            exists = conn.execute("SELECT id FROM entries WHERE id=?", (entry_id,)).fetchone()
        if not exists:
            raise ValueError("绑定的媒体条目不存在")
        return entry_id
    media_type = str(result.get("media_type") or "anime")
    detail = create_media_entry(
        media_type,
        MediaCreatePayload(
            mode="metadata",
            title=result.get("title") or result.get("original_title") or "",
            bangumi_id=result.get("bangumi_id") or "",
            tmdb_id=result.get("tmdb_id") or "",
            year=int(result.get("year") or 0),
            poster_url=result.get("poster_url") or "",
            summary=result.get("summary") or "",
            tags_json=result.get("tags_json") or "[]",
        ),
    )
    created_id = int((detail.get("entry") or {}).get("id") or 0)
    if created_id <= 0:
        raise ValueError("作品收录失败，无法创建资源包")
    return created_id


def create_package_from_discovery(result_id: int, payload: DiscoveryPackageDownloadPayload) -> dict[str, Any]:
    with connect() as conn:
        result_row = conn.execute("SELECT * FROM discovery_results WHERE id=?", (result_id,)).fetchone()
        if not result_row:
            raise ValueError("发现结果不存在")
        resource_rows = conn.execute(
            """
            SELECT *
            FROM discovery_resources
            WHERE result_id=? AND resource_ref!=''
            ORDER BY source_id ASC, episode_number ASC, id ASC
            """,
            (result_id,),
        ).fetchall()
    result = row_to_dict(result_row)
    entry_id = _ensure_entry_for_result(result, payload)
    refs: set[str] = set()
    resources = []
    for row in resource_rows:
        ref = _resource_ref(row)
        if not ref or ref in refs:
            continue
        refs.add(ref)
        resources.append(row_to_dict(row))
    if not resources:
        raise ValueError("该发现结果没有可下载的种子或磁链")

    settings = settings_for_attempt(get_settings(), 0)
    provider = provider_key(settings)
    ts = now()
    title = result.get("title") or result.get("original_title") or "资源包"
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO resource_packages
              (entry_id, result_id, search_id, title, media_type, provider, status, match_status,
               total_resources, completed_resources, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'queued', 'pending', ?, 0, ?, ?)
            """,
            (
                entry_id,
                result_id,
                int(result.get("search_id") or 0),
                title,
                result.get("media_type") or "anime",
                provider,
                len(resources),
                ts,
                ts,
            ),
        )
        package_id = int(conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"])
        base_dir = _package_base_dir(settings, entry_id, package_id)
        conn.execute(
            "UPDATE resource_packages SET target_dir=?, updated_at=? WHERE id=?",
            (base_dir, ts, package_id),
        )
        for resource in resources:
            conn.execute(
                """
                INSERT INTO resource_package_items
                  (package_id, discovery_resource_id, source_ref, source_title, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'queued', ?, ?)
                """,
                (
                    package_id,
                    int(resource.get("id") or 0),
                    resource.get("resource_ref") or "",
                    resource.get("source_title") or "",
                    ts,
                    ts,
                ),
            )
            item_id = int(conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"])
            conn.execute(
                "UPDATE resource_package_items SET target_dir=?, updated_at=? WHERE id=?",
                (_child_dir(base_dir, item_id), ts, item_id),
            )
    trigger_package_download(package_id)
    return package_detail(package_id)


def trigger_package_download(package_id: int) -> None:
    if package_id <= 0:
        return
    task = _active_package_tasks.get(package_id)
    if task and not task.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = loop.create_task(run_package_download(package_id))
    _active_package_tasks[package_id] = task
    task.add_done_callback(lambda finished, key=package_id: _active_package_tasks.pop(key, None))


async def run_package_download(package_id: int) -> None:
    with connect() as conn:
        package = conn.execute("SELECT * FROM resource_packages WHERE id=?", (package_id,)).fetchone()
        items = conn.execute("SELECT * FROM resource_package_items WHERE package_id=? ORDER BY id", (package_id,)).fetchall()
    if not package:
        return
    settings = settings_for_provider(get_settings(), str(package["provider"] or ""))
    ts = now()
    with connect() as conn:
        conn.execute(
            "UPDATE resource_packages SET status='downloading', last_error='', updated_at=? WHERE id=?",
            (ts, package_id),
        )
    completed = 0
    for item in items:
        item_id = int(item["id"] or 0)
        source = str(item["source_ref"] or "")
        target_dir = str(item["target_dir"] or "")
        try:
            with connect() as conn:
                conn.execute(
                    "UPDATE resource_package_items SET status='submitting', last_error='', updated_at=? WHERE id=?",
                    (now(), item_id),
                )
            result = await submit_download(settings, source, target_dir, "")
            submission_id = str(result.get("task_id") or result.get("id") or "") if isinstance(result, dict) else ""
            provider_file_id = str(result.get("file_id") or result.get("id") or "") if isinstance(result, dict) else ""
            with connect() as conn:
                conn.execute(
                    """
                    UPDATE resource_package_items
                    SET status='downloading', submission_id=?, provider_file_id=?, updated_at=?
                    WHERE id=?
                    """,
                    (submission_id, provider_file_id, now(), item_id),
                )
            completed += 1
        except Exception as exc:
            message = str(exc)[:2000]
            log("error", f"资源包下载提交失败: package_id={package_id} item_id={item_id} error={message}")
            with connect() as conn:
                conn.execute(
                    "UPDATE resource_package_items SET status='failed', last_error=?, updated_at=? WHERE id=?",
                    (message, now(), item_id),
                )
    status = "downloading" if completed else "failed"
    with connect() as conn:
        conn.execute(
            """
            UPDATE resource_packages
            SET status=?, completed_resources=?, last_error=CASE WHEN ?=0 THEN '资源包下载提交失败' ELSE '' END, updated_at=?
            WHERE id=?
            """,
            (status, completed, completed, now(), package_id),
        )
    try:
        await scan_package_async(package_id)
    except Exception as exc:
        log("warn", f"资源包首次扫描失败: package_id={package_id} error={str(exc)[:1000]}")


def list_entry_packages(entry_id: int) -> dict[str, Any]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM resource_packages WHERE entry_id=? ORDER BY updated_at DESC, id DESC",
            (entry_id,),
        ).fetchall()
    return {"items": [row_to_dict(row) for row in rows]}


def _package_or_error(package_id: int) -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM resource_packages WHERE id=?", (package_id,)).fetchone()
    if not row:
        raise ValueError("资源包不存在")
    return row_to_dict(row)


def package_detail(package_id: int) -> dict[str, Any]:
    package = _package_or_error(package_id)
    with connect() as conn:
        entry = conn.execute("SELECT id, display_title, title_cn, title_raw FROM entries WHERE id=?", (package["entry_id"],)).fetchone()
        items = conn.execute("SELECT * FROM resource_package_items WHERE package_id=? ORDER BY id", (package_id,)).fetchall()
        files = conn.execute(
            "SELECT * FROM resource_package_files WHERE package_id=? ORDER BY file_kind DESC, episode_number ASC, file_name ASC",
            (package_id,),
        ).fetchall()
    return {
        "package": package,
        "entry": row_to_dict(entry),
        "items": [row_to_dict(row) for row in items],
        "files": [row_to_dict(row) for row in files],
        "active": package_id in _active_package_tasks,
    }


async def scan_package_async(package_id: int) -> dict[str, Any]:
    package = _package_or_error(package_id)
    settings = settings_for_provider(get_settings(), str(package.get("provider") or ""))
    with connect() as conn:
        items = conn.execute("SELECT * FROM resource_package_items WHERE package_id=? ORDER BY id", (package_id,)).fetchall()
    seen = 0
    for item in items:
        item_id = int(item["id"] or 0)
        target_dir = str(item["target_dir"] or "")
        try:
            files = await list_remote_files(settings, target_dir, recursive=True)
        except Exception as exc:
            log("warn", f"资源包目录扫描失败: package_id={package_id} item_id={item_id} error={str(exc)[:1000]}")
            continue
        for file_item in files:
            if file_item.get("is_dir"):
                continue
            _upsert_scanned_file(package_id, item_id, file_item)
            seen += 1
    _refresh_package_counts(package_id, "scanned" if seen else str(package.get("status") or "downloading"))
    return package_detail(package_id)


def _upsert_scanned_file(package_id: int, item_id: int, file_item: dict[str, Any]) -> None:
    file_path = str(file_item.get("remote_path") or "")
    name = str(file_item.get("name") or Path(file_path).name)
    if not file_path and not name:
        return
    kind = _file_kind(name)
    inferred = parse_episode(name) if kind in {"video", "subtitle"} else 0
    role = kind if kind in {"video", "subtitle"} else ""
    status = "ignored" if kind == "other" else "matched" if inferred > 0 else "pending"
    ignored = 1 if kind == "other" else 0
    ts = now()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO resource_package_files
              (package_id, item_id, file_path, provider_file_id, file_name, file_kind, size,
               inferred_episode_number, episode_number, role, status, ignored, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(package_id, file_path) DO UPDATE SET
              item_id=excluded.item_id,
              provider_file_id=excluded.provider_file_id,
              file_name=excluded.file_name,
              file_kind=excluded.file_kind,
              size=excluded.size,
              inferred_episode_number=excluded.inferred_episode_number,
              episode_number=CASE
                WHEN resource_package_files.episode_number > 0 THEN resource_package_files.episode_number
                ELSE excluded.episode_number
              END,
              role=CASE
                WHEN resource_package_files.role != '' THEN resource_package_files.role
                ELSE excluded.role
              END,
              status=CASE
                WHEN resource_package_files.status IN ('applied', 'skipped', 'ignored') THEN resource_package_files.status
                ELSE excluded.status
              END,
              ignored=CASE
                WHEN resource_package_files.ignored=1 THEN 1
                ELSE excluded.ignored
              END,
              updated_at=excluded.updated_at
            """,
            (
                package_id,
                item_id,
                file_path or name,
                remote_file_id(file_item),
                name,
                kind,
                int(file_item.get("size") or 0),
                inferred,
                inferred,
                role,
                status,
                ignored,
                ts,
                ts,
            ),
        )


def _refresh_package_counts(package_id: int, status: str = "") -> None:
    with connect() as conn:
        counts = conn.execute(
            """
            SELECT
              SUM(CASE WHEN status IN ('matched', 'applied', 'skipped') AND ignored=0 THEN 1 ELSE 0 END) AS matched,
              SUM(CASE WHEN file_kind IN ('video', 'subtitle') AND ignored=0 AND episode_number<=0 THEN 1 ELSE 0 END) AS unmatched
            FROM resource_package_files
            WHERE package_id=?
            """,
            (package_id,),
        ).fetchone()
        fields = {
            "matched_files": int(counts["matched"] or 0) if counts else 0,
            "unmatched_files": int(counts["unmatched"] or 0) if counts else 0,
            "updated_at": now(),
        }
        if status:
            fields["status"] = status
            fields["match_status"] = "ready" if fields["unmatched_files"] == 0 else "needs_review"
        assignments = ", ".join(f"{key}=?" for key in fields)
        conn.execute(f"UPDATE resource_packages SET {assignments} WHERE id=?", [*fields.values(), package_id])


def _ensure_episode(conn, entry_id: int, episode_number: int, ts: str):
    conn.execute(
        """
        INSERT INTO episodes (series_id, entry_id, episode_number, title, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, 'configured', ?, ?)
        ON CONFLICT(series_id, episode_number) DO UPDATE SET
          entry_id=excluded.entry_id,
          updated_at=excluded.updated_at
        """,
        (entry_id, entry_id, episode_number, f"第{episode_number:02d}话", ts, ts),
    )
    return conn.execute(
        "SELECT * FROM episodes WHERE entry_id=? AND episode_number=? ORDER BY id DESC LIMIT 1",
        (entry_id, episode_number),
    ).fetchone()


def _existing_watchable_path(conn, entry_id: int, episode_number: int) -> str:
    row = conn.execute(
        """
        SELECT local_path
        FROM local_assets
        WHERE entry_id=? AND episode_number=? AND status='synced'
        ORDER BY updated_at DESC, id DESC
        LIMIT 1
        """,
        (entry_id, episode_number),
    ).fetchone()
    path = str(row["local_path"] or "") if row else ""
    if path and Path(path).exists():
        return path
    return ""


async def apply_package_match(package_id: int, payload: ResourcePackageApplyPayload) -> dict[str, Any]:
    package = _package_or_error(package_id)
    entry_id = int(package.get("entry_id") or 0)
    if entry_id <= 0:
        raise ValueError("资源包未绑定媒体条目")
    ts = now()
    with connect() as conn:
        for item in payload.files:
            file_id = int(item.file_id or 0)
            if file_id <= 0:
                continue
            ignored = 1 if item.ignored else 0
            role = str(item.role or "").strip().lower()
            if role not in {"video", "subtitle", ""}:
                role = ""
            status = "ignored" if ignored else "matched" if int(item.episode_number or 0) > 0 else "pending"
            conn.execute(
                """
                UPDATE resource_package_files
                SET episode_number=?, role=?, ignored=?, status=?, updated_at=?
                WHERE id=? AND package_id=?
                """,
                (int(item.episode_number or 0), role, ignored, status, ts, file_id, package_id),
            )
        entry = conn.execute("SELECT * FROM entries WHERE id=?", (entry_id,)).fetchone()
        files = conn.execute(
            """
            SELECT *
            FROM resource_package_files
            WHERE package_id=? AND ignored=0 AND episode_number>0 AND role IN ('video', 'subtitle')
            ORDER BY role DESC, episode_number ASC, id ASC
            """,
            (package_id,),
        ).fetchall()
    if not entry:
        raise ValueError("媒体条目不存在")
    settings = settings_for_provider(get_settings(), str(package.get("provider") or ""))
    applied = 0
    skipped = 0
    subtitles = 0
    touched_entry = False
    for row in files:
        role = str(row["role"] or "")
        episode_number = int(row["episode_number"] or 0)
        source_path = str(row["file_path"] or "")
        provider_file_id = str(row["provider_file_id"] or "")
        suffix = Path(str(row["file_name"] or source_path)).suffix or ".mkv"
        with connect() as conn:
            episode = _ensure_episode(conn, entry_id, episode_number, ts)
            existing_path = _existing_watchable_path(conn, entry_id, episode_number)
        if role == "video":
            if existing_path:
                final_path = existing_path
                skipped += 1
                status = "skipped"
            else:
                final_path = expected_local_episode_path(dict(entry), episode_number, suffix, get_settings())
                if not final_path:
                    continue
                await download_to_local(settings, provider_file_id, source_path, final_path)
                status = "applied"
                applied += 1
            with connect() as conn:
                episode = _ensure_episode(conn, entry_id, episode_number, ts)
                episode_id = int(episode["id"] or 0)
                digest_source = f"package:{package_id}:file:{int(row['id'] or 0)}"
                asset_id = synthetic_task_id(digest_source)
                conn.execute("UPDATE episode_resources SET selected=0 WHERE entry_id=? AND episode_number=?", (entry_id, episode_number))
                conn.execute(
                    """
                    INSERT INTO episode_resources
                      (entry_id, episode_id, episode_number, source_type, source_ref, title,
                       selected, downloaded, local_path, status, created_at, updated_at)
                    VALUES (?, ?, ?, 'package', ?, ?, 1, 1, ?, 'downloaded', ?, ?)
                    ON CONFLICT(entry_id, episode_number, source_type, source_ref) DO UPDATE SET
                      episode_id=excluded.episode_id,
                      title=excluded.title,
                      selected=1,
                      downloaded=1,
                      local_path=excluded.local_path,
                      status='downloaded',
                      updated_at=excluded.updated_at
                    """,
                    (entry_id, episode_id, episode_number, source_path, row["file_name"] or source_path, final_path, ts, ts),
                )
                conn.execute(
                    """
                    INSERT INTO local_assets
                      (download_artifact_id, release_id, series_id, entry_id, episode_number, local_path,
                       nfo_status, status, created_at, updated_at)
                    VALUES (?, 0, ?, ?, ?, ?, 'pending', 'synced', ?, ?)
                    ON CONFLICT(download_artifact_id) DO UPDATE SET
                      local_path=excluded.local_path,
                      status='synced',
                      updated_at=excluded.updated_at
                    """,
                    (asset_id, entry_id, entry_id, episode_number, final_path, ts, ts),
                )
                conn.execute(
                    """
                    UPDATE episodes
                    SET local_path=?, watchable=1, status='downloaded', source_type='package', updated_at=?
                    WHERE id=?
                    """,
                    (final_path, ts, episode_id),
                )
                upsert_calendar_entry(conn, entry_id, episode_number, ts, True)
                conn.execute(
                    "UPDATE resource_package_files SET status=?, final_path=?, updated_at=? WHERE id=?",
                    (status, final_path, ts, int(row["id"] or 0)),
                )
            touched_entry = True
        elif role == "subtitle":
            with connect() as conn:
                episode = _ensure_episode(conn, entry_id, episode_number, ts)
                video_path = str(episode["local_path"] or "") or _existing_watchable_path(conn, entry_id, episode_number)
            if not video_path:
                skipped += 1
                with connect() as conn:
                    conn.execute(
                        "UPDATE resource_package_files SET status='pending', updated_at=? WHERE id=?",
                        (ts, int(row["id"] or 0)),
                    )
                continue
            final_path = str(Path(video_path).with_suffix(suffix))
            await download_to_local(settings, provider_file_id, source_path, final_path)
            with connect() as conn:
                episode = _ensure_episode(conn, entry_id, episode_number, ts)
                episode_id = int(episode["id"] or 0)
                conn.execute("UPDATE episode_subtitles SET selected=0 WHERE entry_id=? AND episode_number=?", (entry_id, episode_number))
                conn.execute(
                    """
                    INSERT INTO episode_subtitles
                      (episode_id, entry_id, episode_number, language, subtitle_format, subtitle_path,
                       file_name, embedded, selected, created_at, updated_at)
                    VALUES (?, ?, ?, '', 'external', ?, ?, 0, 1, ?, ?)
                    """,
                    (episode_id, entry_id, episode_number, final_path, Path(final_path).name, ts, ts),
                )
                conn.execute("UPDATE episodes SET subtitle_path=?, updated_at=? WHERE id=?", (final_path, ts, episode_id))
                conn.execute(
                    "UPDATE resource_package_files SET status='applied', final_path=?, updated_at=? WHERE id=?",
                    (final_path, ts, int(row["id"] or 0)),
                )
            subtitles += 1
            touched_entry = True
    if touched_entry:
        generate_jellyfin_nfo_for_entry(entry_id, get_settings())
    with connect() as conn:
        conn.execute(
            """
            UPDATE resource_packages
            SET status='organized', match_status='applied', updated_at=?
            WHERE id=?
            """,
            (now(), package_id),
        )
    _refresh_package_counts(package_id)
    detail = package_detail(package_id)
    detail["result"] = {"applied": applied, "skipped": skipped, "subtitles": subtitles}
    return detail


async def cleanup_package_async(package_id: int) -> dict[str, Any]:
    package = _package_or_error(package_id)
    target_dir = str(package.get("target_dir") or "")
    settings = settings_for_provider(get_settings(), str(package.get("provider") or ""))
    backend = backend_key(settings)
    removed = False
    if backend in {"aria2", "qb"}:
        target = Path(target_dir).resolve()
        root = (Path(MEDIA_ROOT) / ".anitrack-staging").resolve()
        if target == root or root not in target.parents:
            raise ValueError("资源包清理路径不在 staging 目录内")
        if target.exists():
            shutil.rmtree(target)
            removed = True
    elif backend == "local":
        target = local_downloader_service.safe_path(settings, target_dir)
        root = local_downloader_service.safe_path(settings, "/.anitrack-staging")
        if target == root or root not in target.parents:
            raise ValueError("资源包清理路径不在本地下载器 staging 目录内")
        if target.exists():
            shutil.rmtree(target)
            removed = True
    elif backend == "rclone":
        await rclone_service.run_rclone(settings, ["purge", rclone_service.remote_path(settings, target_dir)], timeout=300)
        removed = True
    else:
        raise ValueError("当前下载器暂不支持自动清理资源包临时目录")
    with connect() as conn:
        conn.execute(
            "UPDATE resource_packages SET status='cleaned', match_status='cleaned', updated_at=? WHERE id=?",
            (now(), package_id),
        )
    return {"status": "cleaned", "removed": removed, "package": package_detail(package_id)["package"]}
