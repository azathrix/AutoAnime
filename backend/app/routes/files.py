from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from ..config import MEDIA_ROOT


router = APIRouter()

VIDEO_SUFFIXES = {".mkv", ".mp4", ".avi", ".mov", ".wmv", ".ts", ".m2ts", ".flv", ".webm"}
SUBTITLE_SUFFIXES = {".ass", ".srt", ".ssa", ".vtt", ".sup", ".sub"}


def _media_root() -> Path:
    return Path(MEDIA_ROOT).resolve()


def _resolve_media_path(value: str) -> Path:
    root = _media_root()
    raw = str(value or "").strip()
    path = Path(raw) if raw else root
    if not path.is_absolute():
        path = root / path
    resolved = path.resolve()
    if resolved != root and root not in resolved.parents:
        raise HTTPException(status_code=400, detail="只能浏览媒体目录")
    if not resolved.exists():
        raise HTTPException(status_code=404, detail="路径不存在")
    if not resolved.is_dir():
        raise HTTPException(status_code=400, detail="请选择目录")
    return resolved


def _item_payload(path: Path) -> dict[str, Any] | None:
    suffix = path.suffix.lower()
    if path.is_dir():
        kind = "directory"
    elif suffix in VIDEO_SUFFIXES:
        kind = "video"
    elif suffix in SUBTITLE_SUFFIXES:
        kind = "subtitle"
    else:
        return None
    stat = path.stat()
    return {
        "name": path.name,
        "path": str(path),
        "kind": kind,
        "size": stat.st_size if path.is_file() else 0,
        "updated_at": stat.st_mtime,
    }


@router.get("/api/files/browse")
async def api_browse_files(path: str = Query("")) -> dict[str, Any]:
    root = _media_root()
    current = _resolve_media_path(path)
    parent = ""
    current_parent = current.parent.resolve()
    if current != root and (current_parent == root or root in current_parent.parents):
        parent = str(current_parent)
    items = []
    for child in sorted(current.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
        payload = _item_payload(child)
        if payload:
            items.append(payload)
    return {
        "root": str(root),
        "current": str(current),
        "parent": parent,
        "items": items,
    }
