from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..discovery_service import (
    apply_backfill,
    collect_draft,
    delete_search_source,
    discovery_results,
    list_search_sources,
    reorder_search_sources,
    run_discovery_search,
    save_search_source,
    search_backfill,
    test_search_source,
)
from ..resource_package_service import (
    apply_package_match,
    cleanup_package_async,
    create_package_from_discovery,
    create_package_target_entry,
    list_entry_packages,
    package_detail,
    scan_package_async,
)
from ..schemas import (
    BackfillApplyPayload,
    DiscoveryPackageDownloadPayload,
    DiscoverySearchPayload,
    ReorderPayload,
    ResourcePackageApplyPayload,
    ResourcePackageTargetEntryPayload,
    SearchSourcePayload,
)


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


@router.post("/api/search-sources/reorder")
def api_reorder_search_sources(payload: ReorderPayload) -> dict:
    return reorder_search_sources(payload.ids)


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


@router.post("/api/discovery/results/{result_id}/package-download")
async def api_discovery_package_download(result_id: int, payload: DiscoveryPackageDownloadPayload) -> dict:
    try:
        return create_package_from_discovery(result_id, payload)
    except ValueError as exc:
        raise _bad_request(exc) from exc


@router.get("/api/entries/{entry_id}/resource-packages")
def api_entry_resource_packages(entry_id: int) -> dict:
    return list_entry_packages(entry_id)


@router.get("/api/resource-packages/{package_id}")
def api_resource_package_detail(package_id: int) -> dict:
    try:
        return package_detail(package_id)
    except ValueError as exc:
        raise _bad_request(exc) from exc


@router.post("/api/resource-packages/{package_id}/scan")
async def api_resource_package_scan(package_id: int) -> dict:
    try:
        return await scan_package_async(package_id)
    except ValueError as exc:
        raise _bad_request(exc) from exc


@router.post("/api/resource-packages/{package_id}/target-entries")
def api_resource_package_target_entry(package_id: int, payload: ResourcePackageTargetEntryPayload) -> dict:
    try:
        return create_package_target_entry(package_id, payload)
    except ValueError as exc:
        raise _bad_request(exc) from exc


@router.post("/api/resource-packages/{package_id}/apply-match")
async def api_resource_package_apply_match(package_id: int, payload: ResourcePackageApplyPayload) -> dict:
    try:
        return await apply_package_match(package_id, payload)
    except ValueError as exc:
        raise _bad_request(exc) from exc


@router.post("/api/resource-packages/{package_id}/cleanup")
async def api_resource_package_cleanup(package_id: int) -> dict:
    try:
        return await cleanup_package_async(package_id)
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
