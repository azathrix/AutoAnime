from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


ProcessorStatus = Literal[
    "success",
    "skipped",
    "conflict",
    "failed_retryable",
    "failed_terminal",
]


@dataclass(frozen=True)
class ProcessorContext:
    task_id: int
    pipeline_id: int
    run_id: int
    step_id: int
    step_key: str
    processor_key: str
    domain_kind: str
    subject_type: str
    subject_id: int
    attempts: int


@dataclass
class ProcessorResult:
    status: ProcessorStatus = "success"
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    next_payload: dict[str, Any] = field(default_factory=dict)
    next_tasks: list[dict[str, Any]] = field(default_factory=list)
    retry_after: str = ""

    @classmethod
    def success(
        cls,
        message: str = "",
        *,
        data: dict[str, Any] | None = None,
        next_payload: dict[str, Any] | None = None,
        next_tasks: list[dict[str, Any]] | None = None,
    ) -> "ProcessorResult":
        return cls("success", message, data or {}, next_payload or {}, next_tasks or [])

    @classmethod
    def skipped(
        cls,
        message: str = "",
        *,
        data: dict[str, Any] | None = None,
        next_payload: dict[str, Any] | None = None,
    ) -> "ProcessorResult":
        return cls(status="skipped", message=message, data=data or {}, next_payload=next_payload or {})

    @classmethod
    def retryable(cls, message: str, retry_after: str = "", *, data: dict[str, Any] | None = None) -> "ProcessorResult":
        return cls(status="failed_retryable", message=message, data=data or {}, retry_after=retry_after)

    @classmethod
    def terminal(cls, message: str, *, data: dict[str, Any] | None = None) -> "ProcessorResult":
        return cls(status="failed_terminal", message=message, data=data or {})


def merge_payload(base: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    result.update(update)
    return result

