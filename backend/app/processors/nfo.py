from __future__ import annotations

from ..database import connect
from ..db import get_settings, now
from ..metadata import generate_nfo_for_entry
from ..pipeline_models import ProcessorContext, ProcessorResult


async def process_nfo(context: ProcessorContext, payload: dict) -> ProcessorResult:
    local_asset_id = context.subject_id if context.subject_type == "local_asset" else int(payload.get("local_asset_id") or 0)
    entry_id = context.subject_id if context.subject_type == "entry" else int(payload.get("entry_id") or 0)
    if entry_id <= 0 and local_asset_id > 0:
        with connect() as conn:
            row = conn.execute("SELECT entry_id FROM local_assets WHERE id=?", (local_asset_id,)).fetchone()
        entry_id = int(row["entry_id"] or 0) if row else 0
    if entry_id <= 0:
        return ProcessorResult.terminal("NFO 处理器缺少 entry_id")
    settings = get_settings()
    nfo_settings = dict(settings)
    if not nfo_settings.get("nfo_output_root"):
        nfo_settings["nfo_output_root"] = settings.get("local_library_root") or "/media/autoanime"
    generate_nfo_for_entry(entry_id, nfo_settings)
    if local_asset_id > 0:
        with connect() as conn:
            conn.execute(
                "UPDATE local_assets SET nfo_status='generated', updated_at=? WHERE id=?",
                (now(), local_asset_id),
            )
    return ProcessorResult.success("NFO 生成完成", data={"entry_id": entry_id, "local_asset_id": local_asset_id})

