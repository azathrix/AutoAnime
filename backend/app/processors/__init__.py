from __future__ import annotations

from .backfill import process_backfill
from .cloud import (
    process_cloud_asset_register,
    process_cloud_poll,
    process_cloud_presence,
    process_cloud_submit,
)
from .merge import process_entry_merge
from .metadata import process_metadata
from .mikan import process_mikan_match
from .nfo import process_nfo
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
    register_processor("cloud_presence", process_cloud_presence)
    register_processor("cloud_submit", process_cloud_submit)
    register_processor("cloud_poll", process_cloud_poll)
    register_processor("cloud_asset_register", process_cloud_asset_register)
    register_processor("local_sync", process_local_sync)
    register_processor("nfo", process_nfo)
    register_processor("sync_plan", process_sync_plan)
    register_processor("local_presence", process_local_presence)
