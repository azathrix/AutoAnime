from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import httpx


def _active(settings: dict[str, str]) -> dict[str, Any]:
    try:
        value = json.loads(settings.get("_active_downloader_json") or "{}")
    except json.JSONDecodeError:
        value = {}
    return value if isinstance(value, dict) else {}


def base_url(settings: dict[str, str]) -> str:
    active = _active(settings)
    return str(active.get("rpc_url") or active.get("url") or "http://127.0.0.1:8080").strip().rstrip("/")


def username(settings: dict[str, str]) -> str:
    active = _active(settings)
    return str(active.get("username") or active.get("user") or "").strip()


def password(settings: dict[str, str]) -> str:
    active = _active(settings)
    return str(active.get("password") or "").strip()


def task_tag(source: str) -> str:
    digest = hashlib.sha1(source.encode("utf-8", errors="ignore")).hexdigest()[:16]
    return f"autoanime-{digest}"


def magnet_hash(source: str) -> str:
    match = re.search(r"btih:([a-fA-F0-9]{40}|[a-zA-Z2-7]{32})", source or "")
    return match.group(1).lower() if match else ""


async def _client(settings: dict[str, str]) -> httpx.AsyncClient:
    client = httpx.AsyncClient(base_url=base_url(settings), timeout=30)
    user = username(settings)
    pwd = password(settings)
    if user or pwd:
        response = await client.post("/api/v2/auth/login", data={"username": user, "password": pwd})
        response.raise_for_status()
        if response.text.strip().lower() != "ok.":
            await client.aclose()
            raise RuntimeError("qBittorrent 登录失败")
    return client


async def submit(settings: dict[str, str], source: str, target_dir: str, name: str = "") -> dict[str, Any]:
    Path(target_dir).mkdir(parents=True, exist_ok=True)
    tag = task_tag(source)
    data = {
        "urls": source,
        "savepath": target_dir,
        "tags": tag,
    }
    if name:
        data["rename"] = name
    client = await _client(settings)
    try:
        response = await client.post("/api/v2/torrents/add", data=data)
        response.raise_for_status()
        text = response.text.strip().lower()
        if text and text not in {"ok.", "ok"}:
            raise RuntimeError(response.text[:1000])
    finally:
        await client.aclose()
    return {"task_id": magnet_hash(source) or tag, "id": magnet_hash(source) or tag}


async def _files_for_hash(client: httpx.AsyncClient, torrent_hash: str, save_path: str) -> tuple[str, str]:
    if not torrent_hash:
        return "", ""
    try:
        response = await client.get("/api/v2/torrents/files", params={"hash": torrent_hash})
        response.raise_for_status()
        files = response.json()
    except Exception:
        return "", ""
    if not isinstance(files, list) or not files:
        return "", ""
    selected = next((item for item in files if float(item.get("progress") or 0) >= 1), files[0])
    name = str(selected.get("name") or "").strip()
    path = str(Path(save_path) / name) if name else ""
    return path, name


async def list_tasks(settings: dict[str, str]) -> list[dict[str, Any]]:
    client = await _client(settings)
    try:
        response = await client.get("/api/v2/torrents/info")
        response.raise_for_status()
        torrents = response.json()
        if not isinstance(torrents, list):
            return []
        result: list[dict[str, Any]] = []
        for torrent in torrents:
            if not isinstance(torrent, dict):
                continue
            tags = [item.strip() for item in str(torrent.get("tags") or "").split(",") if item.strip()]
            task_id = next((tag for tag in tags if tag.startswith("autoanime-")), str(torrent.get("hash") or ""))
            save_path = str(torrent.get("save_path") or torrent.get("savePath") or "")
            file_path, file_name = await _files_for_hash(client, str(torrent.get("hash") or ""), save_path)
            progress = float(torrent.get("progress") or 0)
            state = str(torrent.get("state") or "")
            error_state = state.lower().startswith("error") or state in {"missingFiles", "unknown"}
            phase = "PHASE_TYPE_COMPLETE" if progress >= 1 else "PHASE_TYPE_ERROR" if error_state else "PHASE_TYPE_RUNNING"
            result.append(
                {
                    "id": task_id,
                    "phase": phase,
                    "file_id": file_path,
                    "reference_resource": {"id": file_path},
                    "name": file_name or str(torrent.get("name") or task_id),
                    "remote_path": file_path,
                    "message": state,
                    "raw": torrent,
                }
            )
        return result
    finally:
        await client.aclose()


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
