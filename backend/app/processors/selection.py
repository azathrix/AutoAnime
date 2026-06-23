from __future__ import annotations

from ..database import connect
from ..db import get_settings, log
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

    release_rows = {}
    if release_ids:
        with connect() as conn:
            from ..media_service import reset_orphaned_download_jobs_in_conn

            reset_count = reset_orphaned_download_jobs_in_conn(conn, entry_id)
            if reset_count:
                log("warn", f"自动选集前已释放中断下载状态: entry_id={entry_id} count={reset_count}")
            placeholders = ",".join("?" for _ in release_ids)
            rows = conn.execute(
                f"""
                SELECT r.id, r.entry_id, r.episode_number, r.title,
                  EXISTS(
                    SELECT 1
                    FROM local_assets la
                    WHERE la.entry_id=r.entry_id
                      AND la.episode_number=r.episode_number
                      AND la.status='synced'
                    LIMIT 1
                  ) AS completed_local,
                  EXISTS(
                    SELECT 1
                    FROM download_jobs dj
                    WHERE dj.entry_id=r.entry_id
                      AND dj.episode_number=r.episode_number
                      AND dj.status IN ('pending','running','submitted','downloading')
                    LIMIT 1
                  ) AS active_download,
                  EXISTS(
                    SELECT 1
                    FROM episode_resources er
                    WHERE er.entry_id=r.entry_id
                      AND er.episode_number=r.episode_number
                      AND er.selected=1
                      AND er.status='cancelled'
                    LIMIT 1
                  ) AS user_cancelled
                FROM releases r
                WHERE r.id IN ({placeholders})
                """,
                tuple(release_ids),
            ).fetchall()
            release_rows = {int(row["id"]): row for row in rows}

    next_tasks = []
    for release_id in release_ids:
        row = release_rows.get(int(release_id))
        episode_number = int(row["episode_number"] or 0) if row else 0
        release_entry_id = int(row["entry_id"] or entry_id) if row else entry_id
        if row and (int(row["completed_local"] or 0) or int(row["active_download"] or 0) or int(row["user_cancelled"] or 0)):
            continue
        next_tasks.append(
            {
                "_subject_type": "release",
                "_subject_id": release_id,
                "_dedupe_key": f"download:entry:{release_entry_id}:episode:{episode_number or release_id}",
                "entry_id": release_entry_id,
                "release_id": release_id,
                "episode_number": episode_number,
                "title": str(row["title"] or "") if row else "",
            }
        )
    if not next_tasks:
        return ProcessorResult.skipped(
            "自动选集完成: 已选集数均已可观看",
            data={"entry_id": entry_id, "release_ids": release_ids},
        )
    return ProcessorResult.success(
        f"自动选集完成: {len(next_tasks)} 个待下载发布",
        data={"entry_id": entry_id, "release_ids": release_ids},
        next_tasks=next_tasks,
    )

