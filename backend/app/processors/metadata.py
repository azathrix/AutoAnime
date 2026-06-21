from __future__ import annotations

from ..database import connect
from ..db import get_settings, log, now
from ..metadata import fetch_bangumi_metadata, refresh_entry_metadata
from ..pipeline_models import ProcessorContext, ProcessorResult
from ..scanner import candidate_to_parsed_release, task_retry_after, upsert_release


async def process_candidate_metadata(context: ProcessorContext, payload: dict) -> ProcessorResult:
    candidate_id = context.subject_id if context.subject_type == "rss_candidate" else int(payload.get("candidate_id") or 0)
    if candidate_id <= 0:
        return ProcessorResult.terminal("候选元数据处理缺少 candidate_id")
    settings = get_settings()
    with connect() as conn:
        row = conn.execute("SELECT * FROM rss_candidates WHERE id=?", (candidate_id,)).fetchone()
    if not row:
        return ProcessorResult.terminal(f"RSS 候选不存在: {candidate_id}")
    bangumi_id = str(row["bangumi_id"] or payload.get("bangumi_id") or "")
    if not bangumi_id:
        return ProcessorResult.retryable("缺少 Bangumi ID", task_retry_after(settings, context.attempts + 1))

    try:
        log("info", f"元数据刷新开始: candidate_id={candidate_id} bangumi_id={bangumi_id} title={row['title']}")
        metadata = await fetch_bangumi_metadata(bangumi_id, settings.get("rss_proxy", ""))
        release = candidate_to_parsed_release(row)
        series_id, entry_id, release_id = upsert_release(release, metadata)
        log(
            "info",
            f"元数据刷新完成: candidate_id={candidate_id} bangumi_id={bangumi_id} "
            f"entry_id={entry_id} release_id={release_id} title={metadata.get('title_cn') or row['series_title']}",
        )
    except Exception as exc:
        error = str(exc)[:2000]
        with connect() as conn:
            conn.execute(
                "UPDATE rss_candidates SET status='failed', reason=?, updated_at=? WHERE id=?",
                (error, now(), candidate_id),
            )
        return ProcessorResult.retryable(error, task_retry_after(settings, context.attempts + 1))

    with connect() as conn:
        ts = now()
        conn.execute(
            "UPDATE rss_candidates SET status='completed', reason='', updated_at=? WHERE id=?",
            (ts, candidate_id),
        )

    return ProcessorResult.success(
        "候选元数据刷新完成",
        data={"candidate_id": candidate_id, "series_id": series_id, "entry_id": entry_id, "release_id": release_id},
        next_payload={
            "_subject_type": "entry",
            "_subject_id": entry_id,
            "entry_id": entry_id,
            "series_id": series_id,
            "release_id": release_id,
        },
    )


async def process_metadata(context: ProcessorContext, payload: dict) -> ProcessorResult:
    if context.subject_type == "rss_candidate" or payload.get("candidate_id"):
        return await process_candidate_metadata(context, payload)
    entry_id = context.subject_id if context.subject_type == "entry" else int(payload.get("entry_id") or 0)
    if entry_id <= 0:
        return ProcessorResult.terminal("元数据处理器缺少 entry_id")
    settings = get_settings()
    await refresh_entry_metadata(entry_id, settings.get("rss_proxy", ""))
    return ProcessorResult.success("元数据刷新完成", data={"entry_id": entry_id})

