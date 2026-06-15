from __future__ import annotations

from pathlib import PurePosixPath

from .db import connect, log, now
from .library import render_episode_name, target_dir


def cloud_asset_path(task: dict, release: dict, series: dict, settings: dict[str, str]) -> tuple[str, str]:
    name = task.get("normalized_name") or render_episode_name(series, release["episode_number"], "", settings)
    directory = task.get("target_dir") or target_dir(series, settings)
    return str(PurePosixPath(directory) / name), name


def upsert_cloud_asset(task_id: int, settings: dict[str, str]) -> int | None:
    with connect() as conn:
        task = conn.execute("SELECT * FROM download_tasks WHERE id=?", (task_id,)).fetchone()
        if not task:
            return None
        release = conn.execute("SELECT * FROM releases WHERE id=?", (task["release_id"],)).fetchone()
        series = conn.execute("SELECT * FROM series WHERE id=?", (task["series_id"],)).fetchone()
        if not release or not series:
            return None
        cloud_path, cloud_name = cloud_asset_path(dict(task), dict(release), dict(series), settings)
        ts = now()
        conn.execute(
            """
            INSERT INTO cloud_assets
              (task_id, release_id, series_id, episode_number, provider, provider_file_id,
               cloud_path, cloud_name, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'pikpak', ?, ?, ?, 'available', ?, ?)
            ON CONFLICT(task_id) DO UPDATE SET
              provider_file_id=excluded.provider_file_id,
              cloud_path=excluded.cloud_path,
              cloud_name=excluded.cloud_name,
              status='available',
              updated_at=excluded.updated_at
            """,
            (
                task["id"],
                task["release_id"],
                task["series_id"],
                release["episode_number"],
                task["pikpak_file_id"],
                cloud_path,
                cloud_name,
                ts,
                ts,
            ),
        )
        asset = conn.execute("SELECT id FROM cloud_assets WHERE task_id=?", (task["id"],)).fetchone()
    if asset:
        log("info", f"云盘资源已入库: {cloud_name}")
        return int(asset["id"])
    return None


def ensure_sync_rule(series_id: int, settings: dict[str, str], enabled: bool = False) -> None:
    ts = now()
    auto_sync = settings.get("auto_sync_following", "false").lower() == "true"
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO sync_rules
              (series_id, sync_enabled, auto_sync_following, local_root, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(series_id) DO UPDATE SET
              local_root=CASE WHEN sync_rules.local_root='' THEN excluded.local_root ELSE sync_rules.local_root END,
              updated_at=excluded.updated_at
            """,
            (
                series_id,
                1 if enabled else 0,
                1 if auto_sync else 0,
                settings.get("local_library_root") or "/media/anime",
                ts,
                ts,
            ),
        )
