from __future__ import annotations

import base64
import json
from typing import Any

from .db import save_settings

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
            username=settings.get("pikpak_username") or None,
            password=settings.get("pikpak_password") or None,
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


async def submit_offline_download(settings: dict[str, str], source: str, target_dir: str) -> dict[str, Any]:
    api = build_client(settings)
    if settings.get("pikpak_auth_mode") == "password":
        await api.login()
    folders = await api.path_to_id(target_dir, create=True)
    parent_id = folders[-1]["id"] if folders else None
    await prepare_offline_captcha(api)
    try:
        return await api.offline_download(source, parent_id=parent_id)
    except Exception as exc:
        if "Verification code is invalid" not in str(exc):
            raise
        await prepare_offline_captcha(api)
        return await api.offline_download(source, parent_id=parent_id)


async def list_offline_tasks(settings: dict[str, str]) -> list[dict[str, Any]]:
    api = build_client(settings)
    phases = [
        "PHASE_TYPE_PENDING",
        "PHASE_TYPE_RUNNING",
        "PHASE_TYPE_ERROR",
        "PHASE_TYPE_COMPLETE",
    ]
    data = await api.offline_list(size=1000, phase=phases)
    return data.get("tasks", []) if isinstance(data, dict) else []


async def rename_cloud_file(settings: dict[str, str], file_id: str, new_name: str) -> None:
    if not file_id:
        return
    api = build_client(settings)
    await api.file_rename(file_id, new_name)


async def get_cloud_download_url(settings: dict[str, str], file_id: str) -> str:
    if not file_id:
        raise RuntimeError("缺少云盘文件 ID")
    api = build_client(settings)
    data = await api.get_download_url(file_id)
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
    for value in candidates:
        if value:
            return str(value)
    raise RuntimeError("云盘没有返回可用下载链接")
