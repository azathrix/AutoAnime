from __future__ import annotations

from ..db import get_settings
from ..metadata import generate_nfo_for_entry
from ..pipeline_models import ProcessorContext, ProcessorResult


async def process_nfo(context: ProcessorContext, payload: dict) -> ProcessorResult:
    entry_id = context.subject_id if context.subject_type == "entry" else int(payload.get("entry_id") or 0)
    if entry_id <= 0:
        return ProcessorResult.terminal("NFO 处理器缺少 entry_id")
    settings = get_settings()
    nfo_settings = dict(settings)
    if not nfo_settings.get("nfo_output_root"):
        nfo_settings["nfo_output_root"] = settings.get("local_library_root") or "/media/pikpak-anime"
    generate_nfo_for_entry(entry_id, nfo_settings)
    return ProcessorResult.success("NFO 生成完成", data={"entry_id": entry_id})
