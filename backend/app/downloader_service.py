from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import httpx

from . import local_downloader_service
from . import rclone_service
from .pikpak_service import build_client, get_cloud_download_url, list_offline_tasks, submit_offline_download


def backend_key(settings: dict[str, str]) -> str:
    return (settings.get("download_backend") or "rclone").strip().lower() or "rclone"


def provider_key(settings: dict[str, str]) -> str:
    backend = backend_key(settings)
    if backend == "rclone":
        return (settings.get("rclone_remote") or "rclone").strip().rstrip(":") or "rclone"
    if backend == "local":
        return "local"
    return backend


def uses_remote_listing(settings: dict[str, str]) -> bool:
    return backend_key(settings) in {"rclone", "local"}


def needs_poll(settings: dict[str, str]) -> bool:
    return backend_key(settings) == "rclone"


def remote_file_id(item: dict[str, Any]) -> str:
    return str(item.get("id") or item.get("file_id") or item.get("fileId") or "")


async def submit_download(settings: dict[str, str], source: str, target_dir: str, name: str = "") -> dict[str, Any]:
    if backend_key(settings) == "rclone":
        return await rclone_service.add_url(settings, source, target_dir, name)
    if backend_key(settings) == "local":
        return await local_downloader_service.submit(settings, source, target_dir, name)
    return await submit_offline_download(settings, source, target_dir, name)


async def list_tasks(settings: dict[str, str]) -> list[dict[str, Any]]:
    if backend_key(settings) in {"rclone", "local"}:
        return []
    return await list_offline_tasks(settings)


async def list_remote_files(
    settings: dict[str, str],
    root_path: str,
    *,
    recursive: bool = True,
    max_depth: int = 4,
    max_items: int = 2000,
) -> list[dict[str, Any]]:
    if backend_key(settings) == "rclone":
        return await rclone_service.list_files(settings, root_path, recursive=recursive)
    if backend_key(settings) == "local":
        return await local_downloader_service.list_files(settings, root_path, recursive=recursive)
    api = build_client(settings)
    if settings.get("pikpak_auth_mode") == "password":
        await api.login()
    folders = await api.path_to_id(root_path, create=False)
    if not folders:
        return []
    root_id = folders[-1]["id"]
    collected: list[dict[str, Any]] = []

    async def walk(parent_id: str, current_path: str, depth: int) -> None:
        if len(collected) >= max_items or depth > max_depth:
            return
        page_token = None
        while len(collected) < max_items:
            data = await api.file_list(size=100, parent_id=parent_id, next_page_token=page_token)
            files = data.get("files", []) if isinstance(data, dict) else []
            folders_to_walk: list[tuple[str, str]] = []
            for item in files:
                name = str(item.get("name") or "")
                path = f"{current_path.rstrip('/')}/{name}" if current_path else name
                item["remote_path"] = path
                kind = str(item.get("kind") or "")
                mime_type = str(item.get("mime_type") or item.get("mimeType") or "")
                is_folder = kind.endswith("#folder") or mime_type == "application/vnd.google-apps.folder"
                if is_folder and item.get("id"):
                    folders_to_walk.append((str(item["id"]), path))
                else:
                    collected.append(item)
                    if len(collected) >= max_items:
                        break
            for folder_id, folder_path in folders_to_walk:
                await walk(folder_id, folder_path, depth + 1)
            page_token = data.get("next_page_token") if isinstance(data, dict) else ""
            if not page_token:
                break

    await walk(root_id, root_path.rstrip("/"), 0)
    return collected


async def download_to_local(
    settings: dict[str, str],
    file_id: str,
    source: str,
    target: str,
    progress_cb=None,
) -> None:
    target_path = Path(target)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    if backend_key(settings) == "local":
        await local_downloader_service.copy_to_local(settings, source, target, progress_cb=progress_cb)
        return

    if backend_key(settings) == "rclone" and source:
        await rclone_service.copy_to_local(settings, source, target, progress_cb=progress_cb)
        return

    if file_id:
        url = await get_cloud_download_url(settings, file_id)
        proxy = settings.get("pikpak_proxy") or None
        async with httpx.AsyncClient(proxy=proxy, timeout=None, follow_redirects=True) as client:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                with target_path.open("wb") as output:
                    async for chunk in response.aiter_bytes():
                        if chunk:
                            output.write(chunk)
        return

    source_path = Path(source)
    if not source_path.exists():
        raise RuntimeError("缺少下载文件 ID，且远端路径不是本机可访问文件")
    shutil.copy2(source_path, target_path)
