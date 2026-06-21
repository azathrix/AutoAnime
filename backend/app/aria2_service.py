from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import httpx


def _active(settings: dict[str, str]) -> dict[str, Any]:
    try:
        value = json.loads(settings.get("_active_downloader_json") or "{}")
    except json.JSONDecodeError:
        value = {}
    return value if isinstance(value, dict) else {}


def rpc_url(settings: dict[str, str]) -> str:
    active = _active(settings)
    return str(active.get("rpc_url") or active.get("url") or "http://127.0.0.1:6800/jsonrpc").strip()


def rpc_token(settings: dict[str, str]) -> str:
    active = _active(settings)
    return str(active.get("token") or active.get("secret") or "").strip()


def _token_arg(settings: dict[str, str]) -> list[str]:
    token = rpc_token(settings)
    return [f"token:{token}"] if token else []


async def _rpc(settings: dict[str, str], method: str, params: list[Any] | None = None) -> Any:
    payload = {
        "jsonrpc": "2.0",
        "id": hashlib.sha1(f"{method}:{params}".encode()).hexdigest()[:12],
        "method": method,
        "params": [*_token_arg(settings), *(params or [])],
    }
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(rpc_url(settings), json=payload)
        response.raise_for_status()
        data = response.json()
    if data.get("error"):
        message = data["error"].get("message") if isinstance(data["error"], dict) else str(data["error"])
        raise RuntimeError(message or "aria2 RPC 调用失败")
    return data.get("result")


def _file_path(task: dict[str, Any]) -> str:
    files = task.get("files") if isinstance(task.get("files"), list) else []
    for item in files:
        path = str(item.get("path") or "").strip()
        if path:
            return path
    return ""


def _task_item(task: dict[str, Any]) -> dict[str, Any]:
    gid = str(task.get("gid") or "")
    status = str(task.get("status") or "")
    path = _file_path(task)
    phase = {
        "complete": "PHASE_TYPE_COMPLETE",
        "error": "PHASE_TYPE_ERROR",
        "removed": "PHASE_TYPE_ERROR",
    }.get(status, "PHASE_TYPE_RUNNING")
    return {
        "id": gid,
        "phase": phase,
        "file_id": path,
        "reference_resource": {"id": path},
        "name": Path(path).name if path else gid,
        "remote_path": path,
        "message": task.get("errorMessage") or status,
        "raw": task,
    }


async def submit(settings: dict[str, str], source: str, target_dir: str, name: str = "") -> dict[str, Any]:
    Path(target_dir).mkdir(parents=True, exist_ok=True)
    options: dict[str, Any] = {"dir": target_dir}
    if name:
        options["out"] = name
    gid = await _rpc(settings, "aria2.addUri", [[source], options])
    return {"task_id": str(gid or ""), "id": str(gid or "")}


async def list_tasks(settings: dict[str, str]) -> list[dict[str, Any]]:
    keys = ["gid", "status", "files", "errorMessage"]
    active = await _rpc(settings, "aria2.tellActive", [keys]) or []
    waiting = await _rpc(settings, "aria2.tellWaiting", [0, 100, keys]) or []
    stopped = await _rpc(settings, "aria2.tellStopped", [0, 100, keys]) or []
    rows = [item for item in [*active, *waiting, *stopped] if isinstance(item, dict)]
    return [_task_item(item) for item in rows]


async def list_files(settings: dict[str, str], root_path: str, recursive: bool = True) -> list[dict[str, Any]]:
    root = Path(root_path)
    if not root.exists():
        return []
    files = root.rglob("*") if recursive else root.glob("*")
    result: list[dict[str, Any]] = []
    for item in sorted(files, key=lambda value: str(value).lower()):
        result.append(
            {
                "id": str(item),
                "file_id": str(item),
                "name": item.name,
                "remote_path": str(item),
                "size": item.stat().st_size if item.is_file() else 0,
                "is_dir": item.is_dir(),
            }
        )
    return result
