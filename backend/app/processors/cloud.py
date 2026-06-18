from __future__ import annotations

from pathlib import PurePosixPath

from .. import rclone_service
from ..database import connect
from ..db import get_settings, log, now
from ..library import render_episode_name, target_dir
from ..pikpak_service import list_offline_tasks, submit_offline_download
from ..pipeline_models import ProcessorContext, ProcessorResult
from ..scanner import extract_file_id, extract_task_id, is_rate_limited_error, retry_after_time
from ..sync_service import (
    cloud_file_id,
    find_existing_remote_episode,
    synthetic_task_id,
    task_retry_after,
    upsert_cloud_asset_for_release,
)


def _release_row(release_id: int):
    with connect() as conn:
        return conn.execute(
            """
            SELECT r.*, e.display_title, e.title_raw, e.title_cn, e.bangumi_id, e.tmdb_id,
                   e.year, e.season_number
            FROM releases r
            JOIN entries e ON e.id=r.entry_id
            WHERE r.id=?
            """,
            (release_id,),
        ).fetchone()


def _release_subject(context: ProcessorContext, payload: dict) -> int:
    if context.subject_type == "release":
        return int(context.subject_id or 0)
    return int(payload.get("release_id") or 0)


def _submission_for_release(release_id: int):
    with connect() as conn:
        return conn.execute(
            """
            SELECT cs.*
            FROM cloud_submissions cs
            JOIN releases r ON r.entry_id=cs.entry_id AND r.episode_number=cs.episode_number
            WHERE r.id=? AND cs.provider='pikpak'
            LIMIT 1
            """,
            (release_id,),
        ).fetchone()


def _upsert_submission(
    *,
    release,
    status: str,
    target_directory: str,
    normalized_name: str,
    submission_id: str = "",
    provider_file_id: str = "",
    retry_after: str = "",
    last_error: str = "",
) -> None:
    ts = now()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO cloud_submissions
              (series_id, entry_id, episode_number, release_id, provider, download_task_id, status,
               attempts, submission_id, provider_file_id, target_dir, normalized_name,
               retry_after, last_error, created_at, updated_at, last_seen_at)
            VALUES (?, ?, ?, ?, 'pikpak', ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(entry_id, episode_number, provider) DO UPDATE SET
              release_id=excluded.release_id,
              series_id=excluded.series_id,
              download_task_id=excluded.download_task_id,
              status=excluded.status,
              submission_id=CASE WHEN excluded.submission_id!='' THEN excluded.submission_id ELSE cloud_submissions.submission_id END,
              provider_file_id=CASE WHEN excluded.provider_file_id!='' THEN excluded.provider_file_id ELSE cloud_submissions.provider_file_id END,
              target_dir=excluded.target_dir,
              normalized_name=excluded.normalized_name,
              retry_after=excluded.retry_after,
              last_error=excluded.last_error,
              updated_at=excluded.updated_at,
              last_seen_at=excluded.last_seen_at
            """,
            (
                int(release["series_id"] or 0),
                int(release["entry_id"] or 0),
                int(release["episode_number"] or 0),
                int(release["id"]),
                synthetic_task_id(f"release:{int(release['id'])}"),
                status,
                submission_id,
                provider_file_id,
                target_directory,
                normalized_name,
                retry_after,
                last_error[:2000],
                ts,
                ts,
                ts,
            ),
        )


def _asset_item(target_directory: str, normalized_name: str, provider_file_id: str) -> dict:
    return {
        "id": provider_file_id,
        "file_id": provider_file_id,
        "name": normalized_name,
        "cloud_path": str(PurePosixPath(target_directory) / normalized_name),
    }


async def process_cloud_presence(context: ProcessorContext, payload: dict) -> ProcessorResult:
    release_id = _release_subject(context, payload)
    if release_id <= 0:
        return ProcessorResult.terminal("云盘存在性检查缺少 release_id")
    settings = get_settings()
    release = _release_row(release_id)
    if not release:
        return ProcessorResult.terminal(f"发布不存在: {release_id}")

    entry_id = int(release["entry_id"] or 0)
    episode_number = int(release["episode_number"] or 0)
    with connect() as conn:
        existing_cloud = conn.execute(
            """
            SELECT id, cloud_name, provider_file_id
            FROM cloud_assets
            WHERE release_id=? OR (entry_id=? AND episode_number=? AND provider='pikpak')
            ORDER BY id ASC
            LIMIT 1
            """,
            (release_id, entry_id, episode_number),
        ).fetchone()
    if existing_cloud:
        log(
            "info",
            f"云盘存在性命中数据库: release_id={release_id} entry_id={entry_id} "
            f"episode={episode_number} cloud_asset_id={existing_cloud['id']} name={existing_cloud['cloud_name']}",
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

    remote_target = target_dir(dict(release), settings)
    remote_name = render_episode_name(dict(release), episode_number, "", settings)
    try:
        existing_remote = await find_existing_remote_episode(settings, remote_target, remote_name, episode_number)
    except Exception as exc:
        return ProcessorResult.retryable(
            f"云盘存在性检查失败，等待后重试: {str(exc)[:1800]}",
            task_retry_after(settings, context.attempts + 1),
        )

    if existing_remote:
        asset_id = upsert_cloud_asset_for_release(release_id, existing_remote, settings) or 0
        _upsert_submission(
            release=release,
            status="completed",
            target_directory=remote_target,
            normalized_name=str(existing_remote.get("name") or remote_name),
            provider_file_id=cloud_file_id(existing_remote),
        )
        log(
            "info",
            f"云盘存在性命中远端: release_id={release_id} entry_id={entry_id} episode={episode_number} "
            f"target_dir={remote_target} expected={remote_name} actual={existing_remote.get('name') or '-'} "
            f"cloud_asset_id={asset_id}",
        )
        return ProcessorResult.success(
            "云盘同集文件已存在，跳过重复提交",
            data={"release_id": release_id, "entry_id": entry_id, "cloud_asset_id": asset_id},
            next_payload={
                "_subject_type": "entry",
                "_subject_id": entry_id,
                "entry_id": entry_id,
                "cloud_asset_id": asset_id,
            },
        )

    log(
        "info",
        f"云盘存在性未命中: release_id={release_id} entry_id={entry_id} episode={episode_number} "
        f"target_dir={remote_target} expected={remote_name}",
    )
    return ProcessorResult.success(
        "云盘资源不存在，进入云盘提交",
        data={"release_id": release_id, "entry_id": entry_id, "episode_number": episode_number},
        next_payload={
            "_subject_type": "release",
            "_subject_id": release_id,
            "release_id": release_id,
            "entry_id": entry_id,
            "episode_number": episode_number,
        },
    )


async def process_cloud_submit(context: ProcessorContext, payload: dict) -> ProcessorResult:
    release_id = _release_subject(context, payload)
    if release_id <= 0:
        return ProcessorResult.terminal("云盘提交缺少 release_id")
    settings = get_settings()
    release = _release_row(release_id)
    if not release:
        return ProcessorResult.terminal(f"发布不存在: {release_id}")
    if not str(release["bangumi_id"] or ""):
        return ProcessorResult.terminal(f"条目缺少 Bangumi ID: release_id={release_id}")

    source = str(release["magnet"] or release["torrent_url"] or "")
    remote_target = target_dir(dict(release), settings)
    remote_name = render_episode_name(dict(release), int(release["episode_number"] or 0), "", settings)
    if not source:
        retry_after = task_retry_after(settings, context.attempts + 1)
        _upsert_submission(
            release=release,
            status="pending",
            target_directory=remote_target,
            normalized_name=remote_name,
            retry_after=retry_after,
            last_error="发布缺少 magnet/torrent 链接，等待后自动重试",
        )
        return ProcessorResult.retryable("发布缺少 magnet/torrent 链接，等待后自动重试", retry_after)

    try:
        existing_remote = await find_existing_remote_episode(
            settings,
            remote_target,
            remote_name,
            int(release["episode_number"] or 0),
        )
    except Exception as exc:
        return ProcessorResult.retryable(
            f"云盘存在性检查失败，等待后重试: {str(exc)[:1800]}",
            task_retry_after(settings, context.attempts + 1),
        )
    if existing_remote:
        asset_id = upsert_cloud_asset_for_release(release_id, existing_remote, settings) or 0
        _upsert_submission(
            release=release,
            status="completed",
            target_directory=remote_target,
            normalized_name=str(existing_remote.get("name") or remote_name),
            provider_file_id=cloud_file_id(existing_remote),
        )
        log(
            "info",
            f"PikPak 提交前命中远端: release_id={release_id} entry_id={release['entry_id']} "
            f"episode={release['episode_number']} expected={remote_name} actual={existing_remote.get('name') or '-'} "
            f"cloud_asset_id={asset_id}",
        )
        return ProcessorResult.skipped(
            "云盘同集文件已存在，跳过重复提交",
            data={"release_id": release_id, "entry_id": int(release["entry_id"]), "cloud_asset_id": asset_id},
            next_payload={
                "_subject_type": "release",
                "_subject_id": release_id,
                "release_id": release_id,
                "entry_id": int(release["entry_id"]),
                "cloud_asset_id": asset_id,
            },
        )

    existing_submission = _submission_for_release(release_id)
    if existing_submission and existing_submission["status"] in {"submitted", "running", "completed"}:
        status = str(existing_submission["status"] or "")
        log(
            "info",
            f"PikPak 提交复用同集记录: release_id={release_id} entry_id={release['entry_id']} "
            f"episode={release['episode_number']} submission_status={status} "
            f"submission_id={existing_submission['submission_id'] or '-'} file_id={existing_submission['provider_file_id'] or '-'}",
        )
        return ProcessorResult.success(
            "同集已有云盘提交记录，跳过重复提交",
            data={"release_id": release_id, "entry_id": int(release["entry_id"]), "status": status},
            next_payload={
                "_subject_type": "release",
                "_subject_id": release_id,
                "release_id": release_id,
                "entry_id": int(release["entry_id"]),
            },
        )

    _upsert_submission(
        release=release,
        status="running",
        target_directory=remote_target,
        normalized_name=remote_name,
    )
    try:
        result = await submit_offline_download(settings, source, remote_target, remote_name)
        task_id = extract_task_id(result) if isinstance(result, dict) else ""
        file_id = extract_file_id(result) if isinstance(result, dict) else ""
    except Exception as exc:
        if is_rate_limited_error(exc):
            retry_after = retry_after_time(settings)
            message = f"PikPak 限流，等待后自动重试: {str(exc)[:1800]}"
        else:
            retry_after = task_retry_after(settings, context.attempts + 1)
            message = f"提交失败，等待后自动重试: {str(exc)[:1800]}"
        _upsert_submission(
            release=release,
            status="pending",
            target_directory=remote_target,
            normalized_name=remote_name,
            retry_after=retry_after,
            last_error=message,
        )
        return ProcessorResult.retryable(message, retry_after)

    status = "completed" if file_id and not task_id and not rclone_service.enabled(settings) else "submitted"
    _upsert_submission(
        release=release,
        status=status,
        target_directory=remote_target,
        normalized_name=remote_name,
        submission_id=task_id,
        provider_file_id=file_id,
    )
    log(
        "info",
        f"已提交 PikPak: release_id={release_id} entry_id={release['entry_id']} episode={release['episode_number']} "
        f"target_dir={remote_target} normalized_name={remote_name} pikpak_task_id={task_id or '-'} file_id={file_id or '-'}",
    )
    return ProcessorResult.success(
        "云盘提交完成" if status == "completed" else "云盘任务已提交",
        data={"release_id": release_id, "entry_id": int(release["entry_id"]), "status": status},
        next_payload={
            "_subject_type": "release",
            "_subject_id": release_id,
            "release_id": release_id,
            "entry_id": int(release["entry_id"]),
        },
    )


async def process_cloud_poll(context: ProcessorContext, payload: dict) -> ProcessorResult:
    release_id = _release_subject(context, payload)
    if release_id <= 0:
        return ProcessorResult.terminal("云盘状态轮询缺少 release_id")
    settings = get_settings()
    release = _release_row(release_id)
    if not release:
        return ProcessorResult.terminal(f"发布不存在: {release_id}")
    submission = _submission_for_release(release_id)
    if not submission:
        return ProcessorResult.retryable("云盘提交记录尚未生成，等待后重试", task_retry_after(settings, context.attempts + 1))

    remote_target = str(submission["target_dir"] or target_dir(dict(release), settings))
    remote_name = str(submission["normalized_name"] or render_episode_name(dict(release), int(release["episode_number"] or 0), "", settings))
    status = str(submission["status"] or "submitted")
    file_id = str(submission["provider_file_id"] or "")
    matched = None
    message = ""
    try:
        if status != "completed":
            if rclone_service.enabled(settings):
                matched = await find_existing_remote_episode(settings, remote_target, remote_name, int(release["episode_number"] or 0))
                if matched:
                    file_id = cloud_file_id(matched)
                    status = "completed"
                else:
                    message = "rclone 已提交，目标目录暂未发现完成文件"
            elif file_id and not str(submission["submission_id"] or ""):
                status = "completed"
            else:
                remote_tasks = await list_offline_tasks(settings)
                remote = next((item for item in remote_tasks if item.get("id") == submission["submission_id"]), None)
                if not remote:
                    message = "PikPak 暂未返回该离线任务，等待后重试"
                else:
                    phase = remote.get("phase", "")
                    file_id = str(remote.get("file_id") or remote.get("reference_resource", {}).get("id", "") or file_id)
                    if phase == "PHASE_TYPE_COMPLETE":
                        status = "completed"
                    elif phase == "PHASE_TYPE_ERROR":
                        status = "failed"
                        message = str(remote.get("message") or "PikPak 离线任务失败")
                    else:
                        message = "云盘任务尚未完成，等待后继续轮询"
    except Exception as exc:
        return ProcessorResult.retryable(str(exc)[:2000], task_retry_after(settings, context.attempts + 1))

    if status != "completed":
        retry_after = task_retry_after(settings, context.attempts + 1)
        _upsert_submission(
            release=release,
            status="submitted",
            target_directory=remote_target,
            normalized_name=remote_name,
            submission_id=str(submission["submission_id"] or ""),
            provider_file_id=file_id,
            retry_after=retry_after,
            last_error=message,
        )
        log(
            "info" if status != "failed" else "warn",
            f"PikPak 状态轮询等待: release_id={release_id} entry_id={release['entry_id']} "
            f"episode={release['episode_number']} status={status} retry_after={retry_after} message={message or '-'}",
        )
        return ProcessorResult.retryable(message or "云盘任务尚未完成，等待后继续轮询", retry_after)

    item = matched or _asset_item(remote_target, remote_name, file_id)
    asset_id = upsert_cloud_asset_for_release(release_id, item, settings) or 0
    actual_name = str(item.get("name") or remote_name)
    _upsert_submission(
        release=release,
        status="completed",
        target_directory=remote_target,
        normalized_name=actual_name,
        submission_id=str(submission["submission_id"] or ""),
        provider_file_id=cloud_file_id(item) or file_id,
    )
    log(
        "info",
        f"PikPak 状态轮询完成: release_id={release_id} entry_id={release['entry_id']} "
        f"episode={release['episode_number']} actual_name={actual_name} file_id={cloud_file_id(item) or file_id or '-'} "
        f"cloud_asset_id={asset_id}",
    )
    return ProcessorResult.success(
        "云盘任务已完成",
        data={"release_id": release_id, "entry_id": int(release["entry_id"]), "cloud_asset_id": asset_id},
        next_payload={
            "_subject_type": "release",
            "_subject_id": release_id,
            "release_id": release_id,
            "entry_id": int(release["entry_id"]),
            "cloud_asset_id": asset_id,
        },
    )


async def process_cloud_asset_register(context: ProcessorContext, payload: dict) -> ProcessorResult:
    release_id = _release_subject(context, payload)
    if release_id <= 0:
        return ProcessorResult.terminal("云盘资源登记缺少 release_id")
    settings = get_settings()
    release = _release_row(release_id)
    if not release:
        return ProcessorResult.terminal(f"发布不存在: {release_id}")
    submission = _submission_for_release(release_id)
    if not submission or submission["status"] != "completed":
        return ProcessorResult.retryable("云盘提交尚未完成，等待后登记资源", task_retry_after(settings, context.attempts + 1))
    item = _asset_item(
        str(submission["target_dir"] or target_dir(dict(release), settings)),
        str(submission["normalized_name"] or render_episode_name(dict(release), int(release["episode_number"] or 0), "", settings)),
        str(submission["provider_file_id"] or ""),
    )
    try:
        asset_id = upsert_cloud_asset_for_release(release_id, item, settings) or 0
    except Exception as exc:
        return ProcessorResult.retryable(str(exc)[:2000], task_retry_after(settings, context.attempts + 1))
    if not asset_id:
        return ProcessorResult.retryable("云盘资源暂未可登记，等待后重试", task_retry_after(settings, context.attempts + 1))
    return ProcessorResult.success(
        "云盘资源登记完成",
        data={"release_id": release_id, "cloud_asset_id": asset_id, "entry_id": int(release["entry_id"])},
        next_payload={
            "_subject_type": "entry",
            "_subject_id": int(release["entry_id"]),
            "entry_id": int(release["entry_id"]),
            "cloud_asset_id": asset_id,
        },
    )
