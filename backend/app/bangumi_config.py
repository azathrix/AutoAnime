from __future__ import annotations

from pathlib import Path
from typing import Any

from .database import connect
from .db import get_settings, log
from .library import local_library_root, render_season_dir, render_series_dir


def setting_enabled(value: str) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def bangumi_plugin_offset(entry: dict[str, Any]) -> int:
    # Jellyfin 第 1 集映射到 Bangumi 第 67 集时，插件 offset 需要写 -66。
    return -max(0, int(entry.get("episode_offset") or 0))


def bangumi_config_dir(entry: dict[str, Any], settings: dict[str, str]) -> Path:
    root = Path(local_library_root(entry, settings))
    series_dir = render_series_dir(entry, settings)
    media_type = str(entry.get("media_type") or "anime").strip().lower()
    if media_type == "movie":
        return root / series_dir
    return root / series_dir / render_season_dir(int(entry.get("season_number") or 1), settings)


def generate_bangumi_ini(entry_id: int, settings: dict[str, str] | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    if not setting_enabled(settings.get("generate_bangumi_ini", "false")):
        return {"generated": False, "reason": "设置未开启"}
    with connect() as conn:
        row = conn.execute("SELECT * FROM entries WHERE id=?", (entry_id,)).fetchone()
    if not row:
        return {"generated": False, "reason": "条目不存在"}
    entry = dict(row)
    bangumi_id = str(entry.get("bangumi_id") or "").strip()
    if not bangumi_id:
        return {"generated": False, "reason": "缺少 Bangumi ID"}

    target_dir = bangumi_config_dir(entry, settings)
    target_dir.mkdir(parents=True, exist_ok=True)
    config_path = target_dir / "bangumi.ini"
    offset = bangumi_plugin_offset(entry)
    content = f"[Bangumi]\nid={bangumi_id}\noffset={offset}\n"
    config_path.write_text(content, encoding="utf-8")
    log(
        "info",
        f"已生成 Bangumi 配置: entry_id={entry_id} bangumi_id={bangumi_id} "
        f"offset={offset} path={config_path}",
    )
    return {"generated": True, "path": str(config_path), "offset": offset, "bangumi_id": bangumi_id}

