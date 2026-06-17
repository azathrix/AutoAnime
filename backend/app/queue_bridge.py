from __future__ import annotations

from collections.abc import Callable

QueueTrigger = Callable[[str, float | None], None]

_trigger: QueueTrigger | None = None


def register_queue_trigger(callback: QueueTrigger) -> None:
    global _trigger
    _trigger = callback


def request_queue_trigger(name: str, delay: float | None = None) -> None:
    callback = _trigger
    if not callback or not name:
        return
    callback(name, delay)


def request_queue_triggers(names: list[str], delay: float | None = None) -> None:
    callback = _trigger
    if not callback:
        return
    seen: set[str] = set()
    for name in names:
        if not name or name in seen:
            continue
        seen.add(name)
        callback(name, delay)
