from __future__ import annotations

import base64
import json
from typing import Any

from .db import save_settings
from . import rclone_service

try:
    from pikpakapi import PikPakApi
except Exception:  # pragma: no cover
    PikPakApi = None


def encode_tokens(access_token: str, refresh_token: str) -> str:
    payload = {
        "access_token": access_token.strip(),
        "refresh_token": refresh_token.strip(),
    }
    return base64.b64encode(json.dumps(payload).encode()).decode()


async def persist_refreshed_tokens(api: Any) -> None:
    save_settings(
        {
            "pikpak_access_token": api.access_token or "",
            "pikpak_refresh_token": api.refresh_token or "",
        }
    )


def build_client(settings: dict[str, str]) -> Any:
    if PikPakApi is None:
        raise RuntimeError("PikPakAPI 未安装")

    httpx_args: dict[str, Any] = {"timeout": 30}
    if settings.get("pikpak_proxy"):
        httpx_args["proxy"] = settings["pikpak_proxy"]

    mode = settings.get("pikpak_auth_mode", "token")
    if mode == "password":
        return PikPakApi(
            username=settings.get("pikpak_username") or "",
            password=settings.get("pikpak_password") or "",
            httpx_client_args=httpx_args,
            token_refresh_callback=persist_refreshed_tokens,
        )

    access_token = settings.get("pikpak_access_token", "")
    refresh_token = settings.get("pikpak_refresh_token", "")
    if not access_token or not refresh_token:
        raise RuntimeError("需要配置 PikPak access_token 和 refresh_token")

    return PikPakApi(
        encoded_token=encode_tokens(access_token, refresh_token),
        httpx_client_args=httpx_args,
        token_refresh_callback=persist_refreshed_tokens,
    )


async def prepare_offline_captcha(api: Any) -> None:
    action = f"POST:https://{api.PIKPAK_API_HOST}/drive/v1/files"
    result = await api.captcha_init(action=action)
    captcha_token = result.get("captcha_token", "")
    if captcha_token:
        api.captcha_token = captcha_token


async def submit_offline_download(settings: dict[str, str], source: str, target_dir: str, name: str = "") -> dict[str, Any]:
    if rclone_service.enabled(settings):
        return await rclone_service.add_url(settings, source, target_dir, name)
    api = build_client(settings)
    if settings.get("pikpak_auth_mode") == "password":
        await api.login()
    folders = await api.path_to_id(target_dir, create=True)
    parent_id = folders[-1]["id"] if folders else None
    kwargs = {"parent_id": parent_id} if parent_id else {}
    try:
        return await api.offline_download(source, **kwargs)
    except Exception as exc:
        error = str(exc)
        captcha_error = "Verification code is invalid" in error or "captcha" in error.lower()
        if not captcha_error:
            raise
        await prepare_offline_captcha(api)
        return await api.offline_download(source, **kwargs)


async def list_offline_tasks(settings: dict[str, str]) -> list[dict[str, Any]]:
    api = build_client(settings)
    if settings.get("pikpak_auth_mode") == "password":
        await api.login()
    phases = [
        "PHASE_TYPE_PENDING",
        "PHASE_TYPE_RUNNING",
        "PHASE_TYPE_ERROR",
        "PHASE_TYPE_COMPLETE",
    ]
    data = await api.offline_list(size=1000, phase=phases)
    return data.get("tasks", []) if isinstance(data, dict) else []


async def list_cloud_files(
    settings: dict[str, str],
    root_path: str,
    max_depth: int = 4,
    max_items: int = 2000,
) -> list[dict[str, Any]]:
    if rclone_service.enabled(settings):
        return await rclone_service.list_files(settings, root_path, recursive=True)
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


async def rename_cloud_file(settings: dict[str, str], file_id: str, new_name: str) -> None:
    if not file_id:
        return
    api = build_client(settings)
    if settings.get("pikpak_auth_mode") == "password":
        await api.login()
    await api.file_rename(file_id, new_name)


async def get_cloud_download_url(settings: dict[str, str], file_id: str) -> str:
    if not file_id:
        raise RuntimeError("缺少下载文件 ID")
    api = build_client(settings)
    if settings.get("pikpak_auth_mode") == "password":
        await api.login()
    data = await api.get_download_url(file_id)
    if isinstance(data, str):
        return data
    if not isinstance(data, dict):
        raise RuntimeError("无法获取云盘下载链接")
    candidates = [
        data.get("web_content_link"),
        data.get("download_url"),
        data.get("url"),
        data.get("link"),
    ]
    file_info = data.get("file") if isinstance(data.get("file"), dict) else {}
    candidates.extend(
        [
            file_info.get("web_content_link"),
            file_info.get("download_url"),
            file_info.get("url"),
            file_info.get("link"),
        ]
    )
    for media in data.get("medias", []) if isinstance(data.get("medias"), list) else []:
        if not isinstance(media, dict):
            continue
        link = media.get("link")
        if isinstance(link, dict):
            candidates.append(link.get("url"))
            candidates.append(link.get("download_url"))
        elif link:
            candidates.append(link)
    for value in candidates:
        if value:
            return str(value)
    raise RuntimeError("云盘没有返回可用下载链接")

