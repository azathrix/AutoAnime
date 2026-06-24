from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from ..runtime_service import reschedule
from ..schedule_service import SCHEDULE_ACTIONS, list_schedules, trigger_schedule, upsert_schedule
from ..schemas import SchedulePayload


router = APIRouter()


@router.get("/api/schedules")
async def api_schedules() -> dict[str, Any]:
    return {"items": list_schedules(), "actions": SCHEDULE_ACTIONS}


@router.post("/api/schedules")
async def api_create_schedule(payload: SchedulePayload) -> dict[str, Any]:
    try:
        item = upsert_schedule(payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    reschedule()
    return {"status": "saved", "item": item}


@router.put("/api/schedules/{schedule_id}")
async def api_update_schedule(schedule_id: int, payload: SchedulePayload) -> dict[str, Any]:
    try:
        item = upsert_schedule(payload.model_dump(), schedule_id=schedule_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    reschedule()
    return {"status": "saved", "item": item}


@router.post("/api/schedules/{schedule_id}/trigger")
async def api_trigger_schedule(schedule_id: int) -> dict[str, Any]:
    try:
        run_id = trigger_schedule(schedule_id, "manual")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "started", "run_id": run_id, "message": "定时器动作已触发"}

