from __future__ import annotations

import httpx
import feedparser

from .db import connect, log, now
from .library import bool_setting, render_episode_name, target_dir
from .metadata import generate_nfo_for_series, refresh_series_metadata
from .parser import ParsedRelease, fingerprint, parse_entry, split_lines
from .pikpak_service import list_offline_tasks, rename_cloud_file, submit_offline_download


async def fetch_entries(settings: dict[str, str]) -> list[ParsedRelease]:
    proxy = settings.get("rss_proxy") or None
    async with httpx.AsyncClient(proxy=proxy, timeout=30, follow_redirects=True) as client:
        resp = await client.get(settings["rss_url"])
        resp.raise_for_status()
    parsed = feedparser.parse(resp.text)
    return [parse_entry(entry) for entry in parsed.entries]


def upsert_release(item: ParsedRelease) -> tuple[int, int]:
    fp = fingerprint(item.series_title or item.title, item.bangumi_id)
    with connect() as conn:
        ts = now()
        conn.execute(
            """
            INSERT INTO series
              (fingerprint, title_raw, title_cn, bangumi_id, year, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(fingerprint) DO UPDATE SET
              title_raw=excluded.title_raw,
              title_cn=CASE WHEN series.title_cn='' THEN excluded.title_cn ELSE series.title_cn END,
              bangumi_id=CASE WHEN series.bangumi_id='' THEN excluded.bangumi_id ELSE series.bangumi_id END,
              year=CASE WHEN series.year=0 THEN excluded.year ELSE series.year END,
              updated_at=excluded.updated_at
            """,
            (fp, item.series_title, item.series_title, item.bangumi_id, item.year, ts, ts),
        )
        series_id = conn.execute("SELECT id FROM series WHERE fingerprint=?", (fp,)).fetchone()["id"]
        if item.episode_number:
            conn.execute(
                """
                INSERT INTO episodes
                  (series_id, episode_number, title, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(series_id, episode_number) DO UPDATE SET updated_at=excluded.updated_at
                """,
                (series_id, item.episode_number, f"第{item.episode_number:02d}话", ts, ts),
            )
        conn.execute(
            """
            INSERT INTO releases
              (series_id, episode_number, guid, title, subtitle_group, resolution,
               torrent_url, magnet, published_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(guid) DO UPDATE SET
              subtitle_group=excluded.subtitle_group,
              resolution=excluded.resolution,
              torrent_url=excluded.torrent_url,
              magnet=excluded.magnet,
              updated_at=excluded.updated_at
            """,
            (
                series_id,
                item.episode_number,
                item.guid,
                item.title,
                item.subtitle_group,
                item.resolution,
                item.torrent_url,
                item.magnet,
                item.published_at,
                ts,
                ts,
            ),
        )
        release_id = conn.execute("SELECT id FROM releases WHERE guid=?", (item.guid,)).fetchone()["id"]
    return series_id, release_id


def priority_pick(values: list[str], priority: list[str]) -> str:
    values_clean = [v for v in values if v]
    if not values_clean:
        return ""
    for preferred in priority:
        for value in values_clean:
            if preferred.lower() == value.lower() or preferred.lower() in value.lower():
                return value
    return values_clean[0] if len(set(values_clean)) == 1 else ""


def resolve_series_choice(series_id: int, settings: dict[str, str]) -> tuple[str, str, bool]:
    with connect() as conn:
        series = conn.execute("SELECT * FROM series WHERE id=?", (series_id,)).fetchone()
        rows = conn.execute(
            "SELECT DISTINCT subtitle_group, resolution FROM releases WHERE series_id=?",
            (series_id,),
        ).fetchall()

    groups = sorted({r["subtitle_group"] for r in rows if r["subtitle_group"]})
    resolutions = sorted({r["resolution"] for r in rows if r["resolution"]})
    selected_group = series["selected_group"] or ""
    selected_resolution = series["selected_resolution"] or ""
    auto_download = series["auto_download"]

    if not selected_group:
        if len(groups) == 1 and bool_setting(settings["auto_download_unique"]):
            selected_group = groups[0]
        elif bool_setting(settings["auto_download_by_priority"]):
            selected_group = priority_pick(groups, split_lines(settings["subtitle_priority"]))

    if not selected_resolution:
        if len(resolutions) == 1 and bool_setting(settings["auto_download_unique"]):
            selected_resolution = resolutions[0]
        elif bool_setting(settings["auto_download_by_priority"]):
            selected_resolution = priority_pick(resolutions, split_lines(settings["resolution_priority"]))

    enabled = auto_download == "on" or (auto_download == "inherit" and bool_setting(settings["auto_download_unique"]))
    return selected_group, selected_resolution, enabled


def mark_selected_releases(series_id: int, group: str, resolution: str) -> list[int]:
    if not group or not resolution:
        return []
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT id FROM releases
            WHERE series_id=? AND subtitle_group=? AND resolution=?
            ORDER BY episode_number ASC
            """,
            (series_id, group, resolution),
        ).fetchall()
        ids = [r["id"] for r in rows]
        conn.execute("UPDATE releases SET selected=0 WHERE series_id=?", (series_id,))
        if ids:
            placeholders = ",".join("?" for _ in ids)
            conn.execute(f"UPDATE releases SET selected=1 WHERE id IN ({placeholders})", ids)
    return ids


def queue_release(release_id: int, settings: dict[str, str]) -> None:
    with connect() as conn:
        release = conn.execute("SELECT * FROM releases WHERE id=?", (release_id,)).fetchone()
        series = conn.execute("SELECT * FROM series WHERE id=?", (release["series_id"],)).fetchone()
        series_dict = dict(series)
        target = target_dir(series_dict, settings)
        name = render_episode_name(series_dict, release["episode_number"], "", settings)
        ts = now()
        conn.execute(
            """
            INSERT INTO download_tasks
              (release_id, series_id, status, target_dir, normalized_name, created_at, updated_at)
            VALUES (?, ?, 'pending', ?, ?, ?, ?)
            ON CONFLICT(release_id) DO NOTHING
            """,
            (release_id, release["series_id"], target, name, ts, ts),
        )


def extract_task_id(result: dict) -> str:
    for path in [
        ("task", "id"),
        ("tasks", 0, "id"),
        ("id",),
    ]:
        value = result
        try:
            for key in path:
                value = value[key]
        except (KeyError, IndexError, TypeError):
            continue
        if value:
            return str(value)
    return ""


def extract_file_id(result: dict) -> str:
    for path in [
        ("file", "id"),
        ("files", 0, "id"),
        ("task", "file_id"),
        ("reference_resource", "id"),
    ]:
        value = result
        try:
            for key in path:
                value = value[key]
        except (KeyError, IndexError, TypeError):
            continue
        if value:
            return str(value)
    return ""


async def process_tasks(settings: dict[str, str], limit: int = 5) -> None:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT dt.*, r.magnet, r.torrent_url, r.title
            FROM download_tasks dt
            JOIN releases r ON r.id = dt.release_id
            WHERE dt.status IN ('pending', 'failed') AND dt.attempts < 3
            ORDER BY dt.id ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    for task in rows:
        source = task["magnet"] or task["torrent_url"]
        if not source:
            continue
        with connect() as conn:
            conn.execute(
                "UPDATE download_tasks SET status='running', attempts=attempts+1, updated_at=? WHERE id=?",
                (now(), task["id"]),
            )
        try:
            result = await submit_offline_download(settings, source, task["target_dir"])
            task_id = extract_task_id(result) if isinstance(result, dict) else ""
            file_id = extract_file_id(result) if isinstance(result, dict) else ""
        except Exception as exc:
            with connect() as conn:
                conn.execute(
                    "UPDATE download_tasks SET status='failed', last_error=?, updated_at=? WHERE id=?",
                    (str(exc)[:2000], now(), task["id"]),
                )
            log("error", f"PikPak 提交失败: {task['title']} - {exc}")
        else:
            with connect() as conn:
                conn.execute(
                    """
                    UPDATE download_tasks
                    SET status='submitted', pikpak_task_id=?, pikpak_file_id=?, last_error='', updated_at=?
                    WHERE id=?
                    """,
                    (task_id, file_id, now(), task["id"]),
                )
            log("info", f"已提交 PikPak: {task['title']}")


async def poll_submitted_tasks(settings: dict[str, str]) -> None:
    with connect() as conn:
        local_tasks = conn.execute(
            """
            SELECT dt.*, r.title
            FROM download_tasks dt
            JOIN releases r ON r.id=dt.release_id
            WHERE dt.status IN ('submitted', 'running')
              AND (dt.pikpak_task_id != '' OR dt.pikpak_file_id != '')
            """
        ).fetchall()
    if not local_tasks:
        return

    try:
        remote_tasks = await list_offline_tasks(settings)
    except Exception as exc:
        log("error", f"PikPak 状态轮询失败: {exc}")
        return

    by_id = {task.get("id"): task for task in remote_tasks if task.get("id")}
    for task in local_tasks:
        remote = by_id.get(task["pikpak_task_id"])
        if not remote:
            continue
        phase = remote.get("phase", "")
        file_id = remote.get("file_id") or remote.get("reference_resource", {}).get("id", "") or task["pikpak_file_id"]
        if phase == "PHASE_TYPE_COMPLETE":
            status = "completed"
        elif phase == "PHASE_TYPE_ERROR":
            status = "failed"
        else:
            status = "submitted"
        with connect() as conn:
            conn.execute(
                """
                UPDATE download_tasks
                SET status=?, pikpak_file_id=?, last_error=?, updated_at=?
                WHERE id=?
                """,
                (status, file_id, remote.get("message", "")[:2000], now(), task["id"]),
            )
            if status == "completed":
                conn.execute(
                    "UPDATE episodes SET status='downloaded', updated_at=? WHERE series_id=? AND episode_number=(SELECT episode_number FROM releases WHERE id=?)",
                    (now(), task["series_id"], task["release_id"]),
                )
        if status == "completed" and file_id and task["normalized_name"]:
            try:
                await rename_cloud_file(settings, file_id, task["normalized_name"])
            except Exception as exc:
                log("warn", f"云端重命名失败: {task['title']} - {exc}")


async def scan_and_queue(settings: dict[str, str]) -> None:
    if not settings.get("rss_url"):
        log("warn", "未配置 Mikan RSS")
        return
    try:
        items = await fetch_entries(settings)
    except Exception as exc:
        log("error", f"RSS 扫描失败: {exc}")
        return

    touched_series: set[int] = set()
    for item in items:
        series_id, _ = upsert_release(item)
        touched_series.add(series_id)

    queued = 0
    for series_id in touched_series:
        with connect() as conn:
            series = conn.execute("SELECT metadata_source, bangumi_id FROM series WHERE id=?", (series_id,)).fetchone()
        if series and not series["metadata_source"]:
            await refresh_series_metadata(series_id, settings.get("rss_proxy", ""))
        group, resolution, enabled = resolve_series_choice(series_id, settings)
        ids = mark_selected_releases(series_id, group, resolution)
        if enabled:
            for release_id in ids:
                queue_release(release_id, settings)
                queued += 1
        generate_nfo_for_series(series_id, settings)

    log("info", f"扫描完成: {len(items)} 条发布，更新 {len(touched_series)} 部番剧，队列 {queued} 条")
    await process_tasks(settings)
