from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin

import httpx
import feedparser

from .db import connect, hide_orphan_series, log, merge_duplicate_series, now
from .library import bool_setting, render_episode_name, target_dir
from .metadata import fetch_bangumi_metadata
from .parser import ParsedRelease, fingerprint, parse_entry, split_lines
from .pikpak_service import list_offline_tasks, rename_cloud_file, submit_offline_download
from .sync_service import process_sync_tasks, reconcile_sync_intents, upsert_cloud_asset


async def fetch_entries(settings: dict[str, str]) -> list[ParsedRelease]:
    proxy = settings.get("rss_proxy") or None
    async with httpx.AsyncClient(proxy=proxy, timeout=30, follow_redirects=True) as client:
        resp = await client.get(settings["rss_url"])
        resp.raise_for_status()
    parsed = feedparser.parse(resp.text)
    return [parse_entry(entry) for entry in parsed.entries]


def mikan_absolute_url(path_or_url: str) -> str:
    return urljoin("https://mikanani.me", path_or_url or "")


def parse_mikan_ids(html: str) -> tuple[str, str]:
    bangumi_match = re.search(r"https?://(?:bgm\.tv|bangumi\.tv)/subject/(\d+)", html, re.I)
    mikan_match = re.search(r"/Home/Bangumi/(\d+)", html, re.I)
    return (bangumi_match.group(1) if bangumi_match else "", mikan_match.group(1) if mikan_match else "")


async def fetch_mikan_match(settings: dict[str, str], page_url: str, mikan_bangumi_id: str = "") -> tuple[str, str]:
    if not page_url and not mikan_bangumi_id:
        return "", ""
    proxy = settings.get("rss_proxy") or None
    first_url = mikan_absolute_url(page_url or f"/Home/Bangumi/{mikan_bangumi_id}")
    async with httpx.AsyncClient(proxy=proxy, timeout=30, follow_redirects=True) as client:
        resp = await client.get(first_url)
        resp.raise_for_status()
        bangumi_id, mikan_id = parse_mikan_ids(resp.text)
        mikan_id = mikan_id or mikan_bangumi_id
        if bangumi_id:
            return bangumi_id, mikan_id
        if mikan_id:
            bgm_url = mikan_absolute_url(f"/Home/Bangumi/{mikan_id}")
            bgm_resp = await client.get(bgm_url)
            bgm_resp.raise_for_status()
            bangumi_id, _ = parse_mikan_ids(bgm_resp.text)
            return bangumi_id, mikan_id
    return "", ""


def upsert_release(item: ParsedRelease, metadata: dict | None = None) -> tuple[int, int]:
    fp = fingerprint(item.series_title or item.title, item.bangumi_id)
    metadata = metadata or {}
    with connect() as conn:
        ts = now()
        conn.execute(
            """
            INSERT INTO series
              (fingerprint, title_raw, title_cn, bangumi_id, year, poster_url, summary,
               metadata_source, hidden, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'bangumi', 0, ?, ?)
            ON CONFLICT(fingerprint) DO UPDATE SET
              title_raw=excluded.title_raw,
              title_cn=CASE WHEN excluded.title_cn!='' THEN excluded.title_cn ELSE series.title_cn END,
              bangumi_id=CASE WHEN series.bangumi_id='' THEN excluded.bangumi_id ELSE series.bangumi_id END,
              year=CASE WHEN excluded.year!=0 THEN excluded.year ELSE series.year END,
              poster_url=CASE WHEN excluded.poster_url!='' THEN excluded.poster_url ELSE series.poster_url END,
              summary=CASE WHEN excluded.summary!='' THEN excluded.summary ELSE series.summary END,
              metadata_source='bangumi',
              updated_at=excluded.updated_at
            """,
            (
                fp,
                item.series_title,
                metadata.get("title_cn") or item.series_title,
                item.bangumi_id,
                metadata.get("year") or item.year,
                metadata.get("poster_url") or "",
                metadata.get("summary") or "",
                ts,
                ts,
            ),
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


def upsert_rss_candidate(item: ParsedRelease, reason: str = "") -> int:
    with connect() as conn:
        ts = now()
        status = "pending_metadata" if item.bangumi_id else "pending"
        reason = reason or ("等待元数据刷新" if item.bangumi_id else "RSS 未提供 Bangumi ID")
        conn.execute(
            """
            INSERT INTO rss_candidates
              (guid, title, series_title, episode_number, subtitle_group, resolution,
               language, bangumi_id, torrent_url, magnet, page_url, published_at, status,
               reason, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(guid) DO UPDATE SET
              title=excluded.title,
              series_title=excluded.series_title,
              episode_number=excluded.episode_number,
              subtitle_group=excluded.subtitle_group,
              resolution=excluded.resolution,
              language=excluded.language,
              bangumi_id=excluded.bangumi_id,
              torrent_url=excluded.torrent_url,
              magnet=excluded.magnet,
              page_url=excluded.page_url,
              published_at=excluded.published_at,
              status=CASE WHEN rss_candidates.status='completed' THEN rss_candidates.status ELSE excluded.status END,
              reason=excluded.reason,
              updated_at=excluded.updated_at
            """,
            (
                item.guid,
                item.title,
                item.series_title,
                item.episode_number,
                item.subtitle_group,
                item.resolution,
                item.language,
                item.bangumi_id,
                item.torrent_url,
                item.magnet,
                item.page_url,
                item.published_at,
                status,
                reason,
                ts,
                ts,
            ),
        )
        row = conn.execute("SELECT id FROM rss_candidates WHERE guid=?", (item.guid,)).fetchone()
        candidate_id = int(row["id"])
        if item.bangumi_id:
            enqueue_metadata_task(conn, candidate_id, item.bangumi_id, ts)
        else:
            conn.execute(
                """
                INSERT INTO mikan_match_tasks
                  (candidate_id, status, mikan_url, mikan_bangumi_id, created_at, updated_at)
                VALUES (?, 'pending', ?, ?, ?, ?)
                ON CONFLICT(candidate_id) DO UPDATE SET
                  status=CASE WHEN mikan_match_tasks.status='completed' THEN mikan_match_tasks.status ELSE 'pending' END,
                  mikan_url=excluded.mikan_url,
                  mikan_bangumi_id=CASE WHEN excluded.mikan_bangumi_id!='' THEN excluded.mikan_bangumi_id ELSE mikan_match_tasks.mikan_bangumi_id END,
                  last_error='',
                  updated_at=excluded.updated_at
                """,
                (candidate_id, item.page_url, item.mikan_bangumi_id, ts, ts),
            )
        return candidate_id


def enqueue_metadata_task(conn, candidate_id: int, bangumi_id: str, ts: str) -> None:
    conn.execute(
        """
        INSERT INTO metadata_tasks
          (candidate_id, status, bangumi_id, created_at, updated_at)
        VALUES (?, 'pending', ?, ?, ?)
        ON CONFLICT(candidate_id) DO UPDATE SET
          status=CASE WHEN metadata_tasks.status='completed' THEN metadata_tasks.status ELSE 'pending' END,
          bangumi_id=excluded.bangumi_id,
          last_error='',
          updated_at=excluded.updated_at
        """,
        (candidate_id, bangumi_id, ts, ts),
    )


def candidate_to_parsed_release(candidate) -> ParsedRelease:
    return ParsedRelease(
        guid=candidate["guid"],
        title=candidate["title"],
        series_title=candidate["series_title"],
        episode_number=candidate["episode_number"],
        subtitle_group=candidate["subtitle_group"],
        resolution=candidate["resolution"],
        language=candidate["language"],
        bangumi_id=candidate["bangumi_id"],
        year=0,
        torrent_url=candidate["torrent_url"],
        magnet=candidate["magnet"],
        page_url=candidate["page_url"],
        mikan_bangumi_id="",
        published_at=candidate["published_at"],
    )


async def process_mikan_match_tasks(settings: dict[str, str], limit: int = 20) -> tuple[int, int]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT mt.*, rc.title, rc.page_url
            FROM mikan_match_tasks mt
            JOIN rss_candidates rc ON rc.id=mt.candidate_id
            WHERE mt.status IN ('pending', 'failed') AND mt.attempts < 3
            ORDER BY mt.id ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    completed = 0
    failed = 0
    for row in rows:
        with connect() as conn:
            conn.execute(
                "UPDATE mikan_match_tasks SET status='running', attempts=attempts+1, updated_at=? WHERE id=?",
                (now(), row["id"]),
            )
        try:
            bangumi_id, mikan_id = await fetch_mikan_match(
                settings,
                row["page_url"] or row["mikan_url"],
                row["mikan_bangumi_id"] or "",
            )
            if not bangumi_id:
                raise RuntimeError("Mikan 页面未找到 Bangumi subject 链接")
        except Exception as exc:
            error = str(exc)[:2000]
            with connect() as conn:
                conn.execute(
                    "UPDATE mikan_match_tasks SET status='failed', last_error=?, updated_at=? WHERE id=?",
                    (error, now(), row["id"]),
                )
                conn.execute(
                    "UPDATE rss_candidates SET status='failed', reason=?, updated_at=? WHERE id=?",
                    (error, now(), row["candidate_id"]),
                )
            failed += 1
            log("warn", f"Mikan 匹配失败: {row['title']} - {error}")
            continue
        with connect() as conn:
            ts = now()
            conn.execute(
                """
                UPDATE mikan_match_tasks
                SET status='completed', bangumi_id=?, mikan_bangumi_id=?, last_error='', updated_at=?
                WHERE id=?
                """,
                (bangumi_id, mikan_id, ts, row["id"]),
            )
            conn.execute(
                """
                UPDATE rss_candidates
                SET status='pending_metadata', bangumi_id=?, reason='等待元数据刷新', updated_at=?
                WHERE id=?
                """,
                (bangumi_id, ts, row["candidate_id"]),
            )
            enqueue_metadata_task(conn, row["candidate_id"], bangumi_id, ts)
        completed += 1
    return completed, failed


async def process_metadata_tasks(settings: dict[str, str], limit: int = 20) -> tuple[int, int]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT mt.*, rc.*
            FROM metadata_tasks mt
            JOIN rss_candidates rc ON rc.id=mt.candidate_id
            WHERE mt.status IN ('pending', 'failed') AND mt.attempts < 3
            ORDER BY mt.id ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    completed = 0
    failed = 0
    for row in rows:
        with connect() as conn:
            conn.execute(
                "UPDATE metadata_tasks SET status='running', attempts=attempts+1, updated_at=? WHERE id=?",
                (now(), row["id"]),
            )
        if not row["bangumi_id"]:
            error = "缺少 Bangumi ID"
            with connect() as conn:
                conn.execute(
                    "UPDATE metadata_tasks SET status='failed', last_error=?, updated_at=? WHERE id=?",
                    (error, now(), row["id"]),
                )
                conn.execute(
                    "UPDATE rss_candidates SET status='failed', reason=?, updated_at=? WHERE id=?",
                    (error, now(), row["candidate_id"]),
                )
            failed += 1
            continue
        try:
            metadata = await fetch_bangumi_metadata(row["bangumi_id"], settings.get("rss_proxy", ""))
            release = candidate_to_parsed_release(row)
            series_id, release_id = upsert_release(release, metadata)
        except Exception as exc:
            error = str(exc)[:2000]
            with connect() as conn:
                conn.execute(
                    "UPDATE metadata_tasks SET status='failed', last_error=?, updated_at=? WHERE id=?",
                    (error, now(), row["id"]),
                )
                conn.execute(
                    "UPDATE rss_candidates SET status='failed', reason=?, updated_at=? WHERE id=?",
                    (error, now(), row["candidate_id"]),
                )
            log("error", f"元数据刷新失败: {row['title']} - {error}")
            failed += 1
            continue
        with connect() as conn:
            conn.execute(
                "UPDATE metadata_tasks SET status='completed', last_error='', updated_at=? WHERE id=?",
                (now(), row["id"]),
            )
            conn.execute(
                "UPDATE rss_candidates SET status='completed', reason='', updated_at=? WHERE id=?",
                (now(), row["candidate_id"]),
            )
        ids, choice = resolve_series_choice(series_id, settings)
        mark_selected_releases(series_id, ids)
        if choice["reason"]:
            log("warn", f"自动入库跳过: {metadata.get('title_cn') or row['series_title']} - {choice['reason']}")
        for selected_release_id in ids:
            queue_release(selected_release_id, settings)
        completed += 1
    return completed, failed


def priority_match(value: str, preferred: str, field: str = "") -> bool:
    preferred_lower = preferred.lower()
    value_lower = value.lower()
    if preferred_lower == value_lower or preferred_lower in value_lower:
        return True
    if field == "language":
        if preferred in {"简体", "简中"}:
            return value.startswith("简")
        if preferred in {"繁体", "繁中"}:
            return value.startswith("繁")
        if preferred in {"日语", "日文"}:
            return "日" in value
        if preferred in {"英语", "英文"}:
            return "英" in value
        return False
    if preferred in {"简体", "简中"} and value.startswith("简"):
        return True
    if preferred in {"繁体", "繁中"} and value.startswith("繁"):
        return True
    if preferred in {"日语", "日文"} and "日" in value:
        return True
    if preferred in {"英语", "英文"} and "英" in value:
        return True
    return False


def priority_pick(values: list[str], priority: list[str], field: str = "") -> str:
    values_clean = sorted({v for v in values if v})
    if not values_clean:
        return ""
    for preferred in priority:
        exact = [value for value in values_clean if value.lower() == preferred.lower()]
        if len(exact) == 1:
            return exact[0]
        matched = [value for value in values_clean if priority_match(value, preferred, field)]
        if len(matched) == 1:
            return matched[0]
        if len(matched) > 1:
            return ""
    return values_clean[0] if len(set(values_clean)) == 1 else ""


def language_tokens(value: str) -> list[str]:
    text = value or ""
    tokens: list[str] = []
    if text.startswith("简") or "简体" in text or "简中" in text:
        tokens.append("简体")
    if text.startswith("繁") or "繁体" in text or "繁中" in text:
        tokens.append("繁体")
    if "日" in text:
        tokens.append("日语")
    if "英" in text:
        tokens.append("英语")
    if text == "中文" and not tokens:
        tokens.append("中文")
    return tokens


def rank_by_language(values: list[str], priority: list[str], token_index: int) -> tuple[list[str], str, str]:
    values_clean = sorted({v for v in values if v})
    if not values_clean or not priority:
        return values_clean, "", ""
    for preferred in priority:
        matched = [
            value
            for value in values_clean
            if len(language_tokens(value)) > token_index
            and priority_match(language_tokens(value)[token_index], preferred, "language")
        ]
        if len(matched) == 1:
            return matched, preferred, ""
        if len(matched) > 1:
            return matched, preferred, ""
    return values_clean, "", ""


def filter_by_priority(rows: list, field: str, priority: list[str]) -> tuple[list, str, str]:
    values = sorted({row[field] for row in rows if row[field]})
    if not values:
        return rows, "", ""
    if len(values) == 1:
        selected = values[0]
    else:
        selected = priority_pick(values, priority, field)
    if not selected:
        return rows, "", f"{field}存在多个候选: {', '.join(values)}"
    return [row for row in rows if row[field] == selected], selected, ""


def filter_by_language_priority(
    rows: list,
    primary_priority: list[str],
    secondary_priority: list[str],
) -> tuple[list, str, str]:
    values = sorted({row["language"] for row in rows if row["language"]})
    if not values:
        return rows, "", ""
    if len(values) == 1:
        return rows, values[0], ""
    primary_values, primary_selected, _ = rank_by_language(values, primary_priority, 0)
    if len(primary_values) == 1:
        selected = primary_values[0]
        return [row for row in rows if row["language"] == selected], selected, ""
    if primary_selected:
        rows = [row for row in rows if row["language"] in primary_values]
        values = primary_values
    secondary_values, _, _ = rank_by_language(values, secondary_priority, 1)
    if len(secondary_values) == 1:
        selected = secondary_values[0]
        return [row for row in rows if row["language"] == selected], selected, ""
    if len(secondary_values) > 1:
        rows = [row for row in rows if row["language"] in secondary_values]
        values = secondary_values
    return rows, "", f"language存在多个候选: {', '.join(values)}"


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
    if not series["bangumi_id"]:
        info["reason"] = "缺少 Bangumi ID，不能进入自动入库"
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

    candidates, selected_language, reason = filter_by_language_priority(
        candidates,
        split_lines(settings.get("language_priority", ""))
        if bool_setting(settings.get("auto_download_by_priority", "true"))
        else [],
        split_lines(settings.get("secondary_language_priority", ""))
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
        if not release:
            return
        series = conn.execute("SELECT * FROM series WHERE id=?", (release["series_id"],)).fetchone()
        if not series:
            return
        if not series["bangumi_id"]:
            log("warn", f"云盘入库跳过: {series['title_cn']} - 缺少 Bangumi ID")
            return
        series_dict = dict(series)
        target = target_dir(series_dict, settings)
        name = render_episode_name(series_dict, release["episode_number"], "", settings)
        ts = now()
        conn.execute(
            """
            INSERT INTO download_tasks
              (release_id, series_id, status, target_dir, normalized_name, retry_after, created_at, updated_at)
            VALUES (?, ?, 'pending', ?, ?, '', ?, ?)
            ON CONFLICT(release_id) DO UPDATE SET
              status=CASE
                WHEN download_tasks.status IN ('completed','submitted','running') THEN download_tasks.status
                ELSE 'pending'
              END,
              target_dir=excluded.target_dir,
              normalized_name=excluded.normalized_name,
              updated_at=excluded.updated_at
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


def is_rate_limited_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "too frequent" in text or "try again later" in text or "rate" in text and "limit" in text


def retry_after_time(settings: dict[str, str]) -> str:
    minutes = max(1, int(settings.get("pikpak_rate_limit_cooldown_minutes") or 15))
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()


async def process_tasks(settings: dict[str, str], limit: int = 1) -> None:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT dt.*, r.magnet, r.torrent_url, r.title
            FROM download_tasks dt
            JOIN releases r ON r.id = dt.release_id
            JOIN series s ON s.id = dt.series_id
            WHERE dt.status IN ('pending', 'failed') AND dt.attempts < 3
              AND s.bangumi_id != ''
              AND (dt.retry_after='' OR dt.retry_after <= ?)
            ORDER BY dt.id ASC
            LIMIT ?
            """,
            (now(), limit),
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
            if is_rate_limited_error(exc):
                retry_after = retry_after_time(settings)
                with connect() as conn:
                    conn.execute(
                        """
                        UPDATE download_tasks
                        SET status='pending', retry_after=?, last_error=?, updated_at=?
                        WHERE id=?
                        """,
                        (retry_after, f"PikPak 限流，等待后自动重试: {str(exc)[:1800]}", now(), task["id"]),
                    )
                log("warn", f"PikPak 限流，已延后重试: {task['title']} - {exc}")
                break
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
                    SET status='submitted', pikpak_task_id=?, pikpak_file_id=?, retry_after='', last_error='', updated_at=?
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
            JOIN series s ON s.id=dt.series_id
            WHERE dt.status IN ('submitted', 'running')
              AND (dt.pikpak_task_id != '' OR dt.pikpak_file_id != '')
              AND s.bangumi_id != ''
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

    candidate_count = 0
    for item in items:
        upsert_rss_candidate(item)
        candidate_count += 1
    log("info", f"RSS 扫描完成: {len(items)} 条发布，写入候选 {candidate_count} 条")
    return f"RSS {len(items)} 条，写入候选 {candidate_count} 条"
