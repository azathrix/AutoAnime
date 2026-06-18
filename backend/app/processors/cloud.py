from __future__ import annotations

from ..database import connect
from ..db import get_settings, now
from ..pipeline_models import ProcessorContext, ProcessorResult
from ..pikpak_service import submit_offline_download
from .. import rclone_service
from ..scanner import (
    enqueue_cloud_poll_task,
    enqueue_download_enqueue_task,
    ensure_download_task_for_release,
    extract_file_id,
    extract_task_id,
    is_rate_limited_error,
    retry_after_time,
    sync_cloud_submission,
    task_retry_after,
)
from ..scanner import poll_submitted_tasks
from ..sync_service import (
    cloud_file_id,
    enqueue_cloud_asset_task,
    enqueue_sync_plan_task,
    find_existing_remote_episode,
    process_cloud_asset_tasks,
    reconcile_rclone_submitted_tasks,
    upsert_cloud_asset,
    upsert_cloud_asset_from_download_task,
)


async def process_cloud_presence(context: ProcessorContext, payload: dict) -> ProcessorResult:
    release_id = context.subject_id if context.subject_type == "release" else int(payload.get("release_id") or 0)
    if release_id <= 0:
        return ProcessorResult.terminal("云盘存在性检查缺少 release_id")
    with connect() as conn:
        task = conn.execute(
            """
            SELECT cpt.*, r.entry_id AS release_entry_id, r.episode_number AS release_episode
            FROM cloud_presence_tasks cpt
            JOIN releases r ON r.id=cpt.release_id
            WHERE cpt.release_id=?
            """,
            (release_id,),
        ).fetchone()
        if not task:
            release = conn.execute("SELECT entry_id, episode_number FROM releases WHERE id=?", (release_id,)).fetchone()
            if not release:
                return ProcessorResult.terminal(f"发布不存在: {release_id}")
            entry_id = int(release["entry_id"] or 0)
            episode_number = int(release["episode_number"] or 0)
        else:
            entry_id = int(task["entry_id"] or task["release_entry_id"] or 0)
            episode_number = int(task["episode_number"] or task["release_episode"] or 0)

        existing_cloud = conn.execute(
            """
            SELECT id
            FROM cloud_assets
            WHERE release_id=? OR (entry_id=? AND episode_number=?)
            LIMIT 1
            """,
            (release_id, entry_id, episode_number),
        ).fetchone()
        ts = now()
        if existing_cloud:
            conn.execute(
                """
                UPDATE cloud_presence_tasks
                SET status='completed', cloud_asset_id=?, retry_after='', last_error='云盘资源已存在，跳过提交', updated_at=?
                WHERE release_id=?
                """,
                (int(existing_cloud["id"]), ts, release_id),
            )
            return ProcessorResult.skipped(
                "云盘资源已存在",
                data={"release_id": release_id, "entry_id": entry_id, "cloud_asset_id": int(existing_cloud["id"])},
                next_payload={
                    "_subject_type": "entry",
                    "_subject_id": entry_id,
                    "entry_id": entry_id,
                    "cloud_asset_id": int(existing_cloud["id"]),
                },
            )

        enqueue_download_enqueue_task(conn, release_id, 0, entry_id, episode_number, ts)
        conn.execute(
            """
            UPDATE cloud_presence_tasks
            SET status='completed', cloud_asset_id=0, retry_after='', last_error='', updated_at=?
            WHERE release_id=?
            """,
            (ts, release_id),
        )

    return ProcessorResult.success(
        "云盘资源不存在，进入下载准备",
        data={"release_id": release_id, "entry_id": entry_id, "episode_number": episode_number},
        next_payload={
            "_subject_type": "release",
            "_subject_id": release_id,
            "release_id": release_id,
            "entry_id": entry_id,
            "episode_number": episode_number,
        },
    )


async def process_download_enqueue(context: ProcessorContext, payload: dict) -> ProcessorResult:
    release_id = context.subject_id if context.subject_type == "release" else int(payload.get("release_id") or 0)
    if release_id <= 0:
        return ProcessorResult.terminal("下载准备缺少 release_id")
    settings = get_settings()
    try:
        with connect() as conn:
            task = conn.execute(
                """
                SELECT det.*, r.entry_id AS release_entry_id, r.episode_number AS release_episode
                FROM download_enqueue_tasks det
                JOIN releases r ON r.id=det.release_id
                WHERE det.release_id=?
                """,
                (release_id,),
            ).fetchone()
            if not task:
                release = conn.execute("SELECT entry_id, episode_number FROM releases WHERE id=?", (release_id,)).fetchone()
                if not release:
                    return ProcessorResult.terminal(f"发布不存在: {release_id}")
                entry_id = int(release["entry_id"] or 0)
                episode_number = int(release["episode_number"] or 0)
            else:
                entry_id = int(task["entry_id"] or task["release_entry_id"] or 0)
                episode_number = int(task["episode_number"] or task["release_episode"] or 0)

            existing_submission = conn.execute(
                """
                SELECT id, status, download_task_id
                FROM cloud_submissions
                WHERE entry_id=? AND episode_number=? AND provider='pikpak'
                LIMIT 1
                """,
                (entry_id, episode_number),
            ).fetchone()
            ts = now()
            if existing_submission and existing_submission["status"] in {"pending", "submitted", "running", "completed"}:
                download_task_id = int(existing_submission["download_task_id"] or 0)
                conn.execute(
                    """
                    UPDATE download_enqueue_tasks
                    SET status='completed', retry_after='', last_error='已存在云盘提交记录，跳过重复准备', updated_at=?
                    WHERE release_id=?
                    """,
                    (ts, release_id),
                )
            else:
                download_task_id = ensure_download_task_for_release(conn, release_id, settings) or 0
                conn.execute(
                    """
                    UPDATE download_enqueue_tasks
                    SET status='completed', retry_after='', last_error='', updated_at=?
                    WHERE release_id=?
                    """,
                    (ts, release_id),
                )
    except Exception as exc:
        return ProcessorResult.retryable(str(exc)[:2000], task_retry_after(settings, context.attempts + 1))

    if download_task_id <= 0:
        return ProcessorResult.terminal("下载准备未生成 download_task")
    return ProcessorResult.success(
        "下载准备完成",
        data={"release_id": release_id, "entry_id": entry_id, "download_task_id": download_task_id},
        next_payload={
            "_subject_type": "download_task",
            "_subject_id": download_task_id,
            "download_task_id": download_task_id,
            "release_id": release_id,
            "entry_id": entry_id,
        },
    )


async def process_download_submit(context: ProcessorContext, payload: dict) -> ProcessorResult:
    download_task_id = context.subject_id if context.subject_type == "download_task" else int(payload.get("download_task_id") or 0)
    if download_task_id <= 0:
        return ProcessorResult.terminal("云盘提交缺少 download_task_id")
    settings = get_settings()
    with connect() as conn:
        task = conn.execute(
            """
            SELECT dt.*, r.magnet, r.torrent_url, r.title, r.episode_number
            FROM download_tasks dt
            JOIN releases r ON r.id=dt.release_id
            JOIN entries e ON e.id=dt.entry_id
            WHERE dt.id=?
              AND e.bangumi_id != ''
            """,
            (download_task_id,),
        ).fetchone()
    if not task:
        return ProcessorResult.terminal(f"下载任务不存在或条目缺少 Bangumi ID: {download_task_id}")
    source = str(task["magnet"] or task["torrent_url"] or "")
    if not source:
        retry_after = task_retry_after(settings, context.attempts + 1)
        with connect() as conn:
            conn.execute(
                """
                UPDATE download_tasks
                SET status='pending', retry_after=?, last_error=?, updated_at=?
                WHERE id=?
                """,
                (retry_after, "发布缺少 magnet/torrent 链接，等待后自动重试", now(), download_task_id),
            )
            sync_cloud_submission(
                conn,
                series_id=0,
                entry_id=int(task["entry_id"]),
                episode_number=int(task["episode_number"]),
                release_id=int(task["release_id"]),
                download_task_id=download_task_id,
                status="pending",
                target_dir=str(task["target_dir"] or ""),
                normalized_name=str(task["normalized_name"] or ""),
                retry_after=retry_after,
                last_error="发布缺少 magnet/torrent 链接，等待后自动重试",
            )
        return ProcessorResult.retryable("发布缺少 magnet/torrent 链接，等待后自动重试", retry_after)

    try:
        existing_remote = await find_existing_remote_episode(
            settings,
            str(task["target_dir"] or ""),
            str(task["normalized_name"] or ""),
            int(task["episode_number"] or 0),
        )
    except Exception as exc:
        return ProcessorResult.retryable(f"云盘存在性检查失败，等待后重试: {str(exc)[:1800]}", task_retry_after(settings, context.attempts + 1))
    if existing_remote:
        asset_id = upsert_cloud_asset_from_download_task(download_task_id, existing_remote, settings)
        file_id = cloud_file_id(existing_remote)
        actual_name = str(existing_remote.get("name") or task["normalized_name"] or "")
        with connect() as conn:
            ts = now()
            conn.execute(
                """
                UPDATE download_tasks
                SET status='completed', pikpak_file_id=?, normalized_name=?, retry_after='', last_error='', updated_at=?
                WHERE id=?
                """,
                (file_id, actual_name, ts, download_task_id),
            )
            sync_cloud_submission(
                conn,
                series_id=0,
                entry_id=int(task["entry_id"]),
                episode_number=int(task["episode_number"]),
                release_id=int(task["release_id"]),
                download_task_id=download_task_id,
                status="completed",
                target_dir=str(task["target_dir"] or ""),
                normalized_name=actual_name,
                provider_file_id=file_id,
            )
            enqueue_cloud_asset_task(conn, download_task_id, ts)
        return ProcessorResult.skipped(
            "云盘同集文件已存在，跳过重复提交",
            data={"download_task_id": download_task_id, "cloud_asset_id": asset_id, "provider_file_id": file_id},
            next_payload={
                "_subject_type": "download_task",
                "_subject_id": download_task_id,
                "download_task_id": download_task_id,
                "release_id": int(task["release_id"]),
                "entry_id": int(task["entry_id"]),
                "cloud_asset_id": asset_id,
            },
        )

    with connect() as conn:
        conn.execute(
            """
            UPDATE download_tasks
            SET status='running', attempts=attempts+1, updated_at=?
            WHERE id=?
            """,
            (now(), download_task_id),
        )
        sync_cloud_submission(
            conn,
            series_id=0,
            entry_id=int(task["entry_id"]),
            episode_number=int(task["episode_number"]),
            release_id=int(task["release_id"]),
            download_task_id=download_task_id,
            status="running",
            target_dir=str(task["target_dir"] or ""),
            normalized_name=str(task["normalized_name"] or ""),
        )

    try:
        result = await submit_offline_download(settings, source, task["target_dir"], task["normalized_name"])
        task_id = extract_task_id(result) if isinstance(result, dict) else ""
        file_id = extract_file_id(result) if isinstance(result, dict) else ""
    except Exception as exc:
        if is_rate_limited_error(exc):
            retry_after = retry_after_time(settings)
            message = f"PikPak 限流，等待后自动重试: {str(exc)[:1800]}"
        else:
            retry_after = task_retry_after(settings, context.attempts + 1)
            message = f"提交失败，等待后自动重试: {str(exc)[:1800]}"
        with connect() as conn:
            conn.execute(
                """
                UPDATE download_tasks
                SET status='pending', retry_after=?, last_error=?, updated_at=?
                WHERE id=?
                """,
                (retry_after, message, now(), download_task_id),
            )
            sync_cloud_submission(
                conn,
                series_id=0,
                entry_id=int(task["entry_id"]),
                episode_number=int(task["episode_number"]),
                release_id=int(task["release_id"]),
                download_task_id=download_task_id,
                status="pending",
                target_dir=str(task["target_dir"] or ""),
                normalized_name=str(task["normalized_name"] or ""),
                retry_after=retry_after,
                last_error=message,
            )
        return ProcessorResult.retryable(message, retry_after)

    with connect() as conn:
        ts = now()
        status = "submitted"
        if file_id and not task_id and not rclone_service.enabled(settings):
            status = "completed"
        conn.execute(
            """
            UPDATE download_tasks
            SET status=?, pikpak_task_id=?, pikpak_file_id=?, retry_after='', last_error='', updated_at=?
            WHERE id=?
            """,
            (status, task_id, file_id, ts, download_task_id),
        )
        sync_cloud_submission(
            conn,
            series_id=0,
            entry_id=int(task["entry_id"]),
            episode_number=int(task["episode_number"]),
            release_id=int(task["release_id"]),
            download_task_id=download_task_id,
            status="completed" if status == "completed" else "submitted",
            target_dir=str(task["target_dir"] or ""),
            normalized_name=str(task["normalized_name"] or ""),
            submission_id=task_id,
            provider_file_id=file_id,
        )
        if status == "completed":
            enqueue_cloud_asset_task(conn, download_task_id, ts)
            next_step_payload = {
                "_subject_type": "download_task",
                "_subject_id": download_task_id,
                "download_task_id": download_task_id,
                "release_id": int(task["release_id"]),
                "entry_id": int(task["entry_id"]),
            }
        else:
            enqueue_cloud_poll_task(conn, download_task_id, ts)
            next_step_payload = {
                "_subject_type": "download_task",
                "_subject_id": download_task_id,
                "download_task_id": download_task_id,
                "release_id": int(task["release_id"]),
                "entry_id": int(task["entry_id"]),
                "pikpak_task_id": task_id,
                "pikpak_file_id": file_id,
            }

    return ProcessorResult.success(
        "云盘提交完成" if status == "completed" else "云盘任务已提交",
        data={"download_task_id": download_task_id, "status": status, "pikpak_task_id": task_id, "pikpak_file_id": file_id},
        next_payload=next_step_payload,
    )


async def process_cloud_poll(context: ProcessorContext, payload: dict) -> ProcessorResult:
    download_task_id = context.subject_id if context.subject_type == "download_task" else int(payload.get("download_task_id") or 0)
    if download_task_id <= 0:
        return ProcessorResult.terminal("云盘状态轮询缺少 download_task_id")
    settings = get_settings()
    try:
        await reconcile_rclone_submitted_tasks(settings, limit=10)
        await poll_submitted_tasks(settings, limit=10, force=True)
    except Exception as exc:
        return ProcessorResult.retryable(str(exc)[:2000], task_retry_after(settings, context.attempts + 1))

    with connect() as conn:
        task = conn.execute("SELECT * FROM download_tasks WHERE id=?", (download_task_id,)).fetchone()
    if not task:
        return ProcessorResult.terminal(f"下载任务不存在: {download_task_id}")
    if task["status"] != "completed":
        return ProcessorResult.retryable("云盘任务尚未完成，等待后继续轮询", task_retry_after(settings, context.attempts + 1))
    with connect() as conn:
        enqueue_cloud_asset_task(conn, download_task_id, now())
    return ProcessorResult.success(
        "云盘任务已完成",
        data={"download_task_id": download_task_id, "entry_id": int(task["entry_id"])},
        next_payload={
            "_subject_type": "download_task",
            "_subject_id": download_task_id,
            "download_task_id": download_task_id,
            "entry_id": int(task["entry_id"]),
            "release_id": int(task["release_id"]),
        },
    )


async def process_cloud_asset_register(context: ProcessorContext, payload: dict) -> ProcessorResult:
    download_task_id = context.subject_id if context.subject_type == "download_task" else int(payload.get("download_task_id") or 0)
    if download_task_id <= 0:
        return ProcessorResult.terminal("云盘资源登记缺少 download_task_id")
    settings = get_settings()
    try:
        asset_id = upsert_cloud_asset(download_task_id, settings)
        if not asset_id:
            await process_cloud_asset_tasks(settings, limit=1, force=True)
            asset_id = upsert_cloud_asset(download_task_id, settings)
    except Exception as exc:
        return ProcessorResult.retryable(str(exc)[:2000], task_retry_after(settings, context.attempts + 1))
    if not asset_id:
        return ProcessorResult.retryable("云盘资源暂未可登记，等待后重试", task_retry_after(settings, context.attempts + 1))

    with connect() as conn:
        row = conn.execute("SELECT entry_id FROM cloud_assets WHERE id=?", (asset_id,)).fetchone()
        entry_id = int(row["entry_id"] or 0) if row else 0
        if entry_id > 0:
            enqueue_sync_plan_task(conn, entry_id, now())
        conn.execute(
            "UPDATE cloud_asset_tasks SET status='completed', retry_after='', last_error='', updated_at=? WHERE download_task_id=?",
            (now(), download_task_id),
        )
    return ProcessorResult.success(
        "云盘资源登记完成",
        data={"download_task_id": download_task_id, "cloud_asset_id": asset_id, "entry_id": entry_id},
        next_payload={
            "_subject_type": "entry",
            "_subject_id": entry_id,
            "entry_id": entry_id,
            "cloud_asset_id": asset_id,
        },
    )
