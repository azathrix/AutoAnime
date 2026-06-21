from __future__ import annotations

import os
from pathlib import Path


APP_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("APP_DATA_DIR", "/data"))
DB_PATH = DATA_DIR / "autoanime.db"


DEFAULT_SETTINGS = {
    "rss_url": "",
    "rss_proxy": "",
    "scan_interval_minutes": "60",
    "auto_scan": "false",
    "auto_download_unique": "true",
    "auto_download_by_priority": "true",
    "default_backfill": "none",
    "subtitle_priority": "LoliHouse\n喵萌奶茶屋\nNekomoe kissaten\nANi\n猎户压制部\n百冬练习组\n桜都字幕组\n动漫国字幕组\n悠哈璃羽字幕社\n北宇治字幕组\n豌豆字幕组\n幻樱字幕组\n千夏字幕组\n雪飘工作室\n织梦字幕组\nSweetSub\nVCB-Studio",
    "resolution_priority": "1080p\n1080\n2160p\n720p",
    "language_priority": "简体\n繁体\n日语",
    "secondary_language_priority": "繁体\n日语\n英语",
    "pikpak_rate_limit_cooldown_minutes": "60",
    "download_backend": "rclone",
    "local_downloader_root": str(DATA_DIR / "local-downloader"),
    "rclone_command": "rclone",
    "rclone_config_path": "/data/rclone/rclone.conf",
    "rclone_remote": "pikpak",
    "pikpak_auth_mode": "password",
    "pikpak_username": "",
    "pikpak_password": "",
    "pikpak_access_token": "",
    "pikpak_refresh_token": "",
    "pikpak_encoded_token": "",
    "pikpak_proxy": "",
    "library_root": "/Anime",
    "local_library_root": "/media/autoanime",
    "auto_sync_following": "false",
    "migration_auto_sync_default_v2": "",
    "nfo_output_root": "",
    "series_dir_template": "{title_base} ({year}) [bangumi-{bangumi_id}]",
    "season_dir_template": "Season {season:02d}",
    "episode_name_template": "{title_cn} - S{season:02d}E{episode:02d} - {episode_title}",
}

