from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from collections import deque
from threading import Lock
from typing import Any

from .config import DATA_DIR, DB_PATH, DEFAULT_SETTINGS
from .database import connect, initialize_database


LOG_PATH = DATA_DIR / "autoanime.log"
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


def ensure_pipeline_runtime(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS pipelines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            domain_kind TEXT NOT NULL DEFAULT 'seasonal',
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS pipeline_steps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pipeline_id INTEGER NOT NULL,
            step_key TEXT NOT NULL,
            processor_key TEXT NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0,
            enabled INTEGER NOT NULL DEFAULT 1,
            max_concurrency INTEGER NOT NULL DEFAULT 1,
            debounce_seconds INTEGER NOT NULL DEFAULT 10,
            retry_policy_json TEXT NOT NULL DEFAULT '',
            config_json TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(pipeline_id, step_key)
        );

        CREATE TABLE IF NOT EXISTS pipeline_transitions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pipeline_id INTEGER NOT NULL,
            from_step_key TEXT NOT NULL,
            result_status TEXT NOT NULL,
            to_step_key TEXT NOT NULL DEFAULT '',
            condition_json TEXT NOT NULL DEFAULT '',
            payload_map_json TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(pipeline_id, from_step_key, result_status, to_step_key)
        );

        CREATE INDEX IF NOT EXISTS idx_pipeline_steps_pipeline_order
            ON pipeline_steps(pipeline_id, sort_order);
        CREATE INDEX IF NOT EXISTS idx_pipeline_transitions_lookup
            ON pipeline_transitions(pipeline_id, from_step_key, result_status);
        """
    )
    ts = now()
    pipelines = [
        ("seasonal_mikan_tracking", "Mikan 新番追更", "seasonal"),
        ("library_backfill", "番剧库补番", "library"),
        ("media_import", "媒体导入", "media_import"),
    ]
    for key, name, domain_kind in pipelines:
        conn.execute(
            """
            INSERT INTO pipelines (key, name, domain_kind, enabled, created_at, updated_at)
            VALUES (?, ?, ?, 1, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
              name=excluded.name,
              domain_kind=excluded.domain_kind,
              updated_at=excluded.updated_at
            """,
            (key, name, domain_kind, ts, ts),
        )
    step_map = {
        "seasonal_mikan_tracking": [
            ("rss_fetch", "rss_fetch"),
            ("rss_candidate_persist", "rss_candidate_persist"),
            ("mikan_match", "mikan_match"),
            ("bangumi_metadata", "metadata"),
            ("seasonal_merge", "seasonal_merge"),
            ("season_backfill", "backfill"),
            ("release_selection", "selection"),
            ("download", "download"),
            ("nfo_generate", "nfo"),
            ("local_presence", "local_presence"),
        ],
        "library_backfill": [
            ("source_search", "source_search"),
            ("candidate_persist", "rss_candidate_persist"),
            ("bangumi_metadata", "metadata"),
            ("library_merge", "library_merge"),
            ("season_backfill", "backfill"),
            ("release_selection", "selection"),
            ("download", "download"),
            ("nfo_generate", "nfo"),
        ],
        "media_import": [
            ("source_scan", "source_scan"),
            ("identity_match", "identity_match"),
            ("bangumi_metadata", "metadata"),
            ("library_merge", "library_merge"),
            ("local_sync", "local_sync"),
            ("nfo_generate", "nfo"),
        ],
    }
    processor_concurrency = {
        "rss_fetch": 1,
        "rss_candidate_persist": 4,
        "mikan_match": 4,
        "metadata": 4,
        "seasonal_merge": 2,
        "library_merge": 2,
        "backfill": 2,
        "selection": 3,
        "download": 2,
        "local_sync": 2,
        "nfo": 1,
        "local_presence": 2,
    }
    for pipeline_key, steps in step_map.items():
        pipeline = conn.execute("SELECT id FROM pipelines WHERE key=?", (pipeline_key,)).fetchone()
        if not pipeline:
            continue
        pipeline_id = int(pipeline["id"])
        conn.execute("DELETE FROM pipeline_transitions WHERE pipeline_id=?", (pipeline_id,))
        step_keys = [step_key for step_key, _ in steps]
        placeholders = ",".join("?" for _ in step_keys)
        conn.execute(
            f"DELETE FROM pipeline_steps WHERE pipeline_id=? AND step_key NOT IN ({placeholders})",
            (pipeline_id, *step_keys),
        )
        for order, (step_key, processor_key) in enumerate(steps, start=1):
            conn.execute(
                """
                INSERT INTO pipeline_steps
                  (pipeline_id, step_key, processor_key, sort_order, max_concurrency, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(pipeline_id, step_key) DO UPDATE SET
                  processor_key=excluded.processor_key,
                  sort_order=excluded.sort_order,
                  max_concurrency=excluded.max_concurrency,
                  updated_at=excluded.updated_at
                """,
                (pipeline_id, step_key, processor_key, order, processor_concurrency.get(processor_key, 1), ts, ts),
            )
            if order < len(steps):
                next_step = steps[order][0]
                conn.execute(
                    """
                    INSERT INTO pipeline_transitions
                      (pipeline_id, from_step_key, result_status, to_step_key, created_at, updated_at)
                    VALUES (?, ?, 'success', ?, ?, ?)
                    ON CONFLICT(pipeline_id, from_step_key, result_status, to_step_key) DO UPDATE SET
                      updated_at=excluded.updated_at
                    """,
                    (pipeline_id, step_key, next_step, ts, ts),
                )
        special_transitions = []
        for from_step, result_status, to_step in special_transitions:
            step_keys = {step_key for step_key, _ in steps}
            if from_step not in step_keys or to_step not in step_keys:
                continue
            conn.execute(
                """
                INSERT INTO pipeline_transitions
                  (pipeline_id, from_step_key, result_status, to_step_key, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(pipeline_id, from_step_key, result_status, to_step_key) DO UPDATE SET
                  updated_at=excluded.updated_at
                """,
                (pipeline_id, from_step, result_status, to_step, ts, ts),
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
                year INTEGER NOT NULL DEFAULT 0,
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
                year INTEGER NOT NULL DEFAULT 0,
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

            CREATE TABLE IF NOT EXISTS download_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                series_id INTEGER NOT NULL,
                entry_id INTEGER NOT NULL DEFAULT 0,
                episode_number INTEGER NOT NULL,
                release_id INTEGER NOT NULL,
                provider TEXT NOT NULL DEFAULT 'pikpak',
                download_task_id INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                submission_id TEXT NOT NULL DEFAULT '',
                provider_file_id TEXT NOT NULL DEFAULT '',
                target_dir TEXT NOT NULL DEFAULT '',
                normalized_name TEXT NOT NULL DEFAULT '',
                retry_after TEXT NOT NULL DEFAULT '',
                last_error TEXT NOT NULL DEFAULT '',
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
        ensure_media_libraries(conn)
        migrate(conn)


def ensure_media_libraries(conn: sqlite3.Connection) -> None:
    ts = now()
    local_root = DEFAULT_SETTINGS.get("local_library_root", "/media/autoanime").rstrip("/")
    libraries = [
        ("seasonal_anime", "新番库", "anime", f"{local_root}/Seasonal", "pikpak"),
        ("anime_library", "番剧库", "anime", f"{local_root}/Library", "pikpak"),
        ("movies", "电影库", "movie", "/media/movies", "pikpak"),
        ("tv", "剧集库", "tv", "/media/tv", "pikpak"),
    ]
    for key, name, media_type, root_path, download_strategy in libraries:
        conn.execute(
            """
            INSERT INTO media_libraries
              (key, name, media_type, root_path, enabled, download_strategy, metadata_provider_priority,
               naming_template, created_at, updated_at)
            VALUES (?, ?, ?, ?, 1, ?, 'bangumi,tmdb,manual', '', ?, ?)
            ON CONFLICT(key) DO UPDATE SET
              name=excluded.name,
              media_type=excluded.media_type,
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
            year INTEGER NOT NULL DEFAULT 0,
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
    }
    for column, ddl in entry_additions.items():
        if column not in entry_columns:
            conn.execute(f"ALTER TABLE entries ADD COLUMN {column} {ddl}")
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
            episode_number INTEGER NOT NULL,
            release_id INTEGER NOT NULL,
            provider TEXT NOT NULL DEFAULT 'pikpak',
            download_task_id INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            submission_id TEXT NOT NULL DEFAULT '',
            provider_file_id TEXT NOT NULL DEFAULT '',
            target_dir TEXT NOT NULL DEFAULT '',
            normalized_name TEXT NOT NULL DEFAULT '',
            retry_after TEXT NOT NULL DEFAULT '',
            last_error TEXT NOT NULL DEFAULT '',
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
    episode_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(episodes)").fetchall()
    }
    if "entry_id" not in episode_columns:
        conn.execute("ALTER TABLE episodes ADD COLUMN entry_id INTEGER NOT NULL DEFAULT 0")
    for table in ["download_jobs", "download_artifacts", "sync_rules", "local_assets"]:
        columns = {
            row["name"]
            for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if "entry_id" not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN entry_id INTEGER NOT NULL DEFAULT 0")
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


def table_count(conn: sqlite3.Connection, table: str) -> int:
    try:
        row = conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()
    except sqlite3.Error:
        return -1
    return int(row["count"]) if row else -1


def diagnostics() -> dict[str, Any]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db_exists = DB_PATH.exists()
    db_size = DB_PATH.stat().st_size if db_exists else 0
    result: dict[str, Any] = {
        "data_dir": str(DATA_DIR),
        "db_path": str(DB_PATH),
        "db_exists": db_exists,
        "db_size": db_size,
        "data_dir_exists": DATA_DIR.exists(),
        "data_dir_writable": False,
        "tables": {},
        "settings_sample": {},
    }
    probe = DATA_DIR / ".write-test"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        result["data_dir_writable"] = True
    except OSError as exc:
        result["write_error"] = str(exc)
    with connect() as conn:
        tables = [
            "settings",
            "pipelines",
            "pipeline_steps",
            "pipeline_transitions",
            "media_libraries",
            "works",
            "entries",
            "seasonal_entries",
            "library_entries",
            "series",
            "episodes",
            "releases",
            "rss_candidates",
            "download_jobs",
            "download_artifacts",
            "sync_rules",
            "local_assets",
        ]
        result["tables"] = {table: table_count(conn, table) for table in tables}
        total_series = max(0, table_count(conn, "series"))
        result["hidden_series"] = max(0, total_series - table_count_visible_series(conn))
        result["hidden_legacy_series"] = result["hidden_series"]
        result["tables"]["legacy_series"] = result["tables"].get("series", 0)
        for key in ["rss_url", "library_root", "local_library_root", "auto_scan", "auto_sync_following"]:
            row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            result["settings_sample"][key] = row["value"] if row else None
    return result


def table_count_visible_series(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute("SELECT COUNT(*) AS count FROM series WHERE COALESCE(hidden, 0)=0").fetchone()
    except sqlite3.Error:
        return 0
    return int(row["count"]) if row else 0


def clear_runtime_data() -> None:
    next_generation = now()
    with connect() as conn:
        for table in [
            "local_assets",
            "sync_rules",
            "download_artifacts",
            "download_jobs",
            "rss_candidates",
            "library_entries",
            "seasonal_entries",
            "entries",
            "works",
            "releases",
            "episodes",
            "series",
        ]:
            conn.execute(f"DELETE FROM {table}")
        conn.execute("DELETE FROM sqlite_sequence WHERE name IN ('local_assets','sync_rules','download_artifacts','download_jobs','rss_candidates','library_entries','seasonal_entries','entries','works','releases','episodes','series')")
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('runtime_generation', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (next_generation,),
        )



