from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

from .config import DATA_DIR, DB_PATH
from .database import connect


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def table_count(conn: sqlite3.Connection, table: str) -> int:
    try:
        row = conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()
    except sqlite3.Error:
        return -1
    return int(row["count"]) if row else -1


def table_count_visible_series(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute("SELECT COUNT(*) AS count FROM series WHERE COALESCE(hidden, 0)=0").fetchone()
    except sqlite3.Error:
        return 0
    return int(row["count"]) if row else 0


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
            "episode_resources",
            "episode_subtitles",
            "rss_candidates",
            "rss_subscriptions",
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


def clear_runtime_data() -> None:
    next_generation = _now()
    with connect() as conn:
        for table in [
            "local_assets",
            "sync_rules",
            "download_artifacts",
            "download_jobs",
            "episode_subtitles",
            "episode_resources",
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
        conn.execute(
            "DELETE FROM sqlite_sequence WHERE name IN ('local_assets','sync_rules','download_artifacts','download_jobs','episode_subtitles','episode_resources','rss_candidates','library_entries','seasonal_entries','entries','works','releases','episodes','series')"
        )
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('runtime_generation', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (next_generation,),
        )
