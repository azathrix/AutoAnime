from __future__ import annotations

from ..database import connect
from ..db import get_settings, log
from ..pipeline_models import ProcessorContext, ProcessorResult
from ..sync_service import local_episode_path, normalize_local_target_path


async def process_sync_plan(context: ProcessorContext, payload: dict) -> ProcessorResult:
    entry_id = context.subject_id if context.subject_type == "entry" else int(payload.get("entry_id") or 0)
    if entry_id <= 0:
        return ProcessorResult.terminal("本地整理计划处理器缺少 entry_id")
    settings = get_settings()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT ca.*, e.display_title, e.title_cn, e.title_raw, e.season_number, e.year,
              e.bangumi_id, e.target_library_id
            FROM download_artifacts ca
            JOIN entries e ON e.id=ca.entry_id
            WHERE ca.entry_id=? AND ca.status='available'
            ORDER BY ca.episode_number ASC
            """,
            (entry_id,),
        ).fetchall()
    if not rows:
        log("info", f"本地整理计划等待: entry_id={entry_id} reason=暂无可整理下载产物")
        return ProcessorResult.skipped("已开启本地整理；下载完成后会自动整理", data={"entry_id": entry_id})

    next_tasks: list[dict] = []
    for row in rows:
        target = normalize_local_target_path(
            local_episode_path(dict(row), dict(row), settings),
            str(row["artifact_name"] or ""),
        )
        next_tasks.append(
            {
                "_subject_type": "download_artifact",
                "_subject_id": int(row["id"]),
                "download_artifact_id": int(row["id"]),
                "entry_id": entry_id,
                "target_path": target,
            }
        )
        log(
            "info",
            f"本地整理计划生成内存任务: entry_id={entry_id} download_artifact_id={row['id']} "
            f"episode={row['episode_number']} target={target}",
        )
    return ProcessorResult.success(
        f"已生成本地整理内存任务: {len(next_tasks)} 条",
        data={"entry_id": entry_id, "created": len(next_tasks)},
        next_tasks=next_tasks,
    )

