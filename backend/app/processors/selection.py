from __future__ import annotations

from ..database import connect
from ..db import get_settings, now
from ..pipeline_models import ProcessorContext, ProcessorResult
from ..scanner import mark_selected_releases, queue_release, resolve_entry_choice, task_retry_after


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

    with connect() as conn:
        ts = now()
        selection_row = conn.execute("SELECT id FROM selection_tasks WHERE entry_id=?", (entry_id,)).fetchone()
        if selection_row:
            if choice.get("reason"):
                conn.execute(
                    """
                    UPDATE selection_tasks
                    SET status='failed', reason=?, retry_after='', last_error='', updated_at=?
                    WHERE entry_id=?
                    """,
                    (str(choice["reason"])[:500], ts, entry_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE selection_tasks
                    SET status='completed', reason='', retry_after='', last_error='', updated_at=?
                    WHERE entry_id=?
                    """,
                    (ts, entry_id),
                )

    if choice.get("reason"):
        return ProcessorResult.terminal(str(choice["reason"]))

    next_tasks = []
    for release_id in release_ids:
        queue_release(release_id, settings)
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
