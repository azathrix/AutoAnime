from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from .database import connect
from .db import get_settings, log, merge_duplicate_series, now
from .parser import fingerprint
from .pipeline_orchestrator import start_pipeline
from .runtime_service import ACTIVE_DOWNLOAD_STATUSES, DOWNLOAD_RUNTIME_PROCESSORS, trigger_queue
from .runtime_store import runtime_store
from .schemas import EntryPayload, MediaCreatePayload
from .utils import normalize_json_list_text, row_to_dict, subtitle_embedded_value, rows_to_dicts, enrich_catalog_entry

def normalize_api_media_type(value: str) -> str:
    key = str(value or "anime").strip().lower()
    if key in {"anime", "movie", "tv"}:
        return key
    raise HTTPException(status_code=404, detail="未知媒体类型")

def media_items_response(media_type: str) -> dict[str, Any]:
    media_type = normalize_api_media_type(media_type)
    from .dashboard_service import dashboard_data

    rows = dashboard_data().get("library_items", [])
    items = [
        item
        for item in rows
        if str(item.get("media_type") or "anime").lower() == media_type
    ]
    return {"type": media_type, "items": items}

def build_media_entry_response(media_type: str, entry_id: int) -> dict[str, Any]:
    media_type = normalize_api_media_type(media_type)
    detail = build_entry_response(entry_id)
    entry = detail.get("entry") or {}
    if not entry:
        raise HTTPException(status_code=404, detail="媒体条目不存在")
    entry_media_type = normalize_api_media_type(str(entry.get("media_type") or "anime"))
    if entry_media_type != media_type:
        raise HTTPException(status_code=404, detail="媒体条目类型不匹配")
    return detail

def media_library_key(media_type: str) -> str:
    return {
        "anime": "anime_library",
        "movie": "movies",
        "tv": "tv",
    }.get(media_type, "anime_library")

def create_media_entry(media_type: str, payload: MediaCreatePayload) -> dict[str, Any]:
    media_type = normalize_api_media_type(media_type)
    title = payload.title.strip() or payload.resource_title.strip() or payload.source_ref.strip() or "未命名媒体"
    bangumi_id = payload.bangumi_id.strip()
    tmdb_id = payload.tmdb_id.strip()
    season_number = max(1, int(payload.season_number or 1))
    year = max(0, int(payload.year or 0))
    month = max(0, min(12, int(payload.month or 0)))
    region = payload.region.strip() or "jp"
    source_ref = payload.source_ref.strip()
    ts = now()
    work_key = fingerprint(title, bangumi_id or tmdb_id)
    entry_key = fingerprint(f"{media_type}:{bangumi_id or tmdb_id or title}:S{season_number}", "")
    release_id = 0
    with connect() as conn:
        target_library = conn.execute("SELECT id FROM media_libraries WHERE key=?", (media_library_key(media_type),)).fetchone()
        target_library_id = int(target_library["id"] or 0) if target_library else 0
        conn.execute(
            """
            INSERT INTO works
              (root_key, title_root, title_root_raw, bangumi_id, metadata_source, hidden, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'manual', 0, ?, ?)
            ON CONFLICT(root_key) DO UPDATE SET
              title_root=excluded.title_root,
              title_root_raw=excluded.title_root_raw,
              bangumi_id=CASE WHEN works.bangumi_id='' THEN excluded.bangumi_id ELSE works.bangumi_id END,
              updated_at=excluded.updated_at
            """,
            (work_key, title, title, bangumi_id, ts, ts),
        )
        work = conn.execute("SELECT id FROM works WHERE root_key=?", (work_key,)).fetchone()
        work_id = int(work["id"] or 0)
        conn.execute(
            """
            INSERT INTO entries
              (work_id, fingerprint, domain_kind, media_type, region, source_provider, metadata_provider,
               external_id, target_library_id, display_title, title_root, title_raw, title_cn,
               bangumi_id, tmdb_id, year, month, season_number, created_at, updated_at)
            VALUES (?, ?, 'library', ?, ?, ?, 'manual', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(fingerprint) DO UPDATE SET
              media_type=excluded.media_type,
              region=excluded.region,
              target_library_id=excluded.target_library_id,
              display_title=excluded.display_title,
              title_root=excluded.title_root,
              title_cn=excluded.title_cn,
              bangumi_id=CASE WHEN entries.bangumi_id='' THEN excluded.bangumi_id ELSE entries.bangumi_id END,
              tmdb_id=CASE WHEN entries.tmdb_id='' THEN excluded.tmdb_id ELSE entries.tmdb_id END,
              year=CASE WHEN excluded.year>0 THEN excluded.year ELSE entries.year END,
              month=CASE WHEN excluded.month>0 THEN excluded.month ELSE entries.month END,
              updated_at=excluded.updated_at
            """,
            (
                work_id,
                entry_key,
                media_type,
                region,
                payload.mode.strip() or "manual",
                bangumi_id or tmdb_id,
                target_library_id,
                title,
                title,
                title,
                title,
                bangumi_id,
                tmdb_id,
                year,
                month,
                season_number,
                ts,
                ts,
            ),
        )
        entry = conn.execute("SELECT * FROM entries WHERE fingerprint=?", (entry_key,)).fetchone()
        entry_id = int(entry["id"] or 0)
        conn.execute(
            """
            INSERT INTO library_entries (entry_id, source_type, source_ref, wanted, archived, created_at, updated_at)
            VALUES (?, ?, ?, 1, 0, ?, ?)
            ON CONFLICT(entry_id) DO UPDATE SET
              source_type=excluded.source_type,
              source_ref=excluded.source_ref,
              wanted=1,
              archived=0,
              updated_at=excluded.updated_at
            """,
            (entry_id, payload.mode.strip() or "manual", source_ref, ts, ts),
        )
        episode_number = max(0, int(payload.episode_number or 0))
        if episode_number > 0 or payload.resource_title.strip() or source_ref:
            episode_number = episode_number or 1
            conn.execute(
                """
                INSERT INTO episodes (series_id, entry_id, episode_number, title, status, created_at, updated_at)
                VALUES (?, ?, ?, '', 'configured', ?, ?)
                ON CONFLICT(series_id, episode_number) DO UPDATE SET
                  entry_id=excluded.entry_id,
                  status=excluded.status,
                  updated_at=excluded.updated_at
                """,
                (entry_id, entry_id, episode_number, ts, ts),
            )
            episode = conn.execute(
                "SELECT id FROM episodes WHERE entry_id=? AND episode_number=? ORDER BY id DESC LIMIT 1",
                (entry_id, episode_number),
            ).fetchone()
            episode_id = int(episode["id"] or 0) if episode else 0
            resource_ref = source_ref or payload.resource_title.strip() or f"manual:{entry_id}:{episode_number}"
            torrent_url = source_ref if source_ref.startswith("http") else ""
            magnet = source_ref if source_ref.startswith("magnet:") else ""
            if torrent_url or magnet:
                digest = hashlib.sha1(resource_ref.encode("utf-8", errors="ignore")).hexdigest()[:20]
                guid = f"manual:{entry_id}:{episode_number}:{digest}"
                conn.execute(
                    "UPDATE releases SET selected=0 WHERE entry_id=? AND episode_number=?",
                    (entry_id, episode_number),
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
                        entry_id,
                        entry_id,
                        episode_number,
                        guid,
                        payload.resource_title.strip() or resource_ref,
                        payload.subtitle_group.strip(),
                        payload.resolution.strip(),
                        payload.language.strip(),
                        payload.subtitle_format.strip(),
                        torrent_url,
                        magnet,
                        ts,
                        ts,
                        ts,
                    ),
                )
                release = conn.execute("SELECT id FROM releases WHERE guid=?", (guid,)).fetchone()
                release_id = int(release["id"] or 0) if release else 0
                conn.execute(
                    "UPDATE episode_resources SET selected=0 WHERE entry_id=? AND episode_number=?",
                    (entry_id, episode_number),
                )
            conn.execute(
                """
                INSERT INTO episode_resources
                  (entry_id, episode_id, episode_number, source_type, source_ref, release_id, title,
                   subtitle_group, resolution, language, subtitle_format, torrent_url, magnet,
                   selected, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 'available', ?, ?)
                ON CONFLICT(entry_id, episode_number, source_type, source_ref) DO UPDATE SET
                  episode_id=excluded.episode_id,
                  release_id=excluded.release_id,
                  title=excluded.title,
                  subtitle_group=excluded.subtitle_group,
                  resolution=excluded.resolution,
                  language=excluded.language,
                  subtitle_format=excluded.subtitle_format,
                  torrent_url=excluded.torrent_url,
                  magnet=excluded.magnet,
                  selected=1,
                  status='available',
                  updated_at=excluded.updated_at
                """,
                (
                    entry_id,
                    episode_id,
                    episode_number,
                    payload.mode.strip() or "manual",
                    resource_ref,
                    release_id,
                    payload.resource_title.strip() or resource_ref,
                    payload.subtitle_group.strip(),
                    payload.resolution.strip(),
                    payload.language.strip(),
                    payload.subtitle_format.strip(),
                    torrent_url,
                    magnet,
                    ts,
                    ts,
                ),
            )
            resource_row = conn.execute(
                """
                SELECT id FROM episode_resources
                WHERE entry_id=? AND episode_number=? AND source_type=? AND source_ref=?
                """,
                (entry_id, episode_number, payload.mode.strip() or "manual", resource_ref),
            ).fetchone()
            episode_resource_id = int(resource_row["id"] or 0) if resource_row else 0
            if payload.subtitle_path.strip() or payload.subtitle_url.strip() or payload.subtitle_file_name.strip():
                conn.execute(
                    """
                    INSERT INTO episode_subtitles
                      (episode_id, episode_resource_id, entry_id, episode_number, language, subtitle_format,
                       subtitle_path, subtitle_url, file_name, embedded, selected, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        episode_id,
                        episode_resource_id,
                        entry_id,
                        episode_number,
                        payload.language.strip(),
                        payload.subtitle_format.strip(),
                        payload.subtitle_path.strip(),
                        payload.subtitle_url.strip(),
                        payload.subtitle_file_name.strip(),
                        subtitle_embedded_value(payload.subtitle_format),
                        ts,
                        ts,
                    ),
                )
    run_id = 0
    if release_id > 0:
        run_id = start_pipeline(
            "library_backfill",
            trigger_source="media_wizard",
            first_step_key="download",
            subject_type="release",
            subject_id=release_id,
            payload={
                "_dedupe_key": f"download:entry:{entry_id}:episode:{int(payload.episode_number or 0)}",
                "entry_id": entry_id,
                "release_id": release_id,
                "episode_number": int(payload.episode_number or 0),
                "domain_kind": "library",
            },
            message=f"媒体向导收录后下载: {title}",
        )
    log("info", f"媒体条目已收录: type={media_type} entry_id={entry_id} release_id={release_id} title={title}")
    detail = build_entry_response(entry_id)
    detail["download_run_id"] = run_id
    return detail

def empty_entry_response() -> dict[str, Any]:
    return {
        "entry": None,
        "episodes": [],
        "episode_resources": [],
        "episode_subtitles": [],
        "groups": [],
        "resolutions": [],
        "languages": [],
    }

def reset_orphaned_download_jobs_in_conn(conn, entry_id: int = 0, episode_number: int = 0) -> int:
    conditions = [f"status IN ({','.join('?' for _ in ACTIVE_DOWNLOAD_STATUSES)})"]
    params: list[Any] = list(ACTIVE_DOWNLOAD_STATUSES)
    if entry_id > 0:
        conditions.append("entry_id=?")
        params.append(entry_id)
    if episode_number > 0:
        conditions.append("episode_number=?")
        params.append(episode_number)
    rows = conn.execute(
        f"""
        SELECT id, entry_id, episode_number, status
        FROM download_jobs
        WHERE {' AND '.join(conditions)}
        """,
        tuple(params),
    ).fetchall()
    ts = now()
    reset_count = 0
    for row in rows:
        row_entry_id = int(row["entry_id"] or 0)
        row_episode_number = int(row["episode_number"] or 0)
        if runtime_store.has_active_episode_task(row_entry_id, row_episode_number, DOWNLOAD_RUNTIME_PROCESSORS):
            continue
        local_asset = conn.execute(
            """
            SELECT local_path
            FROM local_assets
            WHERE entry_id=? AND episode_number=? AND status='synced'
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            """,
            (row_entry_id, row_episode_number),
        ).fetchone()
        local_path = str(local_asset["local_path"] or "") if local_asset else ""
        local_exists = bool(local_path and Path(local_path).exists())
        if local_exists:
            conn.execute(
                """
                UPDATE download_jobs
                SET status='completed', retry_after='', last_error='', updated_at=?, last_seen_at=?
                WHERE id=?
                """,
                (ts, ts, row["id"]),
            )
            conn.execute(
                """
                UPDATE episode_resources
                SET downloaded=1, local_path=?, status='downloaded', updated_at=?
                WHERE entry_id=? AND episode_number=?
                """,
                (local_path, ts, row_entry_id, row_episode_number),
            )
        else:
            conn.execute(
                """
                UPDATE download_jobs
                SET status='failed', retry_after='', last_error='下载流程已中断，请重新下载', updated_at=?
                WHERE id=?
                """,
                (ts, row["id"]),
            )
            conn.execute(
                """
                UPDATE episode_resources
                SET status='available', updated_at=?
                WHERE entry_id=? AND episode_number=? AND selected=1 AND downloaded=0
                  AND status IN ('queued','downloading','remote_completed')
                """,
                (ts, row_entry_id, row_episode_number),
            )
        reset_count += 1
    return reset_count

def reset_orphaned_download_jobs(entry_id: int = 0, episode_number: int = 0) -> int:
    with connect() as conn:
        return reset_orphaned_download_jobs_in_conn(conn, entry_id, episode_number)

def build_entry_response(entry_id: int) -> dict[str, Any]:
    with connect() as conn:
        reset_orphaned_download_jobs_in_conn(conn, entry_id=entry_id)
        entry = conn.execute("SELECT * FROM entries WHERE id=?", (entry_id,)).fetchone()
        if not entry:
            return empty_entry_response()
        episodes = conn.execute(
            "SELECT * FROM episodes WHERE entry_id=? ORDER BY episode_number ASC, id ASC",
            (entry_id,),
        ).fetchall()
        episode_resources = conn.execute(
            """
            SELECT er.*,
              dj.id AS download_job_id,
              dj.status AS download_status,
              dj.retry_after AS download_retry_after,
              dj.last_error AS download_error,
              la.id AS local_asset_id,
              la.nfo_status AS local_nfo_status
            FROM episode_resources er
            LEFT JOIN download_jobs dj ON dj.id=(
              SELECT id
              FROM download_jobs
              WHERE entry_id=er.entry_id
                AND episode_number=er.episode_number
              ORDER BY CASE status
                WHEN 'running' THEN 0
                WHEN 'submitted' THEN 1
                WHEN 'pending' THEN 2
                WHEN 'paused' THEN 3
                WHEN 'failed' THEN 4
                WHEN 'cancelled' THEN 5
                WHEN 'completed' THEN 6
                ELSE 7
              END, updated_at DESC, id DESC
              LIMIT 1
            )
            LEFT JOIN local_assets la
              ON la.entry_id=er.entry_id
             AND la.episode_number=er.episode_number
             AND la.status='synced'
            WHERE er.entry_id=?
            ORDER BY er.episode_number ASC, er.selected DESC, er.id DESC
            """,
            (entry_id,),
        ).fetchall()
        episode_subtitles = conn.execute(
            "SELECT * FROM episode_subtitles WHERE entry_id=? ORDER BY episode_number ASC, selected DESC, id DESC",
            (entry_id,),
        ).fetchall()
    groups = sorted({r["subtitle_group"] for r in episode_resources if r["subtitle_group"]})
    resolutions = sorted({r["resolution"] for r in episode_resources if r["resolution"]})
    languages = sorted({r["language"] for r in episode_resources if r["language"]})
    entry_payload = enrich_catalog_entry({**row_to_dict(entry), "domain_kind": entry["domain_kind"]})
    for legacy_key in ("auto_download", "selected_group", "selected_resolution", "backfill_mode"):
        entry_payload.pop(legacy_key, None)
    return {
        "entry": entry_payload,
        "episodes": rows_to_dicts(episodes),
        "episode_resources": rows_to_dicts(episode_resources),
        "episode_subtitles": rows_to_dicts(episode_subtitles),
        "groups": groups,
        "resolutions": resolutions,
        "languages": languages,
    }

def save_entry_payload(entry_id: int, payload: EntryPayload, *, expected_domain: str | None = None) -> dict[str, Any]:
    with connect() as conn:
        entry = conn.execute("SELECT * FROM entries WHERE id=?", (entry_id,)).fetchone()
        if not entry:
            return empty_entry_response()
        domain_kind = str(entry["domain_kind"] or "")
        if expected_domain and domain_kind != expected_domain:
            return empty_entry_response()
        conn.execute(
            """
            UPDATE entries
            SET title_cn=?,
                bangumi_id=?,
                tmdb_id=?,
                year=?,
                month=?,
                season_number=?,
                media_type=?,
                region=?,
                title_romaji=?,
                title_raw=?,
                poster_url=?,
                summary=?,
                genres_json=?,
                tags_json=?,
                updated_at=?
            WHERE id=?
            """,
            (
                payload.title_cn.strip(),
                payload.bangumi_id.strip(),
                payload.tmdb_id.strip(),
                payload.year,
                max(0, min(12, int(payload.month or 0))),
                payload.season_number,
                normalize_api_media_type(payload.media_type),
                payload.region.strip() or "jp",
                payload.title_romaji.strip(),
                payload.title_raw.strip(),
                payload.poster_url.strip(),
                payload.summary.strip(),
                normalize_json_list_text(payload.genres_json),
                normalize_json_list_text(payload.tags_json),
                now(),
                entry_id,
            ),
        )
        should_refresh_seasonal = domain_kind == "seasonal"
        if domain_kind == "seasonal":
            conn.execute(
                """
                UPDATE series
                SET title_cn=?, bangumi_id=?, tmdb_id=?, year=?, month=?, season_number=?, updated_at=?
                WHERE bangumi_id=?
                """,
                (
                    payload.title_cn.strip(),
                    payload.bangumi_id.strip(),
                    payload.tmdb_id.strip(),
                    payload.year,
                    max(0, min(12, int(payload.month or 0))),
                    payload.season_number,
                    now(),
                    payload.bangumi_id.strip(),
                ),
            )
    if should_refresh_seasonal:
        start_pipeline(
            "seasonal_mikan_tracking",
            trigger_source="settings",
            first_step_key="release_selection",
            subject_type="entry",
            subject_id=entry_id,
            payload={"entry_id": entry_id, "domain_kind": "seasonal"},
            message="番剧规则变更，重新计算自动选集",
        )
        start_pipeline(
            "seasonal_mikan_tracking",
            trigger_source="settings",
            first_step_key="season_backfill",
            subject_type="entry",
            subject_id=entry_id,
            payload={"entry_id": entry_id, "domain_kind": "seasonal"},
            message="番剧规则变更，重新执行补全",
        )
        trigger_queue("processor", delay=0)
    if domain_kind == "seasonal":
        log("info", f"新番条目设置已保存: {payload.title_cn}")
        with connect() as conn:
            merge_duplicate_series(conn)
    else:
        log("info", f"番剧库条目已保存: {payload.title_cn}")
    return build_entry_response(entry_id)

def hide_entry(entry_id: int, *, expected_domain: str | None = None, success_message: str = "已隐藏条目，关联记录已保留", log_prefix: str = "已隐藏条目") -> dict[str, str]:
    with connect() as conn:
        entry = conn.execute(
            "SELECT display_title, domain_kind FROM entries WHERE id=?",
            (entry_id,),
        ).fetchone()
        if not entry:
            return {"status": "not_found", "message": "番剧不存在"}
        if expected_domain and entry["domain_kind"] != expected_domain:
            domain_label = "新番域" if expected_domain == "seasonal" else "番剧库"
            return {"status": "invalid_domain", "message": f"该条目不属于{domain_label}"}
        title = entry["display_title"]
        ts = now()
        conn.execute(
            "UPDATE entries SET hidden=1, updated_at=? WHERE id=?",
            (ts, entry_id),
        )
    log("warn", f"{log_prefix}: {title}")
    return {"status": "completed", "message": success_message}

def archive_seasonal_entry(entry_id: int) -> dict[str, str]:
    ts = now()
    with connect() as conn:
        entry = conn.execute(
            "SELECT display_title, domain_kind FROM entries WHERE id=?",
            (entry_id,),
        ).fetchone()
        if not entry:
            return {"status": "not_found", "message": "番剧不存在"}
        seasonal = conn.execute("SELECT id FROM seasonal_entries WHERE entry_id=?", (entry_id,)).fetchone()
        if not seasonal:
            return {"status": "invalid_domain", "message": "该条目不属于新番追番"}
        conn.execute(
            """
            UPDATE seasonal_entries
            SET following=0, archived=1, updated_at=?
            WHERE entry_id=?
            """,
            (ts, entry_id),
        )
        conn.execute(
            """
            INSERT INTO library_entries (entry_id, source_type, source_ref, wanted, archived, created_at, updated_at)
            VALUES (?, 'seasonal_archive', '', 1, 0, ?, ?)
            ON CONFLICT(entry_id) DO UPDATE SET
              wanted=1,
              archived=0,
              updated_at=excluded.updated_at
            """,
            (entry_id, ts, ts),
        )
        conn.execute(
            """
            UPDATE entries
            SET domain_kind='library',
                hidden=0,
                updated_at=?
            WHERE id=?
            """,
            (ts, entry_id),
        )
    log("info", f"新番已归档到番剧库: {entry['display_title']}")
    return {"status": "completed", "message": "已归档到番剧库"}
