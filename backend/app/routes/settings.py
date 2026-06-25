from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..config import MEDIA_ROOT
from ..db import get_settings, log, now, save_settings
from ..downloader_service import SUPPORTED_DOWNLOADER_TYPES
from ..maintenance import diagnostics
from ..metadata import (
    chinese_summary,
    search_bangumi,
    search_tmdb,
    subject_cn_name,
    subject_month,
    subject_score,
    subject_tags_json,
    subject_year,
)
from ..runtime_service import reschedule
from ..schedule_service import upsert_schedule
from ..schemas import ProcessorSettingsPayload, ScheduledJobPayload, SettingsPayload
from ..settings_service import clean_downloader_items, derived_downloader_settings, settings_response, sync_download_processor_concurrency
from ..utils import int_setting


router = APIRouter()


@router.get("/api/settings")
async def api_settings() -> dict:
    return settings_response()


@router.get("/api/system/diagnostics")
async def api_system_diagnostics() -> dict:
    return diagnostics()


@router.put("/api/settings")
async def api_update_settings(payload: SettingsPayload) -> dict:
    previous = get_settings()
    downloaders = clean_downloader_items(payload.downloaders)
    downloader_settings = derived_downloader_settings(downloaders, previous)
    save_settings(
        {
            "rss_url": payload.rss_url.strip(),
            "rss_proxy": payload.rss_proxy.strip(),
            "scan_interval_minutes": payload.scan_interval_minutes,
            "auto_scan": str(payload.auto_scan).lower(),
            "queue_dispatch_enabled": str(payload.queue_dispatch_enabled).lower(),
            "queue_dispatch_interval_minutes": payload.queue_dispatch_interval_minutes,
            "auto_download_unique": "true",
            "auto_download_by_priority": "true",
            "auto_generate_nfo": str(payload.auto_generate_nfo).lower(),
            "generate_bangumi_ini": str(payload.generate_bangumi_ini).lower(),
            "backfill_current_season": str(payload.backfill_current_season).lower(),
            "default_backfill": "season" if payload.backfill_current_season else "none",
            "subtitle_priority": "\n".join(payload.subtitle_priority),
            "resolution_priority": "\n".join(payload.resolution_priority),
            "language_priority": "\n".join(payload.language_priority),
            "secondary_language_priority": "\n".join(payload.secondary_language_priority),
            "download_concurrency": str(int_setting(payload.download_concurrency, 2, 1, 12)),
            **downloader_settings,
            "local_library_root": str(MEDIA_ROOT),
            "auto_sync_following": "true",
            "nfo_output_root": "",
            "episode_name_template": payload.episode_name_template.strip(),
            "movie_name_template": payload.movie_name_template.strip(),
            "tv_name_template": payload.tv_name_template.strip(),
            "movie_quality_priority": "\n".join(payload.movie_quality_priority),
            "movie_source_priority": "\n".join(payload.movie_source_priority),
            "movie_subtitle_priority": "\n".join(payload.movie_subtitle_priority),
            "tv_quality_priority": "\n".join(payload.tv_quality_priority),
            "tv_source_priority": "\n".join(payload.tv_source_priority),
            "tv_subtitle_priority": "\n".join(payload.tv_subtitle_priority),
            "tmdb_token": payload.tmdb_token.strip(),
        }
    )
    sync_download_processor_concurrency(int_setting(payload.download_concurrency, 2, 1, 12))
    reschedule()
    log("info", "全局设置已保存")
    return settings_response()


@router.put("/api/scheduled-jobs/{job_key}")
async def api_update_scheduled_job(job_key: str, payload: ScheduledJobPayload) -> dict:
    interval = max(1, int(payload.interval_minutes or 1))
    enabled = str(bool(payload.enabled)).lower()
    if job_key == "rss_scan":
        save_settings({"auto_scan": enabled, "scan_interval_minutes": str(interval)})
        upsert_schedule(
            {
                "key": "rss_scan",
                "name": "RSS 定时扫描",
                "action": "rss_scan",
                "enabled": bool(payload.enabled),
                "interval_minutes": interval,
            }
        )
    elif job_key == "queue_dispatch":
        save_settings({"queue_dispatch_enabled": enabled, "queue_dispatch_interval_minutes": str(interval)})
    else:
        raise HTTPException(status_code=404, detail="定时任务不存在")
    reschedule()
    log("info", f"定时任务已更新: job_key={job_key} enabled={enabled} interval={interval}m")
    return {"status": "saved", "settings": settings_response()}


@router.put("/api/processors/{processor_key}/settings")
async def api_update_processor_settings(processor_key: str, payload: ProcessorSettingsPayload) -> dict:
    key = processor_key.strip().lower()
    if key != "download":
        raise HTTPException(status_code=404, detail="处理器设置不存在")
    concurrency = sync_download_processor_concurrency(payload.download_concurrency)
    save_settings({"download_concurrency": str(concurrency)})
    log("info", f"下载处理器并发已更新: {concurrency}")
    return {"status": "saved", "settings": settings_response()}


@router.get("/api/metadata/search")
async def api_search_metadata(provider: str = Query("bangumi"), keyword: str = Query("")) -> dict:
    provider_key = provider.strip().lower() or "bangumi"
    text = keyword.strip()
    if not text:
        raise HTTPException(status_code=400, detail="搜索关键词不能为空")
    if provider_key == "tmdb":
        settings = get_settings()
        token = settings.get("tmdb_token", "").strip()
        if not token:
            raise HTTPException(status_code=400, detail="请先在设置中配置 TMDB token")
        try:
            return {"provider": "tmdb", "items": await search_tmdb(text, token, settings.get("rss_proxy", ""))}
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"TMDB 搜索失败: {exc}") from exc
    if provider_key != "bangumi":
        raise HTTPException(status_code=400, detail="未知元数据来源")
    try:
        rows = await search_bangumi(text, get_settings().get("rss_proxy", ""))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Bangumi 搜索失败: {exc}") from exc
    items = []
    for row in rows[:20]:
        images = row.get("images") or {}
        items.append(
            {
                "provider": "bangumi",
                "id": str(row.get("id") or ""),
                "title": subject_cn_name(row) or row.get("name_cn") or row.get("name") or "",
                "original_title": row.get("name") or "",
                "year": subject_year(row),
                "month": subject_month(row),
                "poster_url": images.get("large") or images.get("common") or images.get("medium") or "",
                "summary": chinese_summary(row.get("summary") or ""),
                "tags_json": subject_tags_json(row),
                "bangumi_score": subject_score(row),
            }
        )
    return {"provider": "bangumi", "items": items}
