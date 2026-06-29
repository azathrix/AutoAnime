from __future__ import annotations

import asyncio
import json
import re
import shutil
from typing import Any
from html import unescape
from urllib.parse import quote, quote_plus, urljoin
from xml.etree import ElementTree as ET

import feedparser
import httpx

from .database import connect
from .db import get_settings, log, now
from .download_task_service import queue_download_for_episode
from .download_worker_service import trigger_download_worker
from .library import parse_entry_labels
from .media_service import normalize_api_media_type
from .parser import ParsedRelease, clean_name, fingerprint, parse_entry, parse_group, parse_language, parse_resolution, parse_subtitle_format
from .processing_cache import get_cached_json, set_cached_json
from .schemas import BackfillApplyPayload, DiscoverySearchPayload, SearchSourcePayload


SUPPORTED_SEARCH_SOURCE_TYPES = {"mikan", "rss", "torznab", "prowlarr", "jackett", "generic_html", "qmp4"}
VIDEO_EXTENSIONS = (".mkv", ".mp4", ".avi", ".mov", ".wmv", ".webm", ".ts")
SUBTITLE_EXTENSIONS = (".ass", ".ssa", ".srt", ".vtt")
DISCOVERY_SOURCE_CACHE_TTL_SECONDS = 3600
QMP4_DEFAULT_BASE_URL = "https://www.qmp4.com"
QMP4_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
QMP4_DEFAULT_DETAIL_LIMIT = 10


def _text(value: Any) -> str:
    return str(value or "").strip()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _row_dict(row: Any) -> dict[str, Any]:
    return dict(row) if row else {}


def _source_kind(value: str) -> str:
    kind = _text(value).lower() or "mikan"
    if kind in {"prowlarr", "jackett"}:
        return "torznab"
    if kind in SUPPORTED_SEARCH_SOURCE_TYPES:
        return kind
    return "mikan"


def _stored_source_kind(value: str) -> str:
    kind = _text(value).lower() or "mikan"
    if kind in SUPPORTED_SEARCH_SOURCE_TYPES:
        return kind
    return "mikan"


def _split_categories(value: str) -> list[str]:
    return [item.strip() for item in re.split(r"[,，\s]+", value or "") if item.strip()]


def _config_dict(source: dict[str, Any]) -> dict[str, Any]:
    raw = source.get("config_json") or "{}"
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _config_int(source: dict[str, Any], key: str, default: int, minimum: int = 0, maximum: int | None = None) -> int:
    value = _config_dict(source).get(key, default)
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    parsed = max(minimum, parsed)
    if maximum is not None:
        parsed = min(maximum, parsed)
    return parsed


def _result_key(title: str, media_type: str = "anime", year: int = 0, bangumi_id: str = "", tmdb_id: str = "") -> str:
    if bangumi_id:
        return f"bangumi:{bangumi_id}"
    if tmdb_id:
        return f"tmdb:{tmdb_id}"
    labels = parse_entry_labels(title)
    root = _text(labels.get("title_root")) or clean_name(title)
    return fingerprint(f"{media_type}:{root}:{year or ''}", "")


def _resource_ref_from_release(item: ParsedRelease) -> str:
    return item.magnet or item.torrent_url or item.page_url or item.guid


def _release_to_item(source: dict[str, Any], release: ParsedRelease, media_type: str, year: int = 0) -> dict[str, Any]:
    labels = parse_entry_labels(release.series_title or release.title)
    title = _text(labels.get("title_root")) or release.series_title or release.title
    return {
        "result": {
            "result_key": _result_key(title, media_type, year or release.year, release.bangumi_id, ""),
            "title": title,
            "original_title": release.series_title or release.title,
            "media_type": media_type,
            "year": year or release.year,
            "bangumi_id": release.bangumi_id,
            "tmdb_id": "",
            "summary": "",
            "poster_url": "",
            "tags_json": "[]",
            "confidence": 0.65 if release.bangumi_id else 0.45,
        },
        "resource": {
            "source_id": int(source.get("id") or 0),
            "source_name": source.get("name") or "",
            "source_kind": source.get("kind") or "",
            "episode_number": int(release.episode_number or 0),
            "resource_ref": _resource_ref_from_release(release),
            "subtitle_ref": "",
            "subtitle_group": release.subtitle_group,
            "resolution": release.resolution,
            "language": release.language,
            "subtitle_format": release.subtitle_format,
            "source_title": release.title,
            "published_at": release.published_at,
        },
    }


def _torznab_item_to_release(item: ET.Element) -> ParsedRelease:
    def child_text(name: str) -> str:
        child = item.find(name)
        return _text(child.text if child is not None else "")

    title = child_text("title")
    link = child_text("link") or child_text("guid")
    enclosure = item.find("enclosure")
    if enclosure is not None and enclosure.attrib.get("url"):
        link = enclosure.attrib.get("url") or link
    attrs: dict[str, str] = {}
    for attr in item.findall("{http://torznab.com/schemas/2015/feed}attr"):
        name = _text(attr.attrib.get("name")).lower()
        value = _text(attr.attrib.get("value"))
        if name and value:
            attrs[name] = value
    magnet = attrs.get("magneturl") or (link if link.startswith("magnet:") else "")
    torrent_url = "" if magnet else link
    return ParsedRelease(
        guid=child_text("guid") or link or title,
        title=title,
        series_title=clean_name(re.sub(r"\bS\d{1,2}E\d{1,3}\b", "", title, flags=re.I)),
        episode_number=_episode_from_title(title),
        subtitle_group=parse_group(title),
        resolution=parse_resolution(title),
        language=parse_language(title),
        subtitle_format=parse_subtitle_format(title),
        bangumi_id="",
        mikan_bangumi_id="",
        torrent_url=torrent_url,
        magnet=magnet,
        page_url=child_text("comments") or link,
        published_at=child_text("pubDate"),
        year=_year_from_title(title),
    )


def _episode_from_title(title: str) -> int:
    match = re.search(r"\bS\d{1,2}E(\d{1,3})\b", title or "", re.I)
    if match:
        return int(match.group(1))
    matches = re.findall(r"(?<!\d)(\d{1,3})(?!\d)", title or "")
    for value in reversed(matches):
        number = int(value)
        if 1 <= number <= 300:
            return number
    return 0


def _year_from_title(title: str) -> int:
    match = re.search(r"(20\d{2}|19\d{2})", title or "")
    return int(match.group(1)) if match else 0


def list_search_sources() -> dict[str, Any]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM search_sources ORDER BY priority ASC, id ASC").fetchall()
    return {"items": [_row_dict(row) for row in rows]}


def save_search_source(payload: SearchSourcePayload, source_id: int = 0) -> dict[str, Any]:
    ts = now()
    kind = _stored_source_kind(payload.kind)
    name = payload.name.strip() or kind.upper()
    config_json = _json(payload.config or {})
    with connect() as conn:
        if source_id > 0:
            existing = conn.execute("SELECT id FROM search_sources WHERE id=?", (source_id,)).fetchone()
            if not existing:
                raise ValueError("搜索源不存在")
            conn.execute(
                """
                UPDATE search_sources
                SET name=?, kind=?, base_url=?, api_key=?, categories=?, proxy=?, timeout_seconds=?,
                    rate_limit_seconds=?, priority=?, enabled=?, config_json=?, updated_at=?
                WHERE id=?
                """,
                (
                    name,
                    kind,
                    payload.base_url.strip(),
                    payload.api_key.strip(),
                    payload.categories.strip(),
                    payload.proxy.strip(),
                    max(3, int(payload.timeout_seconds or 20)),
                    max(0, int(payload.rate_limit_seconds or 0)),
                    int(payload.priority or 0),
                    1 if payload.enabled else 0,
                    config_json,
                    ts,
                    source_id,
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO search_sources
                  (name, kind, base_url, api_key, categories, proxy, timeout_seconds, rate_limit_seconds,
                   priority, enabled, config_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    name,
                    kind,
                    payload.base_url.strip(),
                    payload.api_key.strip(),
                    payload.categories.strip(),
                    payload.proxy.strip(),
                    max(3, int(payload.timeout_seconds or 20)),
                    max(0, int(payload.rate_limit_seconds or 0)),
                    int(payload.priority or 0),
                    1 if payload.enabled else 0,
                    config_json,
                    ts,
                    ts,
                ),
            )
            source_id = int(conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"])
        row = conn.execute("SELECT * FROM search_sources WHERE id=?", (source_id,)).fetchone()
    return _row_dict(row)


def delete_search_source(source_id: int) -> dict[str, str]:
    with connect() as conn:
        conn.execute("DELETE FROM search_sources WHERE id=?", (source_id,))
    return {"status": "deleted"}


def reorder_search_sources(ids: list[int]) -> dict[str, Any]:
    clean_ids = [int(item) for item in ids if int(item or 0) > 0]
    ts = now()
    with connect() as conn:
        existing = {
            int(row["id"])
            for row in conn.execute("SELECT id FROM search_sources").fetchall()
        }
        for index, source_id in enumerate(clean_ids):
            if source_id in existing:
                conn.execute(
                    "UPDATE search_sources SET priority=?, updated_at=? WHERE id=?",
                    (index + 1, ts, source_id),
                )
    return list_search_sources()


async def test_search_source(source_id: int) -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM search_sources WHERE id=?", (source_id,)).fetchone()
    if not row:
        raise ValueError("搜索源不存在")
    source = _row_dict(row)
    try:
        count = len(await _search_source(source, "test", "anime", 0, ""))
        status = "ok"
        error = ""
    except Exception as exc:
        count = 0
        status = "failed"
        error = str(exc)[:500]
    with connect() as conn:
        conn.execute(
            "UPDATE search_sources SET last_status=?, last_error=?, updated_at=? WHERE id=?",
            (status, error, now(), source_id),
        )
    return {"status": status, "items": count, "error": error}


async def run_discovery_search(payload: DiscoverySearchPayload) -> dict[str, Any]:
    keyword = payload.keyword.strip()
    media_type = normalize_api_media_type(payload.media_type)
    ts = now()
    source_ids = [int(item) for item in payload.source_ids if int(item or 0) > 0]
    with connect() as conn:
        if source_ids:
            placeholders = ",".join(["?"] * len(source_ids))
            rows = conn.execute(
                f"SELECT * FROM search_sources WHERE enabled=1 AND id IN ({placeholders}) ORDER BY priority ASC, id ASC",
                source_ids,
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM search_sources WHERE enabled=1 ORDER BY priority ASC, id ASC").fetchall()
        conn.execute(
            """
            INSERT INTO discovery_searches
              (keyword, media_type, year, season, source_ids, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'running', ?, ?)
            """,
            (keyword, media_type, int(payload.year or 0), payload.season.strip(), ",".join(map(str, source_ids)), ts, ts),
        )
        search_id = int(conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"])

    errors: list[str] = []
    items: list[dict[str, Any]] = []
    for row in rows:
        source = _row_dict(row)
        try:
            items.extend(await _search_source(source, keyword, media_type, int(payload.year or 0), payload.season.strip()))
            with connect() as conn:
                conn.execute("UPDATE search_sources SET last_status='ok', last_error='', updated_at=? WHERE id=?", (now(), source["id"]))
        except Exception as exc:
            error = f"{source.get('name')}: {str(exc)[:300]}"
            errors.append(error)
            with connect() as conn:
                conn.execute(
                    "UPDATE search_sources SET last_status='failed', last_error=?, updated_at=? WHERE id=?",
                    (str(exc)[:500], now(), source["id"]),
                )
            log("warn", f"发现搜索源失败: {error}")

    result_count, resource_count = _persist_discovery_items(search_id, items, media_type)
    status = "completed" if items or not errors else "failed"
    with connect() as conn:
        conn.execute(
            """
            UPDATE discovery_searches
            SET status=?, result_count=?, resource_count=?, error=?, updated_at=?
            WHERE id=?
            """,
            (status, result_count, resource_count, "\n".join(errors), now(), search_id),
        )
    return discovery_results(search_id)


async def _search_source(source: dict[str, Any], keyword: str, media_type: str, year: int, season: str) -> list[dict[str, Any]]:
    kind = _source_kind(source.get("kind") or "")
    cache_ref = f"{source.get('id') or 0}:{kind}:{media_type}:{year}:{season}:{keyword}"
    cached = get_cached_json("discovery_source_search", cache_ref)
    if isinstance(cached, list):
        return cached
    if kind in {"mikan", "rss"}:
        result = await _search_feed_source(source, keyword, media_type, year)
    elif kind == "torznab":
        result = await _search_torznab_source(source, keyword, media_type, year)
    elif kind == "qmp4":
        result = await _search_qmp4_source(source, keyword, media_type, year)
    elif kind == "generic_html":
        result = []
    else:
        result = []
    set_cached_json("discovery_source_search", cache_ref, result, ttl_seconds=DISCOVERY_SOURCE_CACHE_TTL_SECONDS)
    return result


def _html_text(value: str) -> str:
    text = re.sub(r"<script\b.*?</script>", " ", value or "", flags=re.I | re.S)
    text = re.sub(r"<style\b.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _html_attr(value: str) -> str:
    return unescape(value or "").strip()


def _qmp4_base_url(source: dict[str, Any]) -> str:
    base_url = _text(source.get("base_url")) or QMP4_DEFAULT_BASE_URL
    return base_url.rstrip("/") or QMP4_DEFAULT_BASE_URL


def _qmp4_headers(base_url: str) -> dict[str, str]:
    return {
        "User-Agent": QMP4_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.7",
        "Referer": f"{base_url}/",
    }


def _qmp4_cookie(source: dict[str, Any]) -> str:
    value = _text(source.get("api_key")) or _text(_config_dict(source).get("cookie"))
    if value.lower().startswith("cookie:"):
        value = value.split(":", 1)[1].strip()
    return value


def _qmp4_is_verify_page(text: str) -> bool:
    return "系统安全验证" in (text or "") and "/index.php/ajax/verify_check?type=search" in (text or "")


def _qmp4_is_guard_page(text: str) -> bool:
    value = text or ""
    return (
        "/_guard/html.js" in value
        or "easy_click_html" in value
        or "Security Verification" in value
        or "Please click in sequence" in value
        or "请依次点击" in value
    )


def _qmp4_raise_if_blocked(text: str, label: str) -> None:
    if _qmp4_is_verify_page(text):
        raise RuntimeError(f"QMP4 {label}触发安全验证，已停止自动解析")
    if _qmp4_is_guard_page(text):
        raise RuntimeError(f"QMP4 {label}返回站点防护页，当前环境无法自动解析")


async def _qmp4_fetch_with_curl(url: str, timeout: int, proxy: str | None, cookie: str, label: str) -> str:
    curl_path = shutil.which("curl.exe") or shutil.which("curl")
    if not curl_path:
        raise RuntimeError(f"QMP4 {label}需要 curl 传输，但当前系统未找到 curl")
    args = [curl_path, "-L", "-sS", "--max-time", str(timeout)]
    if proxy:
        args.extend(["--proxy", proxy])
    if cookie:
        args.extend(["-H", f"Cookie: {cookie}"])
    args.append(url)
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout + 5)
    except asyncio.TimeoutError as exc:
        process.kill()
        await process.communicate()
        raise RuntimeError(f"QMP4 {label}请求超时") from exc
    if process.returncode != 0:
        message = stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"QMP4 {label}curl 请求失败: {message[:180] or process.returncode}")
    return stdout.decode("utf-8", errors="replace")


async def _qmp4_fetch_text(
    client: httpx.AsyncClient,
    url: str,
    label: str,
    timeout: int,
    proxy: str | None,
    cookie: str,
    params: dict[str, str] | None = None,
) -> str:
    request_url = str(httpx.URL(url, params=params or {}))
    # QMP4 currently returns a guard page to Python HTTP clients but serves curl normally.
    if shutil.which("curl.exe") or shutil.which("curl"):
        text = await _qmp4_fetch_with_curl(request_url, timeout, proxy, cookie, label)
        _qmp4_raise_if_blocked(text, label)
        return text
    response = await client.get(request_url)
    response.raise_for_status()
    _qmp4_raise_if_blocked(response.text, label)
    return response.text


def _qmp4_suggest_candidates(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or int(payload.get("code") or 0) != 1:
        return []
    items = payload.get("list")
    if not isinstance(items, list):
        return []
    result: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        qmp4_id = int(item.get("id") or 0)
        if qmp4_id <= 0:
            continue
        result.append({
            "id": qmp4_id,
            "name": _text(item.get("name")),
            "pic": _text(item.get("pic")),
        })
    return result


def _qmp4_extract_title(html_text: str, fallback: str = "") -> str:
    match = re.search(r"<h1[^>]*>(.*?)</h1>", html_text or "", re.I | re.S)
    if match:
        title = re.sub(r"<span\b.*?</span>", " ", match.group(1), flags=re.I | re.S)
        title = _html_text(title)
        if title:
            return title
    match = re.search(r"<title[^>]*>(.*?)</title>", html_text or "", re.I | re.S)
    if match:
        return re.split(r"在线观看|剧情介绍|迅雷下载|-", _html_text(match.group(1)))[0].strip() or fallback
    return fallback


def _qmp4_extract_year(html_text: str) -> int:
    match = re.search(r"<span[^>]*class=[\"']year[\"'][^>]*>\((\d{4})\)</span>", html_text or "", re.I)
    if match:
        return int(match.group(1))
    return _year_from_title(_html_text(html_text[:3000] if html_text else ""))


def _qmp4_extract_meta_content(html_text: str, name: str) -> str:
    match = re.search(
        rf"<meta[^>]+name=[\"']{re.escape(name)}[\"'][^>]+content=[\"']([^\"']*)[\"']",
        html_text or "",
        re.I,
    )
    if not match:
        match = re.search(
            rf"<meta[^>]+content=[\"']([^\"']*)[\"'][^>]+name=[\"']{re.escape(name)}[\"']",
            html_text or "",
            re.I,
        )
    return _html_attr(match.group(1)) if match else ""


def _qmp4_extract_poster(html_text: str, title: str, page_url: str) -> str:
    escaped_title = re.escape(title)
    match = re.search(
        rf"<img[^>]+src=[\"']([^\"']+)[\"'][^>]+alt=[\"']{escaped_title}[\"']",
        html_text or "",
        re.I,
    )
    if not match:
        match = re.search(r"<div[^>]+class=[\"']img[\"'][^>]*>.*?<img[^>]+src=[\"']([^\"']+)[\"']", html_text or "", re.I | re.S)
    return urljoin(page_url, _html_attr(match.group(1))) if match else ""


def _qmp4_extract_updated_at(html_text: str) -> str:
    match = re.search(r"最后更新于\s*<em>\s*(?:<[^>]+>)*([^<]+)", html_text or "", re.I)
    return _html_text(match.group(1)) if match else ""


def _qmp4_extract_tags(html_text: str) -> list[str]:
    tags: list[str] = []
    for label in ("类型", "地区", "语言"):
        pattern = rf"<div>\s*<span>{label}：</span>(.*?)</div>"
        match = re.search(pattern, html_text or "", re.I | re.S)
        if not match:
            continue
        for item in re.findall(r"<a\b[^>]*>(.*?)</a>", match.group(1), re.I | re.S):
            text = _html_text(item)
            if text and text not in tags:
                tags.append(text)
    return tags


def _qmp4_download_section(html_text: str) -> str:
    start = re.search(r"<h2>\s*迅雷下载\s*</h2>", html_text or "", re.I)
    if not start:
        return ""
    end = re.search(r"<h2>\s*网盘下载\s*</h2>", html_text[start.end():], re.I)
    return html_text[start.end(): start.end() + end.start()] if end else html_text[start.end():]


def _qmp4_normalize_magnet(value: str) -> str:
    text = _html_attr(value)
    if not text.lower().startswith("magnet:?"):
        return text
    query = text.split("?", 1)[1]
    parts: list[str] = []
    for raw_part in query.split("&"):
        if not raw_part:
            continue
        if "=" not in raw_part:
            parts.append(quote(raw_part, safe=""))
            continue
        key, raw_value = raw_part.split("=", 1)
        key = quote(unescape(key), safe="")
        safe = ":" if key.lower() == "xt" else ""
        parts.append(f"{key}={quote(unescape(raw_value), safe=safe)}")
    return f"magnet:?{'&'.join(parts)}"


def _qmp4_resource_ref(raw_value: str, page_url: str) -> str:
    value = _html_attr(raw_value)
    lowered = value.lower()
    if lowered.startswith("magnet:?"):
        return _qmp4_normalize_magnet(value)
    if lowered.startswith(("ed2k://", "thunder://")):
        return value
    if lowered.startswith(("http://", "https://")) and (".torrent" in lowered or "torrent" in lowered):
        return value
    if value.startswith("/") and (".torrent" in lowered or "torrent" in lowered):
        return urljoin(page_url, value)
    return ""


def _qmp4_clean_resource_title(value: str) -> str:
    title = _html_text(value)
    title = re.sub(r"\s+", " ", title).strip()
    return title[:300]


def _qmp4_resource_title(block: str) -> str:
    match = re.search(r"<a\b[^>]*class=[\"'][^\"']*\bfolder\b[^\"']*[\"'][^>]*title=[\"']([^\"']+)[\"']", block or "", re.I | re.S)
    if not match:
        match = re.search(r"<a\b[^>]*title=[\"']([^\"']+)[\"'][^>]*class=[\"'][^\"']*\bfolder\b", block or "", re.I | re.S)
    if match:
        return _qmp4_clean_resource_title(match.group(1))
    match = re.search(r"<p[^>]*class=[\"']down-list3[\"'][^>]*>.*?<a\b[^>]*>(.*?)</a>", block or "", re.I | re.S)
    return _qmp4_clean_resource_title(match.group(1)) if match else ""


def _qmp4_parse_download_resources(html_text: str, page_url: str, updated_at: str) -> list[dict[str, Any]]:
    section = _qmp4_download_section(html_text)
    if not section:
        return []
    resources: list[dict[str, Any]] = []
    seen: set[str] = set()
    blocks = re.findall(r"<li\b[^>]*class=[\"'][^\"']*\bdown-list2\b[^\"']*[\"'][^>]*>(.*?)</li>", section, re.I | re.S)
    for block in blocks:
        title = _qmp4_resource_title(block)
        hrefs = re.findall(r"\bhref=[\"']([^\"']+)[\"']", block, re.I)
        for href in hrefs:
            ref = _qmp4_resource_ref(href, page_url)
            if not ref or ref in seen:
                continue
            seen.add(ref)
            title_seed = title or ref
            resources.append({
                "episode_number": 0,
                "resource_ref": ref,
                "subtitle_group": "",
                "resolution": "",
                "language": "",
                "subtitle_format": "",
                "source_title": title_seed,
                "published_at": updated_at,
            })
            break
    return resources


def _qmp4_detail_to_items(
    source: dict[str, Any],
    candidate: dict[str, Any],
    html_text: str,
    page_url: str,
    media_type: str,
    requested_year: int,
) -> list[dict[str, Any]]:
    title = _qmp4_extract_title(html_text, candidate.get("name") or "")
    year = _qmp4_extract_year(html_text)
    if requested_year and year and requested_year != year:
        return []
    updated_at = _qmp4_extract_updated_at(html_text)
    summary = _qmp4_extract_meta_content(html_text, "description")
    if summary.startswith(f"{title}剧情:"):
        summary = summary[len(f"{title}剧情:"):].strip()
    result = {
        "result_key": _result_key(title, media_type, requested_year or year, "", ""),
        "title": title,
        "original_title": title,
        "media_type": media_type,
        "year": requested_year or year,
        "bangumi_id": "",
        "tmdb_id": "",
        "summary": summary,
        "poster_url": _qmp4_extract_poster(html_text, title, page_url) or candidate.get("pic") or "",
        "tags_json": _json(_qmp4_extract_tags(html_text)),
        "confidence": 0.5,
    }
    parsed_resources = _qmp4_parse_download_resources(html_text, page_url, updated_at)
    if not parsed_resources:
        return [{"result": result, "resource": {
            "source_id": int(source.get("id") or 0),
            "source_name": source.get("name") or "",
            "source_kind": source.get("kind") or "",
            "episode_number": 0,
            "resource_ref": "",
            "subtitle_ref": "",
            "subtitle_group": "",
            "resolution": "",
            "language": "",
            "subtitle_format": "",
            "source_title": title,
            "published_at": updated_at,
        }}]
    items: list[dict[str, Any]] = []
    for resource in parsed_resources:
        items.append({"result": result, "resource": {
            "source_id": int(source.get("id") or 0),
            "source_name": source.get("name") or "",
            "source_kind": source.get("kind") or "",
            "episode_number": int(resource.get("episode_number") or 0),
            "resource_ref": resource.get("resource_ref") or "",
            "subtitle_ref": "",
            "subtitle_group": resource.get("subtitle_group") or "",
            "resolution": resource.get("resolution") or "",
            "language": resource.get("language") or "",
            "subtitle_format": resource.get("subtitle_format") or "",
            "source_title": resource.get("source_title") or title,
            "published_at": resource.get("published_at") or "",
        }})
    return items


async def _search_qmp4_source(source: dict[str, Any], keyword: str, media_type: str, year: int) -> list[dict[str, Any]]:
    base_url = _qmp4_base_url(source)
    timeout = max(3, int(source.get("timeout_seconds") or 20))
    proxy = source.get("proxy") or get_settings().get("rss_proxy") or None
    cookie = _qmp4_cookie(source)
    detail_limit = _config_int(source, "detail_limit", QMP4_DEFAULT_DETAIL_LIMIT, 1, 20)
    async with httpx.AsyncClient(
        proxy=proxy,
        timeout=timeout,
        follow_redirects=True,
        headers=_qmp4_headers(base_url),
    ) as client:
        direct_match = re.search(r"(?:https?://[^/]+)?/mv/(\d+)\.html", keyword or "", re.I)
        if direct_match:
            qmp4_id = int(direct_match.group(1))
            page_url = urljoin(f"{base_url}/", f"/mv/{qmp4_id}.html")
            detail_text = await _qmp4_fetch_text(client, page_url, "详情页", timeout, proxy, cookie)
            return _qmp4_detail_to_items(source, {"id": qmp4_id, "name": "", "pic": ""}, detail_text, page_url, media_type, year)

        suggest_text = await _qmp4_fetch_text(
            client,
            urljoin(f"{base_url}/", "/index.php/ajax/suggest"),
            "搜索接口",
            timeout,
            proxy,
            cookie,
            {"mid": "1", "wd": keyword},
        )
        if "Website request error" in suggest_text or "502 Bad Gateway" in suggest_text:
            raise RuntimeError("QMP4 搜索接口返回站点/CDN错误，可能被风控或临时不可用")
        try:
            suggest_payload = json.loads(suggest_text)
        except json.JSONDecodeError as exc:
            if "<html" in suggest_text[:500].lower() or "<!doctype" in suggest_text[:500].lower():
                raise RuntimeError("QMP4 搜索接口返回 HTML 响应，可能被风控或临时不可用") from exc
            raise RuntimeError("QMP4 搜索接口返回非 JSON 响应，无法自动解析") from exc
        candidates = _qmp4_suggest_candidates(suggest_payload)
        result: list[dict[str, Any]] = []
        for candidate in candidates[:detail_limit]:
            page_url = urljoin(f"{base_url}/", f"/mv/{candidate['id']}.html")
            detail_text = await _qmp4_fetch_text(client, page_url, "详情页", timeout, proxy, cookie)
            result.extend(_qmp4_detail_to_items(source, candidate, detail_text, page_url, media_type, year))
    return result


async def _search_feed_source(source: dict[str, Any], keyword: str, media_type: str, year: int) -> list[dict[str, Any]]:
    base_url = source.get("base_url") or "https://mikanani.me/RSS/Search?searchstr={keyword}"
    url = base_url.replace("{keyword}", quote_plus(keyword)).replace("{q}", quote_plus(keyword))
    timeout = max(3, int(source.get("timeout_seconds") or 20))
    proxy = source.get("proxy") or get_settings().get("rss_proxy") or None
    async with httpx.AsyncClient(proxy=proxy, timeout=timeout, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
    parsed = feedparser.parse(resp.text)
    result: list[dict[str, Any]] = []
    for entry in parsed.entries:
        release = parse_entry(entry)
        text = f"{release.title} {release.series_title}".lower()
        if keyword and keyword.lower() not in text:
            continue
        if year and release.year and release.year != year:
            continue
        result.append(_release_to_item(source, release, media_type, year))
    return result


async def _search_torznab_source(source: dict[str, Any], keyword: str, media_type: str, year: int) -> list[dict[str, Any]]:
    base_url = (source.get("base_url") or "").rstrip("/")
    if not base_url:
        return []
    url = base_url if base_url.endswith("/api") else f"{base_url}/api"
    params: dict[str, str] = {"t": "search", "q": keyword}
    api_key = source.get("api_key") or ""
    if api_key:
        params["apikey"] = api_key
    categories = _split_categories(source.get("categories") or "")
    if categories:
        params["cat"] = ",".join(categories)
    timeout = max(3, int(source.get("timeout_seconds") or 20))
    proxy = source.get("proxy") or get_settings().get("rss_proxy") or None
    async with httpx.AsyncClient(proxy=proxy, timeout=timeout, follow_redirects=True) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
    root = ET.fromstring(resp.content)
    result: list[dict[str, Any]] = []
    for item in root.findall(".//item"):
        release = _torznab_item_to_release(item)
        if year and release.year and release.year != year:
            continue
        result.append(_release_to_item(source, release, media_type, year))
    return result


def _persist_discovery_items(search_id: int, items: list[dict[str, Any]], media_type: str) -> tuple[int, int]:
    ts = now()
    with connect() as conn:
        result_ids: dict[str, int] = {}
        source_sets: dict[int, set[int]] = {}
        for item in items:
            result = item["result"]
            result_key = result["result_key"]
            conn.execute(
                """
                INSERT INTO discovery_results
                  (search_id, result_key, title, original_title, media_type, year, bangumi_id, tmdb_id,
                   summary, poster_url, tags_json, confidence, source_count, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT(search_id, result_key) DO UPDATE SET
                  title=CASE WHEN discovery_results.title='' THEN excluded.title ELSE discovery_results.title END,
                  original_title=CASE WHEN discovery_results.original_title='' THEN excluded.original_title ELSE discovery_results.original_title END,
                  bangumi_id=CASE WHEN excluded.bangumi_id!='' THEN excluded.bangumi_id ELSE discovery_results.bangumi_id END,
                  tmdb_id=CASE WHEN excluded.tmdb_id!='' THEN excluded.tmdb_id ELSE discovery_results.tmdb_id END,
                  confidence=MAX(discovery_results.confidence, excluded.confidence),
                  updated_at=excluded.updated_at
                """,
                (
                    search_id,
                    result_key,
                    result["title"],
                    result["original_title"],
                    result.get("media_type") or media_type,
                    int(result.get("year") or 0),
                    result.get("bangumi_id") or "",
                    result.get("tmdb_id") or "",
                    result.get("summary") or "",
                    result.get("poster_url") or "",
                    result.get("tags_json") or "[]",
                    float(result.get("confidence") or 0),
                    ts,
                    ts,
                ),
            )
            row = conn.execute(
                "SELECT id FROM discovery_results WHERE search_id=? AND result_key=?",
                (search_id, result_key),
            ).fetchone()
            result_id = int(row["id"] or 0)
            result_ids[result_key] = result_id
            source_sets.setdefault(result_id, set()).add(int(item["resource"].get("source_id") or 0))
            resource = item["resource"]
            if not resource.get("resource_ref") and not resource.get("subtitle_ref"):
                continue
            conn.execute(
                """
                INSERT INTO discovery_resources
                  (result_id, search_id, source_id, source_name, source_kind, episode_number,
                   resource_ref, subtitle_ref, subtitle_group, resolution, language, subtitle_format,
                   source_title, published_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(result_id, episode_number, resource_ref, subtitle_ref) DO UPDATE SET
                  subtitle_group=excluded.subtitle_group,
                  resolution=excluded.resolution,
                  language=excluded.language,
                  subtitle_format=excluded.subtitle_format,
                  source_title=excluded.source_title,
                  published_at=excluded.published_at,
                  updated_at=excluded.updated_at
                """,
                (
                    result_id,
                    search_id,
                    int(resource.get("source_id") or 0),
                    resource.get("source_name") or "",
                    resource.get("source_kind") or "",
                    int(resource.get("episode_number") or 0),
                    resource.get("resource_ref") or "",
                    resource.get("subtitle_ref") or "",
                    resource.get("subtitle_group") or "",
                    resource.get("resolution") or "",
                    resource.get("language") or "",
                    resource.get("subtitle_format") or "",
                    resource.get("source_title") or "",
                    resource.get("published_at") or "",
                    ts,
                    ts,
                ),
            )
        for result_id, source_ids in source_sets.items():
            conn.execute(
                "UPDATE discovery_results SET source_count=?, updated_at=? WHERE id=?",
                (len([item for item in source_ids if item > 0]), ts, result_id),
            )
        result_count = conn.execute(
            "SELECT COUNT(*) AS c FROM discovery_results WHERE search_id=?",
            (search_id,),
        ).fetchone()["c"]
        resource_count = conn.execute(
            "SELECT COUNT(*) AS c FROM discovery_resources WHERE search_id=?",
            (search_id,),
        ).fetchone()["c"]
    return int(result_count or 0), int(resource_count or 0)


def discovery_results(search_id: int = 0) -> dict[str, Any]:
    with connect() as conn:
        if search_id > 0:
            search = conn.execute("SELECT * FROM discovery_searches WHERE id=?", (search_id,)).fetchone()
            results = conn.execute(
                """
                SELECT dr.*,
                       COUNT(res.id) AS resource_count,
                       COALESCE(MAX(res.episode_number), 0) AS max_episode
                FROM discovery_results dr
                LEFT JOIN discovery_resources res ON res.result_id=dr.id
                WHERE dr.search_id=?
                GROUP BY dr.id
                ORDER BY dr.confidence DESC, dr.id ASC
                """,
                (search_id,),
            ).fetchall()
        else:
            search = None
            results = conn.execute(
                """
                SELECT dr.*,
                       COUNT(res.id) AS resource_count,
                       COALESCE(MAX(res.episode_number), 0) AS max_episode
                FROM discovery_results dr
                LEFT JOIN discovery_resources res ON res.result_id=dr.id
                GROUP BY dr.id
                ORDER BY dr.updated_at DESC, dr.confidence DESC
                LIMIT 100
                """
            ).fetchall()
        resource_rows = conn.execute(
            "SELECT * FROM discovery_resources WHERE search_id=? ORDER BY episode_number ASC, id ASC",
            (search_id,),
        ).fetchall() if search_id > 0 else []
    resources_by_result: dict[int, list[dict[str, Any]]] = {}
    for row in resource_rows:
        item = _row_dict(row)
        resources_by_result.setdefault(int(item["result_id"] or 0), []).append(item)
    result_ids = [int(row["id"] or 0) for row in results]
    packages_by_result: dict[int, dict[str, Any]] = {}
    package_counts: dict[int, int] = {}
    if result_ids:
        placeholders = ",".join(["?"] * len(result_ids))
        with connect() as conn:
            package_rows = conn.execute(
                f"""
                SELECT *
                FROM resource_packages
                WHERE result_id IN ({placeholders})
                ORDER BY id DESC
                """,
                result_ids,
            ).fetchall()
        for package_row in package_rows:
            package = _row_dict(package_row)
            result_id = int(package.get("result_id") or 0)
            package_counts[result_id] = package_counts.get(result_id, 0) + 1
            packages_by_result.setdefault(result_id, package)
    items = []
    for row in results:
        item = _row_dict(row)
        item["resources"] = resources_by_result.get(int(item["id"] or 0), [])
        item["package"] = packages_by_result.get(int(item["id"] or 0), {})
        item["package_count"] = package_counts.get(int(item["id"] or 0), 0)
        items.append(item)
    return {"search": _row_dict(search), "items": items}


def collect_draft(result_id: int) -> dict[str, Any]:
    with connect() as conn:
        result = conn.execute("SELECT * FROM discovery_results WHERE id=?", (result_id,)).fetchone()
        if not result:
            raise ValueError("发现结果不存在")
        resources = conn.execute(
            "SELECT * FROM discovery_resources WHERE result_id=? ORDER BY episode_number ASC, id ASC",
            (result_id,),
        ).fetchall()
    row = _row_dict(result)
    entry = {
        "title": row.get("title") or row.get("original_title") or "",
        "bangumi_id": row.get("bangumi_id") or "",
        "tmdb_id": row.get("tmdb_id") or "",
        "year": int(row.get("year") or 0),
        "media_type": row.get("media_type") or "anime",
        "summary": row.get("summary") or "",
        "poster_url": row.get("poster_url") or "",
        "tags_json": row.get("tags_json") or "[]",
    }
    video_items = []
    subtitle_items = []
    for resource in [_row_dict(item) for item in resources]:
        item = {
            "kind": "link",
            "media_kind": "video",
            "ref": resource.get("resource_ref") or "",
            "episode_number": int(resource.get("episode_number") or 0),
            "title": resource.get("source_title") or "",
        }
        if item["ref"]:
            video_items.append(item)
        if resource.get("subtitle_ref"):
            subtitle_items.append({
                "kind": "link",
                "media_kind": "subtitle",
                "ref": resource.get("subtitle_ref") or "",
                "episode_number": int(resource.get("episode_number") or 0),
                "title": resource.get("source_title") or "",
            })
    return {"entry": entry, "resources": video_items, "subtitles": subtitle_items}


async def search_backfill(entry_id: int) -> dict[str, Any]:
    with connect() as conn:
        entry = conn.execute("SELECT * FROM entries WHERE id=?", (entry_id,)).fetchone()
    if not entry:
        raise ValueError("媒体条目不存在")
    row = _row_dict(entry)
    keyword = row.get("title_root") or row.get("title_cn") or row.get("display_title") or row.get("title_raw") or ""
    payload = DiscoverySearchPayload(
        keyword=keyword,
        media_type=row.get("media_type") or "anime",
        year=int(row.get("year") or 0),
        season=str(row.get("season_number") or ""),
    )
    result = await run_discovery_search(payload)
    result["entry_id"] = entry_id
    result["best_result_id"] = _best_result_for_entry(entry_id, result.get("items", []))
    return result


def _best_result_for_entry(entry_id: int, items: list[dict[str, Any]]) -> int:
    with connect() as conn:
        entry = conn.execute("SELECT * FROM entries WHERE id=?", (entry_id,)).fetchone()
    if not entry or not items:
        return 0
    title_key = clean_name(entry["title_root"] or entry["title_cn"] or entry["display_title"]).lower()
    bangumi_id = _text(entry["bangumi_id"])
    tmdb_id = _text(entry["tmdb_id"])
    best_id = 0
    best_score = -1
    for item in items:
        score = float(item.get("confidence") or 0)
        if bangumi_id and bangumi_id == _text(item.get("bangumi_id")):
            score += 10
        if tmdb_id and tmdb_id == _text(item.get("tmdb_id")):
            score += 8
        if title_key and title_key in clean_name(item.get("title") or "").lower():
            score += 2
        if score > best_score:
            best_score = score
            best_id = int(item.get("id") or 0)
    return best_id


def apply_backfill(entry_id: int, payload: BackfillApplyPayload) -> dict[str, Any]:
    result_id = int(payload.result_id or 0)
    if result_id <= 0 and payload.search_id > 0:
        result = discovery_results(payload.search_id)
        result_id = _best_result_for_entry(entry_id, result.get("items", []))
    if result_id <= 0:
        raise ValueError("缺少可应用的发现结果")
    with connect() as conn:
        entry = conn.execute("SELECT * FROM entries WHERE id=?", (entry_id,)).fetchone()
        if not entry:
            raise ValueError("媒体条目不存在")
        resource_query = "SELECT * FROM discovery_resources WHERE result_id=?"
        params: list[Any] = [result_id]
        if payload.resource_ids:
            placeholders = ",".join(["?"] * len(payload.resource_ids))
            resource_query += f" AND id IN ({placeholders})"
            params.extend([int(item) for item in payload.resource_ids])
        resources = conn.execute(resource_query + " ORDER BY episode_number ASC, id ASC", params).fetchall()
        preferred = _preferred_resource_metadata(conn, entry_id)
        picked = _pick_best_resources([_row_dict(row) for row in resources], preferred)
        ts = now()
        touched: list[int] = []
        skipped = 0
        for episode_number, resource in picked.items():
            if episode_number <= 0:
                skipped += 1
                continue
            existing = conn.execute(
                "SELECT * FROM episodes WHERE entry_id=? AND episode_number=? ORDER BY id DESC LIMIT 1",
                (entry_id, episode_number),
            ).fetchone()
            if existing and (int(existing["watchable"] or 0) or _text(existing["resource_ref"])):
                skipped += 1
                continue
            if existing:
                episode_id = int(existing["id"] or 0)
            else:
                conn.execute(
                    """
                    INSERT INTO episodes (series_id, entry_id, episode_number, title, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (entry_id, entry_id, episode_number, f"第{episode_number:02d}话", ts, ts),
                )
                episode_id = int(conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"])
            ref = resource.get("resource_ref") or ""
            conn.execute(
                """
                UPDATE episodes
                SET resource_ref=?, subtitle_ref=CASE WHEN ?='' THEN subtitle_ref ELSE ? END,
                    subtitle_group=?, resolution=?, language=?, subtitle_format=?,
                    source_title=?, updated_at=?
                WHERE id=?
                """,
                (
                    ref,
                    resource.get("subtitle_ref") or "",
                    resource.get("subtitle_ref") or "",
                    resource.get("subtitle_group") or "",
                    resource.get("resolution") or "",
                    resource.get("language") or "",
                    resource.get("subtitle_format") or "",
                    resource.get("source_title") or "",
                    ts,
                    episode_id,
                ),
            )
            conn.execute("UPDATE episode_resources SET selected=0 WHERE entry_id=? AND episode_number=?", (entry_id, episode_number))
            conn.execute(
                """
                INSERT INTO episode_resources
                  (entry_id, episode_id, episode_number, source_type, source_ref, title, subtitle_group,
                   resolution, language, subtitle_format, torrent_url, magnet, selected, downloaded, status, created_at, updated_at)
                VALUES (?, ?, ?, 'discovery', ?, ?, ?, ?, ?, ?, ?, ?, 1, 0, 'available', ?, ?)
                ON CONFLICT(entry_id, episode_number, source_type, source_ref) DO UPDATE SET
                  episode_id=excluded.episode_id,
                  title=excluded.title,
                  subtitle_group=excluded.subtitle_group,
                  resolution=excluded.resolution,
                  language=excluded.language,
                  subtitle_format=excluded.subtitle_format,
                  selected=1,
                  updated_at=excluded.updated_at
                """,
                (
                    entry_id,
                    episode_id,
                    episode_number,
                    ref,
                    resource.get("source_title") or "",
                    resource.get("subtitle_group") or "",
                    resource.get("resolution") or "",
                    resource.get("language") or "",
                    resource.get("subtitle_format") or "",
                    "" if ref.startswith("magnet:") else ref,
                    ref if ref.startswith("magnet:") else "",
                    ts,
                    ts,
                ),
            )
            touched.append(episode_id)
    queued = []
    if payload.auto_download:
        for episode_id in touched:
            result = queue_download_for_episode(episode_id)
            if result.get("queued"):
                queued.append({"episode_id": episode_id, **result})
        if queued:
            trigger_download_worker(delay=0)
    log("info", f"本季补全已应用: entry_id={entry_id} episodes={len(touched)} skipped={skipped} queued={len(queued)}")
    return {"applied": len(touched), "skipped": skipped, "queued": queued}


def _preferred_resource_metadata(conn, entry_id: int) -> dict[str, str]:
    row = conn.execute(
        """
        SELECT subtitle_group, subtitle_format, language, resolution
        FROM episodes
        WHERE entry_id=?
          AND (subtitle_group!='' OR subtitle_format!='' OR language!='' OR resolution!='')
        ORDER BY watchable DESC, episode_number ASC
        LIMIT 1
        """,
        (entry_id,),
    ).fetchone()
    return _row_dict(row)


def _pick_best_resources(resources: list[dict[str, Any]], preferred: dict[str, str]) -> dict[int, dict[str, Any]]:
    picked: dict[int, dict[str, Any]] = {}
    scores: dict[int, int] = {}
    for resource in resources:
        episode_number = int(resource.get("episode_number") or 0)
        if episode_number <= 0 or not resource.get("resource_ref"):
            continue
        score = 0
        if preferred.get("subtitle_group") and preferred["subtitle_group"] == resource.get("subtitle_group"):
            score += 50
        if preferred.get("subtitle_format") and preferred["subtitle_format"] == resource.get("subtitle_format"):
            score += 20
        if preferred.get("language") and preferred["language"] == resource.get("language"):
            score += 10
        if preferred.get("resolution") and preferred["resolution"] == resource.get("resolution"):
            score += 5
        if episode_number not in picked or score > scores.get(episode_number, -1):
            picked[episode_number] = resource
            scores[episode_number] = score
    return picked
