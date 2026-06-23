from __future__ import annotations

from ..database import connect
from ..db import get_settings, log, now
from ..pipeline_models import ProcessorContext, ProcessorResult
from ..processing_cache import first_resource_ref, get_cached_json, set_cached_json
from ..scanner import fetch_mikan_match, task_retry_after


async def process_mikan_match(context: ProcessorContext, payload: dict) -> ProcessorResult:
    candidate_id = context.subject_id if context.subject_type == "rss_candidate" else int(payload.get("candidate_id") or 0)
    if candidate_id <= 0:
        return ProcessorResult.terminal("Mikan 匹配缺少 candidate_id")
    settings = get_settings()
    with connect() as conn:
        row = conn.execute("SELECT * FROM rss_candidates WHERE id=?", (candidate_id,)).fetchone()
    if not row:
        return ProcessorResult.terminal(f"RSS 候选不存在: {candidate_id}")

    existing_bangumi = str(row["bangumi_id"] or "")
    existing_mikan = str(row["mikan_bangumi_id"] or "")
    cache_ref = first_resource_ref(row["magnet"], row["torrent_url"], row["page_url"], row["guid"])
    if existing_bangumi and existing_mikan:
        set_cached_json(
            "mikan_match",
            cache_ref,
            {"bangumi_id": existing_bangumi, "mikan_bangumi_id": existing_mikan},
        )
        return ProcessorResult.success(
            "Mikan 匹配已存在，跳过页面刷新",
            data={"candidate_id": candidate_id, "bangumi_id": existing_bangumi, "mikan_bangumi_id": existing_mikan},
            next_payload={
                "_subject_type": "rss_candidate",
                "_subject_id": candidate_id,
                "candidate_id": candidate_id,
                "bangumi_id": existing_bangumi,
                "mikan_bangumi_id": existing_mikan,
            },
        )

    cached_match = get_cached_json("mikan_match", cache_ref)
    if isinstance(cached_match, dict):
        cached_bangumi = str(cached_match.get("bangumi_id") or "")
        cached_mikan = str(cached_match.get("mikan_bangumi_id") or existing_mikan or "")
        if cached_bangumi:
            ts = now()
            with connect() as conn:
                conn.execute(
                    """
                    UPDATE rss_candidates
                    SET status='pending_metadata', bangumi_id=?, mikan_bangumi_id=?, reason='Mikan 缓存命中，等待元数据刷新', updated_at=?
                    WHERE id=?
                    """,
                    (cached_bangumi, cached_mikan, ts, candidate_id),
                )
                if cached_mikan:
                    conn.execute(
                        """
                        UPDATE series
                        SET mikan_bangumi_id=?, updated_at=?
                        WHERE bangumi_id=?
                          AND mikan_bangumi_id=''
                        """,
                        (cached_mikan, ts, cached_bangumi),
                    )
            log(
                "info",
                f"Mikan 匹配缓存命中: candidate_id={candidate_id} bangumi_id={cached_bangumi} mikan_id={cached_mikan or '-'}",
            )
            return ProcessorResult.success(
                "Mikan 匹配缓存命中",
                data={"candidate_id": candidate_id, "bangumi_id": cached_bangumi, "mikan_bangumi_id": cached_mikan},
                next_payload={
                    "_subject_type": "rss_candidate",
                    "_subject_id": candidate_id,
                    "candidate_id": candidate_id,
                    "bangumi_id": cached_bangumi,
                    "mikan_bangumi_id": cached_mikan,
                },
            )

    try:
        log(
            "info",
            f"Mikan 匹配开始: candidate_id={candidate_id} title={row['title']} "
            f"page_url={row['page_url'] or '-'} known_mikan_id={existing_mikan or '-'}",
        )
        bangumi_id, mikan_id = await fetch_mikan_match(
            settings,
            str(row["page_url"] or ""),
            existing_mikan,
        )
        if not bangumi_id:
            raise RuntimeError("Mikan 页面未找到 Bangumi subject 链接")
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
            """
            UPDATE rss_candidates
            SET status='pending_metadata', bangumi_id=?, mikan_bangumi_id=?, reason='等待元数据刷新', updated_at=?
            WHERE id=?
            """,
            (bangumi_id, mikan_id, ts, candidate_id),
        )
        if mikan_id:
            conn.execute(
                """
                UPDATE series
                SET mikan_bangumi_id=?, updated_at=?
                WHERE bangumi_id=?
                  AND mikan_bangumi_id=''
                """,
                (mikan_id, ts, bangumi_id),
            )
    cache_payload = {"bangumi_id": bangumi_id, "mikan_bangumi_id": mikan_id}
    set_cached_json("mikan_match", cache_ref, cache_payload)
    if row["page_url"]:
        set_cached_json("mikan_match", str(row["page_url"] or ""), cache_payload)
    if mikan_id:
        set_cached_json("mikan_match", f"mikan:{mikan_id}", cache_payload)
    log(
        "info",
        f"Mikan 匹配完成: candidate_id={candidate_id} bangumi_id={bangumi_id} mikan_id={mikan_id or '-'}",
    )

    return ProcessorResult.success(
        "Mikan 匹配完成",
        data={"candidate_id": candidate_id, "bangumi_id": bangumi_id, "mikan_bangumi_id": mikan_id},
        next_payload={
            "_subject_type": "rss_candidate",
            "_subject_id": candidate_id,
            "candidate_id": candidate_id,
            "bangumi_id": bangumi_id,
            "mikan_bangumi_id": mikan_id,
        },
    )

