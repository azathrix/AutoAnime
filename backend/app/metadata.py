from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Any

import httpx

from .config import DATA_DIR
from .db import connect, log, merge_duplicate_series, now
from .library import render_episode_name, render_season_dir, render_series_dir, target_dir
from .parser import clean_name


BANGUMI_API = "https://api.bgm.tv"
USER_AGENT = "AutoAnime/0.1 (private NAS media automation)"


async def fetch_bangumi_subject(subject_id: str, proxy: str = "") -> dict[str, Any]:
    async with httpx.AsyncClient(
        proxy=proxy or None,
        timeout=30,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    ) as client:
        resp = await client.get(f"{BANGUMI_API}/v0/subjects/{subject_id}")
        resp.raise_for_status()
        return resp.json()


async def search_bangumi(keyword: str, proxy: str = "") -> list[dict[str, Any]]:
    payload = {"keyword": keyword, "filter": {"type": [2]}}
    async with httpx.AsyncClient(
        proxy=proxy or None,
        timeout=30,
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


async def refresh_series_metadata(series_id: int, proxy: str = "") -> None:
    with connect() as conn:
        series = conn.execute("SELECT * FROM series WHERE id=?", (series_id,)).fetchone()
    if not series:
        return

    bangumi_id = series["bangumi_id"]
    try:
        if not bangumi_id:
            log("warn", f"跳过 Bangumi 元数据: {series['title_cn']} - RSS 未提供 Bangumi ID，需人工确认")
            return
        subject = await fetch_bangumi_subject(bangumi_id, proxy)
    except Exception as exc:
        log("error", f"Bangumi 元数据失败: {series['title_cn']} - {exc}")
        return

    title_cn = subject_cn_name(subject) or series["title_cn"]
    images = subject.get("images") or {}
    poster = images.get("large") or images.get("common") or images.get("medium") or ""
    summary = subject.get("summary") or ""
    year = subject_year(subject) or series["year"]
    with connect() as conn:
        conn.execute(
            """
            UPDATE series
            SET title_cn=?, bangumi_id=?, poster_url=?, summary=?, year=?,
                metadata_source='bangumi', updated_at=?
            WHERE id=?
            """,
            (title_cn, bangumi_id, poster, summary, year, now(), series_id),
        )
        merge_duplicate_series(conn)
    log("info", f"已刷新 Bangumi 元数据: {title_cn}")


def xml_text(value: str) -> str:
    return html.escape(value or "", quote=False)


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def generate_nfo_for_series(series_id: int, settings: dict[str, str]) -> None:
    with connect() as conn:
        series = conn.execute("SELECT * FROM series WHERE id=?", (series_id,)).fetchone()
        episodes = conn.execute(
            "SELECT * FROM episodes WHERE series_id=? ORDER BY episode_number ASC",
            (series_id,),
        ).fetchall()
    if not series:
        return

    series_dict = dict(series)
    output_root = settings.get("nfo_output_root") or str(DATA_DIR / "nfo")
    base = Path(output_root) / clean_name(render_series_dir(series_dict, settings))
    tvshow = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<tvshow>
  <title>{xml_text(series['title_cn'])}</title>
  <originaltitle>{xml_text(series['title_raw'])}</originaltitle>
  <plot>{xml_text(series['summary'])}</plot>
  <year>{series['year'] or ''}</year>
  <uniqueid type="bangumi" default="true">{xml_text(series['bangumi_id'])}</uniqueid>
  <uniqueid type="tmdb">{xml_text(series['tmdb_id'])}</uniqueid>
</tvshow>
"""
    write_text(base / "tvshow.nfo", tvshow)

    season_dir = base / render_season_dir(int(series["season_number"] or 1), settings)
    for ep in episodes:
        name = render_episode_name(series_dict, ep["episode_number"], ep["title"], settings)
        nfo = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<episodedetails>
  <title>{xml_text(ep['title'] or f"第{ep['episode_number']:02d}话")}</title>
  <season>{series['season_number'] or 1}</season>
  <episode>{ep['episode_number']}</episode>
  <showtitle>{xml_text(series['title_cn'])}</showtitle>
  <aired>{xml_text(ep['air_date'])}</aired>
</episodedetails>
"""
        write_text(season_dir / f"{name}.nfo", nfo)

    with connect() as conn:
        conn.execute(
            "UPDATE series SET nfo_status='generated', updated_at=? WHERE id=?",
            (now(), series_id),
        )
    log("info", f"已生成 NFO: {series['title_cn']}")
