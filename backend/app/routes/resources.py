from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from ..database import connect
from ..db import log, now
from ..media_service import build_entry_response, reset_orphaned_download_jobs_in_conn
from ..pipeline_orchestrator import start_pipeline
from ..runtime_service import DOWNLOAD_RUNTIME_PROCESSORS, trigger_queue
from ..runtime_store import runtime_store
from ..schemas import (
    BatchSubtitlePayload,
    EpisodeDownloadActionPayload,
    EpisodeImportPayload,
    EpisodeResourcePayload,
    EpisodeSubtitlePayload,
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


@router.post("/api/entries/{entry_id}/resources/import")
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
    invalid_episode_resources = [line for line in resource_lines if parsed_episode_required(line) <= 0]
    if invalid_episode_resources:
        raise HTTPException(status_code=400, detail=f"资源无法识别集数: {invalid_episode_resources[0]}")
    invalid_episode_subtitles = [line for line in subtitle_lines if parsed_episode_required(line) <= 0]
    if invalid_episode_subtitles:
        raise HTTPException(status_code=400, detail=f"字幕无法识别集数: {invalid_episode_subtitles[0]}")
    ts = now()
    created = 0
    with connect() as conn:
        entry = conn.execute("SELECT * FROM entries WHERE id=?", (entry_id,)).fetchone()
        if not entry:
            raise HTTPException(status_code=404, detail="媒体条目不存在")
        for index, line in enumerate(resource_lines, start=1):
            episode_number = parsed_episode_required(line)
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
            episode_number = parsed_episode_required(line)
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
                    payload.title.strip(), payload.title.strip(),
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
            conn.execute(
                """
                INSERT INTO episode_resources
                  (entry_id, episode_id, episode_number, source_type, source_ref, title,
                   subtitle_group, resolution, language, subtitle_format, selected, created_at, updated_at)
                VALUES (?, ?, ?, 'manual', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    episode["entry_id"], episode_id, episode["episode_number"], f"manual:{episode_id}:{ts}",
                    payload.title.strip(), payload.subtitle_group.strip(), payload.resolution.strip(),
                    payload.language.strip(), payload.subtitle_format.strip(), int(payload.selected), ts, ts,
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
        active = conn.execute(
            """
            SELECT 1 FROM download_jobs
            WHERE entry_id=? AND episode_number=?
              AND status IN ('pending','running','submitted','downloading')
            LIMIT 1
            """,
            (entry_id, episode_number),
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


@router.post("/api/episodes/{episode_id}/refresh")
async def api_refresh_episode_resource_state(episode_id: int) -> dict[str, Any]:
    refreshed = 0
    with connect() as conn:
        episode = conn.execute("SELECT * FROM episodes WHERE id=?", (episode_id,)).fetchone()
        if not episode:
            raise HTTPException(status_code=404, detail="集数不存在")
        reset_orphaned_download_jobs_in_conn(conn, int(episode["entry_id"]), int(episode["episode_number"]))
        resources = conn.execute(
            "SELECT * FROM episode_resources WHERE episode_id=? OR (entry_id=? AND episode_number=?)",
            (episode_id, episode["entry_id"], episode["episode_number"]),
        ).fetchall()
        for resource in resources:
            local_path = str(resource["local_path"] or "")
            exists = bool(local_path and Path(local_path).exists())
            conn.execute("UPDATE episode_resources SET downloaded=?, updated_at=? WHERE id=?", (1 if exists else int(resource["downloaded"] or 0), now(), resource["id"]))
            refreshed += 1
    return {"status": "refreshed", "count": refreshed}


@router.post("/api/episodes/{episode_id}/download")
async def api_download_episode_resource(episode_id: int) -> dict[str, Any]:
    with connect() as conn:
        episode = conn.execute("SELECT * FROM episodes WHERE id=?", (episode_id,)).fetchone()
        if not episode:
            raise HTTPException(status_code=404, detail="集数不存在")
        reset_orphaned_download_jobs_in_conn(conn, int(episode["entry_id"]), int(episode["episode_number"]))
        selected = conn.execute(
            """
            SELECT * FROM episode_resources
            WHERE episode_id=? OR (entry_id=? AND episode_number=?)
            ORDER BY selected DESC, id DESC
            LIMIT 1
            """,
            (episode_id, episode["entry_id"], episode["episode_number"]),
        ).fetchone()
        if not selected or int(selected["release_id"] or 0) <= 0:
            raise HTTPException(status_code=400, detail="该集没有可下载资源")
        ts = now()
        conn.execute("UPDATE episode_resources SET status='queued', updated_at=? WHERE entry_id=? AND episode_number=? AND selected=1", (ts, episode["entry_id"], episode["episode_number"]))
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
    log(
        "info",
        f"单集下载请求: entry_id={int(episode['entry_id'])} episode={int(episode['episode_number'])} "
        f"release_id={int(selected['release_id'])} run_id={run_id}",
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
        candidates = conn.execute(
            """
            SELECT er.entry_id, er.episode_number, er.release_id
            FROM episode_resources er
            LEFT JOIN local_assets la ON la.entry_id=er.entry_id AND la.episode_number=er.episode_number AND la.status='synced'
            WHERE er.entry_id=? AND er.selected=1 AND er.release_id > 0
              AND COALESCE(er.downloaded, 0)=0 AND la.id IS NULL
              AND NOT EXISTS (
                SELECT 1 FROM download_jobs dj
                WHERE dj.entry_id=er.entry_id AND dj.episode_number=er.episode_number
                  AND dj.status IN ('pending','running','submitted','downloading')
              )
            ORDER BY er.episode_number ASC, er.id DESC
            """,
            (entry_id,),
        ).fetchall()
        ts = now()
        seen_episodes: set[int] = set()
        for candidate in candidates:
            episode_number = int(candidate["episode_number"] or 0)
            release_id = int(candidate["release_id"] or 0)
            if episode_number <= 0 or release_id <= 0 or episode_number in seen_episodes:
                continue
            seen_episodes.add(episode_number)
            if runtime_store.has_active_episode_task(entry_id, episode_number, DOWNLOAD_RUNTIME_PROCESSORS):
                continue
            conn.execute("UPDATE episode_resources SET status='queued', updated_at=? WHERE entry_id=? AND episode_number=? AND selected=1", (ts, entry_id, episode_number))
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


@router.post("/api/episodes/{episode_id}/download/pause")
async def api_pause_episode_download(episode_id: int) -> dict[str, Any]:
    return await _set_episode_download_state(episode_id, "paused", "用户暂停下载", "已暂停该集本地下载流程")


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
    with connect() as conn:
        conn.execute(
            """
            UPDATE download_jobs
            SET status=?, retry_after='', last_error=?, updated_at=?
            WHERE entry_id=? AND episode_number=? AND status IN ('pending','running','submitted','downloading','failed','paused')
            """,
            (status, error, ts, entry_id, episode_number),
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
        {"download"},
    )
    return {"status": status, "runtime_cancelled": cancelled, "message": message}


@router.post("/api/entries/{entry_id}/refresh-resources")
async def api_refresh_entry_resource_state(entry_id: int) -> dict[str, Any]:
    with connect() as conn:
        episodes = conn.execute("SELECT id FROM episodes WHERE entry_id=? ORDER BY episode_number", (entry_id,)).fetchall()
    count = 0
    for episode in episodes:
        result = await api_refresh_episode_resource_state(int(episode["id"]))
        count += int(result.get("count") or 0)
    return {"status": "refreshed", "count": count}
