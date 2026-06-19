from __future__ import annotations

from ..database import connect
from ..db import get_settings, log
from ..pipeline_models import ProcessorContext, ProcessorResult
from ..sync_service import ensure_sync_rule, local_episode_path, normalize_local_target_path


async def process_sync_plan(context: ProcessorContext, payload: dict) -> ProcessorResult:
    entry_id = context.subject_id if context.subject_type == "entry" else int(payload.get("entry_id") or 0)
    if entry_id <= 0:
        return ProcessorResult.terminal("同步计划处理器缺少 entry_id")
    settings = get_settings()
    ensure_sync_rule(entry_id, settings)
    with connect() as conn:
        rule = conn.execute("SELECT sync_enabled FROM sync_rules WHERE entry_id=?", (entry_id,)).fetchone()
        if not rule or not int(rule["sync_enabled"] or 0):
            log("info", f"同步计划跳过: entry_id={entry_id} reason=本地同步未开启")
            return ProcessorResult.skipped("本地同步未开启，跳过同步计划", data={"entry_id": entry_id})
        rows = conn.execute(
            """
            SELECT ca.*, e.display_title, e.title_cn, e.title_raw, e.season_number, e.year,
              e.bangumi_id, e.target_library_id
            FROM cloud_assets ca
            JOIN entries e ON e.id=ca.entry_id
            WHERE ca.entry_id=? AND ca.status='available'
            ORDER BY ca.episode_number ASC
            """,
            (entry_id,),
        ).fetchall()
    if not rows:
        log("info", f"同步计划等待: entry_id={entry_id} reason=暂无可同步云盘资源")
        return ProcessorResult.skipped("已开启本地同步；云盘资源入库后会自动同步", data={"entry_id": entry_id})

    next_tasks: list[dict] = []
    for row in rows:
        target = normalize_local_target_path(
            local_episode_path(dict(row), dict(row), settings),
            str(row["cloud_name"] or ""),
        )
        next_tasks.append(
            {
                "_subject_type": "cloud_asset",
                "_subject_id": int(row["id"]),
                "cloud_asset_id": int(row["id"]),
                "entry_id": entry_id,
                "target_path": target,
            }
        )
        log(
            "info",
            f"同步计划生成内存任务: entry_id={entry_id} cloud_asset_id={row['id']} "
            f"episode={row['episode_number']} target={target}",
        )
    return ProcessorResult.success(
        f"已生成本地同步内存任务: {len(next_tasks)} 条",
        data={"entry_id": entry_id, "created": len(next_tasks)},
        next_tasks=next_tasks,
    )
