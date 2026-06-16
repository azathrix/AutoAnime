from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any


def enabled(settings: dict[str, str]) -> bool:
    return (settings.get("cloud_transfer_backend") or "rclone").lower() == "rclone"


def command(settings: dict[str, str]) -> str:
    return settings.get("rclone_command") or "rclone"


def remote(settings: dict[str, str]) -> str:
    return (settings.get("rclone_remote") or "pikpak").rstrip(":")


def remote_path(settings: dict[str, str], path: str) -> str:
    clean = (path or "").strip()
    if clean.startswith(f"{remote(settings)}:"):
        return clean
    clean = clean.lstrip("/")
    return f"{remote(settings)}:{clean}"


def config_args(settings: dict[str, str]) -> list[str]:
    config_path = settings.get("rclone_config_path") or ""
    if not config_path:
        return []
    return ["--config", config_path]


async def run_rclone(settings: dict[str, str], args: list[str], timeout: float | None = 300) -> str:
    process = await asyncio.create_subprocess_exec(
        command(settings),
        *config_args(settings),
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        process.kill()
        await process.communicate()
        raise RuntimeError("rclone 命令超时")
    output = stdout.decode("utf-8", errors="replace").strip()
    error = stderr.decode("utf-8", errors="replace").strip()
    if process.returncode != 0:
        detail = error or output or f"exit code {process.returncode}"
        raise RuntimeError(detail[:2000])
    return output


async def add_url(settings: dict[str, str], source: str, target_dir: str, name: str = "") -> dict[str, Any]:
    args = ["backend", "addurl", remote_path(settings, target_dir), source]
    if name:
        args.extend(["-o", f"name={name}"])
    output = await run_rclone(settings, args, timeout=300)
    if not output:
        return {}
    try:
        data = json.loads(output)
        return data if isinstance(data, dict) else {"result": data}
    except json.JSONDecodeError:
        return {"output": output}


async def list_files(settings: dict[str, str], path: str, recursive: bool = True) -> list[dict[str, Any]]:
    args = ["lsjson", remote_path(settings, path)]
    if recursive:
        args.append("--recursive")
    output = await run_rclone(settings, args, timeout=300)
    if not output:
        return []
    data = json.loads(output)
    if not isinstance(data, list):
        return []
    result: list[dict[str, Any]] = []
    root = path.rstrip("/")
    for item in data:
        if not isinstance(item, dict):
            continue
        item_path = str(item.get("Path") or item.get("Name") or "")
        name = str(item.get("Name") or Path(item_path).name)
        cloud_path = f"{root}/{item_path}".replace("//", "/") if item_path else f"{root}/{name}"
        result.append(
            {
                "id": item.get("ID") or item.get("Id") or item.get("id") or cloud_path,
                "name": name,
                "cloud_path": cloud_path,
                "size": item.get("Size") or 0,
                "is_dir": bool(item.get("IsDir")),
                "raw": item,
            }
        )
    return result


async def copy_to_local(settings: dict[str, str], source_path: str, target_path: str) -> None:
    Path(target_path).parent.mkdir(parents=True, exist_ok=True)
    await run_rclone(
        settings,
        ["copyto", remote_path(settings, source_path), target_path],
        timeout=None,
    )
