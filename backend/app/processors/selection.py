from __future__ import annotations

from ..db import get_settings
from ..pipeline_models import ProcessorContext, ProcessorResult
from ..scanner import mark_selected_releases, resolve_entry_choice, task_retry_after


async def process_selection(context: ProcessorContext, payload: dict) -> ProcessorResult:
    entry_id = context.subject_id if context.subject_type == "entry" else int(payload.get("entry_id") or 0)
    if entry_id <= 0:
        return ProcessorResult.terminal("自动选集处理器缺少 entry_id")
    settings = get_settings()
    try:
        release_ids, choice = resolve_entry_choice(entry_id, settings)
        mark_selected_releases(entry_id, release_ids)
    except Exception as exc:
        return ProcessorResult.retryable(str(exc)[:2000], task_retry_after(settings, context.attempts + 1))

    if choice.get("reason"):
        return ProcessorResult.terminal(str(choice["reason"]))

    next_tasks = []
    for release_id in release_ids:
        next_tasks.append(
            {
                "_subject_type": "release",
                "_subject_id": release_id,
                "_dedupe_key": f"cloud-presence:release:{release_id}",
                "entry_id": entry_id,
                "release_id": release_id,
            }
        )
    return ProcessorResult.success(
        f"自动选集完成: {len(release_ids)} 个发布",
        data={"entry_id": entry_id, "release_ids": release_ids},
        next_tasks=next_tasks,
    )
