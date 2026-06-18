from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from .database import connect
from .db import now
from .pipeline_models import ProcessorContext, ProcessorResult, merge_payload
from .pipeline_runtime import finish_pipeline_run, get_pipeline_id, record_processor_event, start_pipeline_run
from .processor_registry import get_processor
from .queue_bridge import request_queue_trigger


TERMINAL_STATUSES = {"completed", "failed", "blocked", "cancelled"}
STALE_RUNNING_MINUTES = 10


def load_json(value: str) -> dict[str, Any]:
    if not value:
        return {}
    try:
        data = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def dump_json(value: dict[str, Any] | None) -> str:
    return json.dumps(value or {}, ensure_ascii=False, separators=(",", ":"))


def retry_after_seconds(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=max(1, seconds))).isoformat()


def stale_running_cutoff() -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=STALE_RUNNING_MINUTES)).isoformat()


def pipeline_step(pipeline_id: int, step_key: str) -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM pipeline_steps
            WHERE pipeline_id=? AND step_key=? AND enabled=1
            """,
            (pipeline_id, step_key),
        ).fetchone()
        return dict(row) if row else {}


def first_pipeline_step(pipeline_id: int) -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM pipeline_steps
            WHERE pipeline_id=? AND enabled=1
            ORDER BY sort_order ASC, id ASC
            LIMIT 1
            """,
            (pipeline_id,),
        ).fetchone()
        return dict(row) if row else {}


def enqueue_processor_task(
    *,
    pipeline_id: int,
    run_id: int,
    step_key: str,
    subject_type: str,
    subject_id: int = 0,
    payload: dict[str, Any] | None = None,
    domain_kind: str = "",
    parent_task_id: int = 0,
    dedupe_key: str = "",
) -> int:
    step = pipeline_step(pipeline_id, step_key)
    if not step:
        return 0
    ts = now()
    payload_json = dump_json(payload)
    with connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO processor_tasks
              (pipeline_id, run_id, step_id, parent_task_id, processor_key, domain_kind,
               subject_type, subject_id, dedupe_key, payload_json, result_json, status,
               attempts, retry_after, progress, progress_text, last_error, locked_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', 'pending', 0, '', 0, '', '', '', ?, ?)
            ON CONFLICT(dedupe_key) WHERE dedupe_key != '' DO UPDATE SET
              run_id=excluded.run_id,
              step_id=excluded.step_id,
              parent_task_id=excluded.parent_task_id,
              processor_key=excluded.processor_key,
              domain_kind=excluded.domain_kind,
              subject_type=excluded.subject_type,
              subject_id=excluded.subject_id,
              payload_json=excluded.payload_json,
              status=CASE
                WHEN processor_tasks.run_id=excluded.run_id AND processor_tasks.status IN ('completed', 'running') THEN processor_tasks.status
                ELSE 'pending'
              END,
              attempts=CASE
                WHEN processor_tasks.run_id=excluded.run_id THEN processor_tasks.attempts
                ELSE 0
              END,
              result_json=CASE
                WHEN processor_tasks.run_id=excluded.run_id THEN processor_tasks.result_json
                ELSE ''
              END,
              progress=CASE
                WHEN processor_tasks.run_id=excluded.run_id THEN processor_tasks.progress
                ELSE 0
              END,
              progress_text=CASE
                WHEN processor_tasks.run_id=excluded.run_id THEN processor_tasks.progress_text
                ELSE ''
              END,
              retry_after='',
              last_error='',
              locked_at='',
              updated_at=excluded.updated_at
            """,
            (
                pipeline_id,
                run_id,
                int(step["id"]),
                parent_task_id,
                step["processor_key"],
                domain_kind or step.get("domain_kind", ""),
                subject_type,
                int(subject_id or 0),
                dedupe_key,
                payload_json,
                ts,
                ts,
            ),
        )
        if cursor.lastrowid:
            request_queue_trigger("processor")
            return int(cursor.lastrowid)
        if dedupe_key:
            row = conn.execute("SELECT id FROM processor_tasks WHERE dedupe_key=?", (dedupe_key,)).fetchone()
            request_queue_trigger("processor")
            return int(row["id"]) if row else 0
        return 0


def start_pipeline(
    pipeline_key: str,
    *,
    trigger_source: str = "manual",
    first_step_key: str = "",
    subject_type: str = "",
    subject_id: int = 0,
    payload: dict[str, Any] | None = None,
    message: str = "",
) -> int:
    pipeline_id = get_pipeline_id(pipeline_key)
    if pipeline_id <= 0:
        return 0
    step = pipeline_step(pipeline_id, first_step_key) if first_step_key else first_pipeline_step(pipeline_id)
    if not step:
        return 0
    run_id = start_pipeline_run(pipeline_key, trigger_source, message or f"启动流水线 {pipeline_key}")
    enqueue_processor_task(
        pipeline_id=pipeline_id,
        run_id=run_id,
        step_key=str(step["step_key"]),
        subject_type=subject_type,
        subject_id=subject_id,
        payload=payload,
        domain_kind=str(payload.get("domain_kind", "") if payload else ""),
        dedupe_key=f"{run_id}:{step['step_key']}:{subject_type}:{subject_id}",
    )
    return run_id


def claim_next_task(processor_key: str = "") -> dict[str, Any]:
    params: list[Any] = []
    processor_filter = ""
    if processor_key:
        processor_filter = "AND pt.processor_key=?"
        params.append(processor_key)
    with connect() as conn:
        conn.execute(
            """
            UPDATE processor_tasks
            SET status='pending',
                locked_at='',
                last_error=CASE
                  WHEN last_error='' THEN '上次处理器执行中断，已自动放回待处理'
                  ELSE last_error
                END,
                updated_at=?
            WHERE status='running'
              AND locked_at != ''
              AND locked_at < ?
            """,
            (now(), stale_running_cutoff()),
        )
        row = conn.execute(
            f"""
            SELECT pt.*, ps.step_key
            FROM processor_tasks pt
            JOIN pipeline_steps ps ON ps.id=pt.step_id
            JOIN pipelines p ON p.id=pt.pipeline_id
            WHERE pt.status='pending'
              AND p.enabled=1
              AND ps.enabled=1
              AND (pt.retry_after='' OR pt.retry_after<=?)
              {processor_filter}
            ORDER BY pt.updated_at ASC, pt.id ASC
            LIMIT 1
            """,
            [now(), *params],
        ).fetchone()
        if not row:
            return {}
        ts = now()
        cursor = conn.execute(
            """
            UPDATE processor_tasks
            SET status='running', attempts=attempts+1, locked_at=?, updated_at=?
            WHERE id=? AND status='pending'
            """,
            (ts, ts, row["id"]),
        )
        if cursor.rowcount != 1:
            return {}
        claimed = conn.execute(
            """
            SELECT pt.*, ps.step_key
            FROM processor_tasks pt
            JOIN pipeline_steps ps ON ps.id=pt.step_id
            WHERE pt.id=?
            """,
            (row["id"],),
        ).fetchone()
        return dict(claimed) if claimed else {}


def task_context(task: dict[str, Any]) -> ProcessorContext:
    return ProcessorContext(
        task_id=int(task["id"]),
        pipeline_id=int(task["pipeline_id"]),
        run_id=int(task["run_id"]),
        step_id=int(task["step_id"]),
        step_key=str(task["step_key"]),
        processor_key=str(task["processor_key"]),
        domain_kind=str(task["domain_kind"] or ""),
        subject_type=str(task["subject_type"] or ""),
        subject_id=int(task["subject_id"] or 0),
        attempts=int(task["attempts"] or 0),
    )


def complete_task(task: dict[str, Any], result: ProcessorResult) -> None:
    task_status = "completed"
    if result.status == "failed_retryable":
        task_status = "pending"
    elif result.status in {"failed_terminal", "conflict"}:
        task_status = "failed"
    ts = now()
    with connect() as conn:
        conn.execute(
            """
            UPDATE processor_tasks
            SET status=?,
                result_json=?,
                retry_after=?,
                progress=CASE WHEN ?='completed' THEN 100 ELSE progress END,
                progress_text=?,
                last_error=?,
                locked_at='',
                updated_at=?
            WHERE id=?
            """,
            (
                task_status,
                dump_json({"status": result.status, "message": result.message, "data": result.data}),
                result.retry_after,
                task_status,
                result.message[:500],
                "" if task_status == "completed" else result.message[:2000],
                ts,
                task["id"],
            ),
        )


def dispatch_result(task: dict[str, Any], payload: dict[str, Any], result: ProcessorResult) -> int:
    record_processor_event(
        task_id=int(task["id"]),
        pipeline_id=int(task["pipeline_id"]),
        run_id=int(task["run_id"]),
        processor_key=str(task["processor_key"]),
        level="error" if result.status.startswith("failed") else "info",
        event_key=result.status,
        message=result.message,
        data=result.data,
    )
    if result.status == "failed_retryable":
        return 0
    with connect() as conn:
        transitions = conn.execute(
            """
            SELECT to_step_key, payload_map_json
            FROM pipeline_transitions
            WHERE pipeline_id=? AND from_step_key=? AND result_status=?
            ORDER BY id ASC
            """,
            (task["pipeline_id"], task["step_key"], result.status),
        ).fetchall()
    if not transitions:
        if result.status == "success":
            finish_pipeline_run(int(task["run_id"]), "completed", result.message or "流水线执行完成", result.data)
        elif result.status in {"failed_terminal", "conflict"}:
            finish_pipeline_run(int(task["run_id"]), "failed", result.message, result.data)
        return 0

    enqueued = 0
    next_task_payloads = result.next_tasks or [result.next_payload or result.data]
    for transition in transitions:
        step_key = str(transition["to_step_key"] or "")
        if not step_key:
            continue
        for index, next_item in enumerate(next_task_payloads):
            item_payload = merge_payload(payload, next_item)
            item_subject_type = str(item_payload.pop("_subject_type", task["subject_type"] or ""))
            item_subject_id = int(item_payload.pop("_subject_id", task["subject_id"] or 0) or 0)
            item_dedupe = str(
                item_payload.pop(
                    "_dedupe_key",
                    f"{task['run_id']}:{step_key}:{item_subject_type}:{item_subject_id}:{index}",
                )
            )
            task_id = enqueue_processor_task(
                pipeline_id=int(task["pipeline_id"]),
                run_id=int(task["run_id"]),
                step_key=step_key,
                subject_type=item_subject_type,
                subject_id=item_subject_id,
                payload=item_payload,
                domain_kind=str(task["domain_kind"] or ""),
                parent_task_id=int(task["id"]),
                dedupe_key=item_dedupe,
            )
            if task_id:
                enqueued += 1
    return enqueued


async def run_one_task(processor_key: str = "") -> bool:
    task = claim_next_task(processor_key)
    if not task:
        return False
    processor = get_processor(str(task["processor_key"]))
    payload = load_json(str(task["payload_json"] or ""))
    if not processor:
        result = ProcessorResult.terminal(f"未注册处理器: {task['processor_key']}")
    else:
        try:
            result = await processor(task_context(task), payload)
        except Exception as exc:
            result = ProcessorResult.retryable(str(exc), retry_after_seconds(60))
    complete_task(task, result)
    dispatch_result(task, payload, result)
    return True


async def run_ready_tasks(limit: int = 20, processor_key: str = "") -> int:
    count = 0
    for _ in range(max(1, limit)):
        if not await run_one_task(processor_key):
            break
        count += 1
    return count
