from __future__ import annotations

import json
from typing import Any

from .database import connect
from .db import now


def get_pipeline_id(key: str) -> int:
    with connect() as conn:
        row = conn.execute("SELECT id FROM pipelines WHERE key=?", (key,)).fetchone()
        return int(row["id"]) if row else 0


def start_pipeline_run(pipeline_key: str, trigger_source: str, message: str = "") -> int:
    pipeline_id = get_pipeline_id(pipeline_key)
    if pipeline_id <= 0:
        return 0
    ts = now()
    with connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO pipeline_runs
              (pipeline_id, trigger_source, status, progress, message, stats_json, started_at, finished_at, updated_at)
            VALUES (?, ?, 'running', 0, ?, '', ?, '', ?)
            """,
            (pipeline_id, trigger_source, message, ts, ts),
        )
        return int(cursor.lastrowid)


def update_pipeline_run(run_id: int, *, progress: int | None = None, message: str | None = None, stats: dict[str, Any] | None = None) -> None:
    if run_id <= 0:
        return
    parts = ["updated_at=?"]
    params: list[Any] = [now()]
    if progress is not None:
        parts.append("progress=?")
        params.append(max(0, min(100, int(progress))))
    if message is not None:
        parts.append("message=?")
        params.append(message[:2000])
    if stats is not None:
        parts.append("stats_json=?")
        params.append(json.dumps(stats, ensure_ascii=False))
    params.append(run_id)
    with connect() as conn:
        conn.execute(f"UPDATE pipeline_runs SET {', '.join(parts)} WHERE id=?", params)


def finish_pipeline_run(run_id: int, status: str, message: str = "", stats: dict[str, Any] | None = None) -> None:
    if run_id <= 0:
        return
    ts = now()
    stats_json = json.dumps(stats or {}, ensure_ascii=False)
    with connect() as conn:
        conn.execute(
            """
            UPDATE pipeline_runs
            SET status=?,
                progress=CASE WHEN ?='completed' THEN 100 ELSE progress END,
                message=?,
                stats_json=?,
                finished_at=?,
                updated_at=?
            WHERE id=?
            """,
            (status, status, message[:2000], stats_json, ts, ts, run_id),
        )


def record_processor_event(
    *,
    task_id: int = 0,
    pipeline_id: int = 0,
    run_id: int = 0,
    processor_key: str = "",
    level: str = "info",
    event_key: str = "",
    message: str = "",
    data: dict[str, Any] | None = None,
) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO processor_events
              (task_id, pipeline_id, run_id, processor_key, level, event_key, message, data_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                pipeline_id,
                run_id,
                processor_key,
                level,
                event_key,
                message[:2000],
                json.dumps(data or {}, ensure_ascii=False),
                now(),
            ),
        )


def pipeline_overview() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT p.id,
              p.key,
              p.name,
              p.domain_kind,
              p.enabled,
              COUNT(DISTINCT ps.id) AS step_count,
              COUNT(DISTINCT CASE WHEN pr.status='running' THEN pr.id END) AS running_count,
              MAX(pr.updated_at) AS last_run_at
            FROM pipelines p
            LEFT JOIN pipeline_steps ps ON ps.pipeline_id=p.id
            LEFT JOIN pipeline_runs pr ON pr.pipeline_id=p.id
            GROUP BY p.id
            ORDER BY p.id ASC
            """
        ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            steps = conn.execute(
                """
                SELECT step_key, processor_key, sort_order, enabled, max_concurrency, debounce_seconds
                FROM pipeline_steps
                WHERE pipeline_id=?
                ORDER BY sort_order ASC
                """,
                (row["id"],),
            ).fetchall()
            latest_runs = conn.execute(
                """
                SELECT id, status, progress, message, stats_json, started_at, finished_at, updated_at
                FROM pipeline_runs
                WHERE pipeline_id=?
                ORDER BY id DESC
                LIMIT 5
                """,
                (row["id"],),
            ).fetchall()
            result.append(
                {
                    "id": int(row["id"]),
                    "key": row["key"],
                    "name": row["name"],
                    "domain_kind": row["domain_kind"],
                    "enabled": bool(row["enabled"]),
                    "step_count": int(row["step_count"] or 0),
                    "running_count": int(row["running_count"] or 0),
                    "last_run_at": row["last_run_at"] or "",
                    "steps": [dict(step) for step in steps],
                    "recent_runs": [dict(run) for run in latest_runs],
                }
            )
        return result
