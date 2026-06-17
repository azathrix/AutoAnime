from __future__ import annotations

from ..database import connect
from ..db import get_settings, now
from ..pipeline_models import ProcessorContext, ProcessorResult
from ..scanner import enqueue_download_enqueue_task, ensure_download_task_for_release, task_retry_after


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
            return ProcessorResult.success(
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
