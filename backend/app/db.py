from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from collections import deque
from typing import Any

from .config import DATA_DIR, DB_PATH, DEFAULT_SETTINGS
from .database import connect, initialize_database


LOG_PATH = DATA_DIR / "autoanime.log"


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

        CREATE TABLE IF NOT EXISTS pipeline_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pipeline_id INTEGER NOT NULL,
            trigger_source TEXT NOT NULL DEFAULT 'manual',
            status TEXT NOT NULL DEFAULT 'running',
            progress INTEGER NOT NULL DEFAULT 0,
            message TEXT NOT NULL DEFAULT '',
            stats_json TEXT NOT NULL DEFAULT '',
            started_at TEXT NOT NULL,
            finished_at TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS processor_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pipeline_id INTEGER NOT NULL DEFAULT 0,
            run_id INTEGER NOT NULL DEFAULT 0,
            step_id INTEGER NOT NULL DEFAULT 0,
            parent_task_id INTEGER NOT NULL DEFAULT 0,
            processor_key TEXT NOT NULL,
            domain_kind TEXT NOT NULL DEFAULT '',
            subject_type TEXT NOT NULL DEFAULT '',
            subject_id INTEGER NOT NULL DEFAULT 0,
            dedupe_key TEXT NOT NULL DEFAULT '',
            payload_json TEXT NOT NULL DEFAULT '',
            result_json TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            retry_after TEXT NOT NULL DEFAULT '',
            progress INTEGER NOT NULL DEFAULT 0,
            progress_text TEXT NOT NULL DEFAULT '',
            last_error TEXT NOT NULL DEFAULT '',
            locked_at TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS processor_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL DEFAULT 0,
            pipeline_id INTEGER NOT NULL DEFAULT 0,
            run_id INTEGER NOT NULL DEFAULT 0,
            processor_key TEXT NOT NULL DEFAULT '',
            level TEXT NOT NULL DEFAULT 'info',
            event_key TEXT NOT NULL DEFAULT '',
            message TEXT NOT NULL DEFAULT '',
            data_json TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_pipeline_steps_pipeline_order
            ON pipeline_steps(pipeline_id, sort_order);
        CREATE INDEX IF NOT EXISTS idx_pipeline_transitions_lookup
            ON pipeline_transitions(pipeline_id, from_step_key, result_status);
        CREATE INDEX IF NOT EXISTS idx_pipeline_runs_status
            ON pipeline_runs(pipeline_id, status, updated_at);
        CREATE INDEX IF NOT EXISTS idx_processor_tasks_status
            ON processor_tasks(status, retry_after, updated_at);
        CREATE INDEX IF NOT EXISTS idx_processor_tasks_pipeline_status
            ON processor_tasks(pipeline_id, status);
        CREATE INDEX IF NOT EXISTS idx_processor_tasks_run
            ON processor_tasks(run_id);
        CREATE INDEX IF NOT EXISTS idx_processor_tasks_processor
            ON processor_tasks(processor_key, status, retry_after);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_processor_tasks_dedupe
            ON processor_tasks(dedupe_key)
            WHERE dedupe_key != '';
        CREATE INDEX IF NOT EXISTS idx_processor_events_task
            ON processor_events(task_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_processor_events_run
            ON processor_events(run_id, created_at);
        """
    )
    for column, ddl in {
        "parent_task_id": "INTEGER NOT NULL DEFAULT 0",
        "dedupe_key": "TEXT NOT NULL DEFAULT ''",
        "result_json": "TEXT NOT NULL DEFAULT ''",
        "locked_at": "TEXT NOT NULL DEFAULT ''",
    }.items():
        ensure_column(conn, "processor_tasks", column, ddl)
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_processor_tasks_dedupe
        ON processor_tasks(dedupe_key)
        WHERE dedupe_key != ''
        """
    )
    ts = now()
    pipelines = [
        ("seasonal_mikan_tracking", "Mikan 新番追更", "seasonal"),
        ("library_backfill", "番剧库补番", "library"),
        ("cloud_import", "云盘导入", "cloud_import"),
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
            ("cloud_presence", "cloud_presence"),
            ("download_enqueue", "download_enqueue"),
            ("cloud_submit", "download"),
            ("cloud_poll", "cloud_poll"),
            ("cloud_asset_register", "cloud_asset"),
            ("sync_plan", "sync_plan"),
            ("local_sync", "sync"),
            ("nfo_generate", "nfo"),
            ("local_presence", "local_presence"),
        ],
        "library_backfill": [
            ("source_search", "source_search"),
            ("candidate_persist", "rss_candidate_persist"),
            ("bangumi_metadata", "metadata"),
            ("library_merge", "library_merge"),
            ("release_selection", "selection"),
            ("cloud_presence", "cloud_presence"),
            ("download_enqueue", "download_enqueue"),
            ("cloud_submit", "download"),
            ("cloud_poll", "cloud_poll"),
            ("cloud_asset_register", "cloud_asset"),
            ("sync_plan", "sync_plan"),
            ("local_sync", "sync"),
            ("nfo_generate", "nfo"),
        ],
        "cloud_import": [
            ("cloud_scan", "cloud_scan"),
            ("cloud_identity_match", "cloud_identity_match"),
            ("bangumi_metadata", "metadata"),
            ("library_merge", "library_merge"),
            ("cloud_asset_register", "cloud_asset"),
            ("sync_plan", "sync_plan"),
        ],
    }
    for pipeline_key, steps in step_map.items():
        pipeline = conn.execute("SELECT id FROM pipelines WHERE key=?", (pipeline_key,)).fetchone()
        if not pipeline:
            continue
        pipeline_id = int(pipeline["id"])
        conn.execute("DELETE FROM pipeline_transitions WHERE pipeline_id=?", (pipeline_id,))
        for order, (step_key, processor_key) in enumerate(steps, start=1):
            conn.execute(
                """
                INSERT INTO pipeline_steps
                  (pipeline_id, step_key, processor_key, sort_order, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(pipeline_id, step_key) DO UPDATE SET
                  processor_key=excluded.processor_key,
                  sort_order=excluded.sort_order,
                  updated_at=excluded.updated_at
                """,
                (pipeline_id, step_key, processor_key, order, ts, ts),
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
        special_transitions = [
            ("cloud_presence", "skipped", "sync_plan"),
        ]
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
    task_tables = [
        "mikan_match_tasks",
        "metadata_tasks",
        "selection_tasks",
        "backfill_tasks",
        "cloud_presence_tasks",
        "download_enqueue_tasks",
        "download_tasks",
        "cloud_poll_tasks",
        "cloud_asset_tasks",
        "sync_plan_tasks",
        "sync_tasks",
        "nfo_tasks",
        "local_presence_tasks",
        "cleanup_tasks",
    ]
    for table in task_tables:
        ensure_column(conn, table, "pipeline_id", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(conn, table, "run_id", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(conn, table, "processor_key", "TEXT NOT NULL DEFAULT ''")
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_pipeline_status ON {table}(pipeline_id, status)")
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_status_retry ON {table}(status, retry_after, updated_at)")


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

            CREATE TABLE IF NOT EXISTS entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                work_id INTEGER NOT NULL,
                fingerprint TEXT NOT NULL UNIQUE,
                domain_kind TEXT NOT NULL DEFAULT 'seasonal',
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

            CREATE TABLE IF NOT EXISTS download_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                release_id INTEGER NOT NULL,
                series_id INTEGER NOT NULL,
                entry_id INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                pikpak_task_id TEXT NOT NULL DEFAULT '',
                pikpak_file_id TEXT NOT NULL DEFAULT '',
                target_dir TEXT NOT NULL DEFAULT '',
                normalized_name TEXT NOT NULL DEFAULT '',
                retry_after TEXT NOT NULL DEFAULT '',
                last_error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(release_id)
            );

            CREATE TABLE IF NOT EXISTS selection_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                series_id INTEGER NOT NULL UNIQUE,
                entry_id INTEGER NOT NULL DEFAULT 0 UNIQUE,
                status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                reason TEXT NOT NULL DEFAULT '',
                retry_after TEXT NOT NULL DEFAULT '',
                last_error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS backfill_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                series_id INTEGER NOT NULL UNIQUE,
                entry_id INTEGER NOT NULL DEFAULT 0 UNIQUE,
                status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                backfill_mode TEXT NOT NULL DEFAULT 'none',
                retry_after TEXT NOT NULL DEFAULT '',
                last_error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS cloud_presence_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                release_id INTEGER NOT NULL UNIQUE,
                series_id INTEGER NOT NULL,
                entry_id INTEGER NOT NULL DEFAULT 0,
                episode_number INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                cloud_asset_id INTEGER NOT NULL DEFAULT 0,
                retry_after TEXT NOT NULL DEFAULT '',
                last_error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS download_enqueue_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                release_id INTEGER NOT NULL UNIQUE,
                series_id INTEGER NOT NULL,
                entry_id INTEGER NOT NULL DEFAULT 0,
                episode_number INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                retry_after TEXT NOT NULL DEFAULT '',
                last_error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS cloud_submissions (
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

            CREATE TABLE IF NOT EXISTS cloud_poll_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                download_task_id INTEGER NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                retry_after TEXT NOT NULL DEFAULT '',
                last_error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS cloud_asset_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                download_task_id INTEGER NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                retry_after TEXT NOT NULL DEFAULT '',
                last_error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS metadata_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                candidate_id INTEGER NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                bangumi_id TEXT NOT NULL DEFAULT '',
                retry_after TEXT NOT NULL DEFAULT '',
                last_error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS mikan_match_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                candidate_id INTEGER NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                mikan_url TEXT NOT NULL DEFAULT '',
                mikan_bangumi_id TEXT NOT NULL DEFAULT '',
                bangumi_id TEXT NOT NULL DEFAULT '',
                retry_after TEXT NOT NULL DEFAULT '',
                last_error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS cloud_assets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER NOT NULL UNIQUE,
                release_id INTEGER NOT NULL,
                series_id INTEGER NOT NULL,
                entry_id INTEGER NOT NULL DEFAULT 0,
                episode_number INTEGER NOT NULL,
                provider TEXT NOT NULL DEFAULT 'pikpak',
                provider_file_id TEXT NOT NULL DEFAULT '',
                cloud_path TEXT NOT NULL DEFAULT '',
                cloud_name TEXT NOT NULL DEFAULT '',
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
                cloud_asset_id INTEGER NOT NULL UNIQUE,
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

            CREATE TABLE IF NOT EXISTS sync_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cloud_asset_id INTEGER NOT NULL,
                release_id INTEGER NOT NULL,
                series_id INTEGER NOT NULL,
                entry_id INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                sync_direction TEXT NOT NULL DEFAULT 'cloud_to_local',
                source_path TEXT NOT NULL DEFAULT '',
                target_path TEXT NOT NULL DEFAULT '',
                progress INTEGER NOT NULL DEFAULT 0,
                progress_text TEXT NOT NULL DEFAULT '',
                retry_after TEXT NOT NULL DEFAULT '',
                last_error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(cloud_asset_id, sync_direction)
            );

            CREATE TABLE IF NOT EXISTS sync_plan_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_id INTEGER NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                retry_after TEXT NOT NULL DEFAULT '',
                last_error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS nfo_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                local_asset_id INTEGER NOT NULL UNIQUE,
                release_id INTEGER NOT NULL,
                series_id INTEGER NOT NULL,
                entry_id INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                retry_after TEXT NOT NULL DEFAULT '',
                last_error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS local_presence_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                local_asset_id INTEGER NOT NULL UNIQUE,
                release_id INTEGER NOT NULL,
                series_id INTEGER NOT NULL,
                entry_id INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                retry_after TEXT NOT NULL DEFAULT '',
                last_error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS cleanup_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_scope TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                retry_after TEXT NOT NULL DEFAULT '',
                last_error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                level TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS operations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'running',
                message TEXT NOT NULL DEFAULT '',
                started_at TEXT NOT NULL,
                finished_at TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS scheduled_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_key TEXT NOT NULL UNIQUE,
                job_type TEXT NOT NULL DEFAULT '',
                enabled INTEGER NOT NULL DEFAULT 1,
                interval_minutes INTEGER NOT NULL DEFAULT 0,
                debounce_seconds INTEGER NOT NULL DEFAULT 10,
                max_concurrency INTEGER NOT NULL DEFAULT 1,
                last_run_at TEXT NOT NULL DEFAULT '',
                next_run_at TEXT NOT NULL DEFAULT '',
                last_status TEXT NOT NULL DEFAULT '',
                last_error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS scheduled_job_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'running',
                trigger_source TEXT NOT NULL DEFAULT 'system',
                message TEXT NOT NULL DEFAULT '',
                stats_json TEXT NOT NULL DEFAULT '',
                started_at TEXT NOT NULL,
                finished_at TEXT NOT NULL DEFAULT ''
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_cloud_assets_provider_file
            ON cloud_assets(provider, provider_file_id)
            WHERE provider_file_id != '';
            """
        )
        ensure_pipeline_runtime(conn)
        for key, value in DEFAULT_SETTINGS.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                (key, value),
            )
        migrate(conn)


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
        CREATE TABLE IF NOT EXISTS entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            work_id INTEGER NOT NULL,
            fingerprint TEXT NOT NULL UNIQUE,
            domain_kind TEXT NOT NULL DEFAULT 'seasonal',
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
            archived INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
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
        "entry_id": "INTEGER NOT NULL DEFAULT 0",
    }
    for column, ddl in release_additions.items():
        if column not in release_columns:
            conn.execute(f"ALTER TABLE releases ADD COLUMN {column} {ddl}")
    download_task_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(download_tasks)").fetchall()
    }
    download_task_additions = {
        "retry_after": "TEXT NOT NULL DEFAULT ''",
        "entry_id": "INTEGER NOT NULL DEFAULT 0",
    }
    for column, ddl in download_task_additions.items():
        if column not in download_task_columns:
            conn.execute(f"ALTER TABLE download_tasks ADD COLUMN {column} {ddl}")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS selection_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            series_id INTEGER NOT NULL UNIQUE,
            entry_id INTEGER NOT NULL DEFAULT 0 UNIQUE,
            status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            reason TEXT NOT NULL DEFAULT '',
            retry_after TEXT NOT NULL DEFAULT '',
            last_error TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS backfill_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            series_id INTEGER NOT NULL UNIQUE,
            entry_id INTEGER NOT NULL DEFAULT 0 UNIQUE,
            status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            backfill_mode TEXT NOT NULL DEFAULT 'none',
            retry_after TEXT NOT NULL DEFAULT '',
            last_error TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS cloud_submissions (
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
        CREATE TABLE IF NOT EXISTS cloud_poll_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            download_task_id INTEGER NOT NULL UNIQUE,
            status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            retry_after TEXT NOT NULL DEFAULT '',
            last_error TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS cloud_asset_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            download_task_id INTEGER NOT NULL UNIQUE,
            status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            retry_after TEXT NOT NULL DEFAULT '',
            last_error TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_cloud_assets_provider_file
        ON cloud_assets(provider, provider_file_id)
        WHERE provider_file_id != ''
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS operations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'running',
            message TEXT NOT NULL DEFAULT '',
            started_at TEXT NOT NULL,
            finished_at TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS scheduled_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_key TEXT NOT NULL UNIQUE,
            job_type TEXT NOT NULL DEFAULT '',
            enabled INTEGER NOT NULL DEFAULT 1,
            interval_minutes INTEGER NOT NULL DEFAULT 0,
            debounce_seconds INTEGER NOT NULL DEFAULT 10,
            max_concurrency INTEGER NOT NULL DEFAULT 1,
            last_run_at TEXT NOT NULL DEFAULT '',
            next_run_at TEXT NOT NULL DEFAULT '',
            last_status TEXT NOT NULL DEFAULT '',
            last_error TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS scheduled_job_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'running',
            trigger_source TEXT NOT NULL DEFAULT 'system',
            message TEXT NOT NULL DEFAULT '',
            stats_json TEXT NOT NULL DEFAULT '',
            started_at TEXT NOT NULL,
            finished_at TEXT NOT NULL DEFAULT ''
        )
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
    }
    for column, ddl in rss_candidate_additions.items():
        if column not in rss_candidate_columns:
            conn.execute(f"ALTER TABLE rss_candidates ADD COLUMN {column} {ddl}")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS metadata_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            candidate_id INTEGER NOT NULL UNIQUE,
            status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            bangumi_id TEXT NOT NULL DEFAULT '',
            retry_after TEXT NOT NULL DEFAULT '',
            last_error TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    metadata_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(metadata_tasks)").fetchall()
    }
    metadata_additions = {
        "retry_after": "TEXT NOT NULL DEFAULT ''",
    }
    for column, ddl in metadata_additions.items():
        if column not in metadata_columns:
            conn.execute(f"ALTER TABLE metadata_tasks ADD COLUMN {column} {ddl}")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS mikan_match_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            candidate_id INTEGER NOT NULL UNIQUE,
            status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            mikan_url TEXT NOT NULL DEFAULT '',
            mikan_bangumi_id TEXT NOT NULL DEFAULT '',
            bangumi_id TEXT NOT NULL DEFAULT '',
            retry_after TEXT NOT NULL DEFAULT '',
            last_error TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    mikan_match_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(mikan_match_tasks)").fetchall()
    }
    mikan_match_additions = {
        "mikan_url": "TEXT NOT NULL DEFAULT ''",
        "mikan_bangumi_id": "TEXT NOT NULL DEFAULT ''",
        "bangumi_id": "TEXT NOT NULL DEFAULT ''",
        "retry_after": "TEXT NOT NULL DEFAULT ''",
    }
    for column, ddl in mikan_match_additions.items():
        if column not in mikan_match_columns:
            conn.execute(f"ALTER TABLE mikan_match_tasks ADD COLUMN {column} {ddl}")
    sync_task_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(sync_tasks)").fetchall()
    }
    sync_task_additions = {
        "entry_id": "INTEGER NOT NULL DEFAULT 0",
        "progress": "INTEGER NOT NULL DEFAULT 0",
        "progress_text": "TEXT NOT NULL DEFAULT ''",
        "retry_after": "TEXT NOT NULL DEFAULT ''",
    }
    for column, ddl in sync_task_additions.items():
        if column not in sync_task_columns:
            conn.execute(f"ALTER TABLE sync_tasks ADD COLUMN {column} {ddl}")
    episode_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(episodes)").fetchall()
    }
    if "entry_id" not in episode_columns:
        conn.execute("ALTER TABLE episodes ADD COLUMN entry_id INTEGER NOT NULL DEFAULT 0")
    for table in ["selection_tasks", "backfill_tasks", "cloud_submissions", "cloud_assets", "sync_rules", "local_assets"]:
        columns = {
            row["name"]
            for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if "entry_id" not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN entry_id INTEGER NOT NULL DEFAULT 0")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS nfo_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            local_asset_id INTEGER NOT NULL UNIQUE,
            release_id INTEGER NOT NULL,
            series_id INTEGER NOT NULL,
            entry_id INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            retry_after TEXT NOT NULL DEFAULT '',
            last_error TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sync_plan_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_id INTEGER NOT NULL UNIQUE,
            status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            retry_after TEXT NOT NULL DEFAULT '',
            last_error TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS local_presence_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            local_asset_id INTEGER NOT NULL UNIQUE,
            release_id INTEGER NOT NULL,
            series_id INTEGER NOT NULL,
            entry_id INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            retry_after TEXT NOT NULL DEFAULT '',
            last_error TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS cleanup_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_scope TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            retry_after TEXT NOT NULL DEFAULT '',
            last_error TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS cloud_presence_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            release_id INTEGER NOT NULL UNIQUE,
            series_id INTEGER NOT NULL,
            entry_id INTEGER NOT NULL DEFAULT 0,
            episode_number INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            cloud_asset_id INTEGER NOT NULL DEFAULT 0,
            retry_after TEXT NOT NULL DEFAULT '',
            last_error TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS download_enqueue_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            release_id INTEGER NOT NULL UNIQUE,
            series_id INTEGER NOT NULL,
            entry_id INTEGER NOT NULL DEFAULT 0,
            episode_number INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            retry_after TEXT NOT NULL DEFAULT '',
            last_error TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    ensure_unique_index(conn, "metadata_tasks", ["candidate_id"], "idx_metadata_tasks_candidate_unique")
    ensure_unique_index(conn, "mikan_match_tasks", ["candidate_id"], "idx_mikan_match_tasks_candidate_unique")
    ensure_unique_index(conn, "selection_tasks", ["release_id"], "idx_selection_tasks_release_unique")
    ensure_unique_index(conn, "backfill_tasks", ["entry_id"], "idx_backfill_tasks_entry_unique")
    ensure_unique_index(conn, "cloud_presence_tasks", ["release_id"], "idx_cloud_presence_tasks_release_unique")
    ensure_unique_index(conn, "download_enqueue_tasks", ["release_id"], "idx_download_enqueue_tasks_release_unique")
    ensure_unique_index(conn, "download_tasks", ["release_id"], "idx_download_tasks_release_unique")
    ensure_unique_index(conn, "cloud_poll_tasks", ["download_task_id"], "idx_cloud_poll_tasks_download_unique")
    ensure_unique_index(conn, "cloud_asset_tasks", ["download_task_id"], "idx_cloud_asset_tasks_download_unique")
    ensure_unique_index(conn, "sync_plan_tasks", ["entry_id"], "idx_sync_plan_tasks_entry_unique")
    ensure_unique_index(conn, "sync_tasks", ["cloud_asset_id", "sync_direction"], "idx_sync_tasks_asset_direction_unique")
    ensure_unique_index(conn, "nfo_tasks", ["local_asset_id"], "idx_nfo_tasks_local_asset_unique")
    ensure_unique_index(conn, "local_presence_tasks", ["local_asset_id"], "idx_local_presence_tasks_local_asset_unique")
    ensure_unique_index(conn, "cleanup_tasks", ["task_scope"], "idx_cleanup_tasks_scope_unique")
    ensure_pipeline_runtime(conn)
    merge_duplicate_series(conn)
    ensure_scheduled_jobs(conn)


def ensure_scheduled_jobs(conn: sqlite3.Connection) -> None:
    ts = now()
    conn.execute(
        """
        INSERT INTO cleanup_tasks
          (task_scope, status, retry_after, last_error, created_at, updated_at)
        VALUES ('runtime', 'pending', '', '', ?, ?)
        ON CONFLICT(task_scope) DO NOTHING
        """,
        (ts, ts),
    )
    jobs = [
        ("rss_scan", "rss_scan", 60, 0, 1),
        ("queue_dispatch", "queue_dispatch", 1, 10, 1),
        ("mikan_match_dispatch", "queue_dispatch", 0, 10, 1),
        ("metadata_dispatch", "queue_dispatch", 0, 10, 1),
        ("selection_dispatch", "queue_dispatch", 0, 10, 1),
        ("backfill_dispatch", "queue_dispatch", 0, 10, 1),
        ("cloud_presence_dispatch", "queue_dispatch", 0, 10, 1),
        ("download_enqueue_dispatch", "queue_dispatch", 0, 10, 1),
        ("download_dispatch", "queue_dispatch", 0, 10, 1),
        ("cloud_poll_dispatch", "queue_dispatch", 0, 10, 1),
        ("cloud_asset_dispatch", "queue_dispatch", 0, 10, 1),
        ("sync_plan_dispatch", "queue_dispatch", 0, 10, 1),
        ("sync_dispatch", "queue_dispatch", 0, 10, 1),
        ("nfo_dispatch", "queue_dispatch", 0, 10, 1),
        ("local_presence_dispatch", "queue_dispatch", 0, 10, 1),
        ("cleanup_dispatch", "queue_dispatch", 0, 10, 1),
    ]
    for job_key, job_type, interval_minutes, debounce_seconds, max_concurrency in jobs:
        conn.execute(
            """
            INSERT INTO scheduled_jobs
              (job_key, job_type, enabled, interval_minutes, debounce_seconds, max_concurrency, created_at, updated_at)
            VALUES (?, ?, 1, ?, ?, ?, ?, ?)
            ON CONFLICT(job_key) DO UPDATE SET
              job_type=excluded.job_type,
              interval_minutes=excluded.interval_minutes,
              debounce_seconds=excluded.debounce_seconds,
              max_concurrency=excluded.max_concurrency,
              updated_at=excluded.updated_at
            """,
            (job_key, job_type, interval_minutes, debounce_seconds, max_concurrency, ts, ts),
        )


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
            conn.execute("UPDATE cloud_assets SET series_id=? WHERE series_id=?", (keep_id, old_id))
            conn.execute("UPDATE local_assets SET series_id=? WHERE series_id=?", (keep_id, old_id))
            conn.execute("UPDATE sync_tasks SET series_id=? WHERE series_id=?", (keep_id, old_id))
            conn.execute("UPDATE cloud_submissions SET series_id=? WHERE series_id=?", (keep_id, old_id))
            conn.execute("UPDATE selection_tasks SET series_id=? WHERE series_id=?", (keep_id, old_id))
            conn.execute("UPDATE backfill_tasks SET series_id=? WHERE series_id=?", (keep_id, old_id))
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
            conn.execute("UPDATE download_tasks SET series_id=? WHERE series_id=?", (keep_id, old_id))
            conn.execute("UPDATE cloud_submissions SET series_id=? WHERE series_id=?", (keep_id, old_id))
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
            OR id IN (SELECT DISTINCT series_id FROM download_tasks)
            OR id IN (SELECT DISTINCT series_id FROM cloud_assets)
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
          AND id NOT IN (SELECT DISTINCT series_id FROM download_tasks)
          AND id NOT IN (SELECT DISTINCT series_id FROM cloud_assets)
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
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    line = f"{now()} [{level.upper()}] {message[:2000]}\n"
    try:
        with LOG_PATH.open("a", encoding="utf-8") as output:
            output.write(line)
    except OSError:
        pass
    try:
        with connect() as conn:
            conn.execute(
                "INSERT INTO logs (level, message, created_at) VALUES (?, ?, ?)",
                (level, message[:2000], now()),
            )
    except sqlite3.OperationalError:
        pass


def read_server_logs(limit: int = 200) -> list[str]:
    if not LOG_PATH.exists():
        return []
    try:
        with LOG_PATH.open("r", encoding="utf-8", errors="replace") as source:
            return list(deque((line.rstrip("\n") for line in source), maxlen=limit))
    except OSError:
        return []


def start_operation(name: str, message: str = "") -> int:
    with connect() as conn:
        cursor = conn.execute(
            "INSERT INTO operations (name, status, message, started_at) VALUES (?, 'running', ?, ?)",
            (name, message[:2000], now()),
        )
        return int(cursor.lastrowid)


def finish_operation(operation_id: int, status: str, message: str = "") -> None:
    with connect() as conn:
        conn.execute(
            """
            UPDATE operations
            SET status=?, message=?, finished_at=?
            WHERE id=?
            """,
            (status, message[:2000], now(), operation_id),
        )
        if status == "completed":
            conn.execute("DELETE FROM operations WHERE id=?", (operation_id,))


def update_operation(operation_id: int, message: str) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE operations SET message=? WHERE id=?",
            (message[:2000], operation_id),
        )


def cleanup_operations(max_failed: int = 20) -> None:
    ts = now()
    with connect() as conn:
        conn.execute(
            """
            UPDATE operations
            SET status='failed', message='服务重启，上次运行已中断', finished_at=?
            WHERE status='running'
            """,
            (ts,),
        )
        failed_ids = [
            int(row["id"])
            for row in conn.execute(
                "SELECT id FROM operations WHERE status='failed' ORDER BY id DESC"
            ).fetchall()
        ]
        if len(failed_ids) > max_failed:
            stale_ids = failed_ids[max_failed:]
            conn.executemany(
                "DELETE FROM operations WHERE id=?",
                [(operation_id,) for operation_id in stale_ids],
            )


def run_cleanup_tasks(max_failed_operations: int = 20, completed_task_limit: int = 200) -> dict[str, int]:
    ts = now()
    deleted_operations = 0
    trimmed_task_rows = 0
    cleanup_operations(max_failed=max_failed_operations)
    with connect() as conn:
        completed_ids = [
            int(row["id"])
            for row in conn.execute(
                "SELECT id FROM operations WHERE status IN ('completed', 'failed') ORDER BY id DESC"
            ).fetchall()
        ]
        if len(completed_ids) > max_failed_operations:
            stale_ids = completed_ids[max_failed_operations:]
            conn.executemany("DELETE FROM operations WHERE id=?", [(operation_id,) for operation_id in stale_ids])
            deleted_operations += len(stale_ids)

        for table in [
            "cloud_presence_tasks",
            "download_enqueue_tasks",
            "download_tasks",
            "cloud_poll_tasks",
            "cloud_asset_tasks",
            "sync_plan_tasks",
            "sync_tasks",
            "nfo_tasks",
            "local_presence_tasks",
        ]:
            rows = conn.execute(
                f"SELECT id FROM {table} WHERE status IN ('completed', 'superseded', 'synced') ORDER BY id DESC"
            ).fetchall()
            if len(rows) > completed_task_limit:
                stale = [(int(row["id"]),) for row in rows[completed_task_limit:]]
                conn.executemany(f"DELETE FROM {table} WHERE id=?", stale)
                trimmed_task_rows += len(stale)
    return {"deleted_operations": deleted_operations, "trimmed_task_rows": trimmed_task_rows, "ran_at": 1 if ts else 0}


def mark_scheduled_job(job_key: str, **fields: Any) -> None:
    if not fields:
        return
    allowed = {
        "enabled",
        "interval_minutes",
        "debounce_seconds",
        "max_concurrency",
        "last_run_at",
        "next_run_at",
        "last_status",
        "last_error",
        "updated_at",
    }
    updates = [(key, value) for key, value in fields.items() if key in allowed]
    if not updates:
        return
    sql = ", ".join(f"{key}=?" for key, _ in updates)
    params = [value for _, value in updates]
    params.append(job_key)
    with connect() as conn:
        conn.execute(f"UPDATE scheduled_jobs SET {sql} WHERE job_key=?", params)


def start_scheduled_job_run(job_key: str, trigger_source: str = "system", message: str = "") -> int:
    ts = now()
    with connect() as conn:
        row = conn.execute(
            "SELECT id FROM scheduled_jobs WHERE job_key=?",
            (job_key,),
        ).fetchone()
        if not row:
            return 0
        cursor = conn.execute(
            """
            INSERT INTO scheduled_job_runs
              (job_id, status, trigger_source, message, stats_json, started_at, finished_at)
            VALUES (?, 'running', ?, ?, '', ?, '')
            """,
            (int(row["id"]), trigger_source, message[:2000], ts),
        )
        conn.execute(
            """
            UPDATE scheduled_jobs
            SET last_run_at=?, last_status='running', last_error='', updated_at=?
            WHERE id=?
            """,
            (ts, ts, int(row["id"])),
        )
        return int(cursor.lastrowid)


def finish_scheduled_job_run(run_id: int, status: str, message: str = "", stats_json: str = "") -> None:
    if not run_id:
        return
    ts = now()
    with connect() as conn:
        run = conn.execute(
            "SELECT job_id FROM scheduled_job_runs WHERE id=?",
            (run_id,),
        ).fetchone()
        conn.execute(
            """
            UPDATE scheduled_job_runs
            SET status=?, message=?, stats_json=?, finished_at=?
            WHERE id=?
            """,
            (status, message[:2000], stats_json[:4000], ts, run_id),
        )
        if run:
            conn.execute(
                """
                UPDATE scheduled_jobs
                SET last_status=?, last_error=?, updated_at=?
                WHERE id=?
                """,
                (
                    status,
                    "" if status == "completed" else message[:2000],
                    ts,
                    int(run["job_id"]),
                ),
            )


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
            "works",
            "entries",
            "seasonal_entries",
            "library_entries",
            "series",
            "episodes",
            "releases",
            "rss_candidates",
            "mikan_match_tasks",
            "metadata_tasks",
            "selection_tasks",
            "backfill_tasks",
            "cloud_submissions",
            "download_tasks",
            "cloud_poll_tasks",
            "cloud_asset_tasks",
            "cloud_assets",
            "sync_rules",
            "local_assets",
            "sync_tasks",
            "logs",
            "operations",
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
            "sync_tasks",
            "local_assets",
            "sync_rules",
            "cloud_assets",
            "cloud_asset_tasks",
            "cloud_poll_tasks",
            "cloud_submissions",
            "download_tasks",
            "backfill_tasks",
            "selection_tasks",
            "metadata_tasks",
            "mikan_match_tasks",
            "rss_candidates",
            "library_entries",
            "seasonal_entries",
            "entries",
            "works",
            "releases",
            "episodes",
            "series",
            "operations",
            "logs",
            "scheduled_job_runs",
        ]:
            conn.execute(f"DELETE FROM {table}")
        conn.execute("DELETE FROM sqlite_sequence WHERE name IN ('sync_tasks','local_assets','sync_rules','cloud_assets','cloud_asset_tasks','cloud_poll_tasks','cloud_submissions','download_tasks','backfill_tasks','selection_tasks','metadata_tasks','mikan_match_tasks','rss_candidates','library_entries','seasonal_entries','entries','works','releases','episodes','series','operations','logs','scheduled_job_runs')")
        conn.execute(
            """
            UPDATE scheduled_jobs
            SET last_run_at='',
                next_run_at='',
                last_status='idle',
                last_error='',
                updated_at=?
            """
            ,
            (next_generation,),
        )
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('runtime_generation', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (next_generation,),
        )
    try:
        LOG_PATH.unlink(missing_ok=True)
    except OSError:
        pass
