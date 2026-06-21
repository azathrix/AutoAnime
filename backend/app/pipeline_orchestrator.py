from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from .database import connect
from .db import log
from .pipeline_models import ProcessorContext, ProcessorResult, merge_payload
from .pipeline_runtime import finish_pipeline_run, get_pipeline_id, record_processor_event, start_pipeline_run
from .processor_registry import get_processor
from .queue_bridge import request_queue_trigger
from .runtime_store import RuntimeTask, runtime_store


LOG_PAYLOAD_KEYS = [
    "entry_id",
    "release_id",
    "candidate_id",
    "download_task_id",
    "download_artifact_id",
    "episode_number",
    "domain_kind",
    "title",
    "normalized_name",
]


def load_json(value: str) -> dict[str, Any]:
    if not value:
        return {}
    try:
        data = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def retry_after_seconds(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=max(1, seconds))).isoformat()


def compact_payload(payload: dict[str, Any] | None) -> str:
    if not payload:
        return "-"
    values: list[str] = []
    for key in LOG_PAYLOAD_KEYS:
        value = payload.get(key)
        if value not in (None, ""):
            values.append(f"{key}={str(value)[:120]}")
    if values:
        return " ".join(values)
    keys = ",".join(sorted(str(key) for key in payload.keys())[:8])
    return f"keys={keys}" if keys else "-"


def processor_concurrency_limits() -> dict[str, int]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT processor_key, MAX(max_concurrency) AS max_concurrency
            FROM pipeline_steps
            WHERE enabled=1
            GROUP BY processor_key
            """
        ).fetchall()
    limits = {str(row["processor_key"]): max(1, int(row["max_concurrency"] or 1)) for row in rows}
    return limits or {"metadata": 4, "mikan_match": 4, "download": 2, "nfo": 1}


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
    try:
        loop = __import__("asyncio").get_running_loop()
    except RuntimeError:
        return 0

    task_id_holder = {"id": 0}

    async def enqueue() -> None:
        task_id_holder["id"] = await runtime_store.enqueue_task(
            pipeline_id=pipeline_id,
            run_id=run_id,
            step_id=int(step["id"]),
            step_key=str(step["step_key"]),
            processor_key=str(step["processor_key"]),
            subject_type=subject_type,
            subject_id=int(subject_id or 0),
            payload=payload or {},
            domain_kind=domain_kind or str(step.get("domain_kind", "")),
            parent_task_id=parent_task_id,
            dedupe_key=dedupe_key,
        )
        request_queue_trigger("processor")

    if loop.is_running():
        task = loop.create_task(enqueue())
        # The caller needs an id only for display/logging. The actual queue
        # operation runs immediately in the current event loop.
        return int(runtime_store._next_task_id)
    return 0


async def enqueue_processor_task_async(
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
    task_id = await runtime_store.enqueue_task(
        pipeline_id=pipeline_id,
        run_id=run_id,
        step_id=int(step["id"]),
        step_key=str(step["step_key"]),
        processor_key=str(step["processor_key"]),
        subject_type=subject_type,
        subject_id=int(subject_id or 0),
        payload=payload or {},
        domain_kind=domain_kind or str(step.get("domain_kind", "")),
        parent_task_id=parent_task_id,
        dedupe_key=dedupe_key,
    )
    request_queue_trigger("processor")
    return task_id


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
    log(
        "info",
        f"流水线启动: run_id={run_id} pipeline={pipeline_key} trigger={trigger_source} "
        f"first_step={step['step_key']} subject={subject_type}:{subject_id} payload={compact_payload(payload)}",
    )
    try:
        loop = __import__("asyncio").get_running_loop()
    except RuntimeError:
        return run_id
    loop.create_task(
        enqueue_processor_task_async(
            pipeline_id=pipeline_id,
            run_id=run_id,
            step_key=str(step["step_key"]),
            subject_type=subject_type,
            subject_id=subject_id,
            payload=payload,
            domain_kind=str(payload.get("domain_kind", "") if payload else ""),
            dedupe_key=f"{run_id}:{step['step_key']}:{subject_type}:{subject_id}",
        )
    )
    return run_id


def task_context(task: RuntimeTask) -> ProcessorContext:
    return ProcessorContext(
        task_id=int(task.id),
        pipeline_id=int(task.pipeline_id),
        run_id=int(task.run_id),
        step_id=int(task.step_id),
        step_key=str(task.step_key),
        processor_key=str(task.processor_key),
        domain_kind=str(task.domain_kind or ""),
        subject_type=str(task.subject_type or ""),
        subject_id=int(task.subject_id or 0),
        attempts=int(task.attempts or 0),
    )


async def dispatch_result(task: RuntimeTask, payload: dict[str, Any], result: ProcessorResult) -> int:
    record_processor_event(
        task_id=int(task.id),
        pipeline_id=int(task.pipeline_id),
        run_id=int(task.run_id),
        processor_key=str(task.processor_key),
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
            (task.pipeline_id, task.step_key, result.status),
        ).fetchall()
    if not transitions:
        if result.status == "success":
            finish_pipeline_run(int(task.run_id), "completed", result.message or "流水线执行完成", result.data)
        elif result.status in {"failed_terminal", "conflict"}:
            finish_pipeline_run(int(task.run_id), "failed", result.message, result.data)
        return 0

    enqueued = 0
    next_task_payloads = result.next_tasks or [result.next_payload or result.data]
    for transition in transitions:
        step_key = str(transition["to_step_key"] or "")
        if not step_key:
            continue
        for index, next_item in enumerate(next_task_payloads):
            item_payload = merge_payload(payload, next_item)
            item_subject_type = str(item_payload.pop("_subject_type", task.subject_type or ""))
            item_subject_id = int(item_payload.pop("_subject_id", task.subject_id or 0) or 0)
            item_dedupe = str(
                item_payload.pop(
                    "_dedupe_key",
                    f"{task.run_id}:{step_key}:{item_subject_type}:{item_subject_id}:{index}",
                )
            )
            task_id = await enqueue_processor_task_async(
                pipeline_id=int(task.pipeline_id),
                run_id=int(task.run_id),
                step_key=step_key,
                subject_type=item_subject_type,
                subject_id=item_subject_id,
                payload=item_payload,
                domain_kind=str(task.domain_kind or ""),
                parent_task_id=int(task.id),
                dedupe_key=item_dedupe,
            )
            if task_id:
                log(
                    "info",
                    f"流水线转移: run_id={task.run_id} from={task.step_key} to={step_key} "
                    f"parent_task_id={task.id} next_task_id={task_id} subject={item_subject_type}:{item_subject_id} "
                    f"payload={compact_payload(item_payload)}",
                )
                enqueued += 1
    return enqueued


async def run_one_task(processor_key: str = "", processor_limits: dict[str, int] | None = None) -> bool:
    task = await runtime_store.claim_task(processor_key, processor_limits)
    if not task:
        return False
    processor = get_processor(str(task.processor_key))
    payload = dict(task.payload or {})
    log(
        "info",
        f"处理器开始: task_id={task.id} run_id={task.run_id} step={task.step_key} "
        f"processor={task.processor_key} subject={task.subject_type}:{task.subject_id} "
        f"attempt={task.attempts} payload={compact_payload(payload)}",
    )
    if not processor:
        result = ProcessorResult.terminal(f"未注册处理器: {task.processor_key}")
    else:
        try:
            result = await processor(task_context(task), payload)
        except Exception as exc:
            log(
                "error",
                f"处理器异常: task_id={task.id} run_id={task.run_id} step={task.step_key} "
                f"processor={task.processor_key} error={str(exc)[:1800]}",
            )
            result = ProcessorResult.retryable(str(exc), retry_after_seconds(60))
    task_status = "completed"
    retry = ""
    if result.status == "failed_retryable":
        task_status = "waiting"
        retry = result.retry_after
    elif result.status in {"failed_terminal", "conflict"}:
        task_status = "failed"
    elif result.status == "skipped":
        task_status = "skipped"
    await runtime_store.complete_task(
        task.id,
        task_status,
        result.message,
        {"status": result.status, "message": result.message, "data": result.data},
        retry,
    )
    await dispatch_result(task, payload, result)
    await __import__("asyncio").sleep(0)
    return True


async def run_ready_tasks(limit: int = 20, processor_key: str = "") -> int:
    import asyncio

    limits = processor_concurrency_limits()
    if processor_key:
        limit = min(max(1, int(limits.get(processor_key, 1))), max(1, limit))
    else:
        limit = min(max(1, limit), 8)
    results = await asyncio.gather(*(run_one_task(processor_key, limits) for _ in range(limit)))
    return sum(1 for item in results if item)

