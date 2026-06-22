from __future__ import annotations

from .backfill import process_backfill
from .download import (
    process_download,
    process_download_artifact_register,
    process_download_poll,
    process_download_presence,
    process_download_submit,
)
from .merge import process_entry_merge
from .metadata import process_metadata
from .mikan import process_mikan_match
from .rss import process_rss_candidate_persist, process_rss_fetch
from .selection import process_selection
from .sync import process_local_presence, process_local_sync
from .sync_plan import process_sync_plan
from ..processor_registry import register_processor


def register_builtin_processors() -> None:
    register_processor("rss_fetch", process_rss_fetch)
    register_processor("rss_candidate_persist", process_rss_candidate_persist)
    register_processor("mikan_match", process_mikan_match)
    register_processor("metadata", process_metadata)
    register_processor("seasonal_merge", process_entry_merge)
    register_processor("library_merge", process_entry_merge)
    register_processor("backfill", process_backfill)
    register_processor("selection", process_selection)
    register_processor("download", process_download)
    register_processor("download_presence", process_download_presence)
    register_processor("download_submit", process_download_submit)
    register_processor("download_poll", process_download_poll)
    register_processor("download_artifact_register", process_download_artifact_register)
    register_processor("local_sync", process_local_sync)
    register_processor("sync_plan", process_sync_plan)
    register_processor("local_presence", process_local_presence)


