from __future__ import annotations

import os
from pathlib import Path


APP_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("APP_DATA_DIR", "/data"))
DB_PATH = DATA_DIR / "autoanime.db"
MEDIA_ROOT = Path(os.environ.get("ANITRACK_MEDIA_ROOT", "/media"))


DEFAULT_SETTINGS = {
    "rss_url": "",
    "rss_proxy": "",
    "scan_interval_minutes": "60",
    "auto_scan": "false",
    "queue_dispatch_enabled": "true",
    "queue_dispatch_interval_minutes": "1",
    "auto_download_unique": "true",
    "auto_download_by_priority": "true",
    "auto_generate_nfo": "false",
    "nfo_write_mode": "fill_missing",
    "backfill_current_season": "false",
    "default_backfill": "none",
    "subtitle_priority": "LoliHouse\n喵萌奶茶屋\nNekomoe kissaten\nANi\n猎户压制部\n百冬练习组\n桜都字幕组\n动漫国字幕组\n悠哈璃羽字幕社\n北宇治字幕组\n豌豆字幕组\n幻樱字幕组\n千夏字幕组\n雪飘工作室\n织梦字幕组\nSweetSub\nVCB-Studio",
    "resolution_priority": "1080p\n1080\n2160p\n720p",
    "language_priority": "简体\n繁体\n日语",
    "secondary_language_priority": "繁体\n日语\n英语",
    "pikpak_rate_limit_cooldown_minutes": "60",
    "download_concurrency": "2",
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
    "tmdb_token": "",
    "library_root": "/Anime",
    "local_library_root": str(MEDIA_ROOT),
    "auto_sync_following": "true",
    "migration_auto_sync_default_v2": "",
    "nfo_output_root": "",
    "series_dir_template": "{title_base}",
    "season_dir_template": "Season {season:02d}",
    "episode_name_template": "{title_base} - S{season:02d}E{episode:02d} - 第 {episode:02d} 话",
    "movie_name_template": "{title_base}/{title_base}",
    "tv_name_template": "{title_base}/Season {season:02d}/{title_base} - S{season:02d}E{episode:02d} - 第 {episode:02d} 话",
    "anime_selection_rules_json": "{}",
    "movie_selection_rules_json": "{}",
    "tv_selection_rules_json": "{}",
    "movie_quality_priority": "2160p,1080p,720p",
    "movie_source_priority": "BluRay,WEB-DL,WebRip",
    "movie_subtitle_priority": "简体,繁体,双语",
    "tv_quality_priority": "2160p,1080p,720p",
    "tv_source_priority": "WEB-DL,WebRip,HDTV",
    "tv_subtitle_priority": "简体,繁体,双语",
    "downloaders_json": "[]",
}

