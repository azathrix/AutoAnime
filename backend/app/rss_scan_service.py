from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .database import connect
from .db import get_settings, log, now
from .download_task_service import queue_download_for_release
from .maintenance import refresh_local_status
from .metadata import fetch_tmdb_metadata, refresh_entry_metadata
from .parser import ParsedRelease
from .processing_cache import first_resource_ref, get_cached_json, set_cached_json
from .runtime_store import runtime_store
from .scanner import fetch_entries, fetch_mikan_match, upsert_release, upsert_rss_candidate


RSS_RESOURCE_CACHE_TYPE = "rss_resource_processed"
RSS_RESOURCE_CACHE_TTL_SECONDS = 30 * 24 * 60 * 60


@dataclass
class ScanStats:
    fetched: int = 0
    skipped_cached: int = 0
    skipped_invalid: int = 0
    synced_bangumi: int = 0
    metadata_tasks: int = 0
    resources: int = 0
    queued_downloads: int = 0
    already_watchable: int = 0
    already_queued: int = 0
    missing_resource: int = 0
    failed: int = 0

    def message(self) -> str:
        return (
            f"RSS 扫描完成: 拉取 {self.fetched} 条，缓存跳过 {self.skipped_cached} 条，"
            f"同步 Bangumi ID {self.synced_bangumi} 条，集数资源 {self.resources} 条，"
            f"下载任务 {self.queued_downloads} 个，已可观看 {self.already_watchable} 集，"
            f"待处理 {self.missing_resource} 条，失败 {self.failed} 条"
        )


def resource_cache_ref(item: ParsedRelease) -> str:
    return first_resource_ref(item.magnet, item.torrent_url, item.page_url, item.guid)


def has_downloadable_source(item: ParsedRelease) -> bool:
    return bool(str(item.magnet or "").strip() or str(item.torrent_url or "").strip())


def update_candidate_status(candidate_id: int, status: str, reason: str, item: ParsedRelease | None = None) -> None:
    if candidate_id <= 0:
        return
    ts = now()
    with connect() as conn:
        if item:
            conn.execute(
                """
                UPDATE rss_candidates
                SET status=?, reason=?, bangumi_id=CASE WHEN ?='' THEN bangumi_id ELSE ? END,
                    mikan_bangumi_id=CASE WHEN ?='' THEN mikan_bangumi_id ELSE ? END,
                    updated_at=?
                WHERE id=?
                """,
                (
                    status,
                    reason,
                    item.bangumi_id,
                    item.bangumi_id,
                    item.mikan_bangumi_id,
                    item.mikan_bangumi_id,
                    ts,
                    candidate_id,
                ),
            )
        else:
            conn.execute(
                "UPDATE rss_candidates SET status=?, reason=?, updated_at=? WHERE id=?",
                (status, reason, ts, candidate_id),
            )


def existing_entry_for_bangumi(bangumi_id: str) -> dict[str, Any] | None:
    if not str(bangumi_id or "").strip():
        return None
    with connect() as conn:
        row = conn.execute(
            """
            SELECT id, bangumi_id, mikan_bangumi_id, display_title, title_cn, poster_url, summary, tags_json
            FROM entries
            WHERE bangumi_id=? AND COALESCE(hidden, 0)=0
            ORDER BY id ASC
            LIMIT 1
            """,
            (bangumi_id,),
        ).fetchone()
    return dict(row) if row else None


def episode_snapshot(entry_id: int, episode_number: int) -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT ep.id, ep.watchable, ep.local_path, ep.resource_ref, ep.release_id,
                   er.source_ref AS selected_source_ref,
                   er.magnet AS selected_magnet,
                   er.torrent_url AS selected_torrent_url,
                   dj.id AS active_download_id
            FROM episodes ep
            LEFT JOIN episode_resources er ON er.id=(
              SELECT id
              FROM episode_resources
              WHERE entry_id=ep.entry_id AND episode_number=ep.episode_number
              ORDER BY selected DESC, id DESC
              LIMIT 1
            )
            LEFT JOIN download_jobs dj ON dj.id=(
              SELECT id
              FROM download_jobs
              WHERE entry_id=ep.entry_id
                AND episode_number=ep.episode_number
                AND status IN ('pending','submitting','remote_downloading','remote_completed','local_copying','running','submitted','downloading')
              ORDER BY updated_at DESC, id DESC
              LIMIT 1
            )
            WHERE ep.entry_id=? AND ep.episode_number=?
            ORDER BY ep.id DESC
            LIMIT 1
            """,
            (entry_id, episode_number),
        ).fetchone()
    return dict(row) if row else {}


async def sync_bangumi_id(settings: dict[str, str], item: ParsedRelease, cache_ref: str) -> bool:
    cached = get_cached_json("mikan_match", cache_ref)
    if isinstance(cached, dict):
        item.bangumi_id = str(cached.get("bangumi_id") or item.bangumi_id or "")
        item.mikan_bangumi_id = str(cached.get("mikan_bangumi_id") or item.mikan_bangumi_id or "")
        return bool(item.bangumi_id)

    bangumi_id, mikan_id = await fetch_mikan_match(settings, item.page_url, item.mikan_bangumi_id)
    item.bangumi_id = str(bangumi_id or item.bangumi_id or "")
    item.mikan_bangumi_id = str(mikan_id or item.mikan_bangumi_id or "")
    if item.bangumi_id:
        payload = {"bangumi_id": item.bangumi_id, "mikan_bangumi_id": item.mikan_bangumi_id}
        set_cached_json("mikan_match", cache_ref, payload)
        if item.page_url:
            set_cached_json("mikan_match", item.page_url, payload)
        if item.mikan_bangumi_id:
            set_cached_json("mikan_match", f"mikan:{item.mikan_bangumi_id}", payload)
    return bool(item.bangumi_id)


async def refresh_entry_metadata_body(entry_id: int) -> str:
    settings = get_settings()
    with connect() as conn:
        entry = conn.execute(
            "SELECT id, display_title, bangumi_id, tmdb_id, media_type FROM entries WHERE id=?",
            (entry_id,),
        ).fetchone()
    if not entry:
        return "条目不存在，已跳过"

    refreshed: list[str] = []
    bangumi_id = str(entry["bangumi_id"] or "").strip()
    if bangumi_id:
        await refresh_entry_metadata(entry_id, settings.get("rss_proxy", ""))
        refreshed.append("Bangumi")

    tmdb_id = str(entry["tmdb_id"] or "").strip()
    tmdb_token = settings.get("tmdb_token", "").strip()
    if tmdb_id and tmdb_token:
        metadata = await fetch_tmdb_metadata(
            tmdb_id,
            str(entry["media_type"] or "tv"),
            tmdb_token,
            settings.get("rss_proxy", ""),
        )
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
        refreshed.append("TMDB")

    if not refreshed:
        return "缺少 Bangumi/TMDB ID，已跳过"
    return f"元数据刷新完成: entry_id={entry_id} provider={','.join(refreshed)}"


def start_metadata_refresh_task(entry_id: int, reason: str = "", *, dedupe: bool = False) -> int:
    from .runtime_service import run_operation

    if dedupe:
        cache_key = str(int(entry_id or 0))
        if get_cached_json("metadata_refresh_requested", cache_key):
            return 0
        set_cached_json("metadata_refresh_requested", cache_key, {"entry_id": entry_id}, ttl_seconds=10 * 60)

    async def runner() -> str:
        return await refresh_entry_metadata_body(entry_id)

    return run_operation("刷新元数据", runner, reason or f"entry_id={entry_id}")


def queue_metadata_refresh_if_needed(before: dict[str, Any] | None, entry_id: int, item: ParsedRelease) -> bool:
    if entry_id <= 0:
        return False
    if before is None:
        return start_metadata_refresh_task(entry_id, f"新作品: entry_id={entry_id} {item.series_title}", dedupe=True) > 0
    old_mikan = str(before.get("mikan_bangumi_id") or "")
    missing_basic = not str(before.get("poster_url") or "").strip() or not str(before.get("summary") or "").strip()
    if item.mikan_bangumi_id and item.mikan_bangumi_id != old_mikan:
        return start_metadata_refresh_task(entry_id, f"Bangumi/Mikan ID 已变化: entry_id={entry_id} {item.series_title}", dedupe=True) > 0
    if missing_basic:
        return start_metadata_refresh_task(entry_id, f"补齐元数据: entry_id={entry_id} {item.series_title}", dedupe=True) > 0
    return False


def mark_episode_unknown(entry_id: int, episode_number: int) -> None:
    ts = now()
    with connect() as conn:
        conn.execute(
            """
            UPDATE episodes
            SET status='missing', watchable=0, updated_at=?
            WHERE entry_id=? AND episode_number=?
            """,
            (ts, entry_id, episode_number),
        )
        conn.execute(
            """
            UPDATE episode_resources
            SET status='missing', updated_at=?
            WHERE entry_id=? AND episode_number=?
            """,
            (ts, entry_id, episode_number),
        )


async def process_rss_item(settings: dict[str, str], item: ParsedRelease, stats: ScanStats) -> None:
    if int(item.episode_number or 0) <= 0:
        stats.skipped_invalid += 1
        log("warn", f"RSS 条目跳过: 未识别集数 title={item.title[:160]}")
        return

    cache_ref = resource_cache_ref(item)
    if cache_ref and get_cached_json(RSS_RESOURCE_CACHE_TYPE, cache_ref):
        stats.skipped_cached += 1
        return

    candidate_id = upsert_rss_candidate(item, "等待同步 Bangumi ID")
    try:
        if await sync_bangumi_id(settings, item, cache_ref):
            stats.synced_bangumi += 1
            update_candidate_status(candidate_id, "pending", "已同步 Bangumi ID，准备写入集数资源", item)
        else:
            stats.failed += 1
            update_candidate_status(candidate_id, "failed", "未能同步 Bangumi ID", item)
            log("warn", f"同步 Bangumi ID 失败: candidate_id={candidate_id} title={item.title[:160]}")
            return

        before = existing_entry_for_bangumi(item.bangumi_id)
        _series_id, entry_id, release_id = upsert_release(item, {})
        if entry_id <= 0 or release_id <= 0:
            stats.failed += 1
            update_candidate_status(candidate_id, "failed", "集数资源写入失败", item)
            return
        stats.resources += 1

        if queue_metadata_refresh_if_needed(before, entry_id, item):
            stats.metadata_tasks += 1

        refresh_local_status(entry_id=entry_id)
        episode = episode_snapshot(entry_id, int(item.episode_number or 0))
        if int(episode.get("watchable") or 0) == 1:
            stats.already_watchable += 1
            update_candidate_status(candidate_id, "completed", "本地文件已存在，跳过下载", item)
        elif has_downloadable_source(item):
            result = queue_download_for_release(release_id)
            if result.get("queued"):
                stats.queued_downloads += 1
                update_candidate_status(candidate_id, "completed", "已生成下载任务", item)
            else:
                reason = str(result.get("reason") or "未生成下载任务")
                if "已有活跃下载任务" in reason:
                    stats.already_queued += 1
                update_candidate_status(candidate_id, "completed", reason, item)
        else:
            stats.missing_resource += 1
            mark_episode_unknown(entry_id, int(item.episode_number or 0))
            update_candidate_status(candidate_id, "completed", "无资源链接，等待手动补资源", item)

        if cache_ref:
            set_cached_json(
                RSS_RESOURCE_CACHE_TYPE,
                cache_ref,
                {
                    "entry_id": entry_id,
                    "release_id": release_id,
                    "episode_number": int(item.episode_number or 0),
                    "bangumi_id": item.bangumi_id,
                    "mikan_bangumi_id": item.mikan_bangumi_id,
                },
                ttl_seconds=RSS_RESOURCE_CACHE_TTL_SECONDS,
            )
    except Exception as exc:
        stats.failed += 1
        update_candidate_status(candidate_id, "failed", str(exc), item)
        log("error", f"RSS 条目处理失败: candidate_id={candidate_id} title={item.title[:160]} error={exc}")


async def run_rss_scan(settings: dict[str, str], operation_id: int | None = None) -> str:
    if not settings.get("rss_url"):
        log("warn", "未配置 Mikan RSS")
        return "未配置 Mikan RSS"
    if operation_id:
        runtime_store.update_operation_sync(operation_id, "正在拉取 RSS")
    items = await fetch_entries(settings)
    stats = ScanStats(fetched=len(items))
    log("info", f"RSS 拉取完成: {len(items)} 条")
    for index, item in enumerate(items, start=1):
        if operation_id:
            runtime_store.update_operation_sync(operation_id, f"正在处理 RSS: {index}/{len(items)}")
        await process_rss_item(settings, item, stats)
    message = stats.message()
    log("info", message)
    return message
