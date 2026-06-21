from __future__ import annotations

import asyncio
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
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        callback(name, delay)
        return
    loop.call_soon(callback, name, delay)

