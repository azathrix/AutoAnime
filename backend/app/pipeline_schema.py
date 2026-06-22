from __future__ import annotations

import sqlite3
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    ts = _now()
    pipelines = [
        ("seasonal_mikan_tracking", "Mikan 新番追更", "seasonal"),
        ("library_backfill", "番剧库补番", "library"),
        ("media_upload", "本地文件上传整理", "library"),
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
        ],
        "media_upload": [
            ("upload", "upload"),
        ],
    }
    conn.execute("UPDATE pipelines SET enabled=0, updated_at=? WHERE key='media_import'", (ts,))
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
        "upload": 2,
        "local_sync": 2,
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
