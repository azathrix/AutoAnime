from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi import APIRouter, HTTPException

from ..database import connect
from ..db import get_settings, log, now
from ..download_task_service import queue_download_for_episode, queue_download_for_release
from ..library import expected_local_episode_path
from ..config import MEDIA_ROOT
from ..maintenance import match_entry_local_files, organize_local_files, refresh_local_status
from ..media_service import build_entry_response, reset_orphaned_download_jobs_in_conn
from ..runtime_service import ACTIVE_DOWNLOAD_STATUSES
from ..pipeline_orchestrator import cancel_active_processor_tasks, start_pipeline
from ..runtime_service import DOWNLOAD_RUNTIME_PROCESSORS, trigger_queue
from ..runtime_store import runtime_store
from ..schemas import (
    BatchSubtitlePayload,
    EpisodeDownloadActionPayload,
    EpisodePayload,
    EpisodeImportPayload,
    EpisodeResourcePayload,
    EpisodeSubtitlePayload,
    LocalPathMatchPayload,
)
from ..utils import (
    is_valid_resource_reference,
    is_valid_subtitle_reference,
    parsed_episode_required,
    row_to_dict,
    split_input_lines,
    subtitle_embedded_value,
)


router = APIRouter()


def _validated_media_path(value: str) -> Path:
    root = Path(MEDIA_ROOT).resolve()
    raw = str(value or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="请选择媒体目录或文件")
    path = Path(raw)
    if not path.is_absolute():
        path = root / path
    resolved = path.resolve()
    if resolved != root and root not in resolved.parents:
        raise HTTPException(status_code=400, detail="只能选择媒体目录下的文件")
    if not resolved.exists():
        raise HTTPException(status_code=404, detail="路径不存在")
    return resolved


@router.post("/api/entries/{entry_id}/resources/import")
async def api_import_entry_resources(entry_id: int, payload: EpisodeImportPayload) -> dict[str, Any]:
    resource_lines = split_input_lines(payload.resources_text)
    subtitle_lines = split_input_lines(payload.subtitles_text)
    resource_items = [item for item in payload.resources if item.source_ref.strip()]
    if not resource_items:
        resource_items = [
            SimpleNamespace(
                source_ref=line,
                episode_number=0,
                title=line,
                language="",
                subtitle_format="",
                subtitle_url="",
                subtitle_file_name="",
            )
            for line in resource_lines
        ]
    if not resource_items:
        raise HTTPException(status_code=400, detail="请至少填写一个资源链接")
    invalid_resources = [item.source_ref.strip() for item in resource_items if not is_valid_resource_reference(item.source_ref)]
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
        entry_media_type = str(entry["media_type"] or "")
        for index, item in enumerate(resource_items, start=1):
            line = item.source_ref.strip()
            episode_number = int(item.episode_number or 0) or parsed_episode_required(line)
            if episode_number <= 0 and entry_media_type == "movie":
                episode_number = 1
            if episode_number <= 0:
                raise HTTPException(status_code=400, detail=f"资源无法识别集数: {line}")
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
                        item.title.strip() or line,
                        (item.language or payload.language).strip(),
                        (item.subtitle_format or payload.subtitle_format).strip(),
                        torrent_url,
                        magnet,
                        ts,
                        ts,
                        ts,
                    ),
                )
                release = conn.execute("SELECT id FROM releases WHERE guid=?", (guid,)).fetchone()
                release_id = int(release["id"] or 0) if release else 0
            suffix = Path(line).suffix if Path(line).suffix.lower() in {".mkv", ".mp4", ".avi", ".mov", ".wmv", ".ts", ".m2ts", ".flv", ".webm"} else ".mkv"
            local_path = expected_local_episode_path(dict(entry), episode_number, suffix, get_settings())
            conn.execute(
                """
                UPDATE episodes
                SET resource_ref=?,
                    source_title=?,
                    source_type='magnet',
                    subtitle_group=CASE WHEN subtitle_group='' THEN ? ELSE subtitle_group END,
                    resolution=CASE WHEN resolution='' THEN ? ELSE resolution END,
                    language=CASE WHEN language='' THEN ? ELSE language END,
                    subtitle_format=CASE WHEN subtitle_format='' THEN ? ELSE subtitle_format END,
                    local_path=CASE WHEN local_path='' THEN ? ELSE local_path END,
                    release_id=CASE WHEN ? > 0 THEN ? ELSE release_id END,
                    status='available',
                    updated_at=?
                WHERE id=?
                """,
                (
                    line,
                    item.title.strip() or line,
                    "",
                    "",
                    (item.language or payload.language).strip(),
                    (item.subtitle_format or payload.subtitle_format).strip(),
                    local_path,
                    release_id,
                    release_id,
                    ts,
                    episode_id,
                ),
            )
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
                (
                    entry_id,
                    episode_id,
                    episode_number,
                    line,
                    release_id,
                    item.title.strip() or line,
                    (item.language or payload.language).strip(),
                    (item.subtitle_format or payload.subtitle_format).strip(),
                    torrent_url,
                    magnet,
                    ts,
                    ts,
                ),
            )
            subtitle_ref = (item.subtitle_url or item.subtitle_file_name or "").strip()
            if subtitle_ref:
                if not is_valid_subtitle_reference(subtitle_ref):
                    raise HTTPException(status_code=400, detail=f"字幕链接或文件名格式无效: {subtitle_ref}")
                subtitle_lines.append(subtitle_ref)
            created += 1
        for index, line in enumerate(subtitle_lines, start=1):
            episode_number = parsed_episode_required(line)
            if episode_number <= 0 and entry_media_type == "movie":
                episode_number = 1
            if episode_number <= 0:
                raise HTTPException(status_code=400, detail=f"字幕无法识别集数: {line}")
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
            conn.execute(
                "UPDATE episodes SET subtitle_ref=CASE WHEN subtitle_ref='' THEN ? ELSE subtitle_ref END, updated_at=? WHERE id=?",
                (line, ts, int(episode["id"])),
            )
    log("info", f"手动导入集数资源: entry_id={entry_id} count={created}")
    return {"status": "saved", "count": created, "detail": build_entry_response(entry_id)}


@router.post("/api/entries/{entry_id}/subtitles/batch")
async def api_batch_entry_subtitles(entry_id: int, payload: BatchSubtitlePayload) -> dict[str, Any]:
    subtitle_items = split_input_lines(payload.subtitles_text) + [item.strip() for item in payload.file_names if item.strip()]
    if not subtitle_items:
        raise HTTPException(status_code=400, detail="请填写字幕链接或选择字幕文件")
    invalid_subtitles = [item for item in subtitle_items if not is_valid_subtitle_reference(item)]
    if invalid_subtitles:
        raise HTTPException(status_code=400, detail=f"字幕链接或文件名格式无效: {invalid_subtitles[0]}")
    invalid_episode_subtitles = [item for item in subtitle_items if parsed_episode_required(item) <= 0]
    if invalid_episode_subtitles:
        raise HTTPException(status_code=400, detail=f"字幕无法识别集数: {invalid_episode_subtitles[0]}")
    ts = now()
    saved = 0
    with connect() as conn:
        entry = conn.execute("SELECT id FROM entries WHERE id=?", (entry_id,)).fetchone()
        if not entry:
            raise HTTPException(status_code=404, detail="媒体条目不存在")
        for index, item in enumerate(subtitle_items, start=1):
            episode_number = parsed_episode_required(item)
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
            conn.execute(
                "UPDATE episodes SET subtitle_ref=CASE WHEN subtitle_ref='' THEN ? ELSE subtitle_ref END, updated_at=? WHERE id=?",
                (item, ts, int(episode["id"])),
            )
            saved += 1
    log("info", f"批量配置字幕: entry_id={entry_id} count={saved}")
    return {"status": "saved", "count": saved, "detail": build_entry_response(entry_id)}


@router.put("/api/episodes/{episode_id}/resource")
async def api_update_episode_resource(episode_id: int, payload: EpisodeResourcePayload) -> dict[str, Any]:
    ts = now()
    with connect() as conn:
        episode = conn.execute("SELECT * FROM episodes WHERE id=?", (episode_id,)).fetchone()
        if not episode:
            raise HTTPException(status_code=404, detail="集数不存在")
        if payload.selected:
            conn.execute("UPDATE episode_resources SET selected=0 WHERE entry_id=? AND episode_number=?", (episode["entry_id"], episode["episode_number"]))
            conn.execute("UPDATE releases SET selected=0 WHERE entry_id=? AND episode_number=?", (episode["entry_id"], episode["episode_number"]))
        if payload.resource_id:
            source_ref = payload.source_ref.strip()
            source_type = payload.source_type.strip()
            torrent_url = source_ref if source_ref.startswith("http") else ""
            magnet = source_ref if source_ref.startswith("magnet:") else ""
            conn.execute(
                """
                UPDATE episode_resources
                SET title=CASE WHEN ?='' THEN title ELSE ? END,
                    source_type=CASE WHEN ?='' THEN source_type ELSE ? END,
                    source_ref=CASE WHEN ?='' THEN source_ref ELSE ? END,
                    torrent_url=CASE WHEN ?='' THEN torrent_url ELSE ? END,
                    magnet=CASE WHEN ?='' THEN magnet ELSE ? END,
                    subtitle_group=CASE WHEN ?='' THEN subtitle_group ELSE ? END,
                    resolution=CASE WHEN ?='' THEN resolution ELSE ? END,
                    language=CASE WHEN ?='' THEN language ELSE ? END,
                    subtitle_format=CASE WHEN ?='' THEN subtitle_format ELSE ? END,
                    selected=?,
                    updated_at=?
                WHERE id=? AND entry_id=?
                """,
                (
                    payload.title.strip(), payload.title.strip(),
                    source_type, source_type,
                    source_ref, source_ref,
                    torrent_url, torrent_url,
                    magnet, magnet,
                    payload.subtitle_group.strip(), payload.subtitle_group.strip(),
                    payload.resolution.strip(), payload.resolution.strip(),
                    payload.language.strip(), payload.language.strip(),
                    payload.subtitle_format.strip(), payload.subtitle_format.strip(),
                    int(payload.selected), ts, payload.resource_id, episode["entry_id"],
                ),
            )
            row = conn.execute("SELECT * FROM episode_resources WHERE id=?", (payload.resource_id,)).fetchone()
            if payload.selected and row and int(row["release_id"] or 0) > 0:
                conn.execute("UPDATE releases SET selected=1 WHERE id=?", (int(row["release_id"]),))
        else:
            source_type = payload.source_type.strip() or "manual"
            source_ref = payload.source_ref.strip() or f"manual:{episode_id}:{ts}"
            torrent_url = source_ref if source_ref.startswith("http") else ""
            magnet = source_ref if source_ref.startswith("magnet:") else ""
            conn.execute(
                """
                INSERT INTO episode_resources
                  (entry_id, episode_id, episode_number, source_type, source_ref, title,
                   subtitle_group, resolution, language, subtitle_format, torrent_url, magnet, selected, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    episode["entry_id"], episode_id, episode["episode_number"], source_type, source_ref,
                    payload.title.strip(), payload.subtitle_group.strip(), payload.resolution.strip(),
                    payload.language.strip(), payload.subtitle_format.strip(), torrent_url, magnet, int(payload.selected), ts, ts,
                ),
            )
            row = conn.execute("SELECT * FROM episode_resources WHERE id=last_insert_rowid()").fetchone()
    return {"status": "saved", "item": row_to_dict(row)}


@router.put("/api/episodes/{episode_id}/subtitle")
async def api_update_episode_subtitle(episode_id: int, payload: EpisodeSubtitlePayload) -> dict[str, Any]:
    ts = now()
    with connect() as conn:
        episode = conn.execute("SELECT * FROM episodes WHERE id=?", (episode_id,)).fetchone()
        if not episode:
            raise HTTPException(status_code=404, detail="集数不存在")
        if payload.selected:
            conn.execute("UPDATE episode_subtitles SET selected=0 WHERE entry_id=? AND episode_number=?", (episode["entry_id"], episode["episode_number"]))
        if payload.subtitle_id:
            conn.execute(
                """
                UPDATE episode_subtitles
                SET language=?, subtitle_format=?, subtitle_path=?, subtitle_url=?,
                    file_name=?, embedded=?, selected=?, updated_at=?
                WHERE id=? AND entry_id=?
                """,
                (
                    payload.language.strip(), payload.subtitle_format.strip(), payload.subtitle_path.strip(),
                    payload.subtitle_url.strip(), payload.file_name.strip(), subtitle_embedded_value(payload.subtitle_format),
                    int(payload.selected), ts, payload.subtitle_id, episode["entry_id"],
                ),
            )
            row = conn.execute("SELECT * FROM episode_subtitles WHERE id=?", (payload.subtitle_id,)).fetchone()
        else:
            conn.execute(
                """
                INSERT INTO episode_subtitles
                  (episode_id, episode_resource_id, entry_id, episode_number, language,
                   subtitle_format, subtitle_path, subtitle_url, file_name, embedded, selected, created_at, updated_at)
                VALUES (?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    episode_id, episode["entry_id"], episode["episode_number"], payload.language.strip(),
                    payload.subtitle_format.strip(), payload.subtitle_path.strip(), payload.subtitle_url.strip(),
                    payload.file_name.strip(), subtitle_embedded_value(payload.subtitle_format), int(payload.selected), ts, ts,
                ),
            )
            row = conn.execute("SELECT * FROM episode_subtitles WHERE id=last_insert_rowid()").fetchone()
    return {"status": "saved", "item": row_to_dict(row)}


@router.put("/api/episodes/{episode_id}")
async def api_update_episode(episode_id: int, payload: EpisodePayload) -> dict[str, Any]:
    ts = now()
    with connect() as conn:
        episode = conn.execute("SELECT * FROM episodes WHERE id=?", (episode_id,)).fetchone()
        if not episode:
            raise HTTPException(status_code=404, detail="集数不存在")
        resource_ref = payload.resource_ref.strip()
        source_title = payload.source_title.strip()
        local_path = payload.local_path.strip()
        subtitle_path = payload.subtitle_path.strip()
        if local_path and not Path(local_path).exists():
            raise HTTPException(status_code=400, detail="本地视频文件不存在")
        if subtitle_path and not Path(subtitle_path).exists():
            raise HTTPException(status_code=400, detail="本地字幕文件不存在")
        watchable = 1 if local_path else int(episode["watchable"] or 0)
        conn.execute(
            """
            UPDATE episodes
            SET resource_ref=CASE WHEN ?='' THEN resource_ref ELSE ? END,
                subtitle_ref=CASE WHEN ?='' THEN subtitle_ref ELSE ? END,
                local_path=CASE WHEN ?='' THEN local_path ELSE ? END,
                subtitle_path=CASE WHEN ?='' THEN subtitle_path ELSE ? END,
                subtitle_group=CASE WHEN ?='' THEN subtitle_group ELSE ? END,
                resolution=CASE WHEN ?='' THEN resolution ELSE ? END,
                language=CASE WHEN ?='' THEN language ELSE ? END,
                subtitle_format=CASE WHEN ?='' THEN subtitle_format ELSE ? END,
                source_title=CASE WHEN ?='' THEN source_title ELSE ? END,
                source_type='magnet',
                release_id=CASE WHEN ?!='' THEN 0 ELSE release_id END,
                watchable=?,
                updated_at=?
            WHERE id=?
            """,
            (
                resource_ref, resource_ref,
                payload.subtitle_ref.strip(), payload.subtitle_ref.strip(),
                local_path, local_path,
                subtitle_path, subtitle_path,
                payload.subtitle_group.strip(), payload.subtitle_group.strip(),
                payload.resolution.strip(), payload.resolution.strip(),
                payload.language.strip(), payload.language.strip(),
                payload.subtitle_format.strip(), payload.subtitle_format.strip(),
                source_title, source_title,
                resource_ref,
                watchable,
                ts,
                episode_id,
            ),
        )
        row = conn.execute("SELECT * FROM episodes WHERE id=?", (episode_id,)).fetchone()
        if local_path:
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
                    int(row["entry_id"] or 0),
                    int(row["episode_number"] or 0),
                    local_path,
                    ts,
                    ts,
                ),
            )
    return {"status": "saved", "item": row_to_dict(row)}


@router.delete("/api/episode-resources/{resource_id}")
async def api_delete_episode_resource(resource_id: int) -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM episode_resources WHERE id=?", (resource_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="集数资源不存在")
        entry_id = int(row["entry_id"] or 0)
        episode_number = int(row["episode_number"] or 0)
        synced = conn.execute(
            """
            SELECT 1 FROM local_assets
            WHERE entry_id=? AND episode_number=? AND status='synced'
            LIMIT 1
            """,
            (entry_id, episode_number),
        ).fetchone()
        if synced:
            raise HTTPException(status_code=400, detail="该集已有本地文件，请先取消/清理本地资源")
        active_placeholders = ",".join("?" for _ in ACTIVE_DOWNLOAD_STATUSES)
        active = conn.execute(
            f"""
            SELECT 1 FROM download_jobs
            WHERE entry_id=? AND episode_number=?
              AND status IN ({active_placeholders})
            LIMIT 1
            """,
            (entry_id, episode_number, *ACTIVE_DOWNLOAD_STATUSES),
        ).fetchone()
        if active:
            raise HTTPException(status_code=400, detail="该集仍有下载任务，请先取消任务")
        release_id = int(row["release_id"] or 0)
        conn.execute("DELETE FROM episode_subtitles WHERE episode_resource_id=?", (resource_id,))
        conn.execute("DELETE FROM episode_resources WHERE id=?", (resource_id,))
        if release_id > 0:
            downstream = conn.execute(
                """
                SELECT 1
                WHERE EXISTS (SELECT 1 FROM download_jobs WHERE release_id=?)
                   OR EXISTS (SELECT 1 FROM download_artifacts WHERE release_id=?)
                   OR EXISTS (SELECT 1 FROM local_assets WHERE release_id=?)
                """,
                (release_id, release_id, release_id),
            ).fetchone()
            if not downstream:
                conn.execute("DELETE FROM releases WHERE id=?", (release_id,))
        remaining = conn.execute(
            """
            SELECT 1
            WHERE EXISTS (SELECT 1 FROM episode_resources WHERE entry_id=? AND episode_number=?)
               OR EXISTS (SELECT 1 FROM episode_subtitles WHERE entry_id=? AND episode_number=?)
               OR EXISTS (SELECT 1 FROM local_assets WHERE entry_id=? AND episode_number=?)
               OR EXISTS (SELECT 1 FROM releases WHERE entry_id=? AND episode_number=?)
            """,
            (entry_id, episode_number, entry_id, episode_number, entry_id, episode_number, entry_id, episode_number),
        ).fetchone()
        if episode_number > 0 and not remaining:
            conn.execute("DELETE FROM episodes WHERE entry_id=? AND episode_number=?", (entry_id, episode_number))
    return {"status": "deleted", "message": "集数资源已删除"}


@router.delete("/api/episodes/{episode_id}")
async def api_delete_episode(episode_id: int) -> dict[str, Any]:
    with connect() as conn:
        episode = conn.execute("SELECT * FROM episodes WHERE id=?", (episode_id,)).fetchone()
        if not episode:
            raise HTTPException(status_code=404, detail="集数不存在")
        entry_id = int(episode["entry_id"] or 0)
        episode_number = int(episode["episode_number"] or 0)
        active_placeholders = ",".join("?" for _ in ACTIVE_DOWNLOAD_STATUSES)
        active = conn.execute(
            f"""
            SELECT 1 FROM download_jobs
            WHERE entry_id=? AND episode_number=?
              AND status IN ({active_placeholders})
            LIMIT 1
            """,
            (entry_id, episode_number, *ACTIVE_DOWNLOAD_STATUSES),
        ).fetchone()
        if active:
            raise HTTPException(status_code=400, detail="该集仍有下载任务，请先取消任务")
        conn.execute("DELETE FROM episode_subtitles WHERE entry_id=? AND episode_number=?", (entry_id, episode_number))
        conn.execute("DELETE FROM episode_resources WHERE entry_id=? AND episode_number=?", (entry_id, episode_number))
        conn.execute("DELETE FROM releases WHERE entry_id=? AND episode_number=?", (entry_id, episode_number))
        conn.execute("DELETE FROM local_assets WHERE entry_id=? AND episode_number=?", (entry_id, episode_number))
        conn.execute("DELETE FROM episodes WHERE id=?", (episode_id,))
    return {"status": "deleted", "message": "集数已删除，本地文件不会被删除"}


@router.post("/api/episodes/{episode_id}/refresh")
async def api_refresh_episode_resource_state(episode_id: int) -> dict[str, Any]:
    with connect() as conn:
        episode = conn.execute("SELECT * FROM episodes WHERE id=?", (episode_id,)).fetchone()
        if not episode:
            raise HTTPException(status_code=404, detail="集数不存在")
        reset_orphaned_download_jobs_in_conn(conn, int(episode["entry_id"]), int(episode["episode_number"]))
    return refresh_local_status(episode_id=episode_id)


@router.post("/api/episodes/{episode_id}/refresh-local-status")
async def api_refresh_episode_local_status(episode_id: int) -> dict[str, Any]:
    return await api_refresh_episode_resource_state(episode_id)


@router.post("/api/episodes/{episode_id}/download")
async def api_download_episode_resource(episode_id: int) -> dict[str, Any]:
    with connect() as conn:
        episode = conn.execute("SELECT * FROM episodes WHERE id=?", (episode_id,)).fetchone()
        if not episode:
            raise HTTPException(status_code=404, detail="集数不存在")
        reset_orphaned_download_jobs_in_conn(conn, int(episode["entry_id"]), int(episode["episode_number"]))
        if not str(episode["resource_ref"] or "").strip() and int(episode["release_id"] or 0) <= 0:
            raise HTTPException(status_code=400, detail="该集没有可下载资源")
        episode_data = row_to_dict(episode)
    queued = queue_download_for_episode(episode_id, reset_cancelled=True)
    if not queued.get("queued") and queued.get("reason") != "已有活跃下载任务":
        return {"status": "skipped", "message": str(queued.get("reason") or "没有需要下载的资源")}
    with connect() as conn:
        refreshed_episode = conn.execute("SELECT release_id FROM episodes WHERE id=?", (episode_id,)).fetchone()
    release_id = int(refreshed_episode["release_id"] or 0) if refreshed_episode else int(episode_data.get("release_id") or 0)
    if release_id <= 0:
        return {"status": "skipped", "message": "该集没有可下载资源"}
    run_id = start_pipeline(
        "library_backfill",
        trigger_source="episode_download",
        first_step_key="download",
        subject_type="release",
        subject_id=release_id,
        payload={
            "_dedupe_key": f"download:entry:{int(episode_data['entry_id'])}:episode:{int(episode_data['episode_number'])}",
            "entry_id": int(episode_data["entry_id"]),
            "release_id": release_id,
            "episode_number": int(episode_data["episode_number"]),
            "domain_kind": "library",
        },
        message=f"手动下载集数: entry_id={episode_data['entry_id']} episode={episode_data['episode_number']}",
    )
    trigger_queue("processor", delay=0)
    log(
        "info",
        f"单集下载请求: entry_id={int(episode_data['entry_id'])} episode={int(episode_data['episode_number'])} "
        f"release_id={release_id} run_id={run_id}",
    )
    return {"status": "started", "download_run_id": run_id, "message": "已加入下载队列"}


@router.post("/api/entries/{entry_id}/download")
async def api_download_entry_resources(entry_id: int) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    with connect() as conn:
        entry = conn.execute("SELECT id FROM entries WHERE id=?", (entry_id,)).fetchone()
        if not entry:
            raise HTTPException(status_code=404, detail="媒体条目不存在")
        reset_orphaned_download_jobs_in_conn(conn, entry_id)
        active_placeholders = ",".join("?" for _ in ACTIVE_DOWNLOAD_STATUSES)
        candidates = conn.execute(
            f"""
            SELECT ep.id AS episode_id, ep.entry_id, ep.episode_number, ep.release_id, ep.resource_ref
            FROM episodes ep
            LEFT JOIN local_assets la ON la.entry_id=ep.entry_id AND la.episode_number=ep.episode_number AND la.status='synced'
            WHERE ep.entry_id=?
              AND ep.episode_number > 0
              AND COALESCE(ep.watchable, 0)=0
              AND (ep.release_id > 0 OR ep.resource_ref != '')
              AND la.id IS NULL
              AND NOT EXISTS (
                SELECT 1 FROM download_jobs dj
                WHERE dj.entry_id=ep.entry_id AND dj.episode_number=ep.episode_number
                  AND dj.status IN ({active_placeholders})
              )
            ORDER BY ep.episode_number ASC, ep.id ASC
            """,
            (entry_id, *ACTIVE_DOWNLOAD_STATUSES),
        ).fetchall()
    seen_episodes: set[int] = set()
    for candidate in candidates:
        episode_number = int(candidate["episode_number"] or 0)
        episode_id = int(candidate["episode_id"] or 0)
        if episode_number <= 0 or episode_id <= 0 or episode_number in seen_episodes:
            continue
        seen_episodes.add(episode_number)
        if runtime_store.has_active_episode_task(entry_id, episode_number, DOWNLOAD_RUNTIME_PROCESSORS):
            continue
        queued = queue_download_for_episode(episode_id)
        if not queued.get("queued"):
            continue
        with connect() as conn:
            release_row = conn.execute("SELECT release_id FROM episodes WHERE id=?", (episode_id,)).fetchone()
        release_id = int(release_row["release_id"] or 0) if release_row else 0
        if release_id <= 0:
            continue
        rows.append({"entry_id": entry_id, "episode_number": episode_number, "release_id": release_id})
    run_ids: list[int] = []
    for row in rows:
        run_id = start_pipeline(
            "library_backfill",
            trigger_source="entry_batch_download",
            first_step_key="download",
            subject_type="release",
            subject_id=int(row["release_id"]),
            payload={
                "_dedupe_key": f"download:entry:{entry_id}:episode:{int(row['episode_number'])}",
                "entry_id": entry_id,
                "release_id": int(row["release_id"]),
                "episode_number": int(row["episode_number"]),
                "domain_kind": "library",
            },
            message=f"批量下载集数: entry_id={entry_id} episode={row['episode_number']}",
        )
        run_ids.append(run_id)
    if run_ids:
        trigger_queue("processor", delay=0)
    log(
        "info",
        f"批量下载请求: entry_id={entry_id} candidates={len(rows)} runs={len(run_ids)} "
        f"message={'已提交下载流水线' if run_ids else '没有符合条件的待下载集数'}",
    )
    return {
        "status": "started" if run_ids else "skipped",
        "count": len(run_ids),
        "download_run_ids": run_ids,
        "message": f"已加入 {len(run_ids)} 个下载任务" if run_ids else "没有需要批量下载的集数",
    }


@router.post("/api/episodes/{episode_id}/download/cancel")
async def api_cancel_episode_download(episode_id: int) -> dict[str, Any]:
    return await _set_episode_download_state(episode_id, "cancelled", "用户取消下载", "已取消该集下载任务")


@router.post("/api/downloads/cancel")
async def api_cancel_download_by_entry_episode(payload: EpisodeDownloadActionPayload) -> dict[str, Any]:
    entry_id = int(payload.entry_id or 0)
    episode_number = int(payload.episode_number or 0)
    if entry_id <= 0 or episode_number <= 0:
        raise HTTPException(status_code=400, detail="取消下载缺少 entry_id 或 episode_number")
    return await _set_episode_download_state_by_entry_episode(
        entry_id,
        episode_number,
        "cancelled",
        "用户从队列取消下载",
        "已取消该集下载任务",
    )


@router.post("/api/downloads/cancel-all")
async def api_cancel_all_downloads() -> dict[str, Any]:
    cancelled_runtime = await runtime_store.cancel_processor_tasks(DOWNLOAD_RUNTIME_PROCESSORS)
    cancelled_active = await cancel_active_processor_tasks(DOWNLOAD_RUNTIME_PROCESSORS)
    ts = now()
    active_placeholders = ",".join("?" for _ in ACTIVE_DOWNLOAD_STATUSES)
    with connect() as conn:
        cursor = conn.execute(
            f"""
            UPDATE download_jobs
            SET status='cancelled', phase='cancelled', last_error='用户取消全部下载', retry_after='', updated_at=?
            WHERE status IN ({active_placeholders}, 'failed', 'paused')
            """,
            (ts, *ACTIVE_DOWNLOAD_STATUSES),
        )
        changed_jobs = cursor.rowcount if cursor.rowcount is not None else 0
        conn.execute(
            """
            UPDATE episode_resources
            SET status='cancelled', updated_at=?
            WHERE downloaded=0
              AND status IN ('queued','downloading','remote_completed','failed','paused')
            """,
            (ts,),
        )
    log("warn", f"已取消全部下载任务: runtime={cancelled_runtime} active={cancelled_active} jobs={changed_jobs}")
    return {
        "status": "cancelled",
        "runtime_cancelled": cancelled_runtime,
        "active_cancelled": cancelled_active,
        "download_jobs": changed_jobs,
        "message": f"已取消全部下载任务: {changed_jobs} 条记录",
    }


async def _set_episode_download_state(episode_id: int, status: str, error: str, message: str) -> dict[str, Any]:
    with connect() as conn:
        episode = conn.execute("SELECT * FROM episodes WHERE id=?", (episode_id,)).fetchone()
        if not episode:
            raise HTTPException(status_code=404, detail="集数不存在")
        entry_id = int(episode["entry_id"])
        episode_number = int(episode["episode_number"])
    return await _set_episode_download_state_by_entry_episode(entry_id, episode_number, status, error, message)


async def _set_episode_download_state_by_entry_episode(
    entry_id: int,
    episode_number: int,
    status: str,
    error: str,
    message: str,
) -> dict[str, Any]:
    ts = now()
    active_placeholders = ",".join("?" for _ in ACTIVE_DOWNLOAD_STATUSES)
    with connect() as conn:
        conn.execute(
            f"""
            UPDATE download_jobs
            SET status=?, phase=?, retry_after='', last_error=?, updated_at=?
            WHERE entry_id=? AND episode_number=? AND status IN ({active_placeholders}, 'failed', 'paused')
            """,
            (status, status, error, ts, entry_id, episode_number, *ACTIVE_DOWNLOAD_STATUSES),
        )
        conn.execute(
            """
            UPDATE episode_resources
            SET status=?, updated_at=?
            WHERE entry_id=? AND episode_number=? AND selected=1 AND downloaded=0
            """,
            (status, ts, entry_id, episode_number),
        )
    cancelled = await runtime_store.cancel_episode_tasks(
        entry_id,
        episode_number,
        DOWNLOAD_RUNTIME_PROCESSORS,
    )
    active_cancelled = await cancel_active_processor_tasks(
        DOWNLOAD_RUNTIME_PROCESSORS,
        entry_id=entry_id,
        episode_number=episode_number,
    )
    return {"status": status, "runtime_cancelled": cancelled, "active_cancelled": active_cancelled, "message": message}


@router.post("/api/entries/{entry_id}/refresh-resources")
async def api_refresh_entry_resource_state(entry_id: int) -> dict[str, Any]:
    with connect() as conn:
        episodes = conn.execute("SELECT id FROM episodes WHERE entry_id=? ORDER BY episode_number", (entry_id,)).fetchall()
    count = 0
    for episode in episodes:
        result = await api_refresh_episode_resource_state(int(episode["id"]))
        count += int(result.get("count") or 0)
    return {"status": "refreshed", "count": count}


@router.post("/api/entries/{entry_id}/refresh-local-status")
async def api_refresh_entry_local_status(entry_id: int) -> dict[str, Any]:
    return refresh_local_status(entry_id)


@router.post("/api/entries/{entry_id}/match-local-files")
async def api_match_entry_local_files(entry_id: int, payload: LocalPathMatchPayload) -> dict[str, Any]:
    target = _validated_media_path(payload.path)
    return match_entry_local_files(entry_id, str(target))


@router.post("/api/entries/{entry_id}/organize-local-files")
async def api_organize_entry_local_files(entry_id: int) -> dict[str, Any]:
    return organize_local_files(entry_id=entry_id)


@router.post("/api/entries/{entry_id}/backfill-current-season")
async def api_backfill_entry_current_season(entry_id: int) -> dict[str, str]:
    with connect() as conn:
        entry = conn.execute("SELECT id, domain_kind FROM entries WHERE id=? AND COALESCE(hidden, 0)=0", (entry_id,)).fetchone()
        if not entry:
            raise HTTPException(status_code=404, detail="媒体条目不存在")
    run_id = start_pipeline(
        "seasonal_mikan_tracking",
        trigger_source="manual_backfill",
        first_step_key="season_backfill",
        subject_type="entry",
        subject_id=entry_id,
        payload={"entry_id": entry_id, "domain_kind": str(entry["domain_kind"] or "seasonal")},
        message=f"手动补全本季: entry_id={entry_id}",
    )
    if run_id <= 0:
        raise HTTPException(status_code=400, detail="补全流水线不可用")
    trigger_queue("processor", delay=0)
    return {"status": "started", "run_id": str(run_id), "message": "已加入补全本季任务"}
