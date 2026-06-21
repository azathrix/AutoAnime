from __future__ import annotations

import html
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin

import httpx
import feedparser

from .database import connect
from .db import get_settings, hide_orphan_series, log, merge_duplicate_series, now
from .library import bool_setting, parse_entry_labels
from .metadata import fetch_bangumi_metadata
from .parser import ParsedRelease, fingerprint, normalize_title_key, parse_entry, parse_episode, parse_group, parse_language, parse_resolution, parse_series_title, parse_subtitle_format, parse_year, split_lines


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
        log(
            "info",
            f"Mikan 匹配请求: url={first_url} status={resp.status_code} "
            f"bangumi_id={bangumi_id or '-'} mikan_id={mikan_id or '-'} bytes={len(resp.text)}",
        )
        if bangumi_id and mikan_id:
            return bangumi_id, mikan_id
        if mikan_id:
            bgm_url = mikan_absolute_url(f"/Home/Bangumi/{mikan_id}")
            bgm_resp = await client.get(bgm_url)
            bgm_resp.raise_for_status()
            bangumi_id, parsed_mikan_id = parse_mikan_ids(bgm_resp.text)
            mikan_id = parsed_mikan_id or mikan_id
            log(
                "info",
                f"Mikan 番组页请求: url={bgm_url} status={bgm_resp.status_code} "
                f"bangumi_id={bangumi_id or '-'} mikan_id={mikan_id or '-'} bytes={len(bgm_resp.text)}",
            )
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
                subtitle_format=parse_subtitle_format(raw_title),
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
            log("info", f"Mikan ID 反查命中: entry_id={entry_id} bangumi_id={bangumi_id} mikan_id={mikan_id}")
            return mikan_id
        log("info", f"Mikan ID 反查未命中: entry_id={entry_id} candidate_id={candidate['id']} matched_bangumi={matched_bangumi_id or '-'} mikan_id={mikan_id or '-'}")
    return ""


def parse_sort_time(value: str) -> float:
    text = str(value or "").strip()
    if not text:
        return 0
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0


def priority_rank(value: str, priority: list[str], field: str) -> int:
    if not priority:
        return 0
    text = str(value or "")
    for index, preferred in enumerate(priority):
        if text.lower() == preferred.lower() or priority_match(text, preferred, field):
            return index
    return len(priority) + 1


def language_rank(value: str, primary: list[str], secondary: list[str]) -> tuple[int, int]:
    tokens = language_tokens(value)
    first = priority_rank(tokens[0] if len(tokens) > 0 else "", primary, "language")
    second = priority_rank(tokens[1] if len(tokens) > 1 else "", secondary, "language")
    return first, second


def release_priority_key(row: dict, settings: dict[str, str]) -> tuple:
    use_priority = bool_setting(settings.get("auto_download_by_priority", "true"))
    subtitle_priority = split_lines(settings.get("subtitle_priority", "")) if use_priority else []
    resolution_priority = split_lines(settings.get("resolution_priority", "")) if use_priority else []
    language_priority = split_lines(settings.get("language_priority", "")) if use_priority else []
    secondary_language_priority = split_lines(settings.get("secondary_language_priority", "")) if use_priority else []
    language_first, language_second = language_rank(str(row.get("language") or ""), language_priority, secondary_language_priority)
    return (
        priority_rank(str(row.get("subtitle_group") or ""), subtitle_priority, "subtitle_group"),
        priority_rank(str(row.get("resolution") or ""), resolution_priority, "resolution"),
        language_first,
        language_second,
        -parse_sort_time(str(row.get("published_at") or "")),
        -int(row.get("id") or 0),
    )


def release_has_downstream(conn, release_id: int) -> bool:
    checks = [
        "download_jobs",
        "download_artifacts",
        "local_assets",
    ]
    for table in checks:
        row = conn.execute(f"SELECT 1 FROM {table} WHERE release_id=? LIMIT 1", (release_id,)).fetchone()
        if row:
            return True
    return False


def coalesce_episode_release(conn, entry_id: int, episode_number: int, item: ParsedRelease, ts: str) -> int | None:
    if episode_number <= 0:
        return None
    settings = get_settings()
    existing_rows = conn.execute(
        """
        SELECT *
        FROM releases
        WHERE entry_id=? AND episode_number=?
        ORDER BY selected DESC, id ASC
        """,
        (entry_id, episode_number),
    ).fetchall()
    if not existing_rows:
        return None
    existing_guid = conn.execute("SELECT id FROM releases WHERE guid=?", (item.guid,)).fetchone()
    if existing_guid:
        log(
            "info",
            f"RSS 发布去重: entry_id={entry_id} episode={episode_number} release_id={existing_guid['id']} reason=guid已存在",
        )
        return int(existing_guid["id"])
    new_row = {
        "id": 0,
        "subtitle_group": item.subtitle_group,
        "resolution": item.resolution,
        "language": item.language,
        "subtitle_format": item.subtitle_format,
        "published_at": item.published_at,
    }
    best_existing = min((dict(row) for row in existing_rows), key=lambda row: release_priority_key(row, settings))
    if release_priority_key(best_existing, settings) <= release_priority_key(new_row, settings):
        log(
            "info",
            f"RSS 发布合并保留旧项: entry_id={entry_id} episode={episode_number} "
            f"keep_release_id={best_existing['id']} new_group={item.subtitle_group or '-'} "
            f"new_resolution={item.resolution or '-'} new_language={item.language or '-'}",
        )
        return int(best_existing["id"])
    keep_id = int(best_existing["id"])
    if release_has_downstream(conn, keep_id):
        log(
            "info",
            f"RSS 发布合并保留旧项: entry_id={entry_id} episode={episode_number} "
            f"keep_release_id={keep_id} reason=已有后续任务 new_guid={item.guid}",
        )
        return keep_id
    conn.execute(
        """
        UPDATE releases
        SET guid=?, title=?, subtitle_group=?, resolution=?, language=?,
            subtitle_format=?, torrent_url=?, magnet=?, published_at=?, updated_at=?
        WHERE id=?
        """,
        (
            item.guid,
            item.title,
            item.subtitle_group,
            item.resolution,
            item.language,
            item.subtitle_format,
            item.torrent_url,
            item.magnet,
            item.published_at,
            ts,
            keep_id,
        ),
    )
    log(
        "info",
        f"RSS 发布合并替换旧项: entry_id={entry_id} episode={episode_number} "
        f"release_id={keep_id} group={item.subtitle_group or '-'} resolution={item.resolution or '-'} "
        f"language={item.language or '-'}",
    )
    return keep_id


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
        target_library = conn.execute("SELECT id FROM media_libraries WHERE key='seasonal_anime'").fetchone()
        target_library_id = int(target_library["id"] or 0) if target_library else 0
        conn.execute(
            """
            INSERT INTO entries
              (work_id, fingerprint, domain_kind, media_type, region, source_provider, metadata_provider,
               external_id, target_library_id, entry_kind, display_title, title_root,
               season_label, arc_label, part_label, special_label,
               title_raw, title_cn, bangumi_id, mikan_bangumi_id, tmdb_id, year, season_number,
               poster_url, summary, metadata_source, hidden, auto_download, selected_group, selected_resolution,
               backfill_mode, created_at, updated_at)
            VALUES (?, ?, 'seasonal', 'anime', 'jp', 'mikan', 'bangumi', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, 'bangumi', 0, 'inherit', '', '', 'inherit', ?, ?)
            ON CONFLICT(fingerprint) DO UPDATE SET
              work_id=excluded.work_id,
              domain_kind='seasonal',
              media_type=excluded.media_type,
              region=excluded.region,
              source_provider=excluded.source_provider,
              metadata_provider=excluded.metadata_provider,
              external_id=CASE WHEN excluded.external_id!='' THEN excluded.external_id ELSE entries.external_id END,
              target_library_id=CASE WHEN entries.target_library_id=0 THEN excluded.target_library_id ELSE entries.target_library_id END,
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
                str(item.bangumi_id or item.mikan_bangumi_id or ""),
                target_library_id,
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
        coalesced_release_id = coalesce_episode_release(conn, int(entry_id), int(item.episode_number or 0), item, ts)
        if coalesced_release_id:
            release_id = coalesced_release_id
            conn.execute(
                "UPDATE releases SET series_id=?, entry_id=?, updated_at=? WHERE id=?",
                (series_id, entry_id, ts, release_id),
            )
        else:
            conn.execute(
                """
                INSERT INTO releases
                  (series_id, entry_id, episode_number, guid, title, subtitle_group, resolution, language, subtitle_format,
                   torrent_url, magnet, published_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(guid) DO UPDATE SET
                  series_id=excluded.series_id,
                  entry_id=excluded.entry_id,
                  episode_number=excluded.episode_number,
                  title=excluded.title,
                  subtitle_group=excluded.subtitle_group,
                  resolution=excluded.resolution,
                  language=excluded.language,
                  subtitle_format=excluded.subtitle_format,
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
                    item.subtitle_format,
                    item.torrent_url,
                    item.magnet,
                    item.published_at,
                    ts,
                    ts,
                ),
            )
            release_id = conn.execute("SELECT id FROM releases WHERE guid=?", (item.guid,)).fetchone()["id"]
        conn.execute(
            "UPDATE download_artifacts SET series_id=? WHERE release_id=?",
            (series_id, release_id),
        )
        conn.execute(
            "UPDATE download_artifacts SET entry_id=? WHERE release_id=?",
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
            "UPDATE download_jobs SET entry_id=? WHERE release_id=?",
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
               language, subtitle_format, bangumi_id, mikan_bangumi_id, torrent_url, magnet, page_url, published_at, status,
               reason, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(guid) DO UPDATE SET
              title=excluded.title,
              series_title=excluded.series_title,
              episode_number=excluded.episode_number,
              subtitle_group=excluded.subtitle_group,
              resolution=excluded.resolution,
              language=excluded.language,
              subtitle_format=excluded.subtitle_format,
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
                item.subtitle_format,
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
        return candidate_id


def candidate_to_parsed_release(candidate) -> ParsedRelease:
    return ParsedRelease(
        guid=candidate["guid"],
        title=candidate["title"],
        series_title=candidate["series_title"],
        episode_number=candidate["episode_number"],
        subtitle_group=candidate["subtitle_group"],
        resolution=candidate["resolution"],
        language=candidate["language"],
        subtitle_format=candidate["subtitle_format"] if "subtitle_format" in candidate.keys() else "",
        bangumi_id=candidate["bangumi_id"],
        year=0,
        torrent_url=candidate["torrent_url"],
        magnet=candidate["magnet"],
        page_url=candidate["page_url"],
        mikan_bangumi_id=candidate["mikan_bangumi_id"],
        published_at=candidate["published_at"],
    )


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


def choose_episode_release(rows: list, settings: dict[str, str]) -> tuple[int | None, dict[str, str]]:
    info = {
        "selected_group": "",
        "selected_resolution": "",
        "selected_language": "",
        "fallback_reason": "",
    }
    candidates = list(rows)
    if not candidates:
        return None, info
    use_priority = bool_setting(settings.get("auto_download_by_priority", "true"))

    candidates, selected_group, reason = filter_by_priority(
        candidates,
        "subtitle_group",
        split_lines(settings.get("subtitle_priority", "")) if use_priority else [],
    )
    if selected_group:
        info["selected_group"] = selected_group
    if reason and not info["fallback_reason"]:
        info["fallback_reason"] = reason.replace("subtitle_group", "字幕组")

    candidates, selected_resolution, reason = filter_by_priority(
        candidates,
        "resolution",
        split_lines(settings.get("resolution_priority", "")) if use_priority else [],
    )
    if selected_resolution:
        info["selected_resolution"] = selected_resolution
    if reason and not info["fallback_reason"]:
        info["fallback_reason"] = reason.replace("resolution", "分辨率")

    candidates, selected_language, reason = filter_by_language_priority(
        candidates,
        split_lines(settings.get("language_priority", "")) if use_priority else [],
        split_lines(settings.get("secondary_language_priority", "")) if use_priority else [],
    )
    if selected_language:
        info["selected_language"] = selected_language
    if reason and not info["fallback_reason"]:
        info["fallback_reason"] = reason.replace("language", "语言")

    # candidates keep the original ordering from the SQL query; id DESC means
    # the newest persisted RSS item is used as the deterministic fallback.
    return int(candidates[0]["id"]), info


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
            ORDER BY episode_number ASC, published_at DESC, id DESC
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

    by_episode: dict[int, list] = {}
    for row in rows:
        by_episode.setdefault(row["episode_number"], []).append(row)
    ids: list[int] = []
    selected_groups: set[str] = set()
    selected_resolutions: set[str] = set()
    selected_languages: set[str] = set()
    fallback_reasons: list[str] = []
    for _, episode_rows in sorted(by_episode.items()):
        release_id, episode_choice = choose_episode_release(episode_rows, settings)
        if release_id:
            ids.append(release_id)
        if episode_choice.get("selected_group"):
            selected_groups.add(str(episode_choice["selected_group"]))
        if episode_choice.get("selected_resolution"):
            selected_resolutions.add(str(episode_choice["selected_resolution"]))
        if episode_choice.get("selected_language"):
            selected_languages.add(str(episode_choice["selected_language"]))
        if episode_choice.get("fallback_reason"):
            fallback_reasons.append(str(episode_choice["fallback_reason"]))
    if not ids:
        info["reason"] = "没有可选发布"
        return [], info
    info.update(
        {
            "selected_group": ", ".join(sorted(selected_groups)),
            "selected_resolution": ", ".join(sorted(selected_resolutions)),
            "selected_language": ", ".join(sorted(selected_languages)),
            "fallback_reason": fallback_reasons[0] if fallback_reasons else "",
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
        ("file_id",),
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




