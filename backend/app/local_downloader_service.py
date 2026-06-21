from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Any

from .config import DATA_DIR


def root(settings: dict[str, str]) -> Path:
    configured = settings.get("local_downloader_root") or str(DATA_DIR / "local-downloader")
    path = Path(configured).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


def logical_path(path: str) -> str:
    clean = (path or "").replace("\\", "/").strip()
    if not clean.startswith("/"):
        clean = f"/{clean}"
    return clean.replace("//", "/")


def safe_path(settings: dict[str, str], remote_path: str) -> Path:
    base = root(settings)
    target = (base / logical_path(remote_path).lstrip("/")).resolve()
    if base != target and base not in target.parents:
        raise RuntimeError(f"本地下载器路径越界: {remote_path}")
    return target


def file_id(remote_path: str) -> str:
    digest = hashlib.sha1(logical_path(remote_path).encode("utf-8", errors="ignore")).hexdigest()[:24]
    return f"local:{digest}"


async def submit(settings: dict[str, str], source: str, target_dir: str, name: str = "") -> dict[str, Any]:
    filename = name or Path(source).name or "download.bin"
    remote_path = logical_path(f"{target_dir.rstrip('/')}/{filename}")
    target = safe_path(settings, remote_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    source_path = Path(source).expanduser()
    if source_path.exists() and source_path.is_file():
        shutil.copy2(source_path, target)
    elif not target.exists():
        target.write_text(f"AutoAnime local downloader placeholder\nsource={source}\n", encoding="utf-8")
    return {
        "file": {"id": file_id(remote_path)},
        "file_id": file_id(remote_path),
        "name": filename,
        "remote_path": remote_path,
    }


async def list_files(settings: dict[str, str], path: str, recursive: bool = True) -> list[dict[str, Any]]:
    directory = safe_path(settings, path)
    if not directory.exists():
        return []
    files = directory.rglob("*") if recursive else directory.glob("*")
    result: list[dict[str, Any]] = []
    for item in sorted(files, key=lambda value: str(value).lower()):
        rel = "/" + item.resolve().relative_to(root(settings)).as_posix()
        result.append(
            {
                "id": file_id(rel),
                "file_id": file_id(rel),
                "name": item.name,
                "remote_path": rel,
                "size": item.stat().st_size if item.is_file() else 0,
                "is_dir": item.is_dir(),
            }
        )
    return result


async def copy_to_local(settings: dict[str, str], source_path: str, target_path: str, progress_cb=None) -> None:
    source = safe_path(settings, source_path)
    if not source.exists() or not source.is_file():
        raise RuntimeError(f"本地下载器源文件不存在: {source_path}")
    target = Path(target_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    if progress_cb:
        await progress_cb(100, "本地下载器复制完成")
