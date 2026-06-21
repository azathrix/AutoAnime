from __future__ import annotations

from collections.abc import Awaitable, Callable

from .pipeline_models import ProcessorContext, ProcessorResult


Processor = Callable[[ProcessorContext, dict], Awaitable[ProcessorResult]]

_processors: dict[str, Processor] = {}


def register_processor(key: str, processor: Processor) -> None:
    if not key:
        raise ValueError("processor key is required")
    _processors[key] = processor


def get_processor(key: str) -> Processor | None:
    return _processors.get(key)


def registered_processor_keys() -> list[str]:
    return sorted(_processors)


def clear_processors() -> None:
    _processors.clear()

