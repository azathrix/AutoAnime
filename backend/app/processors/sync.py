from __future__ import annotations

from pathlib import Path

from ..database import connect
from ..db import get_settings, log, now
from ..pipeline_models import ProcessorContext, ProcessorResult
from ..sync_service import (
    download_cloud_file_to_local,
    local_episode_path,
    normalize_local_target_path,
    task_retry_after,
)


def _cloud_asset_id(context: ProcessorContext, payload: dict) -> int:
    if context.subject_type == "cloud_asset":
        return int(context.subject_id or 0)
    return int(payload.get("cloud_asset_id") or 0)


async def process_local_sync(context: ProcessorContext, payload: dict) -> ProcessorResult:
    cloud_asset_id = _cloud_asset_id(context, payload)
    if cloud_asset_id <= 0:
        return ProcessorResult.terminal("本地同步处理器缺少 cloud_asset_id")
    settings = get_settings()
    with connect() as conn:
        row = conn.execute(
            """
            SELECT ca.*, e.display_title, e.title_cn, e.title_raw, e.season_number, e.year,
              e.bangumi_id, e.target_library_id
            FROM cloud_assets ca
            JOIN entries e ON e.id=ca.entry_id
            WHERE ca.id=? AND ca.status='available'
            """,
            (cloud_asset_id,),
        ).fetchone()
    if not row:
        return ProcessorResult.terminal(f"云盘资源不存在或不可用: {cloud_asset_id}")

    target = str(payload.get("target_path") or "").strip()
    if not target:
        target = local_episode_path(dict(row), dict(row), settings)
    target = normalize_local_target_path(target, str(row["cloud_name"] or ""))
    target_file = Path(target)

    try:
        if target_file.exists() and target_file.stat().st_size > 0:
            log(
                "info",
                f"本地同步跳过下载: cloud_asset_id={cloud_asset_id} reason=本地文件已存在 target={target}",
            )
        else:
            log(
                "info",
                f"本地同步下载: cloud_asset_id={cloud_asset_id} entry_id={row['entry_id']} "
                f"episode={row['episode_number']} source={row['cloud_path']} target={target}",
            )
            await download_cloud_file_to_local(
                str(row["provider_file_id"] or ""),
                str(row["cloud_path"] or ""),
                target,
                settings,
            )
    except Exception as exc:
        return ProcessorResult.retryable(str(exc)[:2000], task_retry_after(settings, context.attempts + 1))

    with connect() as conn:
        ts = now()
        conn.execute(
            """
            INSERT INTO local_assets
              (cloud_asset_id, release_id, series_id, entry_id, episode_number, local_path,
               nfo_status, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'pending', 'synced', ?, ?)
            ON CONFLICT(cloud_asset_id) DO UPDATE SET
              release_id=excluded.release_id,
              series_id=excluded.series_id,
              entry_id=excluded.entry_id,
              episode_number=excluded.episode_number,
              local_path=excluded.local_path,
              status='synced',
              updated_at=excluded.updated_at
            """,
            (
                cloud_asset_id,
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
            "SELECT id FROM local_assets WHERE cloud_asset_id=?",
            (cloud_asset_id,),
        ).fetchone()
    local_asset_id = int(local_asset["id"] or 0) if local_asset else 0
    log(
        "info",
        f"本地同步完成: cloud_asset_id={cloud_asset_id} local_asset_id={local_asset_id} target={target}",
    )
    return ProcessorResult.success(
        "本地同步完成",
        data={
            "cloud_asset_id": cloud_asset_id,
            "local_asset_id": local_asset_id,
            "entry_id": int(row["entry_id"] or 0),
        },
        next_payload={
            "_subject_type": "local_asset",
            "_subject_id": local_asset_id,
            "cloud_asset_id": cloud_asset_id,
            "local_asset_id": local_asset_id,
            "entry_id": int(row["entry_id"] or 0),
        },
    )


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
