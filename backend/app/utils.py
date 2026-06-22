from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from .config import DATA_DIR
from .parser import parse_episode
from .scanner import language_tokens, priority_match, priority_pick

def row_to_dict(row: Any) -> dict[str, Any]:
    return dict(row) if row is not None else {}

def normalize_json_list_text(value: str) -> str:
    if not value:
        return "[]"
    raw = str(value).strip()
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return json.dumps([str(item).strip() for item in parsed if str(item).strip()], ensure_ascii=False)
    except Exception:
        pass
    items = [item.strip() for item in raw.replace(",", "\n").splitlines() if item.strip()]
    return json.dumps(items, ensure_ascii=False)

def int_setting(value: Any, default: int = 0, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    if minimum is not None:
        parsed = max(minimum, parsed)
    if maximum is not None:
        parsed = min(maximum, parsed)
    return parsed

def subtitle_embedded_value(format_value: str) -> int:
    return 1 if str(format_value or "").strip().lower() in {"embedded", "hardsub", "burned"} else 0

def split_input_lines(value: str) -> list[str]:
    return [line.strip() for line in str(value or "").splitlines() if line.strip()]

def safe_upload_filename(value: str) -> str:
    name = Path(value or "upload.bin").name.strip() or "upload.bin"
    name = re.sub(r"[^\w.\-\u4e00-\u9fff\[\]\(\) ]+", "_", name, flags=re.UNICODE)
    return name[:180] or "upload.bin"

def upload_root() -> Path:
    root = DATA_DIR / "uploads"
    root.mkdir(parents=True, exist_ok=True)
    return root

def validate_upload_temp_path(value: str) -> Path:
    root = upload_root().resolve()
    path = Path(value or "").resolve()
    if root not in path.parents and path != root:
        raise HTTPException(status_code=400, detail="上传临时文件路径无效")
    if not path.exists():
        raise HTTPException(status_code=404, detail="上传临时文件不存在")
    return path

def is_valid_resource_reference(value: str) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return False
    return text.startswith(("magnet:?", "http://", "https://", "ftp://", "thunder://", "ed2k://"))

def is_valid_subtitle_reference(value: str) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return False
    if text.startswith(("http://", "https://")):
        return True
    return text.endswith((".ass", ".srt", ".ssa", ".vtt", ".sup", ".sub"))

def parsed_episode_or_fallback(text: str, fallback: int) -> int:
    parsed = parse_episode(text)
    return parsed if parsed > 0 else max(1, fallback)

def rows_to_dicts(rows: list[Any]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]

def seconds_until(value: str) -> int:
    if not value:
        return 0
    try:
        target = datetime.fromisoformat(value)
    except ValueError:
        return 0
    if target.tzinfo is None:
        target = target.replace(tzinfo=timezone.utc)
    return max(0, int((target - datetime.now(timezone.utc)).total_seconds()))

def seconds_since(value: str) -> int:
    if not value:
        return 0
    try:
        target = datetime.fromisoformat(value)
    except ValueError:
        return 0
    if target.tzinfo is None:
        target = target.replace(tzinfo=timezone.utc)
    return max(0, int((datetime.now(timezone.utc) - target).total_seconds()))

def enrich_retry_rows(rows: list[Any]) -> list[dict[str, Any]]:
    result = rows_to_dicts(rows)
    for row in result:
        retry_seconds = seconds_until(str(row.get("retry_after") or ""))
        row["retry_seconds"] = retry_seconds
        row["waiting_retry"] = row.get("status") == "waiting" or (row.get("status") == "pending" and retry_seconds > 0)
        row["display_title"] = (
            row.get("title_cn")
            or row.get("series_title")
            or row.get("release_title")
            or row.get("local_path")
            or row.get("artifact_name")
            or row.get("title")
            or ""
        )
        if row.get("progress_text"):
            row["display_reason"] = row.get("progress_text")
        elif row.get("reason"):
            row["display_reason"] = row.get("reason")
        elif row.get("status") == "running":
            row["display_reason"] = "worker 正在处理当前任务"
        elif row["waiting_retry"]:
            row["display_reason"] = f"等待重试，剩余 {retry_seconds} 秒"
        elif row.get("last_error"):
            row["display_reason"] = row.get("last_error")
        elif row.get("status") == "pending":
            row["display_reason"] = "已入队，等待当前批次执行"
        else:
            row["display_reason"] = ""
    return result

def split_setting(value: str) -> list[str]:
    return [x.strip() for x in (value or "").splitlines() if x.strip()]

def split_candidate_values(value: Any) -> list[str]:
    return [item.strip() for item in str(value or "").split(",") if item.strip()]

def entry_scope_label(item: dict[str, Any]) -> str:
    for key in ("season_label", "arc_label", "part_label", "special_label"):
        value = str(item.get(key) or "").strip()
        if value:
            return value
    season_number = int(item.get("season_number") or 0)
    if season_number > 1:
        return f"Season {season_number:02d}"
    return ""

def entry_badge_text(item: dict[str, Any]) -> str:
    scope = entry_scope_label(item)
    if scope:
        return scope
    kind = str(item.get("entry_kind") or "").strip()
    if kind == "special":
        return "特别篇"
    if kind == "part":
        return "篇章"
    if kind == "arc":
        return "章节"
    return "Season 01"

def enrich_catalog_entry(item: dict[str, Any]) -> dict[str, Any]:
    result = dict(item)
    work_display_title = str(result.get("work_title") or result.get("title_root") or result.get("display_title") or result.get("title_cn") or "").strip()
    scope_label = entry_scope_label(result)
    result["work_display_title"] = work_display_title
    result["entry_scope_label"] = scope_label
    result["entry_badge_text"] = entry_badge_text(result)
    result["entry_display_title"] = str(result.get("display_title") or result.get("title_cn") or work_display_title).strip()
    result["entry_secondary_title"] = scope_label or work_display_title
    return result

def can_resolve_priority(values: list[str], priority: list[str], field: str = "") -> bool:
    values_clean = sorted({value for value in values if value})
    if len(values_clean) <= 1:
        return True
    return bool(priority_pick(values_clean, priority, field))

def rank_subtitle_languages(values: list[str], priority: list[str], token_index: int) -> list[str]:
    values_clean = sorted({value for value in values if value})
    if not values_clean or not priority:
        return values_clean
    for preferred in priority:
        matched = [
            value
            for value in values_clean
            if len(language_tokens(value)) > token_index
            and priority_match(language_tokens(value)[token_index], preferred, "language")
        ]
        if matched:
            return matched
    return values_clean

def pick_subtitle_language(values: list[str], primary: list[str], secondary: list[str]) -> str:
    candidates = rank_subtitle_languages(values, primary, 0)
    if len(candidates) == 1:
        return candidates[0]
    candidates = rank_subtitle_languages(candidates, secondary, 1)
    if len(candidates) == 1:
        return candidates[0]
    return ""

def summarize_seasonal_entry(item: dict[str, Any]) -> dict[str, Any]:
    return dict(item)
