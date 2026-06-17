from __future__ import annotations

import asyncio
import html
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin

import httpx
import feedparser

from .database import connect
from .db import hide_orphan_series, log, merge_duplicate_series, now
from .queue_bridge import request_queue_trigger
from .library import bool_setting, parse_entry_labels, render_episode_name, target_dir
from .metadata import fetch_bangumi_metadata
from .parser import ParsedRelease, fingerprint, normalize_title_key, parse_entry, parse_episode, parse_group, parse_language, parse_resolution, parse_series_title, parse_year, split_lines
from .pikpak_service import list_offline_tasks, rename_cloud_file, submit_offline_download
from .sync_service import enqueue_cloud_asset_task
from . import rclone_service

download_tasks_lock = asyncio.Lock()
cloud_presence_tasks_lock = asyncio.Lock()
download_enqueue_tasks_lock = asyncio.Lock()
mikan_match_lock = asyncio.Lock()
metadata_tasks_lock = asyncio.Lock()
selection_tasks_lock = asyncio.Lock()
backfill_tasks_lock = asyncio.Lock()


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
    if not mikan_match:
        mikan_match = re.search(r'data-bangumiid="(\d+)"', html, re.I)
    return (bangumi_match.group(1) if bangumi_match else "", mikan_match.group(1) if mikan_match else "")


def parse_episode_page_mikan_id(html_text: str) -> str:
    patterns = [
        r'href="/Home/Bangumi/(\d+)(?:#\d+)?"',
        r"onclick=\"window\.open\('/Home/Bangumi/(\d+)(?:#\d+)?'",
        r'data-bangumiid="(\d+)"',
        r"/RSS/Bangumi\?bangumiId=(\d+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, html_text, re.I)
        if match:
            return match.group(1)
    return ""


async def fetch_mikan_match(settings: dict[str, str], page_url: str, mikan_bangumi_id: str = "") -> tuple[str, str]:
    if not page_url and not mikan_bangumi_id:
        return "", ""
    proxy = settings.get("rss_proxy") or None
    first_url = mikan_absolute_url(page_url or f"/Home/Bangumi/{mikan_bangumi_id}")
    async with httpx.AsyncClient(proxy=proxy, timeout=30, follow_redirects=True) as client:
        resp = await client.get(first_url)
        resp.raise_for_status()
        bangumi_id, mikan_id = parse_mikan_ids(resp.text)
        mikan_id = mikan_id or parse_episode_page_mikan_id(resp.text) or mikan_bangumi_id
        if bangumi_id and mikan_id:
            return bangumi_id, mikan_id
        if mikan_id:
            bgm_url = mikan_absolute_url(f"/Home/Bangumi/{mikan_id}")
            bgm_resp = await client.get(bgm_url)
            bgm_resp.raise_for_status()
            bangumi_id, parsed_mikan_id = parse_mikan_ids(bgm_resp.text)
            mikan_id = parsed_mikan_id or mikan_id
            return bangumi_id, mikan_id
    return "", ""


def parse_mikan_datetime(value: str) -> str:
    text = (value or "").strip()
    match = re.search(r"(\d{4}/\d{2}/\d{2}\s+\d{2}:\d{2})", text)
    return match.group(1) if match else text


def parse_mikan_group_sections(html_text: str) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    marker = re.compile(r'<div class="subgroup-text" id="(?P<group_id>\d+)">', re.S)
    matches = list(marker.finditer(html_text))
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(html_text)
        group_id = match.group("group_id") or ""
        section = html_text[start:end]
        if group_id:
            sections.append((group_id, section))
    return sections


def parse_mikan_page_releases(html_text: str, mikan_bangumi_id: str) -> list[ParsedRelease]:
    releases: list[ParsedRelease] = []
    seen_guids: set[str] = set()
    for group_id, section in parse_mikan_group_sections(html_text):
        group_match = re.search(r'<a href="/Home/PublishGroup/\d+"[^>]*>(.*?)</a>', section, re.S)
        group_name = html.unescape(re.sub(r"<.*?>", "", group_match.group(1)).strip()) if group_match else ""
        row_pattern = re.compile(
            r'<tr>.*?class="js-episode-select"[^>]*data-magnet="(?P<magnet>[^"]*)".*?'
            r'<a class="magnet-link-wrap"[^>]*href="(?P<page>[^"]+)">(?P<title>.*?)</a>.*?'
            r'<td>(?P<size>.*?)</td>.*?<td>(?P<published>.*?)</td>.*?'
            r'<a\s+href="(?P<torrent>[^"]+\.torrent)">',
            re.S,
        )
        for row in row_pattern.finditer(section):
            raw_title = html.unescape(re.sub(r"<.*?>", "", row.group("title")).strip())
            magnet = html.unescape(row.group("magnet")).replace("&amp;", "&")
            page_url = mikan_absolute_url(html.unescape(row.group("page")))
            torrent_url = mikan_absolute_url(html.unescape(row.group("torrent")))
            published_at = parse_mikan_datetime(html.unescape(re.sub(r"<.*?>", "", row.group("published")).strip()))
            guid_match = re.search(r"/Home/Episode/([0-9a-fA-F]{20,40})", page_url)
            guid = guid_match.group(1) if guid_match else page_url
            if guid in seen_guids:
                continue
            seen_guids.add(guid)
            parsed = ParsedRelease(
                guid=guid,
                title=raw_title,
                series_title=parse_series_title(raw_title),
                episode_number=parse_episode(raw_title),
                subtitle_group=group_name or parse_group(raw_title),
                resolution=parse_resolution(raw_title),
                language=parse_language(raw_title),
                bangumi_id="",
                year=parse_year(raw_title, published_at),
                torrent_url=torrent_url,
                magnet=magnet,
                page_url=page_url,
                mikan_bangumi_id=mikan_bangumi_id,
                published_at=published_at,
            )
            if parsed.episode_number > 0:
                releases.append(parsed)
    return releases


async def fetch_mikan_page_releases(settings: dict[str, str], mikan_bangumi_id: str) -> list[ParsedRelease]:
    if not mikan_bangumi_id:
        return []
    proxy = settings.get("rss_proxy") or None
    url = mikan_absolute_url(f"/Home/Bangumi/{mikan_bangumi_id}")
    async with httpx.AsyncClient(proxy=proxy, timeout=30, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
    return parse_mikan_page_releases(resp.text, mikan_bangumi_id)


async def resolve_entry_mikan_bangumi_id(settings: dict[str, str], entry_id: int, bangumi_id: str) -> str:
    with connect() as conn:
        entry = conn.execute(
            "SELECT display_title, title_cn, title_raw, bangumi_id FROM entries WHERE id=?",
            (entry_id,),
        ).fetchone()
        candidates = []
        if bangumi_id:
            candidates = conn.execute(
                """
                SELECT id, page_url, series_title, bangumi_id
                FROM rss_candidates
                WHERE bangumi_id=?
                  AND page_url != ''
                ORDER BY updated_at DESC, id DESC
                LIMIT 10
                """,
                (bangumi_id,),
            ).fetchall()
        if not candidates and entry:
            title_keys = {
                normalize_title_key(str(entry["display_title"] or "")),
                normalize_title_key(str(entry["title_cn"] or "")),
                normalize_title_key(str(entry["title_raw"] or "")),
            }
            title_keys = {value for value in title_keys if value}
            rows = conn.execute(
                """
                SELECT id, page_url, series_title, bangumi_id
                FROM rss_candidates
                WHERE page_url != ''
                ORDER BY updated_at DESC, id DESC
                LIMIT 80
                """
            ).fetchall()
            for row in rows:
                row_key = normalize_title_key(str(row["series_title"] or ""))
                if row_key and row_key in title_keys:
                    candidates.append(row)
                if len(candidates) >= 10:
                    break
    log("info", f"Mikan ID 反查候选: entry_id={entry_id} bangumi_id={bangumi_id or '-'} candidates={len(candidates)}")
    for candidate in candidates:
        try:
            log("info", f"Mikan ID 反查尝试: entry_id={entry_id} candidate_id={candidate['id']} page={candidate['page_url']}")
            matched_bangumi_id, mikan_id = await fetch_mikan_match(settings, str(candidate["page_url"] or ""), "")
        except Exception:
            log("warn", f"Mikan ID 反查失败: entry_id={entry_id} candidate_id={candidate['id']} page={candidate['page_url']}")
            continue
        if matched_bangumi_id == bangumi_id and mikan_id:
            ts = now()
            with connect() as conn:
                conn.execute("UPDATE entries SET mikan_bangumi_id=?, updated_at=? WHERE id=?", (mikan_id, ts, entry_id))
                conn.execute(
                    "UPDATE series SET mikan_bangumi_id=?, updated_at=? WHERE bangumi_id=?",
                    (mikan_id, ts, bangumi_id),
                )
                conn.execute(
                    """
                    UPDATE rss_candidates
                    SET mikan_bangumi_id=?, updated_at=?
                    WHERE bangumi_id=?
                      AND mikan_bangumi_id=''
                    """,
                    (mikan_id, ts, bangumi_id),
                )
                conn.execute(
                    """
                    UPDATE mikan_match_tasks
                    SET mikan_bangumi_id=?, bangumi_id=?, updated_at=?
                    WHERE candidate_id=?
                    """,
                    (mikan_id, bangumi_id, ts, candidate["id"]),
                )
            log("info", f"Mikan ID 反查命中: entry_id={entry_id} bangumi_id={bangumi_id} mikan_id={mikan_id}")
            return mikan_id
        log("info", f"Mikan ID 反查未命中: entry_id={entry_id} candidate_id={candidate['id']} matched_bangumi={matched_bangumi_id or '-'} mikan_id={mikan_id or '-'}")
    return ""


async def gather_limited(coros, limit: int = 4):
    semaphore = asyncio.Semaphore(max(1, limit))

    async def run(coro):
        async with semaphore:
            return await coro

    return await asyncio.gather(*(run(coro) for coro in coros))


def upsert_release(item: ParsedRelease, metadata: dict | None = None) -> tuple[int, int, int]:
    fp = fingerprint(item.series_title or item.title, item.bangumi_id)
    metadata = metadata or {}
    labels = parse_entry_labels(metadata.get("title_cn") or item.series_title)
    root_title = str(labels["title_root"] or (metadata.get("title_cn") or item.series_title))
    work_key = fingerprint(root_title, "")
    with connect() as conn:
        ts = now()
        conn.execute(
            """
            INSERT INTO works
              (root_key, title_root, title_root_raw, bangumi_id, metadata_source, hidden, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'bangumi', 0, ?, ?)
            ON CONFLICT(root_key) DO UPDATE SET
              title_root=excluded.title_root,
              title_root_raw=excluded.title_root_raw,
              bangumi_id=CASE WHEN works.bangumi_id='' THEN excluded.bangumi_id ELSE works.bangumi_id END,
              metadata_source='bangumi',
              hidden=0,
              updated_at=excluded.updated_at
            """,
            (
                work_key,
                root_title,
                item.series_title,
                item.bangumi_id,
                ts,
                ts,
            ),
        )
        work_id = conn.execute("SELECT id FROM works WHERE root_key=?", (work_key,)).fetchone()["id"]
        conn.execute(
            """
            INSERT INTO series
              (fingerprint, title_raw, title_cn, bangumi_id, mikan_bangumi_id, year, poster_url, summary,
               metadata_source, hidden, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'bangumi', 0, ?, ?)
            ON CONFLICT(fingerprint) DO UPDATE SET
              title_raw=excluded.title_raw,
              title_cn=CASE WHEN excluded.title_cn!='' THEN excluded.title_cn ELSE series.title_cn END,
              bangumi_id=CASE WHEN series.bangumi_id='' THEN excluded.bangumi_id ELSE series.bangumi_id END,
              mikan_bangumi_id=CASE WHEN excluded.mikan_bangumi_id!='' THEN excluded.mikan_bangumi_id ELSE series.mikan_bangumi_id END,
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
                item.mikan_bangumi_id,
                metadata.get("year") or item.year,
                metadata.get("poster_url") or "",
                metadata.get("summary") or "",
                ts,
                ts,
            ),
        )
        series_id = conn.execute("SELECT id FROM series WHERE fingerprint=?", (fp,)).fetchone()["id"]
        conn.execute(
            """
            INSERT INTO entries
              (work_id, fingerprint, domain_kind, entry_kind, display_title, title_root,
               season_label, arc_label, part_label, special_label,
               title_raw, title_cn, bangumi_id, mikan_bangumi_id, tmdb_id, year, season_number,
               poster_url, summary, metadata_source, hidden, auto_download, selected_group, selected_resolution,
               backfill_mode, created_at, updated_at)
            VALUES (?, ?, 'seasonal', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, 'bangumi', 0, 'inherit', '', '', 'inherit', ?, ?)
            ON CONFLICT(fingerprint) DO UPDATE SET
              work_id=excluded.work_id,
              domain_kind='seasonal',
              entry_kind=excluded.entry_kind,
              display_title=excluded.display_title,
              title_root=excluded.title_root,
              season_label=excluded.season_label,
              arc_label=excluded.arc_label,
              part_label=excluded.part_label,
              special_label=excluded.special_label,
              title_raw=excluded.title_raw,
              title_cn=excluded.title_cn,
              bangumi_id=CASE WHEN entries.bangumi_id='' THEN excluded.bangumi_id ELSE entries.bangumi_id END,
              mikan_bangumi_id=CASE WHEN excluded.mikan_bangumi_id!='' THEN excluded.mikan_bangumi_id ELSE entries.mikan_bangumi_id END,
              year=CASE WHEN excluded.year!=0 THEN excluded.year ELSE entries.year END,
              season_number=CASE WHEN excluded.season_number!=0 THEN excluded.season_number ELSE entries.season_number END,
              poster_url=CASE WHEN excluded.poster_url!='' THEN excluded.poster_url ELSE entries.poster_url END,
              summary=CASE WHEN excluded.summary!='' THEN excluded.summary ELSE entries.summary END,
              metadata_source='bangumi',
              hidden=0,
              updated_at=excluded.updated_at
            """,
            (
                work_id,
                fp,
                labels["entry_kind"],
                metadata.get("title_cn") or item.series_title,
                root_title,
                str(labels["season_label"] or ""),
                str(labels["arc_label"] or ""),
                str(labels["part_label"] or ""),
                str(labels["special_label"] or ""),
                item.series_title,
                metadata.get("title_cn") or item.series_title,
                item.bangumi_id,
                item.mikan_bangumi_id,
                metadata.get("year") or item.year,
                int(labels["season_number"] or 1),
                metadata.get("poster_url") or "",
                metadata.get("summary") or "",
                ts,
                ts,
            ),
        )
        entry_id = conn.execute("SELECT id FROM entries WHERE fingerprint=?", (fp,)).fetchone()["id"]
        conn.execute(
            """
            INSERT INTO seasonal_entries
              (entry_id, source_type, source_ref, following, sync_enabled, archived, created_at, updated_at)
            VALUES (?, 'mikan_rss', ?, 1, 1, 0, ?, ?)
            ON CONFLICT(entry_id) DO UPDATE SET
              source_ref=excluded.source_ref,
              following=1,
              archived=0,
              updated_at=excluded.updated_at
            """,
            (entry_id, item.guid, ts, ts),
        )
        if item.episode_number:
            conn.execute(
                """
                INSERT INTO episodes
                  (series_id, entry_id, episode_number, title, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(series_id, episode_number) DO UPDATE SET updated_at=excluded.updated_at
                """,
                (series_id, entry_id, item.episode_number, f"第{item.episode_number:02d}话", ts, ts),
            )
        conn.execute(
            """
            INSERT INTO releases
              (series_id, entry_id, episode_number, guid, title, subtitle_group, resolution, language,
               torrent_url, magnet, published_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(guid) DO UPDATE SET
              series_id=excluded.series_id,
              entry_id=excluded.entry_id,
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
                entry_id,
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
            "UPDATE download_tasks SET entry_id=? WHERE release_id=?",
            (entry_id, release_id),
        )
        conn.execute(
            "UPDATE cloud_assets SET series_id=? WHERE release_id=?",
            (series_id, release_id),
        )
        conn.execute(
            "UPDATE cloud_assets SET entry_id=? WHERE release_id=?",
            (entry_id, release_id),
        )
        conn.execute(
            "UPDATE local_assets SET series_id=? WHERE release_id=?",
            (series_id, release_id),
        )
        conn.execute(
            "UPDATE local_assets SET entry_id=? WHERE release_id=?",
            (entry_id, release_id),
        )
        conn.execute(
            "UPDATE sync_tasks SET series_id=? WHERE release_id=?",
            (series_id, release_id),
        )
        conn.execute(
            "UPDATE sync_tasks SET entry_id=? WHERE release_id=?",
            (entry_id, release_id),
        )
        conn.execute(
            "UPDATE cloud_submissions SET entry_id=? WHERE release_id=?",
            (entry_id, release_id),
        )
    return series_id, entry_id, release_id


def upsert_rss_candidate(item: ParsedRelease, reason: str = "") -> int:
    with connect() as conn:
        ts = now()
        status = "pending_metadata" if item.bangumi_id else "pending"
        reason = reason or ("等待元数据刷新" if item.bangumi_id else "RSS 未提供 Bangumi ID")
        conn.execute(
            """
            INSERT INTO rss_candidates
              (guid, title, series_title, episode_number, subtitle_group, resolution,
               language, bangumi_id, mikan_bangumi_id, torrent_url, magnet, page_url, published_at, status,
               reason, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(guid) DO UPDATE SET
              title=excluded.title,
              series_title=excluded.series_title,
              episode_number=excluded.episode_number,
              subtitle_group=excluded.subtitle_group,
              resolution=excluded.resolution,
              language=excluded.language,
              bangumi_id=excluded.bangumi_id,
              mikan_bangumi_id=CASE WHEN excluded.mikan_bangumi_id!='' THEN excluded.mikan_bangumi_id ELSE rss_candidates.mikan_bangumi_id END,
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
                item.mikan_bangumi_id,
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
        if not item.bangumi_id or not item.mikan_bangumi_id:
            conn.execute(
                """
                INSERT INTO mikan_match_tasks
                  (candidate_id, status, mikan_url, mikan_bangumi_id, created_at, updated_at)
                VALUES (?, 'pending', ?, ?, ?, ?)
                ON CONFLICT(candidate_id) DO UPDATE SET
                  status=CASE
                    WHEN mikan_match_tasks.mikan_bangumi_id='' OR mikan_match_tasks.bangumi_id='' THEN 'pending'
                    WHEN mikan_match_tasks.status='completed' THEN 'completed'
                    ELSE 'pending'
                  END,
                  mikan_url=excluded.mikan_url,
                  mikan_bangumi_id=CASE WHEN excluded.mikan_bangumi_id!='' THEN excluded.mikan_bangumi_id ELSE mikan_match_tasks.mikan_bangumi_id END,
                  last_error='',
                  updated_at=excluded.updated_at
                """,
                (candidate_id, item.page_url, item.mikan_bangumi_id, ts, ts),
            )
            request_queue_trigger("mikan_match")
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
    request_queue_trigger("metadata")


def enqueue_missing_mikan_match_tasks(ts: str) -> int:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT rc.id, rc.page_url, rc.mikan_bangumi_id
            FROM rss_candidates rc
            LEFT JOIN mikan_match_tasks mt ON mt.candidate_id=rc.id
            WHERE rc.page_url != ''
              AND (
                    rc.bangumi_id = ''
                 OR rc.mikan_bangumi_id = ''
                 OR mt.id IS NULL
                 OR mt.mikan_bangumi_id = ''
                 OR mt.bangumi_id = ''
                 OR mt.status IN ('failed', 'pending')
              )
            ORDER BY rc.id ASC
            """
        ).fetchall()
        for row in rows:
            conn.execute(
                """
                INSERT INTO mikan_match_tasks
                  (candidate_id, status, mikan_url, mikan_bangumi_id, created_at, updated_at)
                VALUES (?, 'pending', ?, ?, ?, ?)
                ON CONFLICT(candidate_id) DO UPDATE SET
                  status='pending',
                  mikan_url=excluded.mikan_url,
                  retry_after='',
                  last_error='',
                  updated_at=excluded.updated_at
                """,
                (row["id"], row["page_url"], row["mikan_bangumi_id"], ts, ts),
            )
    if rows:
        request_queue_trigger("mikan_match")
    return len(rows)


def reclaim_mikan_match_tasks(ts: str) -> int:
    with connect() as conn:
        cursor = conn.execute(
            """
            UPDATE mikan_match_tasks
            SET status='pending',
                retry_after='',
                last_error=CASE
                  WHEN last_error='' THEN '手动扫描前回收运行中的 Mikan 匹配任务'
                  ELSE last_error
                END,
                updated_at=?
            WHERE status='running'
            """,
            (ts,),
        )
    return cursor.rowcount


def repair_series_mikan_ids(ts: str) -> int:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT s.id, rc.mikan_bangumi_id
            FROM series s
            JOIN rss_candidates rc ON rc.bangumi_id=s.bangumi_id
            WHERE s.bangumi_id != ''
              AND s.mikan_bangumi_id = ''
              AND rc.mikan_bangumi_id != ''
            GROUP BY s.id
            ORDER BY rc.id DESC
            """
        ).fetchall()
        for row in rows:
            conn.execute(
                "UPDATE series SET mikan_bangumi_id=?, updated_at=? WHERE id=?",
                (row["mikan_bangumi_id"], ts, row["id"]),
            )
    return len(rows)


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
        mikan_bangumi_id=candidate["mikan_bangumi_id"],
        published_at=candidate["published_at"],
    )


def resolved_backfill_mode(entry: dict, settings: dict[str, str]) -> str:
    value = (entry["backfill_mode"] or "inherit").strip() or "inherit"
    if value == "inherit":
        value = (settings.get("default_backfill") or "none").strip() or "none"
    return value


def resolve_entry_series_id(conn, entry_id: int) -> int:
    row = conn.execute(
        "SELECT series_id FROM releases WHERE entry_id=? ORDER BY id ASC LIMIT 1",
        (entry_id,),
    ).fetchone()
    return int(row["series_id"] or 0) if row else 0


def enqueue_selection_task(conn, series_id: int, entry_id: int, ts: str, reason: str = "") -> None:
    resolved_series_id = int(series_id or 0) or resolve_entry_series_id(conn, entry_id)
    conn.execute(
        """
        INSERT INTO selection_tasks
          (series_id, entry_id, status, reason, created_at, updated_at)
        VALUES (?, ?, 'pending', ?, ?, ?)
        ON CONFLICT(entry_id) DO UPDATE SET
          series_id=excluded.series_id,
          status='pending',
          reason=excluded.reason,
          retry_after='',
          last_error='',
          updated_at=excluded.updated_at
        """,
        (resolved_series_id, entry_id, reason[:500], ts, ts),
    )
    request_queue_trigger("selection")


def enqueue_backfill_task(conn, series_id: int, entry_id: int, settings: dict[str, str], ts: str) -> None:
    entry = conn.execute("SELECT * FROM entries WHERE id=?", (entry_id,)).fetchone()
    if not entry:
        return
    backfill_mode = resolved_backfill_mode(entry, settings)
    if backfill_mode == "none":
        return
    resolved_series_id = int(series_id or 0) or resolve_entry_series_id(conn, entry_id)
    conn.execute(
        """
        INSERT INTO backfill_tasks
          (series_id, entry_id, status, backfill_mode, created_at, updated_at)
        VALUES (?, ?, 'pending', ?, ?, ?)
        ON CONFLICT(entry_id) DO UPDATE SET
          series_id=excluded.series_id,
          status='pending',
          backfill_mode=excluded.backfill_mode,
          retry_after='',
          last_error='',
          updated_at=excluded.updated_at
        """,
        (resolved_series_id, entry_id, backfill_mode, ts, ts),
    )
    request_queue_trigger("backfill")


async def process_mikan_match_tasks(settings: dict[str, str], limit: int = 20) -> tuple[int, int]:
    async with mikan_match_lock:
        return await _process_mikan_match_tasks(settings, limit)


async def _process_mikan_match_tasks(settings: dict[str, str], limit: int = 20) -> tuple[int, int]:
    with connect() as conn:
        conn.execute(
            """
            UPDATE mikan_match_tasks
            SET status='pending', last_error='上次匹配中断，已自动放回待处理', updated_at=?
            WHERE status='running' AND updated_at < ?
            """,
            (now(), stale_running_cutoff()),
        )
        rows = conn.execute(
            """
            SELECT mt.*, rc.title, rc.page_url
            FROM mikan_match_tasks mt
            JOIN rss_candidates rc ON rc.id=mt.candidate_id
            WHERE mt.status IN ('pending', 'failed')
              AND (mt.retry_after='' OR mt.retry_after <= ?)
            ORDER BY mt.id ASC
            LIMIT ?
            """,
            (now(), limit),
        ).fetchall()
    log("info", f"Mikan 匹配队列: 本轮可执行 {len(rows)} 个")
    async def handle(row) -> tuple[int, int]:
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
                    """
                    UPDATE mikan_match_tasks
                    SET status='pending', retry_after=?, last_error=?, updated_at=?
                    WHERE id=?
                    """,
                    (task_retry_after(settings, int(row["attempts"] or 0) + 1), error, now(), row["id"]),
                )
                conn.execute(
                    "UPDATE rss_candidates SET status='failed', reason=?, updated_at=? WHERE id=?",
                    (error, now(), row["candidate_id"]),
                )
            log("warn", f"Mikan 匹配失败: {row['title']} - {error}")
            return 0, 1
        with connect() as conn:
            ts = now()
            conn.execute(
                """
                UPDATE mikan_match_tasks
                SET status='completed', bangumi_id=?, mikan_bangumi_id=?,
                    retry_after='', last_error='', updated_at=?
                WHERE id=?
                """,
                (bangumi_id, mikan_id, ts, row["id"]),
            )
            conn.execute(
                """
                UPDATE rss_candidates
                SET status='pending_metadata', bangumi_id=?, mikan_bangumi_id=?, reason='等待元数据刷新', updated_at=?
                WHERE id=?
                """,
                (bangumi_id, mikan_id, ts, row["candidate_id"]),
            )
            if mikan_id:
                conn.execute(
                    """
                    UPDATE series
                    SET mikan_bangumi_id=?, updated_at=?
                    WHERE bangumi_id=?
                      AND mikan_bangumi_id=''
                    """,
                    (mikan_id, ts, bangumi_id),
                )
            enqueue_metadata_task(conn, row["candidate_id"], bangumi_id, ts)
        return 1, 0

    results = await gather_limited([handle(row) for row in rows], limit=4)
    completed = sum(item[0] for item in results)
    failed = sum(item[1] for item in results)
    return completed, failed


async def process_metadata_tasks(settings: dict[str, str], limit: int = 20) -> tuple[int, int]:
    async with metadata_tasks_lock:
        return await _process_metadata_tasks(settings, limit)


async def _process_metadata_tasks(settings: dict[str, str], limit: int = 20) -> tuple[int, int]:
    with connect() as conn:
        conn.execute(
            """
            UPDATE metadata_tasks
            SET status='pending', last_error='上次元数据处理中断，已自动放回待处理', updated_at=?
            WHERE status='running' AND updated_at < ?
            """,
            (now(), stale_running_cutoff()),
        )
        rows = conn.execute(
            """
            SELECT mt.*, rc.*
            FROM metadata_tasks mt
            JOIN rss_candidates rc ON rc.id=mt.candidate_id
            WHERE mt.status IN ('pending', 'failed')
              AND (mt.retry_after='' OR mt.retry_after <= ?)
            ORDER BY mt.id ASC
            LIMIT ?
            """,
            (now(), limit),
        ).fetchall()
    log("info", f"元数据队列: 本轮可执行 {len(rows)} 个")
    async def handle(row) -> tuple[int, int]:
        with connect() as conn:
            conn.execute(
                "UPDATE metadata_tasks SET status='running', attempts=attempts+1, updated_at=? WHERE id=?",
                (now(), row["id"]),
            )
        if not row["bangumi_id"]:
            error = "缺少 Bangumi ID"
            with connect() as conn:
                conn.execute(
                    """
                    UPDATE metadata_tasks
                    SET status='pending', retry_after=?, last_error=?, updated_at=?
                    WHERE id=?
                    """,
                    (task_retry_after(settings, int(row["attempts"] or 0) + 1), error, now(), row["id"]),
                )
                conn.execute(
                    "UPDATE rss_candidates SET status='failed', reason=?, updated_at=? WHERE id=?",
                    (error, now(), row["candidate_id"]),
                )
            return 0, 1
        try:
            metadata = await fetch_bangumi_metadata(row["bangumi_id"], settings.get("rss_proxy", ""))
            release = candidate_to_parsed_release(row)
            series_id, entry_id, release_id = upsert_release(release, metadata)
        except Exception as exc:
            error = str(exc)[:2000]
            with connect() as conn:
                conn.execute(
                    """
                    UPDATE metadata_tasks
                    SET status='pending', retry_after=?, last_error=?, updated_at=?
                    WHERE id=?
                    """,
                    (task_retry_after(settings, int(row["attempts"] or 0) + 1), error, now(), row["id"]),
                )
                conn.execute(
                    "UPDATE rss_candidates SET status='failed', reason=?, updated_at=? WHERE id=?",
                    (error, now(), row["candidate_id"]),
                )
            log("error", f"元数据刷新失败: {row['title']} - {error}")
            return 0, 1
        with connect() as conn:
            ts = now()
            conn.execute(
                "UPDATE metadata_tasks SET status='completed', retry_after='', last_error='', updated_at=? WHERE id=?",
                (ts, row["id"]),
            )
            conn.execute(
                "UPDATE rss_candidates SET status='completed', reason='', updated_at=? WHERE id=?",
                (ts, row["candidate_id"]),
            )
            enqueue_selection_task(conn, series_id, entry_id, ts, "元数据完成，等待自动选集")
            enqueue_backfill_task(conn, series_id, entry_id, settings, ts)
        return 1, 0

    results = await gather_limited([handle(row) for row in rows], limit=4)
    completed = sum(item[0] for item in results)
    failed = sum(item[1] for item in results)
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


def auto_download_enabled(entry, settings: dict[str, str]) -> bool:
    value = entry["auto_download"]
    return value == "on" or (value == "inherit" and bool_setting(settings.get("auto_download_unique", "true")))


def resolve_entry_choice(entry_id: int, settings: dict[str, str]) -> tuple[list[int], dict[str, str]]:
    with connect() as conn:
        entry = conn.execute("SELECT * FROM entries WHERE id=?", (entry_id,)).fetchone()
        rows = conn.execute(
            """
            SELECT id, episode_number, subtitle_group, resolution, language
            FROM releases
            WHERE entry_id=?
            ORDER BY episode_number ASC, id DESC
            """,
            (entry_id,),
        ).fetchall()

    info = {
        "enabled": "true" if entry and auto_download_enabled(entry, settings) else "false",
        "selected_group": "",
        "selected_resolution": "",
        "selected_language": "",
        "reason": "",
    }
    if not entry:
        info["reason"] = "条目不存在"
        return [], info
    if not entry["bangumi_id"]:
        info["reason"] = "缺少 Bangumi ID，不能进入自动入库"
        return [], info
    if not rows:
        info["reason"] = "没有可下载发布"
        return [], info
    if not auto_download_enabled(entry, settings):
        info["reason"] = "自动下载已关闭"
        return [], info

    candidates = list(rows)
    selected_group = entry["selected_group"] or ""
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

    selected_resolution = entry["selected_resolution"] or ""
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


def mark_selected_releases(entry_id: int, release_ids: list[int]) -> None:
    with connect() as conn:
        conn.execute("UPDATE releases SET selected=0 WHERE entry_id=?", (entry_id,))
    if not release_ids:
        return
    with connect() as conn:
        placeholders = ",".join("?" for _ in release_ids)
        conn.execute(f"UPDATE releases SET selected=1 WHERE id IN ({placeholders})", release_ids)


async def process_selection_tasks(settings: dict[str, str], limit: int = 20) -> tuple[int, int]:
    async with selection_tasks_lock:
        return await _process_selection_tasks(settings, limit)


async def _process_selection_tasks(settings: dict[str, str], limit: int = 20) -> tuple[int, int]:
    with connect() as conn:
        conn.execute(
            """
            UPDATE selection_tasks
            SET status='pending', last_error='上次自动选集处理中断，已自动放回待处理', updated_at=?
            WHERE status='running' AND updated_at < ?
            """,
            (now(), stale_running_cutoff()),
        )
        rows = conn.execute(
            """
            SELECT st.*, e.display_title AS title_cn, e.selected_group, e.selected_resolution
            FROM selection_tasks st
            JOIN entries e ON e.id=st.entry_id
            WHERE st.status IN ('pending', 'failed')
              AND (st.retry_after='' OR st.retry_after <= ?)
              AND COALESCE(e.hidden, 0)=0
              AND e.bangumi_id != ''
            ORDER BY st.id ASC
            LIMIT ?
            """,
            (now(), limit),
        ).fetchall()

    completed = 0
    failed = 0
    for row in rows:
        with connect() as conn:
            conn.execute(
                "UPDATE selection_tasks SET status='running', attempts=attempts+1, updated_at=? WHERE id=?",
                (now(), row["id"]),
            )
        try:
            ids, choice = resolve_entry_choice(int(row["entry_id"]), settings)
            mark_selected_releases(int(row["entry_id"]), ids)
            with connect() as conn:
                ts = now()
                if choice["reason"]:
                    conn.execute(
                        """
                        UPDATE selection_tasks
                        SET status='failed', reason=?, retry_after='', last_error='', updated_at=?
                        WHERE id=?
                        """,
                        (choice["reason"][:500], ts, row["id"]),
                    )
                    log("warn", f"自动选集等待人工处理: {row['title_cn']} - {choice['reason']}")
                    failed += 1
                    continue
                conn.execute(
                    """
                    UPDATE selection_tasks
                    SET status='completed', reason='', retry_after='', last_error='', updated_at=?
                    WHERE id=?
                    """,
                    (ts, row["id"]),
                )
            for selected_release_id in ids:
                queue_release(selected_release_id, settings)
            completed += 1
        except Exception as exc:
            error = str(exc)[:2000]
            with connect() as conn:
                conn.execute(
                    """
                    UPDATE selection_tasks
                    SET status='pending', retry_after=?, last_error=?, updated_at=?
                    WHERE id=?
                    """,
                    (task_retry_after(settings, int(row["attempts"] or 0) + 1), error, now(), row["id"]),
                )
            log("error", f"自动选集失败: {row['title_cn']} - {error}")
            failed += 1
    return completed, failed


async def process_backfill_tasks(settings: dict[str, str], limit: int = 8) -> tuple[int, int]:
    async with backfill_tasks_lock:
        return await _process_backfill_tasks(settings, limit)


async def _process_backfill_tasks(settings: dict[str, str], limit: int = 8) -> tuple[int, int]:
    with connect() as conn:
        conn.execute(
            """
            UPDATE backfill_tasks
            SET status='pending', last_error='上次补全处理中断，已自动放回待处理', updated_at=?
            WHERE status='running' AND updated_at < ?
            """,
            (now(), stale_running_cutoff()),
        )
        rows = conn.execute(
            """
            SELECT bt.*, e.display_title AS title_cn, e.mikan_bangumi_id, e.bangumi_id
            FROM backfill_tasks bt
            JOIN entries e ON e.id=bt.entry_id
            WHERE bt.status IN ('pending', 'failed')
              AND (bt.retry_after='' OR bt.retry_after <= ?)
              AND COALESCE(e.hidden, 0)=0
              AND e.bangumi_id != ''
            ORDER BY bt.id ASC
            LIMIT ?
            """,
            (now(), limit),
        ).fetchall()

    completed = 0
    failed = 0
    for row in rows:
        with connect() as conn:
            conn.execute(
                "UPDATE backfill_tasks SET status='running', attempts=attempts+1, updated_at=? WHERE id=?",
                (now(), row["id"]),
            )
        try:
            mikan_bangumi_id = str(row["mikan_bangumi_id"] or "")
            if not mikan_bangumi_id:
                mikan_bangumi_id = await resolve_entry_mikan_bangumi_id(
                    settings,
                    int(row["entry_id"]),
                    str(row["bangumi_id"] or ""),
                )
                if mikan_bangumi_id:
                    log("info", f"整季补全前已补回 Mikan Bangumi ID: {row['title_cn']} -> {mikan_bangumi_id}")
            if not mikan_bangumi_id:
                raise RuntimeError("缺少 Mikan Bangumi ID，暂不能补全整季")
            releases = await fetch_mikan_page_releases(settings, mikan_bangumi_id)
            if not releases:
                raise RuntimeError("Mikan 番组页未解析到可补全条目")
            with connect() as conn:
                existing = {
                    int(item["episode_number"])
                    for item in conn.execute(
                        "SELECT episode_number FROM releases WHERE entry_id=?",
                        (row["entry_id"],),
                    ).fetchall()
                }
                written = 0
                ts = now()
                for release in releases:
                    if release.episode_number in existing:
                        continue
                    release.bangumi_id = str(
                        conn.execute("SELECT bangumi_id FROM entries WHERE id=?", (row["entry_id"],)).fetchone()["bangumi_id"]
                    )
                    candidate_id = upsert_rss_candidate(release, "整季补全写入候选，等待后续识别/元数据处理")
                    if candidate_id:
                        written += 1
                if written:
                    request_queue_trigger("mikan_match")
            with connect() as conn:
                conn.execute(
                    "UPDATE backfill_tasks SET status='completed', retry_after='', last_error='', updated_at=? WHERE id=?",
                    (now(), row["id"]),
                )
            log("info", f"整季补全完成: {row['title_cn']} - 新增候选 {written} 条")
            completed += 1
        except Exception as exc:
            error = str(exc)[:2000]
            with connect() as conn:
                conn.execute(
                    """
                    UPDATE backfill_tasks
                    SET status='failed', retry_after=?, last_error=?, updated_at=?
                    WHERE id=?
                    """,
                    (task_retry_after(settings, int(row["attempts"] or 0) + 1), error, now(), row["id"]),
                )
            log("warn", f"整季补全暂未完成: {row['title_cn']} - {error}")
            failed += 1
    return completed, failed


def queue_release(release_id: int, settings: dict[str, str]) -> None:
    ts = now()
    with connect() as conn:
        release = conn.execute("SELECT * FROM releases WHERE id=?", (release_id,)).fetchone()
        if not release:
            return
        entry = conn.execute("SELECT * FROM entries WHERE id=?", (release["entry_id"],)).fetchone()
        if not entry:
            return
        if not entry["bangumi_id"]:
            log("warn", f"云盘入库跳过: {entry['display_title']} - 缺少 Bangumi ID")
            return
        enqueue_cloud_presence_task(conn, int(release_id), 0, int(release["entry_id"]), int(release["episode_number"]), ts)


def enqueue_cloud_presence_task(conn, release_id: int, series_id: int, entry_id: int, episode_number: int, ts: str) -> None:
    resolved_series_id = int(series_id or 0) or resolve_entry_series_id(conn, entry_id)
    conn.execute(
        """
        INSERT INTO cloud_presence_tasks
          (release_id, series_id, entry_id, episode_number, status, retry_after, last_error, created_at, updated_at)
        VALUES (?, ?, ?, ?, 'pending', '', '', ?, ?)
        ON CONFLICT(release_id) DO UPDATE SET
          series_id=excluded.series_id,
          entry_id=excluded.entry_id,
          episode_number=excluded.episode_number,
          status='pending',
          cloud_asset_id=0,
          retry_after='',
          last_error='',
          updated_at=excluded.updated_at
        """,
        (release_id, resolved_series_id, entry_id, episode_number, ts, ts),
    )
    request_queue_trigger("cloud_presence")


def enqueue_download_enqueue_task(conn, release_id: int, series_id: int, entry_id: int, episode_number: int, ts: str) -> None:
    resolved_series_id = int(series_id or 0) or resolve_entry_series_id(conn, entry_id)
    conn.execute(
        """
        INSERT INTO download_enqueue_tasks
          (release_id, series_id, entry_id, episode_number, status, retry_after, last_error, created_at, updated_at)
        VALUES (?, ?, ?, ?, 'pending', '', '', ?, ?)
        ON CONFLICT(release_id) DO UPDATE SET
          series_id=excluded.series_id,
          entry_id=excluded.entry_id,
          episode_number=excluded.episode_number,
          status='pending',
          retry_after='',
          last_error='',
          updated_at=excluded.updated_at
        """,
        (release_id, resolved_series_id, entry_id, episode_number, ts, ts),
    )
    request_queue_trigger("download_enqueue")


def sync_cloud_submission(
    conn,
    *,
    series_id: int,
    entry_id: int,
    episode_number: int,
    release_id: int,
    download_task_id: int,
    status: str,
    target_dir: str = "",
    normalized_name: str = "",
    submission_id: str = "",
    provider_file_id: str = "",
    retry_after: str = "",
    last_error: str = "",
) -> None:
    ts = now()
    resolved_series_id = int(series_id or 0) or resolve_entry_series_id(conn, entry_id)
    conn.execute(
        """
        INSERT INTO cloud_submissions
          (series_id, entry_id, episode_number, release_id, provider, download_task_id, status, attempts,
           submission_id, provider_file_id, target_dir, normalized_name, retry_after, last_error,
           created_at, updated_at, last_seen_at)
        VALUES (?, ?, ?, ?, 'pikpak', ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(entry_id, episode_number, provider) DO UPDATE SET
          series_id=excluded.series_id,
          release_id=excluded.release_id,
          download_task_id=excluded.download_task_id,
          status=excluded.status,
          submission_id=CASE WHEN excluded.submission_id!='' THEN excluded.submission_id ELSE cloud_submissions.submission_id END,
          provider_file_id=CASE WHEN excluded.provider_file_id!='' THEN excluded.provider_file_id ELSE cloud_submissions.provider_file_id END,
          target_dir=CASE WHEN excluded.target_dir!='' THEN excluded.target_dir ELSE cloud_submissions.target_dir END,
          normalized_name=CASE WHEN excluded.normalized_name!='' THEN excluded.normalized_name ELSE cloud_submissions.normalized_name END,
          retry_after=excluded.retry_after,
          last_error=excluded.last_error,
          updated_at=excluded.updated_at,
          last_seen_at=excluded.last_seen_at
        """,
        (
            resolved_series_id,
            entry_id,
            episode_number,
            release_id,
            download_task_id,
            status,
            submission_id,
            provider_file_id,
            target_dir,
            normalized_name,
            retry_after,
            last_error[:2000],
            ts,
            ts,
            ts,
        ),
    )


def ensure_download_task_for_release(conn, release_id: int, settings: dict[str, str]) -> int | None:
    release = conn.execute("SELECT * FROM releases WHERE id=?", (release_id,)).fetchone()
    if not release:
        return None
    entry = conn.execute("SELECT * FROM entries WHERE id=?", (release["entry_id"],)).fetchone()
    if not entry:
        return None
    resolved_series_id = resolve_entry_series_id(conn, int(release["entry_id"]))
    entry_dict = dict(entry)
    target = target_dir(entry_dict, settings)
    name = render_episode_name(entry_dict, release["episode_number"], "", settings)
    ts = now()
    conn.execute(
        """
        INSERT INTO download_tasks
          (release_id, series_id, entry_id, status, target_dir, normalized_name, retry_after, created_at, updated_at)
        VALUES (?, ?, ?, 'pending', ?, ?, '', ?, ?)
        ON CONFLICT(release_id) DO UPDATE SET
          status=CASE
            WHEN download_tasks.status IN ('completed','submitted','running') THEN download_tasks.status
            ELSE 'pending'
          END,
          entry_id=excluded.entry_id,
          target_dir=excluded.target_dir,
          normalized_name=excluded.normalized_name,
          updated_at=excluded.updated_at
        """,
        (release_id, resolved_series_id, release["entry_id"], target, name, ts, ts),
    )
    task = conn.execute("SELECT * FROM download_tasks WHERE release_id=?", (release_id,)).fetchone()
    if not task:
        return None
    conn.execute(
        """
        UPDATE download_tasks
        SET status='superseded', retry_after='', last_error='已被新的自动选择替代', updated_at=?
        WHERE id IN (
          SELECT dt.id
          FROM download_tasks dt
          JOIN releases r ON r.id=dt.release_id
          WHERE dt.id != ?
            AND dt.entry_id=?
            AND r.episode_number=?
            AND dt.status IN ('pending','running','submitted','failed')
        )
        """,
        (ts, task["id"], release["entry_id"], release["episode_number"]),
    )
    conn.execute(
        """
        UPDATE cloud_submissions
        SET status='superseded', retry_after='', last_error='已被新的自动选择替代', updated_at=?, last_seen_at=?
        WHERE download_task_id IN (
          SELECT dt.id
          FROM download_tasks dt
          JOIN releases r ON r.id=dt.release_id
          WHERE dt.id != ?
            AND dt.entry_id=?
            AND r.episode_number=?
        )
          AND provider='pikpak'
          AND status IN ('pending','running','submitted','failed')
        """,
        (ts, ts, task["id"], release["entry_id"], release["episode_number"]),
    )
    conn.execute(
        """
        INSERT INTO cloud_submissions
          (series_id, entry_id, episode_number, release_id, provider, download_task_id, status,
           target_dir, normalized_name, created_at, updated_at, last_seen_at)
        VALUES (?, ?, ?, ?, 'pikpak', ?, 'pending', ?, ?, ?, ?, ?)
        ON CONFLICT(entry_id, episode_number, provider) DO UPDATE SET
          series_id=excluded.series_id,
          release_id=excluded.release_id,
          download_task_id=excluded.download_task_id,
          status=CASE
            WHEN cloud_submissions.status='completed' THEN cloud_submissions.status
            ELSE 'pending'
          END,
          target_dir=excluded.target_dir,
          normalized_name=excluded.normalized_name,
          retry_after='',
          last_error='',
          updated_at=excluded.updated_at,
          last_seen_at=excluded.last_seen_at
        """,
        (
            resolved_series_id,
            release["entry_id"],
            release["episode_number"],
            release_id,
            task["id"],
            target,
            name,
            ts,
            ts,
            ts,
        ),
    )
    request_queue_trigger("download")
    return int(task["id"])


def enqueue_cloud_poll_task(conn, download_task_id: int, ts: str) -> None:
    conn.execute(
        """
        INSERT INTO cloud_poll_tasks
          (download_task_id, status, retry_after, last_error, created_at, updated_at)
        VALUES (?, 'pending', '', '', ?, ?)
        ON CONFLICT(download_task_id) DO UPDATE SET
          status=CASE WHEN cloud_poll_tasks.status='completed' THEN cloud_poll_tasks.status ELSE 'pending' END,
          retry_after='',
          last_error='',
          updated_at=excluded.updated_at
        """,
        (download_task_id, ts, ts),
    )
    request_queue_trigger("cloud_poll")


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


def retry_after_time(settings: dict[str, str], default_minutes: int = 60) -> str:
    minutes = max(1, int(settings.get("pikpak_rate_limit_cooldown_minutes") or default_minutes))
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()


def task_retry_after(settings: dict[str, str], attempts: int) -> str:
    minutes = min(180, max(5, 5 * max(1, attempts)))
    return retry_after_time(settings, minutes)


def stale_running_cutoff(minutes: int = 10) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()


async def process_cloud_presence_tasks(settings: dict[str, str], limit: int = 20) -> tuple[int, int]:
    async with cloud_presence_tasks_lock:
        return await _process_cloud_presence_tasks(settings, limit)


async def _process_cloud_presence_tasks(settings: dict[str, str], limit: int = 20) -> tuple[int, int]:
    with connect() as conn:
        conn.execute(
            """
            UPDATE cloud_presence_tasks
            SET status='pending', last_error='上次云盘存在性检查中断，已自动放回待处理', updated_at=?
            WHERE status='running' AND updated_at < ?
            """,
            (now(), stale_running_cutoff()),
        )
        rows = conn.execute(
            """
            SELECT cpt.*, r.title
            FROM cloud_presence_tasks cpt
            JOIN releases r ON r.id=cpt.release_id
            WHERE cpt.status IN ('pending', 'failed')
              AND (cpt.retry_after='' OR cpt.retry_after <= ?)
            ORDER BY cpt.id ASC
            LIMIT ?
            """,
            (now(), limit),
        ).fetchall()

    completed = 0
    failed = 0
    for task in rows:
        with connect() as conn:
            conn.execute(
                "UPDATE cloud_presence_tasks SET status='running', attempts=attempts+1, updated_at=? WHERE id=?",
                (now(), task["id"]),
            )
        try:
            with connect() as conn:
                existing_cloud = conn.execute(
                    """
                    SELECT id
                    FROM cloud_assets
                    WHERE release_id=? OR (entry_id=? AND episode_number=?)
                    LIMIT 1
                    """,
                    (task["release_id"], task["entry_id"], task["episode_number"]),
                ).fetchone()
                ts = now()
                if existing_cloud:
                    conn.execute(
                        """
                        UPDATE cloud_presence_tasks
                        SET status='completed', cloud_asset_id=?, retry_after='', last_error='云盘资源已存在，跳过提交', updated_at=?
                        WHERE id=?
                        """,
                        (int(existing_cloud["id"]), ts, task["id"]),
                    )
                else:
                    enqueue_download_enqueue_task(conn, int(task["release_id"]), 0, int(task["entry_id"]), int(task["episode_number"]), ts)
                    conn.execute(
                        """
                        UPDATE cloud_presence_tasks
                        SET status='completed', cloud_asset_id=0, retry_after='', last_error='', updated_at=?
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
                    UPDATE cloud_presence_tasks
                    SET status='failed', retry_after=?, last_error=?, updated_at=?
                    WHERE id=?
                    """,
                    (task_retry_after(settings, int(task["attempts"] or 0) + 1), str(exc)[:2000], now(), task["id"]),
                )
            log("error", f"云盘存在性检查失败: {task['title']} - {exc}")
    return completed, failed


async def process_download_enqueue_tasks(settings: dict[str, str], limit: int = 20) -> tuple[int, int]:
    async with download_enqueue_tasks_lock:
        return await _process_download_enqueue_tasks(settings, limit)


async def _process_download_enqueue_tasks(settings: dict[str, str], limit: int = 20) -> tuple[int, int]:
    with connect() as conn:
        conn.execute(
            """
            UPDATE download_enqueue_tasks
            SET status='pending', last_error='上次下载准备中断，已自动放回待处理', updated_at=?
            WHERE status='running' AND updated_at < ?
            """,
            (now(), stale_running_cutoff()),
        )
        rows = conn.execute(
            """
            SELECT det.*, r.title
            FROM download_enqueue_tasks det
            JOIN releases r ON r.id=det.release_id
            WHERE det.status IN ('pending', 'failed')
              AND (det.retry_after='' OR det.retry_after <= ?)
            ORDER BY det.id ASC
            LIMIT ?
            """,
            (now(), limit),
        ).fetchall()

    completed = 0
    failed = 0
    for task in rows:
        with connect() as conn:
            conn.execute(
                "UPDATE download_enqueue_tasks SET status='running', attempts=attempts+1, updated_at=? WHERE id=?",
                (now(), task["id"]),
            )
        try:
            with connect() as conn:
                existing_submission = conn.execute(
                    """
                    SELECT id, status
                    FROM cloud_submissions
                    WHERE entry_id=? AND episode_number=? AND provider='pikpak'
                    LIMIT 1
                    """,
                    (task["entry_id"], task["episode_number"]),
                ).fetchone()
                ts = now()
                if existing_submission and existing_submission["status"] in {"pending", "submitted", "running", "completed"}:
                    conn.execute(
                        """
                        UPDATE download_enqueue_tasks
                        SET status='completed', retry_after='', last_error='已存在云盘提交记录，跳过重复准备', updated_at=?
                        WHERE id=?
                        """,
                        (ts, task["id"]),
                    )
                else:
                    ensure_download_task_for_release(conn, int(task["release_id"]), settings)
                    conn.execute(
                        """
                        UPDATE download_enqueue_tasks
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
                    UPDATE download_enqueue_tasks
                    SET status='failed', retry_after=?, last_error=?, updated_at=?
                    WHERE id=?
                    """,
                    (task_retry_after(settings, int(task["attempts"] or 0) + 1), str(exc)[:2000], now(), task["id"]),
                )
            log("error", f"下载准备失败: {task['title']} - {exc}")
    return completed, failed


async def process_tasks(settings: dict[str, str], limit: int = 6, force: bool = False) -> None:
    async with download_tasks_lock:
        await _process_tasks(settings, limit, force)


async def _process_tasks(settings: dict[str, str], limit: int = 6, force: bool = False) -> None:
    with connect() as conn:
        conn.execute(
            """
            UPDATE download_tasks
            SET status='pending', last_error='上次提交中断，已自动放回待处理', updated_at=?
            WHERE status='running' AND updated_at < ?
            """,
            (now(), stale_running_cutoff()),
        )
        if force:
            conn.execute(
                """
                UPDATE download_tasks
                SET retry_after='', updated_at=?
                WHERE status='pending' AND retry_after != ''
                """,
                (now(),),
            )
        rows = conn.execute(
            """
            SELECT dt.*, r.magnet, r.torrent_url, r.title, r.episode_number
            FROM download_tasks dt
            JOIN releases r ON r.id = dt.release_id
            JOIN entries e ON e.id = dt.entry_id
            JOIN cloud_submissions cs ON cs.download_task_id=dt.id
            WHERE dt.status IN ('pending', 'failed')
              AND e.bangumi_id != ''
              AND cs.provider='pikpak'
              AND cs.status IN ('pending', 'running', 'failed')
              AND (dt.retry_after='' OR dt.retry_after <= ?)
            ORDER BY dt.id ASC
            LIMIT ?
            """,
            (now(), limit),
        ).fetchall()

    submitted = 0
    max_submit = limit if rclone_service.enabled(settings) else 1
    for task in rows:
        if submitted >= max_submit:
            break
        source = task["magnet"] or task["torrent_url"]
        if not source:
            with connect() as conn:
                conn.execute(
                    """
                    UPDATE download_tasks
                    SET status='pending', retry_after=?, last_error=?, updated_at=?
                    WHERE id=?
                    """,
                    (task_retry_after(settings, int(task["attempts"] or 0) + 1), "发布缺少 magnet/torrent 链接，等待后自动重试", now(), task["id"]),
                )
                sync_cloud_submission(
                    conn,
                    series_id=0,
                    entry_id=int(task["entry_id"]),
                    episode_number=int(task["episode_number"]),
                    release_id=int(task["release_id"]),
                    download_task_id=int(task["id"]),
                    status="pending",
                    target_dir=str(task["target_dir"] or ""),
                    normalized_name=str(task["normalized_name"] or ""),
                    retry_after=task_retry_after(settings, int(task["attempts"] or 0) + 1),
                    last_error="发布缺少 magnet/torrent 链接，等待后自动重试",
                )
            log("warn", f"下载任务跳过: {task['title']} - 发布缺少 magnet/torrent 链接")
            continue
        with connect() as conn:
            conn.execute(
                """
                UPDATE download_tasks
                SET status='running',
                    attempts=CASE WHEN ?=1 THEN attempts ELSE attempts+1 END,
                    updated_at=?
                WHERE id=?
                """,
                (1 if force else 0, now(), task["id"]),
            )
            sync_cloud_submission(
                conn,
                series_id=0,
                entry_id=int(task["entry_id"]),
                episode_number=int(task["episode_number"]),
                release_id=int(task["release_id"]),
                download_task_id=int(task["id"]),
                status="running",
                target_dir=str(task["target_dir"] or ""),
                normalized_name=str(task["normalized_name"] or ""),
            )
        try:
            result = await submit_offline_download(settings, source, task["target_dir"], task["normalized_name"])
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
                    sync_cloud_submission(
                        conn,
                        series_id=0,
                        entry_id=int(task["entry_id"]),
                        episode_number=int(task["episode_number"]),
                        release_id=int(task["release_id"]),
                        download_task_id=int(task["id"]),
                        status="pending",
                        target_dir=str(task["target_dir"] or ""),
                        normalized_name=str(task["normalized_name"] or ""),
                        retry_after=retry_after,
                        last_error=f"PikPak 限流，等待后自动重试: {str(exc)[:1800]}",
                    )
                    conn.execute(
                        """
                        UPDATE download_tasks
                        SET retry_after=?, last_error=CASE
                              WHEN last_error='' THEN 'PikPak 当前限流，等待统一冷却后自动重试'
                              ELSE last_error
                            END,
                            updated_at=?
                        WHERE status='pending'
                          AND (retry_after='' OR retry_after <= ?)
                        """,
                        (retry_after, now(), now()),
                    )
                log("warn", f"PikPak 限流，已延后重试: {task['title']} - {exc}")
                break
            retry_after = task_retry_after(settings, int(task["attempts"] or 0) + 1)
            with connect() as conn:
                conn.execute(
                    """
                    UPDATE download_tasks
                    SET status='pending', retry_after=?, last_error=?, updated_at=?
                    WHERE id=?
                    """,
                    (retry_after, f"提交失败，等待后自动重试: {str(exc)[:1800]}", now(), task["id"]),
                )
                sync_cloud_submission(
                    conn,
                    series_id=0,
                    entry_id=int(task["entry_id"]),
                    episode_number=int(task["episode_number"]),
                    release_id=int(task["release_id"]),
                    download_task_id=int(task["id"]),
                    status="pending",
                    target_dir=str(task["target_dir"] or ""),
                    normalized_name=str(task["normalized_name"] or ""),
                    retry_after=retry_after,
                    last_error=f"提交失败，等待后自动重试: {str(exc)[:1800]}",
                )
            log("error", f"PikPak 提交失败: {task['title']} - {exc}")
            continue
        else:
            with connect() as conn:
                ts = now()
                status = "submitted"
                if file_id and not task_id and not rclone_service.enabled(settings):
                    status = "completed"
                conn.execute(
                    """
                    UPDATE download_tasks
                    SET status='submitted', pikpak_task_id=?, pikpak_file_id=?, retry_after='', last_error='', updated_at=?
                    WHERE id=?
                    """,
                    (task_id, file_id, ts, task["id"]),
                )
                sync_cloud_submission(
                    conn,
                    series_id=0,
                    entry_id=int(task["entry_id"]),
                    episode_number=int(task["episode_number"]),
                    release_id=int(task["release_id"]),
                    download_task_id=int(task["id"]),
                    status="completed" if status == "completed" else "submitted",
                    target_dir=str(task["target_dir"] or ""),
                    normalized_name=str(task["normalized_name"] or ""),
                    submission_id=task_id,
                    provider_file_id=file_id,
                )
                if status == "completed":
                    conn.execute(
                        "UPDATE download_tasks SET status='completed', updated_at=? WHERE id=?",
                        (ts, task["id"]),
                    )
                    enqueue_cloud_asset_task(conn, task["id"], ts)
                    request_queue_trigger("cloud_asset")
                else:
                    if not rclone_service.enabled(settings):
                        enqueue_cloud_poll_task(conn, task["id"], ts)
            log("info", f"已提交 PikPak: {task['title']}")
            submitted += 1


async def poll_submitted_tasks(settings: dict[str, str], limit: int = 20, force: bool = False) -> tuple[int, int]:
    with connect() as conn:
        conn.execute(
            """
            UPDATE cloud_poll_tasks
            SET status='pending', last_error='上次状态轮询中断，已自动放回待处理', updated_at=?
            WHERE status='running' AND updated_at < ?
            """,
            (now(), stale_running_cutoff()),
        )
        if force:
            conn.execute(
                """
                UPDATE cloud_poll_tasks
                SET retry_after='', updated_at=?
                WHERE status='pending' AND retry_after != ''
                """,
                (now(),),
            )
        local_tasks = conn.execute(
            """
            SELECT cpt.id AS poll_task_id, cpt.attempts AS poll_attempts,
                   dt.*, r.title
            FROM cloud_poll_tasks cpt
            JOIN download_tasks dt ON dt.id=cpt.download_task_id
            JOIN releases r ON r.id=dt.release_id
            JOIN entries e ON e.id=dt.entry_id
            JOIN cloud_submissions cs ON cs.download_task_id=dt.id
            WHERE cpt.status IN ('pending', 'failed')
              AND (cpt.retry_after='' OR cpt.retry_after <= ?)
              AND dt.status IN ('submitted', 'running')
              AND (dt.pikpak_task_id != '' OR dt.pikpak_file_id != '')
              AND e.bangumi_id != ''
              AND cs.provider='pikpak'
              AND cs.status IN ('submitted', 'running')
            ORDER BY cpt.id ASC
            LIMIT ?
            """
            ,
            (now(), limit),
        ).fetchall()
    if not local_tasks:
        return 0, 0

    try:
        remote_tasks = await list_offline_tasks(settings)
    except Exception as exc:
        log("error", f"PikPak 状态轮询失败: {exc}")
        with connect() as conn:
            retry_after = task_retry_after(settings, 1)
            for task in local_tasks:
                conn.execute(
                    """
                    UPDATE cloud_poll_tasks
                    SET status='pending', retry_after=?, last_error=?, updated_at=?
                    WHERE id=?
                    """,
                    (retry_after, str(exc)[:2000], now(), task["poll_task_id"]),
                )
        return 0, len(local_tasks)

    by_id = {task.get("id"): task for task in remote_tasks if task.get("id")}
    completed = 0
    failed = 0
    for task in local_tasks:
        with connect() as conn:
            conn.execute(
                "UPDATE cloud_poll_tasks SET status='running', attempts=attempts+1, updated_at=? WHERE id=?",
                (now(), task["poll_task_id"]),
            )
        remote = by_id.get(task["pikpak_task_id"])
        if not remote:
            if task["pikpak_file_id"] and not task["pikpak_task_id"]:
                with connect() as conn:
                    ts = now()
                    conn.execute(
                        "UPDATE download_tasks SET status='completed', retry_after='', last_error='', updated_at=? WHERE id=?",
                        (ts, task["id"]),
                    )
                    sync_cloud_submission(
                        conn,
                        series_id=0,
                        entry_id=int(task["entry_id"]),
                        episode_number=int(task["episode_number"]),
                        release_id=int(task["release_id"]),
                        download_task_id=int(task["id"]),
                        status="completed",
                        target_dir=str(task["target_dir"] or ""),
                        normalized_name=str(task["normalized_name"] or ""),
                        submission_id=str(task["pikpak_task_id"] or ""),
                        provider_file_id=str(task["pikpak_file_id"] or ""),
                    )
                    conn.execute(
                        "UPDATE cloud_poll_tasks SET status='completed', retry_after='', last_error='', updated_at=? WHERE id=?",
                        (ts, task["poll_task_id"]),
                    )
                    enqueue_cloud_asset_task(conn, task["id"], ts)
                    request_queue_trigger("cloud_asset")
                completed += 1
                continue
            with connect() as conn:
                conn.execute(
                    """
                    UPDATE cloud_poll_tasks
                    SET status='pending', retry_after=?, last_error=?, updated_at=?
                    WHERE id=?
                    """,
                    (
                        task_retry_after(settings, int(task["poll_attempts"] or 0) + 1),
                        "PikPak 暂未返回该离线任务，等待后重试",
                        now(),
                        task["poll_task_id"],
                    ),
                )
                sync_cloud_submission(
                    conn,
                    series_id=0,
                    entry_id=int(task["entry_id"]),
                    episode_number=int(task["episode_number"]),
                    release_id=int(task["release_id"]),
                    download_task_id=int(task["id"]),
                    status="submitted",
                    target_dir=str(task["target_dir"] or ""),
                    normalized_name=str(task["normalized_name"] or ""),
                    submission_id=str(task["pikpak_task_id"] or ""),
                    provider_file_id=str(task["pikpak_file_id"] or ""),
                    retry_after=task_retry_after(settings, int(task["poll_attempts"] or 0) + 1),
                    last_error="PikPak 暂未返回该离线任务，等待后重试",
                )
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
            ts = now()
            conn.execute(
                """
                UPDATE download_tasks
                SET status=?, pikpak_file_id=?, last_error=?, updated_at=?
                WHERE id=?
                """,
                (status, file_id, remote.get("message", "")[:2000], ts, task["id"]),
            )
            sync_cloud_submission(
                conn,
                series_id=0,
                entry_id=int(task["entry_id"]),
                episode_number=int(task["episode_number"]),
                release_id=int(task["release_id"]),
                download_task_id=int(task["id"]),
                status=status,
                target_dir=str(task["target_dir"] or ""),
                normalized_name=str(task["normalized_name"] or ""),
                submission_id=str(task["pikpak_task_id"] or ""),
                provider_file_id=str(file_id or ""),
                retry_after="" if status == "completed" else task_retry_after(settings, int(task["poll_attempts"] or 0) + 1),
                last_error=str(remote.get("message", "") or "")[:2000],
            )
            if status == "completed":
                conn.execute(
                    "UPDATE episodes SET status='downloaded', updated_at=? WHERE entry_id=? AND episode_number=(SELECT episode_number FROM releases WHERE id=?)",
                    (ts, task["entry_id"], task["release_id"]),
                )
                conn.execute(
                    "UPDATE cloud_poll_tasks SET status='completed', retry_after='', last_error='', updated_at=? WHERE id=?",
                    (ts, task["poll_task_id"]),
                )
                enqueue_cloud_asset_task(conn, task["id"], ts)
                request_queue_trigger("cloud_asset")
                completed += 1
            elif status == "failed":
                conn.execute(
                    """
                    UPDATE cloud_poll_tasks
                    SET status='pending', retry_after=?, last_error=?, updated_at=?
                    WHERE id=?
                    """,
                    (
                        task_retry_after(settings, int(task["poll_attempts"] or 0) + 1),
                        remote.get("message", "")[:2000],
                        ts,
                        task["poll_task_id"],
                    ),
                )
                failed += 1
            else:
                conn.execute(
                    "UPDATE cloud_poll_tasks SET status='pending', retry_after=?, last_error='', updated_at=? WHERE id=?",
                    (task_retry_after(settings, int(task["poll_attempts"] or 0) + 1), ts, task["poll_task_id"]),
                )
        if status == "completed" and file_id and task["normalized_name"]:
            try:
                await rename_cloud_file(settings, file_id, task["normalized_name"])
            except Exception as exc:
                log("warn", f"云端重命名失败: {task['title']} - {exc}")
    return completed, failed


async def scan_and_queue(settings: dict[str, str], progress_callback=None) -> str:
    if not settings.get("rss_url"):
        log("warn", "未配置 Mikan RSS")
        return "未配置 Mikan RSS"
    try:
        if progress_callback:
            progress_callback("正在请求 RSS 源")
        items = await fetch_entries(settings)
    except Exception as exc:
        log("error", f"RSS 扫描失败: {exc}")
        return f"RSS 扫描失败: {exc}"

    candidate_count = 0
    total = len(items)
    if progress_callback:
        progress_callback(f"RSS 获取完成，共 {total} 条，开始写入候选")
    for item in items:
        upsert_rss_candidate(item)
        candidate_count += 1
        if progress_callback:
            progress_callback(f"正在写入 RSS 候选 {candidate_count}/{total}")
    log("info", f"RSS 扫描完成: {len(items)} 条发布，写入候选 {candidate_count} 条")
    return f"RSS {len(items)} 条，写入候选 {candidate_count} 条"
