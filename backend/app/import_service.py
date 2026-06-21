from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .database import connect
from .db import now
from .library import normalize_series_root_title, parse_entry_labels
from .parser import (
    clean_name,
    normalize_title_key,
    parse_episode,
    parse_group,
    parse_language,
    parse_resolution,
    parse_series_title,
    parse_subtitle_format,
    parse_year,
)
from .sync_service import synthetic_task_id


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


def commit_local_import(items: list[dict[str, Any]], options: dict[str, Any] | None = None) -> dict[str, Any]:
    options = options or {}
    imported: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for item in items:
        candidate = dict(item or {})
        if str(candidate.get("source_type") or "") != "local":
            skipped.append({"item": candidate, "reason": "不是本地候选"})
            continue
        path = Path(str(candidate.get("source_uri") or ""))
        if not path.exists() or not path.is_file():
            skipped.append({"item": candidate, "reason": "本地文件不存在"})
            continue
        try:
            imported.append(_commit_one_local(candidate, options))
        except Exception as exc:
            skipped.append({"item": candidate, "reason": str(exc)[:500]})
    return {"imported": imported, "skipped": skipped, "imported_count": len(imported), "skipped_count": len(skipped)}


def commit_torrent_import(item: dict[str, Any], options: dict[str, Any] | None = None) -> dict[str, Any]:
    options = options or {}
    candidate = dict(item or {})
    if str(candidate.get("source_type") or "") != "torrent":
        candidate = preview_torrent_import(
            title=str(candidate.get("title") or ""),
            magnet=str(candidate.get("magnet") or ""),
            torrent_url=str(candidate.get("torrent_url") or ""),
            page_url=str(candidate.get("page_url") or candidate.get("source_uri") or ""),
        )
    if not (candidate.get("magnet") or candidate.get("torrent_url") or candidate.get("source_uri")):
        raise ValueError("缺少 magnet/torrent 链接")
    return _commit_one_release(candidate, options, source_provider="torrent_import", local_path="")


def _commit_one_local(candidate: dict[str, Any], options: dict[str, Any]) -> dict[str, Any]:
    source_path = str(candidate.get("source_uri") or "")
    committed = _commit_one_release(candidate, options, source_provider="local_import", local_path=source_path)
    release_id = int(committed["release_id"])
    entry_id = int(committed["entry_id"])
    series_id = int(committed["series_id"])
    episode_number = int(committed["episode_number"])
    artifact_name = Path(source_path).name
    provider = "local"
    provider_file_id = f"local:{_stable_hash(source_path)}"
    ts = now()
    with connect() as conn:
        existing = conn.execute(
            """
            SELECT id
            FROM download_artifacts
            WHERE provider=? AND (provider_file_id=? OR (entry_id=? AND episode_number=?))
            ORDER BY id ASC
            LIMIT 1
            """,
            (provider, provider_file_id, entry_id, episode_number),
        ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE download_artifacts
                SET task_id=?, release_id=?, series_id=?, entry_id=?, episode_number=?,
                    provider_file_id=?, remote_path=?, artifact_name=?, status='available', updated_at=?
                WHERE id=?
                """,
                (
                    synthetic_task_id(provider_file_id),
                    release_id,
                    series_id,
                    entry_id,
                    episode_number,
                    provider_file_id,
                    source_path,
                    artifact_name,
                    ts,
                    int(existing["id"]),
                ),
            )
            artifact_id = int(existing["id"])
        else:
            conn.execute(
                """
                INSERT INTO download_artifacts
                  (task_id, release_id, series_id, entry_id, episode_number, provider, provider_file_id,
                   remote_path, artifact_name, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'available', ?, ?)
                """,
                (
                    synthetic_task_id(provider_file_id),
                    release_id,
                    series_id,
                    entry_id,
                    episode_number,
                    provider,
                    provider_file_id,
                    source_path,
                    artifact_name,
                    ts,
                    ts,
                ),
            )
            artifact_id = int(conn.execute("SELECT id FROM download_artifacts WHERE task_id=?", (synthetic_task_id(provider_file_id),)).fetchone()["id"])
        conn.execute(
            """
            INSERT INTO local_assets
              (download_artifact_id, release_id, series_id, entry_id, episode_number, local_path,
               nfo_status, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'pending', 'synced', ?, ?)
            ON CONFLICT(download_artifact_id) DO UPDATE SET
              release_id=excluded.release_id,
              series_id=excluded.series_id,
              entry_id=excluded.entry_id,
              episode_number=excluded.episode_number,
              local_path=excluded.local_path,
              status='synced',
              updated_at=excluded.updated_at
            """,
            (artifact_id, release_id, series_id, entry_id, episode_number, source_path, ts, ts),
        )
        local_asset_id = int(conn.execute("SELECT id FROM local_assets WHERE download_artifact_id=?", (artifact_id,)).fetchone()["id"])
    committed.update({"download_artifact_id": artifact_id, "local_asset_id": local_asset_id, "local_path": source_path})
    return committed


def _commit_one_release(candidate: dict[str, Any], options: dict[str, Any], *, source_provider: str, local_path: str) -> dict[str, Any]:
    ts = now()
    episode_number = int(options.get("episode_number") or candidate.get("episode_number") or 0)
    if episode_number <= 0:
        raise ValueError("缺少集数，无法入库")
    with connect() as conn:
        series_id, entry_id = _ensure_import_entry(conn, candidate, options, source_provider=source_provider)
        conn.execute(
            """
            INSERT INTO episodes
              (series_id, entry_id, episode_number, title, air_date, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, '', ?, ?, ?)
            ON CONFLICT(series_id, episode_number) DO UPDATE SET
              entry_id=excluded.entry_id,
              status=excluded.status,
              updated_at=excluded.updated_at
            """,
            (
                series_id,
                entry_id,
                episode_number,
                f"第{episode_number:02d}话",
                "downloaded" if local_path else "wanted",
                ts,
                ts,
            ),
        )
        guid = _release_guid(candidate, source_provider)
        conn.execute(
            "UPDATE releases SET selected=0, updated_at=? WHERE entry_id=? AND episode_number=?",
            (ts, entry_id, episode_number),
        )
        conn.execute(
            """
            INSERT INTO releases
              (series_id, entry_id, episode_number, guid, title, subtitle_group, resolution,
               language, subtitle_format, torrent_url, magnet, published_at, selected, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            ON CONFLICT(guid) DO UPDATE SET
              series_id=excluded.series_id,
              entry_id=excluded.entry_id,
              episode_number=excluded.episode_number,
              title=excluded.title,
              subtitle_group=excluded.subtitle_group,
              resolution=excluded.resolution,
              language=excluded.language,
              subtitle_format=excluded.subtitle_format,
              torrent_url=excluded.torrent_url,
              magnet=excluded.magnet,
              selected=1,
              updated_at=excluded.updated_at
            """,
            (
                series_id,
                entry_id,
                episode_number,
                guid,
                str(candidate.get("title") or Path(local_path).stem or candidate.get("series_title") or "Imported"),
                str(candidate.get("subtitle_group") or ""),
                str(candidate.get("resolution") or ""),
                str(candidate.get("language") or ""),
                str(candidate.get("subtitle_format") or ""),
                str(candidate.get("torrent_url") or ""),
                str(candidate.get("magnet") or ""),
                ts,
                ts,
                ts,
            ),
        )
        release_id = int(conn.execute("SELECT id FROM releases WHERE guid=?", (guid,)).fetchone()["id"])
    return {
        "entry_id": entry_id,
        "series_id": series_id,
        "release_id": release_id,
        "episode_number": episode_number,
        "title": str(candidate.get("series_title") or candidate.get("title") or ""),
        "source_type": source_provider,
    }


def _ensure_import_entry(conn, candidate: dict[str, Any], options: dict[str, Any], *, source_provider: str) -> tuple[int, int]:
    ts = now()
    title = clean_name(str(options.get("title_cn") or candidate.get("series_title") or candidate.get("title") or "Imported"))
    title_root = normalize_series_root_title(title)
    labels = parse_entry_labels(title)
    season_number = int(options.get("season_number") or labels.get("season_number") or 1)
    year = int(options.get("year") or candidate.get("year") or 0)
    bangumi_id = str(options.get("bangumi_id") or candidate.get("bangumi_id") or "").strip()
    tmdb_id = str(options.get("tmdb_id") or candidate.get("tmdb_id") or "").strip()
    media_type = str(options.get("media_type") or "anime").strip() or "anime"
    region = str(options.get("region") or "jp").strip() or "jp"
    target_library_id = _target_library_id(conn, int(options.get("target_library_id") or 0), media_type)
    identity = bangumi_id or tmdb_id or normalize_title_key(title_root)
    metadata_provider = "bangumi" if bangumi_id else ("tmdb" if tmdb_id else "manual")
    work_key = f"{media_type}:{metadata_provider}:{identity}"
    entry_key = f"{work_key}:s{season_number}:lib{target_library_id}"

    conn.execute(
        """
        INSERT INTO series
          (fingerprint, title_raw, title_cn, title_romaji, bangumi_id, mikan_bangumi_id, tmdb_id,
           year, season_number, poster_url, poster_path, summary, metadata_source, nfo_status,
           hidden, auto_download, selected_group, selected_resolution, backfill_mode, created_at, updated_at)
        VALUES (?, ?, ?, '', ?, '', ?, ?, ?, '', '', '', ?, 'pending', 0, 'inherit', '', '', 'inherit', ?, ?)
        ON CONFLICT(fingerprint) DO UPDATE SET
          title_raw=excluded.title_raw,
          title_cn=excluded.title_cn,
          bangumi_id=CASE WHEN excluded.bangumi_id!='' THEN excluded.bangumi_id ELSE series.bangumi_id END,
          tmdb_id=CASE WHEN excluded.tmdb_id!='' THEN excluded.tmdb_id ELSE series.tmdb_id END,
          year=CASE WHEN excluded.year!=0 THEN excluded.year ELSE series.year END,
          season_number=excluded.season_number,
          metadata_source=excluded.metadata_source,
          updated_at=excluded.updated_at
        """,
        (entry_key, title, title, bangumi_id, tmdb_id, year, season_number, metadata_provider, ts, ts),
    )
    series_id = int(conn.execute("SELECT id FROM series WHERE fingerprint=?", (entry_key,)).fetchone()["id"])

    conn.execute(
        """
        INSERT INTO works
          (root_key, title_root, title_root_raw, bangumi_id, metadata_source, hidden, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, 0, ?, ?)
        ON CONFLICT(root_key) DO UPDATE SET
          title_root=excluded.title_root,
          title_root_raw=excluded.title_root_raw,
          bangumi_id=CASE WHEN excluded.bangumi_id!='' THEN excluded.bangumi_id ELSE works.bangumi_id END,
          metadata_source=excluded.metadata_source,
          updated_at=excluded.updated_at
        """,
        (work_key, title_root, title, bangumi_id, metadata_provider, ts, ts),
    )
    work_id = int(conn.execute("SELECT id FROM works WHERE root_key=?", (work_key,)).fetchone()["id"])

    conn.execute(
        """
        INSERT INTO entries
          (work_id, fingerprint, domain_kind, media_type, region, source_provider, metadata_provider,
           external_id, target_library_id, entry_kind, display_title, title_root,
           season_label, arc_label, part_label, special_label,
           title_raw, title_cn, bangumi_id, mikan_bangumi_id, tmdb_id, year, season_number,
           poster_url, summary, metadata_source, hidden, auto_download, selected_group, selected_resolution,
           backfill_mode, created_at, updated_at)
        VALUES (?, ?, 'library', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, '', '', ?, 0, 'inherit', '', '', 'inherit', ?, ?)
        ON CONFLICT(fingerprint) DO UPDATE SET
          work_id=excluded.work_id,
          media_type=excluded.media_type,
          region=excluded.region,
          source_provider=excluded.source_provider,
          metadata_provider=excluded.metadata_provider,
          external_id=CASE WHEN excluded.external_id!='' THEN excluded.external_id ELSE entries.external_id END,
          target_library_id=CASE WHEN excluded.target_library_id!=0 THEN excluded.target_library_id ELSE entries.target_library_id END,
          display_title=excluded.display_title,
          title_root=excluded.title_root,
          title_raw=excluded.title_raw,
          title_cn=excluded.title_cn,
          bangumi_id=CASE WHEN excluded.bangumi_id!='' THEN excluded.bangumi_id ELSE entries.bangumi_id END,
          tmdb_id=CASE WHEN excluded.tmdb_id!='' THEN excluded.tmdb_id ELSE entries.tmdb_id END,
          year=CASE WHEN excluded.year!=0 THEN excluded.year ELSE entries.year END,
          season_number=excluded.season_number,
          metadata_source=excluded.metadata_source,
          hidden=0,
          updated_at=excluded.updated_at
        """,
        (
            work_id,
            entry_key,
            media_type,
            region,
            source_provider,
            metadata_provider,
            bangumi_id or tmdb_id,
            target_library_id,
            labels.get("entry_kind") or "season",
            title,
            title_root,
            labels.get("season_label") or "",
            labels.get("arc_label") or "",
            labels.get("part_label") or "",
            labels.get("special_label") or "",
            title,
            title,
            bangumi_id,
            tmdb_id,
            year,
            season_number,
            metadata_provider,
            ts,
            ts,
        ),
    )
    entry_id = int(conn.execute("SELECT id FROM entries WHERE fingerprint=?", (entry_key,)).fetchone()["id"])
    conn.execute(
        """
        INSERT INTO library_entries
          (entry_id, source_type, source_ref, wanted, archived, created_at, updated_at)
        VALUES (?, ?, ?, 1, 0, ?, ?)
        ON CONFLICT(entry_id) DO UPDATE SET
          source_type=excluded.source_type,
          source_ref=CASE WHEN excluded.source_ref!='' THEN excluded.source_ref ELSE library_entries.source_ref END,
          wanted=1,
          archived=0,
          updated_at=excluded.updated_at
        """,
        (entry_id, source_provider, str(candidate.get("source_uri") or ""), ts, ts),
    )
    return series_id, entry_id


def _target_library_id(conn, requested_id: int, media_type: str) -> int:
    if requested_id > 0:
        row = conn.execute("SELECT id FROM media_libraries WHERE id=? AND enabled=1", (requested_id,)).fetchone()
        if row:
            return int(row["id"])
    key = "anime_library" if media_type == "anime" else ("movies" if media_type == "movie" else "tv")
    row = conn.execute("SELECT id FROM media_libraries WHERE key=? AND enabled=1", (key,)).fetchone()
    return int(row["id"] or 0) if row else 0


def _release_guid(candidate: dict[str, Any], source_provider: str) -> str:
    source = str(candidate.get("source_uri") or candidate.get("magnet") or candidate.get("torrent_url") or candidate.get("title") or "")
    return f"{source_provider}:{_stable_hash(source)}"


def _stable_hash(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8", errors="ignore")).hexdigest()[:24]
