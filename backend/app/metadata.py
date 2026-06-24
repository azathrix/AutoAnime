from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any

import httpx

from .database import connect
from .db import log, merge_duplicate_series, now
from .library import local_library_root, render_episode_name, render_season_dir, render_series_dir
from .parser import clean_name
from .processing_cache import get_cached_json, set_cached_json


BANGUMI_API = "https://api.bgm.tv"
TMDB_API = "https://api.themoviedb.org/3"
USER_AGENT = "AniTrack/0.1 (private NAS media automation)"
BANGUMI_TIMEOUT = httpx.Timeout(15.0, connect=5.0)
BANGUMI_SUBJECT_CACHE_TTL = 30 * 24 * 60 * 60


async def fetch_bangumi_subject(subject_id: str, proxy: str = "") -> dict[str, Any]:
    cached = get_cached_json("bangumi_subject", subject_id)
    if isinstance(cached, dict) and cached:
        log("info", f"Bangumi 元数据缓存命中: subject_id={subject_id}")
        return cached
    async with httpx.AsyncClient(
        proxy=proxy or None,
        timeout=BANGUMI_TIMEOUT,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    ) as client:
        log("info", f"Bangumi 元数据请求: subject_id={subject_id}")
        resp = await client.get(f"{BANGUMI_API}/v0/subjects/{subject_id}")
        resp.raise_for_status()
        log("info", f"Bangumi 元数据响应: subject_id={subject_id} status={resp.status_code} bytes={len(resp.content)}")
        subject = resp.json()
        set_cached_json("bangumi_subject", subject_id, subject, ttl_seconds=BANGUMI_SUBJECT_CACHE_TTL)
        return subject


async def fetch_bangumi_metadata(subject_id: str, proxy: str = "") -> dict[str, Any]:
    subject = await fetch_bangumi_subject(subject_id, proxy)
    title_cn = subject_cn_name(subject) or subject.get("name") or ""
    images = subject.get("images") or {}
    return {
        "title_cn": title_cn,
        "poster_url": images.get("large") or images.get("common") or images.get("medium") or "",
        "summary": subject.get("summary") or "",
        "year": subject_year(subject),
        "month": subject_month(subject),
        "bangumi_score": subject_score(subject),
    }


async def search_bangumi(keyword: str, proxy: str = "") -> list[dict[str, Any]]:
    payload = {"keyword": keyword, "filter": {"type": [2]}}
    async with httpx.AsyncClient(
        proxy=proxy or None,
        timeout=BANGUMI_TIMEOUT,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    ) as client:
        resp = await client.post(f"{BANGUMI_API}/v0/search/subjects", json=payload)
        resp.raise_for_status()
        data = resp.json()
        return data.get("data", [])


async def search_tmdb(keyword: str, token: str, proxy: str = "") -> list[dict[str, Any]]:
    async with httpx.AsyncClient(
        proxy=proxy or None,
        timeout=BANGUMI_TIMEOUT,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json", "User-Agent": USER_AGENT},
    ) as client:
        resp = await client.get(
            f"{TMDB_API}/search/multi",
            params={"query": keyword, "language": "zh-CN", "include_adult": "false"},
        )
        resp.raise_for_status()
        data = resp.json()
        rows = data.get("results", [])[:20]
        keyword_map: dict[str, list[str]] = {}
        for row in rows[:10]:
            media_type = row.get("media_type") or ""
            item_id = str(row.get("id") or "")
            if media_type in {"movie", "tv"} and item_id:
                keyword_map[item_id] = await fetch_tmdb_keywords(client, media_type, item_id)
    items: list[dict[str, Any]] = []
    for row in rows:
        media_type = row.get("media_type") or ""
        if media_type not in {"movie", "tv"}:
            continue
        date_value = row.get("release_date") or row.get("first_air_date") or ""
        year = int(date_value[:4]) if re.match(r"\d{4}", date_value) else 0
        month_match = re.match(r"\d{4}-(\d{1,2})", date_value)
        month = int(month_match.group(1)) if month_match else 0
        language = row.get("original_language") or ""
        region = {"ja": "jp", "zh": "cn", "ko": "kr", "en": "us"}.get(language, "")
        poster_path = row.get("poster_path") or ""
        items.append(
            {
                "provider": "tmdb",
                "id": str(row.get("id") or ""),
                "title": row.get("title") or row.get("name") or row.get("original_title") or row.get("original_name") or "",
                "original_title": row.get("original_title") or row.get("original_name") or "",
                "media_type": media_type,
                "year": year,
                "month": month,
                "region": region,
                "poster_url": f"https://image.tmdb.org/t/p/w500{poster_path}" if poster_path else "",
                "summary": row.get("overview") or "",
                "tags_json": json.dumps(keyword_map.get(str(row.get("id") or ""), []), ensure_ascii=False),
                "tmdb_score": float(row.get("vote_average") or 0),
            }
        )
    return items


async def fetch_tmdb_metadata(item_id: str, media_type: str, token: str, proxy: str = "") -> dict[str, Any]:
    item_id = str(item_id or "").strip()
    normalized_type = "movie" if str(media_type or "").lower() == "movie" else "tv"
    if not item_id or not token:
        return {}
    async with httpx.AsyncClient(
        proxy=proxy or None,
        timeout=BANGUMI_TIMEOUT,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json", "User-Agent": USER_AGENT},
    ) as client:
        resp = await client.get(f"{TMDB_API}/{normalized_type}/{item_id}", params={"language": "zh-CN"})
        resp.raise_for_status()
        row = resp.json()
        keywords = await fetch_tmdb_keywords(client, normalized_type, item_id)
    date_value = row.get("release_date") or row.get("first_air_date") or ""
    year = int(date_value[:4]) if re.match(r"\d{4}", date_value) else 0
    month_match = re.match(r"\d{4}-(\d{1,2})", date_value)
    month = int(month_match.group(1)) if month_match else 0
    language = row.get("original_language") or ""
    region = {"ja": "jp", "zh": "cn", "ko": "kr", "en": "us"}.get(language, "")
    poster_path = row.get("poster_path") or ""
    return {
        "title_cn": row.get("title") or row.get("name") or "",
        "title_raw": row.get("original_title") or row.get("original_name") or "",
        "poster_url": f"https://image.tmdb.org/t/p/w500{poster_path}" if poster_path else "",
        "summary": row.get("overview") or "",
        "year": year,
        "month": month,
        "region": region,
        "tags_json": json.dumps(keywords, ensure_ascii=False),
        "tmdb_score": float(row.get("vote_average") or 0),
    }


async def fetch_tmdb_keywords(client: httpx.AsyncClient, media_type: str, item_id: str) -> list[str]:
    try:
        resp = await client.get(f"{TMDB_API}/{media_type}/{item_id}/keywords")
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return []
    rows = data.get("results") if media_type == "tv" else data.get("keywords")
    names = []
    for row in rows or []:
        name = str(row.get("name") or "").strip()
        if name:
            names.append(name)
    return list(dict.fromkeys(names))[:16]


def subject_cn_name(subject: dict[str, Any]) -> str:
    infobox = subject.get("infobox") or []
    for item in infobox:
        if item.get("key") in {"中文名", "简体中文名"}:
            value = item.get("value")
            if isinstance(value, str) and value.strip():
                return value.strip()
    return subject.get("name_cn") or subject.get("name") or ""


def subject_year(subject: dict[str, Any]) -> int:
    date = subject.get("date") or ""
    match = re.match(r"(\d{4})", date)
    return int(match.group(1)) if match else 0


def subject_month(subject: dict[str, Any]) -> int:
    date = subject.get("date") or ""
    match = re.match(r"\d{4}-(\d{1,2})", date)
    if not match:
        return 0
    value = int(match.group(1))
    return value if 1 <= value <= 12 else 0


def subject_score(subject: dict[str, Any]) -> float:
    rating = subject.get("rating") or {}
    try:
        return float(rating.get("score") or 0)
    except (TypeError, ValueError):
        return 0


def chinese_summary(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    lines = [line.strip() for line in re.split(r"[\r\n]+", text) if line.strip()]
    chinese_lines = [line for line in lines if re.search(r"[\u4e00-\u9fff]", line)]
    if chinese_lines:
        return "\n".join(chinese_lines)
    return text


def subject_tags_json(subject: dict[str, Any], limit: int = 16) -> str:
    tags = []
    for item in subject.get("tags") or []:
        name = str(item.get("name") or "").strip()
        if name:
            tags.append(name)
    return json.dumps(list(dict.fromkeys(tags))[:limit], ensure_ascii=False)


async def refresh_entry_metadata(entry_id: int, proxy: str = "") -> None:
    with connect() as conn:
        entry = conn.execute("SELECT * FROM entries WHERE id=?", (entry_id,)).fetchone()
        series = None
        if entry:
            series = conn.execute(
                "SELECT * FROM series WHERE bangumi_id=? ORDER BY id ASC LIMIT 1",
                (entry["bangumi_id"],),
            ).fetchone()
    if not entry:
        return

    bangumi_id = str(entry["bangumi_id"] or "").strip()
    if not bangumi_id:
        log("warn", f"跳过 Bangumi 元数据: {entry['display_title']} - 缺少 Bangumi ID")
        return
    try:
        subject = await fetch_bangumi_subject(bangumi_id, proxy)
    except Exception as exc:
        log("error", f"Bangumi 元数据失败: {entry['display_title']} - {exc}")
        return

    title_cn = subject_cn_name(subject) or entry["title_cn"]
    images = subject.get("images") or {}
    poster = images.get("large") or images.get("common") or images.get("medium") or ""
    summary = chinese_summary(subject.get("summary") or "")
    tags_json = subject_tags_json(subject)
    year = subject_year(subject) or entry["year"]
    month = subject_month(subject) or entry["month"]
    bangumi_score = subject_score(subject)
    with connect() as conn:
        conn.execute(
            """
            UPDATE entries
            SET title_cn=CASE WHEN title_cn='' THEN ? ELSE title_cn END,
                display_title=CASE WHEN display_title='' THEN ? ELSE display_title END,
                bangumi_id=?,
                poster_url=CASE WHEN poster_url='' THEN ? ELSE poster_url END,
                summary=CASE WHEN summary='' THEN ? ELSE summary END,
                year=CASE WHEN year=0 THEN ? ELSE year END,
                month=CASE WHEN month=0 THEN ? ELSE month END,
                tags_json=CASE WHEN tags_json='[]' THEN ? ELSE tags_json END,
                bangumi_score=?,
                metadata_source='bangumi', updated_at=?
            WHERE id=?
            """,
            (title_cn, title_cn, bangumi_id, poster, summary, year, month, tags_json, bangumi_score, now(), entry_id),
        )
        if series:
            conn.execute(
                """
                UPDATE series
                SET title_cn=?, bangumi_id=?, poster_url=?, summary=?, year=?, month=?,
                    metadata_source='bangumi', updated_at=?
                WHERE id=?
                """,
                (title_cn, bangumi_id, poster, summary, year, month, now(), series["id"]),
            )
            merge_duplicate_series(conn)
    log("info", f"已刷新 Bangumi 元数据: {title_cn}")


def xml_text(value: str) -> str:
    return html.escape(value or "", quote=False)


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def generate_nfo_for_entry(entry_id: int, settings: dict[str, str]) -> None:
    with connect() as conn:
        entry = conn.execute("SELECT * FROM entries WHERE id=?", (entry_id,)).fetchone()
        episodes = conn.execute(
            "SELECT * FROM episodes WHERE entry_id=? ORDER BY episode_number ASC",
            (entry_id,),
        ).fetchall()
    if not entry:
        return

    entry_dict = dict(entry)
    output_root = settings.get("nfo_output_root") or local_library_root(entry_dict, settings)
    base = Path(output_root) / clean_name(render_series_dir(entry_dict, settings))
    tvshow = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<tvshow>
  <title>{xml_text(entry['title_cn'])}</title>
  <originaltitle>{xml_text(entry['title_raw'])}</originaltitle>
  <plot>{xml_text(entry['summary'])}</plot>
  <year>{entry['year'] or ''}</year>
  <uniqueid type="bangumi" default="true">{xml_text(entry['bangumi_id'])}</uniqueid>
  <uniqueid type="tmdb">{xml_text(entry['tmdb_id'])}</uniqueid>
</tvshow>
"""
    write_text(base / "tvshow.nfo", tvshow)

    season_dir = base / render_season_dir(int(entry["season_number"] or 1), settings)
    for ep in episodes:
        name = render_episode_name(entry_dict, ep["episode_number"], ep["title"], settings)
        nfo = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<episodedetails>
  <title>{xml_text(ep['title'] or f"第{ep['episode_number']:02d}话")}</title>
  <season>{entry['season_number'] or 1}</season>
  <episode>{ep['episode_number']}</episode>
  <showtitle>{xml_text(entry['title_cn'])}</showtitle>
  <aired>{xml_text(ep['air_date'])}</aired>
</episodedetails>
"""
        write_text(season_dir / f"{name}.nfo", nfo)

    with connect() as conn:
        conn.execute(
            "UPDATE entries SET nfo_status='generated', updated_at=? WHERE id=?",
            (now(), entry_id),
        )
    log("info", f"已生成 NFO: {entry['display_title'] or entry['title_cn']}")

