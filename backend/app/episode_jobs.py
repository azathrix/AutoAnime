from __future__ import annotations

from typing import Any

from .database import connect
from .runtime_store import due


STAGE_LABELS = {
    "metadata": "元数据",
    "select_release": "自动选集",
    "download": "下载到本地",
    "localize": "本地整理",
    "nfo": "NFO",
    "done": "可观看",
    "failed": "失败",
}

PROCESSOR_STAGE = {
    "metadata": "metadata",
    "mikan_match": "metadata",
    "seasonal_merge": "metadata",
    "library_merge": "metadata",
    "backfill": "metadata",
    "selection": "select_release",
    "download": "download",
    "local_sync": "localize",
    "nfo": "nfo",
    "local_presence": "done",
}

STAGE_ORDER = {
    "metadata": 10,
    "select_release": 20,
    "download": 30,
    "localize": 40,
    "nfo": 50,
    "done": 60,
    "failed": 90,
}


def build_episode_jobs(
    runtime_snapshot: dict[str, Any] | None = None,
    *,
    domain_kind: str = "",
    limit: int = 400,
) -> list[dict[str, Any]]:
    """Return the canonical per-episode runtime view.

    The rows are derived from durable facts first, then overlaid with volatile
    Runtime task state. This gives the UI a single source for "where is this
    episode now?" without coupling it to low-level processor queues.
    """

    jobs = _jobs_from_domain(domain_kind=domain_kind, limit=limit)
    _overlay_runtime_state(jobs, runtime_snapshot or {})
    jobs.sort(key=lambda item: (item.get("sort_time") or "", int(item.get("episode_number") or 0)), reverse=True)
    return jobs[:limit]


def _jobs_from_domain(*, domain_kind: str, limit: int) -> list[dict[str, Any]]:
    query = """
        SELECT
          e.id AS entry_id,
          e.domain_kind,
          e.media_type,
          e.display_title,
          e.title_root,
          e.poster_url,
          e.year,
          e.season_number,
          r.id AS release_id,
          r.episode_number,
          r.title AS release_title,
          r.selected,
          r.subtitle_group,
          r.resolution,
          r.language,
          r.subtitle_format,
          r.updated_at AS release_updated_at,
          dj.id AS download_job_id,
          dj.status AS download_status,
          dj.retry_after AS download_retry_after,
          dj.last_error AS download_error,
          da.id AS download_artifact_id,
          da.status AS artifact_status,
          da.updated_at AS artifact_updated_at,
          la.id AS local_asset_id,
          la.status AS local_status,
          la.nfo_status,
          la.local_path,
          la.updated_at AS local_updated_at
        FROM releases r
        JOIN entries e ON e.id=r.entry_id
        LEFT JOIN download_jobs dj ON dj.release_id=r.id
        LEFT JOIN download_artifacts da ON da.release_id=r.id
        LEFT JOIN local_assets la ON la.release_id=r.id
        WHERE COALESCE(e.hidden, 0)=0
    """
    params: list[Any] = []
    if domain_kind:
        query += " AND e.domain_kind=?"
        params.append(domain_kind)
    query += """
        ORDER BY e.updated_at DESC, r.entry_id ASC, r.episode_number ASC,
          r.selected DESC, r.published_at DESC, r.id DESC
        LIMIT ?
    """
    params.append(max(limit * 4, limit))

    chosen: dict[tuple[int, int], dict[str, Any]] = {}
    with connect() as conn:
        rows = conn.execute(query, tuple(params)).fetchall()
    for row in rows:
        item = dict(row)
        entry_id = int(item.get("entry_id") or 0)
        episode_number = int(item.get("episode_number") or 0)
        key = (entry_id, episode_number)
        if key in chosen:
            continue
        chosen[key] = _domain_job(item)
    return list(chosen.values())


def _domain_job(row: dict[str, Any]) -> dict[str, Any]:
    local_asset_id = int(row.get("local_asset_id") or 0)
    download_artifact_id = int(row.get("download_artifact_id") or 0)
    download_job_id = int(row.get("download_job_id") or 0)
    selected = int(row.get("selected") or 0) == 1

    if local_asset_id and str(row.get("local_status") or "") == "synced":
        stage = "done" if str(row.get("nfo_status") or "") == "generated" else "nfo"
        status = "completed" if stage == "done" else "pending"
        reason = "本地文件已就绪" if stage == "done" else "等待生成 NFO"
    elif download_artifact_id:
        stage = "localize"
        status = "pending"
        reason = "下载完成，等待本地整理"
    elif download_job_id:
        stage = "download"
        status = str(row.get("download_status") or "pending")
        reason = row.get("download_error") or "下载任务处理中"
    elif selected:
        stage = "download"
        status = "pending"
        reason = "已选中发布，等待下载"
    else:
        stage = "select_release"
        status = "pending"
        reason = "等待自动选集"

    return {
        "key": f"{row.get('entry_id')}:{row.get('episode_number')}",
        "entry_id": int(row.get("entry_id") or 0),
        "domain_kind": row.get("domain_kind") or "",
        "media_type": row.get("media_type") or "anime",
        "display_title": row.get("display_title") or row.get("title_root") or "",
        "poster_url": row.get("poster_url") or "",
        "year": int(row.get("year") or 0),
        "season_number": int(row.get("season_number") or 1),
        "episode_number": int(row.get("episode_number") or 0),
        "release_id": int(row.get("release_id") or 0),
        "release_title": row.get("release_title") or "",
        "subtitle_group": row.get("subtitle_group") or "",
        "resolution": row.get("resolution") or "",
        "language": row.get("language") or "",
        "subtitle_format": row.get("subtitle_format") or "",
        "download_job_id": download_job_id,
        "download_artifact_id": download_artifact_id,
        "local_asset_id": local_asset_id,
        "local_path": row.get("local_path") or "",
        "stage": stage,
        "stage_label": STAGE_LABELS.get(stage, stage),
        "status": status,
        "reason": reason,
        "retry_after": row.get("download_retry_after") or "",
        "sort_time": row.get("local_updated_at")
        or row.get("artifact_updated_at")
        or row.get("release_updated_at")
        or "",
    }


def _overlay_runtime_state(jobs: list[dict[str, Any]], runtime_snapshot: dict[str, Any]) -> None:
    if not jobs:
        return
    by_entry_episode = {
        (int(job.get("entry_id") or 0), int(job.get("episode_number") or 0)): job
        for job in jobs
    }
    by_release = {int(job.get("release_id") or 0): job for job in jobs if int(job.get("release_id") or 0) > 0}
    tasks: list[dict[str, Any]] = []
    for detail in (runtime_snapshot.get("queue_details") or {}).values():
        for item in detail.get("items", []):
            if isinstance(item, dict):
                tasks.append(item)
    priority = {"failed": 4, "running": 3, "waiting": 2, "pending": 1}
    tasks.sort(key=lambda item: priority.get(str(item.get("status") or ""), 0), reverse=True)
    for task in tasks:
        release_id = int(task.get("release_id") or 0)
        entry_id = int(task.get("entry_id") or 0)
        episode_number = int(task.get("episode_number") or 0)
        job = by_release.get(release_id)
        if not job and entry_id and episode_number:
            job = by_entry_episode.get((entry_id, episode_number))
        if not job and entry_id:
            matches = [item for item in jobs if int(item.get("entry_id") or 0) == entry_id]
            if len(matches) == 1:
                job = matches[0]
        if not job:
            continue
        status = str(task.get("status") or "")
        processor_key = str(task.get("processor_key") or task.get("queue_key") or "")
        stage = PROCESSOR_STAGE.get(processor_key, job.get("stage") or "")
        if _runtime_task_is_stale(job, stage, status):
            continue
        if status == "waiting" and due(str(task.get("retry_after") or "")):
            status = "pending"
        if status in {"failed", "running", "waiting", "pending"}:
            job["stage"] = stage
            job["stage_label"] = STAGE_LABELS.get(stage, stage)
            job["status"] = status
            job["reason"] = task.get("last_error") or task.get("progress_text") or task.get("message") or job.get("reason") or ""
            job["runtime_task_id"] = int(task.get("id") or 0)
            job["retry_after"] = task.get("retry_after") or job.get("retry_after") or ""


def _runtime_task_is_stale(job: dict[str, Any], runtime_stage: str, runtime_status: str) -> bool:
    """Ignore lower-stage Runtime tasks when durable facts already moved on."""

    current_stage = str(job.get("stage") or "")
    if current_stage == "done":
        return True
    if runtime_status == "failed":
        return False
    current_order = STAGE_ORDER.get(current_stage, 0)
    runtime_order = STAGE_ORDER.get(runtime_stage, current_order)
    return runtime_order < current_order
