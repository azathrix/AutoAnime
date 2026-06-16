from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass
class ParsedRelease:
    guid: str
    title: str
    series_title: str
    episode_number: int
    subtitle_group: str
    resolution: str
    language: str
    bangumi_id: str
    year: int
    torrent_url: str
    magnet: str
    page_url: str
    mikan_bangumi_id: str
    published_at: str


def split_lines(value: str) -> list[str]:
    return [x.strip() for x in re.split(r"[\n,，]", value or "") if x.strip()]


def clean_name(value: str) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", value or "")
    value = re.sub(r"\s+", " ", value).strip()
    return value[:140] or "Unknown"


_TRADITIONAL_SIMPLIFIED = str.maketrans(
    {
        "異": "异",
        "轉": "转",
        "為": "为",
        "麼": "么",
        "麽": "么",
        "話": "话",
        "與": "与",
        "國": "国",
        "學": "学",
        "會": "会",
        "體": "体",
        "後": "后",
        "裏": "里",
        "裡": "里",
        "臺": "台",
        "台": "台",
        "姬": "姬",
        "願": "愿",
        "龍": "龙",
        "魔": "魔",
        "戰": "战",
        "樂": "乐",
        "戀": "恋",
        "愛": "爱",
        "櫻": "樱",
        "聖": "圣",
        "劍": "剑",
        "傳": "传",
        "師": "师",
        "聲": "声",
        "無": "无",
        "雙": "双",
        "獸": "兽",
        "靈": "灵",
        "術": "术",
        "門": "门",
        "風": "风",
        "雲": "云",
        "夢": "梦",
        "鄉": "乡",
        "萬": "万",
        "壞": "坏",
        "開": "开",
        "關": "关",
        "輕": "轻",
        "續": "续",
        "邊": "边",
        "隻": "只",
        "殺": "杀",
        "廣": "广",
        "亞": "亚",
        "羅": "罗",
        "貝": "贝",
        "魯": "鲁",
        "蘭": "兰",
    }
)


def normalize_title_key(value: str) -> str:
    value = (value or "").translate(_TRADITIONAL_SIMPLIFIED)
    value = value.lower()
    value = re.sub(r"^\s*[\[【][^\]】]+[\]】]\s*", "", value)
    value = re.sub(r"[\[【(（][^\]】)）]*(1080p|720p|2160p|4k|8k|繁体|繁體|简体|簡體|chs|cht|big5|gb|hevc|avc|x26[45]|aac|webrip|web-dl|baha|cr)[^\]】)）]*[\]】)）]", " ", value, flags=re.I)
    value = re.sub(r"(第\s*)?\d{1,3}\s*[话話集].*$", " ", value)
    value = re.sub(r"\s-\s\d{1,3}.*$", " ", value)
    value = re.sub(r"(season|s)\s*\d+", " ", value, flags=re.I)
    value = re.sub(r"[\s·・,，.。:：;；'\"“”‘’!?！？/\\|_\-~～]+", "", value)
    return value[:120]


def fingerprint(title: str, bangumi_id: str = "") -> str:
    if bangumi_id:
        return f"bangumi:{bangumi_id}"
    return "title:" + normalize_title_key(title)


def parse_group(title: str) -> str:
    match = re.match(r"^\s*[\[【]([^\]】]+)[\]】]", title)
    return match.group(1).strip() if match else ""


def parse_resolution(title: str) -> str:
    match = re.search(r"(2160p|1080p|720p|4k|8k|1080|720)", title, re.I)
    return match.group(1) if match else ""


def parse_language(title: str) -> str:
    value = title.lower()
    if re.search(r"(简繁|簡繁|简体[&+／/ ]*繁体|簡體[&+／/ ]*繁體|chs[&+／/ ]*cht)", value, re.I):
        return "简繁"
    if re.search(r"(简日|簡日|简体[&+／/ ]*日[语語文]?|簡體[&+／/ ]*日[语語文]?|chs[&+／/ ]*(jp|jpn))", value, re.I):
        return "简日"
    if re.search(r"(繁日|繁體?[&+／/ ]*日[语語文]?|cht[&+／/ ]*(jp|jpn))", value, re.I):
        return "繁日"
    if re.search(r"(简英|簡英|简体[&+／/ ]*英[语語文]?|簡體[&+／/ ]*英[语語文]?|chs[&+／/ ]*eng?)", value, re.I):
        return "简英"
    if re.search(r"(繁英|繁體?[&+／/ ]*英[语語文]?|cht[&+／/ ]*eng?)", value, re.I):
        return "繁英"
    if re.search(r"(简体|簡體|简中|簡中|chs|gb|gb2312|sc)", value, re.I):
        return "简体"
    if re.search(r"(繁体|繁體|繁中|繁中|cht|big5|tc)", value, re.I):
        return "繁体"
    if re.search(r"(日语|日語|日文|japanese|jpn|jp)", value, re.I):
        return "日语"
    if re.search(r"(中字|中文|chinese)", value, re.I):
        return "中文"
    return ""


def parse_episode(title: str) -> int:
    patterns = [
        r"S\d{1,2}E(\d{1,3})",
        r"(?:第|EP|E|Episode|#)\s*(\d{1,3})(?:[话話集])?",
        r"\s-\s(\d{1,3})(?:\s|v\d|\[|$)",
        r"\[(\d{1,3})\]",
    ]
    for pattern in patterns:
        match = re.search(pattern, title, re.I)
        if match:
            return int(match.group(1))
    return 0


def parse_year(title: str, published_at: str = "") -> int:
    match = re.search(r"\b(20\d{2})\b", title)
    if match:
        return int(match.group(1))
    match = re.search(r"\b(20\d{2})\b", published_at)
    return int(match.group(1)) if match else 0


def entry_value(entry: Any, candidates: list[str]) -> str:
    for key in candidates:
        value = ""
        if isinstance(entry, dict):
            value = entry.get(key, "")
        if not value:
            value = getattr(entry, key, "")
        if value:
            return str(value)
    if isinstance(entry, dict):
        for key, value in entry.items():
            normalized = re.sub(r"[^a-z0-9]", "", str(key).lower())
            if normalized.endswith("bangumiid") and value:
                return str(value)
    return ""


def parse_mikan_bangumi_id(entry: Any, link: str) -> str:
    rss_value = entry_value(
        entry,
        [
            "mikan_bangumiid",
            "mikan_bangumiId",
            "mikan_bangumi_id",
            "bangumiid",
            "bangumiId",
            "bangumi_id",
        ],
    )
    match = re.search(r"\d+", rss_value)
    if match:
        return match.group(0)
    match = re.search(r"/Home/Bangumi/(\d+)", link, re.I)
    if match:
        return match.group(1)
    match = re.search(r"[?&](?:bangumiId|bangumi_id)=(\d+)", link, re.I)
    return match.group(1) if match else ""


def parse_bangumi_id(entry: Any, link: str, title: str) -> str:
    explicit_value = entry_value(entry, ["bgm_subject_id", "bangumi_subject_id", "subject_id"])
    if re.fullmatch(r"\d+", explicit_value or ""):
        return explicit_value
    for value in [link, title, explicit_value]:
        match = re.search(r"(?:bgm\.tv|bangumi\.tv)/subject/(\d+)", value or "", re.I)
        if match:
            return match.group(1)
    return ""


def parse_series_title(title: str) -> str:
    value = re.sub(r"^\s*[\[【][^\]】]+[\]】]\s*", "", title)
    value = re.sub(r"\[[^\]]*(1080p|720p|2160p|繁体|简体|CHS|CHT|BIG5|GB|HEVC|AVC|x26[45]|AAC)[^\]]*\]", "", value, flags=re.I)
    value = re.sub(r"\s-\s\d{1,3}.*$", "", value)
    value = re.sub(r"(?:第|EP|E|Episode|#)\s*\d{1,3}(?:[话話集])?.*$", "", value, flags=re.I)
    return clean_name(value)


def entry_links(entry: Any) -> tuple[str, str]:
    torrent_url = ""
    magnet = ""
    for link in getattr(entry, "links", []) or []:
        href = link.get("href", "")
        link_type = link.get("type", "")
        if href.startswith("magnet:"):
            magnet = href
        elif "torrent" in link_type or href.endswith(".torrent") or "Download" in href:
            torrent_url = href
    link = getattr(entry, "link", "")
    if link.startswith("magnet:"):
        magnet = link
    elif not torrent_url:
        torrent_url = link
    return torrent_url, magnet


def parse_entry(entry: Any) -> ParsedRelease:
    title = getattr(entry, "title", "").strip()
    link = getattr(entry, "link", "").strip()
    published = getattr(entry, "published", "") or getattr(entry, "updated", "")
    torrent_url, magnet = entry_links(entry)
    return ParsedRelease(
        guid=getattr(entry, "id", "") or link or title,
        title=title,
        series_title=parse_series_title(title),
        episode_number=parse_episode(title),
        subtitle_group=parse_group(title),
        resolution=parse_resolution(title),
        language=parse_language(title),
        bangumi_id=parse_bangumi_id(entry, link, title),
        year=parse_year(title, published),
        torrent_url=torrent_url,
        magnet=magnet,
        page_url=link,
        mikan_bangumi_id=parse_mikan_bangumi_id(entry, link),
        published_at=published,
    )
