from __future__ import annotations

import httpx
import feedparser

from .db import connect, hide_orphan_series, log, merge_duplicate_series, now
from .library import bool_setting, render_episode_name, target_dir
from .metadata import generate_nfo_for_series, refresh_series_metadata
from .parser import ParsedRelease, fingerprint, parse_entry, split_lines
from .pikpak_service import list_offline_tasks, rename_cloud_file, submit_offline_download
from .sync_service import ensure_sync_rule, process_sync_tasks, reconcile_sync_intents, upsert_cloud_asset


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
              (fingerprint, title_raw, title_cn, bangumi_id, year, hidden, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 0, ?, ?)
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
              (series_id, episode_number, guid, title, subtitle_group, resolution, language,
               torrent_url, magnet, published_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(guid) DO UPDATE SET
              series_id=excluded.series_id,
              episode_number=excluded.episode_number,
              title=excluded.title,
              subtitle_group=excluded.subtitle_group,
              resolution=excluded.resolution,
              language=excluded.language,
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
                item.language,
                item.torrent_url,
                item.magnet,
                item.published_at,
                ts,
                ts,
            ),
        )
        release_id = conn.execute("SELECT id FROM releases WHERE guid=?", (item.guid,)).fetchone()["id"]
        conn.execute(
            "UPDATE download_tasks SET series_id=? WHERE release_id=?",
            (series_id, release_id),
        )
        conn.execute(
            "UPDATE cloud_assets SET series_id=? WHERE release_id=?",
            (series_id, release_id),
        )
        conn.execute(
            "UPDATE local_assets SET series_id=? WHERE release_id=?",
            (series_id, release_id),
        )
        conn.execute(
            "UPDATE sync_tasks SET series_id=? WHERE release_id=?",
            (series_id, release_id),
        )
    return series_id, release_id


def priority_pick(values: list[str], priority: list[str]) -> str:
    values_clean = [v for v in values if v]
    if not values_clean:
        return ""
    for preferred in priority:
        for value in values_clean:
            preferred_lower = preferred.lower()
            value_lower = value.lower()
            if preferred_lower == value_lower or preferred_lower in value_lower:
                return value
            if preferred in {"简体", "简中"} and value.startswith("简"):
                return value
            if preferred in {"繁体", "繁中"} and value.startswith("繁"):
                return value
            if preferred in {"日语", "日文"} and "日" in value:
                return value
            if preferred in {"英语", "英文"} and "英" in value:
                return value
    return values_clean[0] if len(set(values_clean)) == 1 else ""


def filter_by_priority(rows: list, field: str, priority: list[str]) -> tuple[list, str, str]:
    values = sorted({row[field] for row in rows if row[field]})
    if not values:
        return rows, "", ""
    if len(values) == 1:
        selected = values[0]
    else:
        selected = priority_pick(values, priority)
    if not selected:
        return rows, "", f"{field}存在多个候选: {', '.join(values)}"
    return [row for row in rows if row[field] == selected], selected, ""


def auto_download_enabled(series, settings: dict[str, str]) -> bool:
    value = series["auto_download"]
    return value == "on" or (value == "inherit" and bool_setting(settings.get("auto_download_unique", "true")))


def resolve_series_choice(series_id: int, settings: dict[str, str]) -> tuple[list[int], dict[str, str]]:
    with connect() as conn:
        series = conn.execute("SELECT * FROM series WHERE id=?", (series_id,)).fetchone()
        rows = conn.execute(
            """
            SELECT id, episode_number, subtitle_group, resolution, language
            FROM releases
            WHERE series_id=?
            ORDER BY episode_number ASC, id DESC
            """,
            (series_id,),
        ).fetchall()

    info = {
        "enabled": "true" if series and auto_download_enabled(series, settings) else "false",
        "selected_group": "",
        "selected_resolution": "",
        "selected_language": "",
        "reason": "",
    }
    if not series:
        info["reason"] = "番剧不存在"
        return [], info
    if not rows:
        info["reason"] = "没有可下载发布"
        return [], info
    if not auto_download_enabled(series, settings):
        info["reason"] = "自动下载已关闭"
        return [], info

    candidates = list(rows)
    selected_group = series["selected_group"] or ""
    if selected_group:
        candidates = [row for row in candidates if row["subtitle_group"] == selected_group]
        if not candidates:
            info["reason"] = f"没有匹配字幕组: {selected_group}"
            return [], info
    else:
        candidates, selected_group, reason = filter_by_priority(
            candidates,
            "subtitle_group",
            split_lines(settings.get("subtitle_priority", ""))
            if bool_setting(settings.get("auto_download_by_priority", "true"))
            else [],
        )
        if reason:
            info["reason"] = reason.replace("subtitle_group", "字幕组")
            return [], info

    selected_resolution = series["selected_resolution"] or ""
    if selected_resolution:
        candidates = [row for row in candidates if row["resolution"] == selected_resolution]
        if not candidates:
            info["reason"] = f"没有匹配分辨率: {selected_resolution}"
            return [], info
    else:
        candidates, selected_resolution, reason = filter_by_priority(
            candidates,
            "resolution",
            split_lines(settings.get("resolution_priority", ""))
            if bool_setting(settings.get("auto_download_by_priority", "true"))
            else [],
        )
        if reason:
            info["reason"] = reason.replace("resolution", "分辨率")
            return [], info

    candidates, selected_language, reason = filter_by_priority(
        candidates,
        "language",
        split_lines(settings.get("language_priority", ""))
        if bool_setting(settings.get("auto_download_by_priority", "true"))
        else [],
    )
    if reason:
        info["reason"] = reason.replace("language", "语言")
        return [], info

    by_episode: dict[int, list] = {}
    for row in candidates:
        by_episode.setdefault(row["episode_number"], []).append(row)
    ambiguous = {
        episode: episode_rows
        for episode, episode_rows in by_episode.items()
        if len(episode_rows) > 1
    }
    if ambiguous:
        info["reason"] = "过滤后仍存在同集多个发布，需要手动选择"
        return [], info

    ids = [episode_rows[0]["id"] for _, episode_rows in sorted(by_episode.items())]
    info.update(
        {
            "selected_group": selected_group,
            "selected_resolution": selected_resolution,
            "selected_language": selected_language,
        }
    )
    return ids, info


def mark_selected_releases(series_id: int, release_ids: list[int]) -> None:
    with connect() as conn:
        conn.execute("UPDATE releases SET selected=0 WHERE series_id=?", (series_id,))
    if not release_ids:
        return
    with connect() as conn:
        placeholders = ",".join("?" for _ in release_ids)
        conn.execute(f"UPDATE releases SET selected=1 WHERE id IN ({placeholders})", release_ids)


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
            with connect() as conn:
                conn.execute(
                    "UPDATE download_tasks SET status='failed', last_error=?, updated_at=? WHERE id=?",
                    ("发布缺少 magnet/torrent 链接", now(), task["id"]),
                )
            log("warn", f"下载任务跳过: {task['title']} - 发布缺少 magnet/torrent 链接")
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
        if status == "completed":
            asset_id = upsert_cloud_asset(task["id"], settings)
            if asset_id:
                _, queued = reconcile_sync_intents(settings)
                if queued:
                    await process_sync_tasks(settings)


async def scan_and_queue(settings: dict[str, str]) -> str:
    if not settings.get("rss_url"):
        log("warn", "未配置 Mikan RSS")
        return "未配置 Mikan RSS"
    try:
        items = await fetch_entries(settings)
    except Exception as exc:
        log("error", f"RSS 扫描失败: {exc}")
        return f"RSS 扫描失败: {exc}"

    touched_release_ids: set[int] = set()
    for item in items:
        _, release_id = upsert_release(item)
        touched_release_ids.add(release_id)
    with connect() as conn:
        merge_duplicate_series(conn)
        hidden = hide_orphan_series(conn)
        if hidden:
            log("info", f"已隐藏无资源临时条目: {hidden} 个")

    queued = 0
    with connect() as conn:
        placeholders = ",".join("?" for _ in touched_release_ids)
        touched_series = {
            row["series_id"]
            for row in conn.execute(
                f"SELECT DISTINCT series_id FROM releases WHERE id IN ({placeholders})",
                list(touched_release_ids),
            ).fetchall()
        } if touched_release_ids else set()
    for series_id in touched_series:
        with connect() as conn:
            series = conn.execute("SELECT metadata_source, bangumi_id FROM series WHERE id=?", (series_id,)).fetchone()
        if series and not series["metadata_source"]:
            await refresh_series_metadata(series_id, settings.get("rss_proxy", ""))
        ensure_sync_rule(series_id, settings)
        ids, choice = resolve_series_choice(series_id, settings)
        mark_selected_releases(series_id, ids)
        if choice["reason"]:
            with connect() as conn:
                series_title = conn.execute("SELECT title_cn FROM series WHERE id=?", (series_id,)).fetchone()
            log("warn", f"自动下载跳过: {series_title['title_cn'] if series_title else series_id} - {choice['reason']}")
        for release_id in ids:
            queue_release(release_id, settings)
            queued += 1
        generate_nfo_for_series(series_id, settings)

    log("info", f"扫描完成: {len(items)} 条发布，更新 {len(touched_series)} 部番剧，队列 {queued} 条")
    await process_tasks(settings)
    from .sync_service import scan_cloud_library

    try:
        imported, skipped = await scan_cloud_library(settings)
        if imported:
            log("info", f"云盘库扫描完成: 入库 {imported} 个，跳过 {skipped} 个")
    except Exception as exc:
        log("warn", f"云盘库扫描跳过: {exc}")
        imported = 0
        skipped = 0
    reconciled, sync_queued = reconcile_sync_intents(settings)
    if sync_queued:
        await process_sync_tasks(settings)
    return f"RSS {len(items)} 条，更新 {len(touched_series)} 部，云盘队列 {queued} 条，云盘入库 {imported} 个，同步排队 {sync_queued} 个"
