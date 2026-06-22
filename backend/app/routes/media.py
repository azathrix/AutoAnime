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
)
from ..metadata import refresh_entry_metadata
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


@router.post("/api/media/{media_type}/{entry_id}/metadata/fetch")
async def api_fetch_media_metadata(media_type: str, entry_id: int, payload: MetadataFetchPayload) -> dict:
    normalize_api_media_type(media_type)
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
        entry = conn.execute("SELECT bangumi_id FROM entries WHERE id=?", (entry_id,)).fetchone()
    if not entry:
        raise HTTPException(status_code=404, detail="媒体条目不存在")
    if not str(entry["bangumi_id"] or "").strip():
        raise HTTPException(status_code=400, detail="当前扒信息需要先填写 Bangumi ID")
    await refresh_entry_metadata(entry_id, get_settings().get("rss_proxy", ""))
    log("info", f"媒体元数据已刷新: entry_id={entry_id} provider=bangumi")
    return build_media_entry_response(media_type, entry_id)


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
