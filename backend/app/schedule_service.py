from __future__ import annotations

import asyncio
import json
from typing import Any

from .database import connect
from .db import get_settings, log, now, save_settings
from .processing_cache import clear_expired_processing_cache, clear_processing_cache_type
from .runtime_store import runtime_store


SCHEDULE_ACTIONS = {
    "rss_scan": "刷新 RSS",
    "rss_cache_cleanup": "清理 RSS 缓存",
    "expired_cache_cleanup": "清理过期缓存",
}


def list_schedules() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM schedules
            ORDER BY id ASC
            """
        ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        try:
            item["config"] = json.loads(str(row["config_json"] or "{}"))
        except json.JSONDecodeError:
            item["config"] = {}
        item["action_name"] = SCHEDULE_ACTIONS.get(str(row["action"] or ""), str(row["action"] or ""))
        result.append(item)
    return result


def upsert_schedule(payload: dict[str, Any], schedule_id: int = 0) -> dict[str, Any]:
    key = str(payload.get("key") or "").strip()
    action = str(payload.get("action") or "").strip()
    if not action or action not in SCHEDULE_ACTIONS:
        raise ValueError("未知定时器动作")
    if not key:
        key = action
    name = str(payload.get("name") or SCHEDULE_ACTIONS[action]).strip()
    interval = max(1, int(payload.get("interval_minutes") or 60))
    enabled = 1 if bool(payload.get("enabled", True)) else 0
    config_json = json.dumps(payload.get("config") or {}, ensure_ascii=False, separators=(",", ":"))
    ts = now()
    with connect() as conn:
        if schedule_id > 0:
            row = conn.execute("SELECT id FROM schedules WHERE id=?", (schedule_id,)).fetchone()
            if not row:
                raise KeyError("定时器不存在")
            conn.execute(
                """
                UPDATE schedules
                SET key=?, name=?, action=?, enabled=?, interval_minutes=?, config_json=?, updated_at=?
                WHERE id=?
                """,
                (key, name, action, enabled, interval, config_json, ts, schedule_id),
            )
        else:
            conn.execute(
                """
                INSERT INTO schedules
                  (key, name, action, enabled, interval_minutes, config_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                  name=excluded.name,
                  action=excluded.action,
                  enabled=excluded.enabled,
                  interval_minutes=excluded.interval_minutes,
                  config_json=excluded.config_json,
                  updated_at=excluded.updated_at
                """,
                (key, name, action, enabled, interval, config_json, ts, ts),
            )
        row = conn.execute("SELECT * FROM schedules WHERE key=?", (key,)).fetchone()
    if action == "rss_scan":
        save_settings({"auto_scan": str(bool(enabled)).lower(), "scan_interval_minutes": str(interval)})
    return dict(row) if row else {}


async def run_schedule_action(action: str, trigger_source: str = "manual") -> str:
    action = str(action or "").strip()
    if action == "rss_scan":
        from .runtime_service import run_scan_source

        operation_id = runtime_store.start_operation_sync("扫描全部", f"定时器触发: {trigger_source}")
        try:
            message = await run_scan_source(get_settings(), operation_id)
        except Exception as exc:
            runtime_store.finish_operation_sync(operation_id, "failed", str(exc))
            raise
        runtime_store.finish_operation_sync(operation_id, "completed", message)
        return message
    if action == "rss_cache_cleanup":
        operation_id = runtime_store.start_operation_sync("清理 RSS 缓存", f"定时器触发: {trigger_source}")
        count = clear_processing_cache_type("rss_resource_processed")
        message = f"RSS 缓存已清理: {count} 条"
        runtime_store.finish_operation_sync(operation_id, "completed", message)
        log("info", message)
        return message
    if action == "expired_cache_cleanup":
        operation_id = runtime_store.start_operation_sync("清理过期缓存", f"定时器触发: {trigger_source}")
        count = clear_expired_processing_cache()
        message = f"过期缓存已清理: {count} 条"
        runtime_store.finish_operation_sync(operation_id, "completed", message)
        log("info", message)
        return message
    raise ValueError(f"未知定时器动作: {action}")


def trigger_schedule(schedule_id: int, trigger_source: str = "manual") -> int:
    with connect() as conn:
        row = conn.execute("SELECT * FROM schedules WHERE id=?", (schedule_id,)).fetchone()
    if not row:
        raise KeyError("定时器不存在")
    action = str(row["action"] or "")
    name = str(row["name"] or action)
    run_id = runtime_store.start_scheduler_run_sync(str(row["key"] or action), trigger_source, name)

    async def runner() -> None:
        try:
            message = await run_schedule_action(action, trigger_source)
            runtime_store.finish_scheduler_run_sync(run_id, "completed", message)
            with connect() as conn:
                conn.execute(
                    "UPDATE schedules SET last_status='completed', last_run_at=?, last_error='', updated_at=? WHERE id=?",
                    (now(), now(), schedule_id),
                )
        except Exception as exc:
            error = str(exc)[:2000]
            runtime_store.finish_scheduler_run_sync(run_id, "failed", error)
            with connect() as conn:
                conn.execute(
                    "UPDATE schedules SET last_status='failed', last_run_at=?, last_error=?, updated_at=? WHERE id=?",
                    (now(), error, now(), schedule_id),
                )

    asyncio.create_task(runner())
    return run_id
