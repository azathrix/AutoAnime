from __future__ import annotations

from ..database import connect
from ..pipeline_models import ProcessorContext, ProcessorResult


async def process_backfill(context: ProcessorContext, payload: dict) -> ProcessorResult:
    entry_id = context.subject_id if context.subject_type == "entry" else int(payload.get("entry_id") or 0)
    if entry_id <= 0:
        return ProcessorResult.terminal("整季补全处理器缺少 entry_id")
    with connect() as conn:
        row = conn.execute("SELECT id, mikan_bangumi_id FROM entries WHERE id=?", (entry_id,)).fetchone()
        if not row:
            return ProcessorResult.terminal(f"条目不存在: {entry_id}")
    return ProcessorResult.success(
        "整季补全边界已确认",
        data={"entry_id": entry_id, "mikan_bangumi_id": row["mikan_bangumi_id"] or ""},
        next_payload={
            "_subject_type": "entry",
            "_subject_id": entry_id,
            "entry_id": entry_id,
        },
    )
