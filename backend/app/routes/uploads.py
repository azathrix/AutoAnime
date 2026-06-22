from __future__ import annotations

import shutil
import uuid
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool

from ..database import connect
from ..db import log, now
from ..media_service import build_entry_response
from ..pipeline_orchestrator import start_pipeline
from ..runtime_service import trigger_queue
from ..schemas import LocalUploadImportPayload
from ..utils import (
    is_valid_subtitle_reference,
    parsed_episode_or_fallback,
    safe_upload_filename,
    subtitle_embedded_value,
    upload_root,
    validate_upload_temp_path,
)


router = APIRouter()


async def save_upload_file(file: UploadFile, subdir: str = "") -> dict[str, Any]:
    original_name = safe_upload_filename(file.filename or "upload.bin")
    token = uuid.uuid4().hex
    root = upload_root() / subdir if subdir else upload_root()
    root.mkdir(parents=True, exist_ok=True)
    target = root / f"{token}_{original_name}"

    def write_file() -> int:
        with target.open("wb") as handle:
            shutil.copyfileobj(file.file, handle)
        return target.stat().st_size

    try:
        size = await run_in_threadpool(write_file)
    finally:
        await file.close()
    log("info", f"本地文件已上传到临时区: name={original_name} size={size}")
    return {
        "status": "uploaded",
        "token": token,
        "temp_path": str(target),
        "file_name": original_name,
        "size": size,
    }


@router.post("/api/uploads/local")
async def api_upload_local_file(file: UploadFile = File(...)) -> dict[str, Any]:
    return await save_upload_file(file)


@router.post("/api/subtitles/upload")
async def api_upload_subtitle_file(file: UploadFile = File(...)) -> dict[str, Any]:
    original_name = safe_upload_filename(file.filename or "subtitle.ass")
    if not is_valid_subtitle_reference(original_name):
        await file.close()
        raise HTTPException(status_code=400, detail="字幕文件格式无效")
    result = await save_upload_file(file, "subtitles")
    log("info", f"字幕文件已上传: name={result['file_name']} size={result['size']}")
    return result


@router.post("/api/entries/{entry_id}/subtitles/uploads/import")
async def api_import_entry_subtitle_uploads(entry_id: int, payload: LocalUploadImportPayload) -> dict[str, Any]:
    uploads = [item for item in payload.uploads if item.temp_path.strip()]
    if not uploads:
        raise HTTPException(status_code=400, detail="请先上传字幕文件")
    ts = now()
    saved = 0
    with connect() as conn:
        entry = conn.execute("SELECT id FROM entries WHERE id=?", (entry_id,)).fetchone()
        if not entry:
            raise HTTPException(status_code=404, detail="媒体条目不存在")
        for index, item in enumerate(uploads, start=1):
            source = validate_upload_temp_path(item.temp_path)
            file_name = safe_upload_filename(item.file_name or source.name)
            if not is_valid_subtitle_reference(file_name):
                continue
            episode_number = parsed_episode_or_fallback(file_name, index)
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
                  (episode_id, entry_id, episode_number, language, subtitle_format, subtitle_path,
                   file_name, embedded, selected, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    int(episode["id"]),
                    entry_id,
                    episode_number,
                    payload.language.strip(),
                    payload.subtitle_format.strip() or "external",
                    str(source),
                    file_name,
                    subtitle_embedded_value(payload.subtitle_format),
                    ts,
                    ts,
                ),
            )
            saved += 1
    log("info", f"批量导入上传字幕: entry_id={entry_id} count={saved}")
    return {"status": "saved", "count": saved, "detail": build_entry_response(entry_id)}


@router.post("/api/entries/{entry_id}/uploads/import")
async def api_import_entry_uploads(entry_id: int, payload: LocalUploadImportPayload) -> dict[str, Any]:
    uploads = [item for item in payload.uploads if item.temp_path.strip()]
    if not uploads:
        raise HTTPException(status_code=400, detail="请先选择并上传本地文件")
    ts = now()
    rows: list[dict[str, Any]] = []
    with connect() as conn:
        entry = conn.execute("SELECT * FROM entries WHERE id=?", (entry_id,)).fetchone()
        if not entry:
            raise HTTPException(status_code=404, detail="媒体条目不存在")
        for index, item in enumerate(uploads, start=1):
            source = validate_upload_temp_path(item.temp_path)
            file_name = safe_upload_filename(item.file_name or source.name)
            episode_number = parsed_episode_or_fallback(file_name, index)
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
            conn.execute(
                "UPDATE episode_resources SET selected=0 WHERE entry_id=? AND episode_number=?",
                (entry_id, episode_number),
            )
            conn.execute(
                """
                INSERT INTO episode_resources
                  (entry_id, episode_id, episode_number, source_type, source_ref, release_id, title,
                   language, subtitle_format, selected, status, created_at, updated_at)
                VALUES (?, ?, ?, 'upload', ?, 0, ?, ?, ?, 1, 'queued', ?, ?)
                ON CONFLICT(entry_id, episode_number, source_type, source_ref) DO UPDATE SET
                  episode_id=excluded.episode_id,
                  title=excluded.title,
                  language=excluded.language,
                  subtitle_format=excluded.subtitle_format,
                  selected=1,
                  status='queued',
                  updated_at=excluded.updated_at
                """,
                (
                    entry_id,
                    episode_id,
                    episode_number,
                    str(source),
                    file_name,
                    payload.language.strip(),
                    payload.subtitle_format.strip(),
                    ts,
                    ts,
                ),
            )
            resource = conn.execute(
                """
                SELECT id FROM episode_resources
                WHERE entry_id=? AND episode_number=? AND source_type='upload' AND source_ref=?
                """,
                (entry_id, episode_number, str(source)),
            ).fetchone()
            rows.append(
                {
                    "episode_id": episode_id,
                    "episode_number": episode_number,
                    "resource_id": int(resource["id"] or 0) if resource else 0,
                    "temp_path": str(source),
                    "file_name": file_name,
                }
            )

    run_ids: list[int] = []
    for row in rows:
        run_id = start_pipeline(
            "media_upload",
            trigger_source="local_upload",
            first_step_key="upload",
            subject_type="episode",
            subject_id=int(row["episode_id"]),
            payload={
                "_dedupe_key": f"upload:entry:{entry_id}:episode:{int(row['episode_number'])}",
                "entry_id": entry_id,
                "episode_id": int(row["episode_id"]),
                "episode_number": int(row["episode_number"]),
                "resource_id": int(row["resource_id"]),
                "temp_path": row["temp_path"],
                "original_name": row["file_name"],
                "domain_kind": "library",
            },
            message=f"上传整理: entry_id={entry_id} episode={row['episode_number']}",
        )
        run_ids.append(run_id)
    if run_ids:
        trigger_queue("processor", delay=0)
    log("info", f"本地上传资源已入队: entry_id={entry_id} count={len(run_ids)}")
    return {"status": "started", "count": len(run_ids), "upload_run_ids": run_ids, "detail": build_entry_response(entry_id)}
