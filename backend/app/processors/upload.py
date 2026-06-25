from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from ..config import DATA_DIR
from ..database import connect
from ..db import get_settings, log, now, upsert_calendar_entry
from ..nfo_service import generate_jellyfin_nfo_for_entry
from ..pipeline_models import ProcessorContext, ProcessorResult
from ..sync_service import local_episode_path, normalize_local_target_path, task_retry_after


def _safe_upload_source(value: str) -> Path:
    root = (DATA_DIR / "uploads").resolve()
    source = Path(value or "").resolve()
    if root not in source.parents and source != root:
        raise ValueError("上传文件路径不在临时上传目录内")
    return source


async def process_upload(context: ProcessorContext, payload: dict) -> ProcessorResult:
    entry_id = int(payload.get("entry_id") or 0)
    episode_number = int(payload.get("episode_number") or 0)
    resource_id = int(payload.get("resource_id") or 0)
    temp_path = str(payload.get("temp_path") or "")
    original_name = str(payload.get("original_name") or Path(temp_path).name)
    if entry_id <= 0 or episode_number <= 0 or not temp_path:
        return ProcessorResult.terminal("上传处理器缺少 entry_id/episode_number/temp_path")

    try:
        source = _safe_upload_source(temp_path)
    except ValueError as exc:
        return ProcessorResult.terminal(str(exc))

    settings = get_settings()
    with connect() as conn:
        entry = conn.execute("SELECT * FROM entries WHERE id=?", (entry_id,)).fetchone()
        if not entry:
            return ProcessorResult.terminal(f"媒体条目不存在: {entry_id}")
        episode = conn.execute(
            "SELECT id FROM episodes WHERE entry_id=? AND episode_number=? ORDER BY id DESC LIMIT 1",
            (entry_id, episode_number),
        ).fetchone()
    episode_id = int(episode["id"] or 0) if episode else 0
    target = normalize_local_target_path(
        local_episode_path(
            {"artifact_name": "", "episode_number": episode_number},
            dict(entry),
            settings,
        ),
        original_name,
    )
    target_file = Path(target)

    try:
        target_file.parent.mkdir(parents=True, exist_ok=True)
        if source.exists() and not target_file.exists():
            await asyncio.to_thread(shutil.move, str(source), str(target_file))
        elif source.exists() and target_file.exists():
            await asyncio.to_thread(source.unlink)
        elif not target_file.exists():
            return ProcessorResult.retryable("上传临时文件不存在，等待后重试", task_retry_after(settings, context.attempts + 1))
    except Exception as exc:
        return ProcessorResult.retryable(str(exc)[:2000], task_retry_after(settings, context.attempts + 1))

    ts = now()
    local_asset_key = -abs(resource_id or (entry_id * 100000 + episode_number))
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO local_assets
              (download_artifact_id, release_id, series_id, entry_id, episode_number, local_path,
               nfo_status, status, created_at, updated_at)
            VALUES (?, 0, ?, ?, ?, ?, 'skipped', 'synced', ?, ?)
            ON CONFLICT(download_artifact_id) DO UPDATE SET
              series_id=excluded.series_id,
              entry_id=excluded.entry_id,
              episode_number=excluded.episode_number,
              local_path=excluded.local_path,
              status='synced',
              updated_at=excluded.updated_at
            """,
            (local_asset_key, entry_id, entry_id, episode_number, target, ts, ts),
        )
        conn.execute(
            """
            UPDATE episode_resources
            SET downloaded=1,
                local_path=?,
                status='downloaded',
                updated_at=?
            WHERE entry_id=? AND episode_number=? AND selected=1
            """,
            (target, ts, entry_id, episode_number),
        )
        upsert_calendar_entry(conn, entry_id, episode_number, ts, True)
    generate_jellyfin_nfo_for_entry(entry_id, settings)
    log("info", f"上传整理完成: entry_id={entry_id} episode={episode_number} target={target}")
    return ProcessorResult.success(
        "上传整理完成",
        data={"entry_id": entry_id, "episode_id": episode_id, "episode_number": episode_number, "local_path": target},
    )
