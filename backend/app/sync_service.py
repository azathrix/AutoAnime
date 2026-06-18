from __future__ import annotations

import asyncio
import shutil
import re
import zlib
from pathlib import Path, PurePosixPath

import httpx

from .database import connect
from .db import log, now
from .queue_bridge import request_queue_trigger
from .library import render_episode_name, render_season_dir, render_series_dir, target_dir
from .metadata import generate_nfo_for_entry
from .parser import normalize_title_key, parse_episode
from .pikpak_service import get_cloud_download_url, list_cloud_files
from . import rclone_service

cloud_asset_tasks_lock = asyncio.Lock()
sync_tasks_lock = asyncio.Lock()
nfo_tasks_lock = asyncio.Lock()
local_presence_tasks_lock = asyncio.Lock()
sync_plan_tasks_lock = asyncio.Lock()


def resolve_entry_series_id(conn, entry_id: int) -> int:
    row = conn.execute(
        "SELECT series_id FROM releases WHERE entry_id=? ORDER BY id ASC LIMIT 1",
        (entry_id,),
    ).fetchone()
    return int(row["series_id"] or 0) if row else 0


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


def enqueue_nfo_task(conn, local_asset_id: int, release_id: int, series_id: int, entry_id: int, ts: str) -> None:
    conn.execute(
        """
        INSERT INTO nfo_tasks
          (local_asset_id, release_id, series_id, entry_id, status, retry_after, last_error, created_at, updated_at)
        VALUES (?, ?, ?, ?, 'pending', '', '', ?, ?)
        ON CONFLICT(local_asset_id) DO UPDATE SET
          release_id=excluded.release_id,
          series_id=excluded.series_id,
          entry_id=excluded.entry_id,
          status=CASE WHEN nfo_tasks.status='completed' THEN nfo_tasks.status ELSE 'pending' END,
          retry_after='',
          last_error='',
          updated_at=excluded.updated_at
        """,
        (local_asset_id, release_id, series_id, entry_id, ts, ts),
    )
    request_queue_trigger("nfo")


def enqueue_local_presence_task(conn, local_asset_id: int, release_id: int, series_id: int, entry_id: int, ts: str) -> None:
    conn.execute(
        """
        INSERT INTO local_presence_tasks
          (local_asset_id, release_id, series_id, entry_id, status, retry_after, last_error, created_at, updated_at)
        VALUES (?, ?, ?, ?, 'pending', '', '', ?, ?)
        ON CONFLICT(local_asset_id) DO UPDATE SET
          release_id=excluded.release_id,
          series_id=excluded.series_id,
          entry_id=excluded.entry_id,
          status='pending',
          retry_after='',
          last_error='',
          updated_at=excluded.updated_at
        """,
        (local_asset_id, release_id, series_id, entry_id, ts, ts),
    )
    request_queue_trigger("local_presence")


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
    request_queue_trigger("cloud_asset")


def enqueue_sync_plan_task(conn, entry_id: int, ts: str) -> None:
    conn.execute(
        """
        INSERT INTO sync_plan_tasks
          (entry_id, status, retry_after, last_error, created_at, updated_at)
        VALUES (?, 'pending', '', '', ?, ?)
        ON CONFLICT(entry_id) DO UPDATE SET
          status='pending',
          retry_after='',
          last_error='',
          updated_at=excluded.updated_at
        """,
        (entry_id, ts, ts),
    )
    request_queue_trigger("sync_plan")


def enqueue_sync_plan_tasks(entry_ids: list[int], ts: str) -> int:
    unique_ids = sorted({int(entry_id) for entry_id in entry_ids if int(entry_id) > 0})
    if not unique_ids:
        return 0
    with connect() as conn:
        for entry_id in unique_ids:
            enqueue_sync_plan_task(conn, entry_id, ts)
    return len(unique_ids)


def cloud_asset_path(task: dict, release: dict, entry: dict, settings: dict[str, str]) -> tuple[str, str]:
    name = task.get("normalized_name") or render_episode_name(entry, release["episode_number"], "", settings)
    directory = task.get("target_dir") or target_dir(entry, settings)
    return str(PurePosixPath(directory) / name), name


def upsert_cloud_asset(task_id: int, settings: dict[str, str]) -> int | None:
    created = False
    with connect() as conn:
        task = conn.execute("SELECT * FROM download_tasks WHERE id=?", (task_id,)).fetchone()
        if not task:
            return None
        release = conn.execute("SELECT * FROM releases WHERE id=?", (task["release_id"],)).fetchone()
        entry = conn.execute("SELECT * FROM entries WHERE id=?", (task["entry_id"],)).fetchone()
        if not release or not entry:
            return None
        if not entry["bangumi_id"]:
            log("warn", f"云盘资源登记跳过: {entry['display_title']} - 缺少 Bangumi ID")
            return None
        cloud_path, cloud_name = cloud_asset_path(dict(task), dict(release), dict(entry), settings)
        ts = now()
        existing_by_file = None
        if task["pikpak_file_id"]:
            existing_by_file = conn.execute(
                "SELECT id FROM cloud_assets WHERE provider='pikpak' AND provider_file_id=?",
                (task["pikpak_file_id"],),
            ).fetchone()
        existing_by_episode = conn.execute(
            """
            SELECT id
            FROM cloud_assets
            WHERE entry_id=? AND episode_number=? AND provider='pikpak'
            ORDER BY id ASC
            LIMIT 1
            """,
            (task["entry_id"], release["episode_number"]),
        ).fetchone()
        existing_asset = existing_by_file or existing_by_episode
        if existing_asset:
            conn.execute(
                """
                UPDATE cloud_assets
                SET task_id=?, release_id=?, series_id=?, entry_id=?, episode_number=?,
                    provider_file_id=CASE
                      WHEN ?!='' THEN ?
                      ELSE provider_file_id
                    END,
                    cloud_path=CASE
                      WHEN provider_file_id!='' AND ?='' THEN cloud_path
                      ELSE ?
                    END,
                    cloud_name=CASE
                      WHEN provider_file_id!='' AND ?='' THEN cloud_name
                      ELSE ?
                    END,
                    status='available', updated_at=?
                WHERE id=?
                """,
                (
                    task["id"],
                    task["release_id"],
                    task["series_id"],
                    task["entry_id"],
                    release["episode_number"],
                    task["pikpak_file_id"],
                    task["pikpak_file_id"],
                    task["pikpak_file_id"],
                    cloud_path,
                    task["pikpak_file_id"],
                    cloud_name,
                    ts,
                    existing_asset["id"],
                ),
            )
        else:
            created = True
            conn.execute(
                """
                INSERT INTO cloud_assets
                  (task_id, release_id, series_id, entry_id, episode_number, provider, provider_file_id,
                   cloud_path, cloud_name, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'pikpak', ?, ?, ?, 'available', ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                  entry_id=excluded.entry_id,
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
                    task["entry_id"],
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
        if created:
            log("info", f"云盘资源已入库: {cloud_name}")
        return int(asset["id"])
    return None


async def process_cloud_asset_tasks(settings: dict[str, str], limit: int = 20, force: bool = False) -> tuple[int, int]:
    async with cloud_asset_tasks_lock:
        return await _process_cloud_asset_tasks(settings, limit, force)


async def _process_cloud_asset_tasks(settings: dict[str, str], limit: int = 20, force: bool = False) -> tuple[int, int]:
    with connect() as conn:
        conn.execute(
            """
            UPDATE cloud_asset_tasks
            SET status='completed', retry_after='', last_error='', updated_at=?
            WHERE download_task_id IN (
              SELECT cat.download_task_id
              FROM cloud_asset_tasks cat
              JOIN cloud_assets ca ON ca.task_id=cat.download_task_id
            )
            """,
            (now(),),
        )
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
            JOIN entries e ON e.id=dt.entry_id
            JOIN cloud_submissions cs ON cs.download_task_id=dt.id
            WHERE cat.status IN ('pending', 'failed')
              AND (cat.retry_after='' OR cat.retry_after <= ?)
              AND dt.status='completed'
              AND dt.pikpak_file_id != ''
              AND e.bangumi_id != ''
              AND cs.provider='pikpak'
              AND cs.status='completed'
            ORDER BY cat.id ASC
            LIMIT ?
            """,
            (now(), limit),
        ).fetchall()

    completed = 0
    failed = 0
    touched_entries: set[int] = set()
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
            entry = conn.execute(
                "SELECT entry_id FROM cloud_assets WHERE id=?",
                (asset_id,),
            ).fetchone()
        if entry and int(entry["entry_id"] or 0) > 0:
            touched_entries.add(int(entry["entry_id"]))
        completed += 1

    if completed:
        with connect() as conn:
            ts = now()
            for entry_id in touched_entries:
                enqueue_sync_plan_task(conn, entry_id, ts)
    return completed, failed


def ensure_sync_rule(entry_id: int, settings: dict[str, str], enabled: bool | None = None) -> None:
    ts = now()
    auto_sync = settings.get("auto_sync_following", "false").lower() == "true"
    sync_enabled = auto_sync if enabled is None else enabled
    with connect() as conn:
        series_id = resolve_entry_series_id(conn, entry_id)
        conn.execute(
            """
            INSERT INTO sync_rules
              (series_id, entry_id, sync_enabled, auto_sync_following, local_root, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(entry_id) DO UPDATE SET
              series_id=excluded.series_id,
              local_root=CASE WHEN sync_rules.local_root='' THEN excluded.local_root ELSE sync_rules.local_root END,
              updated_at=excluded.updated_at
            """,
            (
                series_id,
                entry_id,
                1 if sync_enabled else 0,
                1 if auto_sync else 0,
                settings.get("local_library_root") or "/media/pikpak-anime",
                ts,
                ts,
            ),
        )


def local_episode_path(cloud_asset: dict, entry: dict, settings: dict[str, str]) -> str:
    root = Path(settings.get("local_library_root") or "/media/pikpak-anime")
    series_dir = render_series_dir(entry, settings)
    season_dir = render_season_dir(int(entry.get("season_number") or 1), settings)
    suffix = Path(cloud_asset.get("cloud_name") or "").suffix
    filename = cloud_asset.get("cloud_name") or render_episode_name(
        entry,
        int(cloud_asset.get("episode_number") or 0),
        "",
        settings,
    )
    if suffix and not filename.endswith(suffix):
        filename = f"{filename}{suffix}"
    return str(root / series_dir / season_dir / filename)


def normalize_local_target_path(target_path: str, source_name: str = "") -> str:
    path = Path(target_path)
    suffix = path.suffix or Path(source_name).suffix
    stem = path.stem
    stem = re.sub(r"\(\d+\)$", "", stem).rstrip()
    normalized = path.with_name(f"{stem}{suffix}")
    return str(normalized)


def materialize_sync_tasks_for_entry(entry_id: int, settings: dict[str, str]) -> tuple[int, str]:
    ensure_sync_rule(entry_id, settings)
    with connect() as conn:
        rule = conn.execute("SELECT sync_enabled FROM sync_rules WHERE entry_id=?", (entry_id,)).fetchone()
        if not rule or not int(rule["sync_enabled"] or 0):
            return 0, "本地同步未开启，跳过同步计划"
        assets = conn.execute(
            """
            SELECT ca.*, e.display_title
            FROM cloud_assets ca
            JOIN entries e ON e.id=ca.entry_id
            WHERE ca.entry_id=? AND ca.status='available'
            ORDER BY ca.episode_number ASC
            """,
            (entry_id,),
        ).fetchall()
        if not assets:
            return 0, "已开启本地同步；云盘资源入库后会自动同步"
        ts = now()
        queued = 0
        for asset in assets:
            entry = conn.execute("SELECT * FROM entries WHERE id=?", (asset["entry_id"],)).fetchone()
            if not entry:
                continue
            target = normalize_local_target_path(
                local_episode_path(dict(asset), dict(entry), settings),
                str(asset["cloud_name"] or ""),
            )
            conn.execute(
                """
                INSERT INTO sync_tasks
                  (cloud_asset_id, release_id, series_id, entry_id, status, source_path, target_path,
                   created_at, updated_at)
                VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?)
                ON CONFLICT(cloud_asset_id, sync_direction) DO UPDATE SET
                  status=CASE
                    WHEN sync_tasks.status='synced' AND sync_tasks.target_path=excluded.target_path THEN sync_tasks.status
                    ELSE 'pending'
                  END,
                  source_path=excluded.source_path,
                  target_path=excluded.target_path,
                  retry_after=CASE
                    WHEN sync_tasks.status='synced' AND sync_tasks.target_path=excluded.target_path THEN sync_tasks.retry_after
                    ELSE ''
                  END,
                  last_error=CASE
                    WHEN sync_tasks.status='synced' AND sync_tasks.target_path=excluded.target_path THEN sync_tasks.last_error
                    ELSE ''
                  END,
                  updated_at=excluded.updated_at
                """,
                (
                    asset["id"],
                    asset["release_id"],
                    asset["series_id"],
                    asset["entry_id"],
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
    return queued, f"已生成本地同步任务: {queued} 条"


def queue_sync_for_entry(entry_id: int, settings: dict[str, str]) -> tuple[int, str]:
    ensure_sync_rule(entry_id, settings, enabled=True)
    enqueue_sync_plan_tasks([entry_id], now())
    return 1, "已加入同步计划；云盘资源入库后会自动生成本地同步任务"


async def process_sync_plan_tasks(settings: dict[str, str], limit: int = 20) -> tuple[int, int]:
    async with sync_plan_tasks_lock:
        return await _process_sync_plan_tasks(settings, limit)


async def _process_sync_plan_tasks(settings: dict[str, str], limit: int = 20) -> tuple[int, int]:
    with connect() as conn:
        conn.execute(
            """
            UPDATE sync_plan_tasks
            SET status='pending', last_error='上次同步计划处理中断，已自动放回待处理', updated_at=?
            WHERE status='running' AND updated_at < ?
            """,
            (now(), stale_running_cutoff()),
        )
        rows = conn.execute(
            """
            SELECT spt.*, e.display_title AS title_cn, e.domain_kind
            FROM sync_plan_tasks spt
            JOIN entries e ON e.id=spt.entry_id
            WHERE spt.status IN ('pending', 'failed')
              AND (spt.retry_after='' OR spt.retry_after <= ?)
            ORDER BY spt.id ASC
            LIMIT ?
            """,
            (now(), limit),
        ).fetchall()

    completed = 0
    failed = 0
    for row in rows:
        with connect() as conn:
            conn.execute(
                "UPDATE sync_plan_tasks SET status='running', attempts=attempts+1, updated_at=? WHERE id=?",
                (now(), row["id"]),
            )
        try:
            queued, _ = materialize_sync_tasks_for_entry(int(row["entry_id"]), settings)
            with connect() as conn:
                conn.execute(
                    """
                    UPDATE sync_plan_tasks
                    SET status='completed', retry_after='', last_error=?, updated_at=?
                    WHERE id=?
                    """,
                    (f"同步计划已调和，新增同步任务 {queued} 个", now(), row["id"]),
                )
            completed += 1
            if queued > 0:
                request_queue_trigger("sync")
        except Exception as exc:
            failed += 1
            with connect() as conn:
                conn.execute(
                    """
                    UPDATE sync_plan_tasks
                    SET status='failed', retry_after=?, last_error=?, updated_at=?
                    WHERE id=?
                    """,
                    (task_retry_after(settings, int(row["attempts"] or 0) + 1), str(exc)[:2000], now(), row["id"]),
                )
            log("error", f"同步计划失败: {row['title_cn']} - {exc}")
    return completed, failed


def backfill_cloud_assets_from_completed_tasks(settings: dict[str, str]) -> int:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT dt.id
            FROM download_tasks dt
            JOIN cloud_submissions cs ON cs.download_task_id=dt.id
            LEFT JOIN cloud_assets ca ON ca.task_id=dt.id
            WHERE dt.status='completed'
              AND dt.pikpak_file_id != ''
              AND cs.provider='pikpak'
              AND cs.status='completed'
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


async def find_existing_remote_episode(
    settings: dict[str, str],
    target_directory: str,
    expected_name: str,
    episode_number: int,
) -> dict | None:
    if not rclone_service.enabled(settings) or not target_directory:
        return None
    try:
        files = await rclone_service.list_files(settings, target_directory, recursive=False)
    except Exception as exc:
        if "directory not found" in str(exc).lower():
            return None
        raise
    normalized_expected = normalize_title_key(expected_name)
    for item in files:
        if item.get("is_dir"):
            continue
        name = str(item.get("name") or "")
        if Path(name).suffix.lower() not in VIDEO_SUFFIXES:
            continue
        if normalized_expected and normalize_title_key(name) == normalized_expected:
            return item
        if episode_number > 0 and parse_episode(name) == episode_number:
            return item
    return None


def cloud_file_id(item: dict) -> str:
    return str(item.get("id") or item.get("file_id") or item.get("fileId") or "")


def synthetic_task_id(file_id: str) -> int:
    return 0 - (zlib.crc32(file_id.encode("utf-8")) % 2147483647) - 1


def match_cloud_file_to_entry(item: dict, entry_rows: list[dict]) -> dict | None:
    path = str(item.get("cloud_path") or item.get("name") or "")
    match = re.search(r"bangumi[-_ ]?(\d+)", path, re.I)
    if match:
        bangumi_id = match.group(1)
        for entry in entry_rows:
            if str(entry.get("bangumi_id") or "") == bangumi_id:
                return entry
    normalized_path = normalize_title_key(path)
    best: dict | None = None
    best_len = 0
    for entry in entry_rows:
        keys = {
            normalize_title_key(str(entry.get("title_cn") or "")),
            normalize_title_key(str(entry.get("title_raw") or "")),
        }
        for key in keys:
            if key and key in normalized_path and len(key) > best_len:
                best = entry
                best_len = len(key)
    return best


def ensure_library_entry_for_reference(
    conn,
    *,
    entry_row: dict,
    display_title: str,
    source_ref: str = "",
) -> tuple[int, int]:
    ts = now()
    work_key = normalize_title_key(str(entry_row.get("title_cn") or entry_row.get("title_raw") or display_title or "Unknown"))
    conn.execute(
        """
        INSERT INTO works
          (root_key, title_root, title_root_raw, bangumi_id, metadata_source, hidden, created_at, updated_at)
        VALUES (?, ?, ?, ?, 'cloud_import', 0, ?, ?)
        ON CONFLICT(root_key) DO UPDATE SET
          title_root=excluded.title_root,
          title_root_raw=excluded.title_root_raw,
          bangumi_id=CASE WHEN works.bangumi_id='' THEN excluded.bangumi_id ELSE works.bangumi_id END,
          updated_at=excluded.updated_at
        """,
        (
            work_key,
            str(entry_row.get("title_cn") or entry_row.get("title_raw") or display_title or "Unknown"),
            str(entry_row.get("title_raw") or entry_row.get("title_cn") or display_title or "Unknown"),
            str(entry_row.get("bangumi_id") or ""),
            ts,
            ts,
        ),
    )
    work_id = int(conn.execute("SELECT id FROM works WHERE root_key=?", (work_key,)).fetchone()["id"])
    fingerprint_key = f"cloud-library:{entry_row.get('bangumi_id') or ''}:{normalize_title_key(display_title)}"
    conn.execute(
        """
        INSERT INTO entries
          (work_id, fingerprint, domain_kind, entry_kind, display_title, title_root,
           season_label, arc_label, part_label, special_label,
           title_raw, title_cn, bangumi_id, mikan_bangumi_id, tmdb_id, year, season_number,
           poster_url, summary, metadata_source, hidden, auto_download, selected_group, selected_resolution,
           backfill_mode, created_at, updated_at)
        VALUES (?, ?, 'library', 'season', ?, ?, '', '', '', '', ?, ?, ?, ?, ?, ?, ?, ?, ?, 'cloud_import', 0, 'inherit', '', '', 'inherit', ?, ?)
        ON CONFLICT(fingerprint) DO UPDATE SET
          work_id=excluded.work_id,
          domain_kind='library',
          display_title=excluded.display_title,
          title_root=excluded.title_root,
          title_raw=excluded.title_raw,
          title_cn=excluded.title_cn,
          bangumi_id=CASE WHEN entries.bangumi_id='' THEN excluded.bangumi_id ELSE entries.bangumi_id END,
          mikan_bangumi_id=CASE WHEN excluded.mikan_bangumi_id!='' THEN excluded.mikan_bangumi_id ELSE entries.mikan_bangumi_id END,
          year=CASE WHEN excluded.year!=0 THEN excluded.year ELSE entries.year END,
          season_number=CASE WHEN excluded.season_number!=0 THEN excluded.season_number ELSE entries.season_number END,
          poster_url=CASE WHEN excluded.poster_url!='' THEN excluded.poster_url ELSE entries.poster_url END,
          summary=CASE WHEN excluded.summary!='' THEN excluded.summary ELSE entries.summary END,
          updated_at=excluded.updated_at
        """,
        (
            work_id,
            fingerprint_key,
            display_title,
            str(entry_row.get("title_cn") or entry_row.get("title_raw") or display_title or "Unknown"),
            str(entry_row.get("title_raw") or display_title or "Unknown"),
            str(entry_row.get("title_cn") or display_title or "Unknown"),
            str(entry_row.get("bangumi_id") or ""),
            str(entry_row.get("mikan_bangumi_id") or ""),
            str(entry_row.get("tmdb_id") or ""),
            int(entry_row.get("year") or 0),
            int(entry_row.get("season_number") or 1),
            str(entry_row.get("poster_url") or ""),
            str(entry_row.get("summary") or ""),
            ts,
            ts,
        ),
    )
    entry_id = int(conn.execute("SELECT id FROM entries WHERE fingerprint=?", (fingerprint_key,)).fetchone()["id"])
    conn.execute(
        """
        INSERT INTO library_entries
          (entry_id, source_type, source_ref, wanted, archived, created_at, updated_at)
        VALUES (?, 'cloud_scan', ?, 1, 0, ?, ?)
        ON CONFLICT(entry_id) DO UPDATE SET
          source_ref=CASE WHEN excluded.source_ref!='' THEN excluded.source_ref ELSE library_entries.source_ref END,
          wanted=1,
          archived=0,
          updated_at=excluded.updated_at
        """,
        (entry_id, source_ref, ts, ts),
    )
    return resolve_entry_series_id(conn, entry_id), entry_id


def upsert_scanned_cloud_asset(item: dict, entry: dict, settings: dict[str, str]) -> int | None:
    file_id = cloud_file_id(item)
    name = str(item.get("name") or Path(str(item.get("cloud_path") or "")).name)
    path = str(item.get("cloud_path") or name)
    if not file_id or not name:
        return None
    episode_number = parse_episode(name) or parse_episode(path) or 0
    if episode_number <= 0:
        return None
    with connect() as conn:
        series_id, entry_id = ensure_library_entry_for_reference(
            conn,
            entry_row=entry,
            display_title=str(entry.get("title_cn") or entry.get("title_raw") or name),
            source_ref=path,
        )
        release = conn.execute(
            """
            SELECT *
            FROM releases
            WHERE entry_id=? AND episode_number=?
            ORDER BY selected DESC, id DESC
            LIMIT 1
            """,
            (entry_id, episode_number),
        ).fetchone()
        if not release:
            guid = f"cloud-import:pikpak:{file_id}"
            conn.execute(
                """
                INSERT INTO episodes
                  (series_id, entry_id, episode_number, title, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'downloaded', ?, ?)
                ON CONFLICT(series_id, episode_number) DO UPDATE SET
                  status='downloaded',
                  updated_at=excluded.updated_at
                """,
                (
                    series_id,
                    entry_id,
                    episode_number,
                    f"第{episode_number:02d}话",
                    now(),
                    now(),
                ),
            )
            conn.execute(
                """
                INSERT INTO releases
                  (series_id, entry_id, episode_number, guid, title, subtitle_group, resolution,
                   language, torrent_url, magnet, published_at, selected, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, '云盘导入', '', '', '', '', '', 1, ?, ?)
                ON CONFLICT(guid) DO UPDATE SET
                  series_id=excluded.series_id,
                  entry_id=excluded.entry_id,
                  episode_number=excluded.episode_number,
                  title=excluded.title,
                  updated_at=excluded.updated_at
                """,
                (series_id, entry_id, episode_number, guid, name, now(), now()),
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
                SET release_id=?, series_id=?, entry_id=?, episode_number=?, cloud_path=?, cloud_name=?,
                    status='available', updated_at=?
                WHERE id=?
                """,
                (release["id"], series_id, entry_id, episode_number, path, name, ts, existing["id"]),
            )
        else:
            conn.execute(
                """
                INSERT INTO cloud_assets
                  (task_id, release_id, series_id, entry_id, episode_number, provider, provider_file_id,
                   cloud_path, cloud_name, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'pikpak', ?, ?, ?, 'available', ?, ?)
                """,
                (
                    synthetic_task_id(file_id),
                    release["id"],
                    series_id,
                    entry_id,
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


def upsert_cloud_asset_from_download_task(task_id: int, item: dict, settings: dict[str, str]) -> int | None:
    created = False
    file_id = cloud_file_id(item)
    name = str(item.get("name") or Path(str(item.get("cloud_path") or "")).name)
    path = str(item.get("cloud_path") or name)
    if not name:
        return None
    with connect() as conn:
        task = conn.execute("SELECT * FROM download_tasks WHERE id=?", (task_id,)).fetchone()
        if not task:
            return None
        release = conn.execute("SELECT * FROM releases WHERE id=?", (task["release_id"],)).fetchone()
        entry = conn.execute("SELECT * FROM entries WHERE id=?", (task["entry_id"],)).fetchone()
        if not release or not entry:
            return None
        ts = now()
        provider_file_id = file_id or f"rclone:{path}"
        existing = conn.execute(
            "SELECT id FROM cloud_assets WHERE provider='pikpak' AND provider_file_id=?",
            (provider_file_id,),
        ).fetchone()
        existing_by_episode = conn.execute(
            """
            SELECT id
            FROM cloud_assets
            WHERE entry_id=? AND episode_number=? AND provider='pikpak'
            ORDER BY id ASC
            LIMIT 1
            """,
            (task["entry_id"], release["episode_number"]),
        ).fetchone()
        existing_asset = existing or existing_by_episode
        if existing_asset:
            conn.execute(
                """
                UPDATE cloud_assets
                SET task_id=?, release_id=?, series_id=?, entry_id=?, episode_number=?, cloud_path=?, cloud_name=?,
                    status='available', updated_at=?
                WHERE id=?
                """,
                (
                    task["id"],
                    task["release_id"],
                    task["series_id"],
                    task["entry_id"],
                    release["episode_number"],
                    path,
                    name,
                    ts,
                    existing_asset["id"],
                ),
            )
        else:
            created = True
            conn.execute(
                """
                INSERT INTO cloud_assets
                  (task_id, release_id, series_id, entry_id, episode_number, provider, provider_file_id,
                   cloud_path, cloud_name, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'pikpak', ?, ?, ?, 'available', ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                  entry_id=excluded.entry_id,
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
                    task["entry_id"],
                    release["episode_number"],
                    provider_file_id,
                    path,
                    name,
                    ts,
                    ts,
                ),
            )
        asset = conn.execute("SELECT id FROM cloud_assets WHERE task_id=?", (task["id"],)).fetchone()
    if asset:
        if created:
            log("info", f"云盘资源已入库: {name}")
        return int(asset["id"])
    return None


def upsert_cloud_asset_for_release(release_id: int, item: dict, settings: dict[str, str]) -> int | None:
    file_id = cloud_file_id(item)
    name = str(item.get("name") or Path(str(item.get("cloud_path") or "")).name)
    path = str(item.get("cloud_path") or name)
    if not name:
        return None
    with connect() as conn:
        release = conn.execute("SELECT * FROM releases WHERE id=?", (release_id,)).fetchone()
        if not release:
            return None
        entry = conn.execute("SELECT * FROM entries WHERE id=?", (release["entry_id"],)).fetchone()
        if not entry:
            return None
        ts = now()
        series_id = resolve_entry_series_id(conn, int(release["entry_id"]))
        provider_file_id = file_id or f"rclone:{path}"
        task_id = synthetic_task_id(provider_file_id)
        existing = conn.execute(
            "SELECT id FROM cloud_assets WHERE provider='pikpak' AND provider_file_id=?",
            (provider_file_id,),
        ).fetchone()
        existing_by_episode = conn.execute(
            """
            SELECT id
            FROM cloud_assets
            WHERE entry_id=? AND episode_number=? AND provider='pikpak'
            ORDER BY id ASC
            LIMIT 1
            """,
            (release["entry_id"], release["episode_number"]),
        ).fetchone()
        existing_asset = existing or existing_by_episode
        if existing_asset:
            conn.execute(
                """
                UPDATE cloud_assets
                SET task_id=?, release_id=?, series_id=?, entry_id=?, episode_number=?, provider_file_id=?,
                    cloud_path=?, cloud_name=?, status='available', updated_at=?
                WHERE id=?
                """,
                (
                    task_id,
                    release["id"],
                    series_id,
                    release["entry_id"],
                    release["episode_number"],
                    provider_file_id,
                    path,
                    name,
                    ts,
                    existing_asset["id"],
                ),
            )
            asset_id = int(existing_asset["id"])
        else:
            conn.execute(
                """
                INSERT INTO cloud_assets
                  (task_id, release_id, series_id, entry_id, episode_number, provider, provider_file_id,
                   cloud_path, cloud_name, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'pikpak', ?, ?, ?, 'available', ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                  release_id=excluded.release_id,
                  series_id=excluded.series_id,
                  entry_id=excluded.entry_id,
                  episode_number=excluded.episode_number,
                  provider_file_id=excluded.provider_file_id,
                  cloud_path=excluded.cloud_path,
                  cloud_name=excluded.cloud_name,
                  status='available',
                  updated_at=excluded.updated_at
                """,
                (
                    task_id,
                    release["id"],
                    series_id,
                    release["entry_id"],
                    release["episode_number"],
                    provider_file_id,
                    path,
                    name,
                    ts,
                    ts,
                ),
            )
            asset = conn.execute("SELECT id FROM cloud_assets WHERE task_id=?", (task_id,)).fetchone()
            asset_id = int(asset["id"]) if asset else 0
            if asset_id:
                log("info", f"云盘资源已入库: {name}")
        if asset_id:
            enqueue_sync_plan_task(conn, int(release["entry_id"]), ts)
            return asset_id
    return None


async def reconcile_rclone_submitted_tasks(settings: dict[str, str], limit: int = 20) -> tuple[int, int]:
    if not rclone_service.enabled(settings):
        return 0, 0
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT dt.*, r.episode_number, r.title AS release_title, e.display_title AS title_cn
            FROM download_tasks dt
            JOIN releases r ON r.id=dt.release_id
            JOIN entries e ON e.id=dt.entry_id
            JOIN cloud_submissions cs ON cs.download_task_id=dt.id
            WHERE dt.status='submitted'
              AND e.bangumi_id != ''
              AND cs.provider='pikpak'
              AND cs.status='submitted'
              AND (dt.retry_after='' OR dt.retry_after <= ?)
            ORDER BY dt.id ASC
            LIMIT ?
            """,
            (now(), limit),
        ).fetchall()
    completed = 0
    missing = 0
    for task in rows:
        try:
            files = await rclone_service.list_files(settings, task["target_dir"], recursive=False)
        except Exception as exc:
            error_text = str(exc)
            if "directory not found" in error_text.lower():
                try:
                    await rclone_service.mkdir(settings, task["target_dir"])
                    error_text = "目标目录刚创建，等待 PikPak 完成后自动重试"
                except Exception as mkdir_exc:
                    error_text = f"目标目录不存在且创建失败: {mkdir_exc}"
            with connect() as conn:
                conn.execute(
                    """
                    UPDATE download_tasks
                    SET retry_after=?, last_error=?, updated_at=?
                    WHERE id=?
                    """,
                    (task_retry_after(settings, int(task["attempts"] or 0) + 1), error_text[:2000], now(), task["id"]),
                )
                conn.execute(
                    """
                    UPDATE cloud_submissions
                    SET status='submitted', retry_after=?, last_error=?, updated_at=?, last_seen_at=?
                    WHERE download_task_id=?
                    """,
                    (
                        task_retry_after(settings, int(task["attempts"] or 0) + 1),
                        error_text[:2000],
                        now(),
                        now(),
                        task["id"],
                    ),
                )
            log("warn", f"rclone 云盘状态检查失败: {task['release_title']} - {error_text}")
            missing += 1
            continue
        normalized_name = normalize_title_key(task["normalized_name"])
        matched = None
        for item in files:
            if item.get("is_dir"):
                continue
            name = str(item.get("name") or "")
            if Path(name).suffix.lower() not in VIDEO_SUFFIXES:
                continue
            if normalized_name and normalize_title_key(name) == normalized_name:
                matched = item
                break
            if parse_episode(name) == int(task["episode_number"] or 0):
                matched = item
                break
        if not matched:
            with connect() as conn:
                conn.execute(
                    """
                    UPDATE download_tasks
                    SET retry_after=?, last_error='rclone 已提交，目标目录暂未发现完成文件', updated_at=?
                    WHERE id=?
                    """,
                    (task_retry_after(settings, int(task["attempts"] or 0) + 1), now(), task["id"]),
                )
                conn.execute(
                    """
                    UPDATE cloud_submissions
                    SET status='submitted', retry_after=?, last_error='rclone 已提交，目标目录暂未发现完成文件',
                        updated_at=?, last_seen_at=?
                    WHERE download_task_id=?
                    """,
                    (
                        task_retry_after(settings, int(task["attempts"] or 0) + 1),
                        now(),
                        now(),
                        task["id"],
                    ),
                )
            missing += 1
            continue
        asset_id = upsert_cloud_asset_from_download_task(int(task["id"]), matched, settings)
        if not asset_id:
            missing += 1
            continue
        with connect() as conn:
            ts = now()
            conn.execute(
                """
                UPDATE download_tasks
                SET status='completed',
                    pikpak_file_id=?,
                    normalized_name=?,
                    retry_after='',
                    last_error='',
                    updated_at=?
                WHERE id=?
                """,
                (
                    str(matched.get("id") or matched.get("file_id") or matched.get("fileId") or ""),
                    str(matched.get("name") or task["normalized_name"] or ""),
                    ts,
                    task["id"],
                ),
            )
            conn.execute(
                """
                UPDATE cloud_submissions
                SET status='completed', provider_file_id=?, normalized_name=?, retry_after='', last_error='',
                    updated_at=?, last_seen_at=?
                WHERE download_task_id=?
                """,
                (
                    str(matched.get("id") or matched.get("file_id") or matched.get("fileId") or ""),
                    str(matched.get("name") or task["normalized_name"] or ""),
                    ts,
                    ts,
                    task["id"],
                ),
            )
            enqueue_cloud_asset_task(conn, int(task["id"]), ts)
        completed += 1
    if completed:
        with connect() as conn:
            entry_ids = [
                int(row["entry_id"])
                for row in conn.execute(
                    """
                    SELECT DISTINCT entry_id
                    FROM cloud_assets
                    WHERE task_id IN (
                      SELECT id
                      FROM download_tasks
                      WHERE status='completed'
                    )
                      AND entry_id != 0
                    """
                ).fetchall()
            ]
        enqueue_sync_plan_tasks(entry_ids, now())
    return completed, missing


async def scan_cloud_library(settings: dict[str, str]) -> tuple[int, int]:
    files = await list_cloud_files(settings, settings.get("library_root") or "/Anime")
    with connect() as conn:
        entry_rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT e.*, w.title_root_raw
                FROM entries e
                JOIN library_entries le ON le.entry_id=e.id
                LEFT JOIN works w ON w.id=e.work_id
                WHERE COALESCE(e.hidden, 0)=0 AND e.bangumi_id != ''
                """
            ).fetchall()
        ]
    imported = 0
    skipped = 0
    synced_entries: set[int] = set()
    for item in files:
        name = str(item.get("name") or item.get("cloud_path") or "")
        if Path(name).suffix.lower() not in VIDEO_SUFFIXES:
            continue
        entry = match_cloud_file_to_entry(item, entry_rows)
        if not entry:
            skipped += 1
            continue
        asset_id = upsert_scanned_cloud_asset(item, entry, settings)
        if not asset_id:
            skipped += 1
            continue
        imported += 1
        with connect() as conn:
            rule = conn.execute(
                "SELECT * FROM sync_rules WHERE entry_id=(SELECT entry_id FROM cloud_assets WHERE id=? LIMIT 1)",
                (asset_id,),
            ).fetchone()
        if rule and rule["sync_enabled"]:
            synced_entries.add(int(rule["entry_id"]))
    enqueue_sync_plan_tasks(list(synced_entries), now())
    return imported, skipped


async def download_cloud_file_to_local(file_id: str, source: str, target: str, settings: dict[str, str], progress_cb=None) -> None:
    target_path = Path(target)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    if rclone_service.enabled(settings) and source:
        await rclone_service.copy_to_local(settings, source, target, progress_cb=progress_cb)
        return

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
    async with sync_tasks_lock:
        await _process_sync_tasks(settings, limit)


async def _process_sync_tasks(settings: dict[str, str], limit: int = 5) -> None:
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
                """
                UPDATE sync_tasks
                SET status='running', attempts=attempts+1, progress=0, progress_text='准备同步', updated_at=?
                WHERE id=?
                """,
                (now(), task["id"]),
            )
        try:
            async def progress_cb(percent: int, text: str) -> None:
                with connect() as conn:
                    conn.execute(
                        """
                        UPDATE sync_tasks
                        SET progress=?, progress_text=?, updated_at=?
                        WHERE id=?
                        """,
                        (percent, text[:500], now(), task["id"]),
                    )

            normalized_target = normalize_local_target_path(
                str(task["target_path"] or ""),
                str(task["cloud_name"] or ""),
            )
            target_file = Path(normalized_target)
            if target_file.exists() and target_file.stat().st_size > 0:
                with connect() as conn:
                    ts = now()
                    conn.execute(
                        """
                        INSERT INTO local_assets
                          (cloud_asset_id, release_id, series_id, entry_id, episode_number, local_path,
                           nfo_status, status, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, 'pending', 'synced', ?, ?)
                        ON CONFLICT(cloud_asset_id) DO UPDATE SET
                          local_path=excluded.local_path,
                          status='synced',
                          updated_at=excluded.updated_at
                        """,
                        (
                            task["cloud_asset_id"],
                            task["release_id"],
                            task["series_id"],
                            task["entry_id"],
                            conn.execute("SELECT episode_number FROM cloud_assets WHERE id=?", (task["cloud_asset_id"],)).fetchone()["episode_number"],
                            normalized_target,
                            ts,
                            ts,
                        ),
                    )
                    conn.execute(
                        """
                        UPDATE sync_tasks
                        SET status='synced', target_path=?, progress=100, progress_text='本地已存在，跳过重复同步',
                            retry_after='', last_error='', updated_at=?
                        WHERE id=?
                        """,
                        (normalized_target, ts, task["id"]),
                    )
                    local_asset = conn.execute(
                        "SELECT id FROM local_assets WHERE cloud_asset_id=?",
                        (task["cloud_asset_id"],),
                    ).fetchone()
                    if local_asset:
                        enqueue_nfo_task(conn, int(local_asset["id"]), int(task["release_id"]), int(task["series_id"]), int(task["entry_id"]), ts)
                        enqueue_local_presence_task(conn, int(local_asset["id"]), int(task["release_id"]), int(task["series_id"]), int(task["entry_id"]), ts)
                return True

            await download_cloud_file_to_local(
                task["provider_file_id"],
                task["source_path"],
                normalized_target,
                settings,
                progress_cb=progress_cb,
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
                  (cloud_asset_id, release_id, series_id, entry_id, episode_number, local_path,
                   nfo_status, status, created_at, updated_at)
                SELECT id, release_id, series_id, entry_id, episode_number, ?, 'pending', 'synced', ?, ?
                FROM cloud_assets
                WHERE id=?
                ON CONFLICT(cloud_asset_id) DO UPDATE SET
                  local_path=excluded.local_path,
                  status='synced',
                  updated_at=excluded.updated_at
                """,
                (normalized_target, ts, ts, task["cloud_asset_id"]),
            )
            conn.execute(
                """
                UPDATE sync_tasks
                SET status='synced', target_path=?, progress=100, progress_text='同步完成',
                    retry_after='', last_error='', updated_at=?
                WHERE id=?
                """,
                (normalized_target, ts, task["id"]),
            )
            local_asset = conn.execute(
                "SELECT id FROM local_assets WHERE cloud_asset_id=?",
                (task["cloud_asset_id"],),
            ).fetchone()
            if local_asset:
                enqueue_nfo_task(conn, int(local_asset["id"]), int(task["release_id"]), int(task["series_id"]), int(task["entry_id"]), ts)
                enqueue_local_presence_task(conn, int(local_asset["id"]), int(task["release_id"]), int(task["series_id"]), int(task["entry_id"]), ts)
        log("info", f"已同步到本地: {task['cloud_name']}")
        return True

    semaphore = asyncio.Semaphore(max(1, min(3, limit)))

    async def limited(task):
        async with semaphore:
            return await handle(task)

    await asyncio.gather(*(limited(task) for task in rows))


async def process_nfo_tasks(settings: dict[str, str], limit: int = 10) -> tuple[int, int]:
    async with nfo_tasks_lock:
        return await _process_nfo_tasks(settings, limit)


async def _process_nfo_tasks(settings: dict[str, str], limit: int = 10) -> tuple[int, int]:
    with connect() as conn:
        conn.execute(
            """
            UPDATE nfo_tasks
            SET status='pending', last_error='上次 NFO 处理中断，已自动放回待处理', updated_at=?
            WHERE status='running' AND updated_at < ?
            """,
            (now(), stale_running_cutoff()),
        )
        rows = conn.execute(
            """
            SELECT nt.*, la.local_path
            FROM nfo_tasks nt
            JOIN local_assets la ON la.id=nt.local_asset_id
            WHERE nt.status IN ('pending', 'failed')
              AND (nt.retry_after='' OR nt.retry_after <= ?)
              AND la.status='synced'
            ORDER BY nt.id ASC
            LIMIT ?
            """,
            (now(), limit),
        ).fetchall()

    completed = 0
    failed = 0
    for task in rows:
        with connect() as conn:
            conn.execute(
                """
                UPDATE nfo_tasks
                SET status='running', attempts=attempts+1, updated_at=?
                WHERE id=?
                """,
                (now(), task["id"]),
            )
        try:
            nfo_settings = dict(settings)
            nfo_settings["nfo_output_root"] = settings.get("local_library_root") or "/media/pikpak-anime"
            generate_nfo_for_entry(int(task["entry_id"]), nfo_settings)
            with connect() as conn:
                ts = now()
                conn.execute(
                    "UPDATE local_assets SET nfo_status='generated', updated_at=? WHERE id=?",
                    (ts, task["local_asset_id"]),
                )
                conn.execute(
                    """
                    UPDATE nfo_tasks
                    SET status='completed', retry_after='', last_error='', updated_at=?
                    WHERE id=?
                    """,
                    (ts, task["id"]),
                )
            completed += 1
        except Exception as exc:
            failed += 1
            with connect() as conn:
                conn.execute(
                    """
                    UPDATE nfo_tasks
                    SET status='failed', retry_after=?, last_error=?, updated_at=?
                    WHERE id=?
                    """,
                    (task_retry_after(settings, int(task["attempts"] or 0) + 1), str(exc)[:2000], now(), task["id"]),
                )
            log("error", f"NFO 生成失败: {task['local_path']} - {exc}")
    return completed, failed


async def process_local_presence_tasks(settings: dict[str, str], limit: int = 20) -> tuple[int, int]:
    async with local_presence_tasks_lock:
        return await _process_local_presence_tasks(settings, limit)


async def _process_local_presence_tasks(settings: dict[str, str], limit: int = 20) -> tuple[int, int]:
    with connect() as conn:
        conn.execute(
            """
            UPDATE local_presence_tasks
            SET status='pending', last_error='上次本地存在性检查中断，已自动放回待处理', updated_at=?
            WHERE status='running' AND updated_at < ?
            """,
            (now(), stale_running_cutoff()),
        )
        rows = conn.execute(
            """
            SELECT lpt.*, la.local_path, la.nfo_status
            FROM local_presence_tasks lpt
            JOIN local_assets la ON la.id=lpt.local_asset_id
            WHERE lpt.status IN ('pending', 'failed')
              AND (lpt.retry_after='' OR lpt.retry_after <= ?)
            ORDER BY lpt.id ASC
            LIMIT ?
            """,
            (now(), limit),
        ).fetchall()

    completed = 0
    failed = 0
    for task in rows:
        with connect() as conn:
            conn.execute(
                """
                UPDATE local_presence_tasks
                SET status='running', attempts=attempts+1, updated_at=?
                WHERE id=?
                """,
                (now(), task["id"]),
            )
        try:
            local_path = Path(str(task["local_path"] or ""))
            nfo_path = local_path.with_suffix(".nfo")
            local_exists = local_path.exists()
            nfo_exists = nfo_path.exists()
            with connect() as conn:
                ts = now()
                conn.execute(
                    """
                    UPDATE local_assets
                    SET status=?, nfo_status=?, updated_at=?
                    WHERE id=?
                    """,
                    (
                        "synced" if local_exists else "removed",
                        "generated" if nfo_exists else "pending",
                        ts,
                        task["local_asset_id"],
                    ),
                )
                conn.execute(
                    """
                    UPDATE local_presence_tasks
                    SET status='completed', retry_after='', last_error=?, updated_at=?
                    WHERE id=?
                    """,
                    (
                        "" if local_exists else "检测到本地文件已不存在",
                        ts,
                        task["id"],
                    ),
                )
            completed += 1
        except Exception as exc:
            failed += 1
            with connect() as conn:
                conn.execute(
                    """
                    UPDATE local_presence_tasks
                    SET status='failed', retry_after=?, last_error=?, updated_at=?
                    WHERE id=?
                    """,
                    (task_retry_after(settings, int(task["attempts"] or 0) + 1), str(exc)[:2000], now(), task["id"]),
                )
            log("error", f"本地存在性检查失败: {task['local_path']} - {exc}")
    return completed, failed


def cancel_sync_for_entry(entry_id: int) -> tuple[int, str]:
    removed = 0
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM local_assets WHERE entry_id=? AND status='synced'",
            (entry_id,),
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
            enqueue_local_presence_task(conn, int(row["id"]), int(row["release_id"]), int(row["series_id"]), int(row["entry_id"]), now())
        conn.execute(
            "UPDATE sync_rules SET sync_enabled=0, updated_at=? WHERE entry_id=?",
            (now(), entry_id),
        )
    return removed, f"已取消同步并清理本地文件: {removed} 个"
