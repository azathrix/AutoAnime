from __future__ import annotations

from ..db import get_settings
from ..pipeline_models import ProcessorContext, ProcessorResult
from ..sync_service import materialize_sync_tasks_for_entry


async def process_sync_plan(context: ProcessorContext, payload: dict) -> ProcessorResult:
    entry_id = context.subject_id if context.subject_type == "entry" else int(payload.get("entry_id") or 0)
    if entry_id <= 0:
        return ProcessorResult.terminal("同步计划处理器缺少 entry_id")
    created, message = materialize_sync_tasks_for_entry(entry_id, get_settings())
    return ProcessorResult.success(message or "同步计划已生成", data={"entry_id": entry_id, "created": created})
