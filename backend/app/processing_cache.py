from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from .database import connect


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_table(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS processing_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cache_type TEXT NOT NULL,
            cache_key TEXT NOT NULL UNIQUE,
            source_ref TEXT NOT NULL DEFAULT '',
            content_json TEXT NOT NULL,
            hit_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            expires_at TEXT NOT NULL DEFAULT '',
            last_hit_at TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_processing_cache_type ON processing_cache(cache_type)"
    )


def normalize_source_ref(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    btih = re.search(r"btih:([a-zA-Z0-9]+)", text, re.I)
    if btih:
        return f"magnet:btih:{btih.group(1).lower()}"
    return re.sub(r"\s+", "", text).lower()


def cache_key(cache_type: str, source_ref: str) -> str:
    normalized = normalize_source_ref(source_ref)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return f"{cache_type}:{digest}"


def first_resource_ref(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _expired(expires_at: str) -> bool:
    if not expires_at:
        return False
    try:
        return datetime.fromisoformat(expires_at) <= datetime.now(timezone.utc)
    except ValueError:
        return False


def get_cached_json(cache_type: str, source_ref: str) -> Any | None:
    if not source_ref:
        return None
    key = cache_key(cache_type, source_ref)
    with connect() as conn:
        _ensure_table(conn)
        row = conn.execute(
            "SELECT content_json, expires_at FROM processing_cache WHERE cache_key=?",
            (key,),
        ).fetchone()
        if not row:
            return None
        if _expired(str(row["expires_at"] or "")):
            conn.execute("DELETE FROM processing_cache WHERE cache_key=?", (key,))
            return None
        try:
            payload = json.loads(str(row["content_json"] or "null"))
        except json.JSONDecodeError:
            conn.execute("DELETE FROM processing_cache WHERE cache_key=?", (key,))
            return None
        conn.execute(
            """
            UPDATE processing_cache
            SET hit_count=hit_count+1, last_hit_at=?, updated_at=?
            WHERE cache_key=?
            """,
            (utc_now(), utc_now(), key),
        )
        return payload


def set_cached_json(
    cache_type: str,
    source_ref: str,
    payload: Any,
    *,
    ttl_seconds: int = 0,
) -> None:
    if not source_ref:
        return
    ts = utc_now()
    expires_at = ""
    if ttl_seconds > 0:
        expires_at = (datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)).isoformat()
    key = cache_key(cache_type, source_ref)
    content = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    with connect() as conn:
        _ensure_table(conn)
        conn.execute(
            """
            INSERT INTO processing_cache
              (cache_type, cache_key, source_ref, content_json, hit_count, created_at, updated_at, expires_at, last_hit_at)
            VALUES (?, ?, ?, ?, 0, ?, ?, ?, '')
            ON CONFLICT(cache_key) DO UPDATE SET
              cache_type=excluded.cache_type,
              source_ref=excluded.source_ref,
              content_json=excluded.content_json,
              updated_at=excluded.updated_at,
              expires_at=excluded.expires_at
            """,
            (cache_type, key, source_ref, content, ts, ts, expires_at),
        )


def clear_processing_cache() -> int:
    with connect() as conn:
        _ensure_table(conn)
        cursor = conn.execute("DELETE FROM processing_cache")
        return int(cursor.rowcount or 0)
