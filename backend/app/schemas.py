from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field

class SettingsPayload(BaseModel):
    rss_url: str = ""
    rss_proxy: str = ""
    scan_interval_minutes: int = 60
    auto_scan: bool = False
    queue_dispatch_enabled: bool = True
    queue_dispatch_interval_minutes: int = 1
    auto_generate_nfo: bool = False
    backfill_current_season: bool = False
    subtitle_priority: list[str] = Field(default_factory=list)
    resolution_priority: list[str] = Field(default_factory=list)
    language_priority: list[str] = Field(default_factory=list)
    secondary_language_priority: list[str] = Field(default_factory=list)
    download_concurrency: int = 2
    downloaders: list[dict[str, Any]] = Field(default_factory=list)
    episode_name_template: str = ""
    movie_name_template: str = ""
    tv_name_template: str = ""
    movie_quality_priority: list[str] = Field(default_factory=list)
    movie_source_priority: list[str] = Field(default_factory=list)
    movie_subtitle_priority: list[str] = Field(default_factory=list)
    tv_quality_priority: list[str] = Field(default_factory=list)
    tv_source_priority: list[str] = Field(default_factory=list)
    tv_subtitle_priority: list[str] = Field(default_factory=list)
    tmdb_token: str = ""

class EntryPayload(BaseModel):
    title_cn: str = ""
    bangumi_id: str = ""
    tmdb_id: str = ""
    year: int = 0
    month: int = 0
    season_number: int = 1
    media_type: str = "anime"
    region: str = "jp"
    title_romaji: str = ""
    title_raw: str = ""
    poster_url: str = ""
    summary: str = ""
    genres_json: str = "[]"
    tags_json: str = "[]"

class MetadataFetchPayload(BaseModel):
    bangumi_id: str = ""
    tmdb_id: str = ""
    provider: str = "bangumi"

class MediaCreatePayload(BaseModel):
    mode: str = "add"
    title: str = ""
    bangumi_id: str = ""
    tmdb_id: str = ""
    year: int = 0
    month: int = 0
    season_number: int = 1
    region: str = "jp"
    episode_number: int = 0
    resource_title: str = ""
    source_ref: str = ""
    subtitle_group: str = ""
    resolution: str = ""
    language: str = ""
    subtitle_format: str = ""
    subtitle_path: str = ""
    subtitle_url: str = ""
    subtitle_file_name: str = ""

class RssSubscriptionPayload(BaseModel):
    name: str = ""
    url: str = ""
    kind: str = "mikan"
    enabled: bool = True

class EpisodeResourcePayload(BaseModel):
    resource_id: int = 0
    title: str = ""
    subtitle_group: str = ""
    resolution: str = ""
    language: str = ""
    subtitle_format: str = ""
    selected: bool = True

class EpisodeSubtitlePayload(BaseModel):
    subtitle_id: int = 0
    language: str = ""
    subtitle_format: str = ""
    subtitle_path: str = ""
    subtitle_url: str = ""
    file_name: str = ""
    selected: bool = True

class EpisodeImportPayload(BaseModel):
    resources_text: str = ""
    subtitles_text: str = ""
    subtitle_format: str = "external"
    language: str = ""

class LocalUploadItemPayload(BaseModel):
    temp_path: str = ""
    file_name: str = ""
    size: int = 0

class LocalUploadImportPayload(BaseModel):
    uploads: list[LocalUploadItemPayload] = Field(default_factory=list)
    subtitle_format: str = ""
    language: str = ""

class BatchSubtitlePayload(BaseModel):
    subtitles_text: str = ""
    file_names: list[str] = Field(default_factory=list)
    subtitle_format: str = "external"
    language: str = ""

class ScheduledJobPayload(BaseModel):
    enabled: bool = True
    interval_minutes: int = 1

class ProcessorSettingsPayload(BaseModel):
    download_concurrency: int = 2

class PipelineStartPayload(BaseModel):
    trigger_source: str = "manual"
    first_step_key: str = ""
    subject_type: str = ""
    subject_id: int = 0
    payload: dict[str, Any] = Field(default_factory=dict)
    message: str = ""
