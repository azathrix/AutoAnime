from __future__ import annotations

from typing import Any

from ..db import get_settings, log
from ..parser import ParsedRelease
from ..pipeline_models import ProcessorContext, ProcessorResult
from ..scanner import fetch_entries, upsert_rss_candidate


def release_to_payload(item: ParsedRelease) -> dict[str, Any]:
    return {
        "guid": item.guid,
        "title": item.title,
        "series_title": item.series_title,
        "episode_number": item.episode_number,
        "subtitle_group": item.subtitle_group,
        "resolution": item.resolution,
        "language": item.language,
        "subtitle_format": item.subtitle_format,
        "bangumi_id": item.bangumi_id,
        "year": item.year,
        "torrent_url": item.torrent_url,
        "magnet": item.magnet,
        "page_url": item.page_url,
        "mikan_bangumi_id": item.mikan_bangumi_id,
        "published_at": item.published_at,
    }


def release_from_payload(payload: dict[str, Any]) -> ParsedRelease:
    return ParsedRelease(
        guid=str(payload.get("guid") or ""),
        title=str(payload.get("title") or ""),
        series_title=str(payload.get("series_title") or ""),
        episode_number=int(payload.get("episode_number") or 0),
        subtitle_group=str(payload.get("subtitle_group") or ""),
        resolution=str(payload.get("resolution") or ""),
        language=str(payload.get("language") or ""),
        subtitle_format=str(payload.get("subtitle_format") or ""),
        bangumi_id=str(payload.get("bangumi_id") or ""),
        year=int(payload.get("year") or 0),
        torrent_url=str(payload.get("torrent_url") or ""),
        magnet=str(payload.get("magnet") or ""),
        page_url=str(payload.get("page_url") or ""),
        mikan_bangumi_id=str(payload.get("mikan_bangumi_id") or ""),
        published_at=str(payload.get("published_at") or ""),
    )


async def process_rss_fetch(context: ProcessorContext, payload: dict) -> ProcessorResult:
    settings = get_settings()
    rss_url = str(payload.get("rss_url") or settings.get("rss_url") or "").strip()
    if not rss_url:
        return ProcessorResult.terminal("RSS 地址未配置")
    fetch_settings = dict(settings)
    fetch_settings["rss_url"] = rss_url
    entries = await fetch_entries(fetch_settings)
    log("info", f"RSS 拉取完成: {len(entries)} 条")
    next_tasks = []
    for item in entries:
        item_payload = release_to_payload(item)
        item_payload["_subject_type"] = "rss_candidate"
        item_payload["_subject_id"] = 0
        item_payload["_dedupe_key"] = f"{context.run_id}:rss-candidate:{item.guid}"
        next_tasks.append(item_payload)
    return ProcessorResult.success(
        f"RSS 拉取完成: {len(entries)} 条",
        data={"release_count": len(entries)},
        next_tasks=next_tasks,
    )


async def process_rss_candidate_persist(_context: ProcessorContext, payload: dict) -> ProcessorResult:
    item = release_from_payload(payload)
    if not item.guid or not item.title:
        return ProcessorResult.terminal("RSS 候选缺少 guid 或 title")
    if int(item.episode_number or 0) <= 0:
        log("warn", f"RSS 候选跳过: 未识别集数 title={item.title[:180]}")
        return ProcessorResult.skipped("未识别集数，跳过资源入库")
    candidate_id = upsert_rss_candidate(item, "pipeline:rss")
    return ProcessorResult.success(
        "RSS 候选已写入",
        data={"candidate_id": candidate_id, "guid": item.guid},
        next_payload={
            "_subject_type": "rss_candidate",
            "_subject_id": candidate_id,
            "candidate_id": candidate_id,
            "bangumi_id": item.bangumi_id,
            "mikan_bangumi_id": item.mikan_bangumi_id,
        },
    )

