from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from collections import deque
from typing import Any

from .config import DATA_DIR, DB_PATH, DEFAULT_SETTINGS


LOG_PATH = DATA_DIR / "autoanime.log"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
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

            CREATE TABLE IF NOT EXISTS episodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                series_id INTEGER NOT NULL,
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

            CREATE TABLE IF NOT EXISTS metadata_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                candidate_id INTEGER NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                bangumi_id TEXT NOT NULL DEFAULT '',
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
                last_error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS cloud_assets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER NOT NULL UNIQUE,
                release_id INTEGER NOT NULL,
                series_id INTEGER NOT NULL,
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
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                sync_direction TEXT NOT NULL DEFAULT 'cloud_to_local',
                source_path TEXT NOT NULL DEFAULT '',
                target_path TEXT NOT NULL DEFAULT '',
                last_error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(cloud_asset_id, sync_direction)
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

            CREATE UNIQUE INDEX IF NOT EXISTS idx_cloud_assets_provider_file
            ON cloud_assets(provider, provider_file_id)
            WHERE provider_file_id != '';
            """
        )
        for key, value in DEFAULT_SETTINGS.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                (key, value),
            )
        migrated = conn.execute(
            "SELECT value FROM settings WHERE key='migration_auto_sync_default_v2'"
        ).fetchone()
        if not migrated or not migrated["value"]:
            conn.execute(
                """
                UPDATE settings
                SET value='true'
                WHERE key='auto_sync_following' AND value='false'
                """
            )
            conn.execute(
                """
                INSERT INTO settings (key, value) VALUES ('migration_auto_sync_default_v2', 'done')
                ON CONFLICT(key) DO UPDATE SET value='done'
                """
            )
        migrate(conn)


def migrate(conn: sqlite3.Connection) -> None:
    series_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(series)").fetchall()
    }
    series_additions = {
        "poster_path": "TEXT NOT NULL DEFAULT ''",
        "metadata_source": "TEXT NOT NULL DEFAULT ''",
        "nfo_status": "TEXT NOT NULL DEFAULT 'pending'",
        "hidden": "INTEGER NOT NULL DEFAULT 0",
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
    }
    for column, ddl in download_task_additions.items():
        if column not in download_task_columns:
            conn.execute(f"ALTER TABLE download_tasks ADD COLUMN {column} {ddl}")
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
            last_error TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
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
    }
    for column, ddl in mikan_match_additions.items():
        if column not in mikan_match_columns:
            conn.execute(f"ALTER TABLE mikan_match_tasks ADD COLUMN {column} {ddl}")
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
            conn.execute("UPDATE cloud_assets SET series_id=? WHERE series_id=?", (keep_id, old_id))
            conn.execute("UPDATE local_assets SET series_id=? WHERE series_id=?", (keep_id, old_id))
            conn.execute("UPDATE sync_tasks SET series_id=? WHERE series_id=?", (keep_id, old_id))
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


def log(level: str, message: str) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    line = f"{now()} [{level.upper()}] {message[:2000]}\n"
    try:
        with LOG_PATH.open("a", encoding="utf-8") as output:
            output.write(line)
    except OSError:
        pass
    with connect() as conn:
        conn.execute(
            "INSERT INTO logs (level, message, created_at) VALUES (?, ?, ?)",
            (level, message[:2000], now()),
        )


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


def update_operation(operation_id: int, message: str) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE operations SET message=? WHERE id=?",
            (message[:2000], operation_id),
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
            "series",
            "episodes",
            "releases",
            "rss_candidates",
            "mikan_match_tasks",
            "metadata_tasks",
            "download_tasks",
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
    with connect() as conn:
        for table in [
            "sync_tasks",
            "local_assets",
            "sync_rules",
            "cloud_assets",
            "download_tasks",
            "metadata_tasks",
            "mikan_match_tasks",
            "rss_candidates",
            "releases",
            "episodes",
            "series",
            "operations",
            "logs",
        ]:
            conn.execute(f"DELETE FROM {table}")
        conn.execute("DELETE FROM sqlite_sequence WHERE name IN ('sync_tasks','local_assets','sync_rules','cloud_assets','download_tasks','metadata_tasks','mikan_match_tasks','rss_candidates','releases','episodes','series','operations','logs')")
    try:
        LOG_PATH.unlink(missing_ok=True)
    except OSError:
        pass
