from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Any

import httpx

from .config import DATA_DIR
from .database import connect
from .db import log, merge_duplicate_series, now
from .library import render_episode_name, render_season_dir, render_series_dir, target_dir
from .parser import clean_name


BANGUMI_API = "https://api.bgm.tv"
USER_AGENT = "AutoAnime/0.1 (private NAS media automation)"
BANGUMI_TIMEOUT = httpx.Timeout(15.0, connect=5.0)


async def fetch_bangumi_subject(subject_id: str, proxy: str = "") -> dict[str, Any]:
    async with httpx.AsyncClient(
        proxy=proxy or None,
        timeout=BANGUMI_TIMEOUT,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    ) as client:
        log("info", f"Bangumi 元数据请求: subject_id={subject_id}")
        resp = await client.get(f"{BANGUMI_API}/v0/subjects/{subject_id}")
        resp.raise_for_status()
        log("info", f"Bangumi 元数据响应: subject_id={subject_id} status={resp.status_code} bytes={len(resp.content)}")
        return resp.json()


async def fetch_bangumi_metadata(subject_id: str, proxy: str = "") -> dict[str, Any]:
    subject = await fetch_bangumi_subject(subject_id, proxy)
    title_cn = subject_cn_name(subject) or subject.get("name") or ""
    images = subject.get("images") or {}
    return {
        "title_cn": title_cn,
        "poster_url": images.get("large") or images.get("common") or images.get("medium") or "",
        "summary": subject.get("summary") or "",
        "year": subject_year(subject),
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

    bangumi_id = entry["bangumi_id"]
    try:
        if not bangumi_id:
            log("warn", f"跳过 Bangumi 元数据: {entry['display_title']} - 缺少 Bangumi ID")
            return
        subject = await fetch_bangumi_subject(bangumi_id, proxy)
    except Exception as exc:
        log("error", f"Bangumi 元数据失败: {entry['display_title']} - {exc}")
        return

    title_cn = subject_cn_name(subject) or entry["title_cn"]
    images = subject.get("images") or {}
    poster = images.get("large") or images.get("common") or images.get("medium") or ""
    summary = subject.get("summary") or ""
    year = subject_year(subject) or entry["year"]
    with connect() as conn:
        conn.execute(
            """
            UPDATE entries
            SET title_cn=?, display_title=CASE WHEN display_title='' THEN ? ELSE display_title END,
                bangumi_id=?, poster_url=?, summary=?, year=?,
                metadata_source='bangumi', updated_at=?
            WHERE id=?
            """,
            (title_cn, title_cn, bangumi_id, poster, summary, year, now(), entry_id),
        )
        if series:
            conn.execute(
                """
                UPDATE series
                SET title_cn=?, bangumi_id=?, poster_url=?, summary=?, year=?,
                    metadata_source='bangumi', updated_at=?
                WHERE id=?
                """,
                (title_cn, bangumi_id, poster, summary, year, now(), series["id"]),
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
    output_root = settings.get("nfo_output_root") or str(DATA_DIR / "nfo")
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

