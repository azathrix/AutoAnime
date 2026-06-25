from __future__ import annotations

import asyncio
from pathlib import Path

from ..database import connect
from ..db import get_settings, log, now, upsert_calendar_entry
from ..downloader_service import settings_for_provider
from ..nfo_service import generate_jellyfin_nfo_for_entry
from ..pipeline_models import ProcessorContext, ProcessorResult
from ..runtime_store import runtime_store
from ..sync_service import (
    download_remote_file_to_local,
    local_episode_path,
    normalize_local_target_path,
    task_retry_after,
)


def _download_artifact_id(context: ProcessorContext, payload: dict) -> int:
    if context.subject_type == "download_artifact":
        return int(context.subject_id or 0)
    return int(payload.get("download_artifact_id") or 0)


def _local_copy_size(target_file: Path) -> int:
    try:
        if target_file.exists() and target_file.is_file():
            return max(0, target_file.stat().st_size)
    except OSError:
        return 0
    parent = target_file.parent
    name = target_file.name
    if not name:
        return 0
    try:
        candidates = list(parent.iterdir()) if parent.exists() else []
    except OSError:
        return 0
    sizes: list[int] = []
    for item in candidates:
        try:
            if not item.is_file():
                continue
            item_name = item.name
            if item_name == name:
                continue
            if item_name.startswith(name) or name in item_name:
                sizes.append(max(0, item.stat().st_size))
        except OSError:
            continue
    return max(sizes) if sizes else 0


def _partial_copy_path(target_file: Path) -> Path:
    return target_file.with_name(f"{target_file.name}.anitrack.part")


async def sync_download_artifact_to_local(
    context: ProcessorContext,
    payload: dict,
    download_artifact_id: int,
) -> ProcessorResult:
    if download_artifact_id <= 0:
        return ProcessorResult.terminal("本地整理处理器缺少 download_artifact_id")
    base_settings = get_settings()
    with connect() as conn:
        row = conn.execute(
            """
            SELECT ca.*, e.display_title, e.title_cn, e.title_raw, e.season_number, e.year,
              e.bangumi_id, e.target_library_id
            FROM download_artifacts ca
            JOIN entries e ON e.id=ca.entry_id
            WHERE ca.id=? AND ca.status='available'
            """,
            (download_artifact_id,),
        ).fetchone()
    if not row:
        return ProcessorResult.terminal(f"下载产物不存在或不可用: {download_artifact_id}")
    settings = settings_for_provider(base_settings, str(row["provider"] or ""))

    target = str(payload.get("target_path") or "").strip()
    if not target:
        target = local_episode_path(dict(row), dict(row), settings)
    target = normalize_local_target_path(target, str(row["artifact_name"] or ""))
    target_file = Path(target)
    partial_file = _partial_copy_path(target_file)
    with connect() as conn:
        size_row = conn.execute(
            """
            SELECT total_size
            FROM download_jobs
            WHERE entry_id=? AND episode_number=? AND provider=?
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            """,
            (int(row["entry_id"] or 0), int(row["episode_number"] or 0), str(row["provider"] or "")),
        ).fetchone()
    total_size = int(size_row["total_size"] or 0) if size_row else 0

    try:
        final_size = target_file.stat().st_size if target_file.exists() else 0
        if target_file.exists() and final_size > 0 and (total_size <= 0 or final_size >= total_size):
            log(
                "info",
                f"本地整理跳过下载: download_artifact_id={download_artifact_id} reason=本地文件已存在 target={target}",
            )
        else:
            if target_file.exists() and final_size > 0 and total_size > 0 and final_size < total_size:
                log(
                    "warn",
                    f"检测到疑似未完成最终文件，将重新下载到临时文件: target={target} "
                    f"current_size={final_size} total_size={total_size}",
                )
            log(
                "info",
                f"本地整理下载: download_artifact_id={download_artifact_id} entry_id={row['entry_id']} "
                f"episode={row['episode_number']} provider={row['provider'] or '-'} "
                f"file_id={row['provider_file_id'] or '-'} source={row['remote_path']} "
                f"target={target} temp={partial_file} total_size={total_size}",
            )

            async def progress_cb(percent: int, text: str) -> None:
                downloaded_size = _local_copy_size(partial_file)
                calculated = int(downloaded_size * 100 / total_size) if total_size > 0 and downloaded_size > 0 else 0
                value = max(0, min(100, max(int(percent or 0), calculated)))
                if total_size > 0 and downloaded_size > 0:
                    message = text or f"复制到本地 {value}%"
                elif downloaded_size > 0:
                    message = text or f"复制到本地，已写入 {downloaded_size} 字节"
                else:
                    message = "正在复制到本地"
                ts = now()
                with connect() as progress_conn:
                    progress_conn.execute(
                        """
                        UPDATE download_jobs
                        SET status='local_copying',
                            phase='local_copying',
                            progress=?,
                            progress_text=?,
                            total_size=CASE WHEN ? > 0 THEN ? ELSE total_size END,
                            downloaded_size=?,
                            updated_at=?,
                            last_seen_at=?
                        WHERE entry_id=? AND episode_number=? AND provider=?
                        """,
                        (
                            value,
                            message[:500],
                            total_size,
                            total_size,
                            downloaded_size,
                            ts,
                            ts,
                            int(row["entry_id"] or 0),
                            int(row["episode_number"] or 0),
                            str(row["provider"] or ""),
                        ),
                    )
                    progress_conn.execute(
                        """
                        UPDATE episode_resources
                        SET status='downloading',
                            updated_at=?
                        WHERE entry_id=? AND episode_number=? AND selected=1 AND downloaded=0
                        """,
                        (
                            ts,
                            int(row["entry_id"] or 0),
                            int(row["episode_number"] or 0),
                        ),
                    )
                await runtime_store.update_task_progress(context.task_id, value, message)

            await progress_cb(0, "正在复制到本地")
            stop_monitor = asyncio.Event()

            async def monitor_local_size() -> None:
                while not stop_monitor.is_set():
                    if total_size > 0:
                        await progress_cb(0, "")
                    try:
                        await asyncio.wait_for(stop_monitor.wait(), timeout=1)
                    except asyncio.TimeoutError:
                        pass

            monitor_task = asyncio.create_task(monitor_local_size())
            try:
                await download_remote_file_to_local(
                    str(row["provider_file_id"] or ""),
                    str(row["remote_path"] or ""),
                    str(partial_file),
                    settings,
                    progress_cb=progress_cb,
                )
            finally:
                stop_monitor.set()
                await monitor_task
            if not partial_file.exists() or partial_file.stat().st_size <= 0:
                raise RuntimeError(f"临时下载文件不存在或为空: {partial_file}")
            target_file.parent.mkdir(parents=True, exist_ok=True)
            partial_file.replace(target_file)
    except Exception as exc:
        log(
            "error",
            f"本地整理失败: download_artifact_id={download_artifact_id} entry_id={row['entry_id']} "
            f"episode={row['episode_number']} source={row['remote_path']} target={target} error={str(exc)[:1500]}",
        )
        return ProcessorResult.retryable(str(exc)[:2000], task_retry_after(settings, context.attempts + 1))

    with connect() as conn:
        ts = now()
        conn.execute(
            """
            INSERT INTO local_assets
              (download_artifact_id, release_id, series_id, entry_id, episode_number, local_path,
               nfo_status, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'pending', 'synced', ?, ?)
            ON CONFLICT(download_artifact_id) DO UPDATE SET
              release_id=excluded.release_id,
              series_id=excluded.series_id,
              entry_id=excluded.entry_id,
              episode_number=excluded.episode_number,
              local_path=excluded.local_path,
              status='synced',
              updated_at=excluded.updated_at
            """,
            (
                download_artifact_id,
                int(row["release_id"] or 0),
                int(row["series_id"] or 0),
                int(row["entry_id"] or 0),
                int(row["episode_number"] or 0),
                target,
                ts,
                ts,
            ),
        )
        local_asset = conn.execute(
            "SELECT id FROM local_assets WHERE download_artifact_id=?",
            (download_artifact_id,),
        ).fetchone()
        conn.execute(
            """
            UPDATE episode_resources
            SET downloaded=1,
                local_path=?,
                status='downloaded',
                updated_at=?
            WHERE entry_id=? AND episode_number=?
            """,
            (target, ts, int(row["entry_id"] or 0), int(row["episode_number"] or 0)),
        )
        conn.execute(
            """
            UPDATE episodes
            SET local_path=?,
                watchable=1,
                status='downloaded',
                updated_at=?
            WHERE entry_id=? AND episode_number=?
            """,
            (target, ts, int(row["entry_id"] or 0), int(row["episode_number"] or 0)),
        )
        upsert_calendar_entry(
            conn,
            int(row["entry_id"] or 0),
            int(row["episode_number"] or 0),
            ts,
            True,
        )
        conn.execute(
            """
            UPDATE download_jobs
            SET status='completed',
                phase='completed',
                progress=100,
                progress_text='本地下载完成',
                target_local_path=?,
                downloaded_size=CASE WHEN total_size > 0 THEN total_size ELSE downloaded_size END,
                updated_at=?,
                last_seen_at=?
            WHERE entry_id=? AND episode_number=? AND provider=?
            """,
            (
                target,
                ts,
                ts,
                int(row["entry_id"] or 0),
                int(row["episode_number"] or 0),
                str(row["provider"] or ""),
            ),
        )
    local_asset_id = int(local_asset["id"] or 0) if local_asset else 0
    generate_jellyfin_nfo_for_entry(int(row["entry_id"] or 0), base_settings)
    final_size = 0
    try:
        final_size = target_file.stat().st_size if target_file.exists() else 0
    except OSError:
        final_size = 0
    log(
        "info",
        f"本地整理完成: download_artifact_id={download_artifact_id} local_asset_id={local_asset_id} "
        f"target={target} size={final_size}",
    )
    return ProcessorResult.success(
        "本地整理完成",
        data={
            "download_artifact_id": download_artifact_id,
            "local_asset_id": local_asset_id,
            "entry_id": int(row["entry_id"] or 0),
        },
    )


async def process_local_sync(context: ProcessorContext, payload: dict) -> ProcessorResult:
    return await sync_download_artifact_to_local(context, payload, _download_artifact_id(context, payload))


async def process_local_presence(context: ProcessorContext, payload: dict) -> ProcessorResult:
    local_asset_id = context.subject_id if context.subject_type == "local_asset" else int(payload.get("local_asset_id") or 0)
    entry_id = int(payload.get("entry_id") or 0)
    if local_asset_id <= 0 and entry_id <= 0:
        return ProcessorResult.terminal("本地存在性处理器缺少 local_asset_id 或 entry_id")
    with connect() as conn:
        if local_asset_id > 0:
            row = conn.execute("SELECT id, local_path FROM local_assets WHERE id=?", (local_asset_id,)).fetchone()
            rows = [row] if row else []
        else:
            rows = conn.execute("SELECT id, local_path FROM local_assets WHERE entry_id=? AND status='synced'", (entry_id,)).fetchall()
        missing: list[int] = []
        for row in rows:
            if not Path(str(row["local_path"] or "")).exists():
                missing.append(int(row["id"]))
        if missing:
            ts = now()
            for asset_id in missing:
                conn.execute("UPDATE local_assets SET status='removed', updated_at=? WHERE id=?", (ts, asset_id))
    if missing:
        return ProcessorResult.success("本地存在性检查完成，部分文件已标记移除", data={"missing": missing})
    return ProcessorResult.success("本地存在性检查完成", data={"entry_id": entry_id, "local_asset_id": local_asset_id})

