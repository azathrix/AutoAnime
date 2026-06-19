from __future__ import annotations

import re
import zlib
from pathlib import Path

from .database import connect
from .downloader_service import download_to_local, list_remote_files, provider_key, remote_file_id
from .db import log, now
from .library import local_library_root, render_episode_name, render_season_dir, render_series_dir
from .parser import normalize_title_key, parse_episode



def resolve_entry_series_id(conn, entry_id: int) -> int:
    row = conn.execute(
        "SELECT series_id FROM releases WHERE entry_id=? ORDER BY id ASC LIMIT 1",
        (entry_id,),
    ).fetchone()
    return int(row["series_id"] or 0) if row else 0


def task_retry_after_minutes(minutes: int) -> str:
    from datetime import datetime, timedelta, timezone

    return (datetime.now(timezone.utc) + timedelta(minutes=max(1, minutes))).isoformat()


def stale_running_cutoff(minutes: int = 10) -> str:
    from datetime import datetime, timedelta, timezone

    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()


def task_retry_after(settings: dict[str, str], attempts: int) -> str:
    default_minutes = max(5, 5 * max(1, attempts))
    minutes = min(180, default_minutes)
    return task_retry_after_minutes(minutes)


def ensure_sync_rule(entry_id: int, settings: dict[str, str], enabled: bool | None = None) -> None:
    ts = now()
    auto_sync = settings.get("auto_sync_following", "false").lower() == "true"
    sync_enabled = auto_sync if enabled is None else enabled
    explicit_enabled = enabled is not None
    with connect() as conn:
        series_id = resolve_entry_series_id(conn, entry_id)
        conn.execute(
            """
            INSERT INTO sync_rules
              (series_id, entry_id, sync_enabled, auto_sync_following, local_root, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(entry_id) DO UPDATE SET
              series_id=excluded.series_id,
              sync_enabled=CASE
                WHEN ?=1 THEN excluded.sync_enabled
                ELSE sync_rules.sync_enabled
              END,
              auto_sync_following=excluded.auto_sync_following,
              local_root=CASE WHEN sync_rules.local_root='' THEN excluded.local_root ELSE sync_rules.local_root END,
              updated_at=excluded.updated_at
            """,
            (
                series_id,
                entry_id,
                1 if sync_enabled else 0,
                1 if auto_sync else 0,
                settings.get("local_library_root") or "/media/autoanime",
                ts,
                ts,
                1 if explicit_enabled else 0,
            ),
        )


def local_episode_path(download_artifact: dict, entry: dict, settings: dict[str, str]) -> str:
    root = Path(local_library_root(entry, settings))
    series_dir = render_series_dir(entry, settings)
    season_dir = render_season_dir(int(entry.get("season_number") or 1), settings)
    suffix = Path(download_artifact.get("artifact_name") or "").suffix
    filename = download_artifact.get("artifact_name") or render_episode_name(
        entry,
        int(download_artifact.get("episode_number") or 0),
        "",
        settings,
    )
    if suffix and not filename.endswith(suffix):
        filename = f"{filename}{suffix}"
    return str(root / series_dir / season_dir / filename)


def normalize_local_target_path(target_path: str, source_name: str = "") -> str:
    path = Path(target_path)
    suffix = path.suffix or Path(source_name).suffix
    stem = path.stem
    stem = re.sub(r"\(\d+\)$", "", stem).rstrip()
    normalized = path.with_name(f"{stem}{suffix}")
    return str(normalized)


VIDEO_SUFFIXES = {".mkv", ".mp4", ".avi", ".mov", ".wmv", ".ts", ".m2ts", ".flv", ".webm"}


async def find_existing_remote_episode(
    settings: dict[str, str],
    target_directory: str,
    expected_name: str,
    episode_number: int,
) -> dict | None:
    try:
        files = await list_remote_files(settings, target_directory, recursive=False)
    except Exception as exc:
        if "directory not found" in str(exc).lower():
            return None
        raise
    normalized_expected = normalize_title_key(expected_name)
    for item in files:
        if item.get("is_dir"):
            continue
        name = str(item.get("name") or "")
        if Path(name).suffix.lower() not in VIDEO_SUFFIXES:
            continue
        if normalized_expected and normalize_title_key(name) == normalized_expected:
            return item
        if episode_number > 0 and parse_episode(name) == episode_number:
            return item
    return None


def download_file_id(item: dict) -> str:
    return remote_file_id(item)


def synthetic_task_id(file_id: str) -> int:
    return 0 - (zlib.crc32(file_id.encode("utf-8")) % 2147483647) - 1


def match_remote_file_to_entry(item: dict, entry_rows: list[dict]) -> dict | None:
    path = str(item.get("remote_path") or item.get("name") or "")
    match = re.search(r"bangumi[-_ ]?(\d+)", path, re.I)
    if match:
        bangumi_id = match.group(1)
        for entry in entry_rows:
            if str(entry.get("bangumi_id") or "") == bangumi_id:
                return entry
    normalized_path = normalize_title_key(path)
    best: dict | None = None
    best_len = 0
    for entry in entry_rows:
        keys = {
            normalize_title_key(str(entry.get("title_cn") or "")),
            normalize_title_key(str(entry.get("title_raw") or "")),
        }
        for key in keys:
            if key and key in normalized_path and len(key) > best_len:
                best = entry
                best_len = len(key)
    return best


def ensure_library_entry_for_reference(
    conn,
    *,
    entry_row: dict,
    display_title: str,
    source_ref: str = "",
) -> tuple[int, int]:
    ts = now()
    work_key = normalize_title_key(str(entry_row.get("title_cn") or entry_row.get("title_raw") or display_title or "Unknown"))
    conn.execute(
        """
        INSERT INTO works
          (root_key, title_root, title_root_raw, bangumi_id, metadata_source, hidden, created_at, updated_at)
        VALUES (?, ?, ?, ?, 'remote_import', 0, ?, ?)
        ON CONFLICT(root_key) DO UPDATE SET
          title_root=excluded.title_root,
          title_root_raw=excluded.title_root_raw,
          bangumi_id=CASE WHEN works.bangumi_id='' THEN excluded.bangumi_id ELSE works.bangumi_id END,
          updated_at=excluded.updated_at
        """,
        (
            work_key,
            str(entry_row.get("title_cn") or entry_row.get("title_raw") or display_title or "Unknown"),
            str(entry_row.get("title_raw") or entry_row.get("title_cn") or display_title or "Unknown"),
            str(entry_row.get("bangumi_id") or ""),
            ts,
            ts,
        ),
    )
    work_id = int(conn.execute("SELECT id FROM works WHERE root_key=?", (work_key,)).fetchone()["id"])
    target_library = conn.execute("SELECT id FROM media_libraries WHERE key='anime_library'").fetchone()
    target_library_id = int(target_library["id"] or 0) if target_library else 0
    fingerprint_key = f"remote-library:{entry_row.get('bangumi_id') or ''}:{normalize_title_key(display_title)}"
    conn.execute(
        """
        INSERT INTO entries
          (work_id, fingerprint, domain_kind, media_type, region, source_provider, metadata_provider,
           external_id, target_library_id, entry_kind, display_title, title_root,
           season_label, arc_label, part_label, special_label,
           title_raw, title_cn, bangumi_id, mikan_bangumi_id, tmdb_id, year, season_number,
           poster_url, summary, metadata_source, hidden, auto_download, selected_group, selected_resolution,
           backfill_mode, created_at, updated_at)
        VALUES (?, ?, 'library', 'anime', 'jp', 'remote_import', ?, ?, ?, 'season', ?, ?, '', '', '', '', ?, ?, ?, ?, ?, ?, ?, ?, ?, 'remote_import', 0, 'inherit', '', '', 'inherit', ?, ?)
        ON CONFLICT(fingerprint) DO UPDATE SET
          work_id=excluded.work_id,
          domain_kind='library',
          media_type=excluded.media_type,
          region=excluded.region,
          source_provider=excluded.source_provider,
          metadata_provider=excluded.metadata_provider,
          external_id=CASE WHEN excluded.external_id!='' THEN excluded.external_id ELSE entries.external_id END,
          target_library_id=CASE WHEN entries.target_library_id=0 THEN excluded.target_library_id ELSE entries.target_library_id END,
          display_title=excluded.display_title,
          title_root=excluded.title_root,
          title_raw=excluded.title_raw,
          title_cn=excluded.title_cn,
          bangumi_id=CASE WHEN entries.bangumi_id='' THEN excluded.bangumi_id ELSE entries.bangumi_id END,
          mikan_bangumi_id=CASE WHEN excluded.mikan_bangumi_id!='' THEN excluded.mikan_bangumi_id ELSE entries.mikan_bangumi_id END,
          year=CASE WHEN excluded.year!=0 THEN excluded.year ELSE entries.year END,
          season_number=CASE WHEN excluded.season_number!=0 THEN excluded.season_number ELSE entries.season_number END,
          poster_url=CASE WHEN excluded.poster_url!='' THEN excluded.poster_url ELSE entries.poster_url END,
          summary=CASE WHEN excluded.summary!='' THEN excluded.summary ELSE entries.summary END,
          updated_at=excluded.updated_at
        """,
        (
            work_id,
            fingerprint_key,
            "bangumi" if str(entry_row.get("bangumi_id") or "") else ("tmdb" if str(entry_row.get("tmdb_id") or "") else "source"),
            str(entry_row.get("bangumi_id") or entry_row.get("tmdb_id") or ""),
            target_library_id,
            display_title,
            str(entry_row.get("title_cn") or entry_row.get("title_raw") or display_title or "Unknown"),
            str(entry_row.get("title_raw") or display_title or "Unknown"),
            str(entry_row.get("title_cn") or display_title or "Unknown"),
            str(entry_row.get("bangumi_id") or ""),
            str(entry_row.get("mikan_bangumi_id") or ""),
            str(entry_row.get("tmdb_id") or ""),
            int(entry_row.get("year") or 0),
            int(entry_row.get("season_number") or 1),
            str(entry_row.get("poster_url") or ""),
            str(entry_row.get("summary") or ""),
            ts,
            ts,
        ),
    )
    entry_id = int(conn.execute("SELECT id FROM entries WHERE fingerprint=?", (fingerprint_key,)).fetchone()["id"])
    conn.execute(
        """
        INSERT INTO library_entries
          (entry_id, source_type, source_ref, wanted, archived, created_at, updated_at)
        VALUES (?, 'remote_scan', ?, 1, 0, ?, ?)
        ON CONFLICT(entry_id) DO UPDATE SET
          source_ref=CASE WHEN excluded.source_ref!='' THEN excluded.source_ref ELSE library_entries.source_ref END,
          wanted=1,
          archived=0,
          updated_at=excluded.updated_at
        """,
        (entry_id, source_ref, ts, ts),
    )
    return resolve_entry_series_id(conn, entry_id), entry_id


def upsert_scanned_download_artifact(item: dict, entry: dict, settings: dict[str, str]) -> int | None:
    provider = provider_key(settings)
    file_id = download_file_id(item)
    name = str(item.get("name") or Path(str(item.get("remote_path") or "")).name)
    path = str(item.get("remote_path") or name)
    if not file_id or not name:
        return None
    episode_number = parse_episode(name) or parse_episode(path) or 0
    if episode_number <= 0:
        return None
    with connect() as conn:
        series_id, entry_id = ensure_library_entry_for_reference(
            conn,
            entry_row=entry,
            display_title=str(entry.get("title_cn") or entry.get("title_raw") or name),
            source_ref=path,
        )
        release = conn.execute(
            """
            SELECT *
            FROM releases
            WHERE entry_id=? AND episode_number=?
            ORDER BY selected DESC, id DESC
            LIMIT 1
            """,
            (entry_id, episode_number),
        ).fetchone()
        if not release:
            guid = f"remote-import:pikpak:{file_id}"
            conn.execute(
                """
                INSERT INTO episodes
                  (series_id, entry_id, episode_number, title, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'downloaded', ?, ?)
                ON CONFLICT(series_id, episode_number) DO UPDATE SET
                  status='downloaded',
                  updated_at=excluded.updated_at
                """,
                (
                    series_id,
                    entry_id,
                    episode_number,
                    f"第{episode_number:02d}话",
                    now(),
                    now(),
                ),
            )
            conn.execute(
                """
                INSERT INTO releases
                  (series_id, entry_id, episode_number, guid, title, subtitle_group, resolution,
                   language, torrent_url, magnet, published_at, selected, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, '远端导入', '', '', '', '', '', 1, ?, ?)
                ON CONFLICT(guid) DO UPDATE SET
                  series_id=excluded.series_id,
                  entry_id=excluded.entry_id,
                  episode_number=excluded.episode_number,
                  title=excluded.title,
                  updated_at=excluded.updated_at
                """,
                (series_id, entry_id, episode_number, guid, name, now(), now()),
            )
            release = conn.execute("SELECT * FROM releases WHERE guid=?", (guid,)).fetchone()
        if not release:
            return None
        ts = now()
        existing = conn.execute(
            "SELECT id FROM download_artifacts WHERE provider=? AND provider_file_id=?",
            (provider, file_id),
        ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE download_artifacts
                SET release_id=?, series_id=?, entry_id=?, episode_number=?, remote_path=?, artifact_name=?,
                    status='available', updated_at=?
                WHERE id=?
                """,
                (release["id"], series_id, entry_id, episode_number, path, name, ts, existing["id"]),
            )
        else:
            conn.execute(
                """
                INSERT INTO download_artifacts
                  (task_id, release_id, series_id, entry_id, episode_number, provider, provider_file_id,
                   remote_path, artifact_name, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'available', ?, ?)
                """,
                (
                    synthetic_task_id(file_id),
                    release["id"],
                    series_id,
                    entry_id,
                    episode_number,
                    provider,
                    file_id,
                    path,
                    name,
                    ts,
                    ts,
                ),
            )
        asset = conn.execute("SELECT id FROM download_artifacts WHERE provider_file_id=?", (file_id,)).fetchone()
    return int(asset["id"]) if asset else None


def upsert_download_artifact_for_release(release_id: int, item: dict, settings: dict[str, str]) -> int | None:
    provider = provider_key(settings)
    file_id = download_file_id(item)
    name = str(item.get("name") or Path(str(item.get("remote_path") or "")).name)
    path = str(item.get("remote_path") or name)
    if not name:
        return None
    with connect() as conn:
        release = conn.execute("SELECT * FROM releases WHERE id=?", (release_id,)).fetchone()
        if not release:
            return None
        entry = conn.execute("SELECT * FROM entries WHERE id=?", (release["entry_id"],)).fetchone()
        if not entry:
            return None
        ts = now()
        series_id = resolve_entry_series_id(conn, int(release["entry_id"]))
        provider_file_id = file_id or f"rclone:{path}"
        task_id = synthetic_task_id(provider_file_id)
        existing = conn.execute(
            "SELECT id FROM download_artifacts WHERE provider=? AND provider_file_id=?",
            (provider, provider_file_id),
        ).fetchone()
        existing_by_episode = conn.execute(
            """
            SELECT id
            FROM download_artifacts
            WHERE entry_id=? AND episode_number=? AND provider=?
            ORDER BY id ASC
            LIMIT 1
            """,
            (release["entry_id"], release["episode_number"], provider),
        ).fetchone()
        existing_asset = existing or existing_by_episode
        if existing_asset:
            conn.execute(
                """
                UPDATE download_artifacts
                SET task_id=?, release_id=?, series_id=?, entry_id=?, episode_number=?, provider_file_id=?,
                    remote_path=?, artifact_name=?, status='available', updated_at=?
                WHERE id=?
                """,
                (
                    task_id,
                    release["id"],
                    series_id,
                    release["entry_id"],
                    release["episode_number"],
                    provider_file_id,
                    path,
                    name,
                    ts,
                    existing_asset["id"],
                ),
            )
            asset_id = int(existing_asset["id"])
            log(
                "info",
                f"下载产物登记更新: download_artifact_id={asset_id} release_id={release['id']} "
                f"entry_id={release['entry_id']} episode={release['episode_number']} "
                f"file_id={provider_file_id or '-'} artifact_name={name}",
            )
        else:
            conn.execute(
                """
                INSERT INTO download_artifacts
                  (task_id, release_id, series_id, entry_id, episode_number, provider, provider_file_id,
                   remote_path, artifact_name, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'available', ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                  release_id=excluded.release_id,
                  series_id=excluded.series_id,
                  entry_id=excluded.entry_id,
                  episode_number=excluded.episode_number,
                  provider_file_id=excluded.provider_file_id,
                  remote_path=excluded.remote_path,
                  artifact_name=excluded.artifact_name,
                  status='available',
                  updated_at=excluded.updated_at
                """,
                (
                    task_id,
                    release["id"],
                    series_id,
                    release["entry_id"],
                    release["episode_number"],
                    provider,
                    provider_file_id,
                    path,
                    name,
                    ts,
                    ts,
                ),
            )
            asset = conn.execute("SELECT id FROM download_artifacts WHERE task_id=?", (task_id,)).fetchone()
            asset_id = int(asset["id"]) if asset else 0
            if asset_id:
                log("info", f"下载产物已入库: {name}")
        if asset_id:
            return asset_id
    return None


async def scan_remote_library(settings: dict[str, str]) -> tuple[int, int]:
    files = await list_remote_files(settings, settings.get("library_root") or "/Anime")
    with connect() as conn:
        entry_rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT e.*, w.title_root_raw
                FROM entries e
                JOIN library_entries le ON le.entry_id=e.id
                LEFT JOIN works w ON w.id=e.work_id
                WHERE COALESCE(e.hidden, 0)=0 AND e.bangumi_id != ''
                """
            ).fetchall()
        ]
    imported = 0
    skipped = 0
    synced_entries: set[int] = set()
    for item in files:
        name = str(item.get("name") or item.get("remote_path") or "")
        if Path(name).suffix.lower() not in VIDEO_SUFFIXES:
            continue
        entry = match_remote_file_to_entry(item, entry_rows)
        if not entry:
            skipped += 1
            continue
        asset_id = upsert_scanned_download_artifact(item, entry, settings)
        if not asset_id:
            skipped += 1
            continue
        imported += 1
        with connect() as conn:
            rule = conn.execute(
                "SELECT * FROM sync_rules WHERE entry_id=(SELECT entry_id FROM download_artifacts WHERE id=? LIMIT 1)",
                (asset_id,),
            ).fetchone()
        if rule and rule["sync_enabled"]:
            synced_entries.add(int(rule["entry_id"]))
    return imported, skipped


async def download_remote_file_to_local(file_id: str, source: str, target: str, settings: dict[str, str], progress_cb=None) -> None:
    await download_to_local(settings, file_id, source, target, progress_cb=progress_cb)


def cancel_sync_for_entry(entry_id: int) -> tuple[int, str]:
    removed = 0
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM local_assets WHERE entry_id=? AND status='synced'",
            (entry_id,),
        ).fetchall()
        for row in rows:
            path = Path(row["local_path"])
            try:
                if path.exists():
                    path.unlink()
                nfo_path = path.with_suffix(".nfo")
                if nfo_path.exists():
                    nfo_path.unlink()
                removed += 1
            except Exception as exc:
                log("warn", f"删除本地文件失败: {path} - {exc}")
            conn.execute(
                "UPDATE local_assets SET status='removed', updated_at=? WHERE id=?",
                (now(), row["id"]),
            )
        conn.execute(
            "UPDATE sync_rules SET sync_enabled=0, updated_at=? WHERE entry_id=?",
            (now(), entry_id),
        )
    return removed, f"已取消同步并清理本地文件: {removed} 个"


