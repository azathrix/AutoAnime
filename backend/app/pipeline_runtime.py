from __future__ import annotations

from typing import Any

from .database import connect
from .runtime_store import runtime_store


def get_pipeline_id(key: str) -> int:
    with connect() as conn:
        row = conn.execute("SELECT id FROM pipelines WHERE key=?", (key,)).fetchone()
        return int(row["id"]) if row else 0


def _notify_runtime() -> None:
    try:
        import asyncio

        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(runtime_store.bump())


def start_pipeline_run(pipeline_key: str, trigger_source: str, message: str = "") -> int:
    pipeline_id = get_pipeline_id(pipeline_key)
    if pipeline_id <= 0:
        return 0
    run_id = runtime_store._next_run_id
    runtime_store._next_run_id += 1
    from .runtime_store import RuntimeRun

    runtime_store.runs[run_id] = RuntimeRun(
        id=run_id,
        pipeline_id=pipeline_id,
        pipeline_key=pipeline_key,
        trigger_source=trigger_source,
        message=message[:2000],
    )
    _notify_runtime()
    return run_id


def update_pipeline_run(run_id: int, *, progress: int | None = None, message: str | None = None, stats: dict[str, Any] | None = None) -> None:
    run = runtime_store.runs.get(run_id)
    if not run:
        return
    if progress is not None:
        run.progress = max(0, min(100, int(progress)))
    if message is not None:
        run.message = message[:2000]
    if stats is not None:
        run.stats = stats
    from .runtime_store import utc_now

    run.updated_at = utc_now()
    _notify_runtime()


def finish_pipeline_run(run_id: int, status: str, message: str = "", stats: dict[str, Any] | None = None) -> None:
    run = runtime_store.runs.get(run_id)
    if not run:
        return
    from .runtime_store import utc_now

    ts = utc_now()
    run.status = status
    run.message = message[:2000]
    run.stats = stats or run.stats
    run.progress = 100 if status == "completed" else run.progress
    run.finished_at = ts
    run.updated_at = ts
    _notify_runtime()


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
    runtime_store.append_log_sync(level, f"{processor_key}:{event_key} task_id={task_id} run_id={run_id} {message}")


def pipeline_overview() -> list[dict[str, Any]]:
    snapshot = runtime_store.snapshot()
    runs_by_pipeline: dict[str, list[dict[str, Any]]] = {}
    for run in snapshot["runs"]:
        runs_by_pipeline.setdefault(str(run["pipeline_key"]), []).append(run)
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT p.id, p.key, p.name, p.domain_kind, p.enabled, COUNT(ps.id) AS step_count
            FROM pipelines p
            LEFT JOIN pipeline_steps ps ON ps.pipeline_id=p.id
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
            runs = runs_by_pipeline.get(str(row["key"]), [])
            result.append(
                {
                    "id": int(row["id"]),
                    "key": row["key"],
                    "name": row["name"],
                    "domain_kind": row["domain_kind"],
                    "enabled": bool(row["enabled"]),
                    "step_count": int(row["step_count"] or 0),
                    "running_count": sum(1 for run in runs if run["status"] == "running"),
                    "last_run_at": runs[0]["updated_at"] if runs else "",
                    "steps": [dict(step) for step in steps],
                    "recent_runs": runs[:5],
                }
            )
        return result

