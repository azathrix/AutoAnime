from __future__ import annotations

import re
from pathlib import Path

from .database import connect
from .parser import clean_name


def bool_setting(value: str) -> bool:
    return str(value).lower() in {"1", "true", "yes", "on"}


def normalize_series_root_title(value: str) -> str:
    text = clean_name(value or "Unknown")
    labels = parse_entry_labels(text)
    text = str(labels.get("title_root") or text)
    patterns = [
        r"\s+第[一二三四五六七八九十百零两\d]+季$",
        r"\s+第[一二三四五六七八九十百零两\d]+期$",
        r"\s+第[一二三四五六七八九十百零两\d]+[章部]$",
        r"\s+season\s*\d+$",
        r"\s+s(?:eason)?\s*\d+$",
        r"\s+part\s*\d+$",
        r"\s+cour\s*\d+$",
        r"\s+[^ ]{1,18}[篇編]$",
    ]
    for pattern in patterns:
        text = re.sub(pattern, "", text, flags=re.I)
    return clean_name(text or value or "Unknown")


def parse_entry_labels(value: str) -> dict[str, str | int]:
    text = clean_name(value or "Unknown")
    text = re.sub(r"\b(season|part|cour|s)\s+(\d+)\b", r"\1\2", text, flags=re.I)
    tokens = [token for token in text.split(" ") if token]
    season_label = ""
    arc_label = ""
    part_label = ""
    special_label = ""
    season_number = 1
    root_tokens: list[str] = []

    season_pattern = re.compile(r"^第([一二三四五六七八九十百零两\d]+)季$", re.I)
    part_pattern = re.compile(r"^第([一二三四五六七八九十百零两\d]+)(部分|期)$", re.I)
    season_word_pattern = re.compile(r"^(?:season|s)(\d+)$", re.I)
    part_word_pattern = re.compile(r"^part(\d+)$", re.I)
    specials = {"ova", "oad", "sp", "特别篇", "剧场版"}
    arc_suffixes = ("篇", "編")
    arc_words = {"前篇", "后篇", "中篇"}

    for token in tokens:
        normalized = token.strip()
        lower = normalized.lower()
        spaced_season_match = re.match(r"^(?:season|s)\s*(\d+)$", normalized, re.I)
        spaced_part_match = re.match(r"^part\s*(\d+)$", normalized, re.I)
        if lower in specials:
            special_label = normalized
            continue
        season_match = season_pattern.match(normalized)
        if season_match:
            season_label = normalized
            season_number = cn_number_to_int(season_match.group(1))
            continue
        season_word_match = season_word_pattern.match(lower)
        if season_word_match or spaced_season_match:
            season_label = normalized
            season_number = max(1, int((season_word_match or spaced_season_match).group(1)))
            continue
        part_match = part_pattern.match(normalized)
        if part_match:
            part_label = normalized
            continue
        part_word_match = part_word_pattern.match(lower)
        if part_word_match or spaced_part_match:
            part_label = normalized
            continue
        if normalized in arc_words or normalized.endswith(arc_suffixes):
            if arc_label:
                arc_label = f"{arc_label} {normalized}".strip()
            else:
                arc_label = normalized
            continue
        if normalized.startswith("～") and normalized.endswith("～"):
            arc_label = normalized
            continue
        root_tokens.append(normalized)

    title_root = clean_name(" ".join(root_tokens) or text)
    entry_kind = "season"
    if special_label:
        entry_kind = "special"
    elif part_label:
        entry_kind = "part"
    elif arc_label:
        entry_kind = "arc"
    return {
        "title_root": title_root,
        "season_label": season_label,
        "arc_label": arc_label,
        "part_label": part_label,
        "special_label": special_label,
        "season_number": season_number,
        "entry_kind": entry_kind,
    }


def cn_number_to_int(value: str) -> int:
    if not value:
        return 1
    if value.isdigit():
        return max(1, int(value))
    mapping = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    unit_map = {"十": 10, "百": 100}
    total = 0
    current = 0
    for char in value:
        if char in mapping:
            current = mapping[char]
        elif char in unit_map:
            unit = unit_map[char]
            if current == 0:
                current = 1
            total += current * unit
            current = 0
    total += current
    return max(1, total or 1)


def render_series_dir(series: dict, settings: dict[str, str]) -> str:
    template = settings.get("series_dir_template") or "{title_base} ({year}) [bangumi-{bangumi_id}]"
    title_cn = clean_name(series.get("title_cn") or series.get("title_raw") or "Unknown")
    title_base = normalize_series_root_title(title_cn)
    bangumi_id = series.get("bangumi_id") or "unknown"
    year = int(series.get("year") or 0)
    return template.format(
        title_cn=title_cn,
        title_base=title_base,
        title_raw=clean_name(series.get("title_raw") or title_cn),
        bangumi_id=bangumi_id,
        tmdb_id=series.get("tmdb_id") or "unknown",
        year=year or "0000",
    )


def render_season_dir(season: int, settings: dict[str, str]) -> str:
    template = settings.get("season_dir_template") or "Season {season:02d}"
    return template.format(season=season or 1)


def render_episode_name(series: dict, episode_number: int, episode_title: str, settings: dict[str, str]) -> str:
    template = settings.get("episode_name_template") or "{title_cn} - S{season:02d}E{episode:02d} - {episode_title}"
    title_cn = clean_name(series.get("title_cn") or series.get("title_raw") or "Unknown")
    return template.format(
        title_cn=title_cn,
        season=int(series.get("season_number") or 1),
        episode=int(episode_number or 0),
        episode_title=clean_name(episode_title or f"第{int(episode_number or 0):02d}话"),
    )


def local_library_root(entry: dict, settings: dict[str, str]) -> str:
    library_id = int(entry.get("target_library_id") or 0)
    if library_id > 0:
        with connect() as conn:
            row = conn.execute(
                "SELECT root_path FROM media_libraries WHERE id=? AND enabled=1",
                (library_id,),
            ).fetchone()
        if row and str(row["root_path"] or "").strip():
            return str(row["root_path"]).strip()
    return settings.get("local_library_root") or "/media/autoanime"


def local_series_path(entry: dict, settings: dict[str, str]) -> Path:
    return Path(local_library_root(entry, settings)) / render_series_dir(entry, settings)


def target_dir(series: dict, settings: dict[str, str]) -> str:
    root = settings.get("library_root") or "/Anime"
    root = "/" + root.strip("/")
    return "/".join(
        [
            root,
            render_series_dir(series, settings),
            render_season_dir(int(series.get("season_number") or 1), settings),
        ]
    )

