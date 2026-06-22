from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import APP_DIR
from .db import get_settings, init_db, log
from .media_service import reset_orphaned_download_jobs
from .processors import register_builtin_processors
from .routes import dashboard, media, resources, rss, runtime, settings, uploads
from .runtime_service import reschedule, scheduler
from .settings_service import sync_download_processor_concurrency
from .utils import int_setting


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    sync_download_processor_concurrency(int_setting(get_settings().get("download_concurrency"), 2, 1, 12))
    recovered = reset_orphaned_download_jobs()
    if recovered:
        log("warn", f"已恢复中断下载状态: {recovered} 个")
    register_builtin_processors()
    reschedule()
    scheduler.start()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="AniTrack", lifespan=lifespan)
app.include_router(dashboard.router)
app.include_router(runtime.router)
app.include_router(settings.router)
app.include_router(rss.router)
app.include_router(media.router)
app.include_router(resources.router)
app.include_router(uploads.router)

FRONTEND_DIR = APP_DIR.parent / "frontend_dist"
if FRONTEND_DIR.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIR / "assets"), name="assets")
else:
    fallback_static = APP_DIR / "static"
    fallback_static.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=fallback_static), name="static")


def frontend_asset_response(filename: str) -> FileResponse:
    path = FRONTEND_DIR / filename
    fallback = APP_DIR / "static" / filename
    if path.exists():
        return FileResponse(path)
    if fallback.exists():
        return FileResponse(fallback)
    return FileResponse(APP_DIR / "static" / "missing-frontend.html")


@app.get("/anitrack-icon.png")
async def anitrack_icon() -> FileResponse:
    return frontend_asset_response("anitrack-icon.png")


@app.get("/anitrack-logo.png")
async def anitrack_logo() -> FileResponse:
    return frontend_asset_response("anitrack-logo.png")


@app.api_route("/api/{full_path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def api_not_found(full_path: str) -> None:
    raise HTTPException(status_code=404, detail="API 不存在")


@app.get("/{full_path:path}")
async def spa(full_path: str) -> FileResponse:
    index = FRONTEND_DIR / "index.html"
    if index.exists():
        return FileResponse(index)
    return FileResponse(APP_DIR / "static" / "missing-frontend.html")
