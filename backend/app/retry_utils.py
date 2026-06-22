from __future__ import annotations

from datetime import datetime, timedelta, timezone


def extract_task_id(result: dict) -> str:
    for path in [
        ("task", "id"),
        ("tasks", 0, "id"),
        ("id",),
    ]:
        value = result
        try:
            for key in path:
                value = value[key]
        except (KeyError, IndexError, TypeError):
            continue
        if value:
            return str(value)
    return ""


def extract_file_id(result: dict) -> str:
    for path in [
        ("file", "id"),
        ("file_id",),
        ("files", 0, "id"),
        ("task", "file_id"),
        ("reference_resource", "id"),
    ]:
        value = result
        try:
            for key in path:
                value = value[key]
        except (KeyError, IndexError, TypeError):
            continue
        if value:
            return str(value)
    return ""


def is_rate_limited_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "too frequent" in text or "try again later" in text or "rate" in text and "limit" in text


def retry_after_time(settings: dict[str, str], default_minutes: int = 60) -> str:
    minutes = max(1, int(settings.get("pikpak_rate_limit_cooldown_minutes") or default_minutes))
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()


def task_retry_after(settings: dict[str, str], attempts: int) -> str:
    minutes = min(180, max(5, 5 * max(1, attempts)))
    return retry_after_time(settings, minutes)
