from __future__ import annotations

import os
from pathlib import Path


APP_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("APP_DATA_DIR", "/data"))
DB_PATH = DATA_DIR / "autoanime.db"


DEFAULT_SETTINGS = {
    "rss_url": "",
    "rss_proxy": "",
    "scan_interval_minutes": "10",
    "auto_scan": "false",
    "auto_download_unique": "true",
    "auto_download_by_priority": "true",
    "default_backfill": "none",
    "subtitle_priority": "LoliHouse\n喵萌奶茶屋\n桜都字幕组",
    "resolution_priority": "1080p\n1080\n2160p\n720p",
    "language_priority": "简体\n繁体\n日语",
    "pikpak_auth_mode": "password",
    "pikpak_username": "",
    "pikpak_password": "",
    "pikpak_access_token": "",
    "pikpak_refresh_token": "",
    "pikpak_encoded_token": "",
    "pikpak_proxy": "",
    "library_root": "/Anime",
    "local_library_root": "/media/anime",
    "sync_command_template": "",
    "auto_sync_following": "false",
    "nfo_output_root": "",
    "series_dir_template": "{title_cn} ({year}) [bangumi-{bangumi_id}]",
    "season_dir_template": "Season {season:02d}",
    "episode_name_template": "{title_cn} - S{season:02d}E{episode:02d} - {episode_title}",
}
