from __future__ import annotations

from typing import Any

from .database import connect
from .db import now


RECENT_OPERATION_LIMIT = 100


def record_operation_event(
    action: str,
    title: str,
    message: str = "",
    *,
    level: str = "info",
    ref_type: str = "",
    ref_id: int = 0,
) -> None:
    ts = now()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO operation_events
              (action, title, message, level, ref_type, ref_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(action or "operation")[:80],
                str(title or "操作记录")[:200],
                str(message or "")[:1000],
                str(level or "info")[:20],
                str(ref_type or "")[:40],
                int(ref_id or 0),
                ts,
            ),
        )
        conn.execute(
            """
            DELETE FROM operation_events
            WHERE id NOT IN (
              SELECT id FROM operation_events ORDER BY id DESC LIMIT ?
            )
            """,
            (RECENT_OPERATION_LIMIT,),
        )


def list_recent_operations(limit: int = 20) -> list[dict[str, Any]]:
    safe_limit = max(1, min(100, int(limit or 20)))
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT id, action, title, message, level, ref_type, ref_id, created_at
            FROM operation_events
            ORDER BY id DESC
            LIMIT ?
            """,
            (safe_limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def clear_recent_operations() -> int:
    with connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS total FROM operation_events").fetchone()
        total = int(row["total"] or 0) if row else 0
        conn.execute("DELETE FROM operation_events")
    return total
