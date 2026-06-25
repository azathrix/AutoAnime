from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any

import httpx

from .bangumi_config import generate_bangumi_ini, setting_enabled
from .database import connect
from .db import get_settings, log, merge_duplicate_series, now
from .library import parse_entry_labels
from .nfo_service import generate_jellyfin_nfo_for_entry
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
        "title_raw": subject.get("name") or title_cn,
        "poster_url": images.get("large") or images.get("common") or images.get("medium") or "",
        "summary": subject.get("summary") or "",
        "year": subject_year(subject),
        "month": subject_month(subject),
        "tags_json": subject_tags_json(subject),
        "bangumi_score": subject_score(subject),
    }


async def fetch_bangumi_episodes(subject_id: str, proxy: str = "") -> list[dict[str, Any]]:
    subject_id = str(subject_id or "").strip()
    if not subject_id:
        return []
    rows: list[dict[str, Any]] = []
    offset = 0
    limit = 100
    async with httpx.AsyncClient(
        proxy=proxy or None,
        timeout=BANGUMI_TIMEOUT,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    ) as client:
        while True:
            resp = await client.get(
                f"{BANGUMI_API}/v0/episodes",
                params={"subject_id": subject_id, "type": 0, "limit": limit, "offset": offset},
            )
            resp.raise_for_status()
            data = resp.json()
            batch = data.get("data") if isinstance(data, dict) else []
            if not isinstance(batch, list) or not batch:
                break
            rows.extend([item for item in batch if isinstance(item, dict)])
            total = int(data.get("total") or len(rows)) if isinstance(data, dict) else len(rows)
            offset += len(batch)
            if offset >= total or len(batch) < limit:
                break

    return rows


def _number_value(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def bangumi_episode_offset(episodes: list[dict[str, Any]]) -> int:
    offsets: list[int] = []
    for item in episodes or []:
        sort_number = _number_value(item.get("sort"))
        ep_number = _number_value(item.get("ep"))
        if sort_number <= 0 or ep_number <= 0 or sort_number == ep_number:
            continue
        smaller = min(sort_number, ep_number)
        larger = max(sort_number, ep_number)
        if smaller > 36:
            continue
        offset = larger - smaller
        if offset > 0:
            offsets.append(offset)
    if not offsets:
        return 0
    return int(Counter(offsets).most_common(1)[0][0])


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
    def probably_chinese(candidate: str) -> bool:
        cjk_count = len(re.findall(r"[\u4e00-\u9fff]", candidate))
        kana_count = len(re.findall(r"[\u3040-\u30ff]", candidate))
        return cjk_count > 0 and (kana_count == 0 or kana_count / max(1, cjk_count + kana_count) <= 0.08)

    paragraphs = [part.strip() for part in re.split(r"(?:\r?\n){2,}", text) if part.strip()]
    for paragraph in paragraphs:
        if probably_chinese(paragraph):
            return paragraph
    lines = [line.strip() for line in re.split(r"[\r\n]+", text) if line.strip()]
    chinese_lines = [line for line in lines if probably_chinese(line)]
    if chinese_lines:
        return "\n".join(chinese_lines)
    mixed_chinese_lines = [line for line in lines if re.search(r"[\u4e00-\u9fff]", line)]
    if mixed_chinese_lines:
        return "\n".join(mixed_chinese_lines)
    return text


def subject_tags_json(subject: dict[str, Any], limit: int = 16) -> str:
    tags = []
    for item in subject.get("tags") or []:
        name = str(item.get("name") or "").strip()
        if name:
            tags.append(name)
    return json.dumps(list(dict.fromkeys(tags))[:limit], ensure_ascii=False)


def metadata_root_labels(title: str) -> dict[str, str | int]:
    labels = parse_entry_labels(title)
    if not str(labels.get("title_root") or "").strip():
        labels["title_root"] = clean_name(title or "Unknown")
    return labels


async def apply_bangumi_metadata(entry_id: int, bangumi_id: str, proxy: str = "", *, force: bool = True) -> bool:
    bangumi_id = str(bangumi_id or "").strip()
    if not bangumi_id:
        return False
    with connect() as conn:
        entry = conn.execute("SELECT * FROM entries WHERE id=?", (entry_id,)).fetchone()
        series = None
        if entry:
            series = conn.execute(
                "SELECT * FROM series WHERE bangumi_id=? ORDER BY id ASC LIMIT 1",
                (bangumi_id,),
            ).fetchone()
    if not entry:
        return False
    try:
        subject = await fetch_bangumi_subject(bangumi_id, proxy)
    except Exception as exc:
        log("error", f"Bangumi 元数据失败: {entry['display_title']} - {exc}")
        return False

    title_cn = subject_cn_name(subject) or entry["title_cn"]
    title_raw = subject.get("name") or entry["title_raw"] or title_cn
    labels = metadata_root_labels(title_cn)
    title_root = str(labels.get("title_root") or title_cn)
    images = subject.get("images") or {}
    poster = images.get("large") or images.get("common") or images.get("medium") or ""
    summary = chinese_summary(subject.get("summary") or "")
    tags_json = subject_tags_json(subject)
    year = subject_year(subject) or entry["year"]
    month = subject_month(subject) or entry["month"]
    bangumi_score = subject_score(subject)
    try:
        episode_offset = bangumi_episode_offset(await fetch_bangumi_episodes(bangumi_id, proxy))
    except Exception as exc:
        episode_offset = int(entry["episode_offset"] or 0) if "episode_offset" in entry.keys() else 0
        log("warn", f"Bangumi 集数 offset 获取失败: entry_id={entry_id} bangumi_id={bangumi_id} error={exc}")
    with connect() as conn:
        if force:
            conn.execute(
                """
                UPDATE entries
                SET title_cn=?,
                    display_title=?,
                    title_root=?,
                    title_raw=?,
                    entry_kind=?,
                    season_label=?,
                    arc_label=?,
                    part_label=?,
                    special_label=?,
                    season_number=?,
                    bangumi_id=?,
                    poster_url=?,
                    summary=?,
                    year=?,
                    month=?,
                    tags_json=?,
                    bangumi_score=?,
                    episode_offset=?,
                    metadata_source='bangumi',
                    metadata_provider='bangumi',
                    updated_at=?
                WHERE id=?
                """,
                (
                    title_cn,
                    title_cn,
                    title_root,
                    title_raw,
                    str(labels.get("entry_kind") or "season"),
                    str(labels.get("season_label") or ""),
                    str(labels.get("arc_label") or ""),
                    str(labels.get("part_label") or ""),
                    str(labels.get("special_label") or ""),
                    int(labels.get("season_number") or entry["season_number"] or 1),
                    bangumi_id,
                    poster,
                    summary,
                    year,
                    month,
                    tags_json,
                    bangumi_score,
                    episode_offset,
                    now(),
                    entry_id,
                ),
            )
        else:
            conn.execute(
                """
                UPDATE entries
                SET bangumi_id=?,
                    bangumi_score=?,
                    episode_offset=CASE WHEN ?!=0 THEN ? ELSE episode_offset END,
                    poster_url=CASE WHEN poster_url='' THEN ? ELSE poster_url END,
                    summary=CASE WHEN summary='' THEN ? ELSE summary END,
                    tags_json=CASE WHEN tags_json='[]' THEN ? ELSE tags_json END,
                    updated_at=?
                WHERE id=?
                """,
                (bangumi_id, bangumi_score, episode_offset, episode_offset, poster, summary, tags_json, now(), entry_id),
            )
        if int(entry["work_id"] or 0) > 0 and force:
            conn.execute(
                """
                UPDATE works
                SET title_root=?,
                    title_root_raw=?,
                    bangumi_id=CASE WHEN ?='' THEN bangumi_id ELSE ? END,
                    metadata_source='bangumi',
                    updated_at=?
                WHERE id=?
                """,
                (title_root, title_raw, bangumi_id, bangumi_id, now(), int(entry["work_id"] or 0)),
            )
        if series:
            conn.execute(
                """
                UPDATE series
                SET title_raw=?,
                    title_cn=?,
                    bangumi_id=?,
                    poster_url=?,
                    summary=?,
                    year=?,
                    month=?,
                    metadata_source='bangumi',
                    updated_at=?
                WHERE id=?
                """,
                (title_raw, title_cn, bangumi_id, poster, summary, year, month, now(), series["id"]),
            )
            merge_duplicate_series(conn)
    settings = get_settings()
    if setting_enabled(settings.get("generate_bangumi_ini", "false")):
        generate_bangumi_ini(entry_id, settings)
    if setting_enabled(settings.get("auto_generate_nfo", "false")):
        generate_jellyfin_nfo_for_entry(entry_id, settings)
    log("info", f"已刷新 Bangumi 元数据: {title_cn}")
    return True


async def apply_tmdb_metadata(
    entry_id: int,
    tmdb_id: str,
    media_type: str,
    token: str,
    proxy: str = "",
    *,
    force: bool = True,
) -> bool:
    tmdb_id = str(tmdb_id or "").strip()
    if not tmdb_id or not token:
        return False
    with connect() as conn:
        entry = conn.execute("SELECT * FROM entries WHERE id=?", (entry_id,)).fetchone()
    if not entry:
        return False
    try:
        metadata = await fetch_tmdb_metadata(tmdb_id, media_type, token, proxy)
    except Exception as exc:
        log("error", f"TMDB 元数据失败: entry_id={entry_id} tmdb_id={tmdb_id} - {exc}")
        return False
    title_cn = metadata.get("title_cn") or entry["title_cn"] or entry["display_title"]
    title_raw = metadata.get("title_raw") or entry["title_raw"] or title_cn
    labels = metadata_root_labels(title_cn)
    title_root = str(labels.get("title_root") or title_cn)
    year = int(metadata.get("year") or entry["year"] or 0)
    month = int(metadata.get("month") or entry["month"] or 0)
    with connect() as conn:
        if force:
            conn.execute(
                """
                UPDATE entries
                SET title_cn=?,
                    display_title=?,
                    title_root=?,
                    title_raw=?,
                    tmdb_id=?,
                    poster_url=?,
                    summary=?,
                    year=?,
                    month=?,
                    region=?,
                    tags_json=?,
                    tmdb_score=?,
                    metadata_source='tmdb',
                    metadata_provider='tmdb',
                    updated_at=?
                WHERE id=?
                """,
                (
                    title_cn,
                    title_cn,
                    title_root,
                    title_raw,
                    tmdb_id,
                    metadata.get("poster_url", ""),
                    metadata.get("summary", ""),
                    year,
                    month,
                    metadata.get("region", ""),
                    metadata.get("tags_json", "[]"),
                    float(metadata.get("tmdb_score") or 0),
                    now(),
                    entry_id,
                ),
            )
        else:
            conn.execute(
                """
                UPDATE entries
                SET tmdb_id=?,
                    tmdb_score=?,
                    poster_url=CASE WHEN poster_url='' THEN ? ELSE poster_url END,
                    summary=CASE WHEN summary='' THEN ? ELSE summary END,
                    tags_json=CASE WHEN tags_json='[]' THEN ? ELSE tags_json END,
                    updated_at=?
                WHERE id=?
                """,
                (
                    tmdb_id,
                    float(metadata.get("tmdb_score") or 0),
                    metadata.get("poster_url", ""),
                    metadata.get("summary", ""),
                    metadata.get("tags_json", "[]"),
                    now(),
                    entry_id,
                ),
            )
        if int(entry["work_id"] or 0) > 0 and force:
            conn.execute(
                """
                UPDATE works
                SET title_root=?,
                    title_root_raw=?,
                    metadata_source='tmdb',
                    updated_at=?
                WHERE id=?
                """,
                (title_root, title_raw, now(), int(entry["work_id"] or 0)),
            )
    settings = get_settings()
    if setting_enabled(settings.get("auto_generate_nfo", "false")):
        generate_jellyfin_nfo_for_entry(entry_id, settings)
    log("info", f"已刷新 TMDB 元数据: {title_cn}")
    return True


async def refresh_entry_metadata_by_ids(
    entry_id: int,
    media_type: str = "",
    *,
    bangumi_id: str = "",
    tmdb_id: str = "",
    tmdb_token: str = "",
    proxy: str = "",
) -> list[str]:
    with connect() as conn:
        entry = conn.execute("SELECT id, media_type, bangumi_id, tmdb_id FROM entries WHERE id=?", (entry_id,)).fetchone()
        if not entry:
            return []
        if bangumi_id or tmdb_id:
            conn.execute(
                """
                UPDATE entries
                SET bangumi_id=CASE WHEN ?='' THEN bangumi_id ELSE ? END,
                    tmdb_id=CASE WHEN ?='' THEN tmdb_id ELSE ? END,
                    updated_at=?
                WHERE id=?
                """,
                (bangumi_id, bangumi_id, tmdb_id, tmdb_id, now(), entry_id),
            )
    normalized_type = str(media_type or entry["media_type"] or "anime").strip().lower()
    current_bangumi_id = str(bangumi_id or entry["bangumi_id"] or "").strip()
    current_tmdb_id = str(tmdb_id or entry["tmdb_id"] or "").strip()
    providers: list[str] = []

    if normalized_type == "anime":
        if current_bangumi_id and await apply_bangumi_metadata(entry_id, current_bangumi_id, proxy, force=True):
            providers.append("bangumi")
        if current_tmdb_id and await apply_tmdb_metadata(
            entry_id,
            current_tmdb_id,
            normalized_type,
            tmdb_token,
            proxy,
            force=not providers,
        ):
            providers.append("tmdb")
    else:
        if current_tmdb_id and await apply_tmdb_metadata(
            entry_id,
            current_tmdb_id,
            normalized_type,
            tmdb_token,
            proxy,
            force=True,
        ):
            providers.append("tmdb")
        if current_bangumi_id and await apply_bangumi_metadata(entry_id, current_bangumi_id, proxy, force=not providers):
            providers.append("bangumi")
    return providers


async def refresh_entry_metadata(entry_id: int, proxy: str = "") -> None:
    with connect() as conn:
        entry = conn.execute("SELECT bangumi_id, display_title FROM entries WHERE id=?", (entry_id,)).fetchone()
    if not entry:
        return
    bangumi_id = str(entry["bangumi_id"] or "").strip()
    if not bangumi_id:
        log("warn", f"跳过 Bangumi 元数据: {entry['display_title']} - 缺少 Bangumi ID")
        return
    await apply_bangumi_metadata(entry_id, bangumi_id, proxy, force=True)


async def refresh_all_metadata() -> dict[str, Any]:
    settings = get_settings()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT id, media_type, bangumi_id, tmdb_id, display_title
            FROM entries
            WHERE COALESCE(hidden, 0)=0
              AND (bangumi_id!='' OR tmdb_id!='')
            ORDER BY id ASC
            """
        ).fetchall()
    refreshed = 0
    skipped = 0
    failed = 0
    for row in rows:
        try:
            providers = await refresh_entry_metadata_by_ids(
                int(row["id"] or 0),
                str(row["media_type"] or "anime"),
                tmdb_token=settings.get("tmdb_token", "").strip(),
                proxy=settings.get("rss_proxy", ""),
            )
            if providers:
                refreshed += 1
            else:
                skipped += 1
        except Exception as exc:
            failed += 1
            log("error", f"刷新全部元数据失败: entry_id={row['id']} title={row['display_title']} error={exc}")
    message = f"刷新全部元数据完成: 成功 {refreshed} 个，跳过 {skipped} 个，失败 {failed} 个"
    log("info", message)
    return {"message": message, "refreshed": refreshed, "skipped": skipped, "failed": failed}


def generate_nfo_for_entry(entry_id: int, settings: dict[str, str]) -> None:
    generate_jellyfin_nfo_for_entry(entry_id, settings)

