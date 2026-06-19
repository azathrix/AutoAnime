from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any


def enabled(settings: dict[str, str]) -> bool:
    return (settings.get("download_backend") or "rclone").lower() == "rclone"


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


def remote_exists(settings: dict[str, str]) -> bool:
    config_path = settings.get("rclone_config_path") or ""
    if not config_path:
        return True
    path = Path(config_path)
    if not path.exists():
        return False
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return f"[{remote(settings)}]" in text


def remote_config_block(settings: dict[str, str]) -> dict[str, str]:
    config_path = settings.get("rclone_config_path") or ""
    if not config_path:
        return {}
    path = Path(config_path)
    if not path.exists():
        return {}
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {}
    in_block = False
    result: dict[str, str] = {}
    header = f"[{remote(settings)}]"
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if in_block:
                break
            in_block = stripped == header
            continue
        if in_block and "=" in stripped:
            key, value = stripped.split("=", 1)
            result[key.strip()] = value.strip()
    return result


def remote_has_token(settings: dict[str, str]) -> bool:
    block = remote_config_block(settings)
    token = block.get("token") or ""
    return bool(token.strip())


async def ensure_config(settings: dict[str, str]) -> None:
    if not enabled(settings):
        return
    created = False
    if remote_exists(settings) and remote_has_token(settings):
        return
    username = (settings.get("pikpak_username") or "").strip()
    password = settings.get("pikpak_password") or ""
    config_path = settings.get("rclone_config_path") or ""
    if not username or not password or not config_path:
        raise RuntimeError("rclone PikPak 未配置，且缺少 PikPak 用户名或密码，无法自动初始化")
    if not remote_exists(settings):
        obscured = await obscure_password(settings, password)
        path = Path(config_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = ""
        if path.exists():
            existing = path.read_text(encoding="utf-8", errors="replace").rstrip()
        block = "\n".join(
            [
                f"[{remote(settings)}]",
                "type = pikpak",
                f"user = {username}",
                f"pass = {obscured}",
                "",
            ]
        )
        content = f"{existing}\n\n{block}" if existing else block
        path.write_text(content, encoding="utf-8")
        created = True
    if created or not remote_has_token(settings):
        await reconnect(settings)


async def reconnect(settings: dict[str, str]) -> None:
    process = await asyncio.create_subprocess_exec(
        command(settings),
        *config_args(settings),
        "config",
        "reconnect",
        f"{remote(settings)}:",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    output = stdout.decode("utf-8", errors="replace").strip()
    error = stderr.decode("utf-8", errors="replace").strip()
    if process.returncode != 0:
        raise RuntimeError((error or output or "rclone config reconnect 失败")[:2000])


async def version(settings: dict[str, str]) -> str:
    process = await asyncio.create_subprocess_exec(
        command(settings),
        "version",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    output = stdout.decode("utf-8", errors="replace").strip()
    error = stderr.decode("utf-8", errors="replace").strip()
    if process.returncode != 0:
        raise RuntimeError((error or output or "rclone version 失败")[:2000])
    return output


async def obscure_password(settings: dict[str, str], password: str) -> str:
    process = await asyncio.create_subprocess_exec(
        command(settings),
        "obscure",
        password,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    output = stdout.decode("utf-8", errors="replace").strip()
    error = stderr.decode("utf-8", errors="replace").strip()
    if process.returncode != 0 or not output:
        raise RuntimeError((error or output or "rclone obscure 失败")[:2000])
    return output


async def run_rclone(settings: dict[str, str], args: list[str], timeout: float | None = 300) -> str:
    await ensure_config(settings)
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
    await mkdir(settings, target_dir)
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


async def mkdir(settings: dict[str, str], path: str) -> None:
    await run_rclone(settings, ["mkdir", remote_path(settings, path)], timeout=300)


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
        item_remote_path = f"{root}/{item_path}".replace("//", "/") if item_path else f"{root}/{name}"
        result.append(
            {
                "id": item.get("ID") or item.get("Id") or item.get("id") or item_remote_path,
                "name": name,
                "remote_path": item_remote_path,
                "size": item.get("Size") or 0,
                "is_dir": bool(item.get("IsDir")),
                "raw": item,
            }
        )
    return result


async def copy_to_local(settings: dict[str, str], source_path: str, target_path: str, progress_cb=None) -> None:
    Path(target_path).parent.mkdir(parents=True, exist_ok=True)
    await run_rclone_streaming(
        settings,
        [
            "copyto",
            remote_path(settings, source_path),
            target_path,
            "--progress",
            "--stats",
            "1s",
            "--stats-one-line",
        ],
        progress_cb=progress_cb,
    )


async def run_rclone_streaming(settings: dict[str, str], args: list[str], progress_cb=None) -> str:
    await ensure_config(settings)
    process = await asyncio.create_subprocess_exec(
        command(settings),
        *config_args(settings),
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    chunks: list[str] = []

    async def consume(stream) -> None:
        while True:
            line = await stream.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            chunks.append(text)
            match = re.search(r"(\d{1,3})%", text)
            if match and progress_cb:
                percent = max(0, min(100, int(match.group(1))))
                await progress_cb(percent, text[:500])

    await asyncio.gather(consume(process.stdout), consume(process.stderr))
    return_code = await process.wait()
    output = "\n".join(chunks).strip()
    if return_code != 0:
        raise RuntimeError((output or f"exit code {return_code}")[:2000])
    if progress_cb:
        await progress_cb(100, "同步完成")
    return output

