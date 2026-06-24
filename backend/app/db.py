from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from collections import deque
from pathlib import Path
from threading import Lock
from typing import Any

from .config import DATA_DIR, DEFAULT_SETTINGS, MEDIA_ROOT
from .database import connect, initialize_database
from .pipeline_schema import ensure_pipeline_runtime


LOG_BUFFER: deque[str] = deque(maxlen=2000)
LOG_LOCK = Lock()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_unique_index(conn: sqlite3.Connection, table: str, columns: list[str], index_name: str) -> None:
    table_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    if not table_exists:
        return
    existing_columns = {
        row["name"]
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }
    if "id" not in existing_columns or any(column not in existing_columns for column in columns):
        return
    column_sql = ", ".join(columns)
    null_guard = " AND ".join(f"{column} IS NOT NULL" for column in columns)
    conn.execute(
        f"""
        DELETE FROM {table}
        WHERE id NOT IN (
            SELECT MAX(id)
            FROM {table}
            WHERE {null_guard}
            GROUP BY {column_sql}
        )
        """
    )
    conn.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS {index_name} ON {table} ({column_sql})")


def ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    table_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    if not table_exists:
        return
    columns = {
        row["name"]
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def seed_schedules(conn: sqlite3.Connection) -> None:
    ts = now()
    settings = {row["key"]: row["value"] for row in conn.execute("SELECT key, value FROM settings").fetchall()}
    rss_enabled = 1 if str(settings.get("auto_scan", "false")).lower() == "true" else 0
    try:
        rss_interval = max(1, int(settings.get("scan_interval_minutes") or 60))
    except ValueError:
        rss_interval = 60
    defaults = [
        ("rss_scan", "RSS 定时扫描", "rss_scan", rss_enabled, rss_interval),
        ("rss_cache_cleanup", "清理 RSS 缓存", "rss_cache_cleanup", 1, 1440),
        ("expired_cache_cleanup", "清理过期缓存", "expired_cache_cleanup", 1, 1440),
    ]
    for key, name, action, enabled, interval in defaults:
        conn.execute(
            """
            INSERT INTO schedules
              (key, name, action, enabled, interval_minutes, config_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, '{}', ?, ?)
            ON CONFLICT(key) DO UPDATE SET
              name=CASE WHEN schedules.name='' THEN excluded.name ELSE schedules.name END,
              action=CASE WHEN schedules.action='' THEN excluded.action ELSE schedules.action END,
              updated_at=schedules.updated_at
            """,
            (key, name, action, enabled, interval, ts, ts),
        )


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    initialize_database()
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS series (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fingerprint TEXT NOT NULL UNIQUE,
                title_raw TEXT NOT NULL,
                title_cn TEXT NOT NULL,
                title_romaji TEXT NOT NULL DEFAULT '',
                bangumi_id TEXT NOT NULL DEFAULT '',
                mikan_bangumi_id TEXT NOT NULL DEFAULT '',
                tmdb_id TEXT NOT NULL DEFAULT '',
                bangumi_score REAL NOT NULL DEFAULT 0,
                tmdb_score REAL NOT NULL DEFAULT 0,
                year INTEGER NOT NULL DEFAULT 0,
                month INTEGER NOT NULL DEFAULT 0,
                season_number INTEGER NOT NULL DEFAULT 1,
                poster_url TEXT NOT NULL DEFAULT '',
                poster_path TEXT NOT NULL DEFAULT '',
                summary TEXT NOT NULL DEFAULT '',
                metadata_source TEXT NOT NULL DEFAULT '',
                nfo_status TEXT NOT NULL DEFAULT 'pending',
                hidden INTEGER NOT NULL DEFAULT 0,
                auto_download TEXT NOT NULL DEFAULT 'inherit',
                selected_group TEXT NOT NULL DEFAULT '',
                selected_resolution TEXT NOT NULL DEFAULT '',
                backfill_mode TEXT NOT NULL DEFAULT 'inherit',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS works (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                root_key TEXT NOT NULL UNIQUE,
                title_root TEXT NOT NULL,
                title_root_raw TEXT NOT NULL DEFAULT '',
                bangumi_id TEXT NOT NULL DEFAULT '',
                metadata_source TEXT NOT NULL DEFAULT '',
                hidden INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS media_libraries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                media_type TEXT NOT NULL DEFAULT 'anime',
                root_path TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                download_strategy TEXT NOT NULL DEFAULT 'pikpak',
                metadata_provider_priority TEXT NOT NULL DEFAULT 'bangumi,tmdb,manual',
                naming_template TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                work_id INTEGER NOT NULL,
                fingerprint TEXT NOT NULL UNIQUE,
                domain_kind TEXT NOT NULL DEFAULT 'seasonal',
                media_type TEXT NOT NULL DEFAULT 'anime',
                region TEXT NOT NULL DEFAULT 'jp',
                source_provider TEXT NOT NULL DEFAULT '',
                metadata_provider TEXT NOT NULL DEFAULT '',
                external_id TEXT NOT NULL DEFAULT '',
                target_library_id INTEGER NOT NULL DEFAULT 0,
                genres_json TEXT NOT NULL DEFAULT '[]',
                tags_json TEXT NOT NULL DEFAULT '[]',
                watch_status TEXT NOT NULL DEFAULT '',
                entry_kind TEXT NOT NULL DEFAULT 'season',
                display_title TEXT NOT NULL,
                title_root TEXT NOT NULL,
                season_label TEXT NOT NULL DEFAULT '',
                arc_label TEXT NOT NULL DEFAULT '',
                part_label TEXT NOT NULL DEFAULT '',
                special_label TEXT NOT NULL DEFAULT '',
                title_raw TEXT NOT NULL DEFAULT '',
                title_cn TEXT NOT NULL DEFAULT '',
                title_romaji TEXT NOT NULL DEFAULT '',
                bangumi_id TEXT NOT NULL DEFAULT '',
                mikan_bangumi_id TEXT NOT NULL DEFAULT '',
                tmdb_id TEXT NOT NULL DEFAULT '',
                bangumi_score REAL NOT NULL DEFAULT 0,
                tmdb_score REAL NOT NULL DEFAULT 0,
                year INTEGER NOT NULL DEFAULT 0,
                month INTEGER NOT NULL DEFAULT 0,
                season_number INTEGER NOT NULL DEFAULT 1,
                poster_url TEXT NOT NULL DEFAULT '',
                poster_path TEXT NOT NULL DEFAULT '',
                summary TEXT NOT NULL DEFAULT '',
                metadata_source TEXT NOT NULL DEFAULT '',
                nfo_status TEXT NOT NULL DEFAULT 'pending',
                hidden INTEGER NOT NULL DEFAULT 0,
                auto_download TEXT NOT NULL DEFAULT 'inherit',
                selected_group TEXT NOT NULL DEFAULT '',
                selected_resolution TEXT NOT NULL DEFAULT '',
                backfill_mode TEXT NOT NULL DEFAULT 'inherit',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS seasonal_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_id INTEGER NOT NULL UNIQUE,
                source_type TEXT NOT NULL DEFAULT 'mikan_rss',
                source_ref TEXT NOT NULL DEFAULT '',
                following INTEGER NOT NULL DEFAULT 1,
                sync_enabled INTEGER NOT NULL DEFAULT 1,
                archived INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS library_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_id INTEGER NOT NULL UNIQUE,
                source_type TEXT NOT NULL DEFAULT '',
                source_ref TEXT NOT NULL DEFAULT '',
                wanted INTEGER NOT NULL DEFAULT 1,
                archived INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS episodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                series_id INTEGER NOT NULL,
                entry_id INTEGER NOT NULL DEFAULT 0,
                episode_number INTEGER NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                air_date TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'missing',
                resource_ref TEXT NOT NULL DEFAULT '',
                subtitle_ref TEXT NOT NULL DEFAULT '',
                local_path TEXT NOT NULL DEFAULT '',
                subtitle_path TEXT NOT NULL DEFAULT '',
                watchable INTEGER NOT NULL DEFAULT 0,
                subtitle_group TEXT NOT NULL DEFAULT '',
                resolution TEXT NOT NULL DEFAULT '',
                language TEXT NOT NULL DEFAULT '',
                subtitle_format TEXT NOT NULL DEFAULT '',
                source_title TEXT NOT NULL DEFAULT '',
                source_type TEXT NOT NULL DEFAULT 'magnet',
                release_id INTEGER NOT NULL DEFAULT 0,
                last_download_job_id INTEGER NOT NULL DEFAULT 0,
                status_note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(series_id, episode_number)
            );

            CREATE TABLE IF NOT EXISTS releases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                series_id INTEGER NOT NULL,
                entry_id INTEGER NOT NULL DEFAULT 0,
                episode_number INTEGER NOT NULL,
                guid TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                subtitle_group TEXT NOT NULL DEFAULT '',
                resolution TEXT NOT NULL DEFAULT '',
                language TEXT NOT NULL DEFAULT '',
                subtitle_format TEXT NOT NULL DEFAULT '',
                torrent_url TEXT NOT NULL DEFAULT '',
                magnet TEXT NOT NULL DEFAULT '',
                published_at TEXT NOT NULL DEFAULT '',
                selected INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS episode_resources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_id INTEGER NOT NULL,
                episode_id INTEGER NOT NULL DEFAULT 0,
                episode_number INTEGER NOT NULL,
                source_type TEXT NOT NULL DEFAULT '',
                source_ref TEXT NOT NULL DEFAULT '',
                release_id INTEGER NOT NULL DEFAULT 0,
                title TEXT NOT NULL DEFAULT '',
                subtitle_group TEXT NOT NULL DEFAULT '',
                resolution TEXT NOT NULL DEFAULT '',
                language TEXT NOT NULL DEFAULT '',
                subtitle_format TEXT NOT NULL DEFAULT '',
                torrent_url TEXT NOT NULL DEFAULT '',
                magnet TEXT NOT NULL DEFAULT '',
                selected INTEGER NOT NULL DEFAULT 0,
                downloaded INTEGER NOT NULL DEFAULT 0,
                local_path TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'available',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(entry_id, episode_number, source_type, source_ref)
            );

            CREATE TABLE IF NOT EXISTS episode_subtitles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                episode_id INTEGER NOT NULL DEFAULT 0,
                episode_resource_id INTEGER NOT NULL DEFAULT 0,
                entry_id INTEGER NOT NULL,
                episode_number INTEGER NOT NULL,
                language TEXT NOT NULL DEFAULT '',
                subtitle_format TEXT NOT NULL DEFAULT '',
                subtitle_path TEXT NOT NULL DEFAULT '',
                subtitle_url TEXT NOT NULL DEFAULT '',
                file_name TEXT NOT NULL DEFAULT '',
                embedded INTEGER NOT NULL DEFAULT 0,
                selected INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS rss_candidates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guid TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                series_title TEXT NOT NULL DEFAULT '',
                episode_number INTEGER NOT NULL DEFAULT 0,
                subtitle_group TEXT NOT NULL DEFAULT '',
                resolution TEXT NOT NULL DEFAULT '',
                language TEXT NOT NULL DEFAULT '',
                subtitle_format TEXT NOT NULL DEFAULT '',
                bangumi_id TEXT NOT NULL DEFAULT '',
                mikan_bangumi_id TEXT NOT NULL DEFAULT '',
                torrent_url TEXT NOT NULL DEFAULT '',
                magnet TEXT NOT NULL DEFAULT '',
                page_url TEXT NOT NULL DEFAULT '',
                published_at TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                reason TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS rss_subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                url TEXT NOT NULL UNIQUE,
                kind TEXT NOT NULL DEFAULT 'mikan',
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS schedules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                action TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                interval_minutes INTEGER NOT NULL DEFAULT 60,
                config_json TEXT NOT NULL DEFAULT '{}',
                last_status TEXT NOT NULL DEFAULT 'idle',
                last_run_at TEXT NOT NULL DEFAULT '',
                last_error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS download_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                series_id INTEGER NOT NULL,
                entry_id INTEGER NOT NULL DEFAULT 0,
                episode_resource_id INTEGER NOT NULL DEFAULT 0,
                episode_id INTEGER NOT NULL DEFAULT 0,
                episode_number INTEGER NOT NULL,
                release_id INTEGER NOT NULL,
                provider TEXT NOT NULL DEFAULT 'pikpak',
                provider_index INTEGER NOT NULL DEFAULT 0,
                provider_key TEXT NOT NULL DEFAULT '',
                download_task_id INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'pending',
                phase TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                submission_id TEXT NOT NULL DEFAULT '',
                provider_file_id TEXT NOT NULL DEFAULT '',
                target_dir TEXT NOT NULL DEFAULT '',
                remote_path TEXT NOT NULL DEFAULT '',
                target_local_path TEXT NOT NULL DEFAULT '',
                normalized_name TEXT NOT NULL DEFAULT '',
                source_ref TEXT NOT NULL DEFAULT '',
                media_type TEXT NOT NULL DEFAULT 'anime',
                retry_after TEXT NOT NULL DEFAULT '',
                last_error TEXT NOT NULL DEFAULT '',
                progress INTEGER NOT NULL DEFAULT 0,
                progress_text TEXT NOT NULL DEFAULT '',
                total_size INTEGER NOT NULL DEFAULT 0,
                downloaded_size INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL DEFAULT '',
                UNIQUE(entry_id, episode_number, provider)
            );

            CREATE TABLE IF NOT EXISTS download_artifacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER NOT NULL UNIQUE,
                release_id INTEGER NOT NULL,
                series_id INTEGER NOT NULL,
                entry_id INTEGER NOT NULL DEFAULT 0,
                episode_number INTEGER NOT NULL,
                provider TEXT NOT NULL DEFAULT 'pikpak',
                provider_file_id TEXT NOT NULL DEFAULT '',
                remote_path TEXT NOT NULL DEFAULT '',
                artifact_name TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'available',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sync_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                series_id INTEGER NOT NULL UNIQUE,
                entry_id INTEGER NOT NULL DEFAULT 0 UNIQUE,
                sync_enabled INTEGER NOT NULL DEFAULT 0,
                auto_sync_following INTEGER NOT NULL DEFAULT 0,
                local_root TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS local_assets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                download_artifact_id INTEGER NOT NULL UNIQUE,
                release_id INTEGER NOT NULL,
                series_id INTEGER NOT NULL,
                entry_id INTEGER NOT NULL DEFAULT 0,
                episode_number INTEGER NOT NULL,
                local_path TEXT NOT NULL,
                nfo_status TEXT NOT NULL DEFAULT 'pending',
                status TEXT NOT NULL DEFAULT 'synced',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_download_artifacts_provider_file
            ON download_artifacts(provider, provider_file_id)
            WHERE provider_file_id != '';
            """
        )
        ensure_pipeline_runtime(conn)
        for key, value in DEFAULT_SETTINGS.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                (key, value),
            )
        seed_schedules(conn)
        for key, old_value, new_value in [
            ("series_dir_template", "{title_base} ({year}) [bangumi-{bangumi_id}]", "{title_base}"),
            ("series_dir_template", "{title_base}{year_suffix}", "{title_base}"),
            ("movie_name_template", "{title_cn} ({year})/{title_cn} ({year})", "{title_base}/{title_base}"),
            ("movie_name_template", "{title_base}{year_suffix}/{title_base}{year_suffix}", "{title_base}/{title_base}"),
            (
                "tv_name_template",
                "{title_cn} ({year})/Season {season:02d}/{title_cn} - S{season:02d}E{episode:02d}",
                "{title_base}/Season {season:02d}/{title_base} - S{season:02d}E{episode:02d} - 第 {episode:02d} 话",
            ),
            (
                "tv_name_template",
                "{title_base}{year_suffix}/Season {season:02d}/{title_base} - S{season:02d}E{episode:02d} - 第 {episode:02d} 话",
                "{title_base}/Season {season:02d}/{title_base} - S{season:02d}E{episode:02d} - 第 {episode:02d} 话",
            ),
            (
                "tv_name_template",
                "{title_base}/Season {season:02d}/{title_base} - S{season:02d}E{episode:02d}",
                "{title_base}/Season {season:02d}/{title_base} - S{season:02d}E{episode:02d} - 第 {episode:02d} 话",
            ),
            (
                "episode_name_template",
                "{title_cn} - S{season:02d}E{episode:02d} - {episode_title}",
                "{title_base} - S{season:02d}E{episode:02d} - 第 {episode:02d} 话",
            ),
        ]:
            conn.execute(
                "UPDATE settings SET value=? WHERE key=? AND value=?",
                (new_value, key, old_value),
            )
        ensure_media_libraries(conn)
        migrate(conn)


def ensure_media_libraries(conn: sqlite3.Connection) -> None:
    ts = now()
    media_root = Path(MEDIA_ROOT)
    anime_root = str(media_root / "anime")
    movies_root = str(media_root / "movies")
    tv_root = str(media_root / "tv")
    libraries = [
        ("seasonal_anime", "新番库", "anime", anime_root, "download"),
        ("anime_library", "番剧库", "anime", anime_root, "download"),
        ("movies", "电影库", "movie", movies_root, "download"),
        ("tv", "剧集库", "tv", tv_root, "download"),
    ]
    for key, name, media_type, root_path, download_strategy in libraries:
        try:
            Path(root_path).mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        conn.execute(
            """
            INSERT INTO media_libraries
              (key, name, media_type, root_path, enabled, download_strategy, metadata_provider_priority,
               naming_template, created_at, updated_at)
            VALUES (?, ?, ?, ?, 1, ?, 'bangumi,tmdb,manual', '', ?, ?)
            ON CONFLICT(key) DO UPDATE SET
              name=excluded.name,
              media_type=excluded.media_type,
              root_path=excluded.root_path,
              enabled=1,
              download_strategy=excluded.download_strategy,
              metadata_provider_priority=excluded.metadata_provider_priority,
              updated_at=excluded.updated_at
            """,
            (key, name, media_type, root_path, download_strategy, ts, ts),
        )


def migrate(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS works (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            root_key TEXT NOT NULL UNIQUE,
            title_root TEXT NOT NULL,
            title_root_raw TEXT NOT NULL DEFAULT '',
            bangumi_id TEXT NOT NULL DEFAULT '',
            metadata_source TEXT NOT NULL DEFAULT '',
            hidden INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS media_libraries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            media_type TEXT NOT NULL DEFAULT 'anime',
            root_path TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            download_strategy TEXT NOT NULL DEFAULT 'pikpak',
            metadata_provider_priority TEXT NOT NULL DEFAULT 'bangumi,tmdb,manual',
            naming_template TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    ensure_media_libraries(conn)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            work_id INTEGER NOT NULL,
            fingerprint TEXT NOT NULL UNIQUE,
            domain_kind TEXT NOT NULL DEFAULT 'seasonal',
            media_type TEXT NOT NULL DEFAULT 'anime',
            region TEXT NOT NULL DEFAULT 'jp',
            source_provider TEXT NOT NULL DEFAULT '',
            metadata_provider TEXT NOT NULL DEFAULT '',
            external_id TEXT NOT NULL DEFAULT '',
            target_library_id INTEGER NOT NULL DEFAULT 0,
            genres_json TEXT NOT NULL DEFAULT '[]',
            tags_json TEXT NOT NULL DEFAULT '[]',
            watch_status TEXT NOT NULL DEFAULT '',
            entry_kind TEXT NOT NULL DEFAULT 'season',
            display_title TEXT NOT NULL,
            title_root TEXT NOT NULL,
            season_label TEXT NOT NULL DEFAULT '',
            arc_label TEXT NOT NULL DEFAULT '',
            part_label TEXT NOT NULL DEFAULT '',
            special_label TEXT NOT NULL DEFAULT '',
            title_raw TEXT NOT NULL DEFAULT '',
            title_cn TEXT NOT NULL DEFAULT '',
            title_romaji TEXT NOT NULL DEFAULT '',
            bangumi_id TEXT NOT NULL DEFAULT '',
            mikan_bangumi_id TEXT NOT NULL DEFAULT '',
            tmdb_id TEXT NOT NULL DEFAULT '',
            bangumi_score REAL NOT NULL DEFAULT 0,
            tmdb_score REAL NOT NULL DEFAULT 0,
            year INTEGER NOT NULL DEFAULT 0,
            month INTEGER NOT NULL DEFAULT 0,
            season_number INTEGER NOT NULL DEFAULT 1,
            poster_url TEXT NOT NULL DEFAULT '',
            poster_path TEXT NOT NULL DEFAULT '',
            summary TEXT NOT NULL DEFAULT '',
            metadata_source TEXT NOT NULL DEFAULT '',
            nfo_status TEXT NOT NULL DEFAULT 'pending',
            hidden INTEGER NOT NULL DEFAULT 0,
            auto_download TEXT NOT NULL DEFAULT 'inherit',
            selected_group TEXT NOT NULL DEFAULT '',
            selected_resolution TEXT NOT NULL DEFAULT '',
            backfill_mode TEXT NOT NULL DEFAULT 'inherit',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS seasonal_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_id INTEGER NOT NULL UNIQUE,
            source_type TEXT NOT NULL DEFAULT 'mikan_rss',
            source_ref TEXT NOT NULL DEFAULT '',
            following INTEGER NOT NULL DEFAULT 1,
            sync_enabled INTEGER NOT NULL DEFAULT 1,
            archived INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS library_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_id INTEGER NOT NULL UNIQUE,
            source_type TEXT NOT NULL DEFAULT '',
            source_ref TEXT NOT NULL DEFAULT '',
            wanted INTEGER NOT NULL DEFAULT 1,
            archived INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    ensure_column(conn, "library_entries", "wanted", "INTEGER NOT NULL DEFAULT 1")
    series_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(series)").fetchall()
    }
    series_additions = {
        "poster_path": "TEXT NOT NULL DEFAULT ''",
        "metadata_source": "TEXT NOT NULL DEFAULT ''",
        "nfo_status": "TEXT NOT NULL DEFAULT 'pending'",
        "hidden": "INTEGER NOT NULL DEFAULT 0",
        "mikan_bangumi_id": "TEXT NOT NULL DEFAULT ''",
        "month": "INTEGER NOT NULL DEFAULT 0",
    }
    for column, ddl in series_additions.items():
        if column not in series_columns:
            conn.execute(f"ALTER TABLE series ADD COLUMN {column} {ddl}")

    release_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(releases)").fetchall()
    }
    release_additions = {
        "language": "TEXT NOT NULL DEFAULT ''",
        "subtitle_format": "TEXT NOT NULL DEFAULT ''",
        "entry_id": "INTEGER NOT NULL DEFAULT 0",
    }
    for column, ddl in release_additions.items():
        if column not in release_columns:
            conn.execute(f"ALTER TABLE releases ADD COLUMN {column} {ddl}")
    candidate_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(rss_candidates)").fetchall()
    }
    candidate_additions = {
        "subtitle_format": "TEXT NOT NULL DEFAULT ''",
    }
    for column, ddl in candidate_additions.items():
        if column not in candidate_columns:
            conn.execute(f"ALTER TABLE rss_candidates ADD COLUMN {column} {ddl}")
    entry_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(entries)").fetchall()
    }
    entry_additions = {
        "media_type": "TEXT NOT NULL DEFAULT 'anime'",
        "region": "TEXT NOT NULL DEFAULT 'jp'",
        "source_provider": "TEXT NOT NULL DEFAULT ''",
        "metadata_provider": "TEXT NOT NULL DEFAULT ''",
        "external_id": "TEXT NOT NULL DEFAULT ''",
        "target_library_id": "INTEGER NOT NULL DEFAULT 0",
        "genres_json": "TEXT NOT NULL DEFAULT '[]'",
        "tags_json": "TEXT NOT NULL DEFAULT '[]'",
        "watch_status": "TEXT NOT NULL DEFAULT ''",
        "month": "INTEGER NOT NULL DEFAULT 0",
        "bangumi_score": "REAL NOT NULL DEFAULT 0",
        "tmdb_score": "REAL NOT NULL DEFAULT 0",
    }
    for column, ddl in entry_additions.items():
        if column not in entry_columns:
            conn.execute(f"ALTER TABLE entries ADD COLUMN {column} {ddl}")
    subtitle_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(episode_subtitles)").fetchall()
    }
    subtitle_additions = {
        "subtitle_url": "TEXT NOT NULL DEFAULT ''",
        "file_name": "TEXT NOT NULL DEFAULT ''",
    }
    for column, ddl in subtitle_additions.items():
        if column not in subtitle_columns:
            conn.execute(f"ALTER TABLE episode_subtitles ADD COLUMN {column} {ddl}")
    seasonal_library = conn.execute("SELECT id FROM media_libraries WHERE key='seasonal_anime'").fetchone()
    archive_library = conn.execute("SELECT id FROM media_libraries WHERE key='anime_library'").fetchone()
    if seasonal_library:
        conn.execute(
            """
            UPDATE entries
            SET target_library_id=?,
                media_type=CASE WHEN media_type='' THEN 'anime' ELSE media_type END,
                region=CASE WHEN region='' THEN 'jp' ELSE region END,
                source_provider=CASE WHEN source_provider='' THEN 'mikan' ELSE source_provider END
            WHERE domain_kind='seasonal' AND COALESCE(target_library_id, 0)=0
            """,
            (int(seasonal_library["id"]),),
        )
    if archive_library:
        conn.execute(
            """
            UPDATE entries
            SET target_library_id=?,
                media_type=CASE WHEN media_type='' THEN 'anime' ELSE media_type END,
                region=CASE WHEN region='' THEN 'jp' ELSE region END,
                source_provider=CASE WHEN source_provider='' THEN 'manual' ELSE source_provider END
            WHERE domain_kind='library' AND COALESCE(target_library_id, 0)=0
            """,
            (int(archive_library["id"]),),
        )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS download_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            series_id INTEGER NOT NULL,
            entry_id INTEGER NOT NULL DEFAULT 0,
            episode_resource_id INTEGER NOT NULL DEFAULT 0,
            episode_id INTEGER NOT NULL DEFAULT 0,
            episode_number INTEGER NOT NULL,
            release_id INTEGER NOT NULL,
            provider TEXT NOT NULL DEFAULT 'pikpak',
            provider_index INTEGER NOT NULL DEFAULT 0,
            provider_key TEXT NOT NULL DEFAULT '',
            download_task_id INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'pending',
            phase TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            submission_id TEXT NOT NULL DEFAULT '',
            provider_file_id TEXT NOT NULL DEFAULT '',
            target_dir TEXT NOT NULL DEFAULT '',
            remote_path TEXT NOT NULL DEFAULT '',
            target_local_path TEXT NOT NULL DEFAULT '',
            normalized_name TEXT NOT NULL DEFAULT '',
            source_ref TEXT NOT NULL DEFAULT '',
            media_type TEXT NOT NULL DEFAULT 'anime',
            retry_after TEXT NOT NULL DEFAULT '',
            last_error TEXT NOT NULL DEFAULT '',
            progress INTEGER NOT NULL DEFAULT 0,
            progress_text TEXT NOT NULL DEFAULT '',
            total_size INTEGER NOT NULL DEFAULT 0,
            downloaded_size INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL DEFAULT '',
            UNIQUE(entry_id, episode_number, provider)
        )
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_download_artifacts_provider_file
        ON download_artifacts(provider, provider_file_id)
        WHERE provider_file_id != ''
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS rss_candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guid TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            series_title TEXT NOT NULL DEFAULT '',
            episode_number INTEGER NOT NULL DEFAULT 0,
            subtitle_group TEXT NOT NULL DEFAULT '',
            resolution TEXT NOT NULL DEFAULT '',
            language TEXT NOT NULL DEFAULT '',
            subtitle_format TEXT NOT NULL DEFAULT '',
            bangumi_id TEXT NOT NULL DEFAULT '',
            torrent_url TEXT NOT NULL DEFAULT '',
            magnet TEXT NOT NULL DEFAULT '',
            page_url TEXT NOT NULL DEFAULT '',
            published_at TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            reason TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    rss_candidate_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(rss_candidates)").fetchall()
    }
    rss_candidate_additions = {
        "page_url": "TEXT NOT NULL DEFAULT ''",
        "mikan_bangumi_id": "TEXT NOT NULL DEFAULT ''",
        "subtitle_format": "TEXT NOT NULL DEFAULT ''",
    }
    for column, ddl in rss_candidate_additions.items():
        if column not in rss_candidate_columns:
            conn.execute(f"ALTER TABLE rss_candidates ADD COLUMN {column} {ddl}")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS episode_resources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_id INTEGER NOT NULL,
            episode_id INTEGER NOT NULL DEFAULT 0,
            episode_number INTEGER NOT NULL,
            source_type TEXT NOT NULL DEFAULT '',
            source_ref TEXT NOT NULL DEFAULT '',
            release_id INTEGER NOT NULL DEFAULT 0,
            title TEXT NOT NULL DEFAULT '',
            subtitle_group TEXT NOT NULL DEFAULT '',
            resolution TEXT NOT NULL DEFAULT '',
            language TEXT NOT NULL DEFAULT '',
            subtitle_format TEXT NOT NULL DEFAULT '',
            torrent_url TEXT NOT NULL DEFAULT '',
            magnet TEXT NOT NULL DEFAULT '',
            selected INTEGER NOT NULL DEFAULT 0,
            downloaded INTEGER NOT NULL DEFAULT 0,
            local_path TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'available',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(entry_id, episode_number, source_type, source_ref)
        );

        CREATE TABLE IF NOT EXISTS episode_subtitles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            episode_id INTEGER NOT NULL DEFAULT 0,
            episode_resource_id INTEGER NOT NULL DEFAULT 0,
            entry_id INTEGER NOT NULL,
            episode_number INTEGER NOT NULL,
            language TEXT NOT NULL DEFAULT '',
            subtitle_format TEXT NOT NULL DEFAULT '',
            subtitle_path TEXT NOT NULL DEFAULT '',
            subtitle_url TEXT NOT NULL DEFAULT '',
            file_name TEXT NOT NULL DEFAULT '',
            embedded INTEGER NOT NULL DEFAULT 0,
            selected INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS rss_subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            url TEXT NOT NULL UNIQUE,
            kind TEXT NOT NULL DEFAULT 'mikan',
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )
    episode_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(episodes)").fetchall()
    }
    if "entry_id" not in episode_columns:
        conn.execute("ALTER TABLE episodes ADD COLUMN entry_id INTEGER NOT NULL DEFAULT 0")
    episode_additions = {
        "resource_ref": "TEXT NOT NULL DEFAULT ''",
        "subtitle_ref": "TEXT NOT NULL DEFAULT ''",
        "local_path": "TEXT NOT NULL DEFAULT ''",
        "subtitle_path": "TEXT NOT NULL DEFAULT ''",
        "watchable": "INTEGER NOT NULL DEFAULT 0",
        "subtitle_group": "TEXT NOT NULL DEFAULT ''",
        "resolution": "TEXT NOT NULL DEFAULT ''",
        "language": "TEXT NOT NULL DEFAULT ''",
        "subtitle_format": "TEXT NOT NULL DEFAULT ''",
        "source_title": "TEXT NOT NULL DEFAULT ''",
        "source_type": "TEXT NOT NULL DEFAULT 'magnet'",
        "release_id": "INTEGER NOT NULL DEFAULT 0",
        "last_download_job_id": "INTEGER NOT NULL DEFAULT 0",
        "status_note": "TEXT NOT NULL DEFAULT ''",
    }
    for column, ddl in episode_additions.items():
        if column not in episode_columns:
            conn.execute(f"ALTER TABLE episodes ADD COLUMN {column} {ddl}")
    for table in ["download_jobs", "download_artifacts", "sync_rules", "local_assets"]:
        columns = {
            row["name"]
            for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if "entry_id" not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN entry_id INTEGER NOT NULL DEFAULT 0")
        if table == "download_jobs":
            additions = {
                "episode_resource_id": "INTEGER NOT NULL DEFAULT 0",
                "episode_id": "INTEGER NOT NULL DEFAULT 0",
                "media_type": "TEXT NOT NULL DEFAULT 'anime'",
                "provider_index": "INTEGER NOT NULL DEFAULT 0",
                "provider_key": "TEXT NOT NULL DEFAULT ''",
                "phase": "TEXT NOT NULL DEFAULT 'pending'",
                "source_ref": "TEXT NOT NULL DEFAULT ''",
                "remote_path": "TEXT NOT NULL DEFAULT ''",
                "target_local_path": "TEXT NOT NULL DEFAULT ''",
                "total_size": "INTEGER NOT NULL DEFAULT 0",
                "downloaded_size": "INTEGER NOT NULL DEFAULT 0",
            }
            for column, ddl in additions.items():
                if column not in columns:
                    conn.execute(f"ALTER TABLE download_jobs ADD COLUMN {column} {ddl}")
            if "progress" not in columns:
                conn.execute("ALTER TABLE download_jobs ADD COLUMN progress INTEGER NOT NULL DEFAULT 0")
            if "progress_text" not in columns:
                conn.execute("ALTER TABLE download_jobs ADD COLUMN progress_text TEXT NOT NULL DEFAULT ''")
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_download_jobs_entry_episode_status
        ON download_jobs(entry_id, episode_number, status)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_download_jobs_resource
        ON download_jobs(episode_resource_id)
        """
    )
    ensure_pipeline_runtime(conn)
    merge_duplicate_series(conn)


def merge_duplicate_series(conn: sqlite3.Connection) -> None:
    duplicate_groups = conn.execute(
        """
        SELECT bangumi_id, GROUP_CONCAT(id) AS ids
        FROM series
        WHERE bangumi_id != ''
        GROUP BY bangumi_id
        HAVING COUNT(*) > 1
        """
    ).fetchall()
    for group in duplicate_groups:
        ids = [int(value) for value in group["ids"].split(",")]
        keep_id = min(ids)
        remove_ids = [value for value in ids if value != keep_id]
        for old_id in remove_ids:
            conn.execute("UPDATE releases SET series_id=? WHERE series_id=?", (keep_id, old_id))
            conn.execute("UPDATE download_artifacts SET series_id=? WHERE series_id=?", (keep_id, old_id))
            conn.execute("UPDATE local_assets SET series_id=? WHERE series_id=?", (keep_id, old_id))
            conn.execute("UPDATE download_jobs SET series_id=? WHERE series_id=?", (keep_id, old_id))
            conn.execute(
                """
                INSERT INTO sync_rules
                  (series_id, sync_enabled, auto_sync_following, local_root, created_at, updated_at)
                SELECT ?, sync_enabled, auto_sync_following, local_root, created_at, updated_at
                FROM sync_rules
                WHERE series_id=?
                ON CONFLICT(series_id) DO UPDATE SET
                  sync_enabled=MAX(sync_rules.sync_enabled, excluded.sync_enabled),
                  auto_sync_following=MAX(sync_rules.auto_sync_following, excluded.auto_sync_following),
                  local_root=CASE WHEN sync_rules.local_root='' THEN excluded.local_root ELSE sync_rules.local_root END,
                  updated_at=excluded.updated_at
                """,
                (keep_id, old_id),
            )
            old_episodes = conn.execute(
                "SELECT * FROM episodes WHERE series_id=?",
                (old_id,),
            ).fetchall()
            for ep in old_episodes:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO episodes
                      (series_id, episode_number, title, air_date, status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        keep_id,
                        ep["episode_number"],
                        ep["title"],
                        ep["air_date"],
                        ep["status"],
                        ep["created_at"],
                        ep["updated_at"],
                    ),
            )
            conn.execute("UPDATE download_jobs SET series_id=? WHERE series_id=?", (keep_id, old_id))
            conn.execute("DELETE FROM episodes WHERE series_id=?", (old_id,))
            conn.execute("DELETE FROM sync_rules WHERE series_id=?", (old_id,))
            conn.execute("DELETE FROM series WHERE id=?", (old_id,))


def restore_visible_series(conn: sqlite3.Connection) -> int:
    cursor = conn.execute(
        """
        UPDATE series
        SET hidden=0, updated_at=?
        WHERE COALESCE(hidden, 0)=1
          AND (
            id IN (SELECT DISTINCT series_id FROM releases)
            OR id IN (SELECT DISTINCT series_id FROM download_artifacts)
            OR id IN (SELECT DISTINCT series_id FROM local_assets)
          )
        """,
        (now(),),
    )
    return cursor.rowcount


def hide_orphan_series(conn: sqlite3.Connection) -> int:
    cursor = conn.execute(
        """
        UPDATE series
        SET hidden=1, updated_at=?
        WHERE COALESCE(hidden, 0)=0
          AND id NOT IN (SELECT DISTINCT series_id FROM releases)
          AND id NOT IN (SELECT DISTINCT series_id FROM download_artifacts)
          AND id NOT IN (SELECT DISTINCT series_id FROM local_assets)
        """,
        (now(),),
    )
    return cursor.rowcount


def get_settings() -> dict[str, str]:
    cfg = dict(DEFAULT_SETTINGS)
    with connect() as conn:
        for row in conn.execute("SELECT key, value FROM settings"):
            cfg[row["key"]] = row["value"]
    return cfg


def save_settings(values: dict[str, Any]) -> None:
    with connect() as conn:
        for key, value in values.items():
            if key in DEFAULT_SETTINGS:
                conn.execute(
                    "INSERT INTO settings (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, str(value)),
                )


def get_runtime_generation() -> str:
    with connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key='runtime_generation'").fetchone()
        return row["value"] if row and row["value"] else "0"


def bump_runtime_generation() -> str:
    value = now()
    with connect() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('runtime_generation', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (value,),
        )
    return value


def log(level: str, message: str) -> None:
    ts = now()
    normalized_level = level.lower()
    line = f"{ts} [{level.upper()}] {message[:2000]}"
    with LOG_LOCK:
        LOG_BUFFER.append(line)
    try:
        from .runtime_store import runtime_store

        runtime_store.append_log_sync(normalized_level, message)
    except Exception:
        pass


def read_server_logs(limit: int = 200) -> list[str]:
    with LOG_LOCK:
        return list(LOG_BUFFER)[-limit:]


def clear_server_logs() -> int:
    with LOG_LOCK:
        count = len(LOG_BUFFER)
        LOG_BUFFER.clear()
    return count
