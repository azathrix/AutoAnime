from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..discovery_service import (
    apply_backfill,
    collect_draft,
    delete_search_source,
    discovery_results,
    list_search_sources,
    run_discovery_search,
    save_search_source,
    search_backfill,
    test_search_source,
)
from ..schemas import BackfillApplyPayload, DiscoverySearchPayload, SearchSourcePayload


router = APIRouter()


def _bad_request(exc: ValueError) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


@router.get("/api/search-sources")
def api_search_sources() -> dict:
    return list_search_sources()


@router.post("/api/search-sources")
def api_create_search_source(payload: SearchSourcePayload) -> dict:
    try:
        return save_search_source(payload)
    except ValueError as exc:
        raise _bad_request(exc) from exc


@router.put("/api/search-sources/{source_id}")
def api_update_search_source(source_id: int, payload: SearchSourcePayload) -> dict:
    try:
        return save_search_source(payload, source_id)
    except ValueError as exc:
        raise _bad_request(exc) from exc


@router.delete("/api/search-sources/{source_id}")
def api_delete_search_source(source_id: int) -> dict:
    return delete_search_source(source_id)


@router.post("/api/search-sources/{source_id}/test")
async def api_test_search_source(source_id: int) -> dict:
    try:
        return await test_search_source(source_id)
    except ValueError as exc:
        raise _bad_request(exc) from exc


@router.post("/api/discovery/search")
async def api_discovery_search(payload: DiscoverySearchPayload) -> dict:
    return await run_discovery_search(payload)


@router.get("/api/discovery/searches/{search_id}")
def api_discovery_search_detail(search_id: int) -> dict:
    try:
        return discovery_results(search_id)
    except ValueError as exc:
        raise _bad_request(exc) from exc


@router.get("/api/discovery/results")
def api_discovery_results(search_id: int = Query(..., ge=1)) -> dict:
    try:
        return discovery_results(search_id)
    except ValueError as exc:
        raise _bad_request(exc) from exc


@router.post("/api/discovery/results/{result_id}/collect-draft")
def api_discovery_collect_draft(result_id: int) -> dict:
    try:
        return collect_draft(result_id)
    except ValueError as exc:
        raise _bad_request(exc) from exc


@router.post("/api/entries/{entry_id}/backfill/search")
async def api_entry_backfill_search(entry_id: int) -> dict:
    try:
        return await search_backfill(entry_id)
    except ValueError as exc:
        raise _bad_request(exc) from exc


@router.post("/api/entries/{entry_id}/backfill/apply")
def api_entry_backfill_apply(entry_id: int, payload: BackfillApplyPayload) -> dict:
    try:
        return apply_backfill(entry_id, payload)
    except ValueError as exc:
        raise _bad_request(exc) from exc
