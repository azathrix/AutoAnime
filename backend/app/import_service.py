from __future__ import annotations

from pathlib import Path
from typing import Any

from .parser import (
    clean_name,
    parse_episode,
    parse_group,
    parse_language,
    parse_resolution,
    parse_series_title,
    parse_subtitle_format,
    parse_year,
)


VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".mov", ".m4v", ".ts", ".wmv", ".flv", ".webm"}


def media_candidate_from_title(
    title: str,
    *,
    source_type: str,
    source_uri: str = "",
    size_bytes: int = 0,
) -> dict[str, Any]:
    clean_title = clean_name(title or Path(source_uri).stem or "Unknown")
    return {
        "source_type": source_type,
        "source_uri": source_uri,
        "title": clean_title,
        "series_title": parse_series_title(clean_title),
        "episode_number": parse_episode(clean_title),
        "subtitle_group": parse_group(clean_title),
        "resolution": parse_resolution(clean_title),
        "language": parse_language(clean_title),
        "subtitle_format": parse_subtitle_format(clean_title),
        "year": parse_year(clean_title),
        "size_bytes": int(size_bytes or 0),
        "needs_metadata": True,
        "needs_episode": parse_episode(clean_title) <= 0,
    }


def preview_local_import(root_path: str, *, limit: int = 200) -> list[dict[str, Any]]:
    root = Path(root_path).expanduser()
    if not root.exists():
        raise FileNotFoundError(f"路径不存在: {root_path}")
    if root.is_file():
        files = [root]
    else:
        files = [
            item
            for item in root.rglob("*")
            if item.is_file() and item.suffix.lower() in VIDEO_EXTENSIONS
        ]
    files.sort(key=lambda item: str(item).lower())
    result = []
    for item in files[: max(1, int(limit))]:
        try:
            size = item.stat().st_size
        except OSError:
            size = 0
        result.append(
            media_candidate_from_title(
                item.stem,
                source_type="local",
                source_uri=str(item),
                size_bytes=size,
            )
        )
    return result


def preview_torrent_import(
    *,
    title: str,
    magnet: str = "",
    torrent_url: str = "",
    page_url: str = "",
) -> dict[str, Any]:
    source_uri = magnet or torrent_url or page_url
    if not source_uri:
        raise ValueError("缺少 magnet/torrent/page_url")
    item = media_candidate_from_title(
        title or source_uri,
        source_type="torrent",
        source_uri=source_uri,
    )
    item["magnet"] = magnet
    item["torrent_url"] = torrent_url
    item["page_url"] = page_url
    return item
