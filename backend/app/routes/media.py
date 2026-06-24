from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..database import connect
from ..db import get_settings, log, now
from ..media_service import (
    archive_seasonal_entry,
    build_entry_response,
    build_media_entry_response,
    create_media_entry,
    hide_entry,
    media_items_response,
    normalize_api_media_type,
    save_entry_payload,
    set_entry_following,
)
from ..metadata import fetch_tmdb_metadata, refresh_entry_metadata
from ..rss_scan_service import start_metadata_refresh_task
from ..schemas import EntryPayload, MediaCreatePayload, MetadataFetchPayload


router = APIRouter()


@router.get("/api/media/{media_type}")
async def api_media_items(media_type: str) -> dict:
    return media_items_response(media_type)


@router.post("/api/media/{media_type}")
async def api_create_media_entry(media_type: str, payload: MediaCreatePayload) -> dict:
    return create_media_entry(media_type, payload)


@router.get("/api/media/{media_type}/{entry_id}")
async def api_media_entry(media_type: str, entry_id: int) -> dict:
    return build_media_entry_response(media_type, entry_id)


@router.put("/api/media/{media_type}/{entry_id}")
async def api_update_media_entry(media_type: str, entry_id: int, payload: EntryPayload) -> dict:
    normalize_api_media_type(media_type)
    return save_entry_payload(entry_id, payload, expected_domain=None)


@router.delete("/api/media/{media_type}/{entry_id}")
async def api_delete_media_entry(media_type: str, entry_id: int) -> dict[str, str]:
    normalized = normalize_api_media_type(media_type)
    return hide_entry(
        entry_id,
        expected_media_type=normalized,
        success_message="已删除媒体条目，本地文件不会被删除",
        log_prefix="已删除媒体条目",
    )


@router.post("/api/media/{media_type}/{entry_id}/follow")
async def api_follow_media_entry(media_type: str, entry_id: int) -> dict[str, str]:
    normalize_api_media_type(media_type)
    return set_entry_following(entry_id, True)


@router.post("/api/media/{media_type}/{entry_id}/unfollow")
async def api_unfollow_media_entry(media_type: str, entry_id: int) -> dict[str, str]:
    normalize_api_media_type(media_type)
    return set_entry_following(entry_id, False)


@router.post("/api/media/{media_type}/{entry_id}/metadata/fetch")
async def api_fetch_media_metadata(media_type: str, entry_id: int, payload: MetadataFetchPayload) -> dict:
    normalized_type = normalize_api_media_type(media_type)
    bangumi_id = payload.bangumi_id.strip()
    tmdb_id = payload.tmdb_id.strip()
    if bangumi_id or tmdb_id:
        with connect() as conn:
            exists = conn.execute("SELECT id FROM entries WHERE id=?", (entry_id,)).fetchone()
            if not exists:
                raise HTTPException(status_code=404, detail="媒体条目不存在")
            conn.execute(
                "UPDATE entries SET bangumi_id=CASE WHEN ?='' THEN bangumi_id ELSE ? END, tmdb_id=CASE WHEN ?='' THEN tmdb_id ELSE ? END, updated_at=? WHERE id=?",
                (bangumi_id, bangumi_id, tmdb_id, tmdb_id, now(), entry_id),
            )
    with connect() as conn:
        entry = conn.execute("SELECT bangumi_id, tmdb_id FROM entries WHERE id=?", (entry_id,)).fetchone()
    if not entry:
        raise HTTPException(status_code=404, detail="媒体条目不存在")
    settings = get_settings()
    refreshed = []
    if str(entry["bangumi_id"] or "").strip():
        await refresh_entry_metadata(entry_id, settings.get("rss_proxy", ""))
        refreshed.append("bangumi")
    tmdb_value = str(entry["tmdb_id"] or "").strip()
    token = settings.get("tmdb_token", "").strip()
    if tmdb_value and token:
        try:
            metadata = await fetch_tmdb_metadata(tmdb_value, normalized_type, token, settings.get("rss_proxy", ""))
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"TMDB 元数据刷新失败: {exc}") from exc
        with connect() as conn:
            conn.execute(
                """
                UPDATE entries
                SET title_cn=CASE WHEN title_cn='' THEN ? ELSE title_cn END,
                    title_raw=CASE WHEN title_raw='' THEN ? ELSE title_raw END,
                    poster_url=CASE WHEN poster_url='' THEN ? ELSE poster_url END,
                    summary=CASE WHEN summary='' THEN ? ELSE summary END,
                    year=CASE WHEN year=0 THEN ? ELSE year END,
                    month=CASE WHEN month=0 THEN ? ELSE month END,
                    region=CASE WHEN region='' THEN ? ELSE region END,
                    tags_json=CASE WHEN tags_json='[]' THEN ? ELSE tags_json END,
                    tmdb_score=?,
                    metadata_source=CASE WHEN metadata_source='' THEN 'tmdb' ELSE metadata_source END,
                    updated_at=?
                WHERE id=?
                """,
                (
                    metadata.get("title_cn", ""),
                    metadata.get("title_raw", ""),
                    metadata.get("poster_url", ""),
                    metadata.get("summary", ""),
                    int(metadata.get("year") or 0),
                    int(metadata.get("month") or 0),
                    metadata.get("region", ""),
                    metadata.get("tags_json", "[]"),
                    float(metadata.get("tmdb_score") or 0),
                    now(),
                    entry_id,
                ),
            )
        refreshed.append("tmdb")
    if not refreshed:
        if tmdb_value and not token:
            raise HTTPException(status_code=400, detail="刷新 TMDB 信息需要先在设置中配置 TMDB token")
        raise HTTPException(status_code=400, detail="请先填写 Bangumi ID 或 TMDB ID")
    log("info", f"媒体元数据已刷新: entry_id={entry_id} provider={','.join(refreshed)}")
    return build_media_entry_response(media_type, entry_id)


@router.post("/api/entries/{entry_id}/metadata/refresh")
async def api_refresh_entry_metadata_task(entry_id: int) -> dict[str, str]:
    with connect() as conn:
        exists = conn.execute("SELECT id FROM entries WHERE id=?", (entry_id,)).fetchone()
    if not exists:
        raise HTTPException(status_code=404, detail="媒体条目不存在")
    operation_id = start_metadata_refresh_task(entry_id, f"手动刷新元数据: entry_id={entry_id}")
    return {"status": "started", "operation_id": str(operation_id), "message": "元数据刷新任务已创建"}


@router.get("/api/entries/{entry_id}/episodes")
async def api_entry_episodes(entry_id: int) -> dict:
    detail = build_entry_response(entry_id)
    if not detail.get("entry"):
        raise HTTPException(status_code=404, detail="媒体条目不存在")
    return {
        "entry": detail.get("entry"),
        "episodes": detail.get("episodes", []),
        "episode_resources": detail.get("episode_resources", []),
        "episode_subtitles": detail.get("episode_subtitles", []),
    }


@router.delete("/api/seasonal/{entry_id}")
async def api_delete_seasonal_entry(entry_id: int) -> dict[str, str]:
    return archive_seasonal_entry(entry_id)


@router.delete("/api/library/{entry_id}")
async def api_delete_library_entry(entry_id: int) -> dict[str, str]:
    return hide_entry(
        entry_id,
        expected_domain="library",
        success_message="已隐藏番剧库条目，关联记录已保留",
        log_prefix="已隐藏番剧库条目",
    )
