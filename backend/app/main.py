from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import APP_DIR
from .db import connect, get_settings, init_db, log, merge_duplicate_series, now, save_settings
from .library import bool_setting
from .metadata import generate_nfo_for_series, refresh_series_metadata
from .scanner import mark_selected_releases, poll_submitted_tasks, process_tasks, queue_release, resolve_series_choice, scan_and_queue
from .sync_service import backfill_cloud_assets_from_completed_tasks, cancel_sync_for_series, process_sync_tasks, queue_sync_for_series


scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")


class SettingsPayload(BaseModel):
    rss_url: str = ""
    rss_proxy: str = ""
    scan_interval_minutes: int = 60
    auto_scan: bool = False
    auto_download_unique: bool = True
    auto_download_by_priority: bool = True
    default_backfill: str = "none"
    subtitle_priority: list[str] = []
    resolution_priority: list[str] = []
    language_priority: list[str] = []
    pikpak_auth_mode: str = "token"
    pikpak_username: str = ""
    pikpak_password: str = ""
    pikpak_access_token: str = ""
    pikpak_refresh_token: str = ""
    pikpak_proxy: str = ""
    library_root: str = "/Anime"
    local_library_root: str = "/media/pikpak-anime"
    auto_sync_following: bool = True
    nfo_output_root: str = ""
    series_dir_template: str = ""
    season_dir_template: str = ""
    episode_name_template: str = ""


class SeriesPayload(BaseModel):
    title_cn: str = ""
    bangumi_id: str = ""
    tmdb_id: str = ""
    year: int = 0
    season_number: int = 1
    auto_download: str = "inherit"
    selected_group: str = ""
    selected_resolution: str = ""
    backfill_mode: str = "inherit"


def row_to_dict(row: Any) -> dict[str, Any]:
    return dict(row) if row is not None else {}


def rows_to_dicts(rows: list[Any]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def split_setting(value: str) -> list[str]:
    return [x.strip() for x in (value or "").splitlines() if x.strip()]


def settings_response() -> dict[str, Any]:
    settings = get_settings()
    result: dict[str, Any] = dict(settings)
    result["scan_interval_minutes"] = int(settings.get("scan_interval_minutes") or 60)
    result["auto_scan"] = bool_setting(settings.get("auto_scan", "false"))
    result["auto_download_unique"] = bool_setting(settings.get("auto_download_unique", "true"))
    result["auto_download_by_priority"] = bool_setting(settings.get("auto_download_by_priority", "true"))
    result["auto_sync_following"] = bool_setting(settings.get("auto_sync_following", "true"))
    result["subtitle_priority"] = split_setting(settings.get("subtitle_priority", ""))
    result["resolution_priority"] = split_setting(settings.get("resolution_priority", ""))
    result["language_priority"] = split_setting(settings.get("language_priority", ""))
    return result


def reschedule() -> None:
    scheduler.remove_all_jobs()
    settings = get_settings()
    minutes = max(1, int(settings.get("scan_interval_minutes") or 60))
    scheduler.add_job(lambda: asyncio.create_task(scheduled_scan()), "interval", minutes=minutes)


async def scheduled_scan() -> None:
    settings = get_settings()
    if bool_setting(settings.get("auto_scan", "false")):
        await scan_and_queue(settings)
    await poll_submitted_tasks(settings)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    reschedule()
    scheduler.start()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="AutoAnime", lifespan=lifespan)


def dashboard_data() -> dict[str, Any]:
    settings = get_settings()
    backfilled = backfill_cloud_assets_from_completed_tasks(settings)
    if backfilled:
        log("info", f"已补齐云盘资源状态: {backfilled} 个")
    with connect() as conn:
        series = conn.execute(
            """
            SELECT s.*,
              COUNT(DISTINCT e.id) AS episode_count,
              COUNT(DISTINCT r.id) AS release_count,
              COUNT(DISTINCT r.subtitle_group) AS group_count,
              COUNT(DISTINCT r.resolution) AS resolution_count,
              COUNT(DISTINCT r.language) AS language_count,
              COUNT(DISTINCT CASE WHEN dt.status IN ('submitted','completed') THEN dt.id END) AS downloaded_count,
              COUNT(DISTINCT ca.id) AS cloud_asset_count,
              COUNT(DISTINCT la.id) AS local_asset_count,
              COALESCE(MAX(sr.sync_enabled), 0) AS sync_enabled,
              COALESCE(MAX(sr.auto_sync_following), 0) AS auto_sync_following
            FROM series s
            LEFT JOIN episodes e ON e.series_id=s.id
            LEFT JOIN releases r ON r.series_id=s.id
            LEFT JOIN download_tasks dt ON dt.series_id=s.id
            LEFT JOIN cloud_assets ca ON ca.series_id=s.id
            LEFT JOIN local_assets la ON la.series_id=s.id AND la.status='synced'
            LEFT JOIN sync_rules sr ON sr.series_id=s.id
            WHERE COALESCE(s.hidden, 0)=0
            GROUP BY s.id
            ORDER BY s.updated_at DESC
            """
        ).fetchall()
        tasks = conn.execute(
            """
            SELECT dt.*, s.title_cn, r.episode_number, r.subtitle_group, r.resolution, r.language, r.title AS release_title
            FROM download_tasks dt
            JOIN series s ON s.id=dt.series_id
            JOIN releases r ON r.id=dt.release_id
            ORDER BY dt.id DESC
            LIMIT 80
            """
        ).fetchall()
        logs = conn.execute("SELECT * FROM logs ORDER BY id DESC LIMIT 80").fetchall()
        cloud_assets = conn.execute(
            """
            SELECT ca.*, s.title_cn
            FROM cloud_assets ca
            JOIN series s ON s.id=ca.series_id
            ORDER BY ca.updated_at DESC
            LIMIT 80
            """
        ).fetchall()
        sync_tasks = conn.execute(
            """
            SELECT st.*, s.title_cn
            FROM sync_tasks st
            JOIN series s ON s.id=st.series_id
            ORDER BY st.updated_at DESC
            LIMIT 80
            """
        ).fetchall()
        sync_rules = conn.execute(
            """
            SELECT sr.*, s.title_cn
            FROM sync_rules sr
            JOIN series s ON s.id=sr.series_id
            WHERE COALESCE(s.hidden, 0)=0
            ORDER BY sr.updated_at DESC
            LIMIT 200
            """
        ).fetchall()
        task_counts = conn.execute(
            "SELECT status, COUNT(*) AS count FROM download_tasks GROUP BY status"
        ).fetchall()
        active_tasks = conn.execute(
            """
            SELECT dt.*, s.title_cn, r.episode_number, r.subtitle_group, r.resolution, r.language, r.title AS release_title
            FROM download_tasks dt
            JOIN series s ON s.id=dt.series_id
            JOIN releases r ON r.id=dt.release_id
            WHERE dt.status IN ('pending', 'running', 'submitted', 'failed')
            ORDER BY
              CASE dt.status
                WHEN 'running' THEN 0
                WHEN 'pending' THEN 1
                WHEN 'submitted' THEN 2
                WHEN 'failed' THEN 3
                ELSE 4
              END,
              dt.updated_at DESC
            LIMIT 20
            """
        ).fetchall()
        calendar = conn.execute(
            """
            SELECT s.title_cn, e.episode_number, e.air_date, e.status
            FROM episodes e
            JOIN series s ON s.id=e.series_id
            WHERE e.air_date != ''
            ORDER BY e.air_date ASC
            LIMIT 80
            """
        ).fetchall()
    return {
        "series": rows_to_dicts(series),
        "tasks": rows_to_dicts(tasks),
        "logs": rows_to_dicts(logs),
        "cloud_assets": rows_to_dicts(cloud_assets),
        "sync_rules": rows_to_dicts(sync_rules),
        "sync_tasks": rows_to_dicts(sync_tasks),
        "calendar": rows_to_dicts(calendar),
        "task_counts": {row["status"]: row["count"] for row in task_counts},
        "active_tasks": rows_to_dicts(active_tasks),
    }


@app.get("/api/dashboard")
async def api_dashboard() -> dict[str, Any]:
    return dashboard_data()


@app.get("/api/settings")
async def api_settings() -> dict[str, Any]:
    return settings_response()


@app.put("/api/settings")
async def api_update_settings(payload: SettingsPayload) -> dict[str, Any]:
    save_settings(
        {
            "rss_url": payload.rss_url.strip(),
            "rss_proxy": payload.rss_proxy.strip(),
            "scan_interval_minutes": payload.scan_interval_minutes,
            "auto_scan": str(payload.auto_scan).lower(),
            "auto_download_unique": str(payload.auto_download_unique).lower(),
            "auto_download_by_priority": str(payload.auto_download_by_priority).lower(),
            "default_backfill": payload.default_backfill,
            "subtitle_priority": "\n".join(payload.subtitle_priority),
            "resolution_priority": "\n".join(payload.resolution_priority),
            "language_priority": "\n".join(payload.language_priority),
            "pikpak_auth_mode": payload.pikpak_auth_mode,
            "pikpak_username": payload.pikpak_username.strip(),
            "pikpak_password": payload.pikpak_password,
            "pikpak_access_token": payload.pikpak_access_token.strip(),
            "pikpak_refresh_token": payload.pikpak_refresh_token.strip(),
            "pikpak_proxy": payload.pikpak_proxy.strip(),
            "library_root": payload.library_root.strip() or "/Anime",
            "local_library_root": payload.local_library_root.strip() or "/media/pikpak-anime",
            "auto_sync_following": str(payload.auto_sync_following).lower(),
            "nfo_output_root": payload.nfo_output_root.strip(),
            "series_dir_template": payload.series_dir_template.strip(),
            "season_dir_template": payload.season_dir_template.strip(),
            "episode_name_template": payload.episode_name_template.strip(),
        }
    )
    reschedule()
    log("info", "全局设置已保存")
    return settings_response()


@app.get("/api/series/{series_id}")
async def api_series(series_id: int) -> dict[str, Any]:
    with connect() as conn:
        series = conn.execute("SELECT * FROM series WHERE id=?", (series_id,)).fetchone()
        releases = conn.execute(
            "SELECT * FROM releases WHERE series_id=? ORDER BY episode_number ASC, id DESC",
            (series_id,),
        ).fetchall()
        tasks = conn.execute(
            "SELECT * FROM download_tasks WHERE series_id=? ORDER BY id DESC",
            (series_id,),
        ).fetchall()
    if not series:
        return {"series": None, "releases": [], "tasks": [], "groups": [], "resolutions": []}
    groups = sorted({r["subtitle_group"] for r in releases if r["subtitle_group"]})
    resolutions = sorted({r["resolution"] for r in releases if r["resolution"]})
    languages = sorted({r["language"] for r in releases if r["language"]})
    return {
        "series": row_to_dict(series),
        "releases": rows_to_dicts(releases),
        "tasks": rows_to_dicts(tasks),
        "groups": groups,
        "resolutions": resolutions,
        "languages": languages,
    }


@app.put("/api/series/{series_id}")
async def api_update_series(series_id: int, payload: SeriesPayload) -> dict[str, Any]:
    with connect() as conn:
        conn.execute(
            """
            UPDATE series
            SET title_cn=?, bangumi_id=?, tmdb_id=?, year=?, season_number=?,
                auto_download=?, selected_group=?, selected_resolution=?,
                backfill_mode=?, updated_at=?
            WHERE id=?
            """,
            (
                payload.title_cn.strip(),
                payload.bangumi_id.strip(),
                payload.tmdb_id.strip(),
                payload.year,
                payload.season_number,
                payload.auto_download,
                payload.selected_group.strip(),
                payload.selected_resolution.strip(),
                payload.backfill_mode,
                now(),
                series_id,
            ),
        )
    log("info", f"番剧设置已保存: {payload.title_cn}")
    with connect() as conn:
        merge_duplicate_series(conn)
    return await api_series(series_id)


@app.delete("/api/series/{series_id}")
async def api_delete_series(series_id: int) -> dict[str, str]:
    with connect() as conn:
        series = conn.execute("SELECT title_cn FROM series WHERE id=?", (series_id,)).fetchone()
        if not series:
            return {"status": "not_found", "message": "番剧不存在"}
        title = series["title_cn"]
        ts = now()
        conn.execute("DELETE FROM releases WHERE series_id=?", (series_id,))
        conn.execute("DELETE FROM episodes WHERE series_id=?", (series_id,))
        conn.execute("DELETE FROM download_tasks WHERE series_id=?", (series_id,))
        conn.execute("DELETE FROM cloud_assets WHERE series_id=?", (series_id,))
        conn.execute("DELETE FROM sync_tasks WHERE series_id=?", (series_id,))
        conn.execute("DELETE FROM local_assets WHERE series_id=?", (series_id,))
        conn.execute("DELETE FROM sync_rules WHERE series_id=?", (series_id,))
        conn.execute(
            "UPDATE series SET hidden=1, updated_at=? WHERE id=?",
            (ts, series_id),
        )
    log("warn", f"已删除误识别番剧: {title}")
    return {"status": "completed", "message": "已删除误识别番剧和关联记录"}


@app.post("/api/scan")
async def api_scan() -> dict[str, str]:
    asyncio.create_task(scan_and_queue(get_settings()))
    return {"status": "started"}


@app.post("/api/tasks/process")
async def api_process_tasks() -> dict[str, str]:
    asyncio.create_task(process_tasks(get_settings()))
    return {"status": "started", "message": "队列处理已启动"}


@app.post("/api/tasks/poll")
async def api_poll_tasks() -> dict[str, str]:
    settings = get_settings()
    asyncio.create_task(poll_submitted_tasks(settings))
    count = backfill_cloud_assets_from_completed_tasks(settings)
    if count:
        log("info", f"已补齐云盘资源状态: {count} 个")
    return {"status": "started", "message": "状态刷新已启动"}


@app.post("/api/sync/tasks/process")
async def api_process_sync_tasks() -> dict[str, str]:
    asyncio.create_task(process_sync_tasks(get_settings()))
    return {"status": "started", "message": "本地同步处理已启动"}


@app.post("/api/tasks/retry-failed")
async def api_retry_failed() -> dict[str, str]:
    with connect() as conn:
        cursor = conn.execute(
            """
            UPDATE download_tasks
            SET status='pending', attempts=0, last_error='', updated_at=?
            WHERE status='failed'
            """,
            (now(),),
        )
        count = cursor.rowcount
    log("info", f"已重置失败任务: {count} 个")
    asyncio.create_task(process_tasks(get_settings()))
    return {"status": "started", "count": str(count), "message": "失败任务已重新入队"}


@app.post("/api/series/{series_id}/download")
async def api_download_series(series_id: int) -> dict[str, str]:
    settings = get_settings()
    with connect() as conn:
        series = conn.execute("SELECT * FROM series WHERE id=?", (series_id,)).fetchone()
        if not series:
            return {"status": "not_found"}
    ids, choice = resolve_series_choice(series_id, settings)
    mark_selected_releases(series_id, ids)
    if not ids:
        message = choice["reason"] or "没有可入云盘发布"
        log("warn", f"手动入云盘跳过: {series['title_cn']} - {message}")
        return {"status": "skipped", "count": "0", "message": message}
    for release_id in ids:
        queue_release(release_id, settings)
    asyncio.create_task(process_tasks(settings))
    return {"status": "queued", "count": str(len(ids)), "message": f"已加入云盘队列: {len(ids)} 条"}


@app.post("/api/releases/{release_id}/download")
async def api_download_release(release_id: int) -> dict[str, str]:
    settings = get_settings()
    queue_release(release_id, settings)
    asyncio.create_task(process_tasks(settings))
    return {"status": "queued"}


@app.post("/api/series/{series_id}/metadata")
async def api_refresh_metadata(series_id: int) -> dict[str, str]:
    settings = get_settings()
    asyncio.create_task(refresh_series_metadata(series_id, settings.get("rss_proxy", "")))
    return {"status": "started"}


@app.post("/api/series/{series_id}/nfo")
async def api_generate_nfo(series_id: int) -> dict[str, str]:
    generate_nfo_for_series(series_id, get_settings())
    return {"status": "generated"}


@app.post("/api/series/{series_id}/sync")
async def api_sync_series(series_id: int) -> dict[str, str]:
    settings = get_settings()
    count, message = queue_sync_for_series(series_id, settings)
    if count > 0:
        asyncio.create_task(process_sync_tasks(settings))
        return {"status": "queued", "count": str(count), "message": message}
    return {"status": "completed", "count": "0", "message": message}


@app.post("/api/series/{series_id}/sync/cancel")
async def api_cancel_sync_series(series_id: int) -> dict[str, str]:
    count, message = cancel_sync_for_series(series_id)
    return {"status": "completed", "count": str(count), "message": message}


frontend_dir = APP_DIR.parent / "frontend_dist"
if frontend_dir.exists():
    app.mount("/assets", StaticFiles(directory=frontend_dir / "assets"), name="assets")


@app.get("/{full_path:path}")
async def spa(full_path: str) -> FileResponse:
    index_file = frontend_dir / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    fallback = APP_DIR / "static" / "missing-frontend.html"
    return FileResponse(fallback)
