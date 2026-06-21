from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any


TASK_TERMINAL_STATUSES = {"completed", "failed", "skipped", "cancelled"}
VISIBLE_TASK_STATUSES = {"pending", "running", "waiting", "failed"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def retry_at(seconds: int = 60) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=max(1, seconds))).isoformat()


def due(value: str) -> bool:
    if not value:
        return True
    try:
        target = datetime.fromisoformat(value)
    except ValueError:
        return True
    if target.tzinfo is None:
        target = target.replace(tzinfo=timezone.utc)
    return target <= datetime.now(timezone.utc)


@dataclass
class RuntimeTask:
    id: int
    run_id: int
    pipeline_id: int
    step_id: int
    step_key: str
    processor_key: str
    queue_key: str
    domain_kind: str
    subject_type: str
    subject_id: int
    dedupe_key: str
    payload: dict[str, Any]
    parent_task_id: int = 0
    status: str = "pending"
    attempts: int = 0
    retry_at: str = ""
    message: str = ""
    error: str = ""
    result: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)


@dataclass
class RuntimeRun:
    id: int
    pipeline_id: int
    pipeline_key: str
    trigger_source: str
    status: str = "running"
    progress: int = 0
    message: str = ""
    stats: dict[str, Any] = field(default_factory=dict)
    started_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    finished_at: str = ""


class RuntimeStore:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._changed = asyncio.Condition()
        self._next_task_id = 1
        self._next_run_id = 1
        self._next_operation_id = 1
        self._next_scheduler_run_id = 1
        self._generation = utc_now()
        self._version = 0
        self.tasks: dict[int, RuntimeTask] = {}
        self.runs: dict[int, RuntimeRun] = {}
        self.dedupe_index: dict[str, int] = {}
        self.logs: deque[str] = deque(maxlen=5000)
        self.scheduler: dict[str, dict[str, Any]] = {}
        self.scheduler_runs: deque[dict[str, Any]] = deque(maxlen=200)
        self.operations: dict[int, dict[str, Any]] = {}

    @property
    def generation(self) -> str:
        return self._generation

    async def bump(self) -> int:
        async with self._changed:
            self._version += 1
            self._changed.notify_all()
            return self._version

    def bump_sync(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._version += 1
            return
        loop.create_task(self.bump())

    async def wait_for_change(self, last_version: int, timeout: float = 15.0) -> int:
        async with self._changed:
            if self._version != last_version:
                return self._version
            try:
                await asyncio.wait_for(self._changed.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                pass
            return self._version

    async def append_log(self, level: str, message: str) -> None:
        line = f"{utc_now()} [{level.upper()}] {message[:2000]}"
        async with self._lock:
            self.logs.append(line)
        await self.bump()

    def append_log_sync(self, level: str, message: str) -> None:
        line = f"{utc_now()} [{level.upper()}] {message[:2000]}"
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self.logs.append(line)
            return
        loop.create_task(self.append_log(level, message))

    async def clear_logs(self) -> int:
        async with self._lock:
            count = len(self.logs)
            self.logs.clear()
        await self.bump()
        return count

    async def start_run(self, pipeline_id: int, pipeline_key: str, trigger_source: str, message: str = "") -> int:
        async with self._lock:
            run_id = self._next_run_id
            self._next_run_id += 1
            self.runs[run_id] = RuntimeRun(
                id=run_id,
                pipeline_id=pipeline_id,
                pipeline_key=pipeline_key,
                trigger_source=trigger_source,
                message=message[:2000],
            )
        await self.bump()
        return run_id

    async def finish_run(self, run_id: int, status: str, message: str = "", stats: dict[str, Any] | None = None) -> None:
        async with self._lock:
            run = self.runs.get(run_id)
            if not run:
                return
            run.status = status
            run.message = message[:2000]
            run.stats = stats or run.stats
            run.progress = 100 if status == "completed" else run.progress
            run.finished_at = utc_now()
            run.updated_at = run.finished_at
        await self.bump()

    async def enqueue_task(
        self,
        *,
        run_id: int,
        pipeline_id: int,
        step_id: int,
        step_key: str,
        processor_key: str,
        subject_type: str,
        subject_id: int = 0,
        payload: dict[str, Any] | None = None,
        domain_kind: str = "",
        parent_task_id: int = 0,
        dedupe_key: str = "",
    ) -> int:
        async with self._lock:
            if dedupe_key:
                existing_id = self.dedupe_index.get(dedupe_key)
                existing = self.tasks.get(existing_id or 0)
                if existing and existing.status not in {"completed", "running"}:
                    existing.run_id = run_id
                    existing.pipeline_id = pipeline_id
                    existing.step_id = step_id
                    existing.step_key = step_key
                    existing.processor_key = processor_key
                    existing.queue_key = processor_key
                    existing.subject_type = subject_type
                    existing.subject_id = subject_id
                    existing.payload = payload or {}
                    existing.domain_kind = domain_kind
                    existing.parent_task_id = parent_task_id
                    existing.status = "pending"
                    existing.retry_at = ""
                    existing.error = ""
                    existing.message = ""
                    existing.updated_at = utc_now()
                    task_id = existing.id
                elif existing:
                    task_id = existing.id
                else:
                    task_id = self._create_task_locked(
                        run_id=run_id,
                        pipeline_id=pipeline_id,
                        step_id=step_id,
                        step_key=step_key,
                        processor_key=processor_key,
                        subject_type=subject_type,
                        subject_id=subject_id,
                        payload=payload or {},
                        domain_kind=domain_kind,
                        parent_task_id=parent_task_id,
                        dedupe_key=dedupe_key,
                    )
                    self.dedupe_index[dedupe_key] = task_id
            else:
                task_id = self._create_task_locked(
                    run_id=run_id,
                    pipeline_id=pipeline_id,
                    step_id=step_id,
                    step_key=step_key,
                    processor_key=processor_key,
                    subject_type=subject_type,
                    subject_id=subject_id,
                    payload=payload or {},
                    domain_kind=domain_kind,
                    parent_task_id=parent_task_id,
                    dedupe_key="",
                )
        await self.bump()
        return task_id

    def _create_task_locked(self, **kwargs: Any) -> int:
        task_id = self._next_task_id
        self._next_task_id += 1
        self.tasks[task_id] = RuntimeTask(id=task_id, queue_key=kwargs["processor_key"], **kwargs)
        return task_id

    async def claim_task(self, processor_key: str = "", processor_limits: dict[str, int] | None = None) -> RuntimeTask | None:
        async with self._lock:
            running_counts: dict[str, int] = {}
            for task in self.tasks.values():
                if task.status == "running":
                    running_counts[task.processor_key] = running_counts.get(task.processor_key, 0) + 1
            candidates = [
                task
                for task in self.tasks.values()
                if task.status in {"pending", "waiting"}
                and (not processor_key or task.processor_key == processor_key)
                and due(task.retry_at)
                and (
                    not processor_limits
                    or running_counts.get(task.processor_key, 0) < max(1, int(processor_limits.get(task.processor_key, 1)))
                )
            ]
            candidates.sort(key=lambda task: (task.updated_at, task.id))
            if not candidates:
                return None
            task = candidates[0]
            task.status = "running"
            task.attempts += 1
            task.updated_at = utc_now()
        await self.bump()
        return task

    async def complete_task(self, task_id: int, status: str, message: str = "", result: dict[str, Any] | None = None, retry: str = "") -> None:
        async with self._lock:
            task = self.tasks.get(task_id)
            if not task:
                return
            task.status = status
            task.message = message[:2000]
            task.result = result or {}
            task.retry_at = retry
            task.error = "" if status in {"completed", "skipped"} else message[:2000]
            task.updated_at = utc_now()
        await self.bump()

    async def cancel_all(self) -> None:
        async with self._lock:
            now_value = utc_now()
            self._generation = now_value
            for task in self.tasks.values():
                if task.status not in TASK_TERMINAL_STATUSES:
                    task.status = "cancelled"
                    task.updated_at = now_value
            for run in self.runs.values():
                if run.status == "running":
                    run.status = "cancelled"
                    run.finished_at = now_value
                    run.updated_at = now_value
        await self.bump()

    async def clear_all(self) -> None:
        async with self._lock:
            self._generation = utc_now()
            self.tasks.clear()
            self.runs.clear()
            self.dedupe_index.clear()
            self.logs.clear()
            self.scheduler.clear()
            self.scheduler_runs.clear()
            self.operations.clear()
        await self.bump()

    async def set_scheduler(self, job_key: str, **fields: Any) -> None:
        async with self._lock:
            item = self.scheduler.setdefault(job_key, {"job_key": job_key, "last_status": "idle"})
            item.update(fields)
            item["updated_at"] = utc_now()
        await self.bump()

    def set_scheduler_sync(self, job_key: str, **fields: Any) -> None:
        item = self.scheduler.setdefault(job_key, {"job_key": job_key, "last_status": "idle"})
        item.update(fields)
        item["updated_at"] = utc_now()
        self.bump_sync()

    def start_scheduler_run_sync(self, job_key: str, trigger_source: str = "system", message: str = "") -> int:
        run_id = self._next_scheduler_run_id
        self._next_scheduler_run_id += 1
        started_at = utc_now()
        run = {
            "id": run_id,
            "job_id": 0,
            "job_key": job_key,
            "job_type": "runtime",
            "status": "running",
            "trigger_source": trigger_source,
            "message": message[:2000],
            "stats_json": "",
            "started_at": started_at,
            "finished_at": "",
        }
        self.scheduler_runs.append(run)
        self.set_scheduler_sync(job_key, last_status="running", last_run_at=started_at, last_error="")
        return run_id

    def finish_scheduler_run_sync(self, run_id: int, status: str, message: str = "", stats_json: str = "") -> None:
        finished_at = utc_now()
        for run in reversed(self.scheduler_runs):
            if int(run.get("id") or 0) != run_id:
                continue
            run["status"] = status
            run["message"] = message[:2000]
            run["stats_json"] = stats_json[:4000]
            run["finished_at"] = finished_at
            job_key = str(run.get("job_key") or "")
            self.set_scheduler_sync(
                job_key,
                last_status=status,
                last_error="" if status == "completed" else message[:2000],
                last_run_at=run.get("started_at", ""),
            )
            return
        self.bump_sync()

    def start_operation_sync(self, name: str, message: str = "") -> int:
        operation_id = self._next_operation_id
        self._next_operation_id += 1
        self.operations[operation_id] = {
            "id": operation_id,
            "name": name,
            "status": "running",
            "message": message[:2000],
            "started_at": utc_now(),
            "finished_at": "",
        }
        self.bump_sync()
        return operation_id

    def update_operation_sync(self, operation_id: int, message: str) -> None:
        operation = self.operations.get(operation_id)
        if not operation:
            return
        operation["message"] = message[:2000]
        self.bump_sync()

    def finish_operation_sync(self, operation_id: int, status: str, message: str = "") -> None:
        operation = self.operations.get(operation_id)
        if not operation:
            return
        operation["status"] = status
        operation["message"] = message[:2000]
        operation["finished_at"] = utc_now()
        self.bump_sync()

    def clear_finished_operations_sync(self) -> int:
        removable = [
            operation_id
            for operation_id, operation in self.operations.items()
            if str(operation.get("status") or "") in {"completed", "failed", "cancelled"}
        ]
        for operation_id in removable:
            self.operations.pop(operation_id, None)
        self.bump_sync()
        return len(removable)

    def snapshot(self) -> dict[str, Any]:
        tasks = list(self.tasks.values())
        queues: dict[str, dict[str, Any]] = {}
        for task in tasks:
            queue = queues.setdefault(
                task.queue_key,
                {
                    "key": task.queue_key,
                    "name": task.queue_key,
                    "pending": 0,
                    "running": 0,
                    "failed": 0,
                    "waiting": 0,
                    "items": [],
                },
            )
            if task.status == "pending":
                queue["pending"] += 1
            elif task.status == "running":
                queue["running"] += 1
            elif task.status == "failed":
                queue["failed"] += 1
            elif task.status == "waiting":
                queue["waiting"] += 1
            if task.status in VISIBLE_TASK_STATUSES:
                queue["items"].append(self.task_dict(task))
        for queue in queues.values():
            queue["items"] = sorted(queue["items"], key=lambda item: item.get("updated_at", ""), reverse=True)[:200]
        return {
            "version": self._version,
            "generation": self._generation,
            "runs": [self.run_dict(run) for run in sorted(self.runs.values(), key=lambda item: item.id, reverse=True)[:50]],
            "queues": list(queues.values()),
            "queue_details": {key: {"items": value["items"]} for key, value in queues.items()},
            "logs": list(self.logs)[-200:],
            "scheduler": list(self.scheduler.values()),
            "scheduler_runs": list(self.scheduler_runs)[-40:],
            "operations": sorted(self.operations.values(), key=lambda item: int(item.get("id") or 0), reverse=True)[:50],
        }

    def ready_count(self, processor_key: str = "") -> int:
        return sum(
            1
            for task in self.tasks.values()
            if task.status in {"pending", "waiting"}
            and (not processor_key or task.processor_key == processor_key)
            and due(task.retry_at)
        )

    @staticmethod
    def task_dict(task: RuntimeTask) -> dict[str, Any]:
        return {
            "id": task.id,
            "run_id": task.run_id,
            "pipeline_id": task.pipeline_id,
            "step_key": task.step_key,
            "processor_key": task.processor_key,
            "domain_kind": task.domain_kind,
            "subject_type": task.subject_type,
            "subject_id": task.subject_id,
            "status": task.status,
            "attempts": task.attempts,
            "retry_after": task.retry_at,
            "last_error": task.error,
            "progress_text": task.message,
            "message": task.message,
            "updated_at": task.updated_at,
            "entry_id": task.payload.get("entry_id", task.subject_id if task.subject_type == "entry" else 0),
            "release_id": task.payload.get("release_id", task.subject_id if task.subject_type == "release" else 0),
            "candidate_id": task.payload.get("candidate_id", task.subject_id if task.subject_type == "rss_candidate" else 0),
            "download_artifact_id": task.payload.get("download_artifact_id", task.subject_id if task.subject_type == "download_artifact" else 0),
            "local_asset_id": task.payload.get("local_asset_id", task.subject_id if task.subject_type == "local_asset" else 0),
            "episode_number": task.payload.get("episode_number", ""),
            "display_title": task.payload.get("display_title", task.payload.get("title", "")),
            "title_cn": task.payload.get("title", ""),
            "release_title": task.payload.get("title", ""),
        }

    @staticmethod
    def run_dict(run: RuntimeRun) -> dict[str, Any]:
        return {
            "id": run.id,
            "pipeline_id": run.pipeline_id,
            "pipeline_key": run.pipeline_key,
            "trigger_source": run.trigger_source,
            "status": run.status,
            "progress": run.progress,
            "message": run.message,
            "stats": run.stats,
            "started_at": run.started_at,
            "updated_at": run.updated_at,
            "finished_at": run.finished_at,
        }


runtime_store = RuntimeStore()

