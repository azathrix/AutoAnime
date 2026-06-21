from __future__ import annotations

import shutil
import json
from pathlib import Path
from typing import Any

import httpx

from . import aria2_service
from . import local_downloader_service
from . import qbittorrent_service
from . import rclone_service
from .pikpak_service import build_client, get_cloud_download_url, list_offline_tasks, submit_offline_download


SUPPORTED_DOWNLOADER_TYPES = {
    "pikpak_rclone": "rclone",
    "pikpak_api": "api",
    "rclone": "rclone",
    "api": "api",
    "local": "local",
    "aria2": "aria2",
    "qb": "qb",
    "qbittorrent": "qb",
}
ACTIVE_DOWNLOADER_KEY = "_active_downloader_json"
DOWNLOAD_FAILOVER_ATTEMPTS = 3


def configured_downloaders(settings: dict[str, str]) -> list[dict[str, Any]]:
    raw = settings.get("downloaders_json") or "[]"
    try:
        rows = json.loads(raw)
    except json.JSONDecodeError:
        rows = []
    if not isinstance(rows, list):
        return []
    result: list[dict[str, Any]] = []
    for index, item in enumerate(rows):
        if not isinstance(item, dict) or item.get("enabled") is False:
            continue
        item_type = str(item.get("type") or "").strip().lower()
        backend = SUPPORTED_DOWNLOADER_TYPES.get(item_type)
        if not backend:
            continue
        result.append({
            **item,
            "_backend": backend,
            "_priority": index,
        })
    return result


def active_downloader(settings: dict[str, str]) -> dict[str, Any]:
    active_json = settings.get(ACTIVE_DOWNLOADER_KEY) or ""
    if active_json:
        try:
            active = json.loads(active_json)
        except json.JSONDecodeError:
            active = {}
        if isinstance(active, dict) and active.get("_backend"):
            return active
    rows = configured_downloaders(settings)
    if rows:
        return rows[0]
    return {
        "type": settings.get("download_backend") or "rclone",
        "name": settings.get("download_backend") or "rclone",
        "_backend": (settings.get("download_backend") or "rclone").strip().lower() or "rclone",
    }


def downloader_for_attempt(settings: dict[str, str], attempts: int) -> dict[str, Any]:
    rows = configured_downloaders(settings)
    if not rows:
        return active_downloader(settings)
    index = min(max(0, int(attempts or 0) // DOWNLOAD_FAILOVER_ATTEMPTS), len(rows) - 1)
    return rows[index]


def failover_exhausted(settings: dict[str, str], attempts: int) -> bool:
    rows = configured_downloaders(settings)
    return bool(rows) and int(attempts or 0) >= len(rows) * DOWNLOAD_FAILOVER_ATTEMPTS


def bind_downloader(settings: dict[str, str], downloader: dict[str, Any]) -> dict[str, str]:
    result = dict(settings)
    active = dict(downloader)
    result[ACTIVE_DOWNLOADER_KEY] = json.dumps(active, ensure_ascii=False)
    backend = str(active.get("_backend") or "").strip().lower()
    if backend:
        result["download_backend"] = backend
    remote_dir = str(active.get("remote_dir") or "").strip()
    if remote_dir:
        result["library_root"] = remote_dir
    return result


def settings_for_attempt(settings: dict[str, str], attempts: int) -> dict[str, str]:
    return bind_downloader(settings, downloader_for_attempt(settings, attempts))


def backend_key(settings: dict[str, str]) -> str:
    return str(active_downloader(settings).get("_backend") or "rclone").strip().lower() or "rclone"


def provider_key(settings: dict[str, str]) -> str:
    backend = backend_key(settings)
    downloader = active_downloader(settings)
    provider_name = str(downloader.get("name") or downloader.get("type") or "").strip()
    if provider_name:
        if "_priority" in downloader:
            return f"{provider_name}#{int(downloader.get('_priority') or 0) + 1}"
        return provider_name
    if backend == "rclone":
        return (settings.get("rclone_remote") or "rclone").strip().rstrip(":") or "rclone"
    if backend == "local":
        return "local"
    return backend


def settings_for_provider(settings: dict[str, str], provider: str) -> dict[str, str]:
    wanted = str(provider or "").strip()
    if not wanted:
        return settings_for_attempt(settings, 0)
    for downloader in configured_downloaders(settings):
        candidate = bind_downloader(settings, downloader)
        if provider_key(candidate) == wanted:
            return candidate
    return settings_for_attempt(settings, 0)


def uses_remote_listing(settings: dict[str, str]) -> bool:
    return backend_key(settings) in {"rclone", "local", "aria2", "qb"}


def needs_poll(settings: dict[str, str]) -> bool:
    return backend_key(settings) in {"rclone", "aria2", "qb"}


def remote_file_id(item: dict[str, Any]) -> str:
    return str(item.get("id") or item.get("file_id") or item.get("fileId") or "")


async def submit_download(settings: dict[str, str], source: str, target_dir: str, name: str = "") -> dict[str, Any]:
    if backend_key(settings) == "rclone":
        return await rclone_service.add_url(settings, source, target_dir, name)
    if backend_key(settings) == "local":
        return await local_downloader_service.submit(settings, source, target_dir, name)
    if backend_key(settings) == "aria2":
        return await aria2_service.submit(settings, source, target_dir, name)
    if backend_key(settings) == "qb":
        return await qbittorrent_service.submit(settings, source, target_dir, name)
    return await submit_offline_download(settings, source, target_dir, name)


async def list_tasks(settings: dict[str, str]) -> list[dict[str, Any]]:
    if backend_key(settings) in {"rclone", "local"}:
        return []
    if backend_key(settings) == "aria2":
        return await aria2_service.list_tasks(settings)
    if backend_key(settings) == "qb":
        return await qbittorrent_service.list_tasks(settings)
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
    if backend_key(settings) == "aria2":
        return await aria2_service.list_files(settings, root_path, recursive=recursive)
    if backend_key(settings) == "qb":
        return await qbittorrent_service.list_files(settings, root_path, recursive=recursive)
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

    if backend_key(settings) in {"aria2", "qb"}:
        source_path = Path(file_id or source)
        if not source_path.exists():
            raise RuntimeError(f"下载器源文件不存在: {file_id or source}")
        shutil.copy2(source_path, target_path)
        if progress_cb:
            await progress_cb(100, "本地下载器复制完成")
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
