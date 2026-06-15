from __future__ import annotations

from .parser import clean_name


def bool_setting(value: str) -> bool:
    return str(value).lower() in {"1", "true", "yes", "on"}


def render_series_dir(series: dict, settings: dict[str, str]) -> str:
    template = settings.get("series_dir_template") or "{title_cn} ({year}) [bangumi-{bangumi_id}]"
    title_cn = clean_name(series.get("title_cn") or series.get("title_raw") or "Unknown")
    bangumi_id = series.get("bangumi_id") or "unknown"
    year = int(series.get("year") or 0)
    return template.format(
        title_cn=title_cn,
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
