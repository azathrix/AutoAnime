from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any

from fastapi.concurrency import run_in_threadpool

from .database import connect
from .db import get_settings
from .download_task_service import download_overview, list_download_tasks
from .library import bool_setting
from .pipeline_runtime import pipeline_overview
from .runtime_service import canonical_queue_key, queue_job_key, QUEUE_KEY_ALIASES
from .runtime_store import runtime_store
from .utils import enrich_catalog_entry, int_setting, rows_to_dicts, seconds_until, summarize_seasonal_entry

DASHBOARD_CACHE_TTL = 1.0
dashboard_cache: dict[str, Any] = {"ts": 0.0, "data": None}
dashboard_cache_lock = asyncio.Lock()

def queue_summary(settings: dict[str, str]) -> list[dict[str, Any]]:
    snapshot = runtime_store.snapshot()
    runtime_queues = {str(item.get("key") or ""): item for item in snapshot.get("queues", [])}

    def runtime_item(key: str, name: str, description: str = "") -> dict[str, Any]:
        runtime_key = canonical_queue_key(key)
        row = runtime_queues.get(runtime_key, {})
        pending = int(row.get("pending", 0) or 0)
        running = int(row.get("running", 0) or 0)
        failed = int(row.get("failed", 0) or 0)
        waiting = int(row.get("waiting", 0) or 0)
        if running:
            queue_state = "running"
            state_reason = "队列正在处理当前批次任务"
        elif pending:
            queue_state = "ready"
            state_reason = "已有待处理任务，可立即执行"
        elif waiting:
            queue_state = "cooldown"
            state_reason = "任务正在等待重试"
        elif failed:
            queue_state = "failed"
            state_reason = "存在失败任务，等待重试或人工处理"
        else:
            queue_state = "idle"
            state_reason = "当前没有可处理任务"
        return {
            "key": key,
            "runtime_queue_key": runtime_key,
            "name": name,
            "pending": pending,
            "running": running,
            "failed": failed,
            "waiting": waiting,
            "next_retry_after": "",
            "next_retry_seconds": 0,
            "queue_state": queue_state,
            "state_reason": state_reason,
            "state_detail": f"当前运行 {running} 个，待处理 {pending} 个" if running else (f"当前批次可执行 {pending} 个" if pending else ""),
            "description": description,
            "max_concurrency": int_setting(settings.get("download_concurrency"), 2, 1, 12) if key == "download" else 1,
        }

    return [
        {
            "key": "rss",
            "name": "Mikan RSS",
            "pending": 0,
            "running": 0,
            "failed": 0,
            "waiting": 0,
            "description": "周期读取 RSS，新增发布后进入 Runtime 流水线",
            "queue_state": "scheduled" if bool_setting(settings.get("auto_scan", "false")) else "disabled",
            "state_reason": "自动扫描已启用" if bool_setting(settings.get("auto_scan", "false")) else "自动扫描未启用",
            "system_queue": True,
            "runtime_queue_key": "rss",
        },
        runtime_item("processor", "流水线处理器", "统一 Runtime 处理器队列"),
        runtime_item("mikan_match", "Mikan 匹配", "解析 Mikan 与 Bangumi 对应关系"),
        runtime_item("metadata", "元数据", "刷新 Bangumi/条目元数据"),
        runtime_item("selection", "自动选集", "根据全局优先级选择唯一发布"),
        runtime_item("backfill", "整季补全", "补抓当季历史条目"),
        runtime_item("download", "下载到本地", "提交下载器、轮询完成并整理到本地媒体库"),
        runtime_item("local_presence", "本地存在性检查", "检查本地最终文件状态"),
    ]


def scanner_status(runtime_snapshot: dict[str, Any]) -> dict[str, Any]:
    scan_ops = [dict(item) for item in runtime_snapshot.get("operations", []) if item.get("name") == "扫描全部"]
    running = next((item for item in scan_ops if item.get("status") == "running"), None)
    latest = running or (scan_ops[0] if scan_ops else {})
    return {
        "status": latest.get("status") or "idle",
        "message": latest.get("message") or ("正在扫描" if running else "空闲"),
        "operation_id": latest.get("id") or "",
        "updated_at": latest.get("updated_at") or latest.get("finished_at") or "",
    }

def console_overview(
    queue_items: list[dict[str, Any]],
    scheduled_jobs: list[dict[str, Any]],
    operations: list[dict[str, Any]],
    server_logs: list[str],
) -> dict[str, Any]:
    business_queue_items = [item for item in queue_items if not item.get("system_queue")]
    queue_total = len(business_queue_items)
    running_queue_count = sum(1 for item in business_queue_items if item.get("queue_state") == "running" or int(item.get("running", 0) or 0) > 0)
    pending_queue_count = sum(1 for item in business_queue_items if int(item.get("pending", 0) or 0) > 0)
    failed_queue_count = sum(1 for item in business_queue_items if int(item.get("failed", 0) or 0) > 0)
    waiting_retry_count = sum(int(item.get("waiting", 0) or 0) for item in business_queue_items)
    pending_task_count = sum(int(item.get("pending", 0) or 0) for item in business_queue_items)
    running_task_count = sum(int(item.get("running", 0) or 0) for item in business_queue_items)
    failed_task_count = sum(int(item.get("failed", 0) or 0) for item in business_queue_items)
    running_operation_count = sum(1 for item in operations if str(item.get("status", "")) == "running")
    failed_operation_count = sum(1 for item in operations if str(item.get("status", "")) == "failed")
    scheduled_failed_count = sum(1 for item in scheduled_jobs if str(item.get("last_status", "")) == "failed")
    scheduled_running_count = sum(1 for item in scheduled_jobs if str(item.get("last_status", "")) == "running")
    recent_error_count = sum(1 for line in server_logs if "[ERROR]" in str(line))
    recent_warn_count = sum(1 for line in server_logs if "[WARN]" in str(line))
    active_queue_names = [
        str(item.get("name", ""))
        for item in business_queue_items
        if item.get("queue_state") in {"running", "debouncing", "rerun_pending", "cooldown"}
        or int(item.get("pending", 0) or 0) > 0
        or int(item.get("failed", 0) or 0) > 0
    ][:6]
    return {
        "queue_total": queue_total,
        "running_queue_count": running_queue_count,
        "pending_queue_count": pending_queue_count,
        "failed_queue_count": failed_queue_count,
        "waiting_retry_count": waiting_retry_count,
        "pending_task_count": pending_task_count,
        "running_task_count": running_task_count,
        "failed_task_count": failed_task_count,
        "running_operation_count": running_operation_count,
        "failed_operation_count": failed_operation_count,
        "scheduled_running_count": scheduled_running_count,
        "scheduled_failed_count": scheduled_failed_count,
        "recent_error_count": recent_error_count,
        "recent_warn_count": recent_warn_count,
        "active_queue_names": active_queue_names,
    }

def scheduled_jobs_summary() -> list[dict[str, Any]]:
    snapshot = runtime_store.snapshot()
    latest_runs: dict[str, dict[str, Any]] = {}
    for run in snapshot.get("scheduler_runs", []):
        job_key = str(run.get("job_key") or "")
        if job_key and job_key not in latest_runs:
            latest_runs[job_key] = dict(run)
    result = []
    for job in sorted(snapshot.get("scheduler", []), key=lambda item: str(item.get("job_key") or "")):
        if str(job.get("job_key") or "") == "queue_dispatch":
            continue
        item = dict(job)
        item.setdefault("id", 0)
        item.setdefault("job_type", "runtime")
        item.setdefault("enabled", 1)
        item.setdefault("last_status", "idle")
        item["latest_run"] = latest_runs.get(str(item.get("job_key") or ""), {})
        result.append(item)
    return result

def queue_detail_map() -> dict[str, dict[str, Any]]:
    snapshot = runtime_store.snapshot()
    details = dict(snapshot.get("queue_details") or {})
    for alias, canonical in QUEUE_KEY_ALIASES.items():
        if canonical in details and alias not in details:
            details[alias] = details[canonical]
    details.setdefault("rss", {"items": []})
    details.setdefault("processor", {"items": []})
    hydrate_queue_item_titles(details)
    return details

def hydrate_queue_item_titles(details: dict[str, dict[str, Any]]) -> None:
    items: list[dict[str, Any]] = []
    for detail in details.values():
        for item in detail.get("items", []):
            if isinstance(item, dict):
                items.append(item)
    if not items:
        return

    entry_ids = {int(item.get("entry_id") or 0) for item in items if int(item.get("entry_id") or 0) > 0}
    release_ids = {int(item.get("release_id") or 0) for item in items if int(item.get("release_id") or 0) > 0}
    candidate_ids = {int(item.get("candidate_id") or 0) for item in items if int(item.get("candidate_id") or 0) > 0}
    download_artifact_ids = {
        int(item.get("download_artifact_id") or item.get("subject_id") or 0)
        for item in items
        if item.get("subject_type") == "download_artifact" and int(item.get("download_artifact_id") or item.get("subject_id") or 0) > 0
    }
    local_asset_ids = {
        int(item.get("local_asset_id") or item.get("subject_id") or 0)
        for item in items
        if item.get("subject_type") == "local_asset" and int(item.get("local_asset_id") or item.get("subject_id") or 0) > 0
    }

    entry_titles: dict[int, dict[str, Any]] = {}
    release_titles: dict[int, dict[str, Any]] = {}
    candidate_titles: dict[int, dict[str, Any]] = {}
    download_artifact_titles: dict[int, dict[str, Any]] = {}
    local_asset_titles: dict[int, dict[str, Any]] = {}

    def placeholders(values: set[int]) -> str:
        return ",".join("?" for _ in values)

    with connect() as conn:
        if entry_ids:
            rows = conn.execute(
                f"""
                SELECT id, display_title, title_cn, title_root, domain_kind
                FROM entries
                WHERE id IN ({placeholders(entry_ids)})
                """,
                tuple(entry_ids),
            ).fetchall()
            entry_titles = {int(row["id"]): dict(row) for row in rows}
        if release_ids:
            rows = conn.execute(
                f"""
                SELECT r.id, r.entry_id, r.title AS release_title, r.episode_number,
                  e.display_title, e.title_cn, e.title_root, e.domain_kind
                FROM releases r
                JOIN entries e ON e.id=r.entry_id
                WHERE r.id IN ({placeholders(release_ids)})
                """,
                tuple(release_ids),
            ).fetchall()
            release_titles = {int(row["id"]): dict(row) for row in rows}
        if candidate_ids:
            rows = conn.execute(
                f"""
                SELECT id, title, series_title, episode_number, status
                FROM rss_candidates
                WHERE id IN ({placeholders(candidate_ids)})
                """,
                tuple(candidate_ids),
            ).fetchall()
            candidate_titles = {int(row["id"]): dict(row) for row in rows}
        if download_artifact_ids:
            rows = conn.execute(
                f"""
                SELECT ca.id, ca.release_id, ca.entry_id, ca.artifact_name, ca.episode_number,
                  e.display_title, e.title_cn, e.title_root, e.domain_kind
                FROM download_artifacts ca
                JOIN entries e ON e.id=ca.entry_id
                WHERE ca.id IN ({placeholders(download_artifact_ids)})
                """,
                tuple(download_artifact_ids),
            ).fetchall()
            download_artifact_titles = {int(row["id"]): dict(row) for row in rows}
        if local_asset_ids:
            rows = conn.execute(
                f"""
                SELECT la.id, la.release_id, la.entry_id, la.local_path, la.episode_number,
                  e.display_title, e.title_cn, e.title_root, e.domain_kind
                FROM local_assets la
                JOIN entries e ON e.id=la.entry_id
                WHERE la.id IN ({placeholders(local_asset_ids)})
                """,
                tuple(local_asset_ids),
            ).fetchall()
            local_asset_titles = {int(row["id"]): dict(row) for row in rows}

    for item in items:
        subject_type = str(item.get("subject_type") or "")
        entry_id = int(item.get("entry_id") or 0)
        release_id = int(item.get("release_id") or 0)
        candidate_id = int(item.get("candidate_id") or 0)
        row: dict[str, Any] = {}
        if release_id and release_id in release_titles:
            row = release_titles[release_id]
        elif entry_id and entry_id in entry_titles:
            row = entry_titles[entry_id]
        elif candidate_id and candidate_id in candidate_titles:
            row = candidate_titles[candidate_id]
        elif subject_type == "download_artifact":
            row = download_artifact_titles.get(int(item.get("download_artifact_id") or item.get("subject_id") or 0), {})
        elif subject_type == "local_asset":
            row = local_asset_titles.get(int(item.get("local_asset_id") or item.get("subject_id") or 0), {})
        if not row:
            continue
        title = str(row.get("display_title") or row.get("title_cn") or row.get("series_title") or row.get("title_root") or row.get("title") or "").strip()
        release_title = str(row.get("release_title") or row.get("artifact_name") or row.get("local_path") or row.get("title") or title).strip()
        item["display_title"] = title or item.get("display_title") or ""
        item["title_cn"] = title or item.get("title_cn") or ""
        item["release_title"] = release_title or item.get("release_title") or ""
        item["entry_id"] = int(row.get("entry_id") or entry_id or 0)
        item["episode_number"] = item.get("episode_number") or row.get("episode_number") or ""
        item["domain_kind"] = row.get("domain_kind") or item.get("domain_kind") or ""

def console_sections() -> list[dict[str, Any]]:
    return [
        {"key": "queues", "name": "队列", "kind": "group"},
        {"key": "queue:rss", "name": "RSS 扫描", "kind": "queue", "queue_key": "rss"},
        {"key": "queue:processor", "name": "流水线处理器", "kind": "queue", "queue_key": "processor"},
        {"key": "queue:mikan_match", "name": "Mikan 匹配", "kind": "queue", "queue_key": "mikan_match"},
        {"key": "queue:metadata", "name": "元数据", "kind": "queue", "queue_key": "metadata"},
        {"key": "queue:selection", "name": "自动选集", "kind": "queue", "queue_key": "selection"},
        {"key": "queue:backfill", "name": "整季补全", "kind": "queue", "queue_key": "backfill"},
        {"key": "queue:download", "name": "下载到本地", "kind": "queue", "queue_key": "download"},
        {"key": "queue:local_presence", "name": "本地存在性检查", "kind": "queue", "queue_key": "local_presence"},
        {"key": "queue:cleanup", "name": "清理", "kind": "queue", "queue_key": "cleanup"},
        {"key": "scheduler", "name": "定时任务", "kind": "group"},
        {"key": "scheduler:rss_scan", "name": "RSS 定时扫描", "kind": "scheduled", "job_key": "rss_scan"},
        {"key": "logs", "name": "服务日志", "kind": "logs"},
        {"key": "maintenance", "name": "维护", "kind": "maintenance"},
    ]

def dashboard_data() -> dict[str, Any]:
    settings = get_settings()
    recent_cutoff = datetime.now(timezone.utc).timestamp() - 7 * 24 * 60 * 60
    calendar_cutoff = datetime.now(timezone.utc).timestamp() - 28 * 24 * 60 * 60
    with connect() as conn:
        seasonal_items = conn.execute(
            """
            SELECT e.id,
              e.work_id,
              e.media_type,
              e.region,
              e.source_provider,
              e.metadata_provider,
              e.external_id,
              e.target_library_id,
              e.genres_json,
              e.tags_json,
              e.display_title,
              e.title_root,
              e.poster_url,
              e.entry_kind,
              e.season_label,
              e.arc_label,
              e.part_label,
              e.special_label,
              e.title_cn,
              e.poster_url,
              e.bangumi_id,
              e.tmdb_id,
              e.bangumi_score,
              e.tmdb_score,
              e.year,
              e.month,
              e.season_number,
              w.title_root AS work_title,
              COUNT(DISTINCT ep.id) AS episode_count,
              COUNT(DISTINCT r.id) AS release_count,
              COUNT(DISTINCT r.subtitle_group) AS group_count,
              COUNT(DISTINCT r.resolution) AS resolution_count,
              COUNT(DISTINCT r.language) AS language_count,
              GROUP_CONCAT(DISTINCT r.subtitle_group) AS subtitle_groups,
              GROUP_CONCAT(DISTINCT r.resolution) AS resolutions,
              GROUP_CONCAT(DISTINCT r.language) AS languages,
              COUNT(DISTINCT CASE WHEN cs.status IN ('submitted','running','completed') THEN cs.id END) AS downloaded_count,
              COUNT(DISTINCT ca.id) AS download_artifact_count,
              COUNT(DISTINCT CASE WHEN COALESCE(ep.watchable, 0)=1 THEN ep.id END) AS local_asset_count
            FROM entries e
            JOIN seasonal_entries se ON se.entry_id=e.id
            JOIN works w ON w.id=e.work_id
            LEFT JOIN episodes ep ON ep.entry_id=e.id
            LEFT JOIN releases r ON r.entry_id=e.id
            LEFT JOIN download_jobs cs ON cs.release_id=r.id
            LEFT JOIN download_artifacts ca ON ca.release_id=r.id
            LEFT JOIN local_assets la ON la.release_id=r.id AND la.status='synced'
            WHERE COALESCE(e.hidden, 0)=0
              AND e.bangumi_id != ''
              AND COALESCE(se.following, 1)=1
              AND COALESCE(se.archived, 0)=0
            GROUP BY e.id
            ORDER BY e.updated_at DESC
            """
        ).fetchall()
        library_items = conn.execute(
            """
            SELECT e.id,
              e.work_id,
              e.media_type,
              e.region,
              e.source_provider,
              e.metadata_provider,
              e.external_id,
              e.target_library_id,
              e.genres_json,
              e.tags_json,
              e.display_title,
              e.title_root,
              e.poster_url,
              e.entry_kind,
              e.season_label,
              e.arc_label,
              e.part_label,
              e.special_label,
              e.title_cn,
              e.bangumi_id,
              e.tmdb_id,
              e.bangumi_score,
              e.tmdb_score,
              e.year,
              e.month,
              e.season_number,
              w.title_root AS work_title,
              COUNT(DISTINCT ep.id) AS episode_count,
              COUNT(DISTINCT r.id) AS release_count,
              COUNT(DISTINCT ca.id) AS download_artifact_count,
              COUNT(DISTINCT CASE WHEN COALESCE(ep.watchable, 0)=1 THEN ep.id END) AS local_asset_count
            FROM entries e
            LEFT JOIN library_entries le ON le.entry_id=e.id
            JOIN works w ON w.id=e.work_id
            LEFT JOIN episodes ep ON ep.entry_id=e.id
            LEFT JOIN releases r ON r.entry_id=e.id
            LEFT JOIN download_artifacts ca ON ca.release_id=r.id
            LEFT JOIN local_assets la ON la.release_id=r.id AND la.status='synced'
            WHERE COALESCE(e.hidden, 0)=0
            GROUP BY e.id
            ORDER BY e.updated_at DESC
            """
        ).fetchall()
        seasonal_sync_calendar = conn.execute(
            """
            SELECT la.id,
              e.id AS entry_id,
              la.local_path,
              la.updated_at AS synced_at,
              ca.episode_number,
              e.display_title,
              e.title_root,
              e.poster_url,
              e.entry_kind,
              e.season_label,
              e.arc_label,
              e.part_label,
              e.special_label,
              w.title_root AS work_title
            FROM local_assets la
            JOIN download_artifacts ca ON ca.id=la.download_artifact_id
            JOIN releases r ON r.id=la.release_id
            JOIN entries e ON e.id=r.entry_id
            JOIN seasonal_entries se ON se.entry_id=e.id
            JOIN works w ON w.id=e.work_id
            WHERE la.status='synced'
              AND COALESCE(e.hidden, 0)=0
              AND COALESCE(se.following, 1)=1
              AND COALESCE(se.archived, 0)=0
              AND strftime('%s', la.updated_at) >= ?
            ORDER BY la.updated_at DESC
            LIMIT 120
            """,
            (int(recent_cutoff),),
        ).fetchall()
        seasonal_update_calendar = conn.execute(
            """
            SELECT
              e.id AS entry_id,
              e.display_title,
              e.title_root,
              e.poster_url,
              e.entry_kind,
              e.season_label,
              e.arc_label,
              e.part_label,
              e.special_label,
              w.title_root AS work_title,
              r.episode_number,
              MAX(COALESCE(la.updated_at, ca.updated_at, r.updated_at, r.created_at)) AS updated_at,
              MAX(CASE WHEN la.status='synced' THEN 1 ELSE 0 END) AS synced
            FROM releases r
            JOIN entries e ON e.id=r.entry_id
            JOIN seasonal_entries se ON se.entry_id=e.id
            JOIN works w ON w.id=e.work_id
            LEFT JOIN download_artifacts ca ON ca.release_id=r.id
            LEFT JOIN local_assets la ON la.release_id=r.id
            WHERE COALESCE(e.hidden, 0)=0
              AND COALESCE(se.following, 1)=1
              AND COALESCE(se.archived, 0)=0
              AND la.status='synced'
              AND strftime('%s', COALESCE(la.updated_at, ca.updated_at, r.updated_at, r.created_at)) >= ?
            GROUP BY e.id, r.episode_number
            ORDER BY updated_at DESC
            LIMIT 160
            """,
            (int(calendar_cutoff),),
        ).fetchall()
        recent_synced_entries = conn.execute(
            """
            SELECT
              e.id AS entry_id,
              e.display_title,
              e.title_root,
              e.poster_url,
              e.entry_kind,
              e.season_label,
              e.arc_label,
              e.part_label,
              e.special_label,
              w.title_root AS work_title,
              MAX(la.updated_at) AS synced_at,
              COUNT(DISTINCT la.id) AS synced_count
            FROM local_assets la
            JOIN entries e ON e.id=la.entry_id
            JOIN seasonal_entries se ON se.entry_id=e.id
            JOIN works w ON w.id=e.work_id
            WHERE la.status='synced'
              AND COALESCE(e.hidden, 0)=0
              AND COALESCE(se.following, 1)=1
              AND COALESCE(se.archived, 0)=0
              AND strftime('%s', la.updated_at) >= ?
            GROUP BY e.id
            ORDER BY synced_at DESC
            LIMIT 12
            """,
            (int(recent_cutoff),),
        ).fetchall()
    queue_items = queue_summary(settings)
    runtime_snapshot = runtime_store.snapshot()
    scheduled_jobs = scheduled_jobs_summary()
    scheduled_runs = list(runtime_snapshot.get("scheduler_runs") or [])[-40:]
    server_logs = list(runtime_snapshot.get("logs") or [])[-160:]
    operations_list = [
        dict(item)
        for item in runtime_snapshot.get("operations", [])
        if str(item.get("status") or "") in {"running", "failed", "cancelled"}
    ][:20]
    download_tasks = list_download_tasks()
    queue_details = queue_detail_map()
    seasonal_rows = [
        enrich_catalog_entry(summarize_seasonal_entry(row))
        for row in rows_to_dicts(seasonal_items)
    ]
    library_rows = [enrich_catalog_entry(row) for row in rows_to_dicts(library_items)]
    seasonal_calendar_rows = [enrich_catalog_entry(row) for row in rows_to_dicts(seasonal_sync_calendar)]
    seasonal_update_rows = [enrich_catalog_entry(row) for row in rows_to_dicts(seasonal_update_calendar)]
    recent_synced_rows = [enrich_catalog_entry(row) for row in rows_to_dicts(recent_synced_entries)]
    return {
        "seasonal_items": seasonal_rows,
        "library_items": library_rows,
        "seasonal_sync_calendar": seasonal_calendar_rows,
        "seasonal_update_calendar": seasonal_update_rows,
        "recent_synced_seasonal_entries": recent_synced_rows,
        "operations": operations_list,
        "scheduled_jobs": scheduled_jobs,
        "scheduled_runs": scheduled_runs,
        "queue_summary": queue_items,
        "queue_details": queue_details,
        "scanner_status": scanner_status(runtime_snapshot),
        "download_tasks": download_tasks,
        "download_overview": download_overview(download_tasks),
        "pipelines": pipeline_overview(),
        "console_sections": console_sections(),
        "server_logs": server_logs,
        "console_overview": console_overview(queue_items, scheduled_jobs, operations_list, server_logs),
    }

async def cached_dashboard_data() -> dict[str, Any]:
    async with dashboard_cache_lock:
        cached = dashboard_cache.get("data")
        ts = float(dashboard_cache.get("ts") or 0)
        if cached is not None and time.monotonic() - ts < DASHBOARD_CACHE_TTL:
            return cached
        data = await run_in_threadpool(dashboard_data)
        dashboard_cache["data"] = data
        dashboard_cache["ts"] = time.monotonic()
        return data
