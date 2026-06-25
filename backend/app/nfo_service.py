from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from .database import connect
from .db import get_settings, log, now
from .library import expected_local_episode_path, local_series_path, render_season_dir


def setting_enabled(value: str) -> bool:
    return str(value or "").lower() in {"1", "true", "yes", "on"}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _backup_broken_nfo(path: Path) -> None:
    if not path.exists():
        return
    backup = path.with_suffix(f"{path.suffix}.anitrack.bak")
    try:
        shutil.copy2(path, backup)
    except OSError as exc:
        log("warn", f"NFO 备份失败: path={path} error={exc}")


def _load_or_create(path: Path, root_tag: str) -> tuple[ET.ElementTree, ET.Element, bool]:
    if path.exists():
        try:
            tree = ET.parse(path)
            root = tree.getroot()
            if root.tag == root_tag:
                return tree, root, False
            _backup_broken_nfo(path)
        except ET.ParseError:
            _backup_broken_nfo(path)
        except OSError as exc:
            log("warn", f"NFO 读取失败: path={path} error={exc}")
            _backup_broken_nfo(path)
    root = ET.Element(root_tag)
    return ET.ElementTree(root), root, True


def _child(root: ET.Element, tag: str) -> ET.Element | None:
    return root.find(tag)


def _set_if_blank(root: ET.Element, tag: str, value: Any) -> bool:
    text = _text(value)
    if not text:
        return False
    node = _child(root, tag)
    if node is None:
        node = ET.SubElement(root, tag)
        node.text = text
        return True
    if not _text(node.text):
        node.text = text
        return True
    return False


def _set_required(root: ET.Element, tag: str, value: Any) -> bool:
    text = _text(value)
    if not text:
        return False
    node = _child(root, tag)
    if node is None:
        node = ET.SubElement(root, tag)
        node.text = text
        return True
    if _text(node.text) != text:
        node.text = text
        return True
    return False


def _ensure_tmdb_id(root: ET.Element, tmdb_id: str) -> bool:
    tmdb_id = _text(tmdb_id)
    if not tmdb_id:
        return False
    changed = _set_required(root, "tmdbid", tmdb_id)
    tmdb_nodes = [
        node
        for node in root.findall("uniqueid")
        if _text(node.attrib.get("type")).lower() == "tmdb"
    ]
    if not tmdb_nodes:
        node = ET.SubElement(root, "uniqueid", {"type": "tmdb"})
        node.text = tmdb_id
        changed = True
    else:
        for node in tmdb_nodes:
            if _text(node.text) != tmdb_id:
                node.text = tmdb_id
                changed = True
    return changed


def _write_if_changed(path: Path, tree: ET.ElementTree, changed: bool) -> bool:
    if not changed:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        ET.indent(tree, space="  ")
    except AttributeError:
        pass
    tree.write(path, encoding="utf-8", xml_declaration=True, short_empty_elements=False)
    return True


def _entry_title(entry: dict[str, Any]) -> str:
    return _text(entry.get("title_cn") or entry.get("display_title") or entry.get("title_raw"))


def _ensure_show_nfo(path: Path, entry: dict[str, Any]) -> bool:
    tree, root, changed = _load_or_create(path, "tvshow")
    changed = _ensure_tmdb_id(root, _text(entry.get("tmdb_id"))) or changed
    changed = _set_if_blank(root, "title", _entry_title(entry)) or changed
    changed = _set_if_blank(root, "originaltitle", entry.get("title_raw")) or changed
    changed = _set_if_blank(root, "plot", entry.get("summary")) or changed
    changed = _set_if_blank(root, "year", entry.get("year")) or changed
    return _write_if_changed(path, tree, changed)


def _ensure_movie_nfo(path: Path, entry: dict[str, Any]) -> bool:
    tree, root, changed = _load_or_create(path, "movie")
    changed = _ensure_tmdb_id(root, _text(entry.get("tmdb_id"))) or changed
    changed = _set_if_blank(root, "title", _entry_title(entry)) or changed
    changed = _set_if_blank(root, "originaltitle", entry.get("title_raw")) or changed
    changed = _set_if_blank(root, "plot", entry.get("summary")) or changed
    changed = _set_if_blank(root, "year", entry.get("year")) or changed
    return _write_if_changed(path, tree, changed)


def _ensure_season_nfo(path: Path, entry: dict[str, Any]) -> bool:
    season = int(entry.get("season_number") or 1)
    tree, root, changed = _load_or_create(path, "season")
    changed = _ensure_tmdb_id(root, _text(entry.get("tmdb_id"))) or changed
    changed = _set_if_blank(root, "title", f"Season {season:02d}") or changed
    changed = _set_if_blank(root, "seasonnumber", season) or changed
    return _write_if_changed(path, tree, changed)


def _ensure_episode_nfo(path: Path, entry: dict[str, Any], episode: dict[str, Any]) -> bool:
    episode_number = int(episode.get("episode_number") or 0)
    if episode_number <= 0:
        return False
    tree, root, changed = _load_or_create(path, "episodedetails")
    changed = _ensure_tmdb_id(root, _text(entry.get("tmdb_id"))) or changed
    changed = _set_if_blank(root, "title", episode.get("title") or f"第 {episode_number:02d} 话") or changed
    changed = _set_if_blank(root, "season", int(entry.get("season_number") or 1)) or changed
    changed = _set_if_blank(root, "episode", episode_number) or changed
    changed = _set_if_blank(root, "showtitle", _entry_title(entry)) or changed
    changed = _set_if_blank(root, "aired", episode.get("air_date")) or changed
    return _write_if_changed(path, tree, changed)


def _episode_nfo_path(entry: dict[str, Any], episode: dict[str, Any], settings: dict[str, str]) -> Path | None:
    for local_path in (_text(episode.get("local_path")), _text(episode.get("asset_local_path"))):
        if not local_path:
            continue
        path = Path(local_path)
        nfo_path = path.with_suffix(".nfo")
        if path.exists() or nfo_path.exists():
            return nfo_path
    expected = expected_local_episode_path(entry, int(episode.get("episode_number") or 0), ".mkv", settings)
    expected_path = Path(expected)
    expected_nfo = expected_path.with_suffix(".nfo")
    if expected_path.exists() or expected_nfo.exists():
        return expected_nfo
    return None


def generate_jellyfin_nfo_for_entry(entry_id: int, settings: dict[str, str] | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    if not setting_enabled(settings.get("auto_generate_nfo", "false")):
        return {"generated": False, "reason": "disabled", "changed": 0}
    with connect() as conn:
        row = conn.execute("SELECT * FROM entries WHERE id=? AND COALESCE(hidden, 0)=0", (entry_id,)).fetchone()
        episodes = conn.execute(
            """
            SELECT ep.*,
                   la.local_path AS asset_local_path
            FROM episodes ep
            LEFT JOIN local_assets la ON la.id=(
              SELECT id FROM local_assets
              WHERE entry_id=ep.entry_id
                AND episode_number=ep.episode_number
                AND status='synced'
              ORDER BY updated_at DESC, id DESC
              LIMIT 1
            )
            WHERE ep.entry_id=? AND ep.episode_number > 0
            ORDER BY ep.episode_number ASC
            """,
            (entry_id,),
        ).fetchall()
    if not row:
        return {"generated": False, "reason": "not_found", "changed": 0}
    entry = dict(row)
    tmdb_id = _text(entry.get("tmdb_id"))
    if not tmdb_id:
        return {"generated": False, "reason": "missing_tmdb_id", "changed": 0}

    media_type = _text(entry.get("media_type")).lower()
    base_dir = local_series_path(entry, settings)
    changed = 0
    paths: list[str] = []
    if media_type == "movie":
        path = base_dir / "movie.nfo"
        changed += 1 if _ensure_movie_nfo(path, entry) else 0
        paths.append(str(path))
    else:
        show_path = base_dir / "tvshow.nfo"
        changed += 1 if _ensure_show_nfo(show_path, entry) else 0
        paths.append(str(show_path))
        season_dir = base_dir / render_season_dir(int(entry.get("season_number") or 1), settings)
        season_path = season_dir / "season.nfo"
        changed += 1 if _ensure_season_nfo(season_path, entry) else 0
        paths.append(str(season_path))
        for episode_row in episodes:
            episode = dict(episode_row)
            episode_path = _episode_nfo_path(entry, episode, settings)
            if episode_path is None:
                continue
            changed += 1 if _ensure_episode_nfo(episode_path, entry, episode) else 0
            paths.append(str(episode_path))

    with connect() as conn:
        conn.execute("UPDATE entries SET nfo_status='generated', updated_at=? WHERE id=?", (now(), entry_id))
        conn.execute(
            "UPDATE local_assets SET nfo_status='generated', updated_at=? WHERE entry_id=? AND status='synced'",
            (now(), entry_id),
        )
    if changed:
        log("info", f"已校验 Jellyfin NFO: entry_id={entry_id} changed={changed}")
    return {"generated": True, "changed": changed, "paths": paths}
