from __future__ import annotations

from ..database import connect
from ..pipeline_models import ProcessorContext, ProcessorResult


async def process_entry_merge(context: ProcessorContext, payload: dict) -> ProcessorResult:
    entry_id = context.subject_id if context.subject_type == "entry" else int(payload.get("entry_id") or 0)
    if entry_id <= 0:
        return ProcessorResult.terminal("整合处理器缺少 entry_id")
    with connect() as conn:
        row = conn.execute(
            "SELECT id, work_id, domain_kind, display_title, bangumi_id FROM entries WHERE id=?",
            (entry_id,),
        ).fetchone()
    if not row:
        return ProcessorResult.terminal(f"条目不存在: {entry_id}")
    return ProcessorResult.success(
        "条目整合边界已确认",
        data={"entry_id": entry_id, "work_id": int(row["work_id"] or 0), "bangumi_id": row["bangumi_id"] or ""},
        next_payload={
            "_subject_type": "entry",
            "_subject_id": entry_id,
            "entry_id": entry_id,
            "work_id": int(row["work_id"] or 0),
        },
    )

