from __future__ import annotations

import asyncio
import shutil
import re
import zlib
from pathlib import Path, PurePosixPath

import httpx

from .db import connect, log, now
from .library import render_episode_name, render_season_dir, render_series_dir, target_dir
from .metadata import generate_nfo_for_series
from .parser import normalize_title_key, parse_episode
from .pikpak_service import get_cloud_download_url, list_cloud_files


def task_retry_after_minutes(minutes: int) -> str:
    from datetime import datetime, timedelta, timezone

    return (datetime.now(timezone.utc) + timedelta(minutes=max(1, minutes))).isoformat()


def stale_running_cutoff(minutes: int = 10) -> str:
    from datetime import datetime, timedelta, timezone

    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()


def task_retry_after(settings: dict[str, str], attempts: int) -> str:
    default_minutes = max(5, 5 * max(1, attempts))
    minutes = min(180, default_minutes)
    return task_retry_after_minutes(minutes)


def enqueue_cloud_asset_task(conn, download_task_id: int, ts: str) -> None:
    conn.execute(
        """
        INSERT INTO cloud_asset_tasks
          (download_task_id, status, retry_after, last_error, created_at, updated_at)
        VALUES (?, 'pending', '', '', ?, ?)
        ON CONFLICT(download_task_id) DO UPDATE SET
          status=CASE WHEN cloud_asset_tasks.status='completed' THEN cloud_asset_tasks.status ELSE 'pending' END,
          retry_after='',
          last_error='',
          updated_at=excluded.updated_at
        """,
        (download_task_id, ts, ts),
    )


def cloud_asset_path(task: dict, release: dict, series: dict, settings: dict[str, str]) -> tuple[str, str]:
    name = task.get("normalized_name") or render_episode_name(series, release["episode_number"], "", settings)
    directory = task.get("target_dir") or target_dir(series, settings)
    return str(PurePosixPath(directory) / name), name


def upsert_cloud_asset(task_id: int, settings: dict[str, str]) -> int | None:
    with connect() as conn:
        task = conn.execute("SELECT * FROM download_tasks WHERE id=?", (task_id,)).fetchone()
        if not task:
            return None
        release = conn.execute("SELECT * FROM releases WHERE id=?", (task["release_id"],)).fetchone()
        series = conn.execute("SELECT * FROM series WHERE id=?", (task["series_id"],)).fetchone()
        if not release or not series:
            return None
        if not series["bangumi_id"]:
            log("warn", f"云盘资源登记跳过: {series['title_cn']} - 缺少 Bangumi ID")
            return None
        cloud_path, cloud_name = cloud_asset_path(dict(task), dict(release), dict(series), settings)
        ts = now()
        existing_by_file = None
        if task["pikpak_file_id"]:
            existing_by_file = conn.execute(
                "SELECT id FROM cloud_assets WHERE provider='pikpak' AND provider_file_id=?",
                (task["pikpak_file_id"],),
            ).fetchone()
        if existing_by_file:
            conn.execute(
                """
                UPDATE cloud_assets
                SET task_id=?, release_id=?, series_id=?, episode_number=?,
                    cloud_path=?, cloud_name=?, status='available', updated_at=?
                WHERE id=?
                """,
                (
                    task["id"],
                    task["release_id"],
                    task["series_id"],
                    release["episode_number"],
                    cloud_path,
                    cloud_name,
                    ts,
                    existing_by_file["id"],
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO cloud_assets
                  (task_id, release_id, series_id, episode_number, provider, provider_file_id,
                   cloud_path, cloud_name, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'pikpak', ?, ?, ?, 'available', ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                  provider_file_id=excluded.provider_file_id,
                  cloud_path=excluded.cloud_path,
                  cloud_name=excluded.cloud_name,
                  status='available',
                  updated_at=excluded.updated_at
                """,
                (
                    task["id"],
                    task["release_id"],
                    task["series_id"],
                    release["episode_number"],
                    task["pikpak_file_id"],
                    cloud_path,
                    cloud_name,
                    ts,
                    ts,
                ),
            )
        asset = conn.execute("SELECT id FROM cloud_assets WHERE task_id=?", (task["id"],)).fetchone()
    if asset:
        log("info", f"云盘资源已入库: {cloud_name}")
        return int(asset["id"])
    return None


async def process_cloud_asset_tasks(settings: dict[str, str], limit: int = 20, force: bool = False) -> tuple[int, int]:
    with connect() as conn:
        conn.execute(
            """
            UPDATE cloud_asset_tasks
            SET status='pending', last_error='上次云盘资源登记中断，已自动放回待处理', updated_at=?
            WHERE status='running' AND updated_at < ?
            """,
            (now(), stale_running_cutoff()),
        )
        if force:
            conn.execute(
                """
                UPDATE cloud_asset_tasks
                SET retry_after='', updated_at=?
                WHERE status='pending' AND retry_after != ''
                """,
                (now(),),
            )
        rows = conn.execute(
            """
            SELECT cat.*, dt.pikpak_file_id, r.title
            FROM cloud_asset_tasks cat
            JOIN download_tasks dt ON dt.id=cat.download_task_id
            JOIN releases r ON r.id=dt.release_id
            JOIN series s ON s.id=dt.series_id
            WHERE cat.status IN ('pending', 'failed')
              AND (cat.retry_after='' OR cat.retry_after <= ?)
              AND dt.status='completed'
              AND dt.pikpak_file_id != ''
              AND s.bangumi_id != ''
            ORDER BY cat.id ASC
            LIMIT ?
            """,
            (now(), limit),
        ).fetchall()

    completed = 0
    failed = 0
    touched_series: set[int] = set()
    for row in rows:
        with connect() as conn:
            conn.execute(
                "UPDATE cloud_asset_tasks SET status='running', attempts=attempts+1, updated_at=? WHERE id=?",
                (now(), row["id"]),
            )
        try:
            asset_id = upsert_cloud_asset(int(row["download_task_id"]), settings)
            if not asset_id:
                raise RuntimeError("云盘资源登记失败：缺少任务、发布、番剧或 file_id")
        except Exception as exc:
            failed += 1
            with connect() as conn:
                conn.execute(
                    """
                    UPDATE cloud_asset_tasks
                    SET status='pending', retry_after=?, last_error=?, updated_at=?
                    WHERE id=?
                    """,
                    (task_retry_after(settings, int(row["attempts"] or 0) + 1), str(exc)[:2000], now(), row["id"]),
                )
            log("error", f"云盘资源登记失败: {row['title']} - {exc}")
            continue

        with connect() as conn:
            conn.execute(
                "UPDATE cloud_asset_tasks SET status='completed', retry_after='', last_error='', updated_at=? WHERE id=?",
                (now(), row["id"]),
            )
            series = conn.execute(
                "SELECT series_id FROM cloud_assets WHERE id=?",
                (asset_id,),
            ).fetchone()
        if series:
            touched_series.add(int(series["series_id"]))
        completed += 1

    if completed:
        reconcile_sync_intents(settings)
    return completed, failed


def ensure_sync_rule(series_id: int, settings: dict[str, str], enabled: bool | None = None) -> None:
    ts = now()
    auto_sync = settings.get("auto_sync_following", "true").lower() == "true"
    sync_enabled = auto_sync if enabled is None else enabled
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO sync_rules
              (series_id, sync_enabled, auto_sync_following, local_root, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(series_id) DO UPDATE SET
              local_root=CASE WHEN sync_rules.local_root='' THEN excluded.local_root ELSE sync_rules.local_root END,
              updated_at=excluded.updated_at
            """,
            (
                series_id,
                1 if sync_enabled else 0,
                1 if auto_sync else 0,
                settings.get("local_library_root") or "/media/pikpak-anime",
                ts,
                ts,
            ),
        )


def local_episode_path(cloud_asset: dict, series: dict, settings: dict[str, str]) -> str:
    root = Path(settings.get("local_library_root") or "/media/pikpak-anime")
    series_dir = render_series_dir(series, settings)
    season_dir = render_season_dir(int(series.get("season_number") or 1), settings)
    suffix = Path(cloud_asset.get("cloud_name") or "").suffix
    filename = cloud_asset.get("cloud_name") or render_episode_name(
        series,
        int(cloud_asset.get("episode_number") or 0),
        "",
        settings,
    )
    if suffix and not filename.endswith(suffix):
        filename = f"{filename}{suffix}"
    return str(root / series_dir / season_dir / filename)


def queue_sync_for_series(series_id: int, settings: dict[str, str]) -> tuple[int, str]:
    ensure_sync_rule(series_id, settings, enabled=True)
    with connect() as conn:
        assets = conn.execute(
            """
            SELECT ca.*, s.title_cn
            FROM cloud_assets ca
            JOIN series s ON s.id=ca.series_id
            WHERE ca.series_id=? AND ca.status='available'
            ORDER BY ca.episode_number ASC
            """,
            (series_id,),
        ).fetchall()
        if not assets:
            return 0, "已开启本地同步；云盘资源入库后会自动同步"
        ts = now()
        queued = 0
        for asset in assets:
            series = conn.execute("SELECT * FROM series WHERE id=?", (asset["series_id"],)).fetchone()
            target = local_episode_path(dict(asset), dict(series), settings)
            conn.execute(
                """
                INSERT INTO sync_tasks
                  (cloud_asset_id, release_id, series_id, status, source_path, target_path,
                   created_at, updated_at)
                VALUES (?, ?, ?, 'pending', ?, ?, ?, ?)
                ON CONFLICT(cloud_asset_id, sync_direction) DO UPDATE SET
                  status=CASE WHEN sync_tasks.status='synced' THEN sync_tasks.status ELSE 'pending' END,
                  source_path=excluded.source_path,
                  target_path=excluded.target_path,
                  retry_after='',
                  last_error='',
                  updated_at=excluded.updated_at
                """,
                (
                    asset["id"],
                    asset["release_id"],
                    asset["series_id"],
                    asset["cloud_path"],
                    target,
                    ts,
                    ts,
                ),
            )
            task_row = conn.execute(
                """
                SELECT status
                FROM sync_tasks
                WHERE cloud_asset_id=? AND sync_direction='cloud_to_local'
                """,
                (asset["id"],),
            ).fetchone()
            if task_row and task_row["status"] != "synced":
                queued += 1
    return queued, f"已加入本地同步队列: {queued} 条"


def reconcile_sync_intents(settings: dict[str, str]) -> tuple[int, int]:
    auto_sync = settings.get("auto_sync_following", "true").lower() == "true"
    with connect() as conn:
        series_ids = [
            int(row["series_id"])
            for row in conn.execute(
                """
                SELECT DISTINCT ca.series_id
                FROM cloud_assets ca
                JOIN series s ON s.id=ca.series_id
                LEFT JOIN sync_rules sr ON sr.series_id=ca.series_id
                WHERE ca.status='available'
                  AND COALESCE(s.hidden, 0)=0
                  AND s.bangumi_id != ''
                  AND (COALESCE(sr.sync_enabled, 0)=1 OR ?=1)
                """,
                (1 if auto_sync else 0,),
            ).fetchall()
        ]
    queued_total = 0
    for series_id in series_ids:
        queued, _ = queue_sync_for_series(series_id, settings)
        queued_total += queued
    return len(series_ids), queued_total


def backfill_cloud_assets_from_completed_tasks(settings: dict[str, str]) -> int:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT dt.id
            FROM download_tasks dt
            LEFT JOIN cloud_assets ca ON ca.task_id=dt.id
            WHERE dt.status='completed'
              AND dt.pikpak_file_id != ''
              AND ca.id IS NULL
            ORDER BY dt.id ASC
            """
        ).fetchall()
    count = 0
    for row in rows:
        if upsert_cloud_asset(row["id"], settings):
            count += 1
    return count


VIDEO_SUFFIXES = {".mkv", ".mp4", ".avi", ".mov", ".wmv", ".ts", ".m2ts", ".flv", ".webm"}


def cloud_file_id(item: dict) -> str:
    return str(item.get("id") or item.get("file_id") or item.get("fileId") or "")


def synthetic_task_id(file_id: str) -> int:
    return 0 - (zlib.crc32(file_id.encode("utf-8")) % 2147483647) - 1


def match_cloud_file_to_series(item: dict, series_rows: list[dict]) -> dict | None:
    path = str(item.get("cloud_path") or item.get("name") or "")
    match = re.search(r"bangumi[-_ ]?(\d+)", path, re.I)
    if match:
        bangumi_id = match.group(1)
        for series in series_rows:
            if str(series.get("bangumi_id") or "") == bangumi_id:
                return series
    normalized_path = normalize_title_key(path)
    best: dict | None = None
    best_len = 0
    for series in series_rows:
        keys = {
            normalize_title_key(str(series.get("title_cn") or "")),
            normalize_title_key(str(series.get("title_raw") or "")),
        }
        for key in keys:
            if key and key in normalized_path and len(key) > best_len:
                best = series
                best_len = len(key)
    return best


def upsert_scanned_cloud_asset(item: dict, series: dict, settings: dict[str, str]) -> int | None:
    file_id = cloud_file_id(item)
    name = str(item.get("name") or Path(str(item.get("cloud_path") or "")).name)
    path = str(item.get("cloud_path") or name)
    if not file_id or not name:
        return None
    episode_number = parse_episode(name) or parse_episode(path) or 0
    if episode_number <= 0:
        return None
    with connect() as conn:
        release = conn.execute(
            """
            SELECT *
            FROM releases
            WHERE series_id=? AND episode_number=?
            ORDER BY selected DESC, id DESC
            LIMIT 1
            """,
            (series["id"], episode_number),
        ).fetchone()
        if not release:
            guid = f"cloud-import:pikpak:{file_id}"
            conn.execute(
                """
                INSERT INTO episodes
                  (series_id, episode_number, title, status, created_at, updated_at)
                VALUES (?, ?, ?, 'downloaded', ?, ?)
                ON CONFLICT(series_id, episode_number) DO UPDATE SET
                  status='downloaded',
                  updated_at=excluded.updated_at
                """,
                (
                    series["id"],
                    episode_number,
                    f"第{episode_number:02d}话",
                    now(),
                    now(),
                ),
            )
            conn.execute(
                """
                INSERT INTO releases
                  (series_id, episode_number, guid, title, subtitle_group, resolution,
                   language, torrent_url, magnet, published_at, selected, created_at, updated_at)
                VALUES (?, ?, ?, ?, '云盘导入', '', '', '', '', '', 1, ?, ?)
                ON CONFLICT(guid) DO UPDATE SET
                  series_id=excluded.series_id,
                  episode_number=excluded.episode_number,
                  title=excluded.title,
                  updated_at=excluded.updated_at
                """,
                (series["id"], episode_number, guid, name, now(), now()),
            )
            release = conn.execute("SELECT * FROM releases WHERE guid=?", (guid,)).fetchone()
        if not release:
            return None
        ts = now()
        existing = conn.execute(
            "SELECT id FROM cloud_assets WHERE provider='pikpak' AND provider_file_id=?",
            (file_id,),
        ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE cloud_assets
                SET release_id=?, series_id=?, episode_number=?, cloud_path=?, cloud_name=?,
                    status='available', updated_at=?
                WHERE id=?
                """,
                (release["id"], series["id"], episode_number, path, name, ts, existing["id"]),
            )
        else:
            conn.execute(
                """
                INSERT INTO cloud_assets
                  (task_id, release_id, series_id, episode_number, provider, provider_file_id,
                   cloud_path, cloud_name, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'pikpak', ?, ?, ?, 'available', ?, ?)
                """,
                (
                    synthetic_task_id(file_id),
                    release["id"],
                    series["id"],
                    episode_number,
                    file_id,
                    path,
                    name,
                    ts,
                    ts,
                ),
            )
        asset = conn.execute("SELECT id FROM cloud_assets WHERE provider_file_id=?", (file_id,)).fetchone()
    return int(asset["id"]) if asset else None


async def scan_cloud_library(settings: dict[str, str]) -> tuple[int, int]:
    files = await list_cloud_files(settings, settings.get("library_root") or "/Anime")
    with connect() as conn:
        series_rows = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM series WHERE COALESCE(hidden, 0)=0 AND bangumi_id != ''"
            ).fetchall()
        ]
    imported = 0
    skipped = 0
    synced_series: set[int] = set()
    for item in files:
        name = str(item.get("name") or item.get("cloud_path") or "")
        if Path(name).suffix.lower() not in VIDEO_SUFFIXES:
            continue
        series = match_cloud_file_to_series(item, series_rows)
        if not series:
            skipped += 1
            continue
        asset_id = upsert_scanned_cloud_asset(item, series, settings)
        if not asset_id:
            skipped += 1
            continue
        imported += 1
        with connect() as conn:
            rule = conn.execute(
                "SELECT * FROM sync_rules WHERE series_id=?",
                (series["id"],),
            ).fetchone()
        if rule and rule["sync_enabled"]:
            synced_series.add(int(series["id"]))
    for series_id in synced_series:
        queue_sync_for_series(series_id, settings)
    if not synced_series:
        reconcile_sync_intents(settings)
    if synced_series:
        await process_sync_tasks(settings)
    else:
        with connect() as conn:
            pending = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM sync_tasks
                WHERE status IN ('pending','failed')
                  AND (retry_after='' OR retry_after <= ?)
                """,
                (now(),),
            ).fetchone()["count"]
        if pending:
            await process_sync_tasks(settings)
    return imported, skipped


async def download_cloud_file_to_local(file_id: str, source: str, target: str, settings: dict[str, str]) -> None:
    target_path = Path(target)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    if file_id:
        url = await get_cloud_download_url(settings, file_id)
        proxy = settings.get("pikpak_proxy") or None
        async with httpx.AsyncClient(proxy=proxy, timeout=None, follow_redirects=True) as client:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                with target_path.open("wb") as output:
                    async for chunk in response.aiter_bytes():
                        if chunk:
                            output.write(chunk)
        return

    source_path = Path(source)
    target_path = Path(target)
    if not source_path.exists():
        raise RuntimeError("缺少云盘文件 ID，且云盘路径不是本机可访问文件")
    shutil.copy2(source_path, target_path)


async def process_sync_tasks(settings: dict[str, str], limit: int = 5) -> None:
    with connect() as conn:
        conn.execute(
            """
            UPDATE sync_tasks
            SET status='pending', last_error='上次本地同步中断，已自动放回待处理', updated_at=?
            WHERE status='running' AND updated_at < ?
            """,
            (now(), stale_running_cutoff()),
        )
        rows = conn.execute(
            """
            SELECT st.*, ca.cloud_name, ca.provider_file_id
            FROM sync_tasks st
            JOIN cloud_assets ca ON ca.id=st.cloud_asset_id
            WHERE st.status IN ('pending', 'failed')
              AND (st.retry_after='' OR st.retry_after <= ?)
            ORDER BY st.id ASC
            LIMIT ?
            """,
            (now(), limit),
        ).fetchall()

    async def handle(task) -> bool:
        with connect() as conn:
            conn.execute(
                "UPDATE sync_tasks SET status='running', attempts=attempts+1, updated_at=? WHERE id=?",
                (now(), task["id"]),
            )
        try:
            await download_cloud_file_to_local(
                task["provider_file_id"],
                task["source_path"],
                task["target_path"],
                settings,
            )
        except Exception as exc:
            with connect() as conn:
                conn.execute(
                    """
                    UPDATE sync_tasks
                    SET status='pending', retry_after=?, last_error=?, updated_at=?
                    WHERE id=?
                    """,
                    (task_retry_after(settings, int(task["attempts"] or 0) + 1), str(exc)[:2000], now(), task["id"]),
                )
            log("error", f"本地同步失败: {task['cloud_name']} - {exc}")
            return False

        with connect() as conn:
            ts = now()
            conn.execute(
                """
                INSERT INTO local_assets
                  (cloud_asset_id, release_id, series_id, episode_number, local_path,
                   nfo_status, status, created_at, updated_at)
                SELECT id, release_id, series_id, episode_number, ?, 'pending', 'synced', ?, ?
                FROM cloud_assets
                WHERE id=?
                ON CONFLICT(cloud_asset_id) DO UPDATE SET
                  local_path=excluded.local_path,
                  status='synced',
                  updated_at=excluded.updated_at
                """,
                (task["target_path"], ts, ts, task["cloud_asset_id"]),
            )
            conn.execute(
                "UPDATE sync_tasks SET status='synced', retry_after='', last_error='', updated_at=? WHERE id=?",
                (ts, task["id"]),
            )
        nfo_settings = dict(settings)
        nfo_settings["nfo_output_root"] = settings.get("local_library_root") or "/media/pikpak-anime"
        generate_nfo_for_series(task["series_id"], nfo_settings)
        with connect() as conn:
            conn.execute(
                "UPDATE local_assets SET nfo_status='generated', updated_at=? WHERE cloud_asset_id=?",
                (now(), task["cloud_asset_id"]),
            )
        log("info", f"已同步到本地: {task['cloud_name']}")
        return True

    semaphore = asyncio.Semaphore(max(1, min(3, limit)))

    async def limited(task):
        async with semaphore:
            return await handle(task)

    await asyncio.gather(*(limited(task) for task in rows))


def cancel_sync_for_series(series_id: int) -> tuple[int, str]:
    removed = 0
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM local_assets WHERE series_id=? AND status='synced'",
            (series_id,),
        ).fetchall()
        for row in rows:
            path = Path(row["local_path"])
            try:
                if path.exists():
                    path.unlink()
                nfo_path = path.with_suffix(".nfo")
                if nfo_path.exists():
                    nfo_path.unlink()
                removed += 1
            except Exception as exc:
                log("warn", f"删除本地文件失败: {path} - {exc}")
            conn.execute(
                "UPDATE local_assets SET status='removed', updated_at=? WHERE id=?",
                (now(), row["id"]),
            )
        conn.execute(
            "UPDATE sync_rules SET sync_enabled=0, updated_at=? WHERE series_id=?",
            (now(), series_id),
        )
    return removed, f"已取消同步并清理本地文件: {removed} 个"
