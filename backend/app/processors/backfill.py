from __future__ import annotations

from ..database import connect
from ..db import get_settings, log, now
from ..pipeline_models import ProcessorContext, ProcessorResult
from ..scanner import fetch_mikan_page_releases, resolve_entry_mikan_bangumi_id, task_retry_after, upsert_release


async def process_backfill(context: ProcessorContext, payload: dict) -> ProcessorResult:
    entry_id = context.subject_id if context.subject_type == "entry" else int(payload.get("entry_id") or 0)
    if entry_id <= 0:
        return ProcessorResult.terminal("整季补全处理器缺少 entry_id")
    settings = get_settings()
    with connect() as conn:
        row = conn.execute(
            """
            SELECT id, display_title, title_cn, title_raw, bangumi_id, mikan_bangumi_id,
              year, poster_url, summary
            FROM entries
            WHERE id=?
            """,
            (entry_id,),
        ).fetchone()
        if not row:
            return ProcessorResult.terminal(f"条目不存在: {entry_id}")
    mikan_bangumi_id = str(row["mikan_bangumi_id"] or "")
    if not mikan_bangumi_id:
        try:
            mikan_bangumi_id = await resolve_entry_mikan_bangumi_id(settings, entry_id, str(row["bangumi_id"] or ""))
        except Exception as exc:
            return ProcessorResult.retryable(f"解析 Mikan 番组 ID 失败: {str(exc)[:1800]}", task_retry_after(settings, context.attempts + 1))
    if not mikan_bangumi_id:
        return ProcessorResult.skipped("缺少 Mikan 番组 ID，无法补全整季", data={"entry_id": entry_id})
    try:
        releases = await fetch_mikan_page_releases(settings, mikan_bangumi_id)
    except Exception as exc:
        return ProcessorResult.retryable(f"Mikan 番组页补全失败: {str(exc)[:1800]}", task_retry_after(settings, context.attempts + 1))
    metadata = {
        "title_cn": row["title_cn"] or row["display_title"] or row["title_raw"],
        "year": row["year"] or 0,
        "poster_url": row["poster_url"] or "",
        "summary": row["summary"] or "",
    }
    created = 0
    for item in releases:
        item.bangumi_id = str(row["bangumi_id"] or item.bangumi_id or "")
        item.mikan_bangumi_id = mikan_bangumi_id
        _, _, release_id = upsert_release(item, metadata)
        if release_id:
            created += 1
    with connect() as conn:
        conn.execute(
            "UPDATE entries SET mikan_bangumi_id=?, updated_at=? WHERE id=? AND mikan_bangumi_id=''",
            (mikan_bangumi_id, now(), entry_id),
        )
    log("info", f"整季补全完成: entry_id={entry_id} mikan_id={mikan_bangumi_id} releases={created}")
    return ProcessorResult.success(
        f"整季补全完成: {created} 条发布",
        data={"entry_id": entry_id, "mikan_bangumi_id": mikan_bangumi_id, "release_count": created},
        next_payload={
            "_subject_type": "entry",
            "_subject_id": entry_id,
            "entry_id": entry_id,
        },
    )

