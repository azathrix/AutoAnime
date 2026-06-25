from __future__ import annotations

from pathlib import Path, PurePosixPath

from ..database import connect
from ..db import get_settings, log, now
from ..download_task_service import canonical_download_status, download_phase, provider_index_from_key
from ..downloader_service import (
    failover_exhausted,
    list_tasks,
    needs_poll,
    provider_key,
    settings_for_attempt,
    settings_for_provider,
    submit_download,
)
from ..library import VIDEO_SUFFIXES, render_episode_file_name, target_dir
from ..pipeline_models import ProcessorContext, ProcessorResult
from ..runtime_store import runtime_store
from ..scanner import extract_file_id, extract_task_id, is_rate_limited_error, retry_after_time
from ..sync_service import (
    download_file_id,
    find_existing_remote_episode,
    local_episode_path,
    synthetic_task_id,
    task_retry_after,
    task_retry_after_minutes,
    upsert_download_artifact_for_release,
)


def _release_row(release_id: int):
    with connect() as conn:
        return conn.execute(
            """
            SELECT r.*, e.display_title, e.title_raw, e.title_cn, e.bangumi_id, e.tmdb_id,
                   e.year, e.season_number, e.media_type, e.target_library_id
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


def _remote_name_for_release(release, settings: dict[str, str]) -> str:
    return render_episode_file_name(
        dict(release),
        int(release["episode_number"] or 0),
        "",
        settings,
        str(release["title"] or ""),
        str(release["torrent_url"] or ""),
        str(release["magnet"] or ""),
    )


def _stored_or_expected_remote_name(release, settings: dict[str, str], stored_name: str = "") -> str:
    name = str(stored_name or "").strip()
    if Path(name).suffix.lower() in VIDEO_SUFFIXES:
        return name
    return _remote_name_for_release(release, settings)


def _submission_for_release(release_id: int):
    with connect() as conn:
        return conn.execute(
            """
            SELECT cs.*
            FROM download_jobs cs
            JOIN releases r ON r.entry_id=cs.entry_id AND r.episode_number=cs.episode_number
            WHERE r.id=?
            ORDER BY CASE cs.status
              WHEN 'local_copying' THEN 0
              WHEN 'remote_completed' THEN 1
              WHEN 'remote_downloading' THEN 2
              WHEN 'submitting' THEN 3
              WHEN 'pending' THEN 4
              WHEN 'completed' THEN 5
              WHEN 'failed' THEN 6
              WHEN 'submitted' THEN 7
              WHEN 'running' THEN 8
              ELSE 8
            END, cs.updated_at DESC, cs.id DESC
            LIMIT 1
            """,
            (release_id,),
        ).fetchone()


def _upsert_submission(
    *,
    settings: dict,
    release,
    status: str,
    target_directory: str,
    normalized_name: str,
    submission_id: str = "",
    provider_file_id: str = "",
    retry_after: str = "",
    last_error: str = "",
    total_size: int = 0,
) -> None:
    ts = now()
    provider = provider_key(settings)
    if status == "completed":
        status = "remote_completed"
    status = canonical_download_status(status)
    phase = download_phase(status)
    progress = {
        "pending": 0,
        "submitting": 0,
        "remote_downloading": 0,
        "remote_completed": 0,
        "local_copying": 0,
        "completed": 100,
        "failed": 0,
        "cancelled": 0,
    }.get(status, 0)
    progress_text = {
        "pending": "等待提交下载器",
        "submitting": "正在提交下载器",
        "remote_downloading": "下载器已提交，等待完成",
        "remote_completed": "下载器产物已完成，等待整理到本地",
        "local_copying": "正在整理到本地",
        "completed": "本地下载完成",
        "failed": last_error or "下载失败",
        "cancelled": "已取消",
    }.get(status, "")
    resource_status = {
        "pending": "queued",
        "submitting": "queued",
        "remote_downloading": "downloading",
        "remote_completed": "remote_completed",
        "local_copying": "downloading",
        "completed": "downloaded",
        "failed": "failed",
        "cancelled": "cancelled",
    }.get(status, "")
    source_ref = str(release["magnet"] or release["torrent_url"] or "")
    remote_path = str(PurePosixPath(target_directory) / normalized_name)
    target_local_path = local_episode_path(
        {"artifact_name": normalized_name, "episode_number": int(release["episode_number"] or 0)},
        dict(release),
        settings,
    )
    with connect() as conn:
        episode = conn.execute(
            "SELECT id FROM episodes WHERE entry_id=? AND episode_number=? ORDER BY id DESC LIMIT 1",
            (int(release["entry_id"] or 0), int(release["episode_number"] or 0)),
        ).fetchone()
        resource = conn.execute(
            """
            SELECT id
            FROM episode_resources
            WHERE entry_id=? AND episode_number=?
            ORDER BY selected DESC, id DESC
            LIMIT 1
            """,
            (int(release["entry_id"] or 0), int(release["episode_number"] or 0)),
        ).fetchone()
        conn.execute(
            """
            INSERT INTO download_jobs
              (series_id, entry_id, episode_resource_id, episode_id, episode_number, release_id,
               provider, provider_index, provider_key, download_task_id, status, phase,
               attempts, submission_id, provider_file_id, target_dir, remote_path, target_local_path,
               normalized_name, source_ref, media_type, retry_after, last_error, progress,
               progress_text, total_size, created_at, updated_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(entry_id, episode_number, provider) DO UPDATE SET
              release_id=excluded.release_id,
              series_id=excluded.series_id,
              episode_resource_id=excluded.episode_resource_id,
              episode_id=excluded.episode_id,
              download_task_id=excluded.download_task_id,
              status=excluded.status,
              phase=excluded.phase,
              provider_index=excluded.provider_index,
              provider_key=excluded.provider_key,
              submission_id=CASE WHEN excluded.submission_id!='' THEN excluded.submission_id ELSE download_jobs.submission_id END,
              provider_file_id=CASE WHEN excluded.provider_file_id!='' THEN excluded.provider_file_id ELSE download_jobs.provider_file_id END,
              target_dir=excluded.target_dir,
              remote_path=excluded.remote_path,
              target_local_path=excluded.target_local_path,
              normalized_name=excluded.normalized_name,
              source_ref=excluded.source_ref,
              media_type=excluded.media_type,
              retry_after=excluded.retry_after,
              last_error=excluded.last_error,
              progress=excluded.progress,
              progress_text=excluded.progress_text,
              total_size=CASE WHEN excluded.total_size>0 THEN excluded.total_size ELSE download_jobs.total_size END,
              updated_at=excluded.updated_at,
              last_seen_at=excluded.last_seen_at
            """,
            (
                int(release["series_id"] or 0),
                int(release["entry_id"] or 0),
                int(resource["id"] or 0) if resource else 0,
                int(episode["id"] or 0) if episode else 0,
                int(release["episode_number"] or 0),
                int(release["id"]),
                provider,
                provider_index_from_key(provider),
                provider,
                synthetic_task_id(f"release:{int(release['id'])}"),
                status,
                phase,
                submission_id,
                provider_file_id,
                target_directory,
                remote_path,
                target_local_path,
                normalized_name,
                source_ref,
                str(release["media_type"] or "anime"),
                retry_after,
                last_error[:2000],
                progress,
                progress_text[:500],
                max(0, int(total_size or 0)),
                ts,
                ts,
                ts,
            ),
        )
        if resource_status:
            conn.execute(
                """
                UPDATE episode_resources
                SET status=?,
                    updated_at=?
                WHERE entry_id=? AND episode_number=? AND selected=1
                """,
                (
                    resource_status,
                    ts,
                    int(release["entry_id"] or 0),
                    int(release["episode_number"] or 0),
                ),
            )


def _asset_item(target_directory: str, normalized_name: str, provider_file_id: str) -> dict:
    return {
        "id": provider_file_id,
        "file_id": provider_file_id,
        "name": normalized_name,
        "remote_path": str(PurePosixPath(target_directory) / normalized_name),
    }


def _remote_item_size(item: dict | None) -> int:
    if not item:
        return 0
    keys = (
        "size",
        "Size",
        "file_size",
        "fileSize",
        "total_size",
        "totalSize",
        "bytes",
        "total_bytes",
        "totalBytes",
    )
    reference = item.get("reference_resource")
    nested = reference if isinstance(reference, dict) else {}
    candidates = [item.get(key) for key in keys] + [nested.get(key) for key in keys]
    for value in candidates:
        if value in (None, ""):
            continue
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            continue
    progress = item.get("progress")
    if isinstance(progress, dict):
        for key in keys:
            value = progress.get(key)
            if value in (None, ""):
                continue
            try:
                return max(0, int(value))
            except (TypeError, ValueError):
                continue
    return 0


def _remote_item_ready(item: dict | None) -> bool:
    return _remote_item_size(item) > 0


def _local_asset_for_episode(entry_id: int, episode_number: int):
    with connect() as conn:
        return conn.execute(
            """
            SELECT id, local_path, nfo_status, status
            FROM local_assets
            WHERE entry_id=? AND episode_number=? AND status='synced'
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            """,
            (entry_id, episode_number),
        ).fetchone()


def _local_asset_exists(row) -> bool:
    if not row:
        return False
    local_path = str(row["local_path"] or "")
    if not local_path:
        return False
    try:
        path = Path(local_path)
        return path.exists() and path.stat().st_size > 0
    except OSError:
        return False


async def process_download_presence(context: ProcessorContext, payload: dict) -> ProcessorResult:
    release_id = _release_subject(context, payload)
    if release_id <= 0:
        return ProcessorResult.terminal("下载产物检查缺少 release_id")
    settings = settings_for_attempt(get_settings(), 0)
    release = _release_row(release_id)
    if not release:
        return ProcessorResult.terminal(f"发布不存在: {release_id}")

    entry_id = int(release["entry_id"] or 0)
    episode_number = int(release["episode_number"] or 0)
    with connect() as conn:
        existing_cloud = conn.execute(
            """
            SELECT id, artifact_name, provider_file_id
            FROM download_artifacts
            WHERE release_id=? OR (entry_id=? AND episode_number=?)
            ORDER BY id ASC
            LIMIT 1
            """,
            (release_id, entry_id, episode_number),
        ).fetchone()
    if existing_cloud:
        log(
            "info",
            f"下载产物命中数据库: release_id={release_id} entry_id={entry_id} "
            f"episode={episode_number} download_artifact_id={existing_cloud['id']} name={existing_cloud['artifact_name']}",
        )
        return ProcessorResult.skipped(
            "下载产物已存在",
            data={"release_id": release_id, "entry_id": entry_id, "download_artifact_id": int(existing_cloud["id"])},
            next_payload={
                "_subject_type": "entry",
                "_subject_id": entry_id,
                "entry_id": entry_id,
                "download_artifact_id": int(existing_cloud["id"]),
            },
        )

    remote_target = target_dir(dict(release), settings)
    remote_name = _remote_name_for_release(release, settings)
    try:
        existing_remote = await find_existing_remote_episode(settings, remote_target, remote_name, episode_number)
    except Exception as exc:
        return ProcessorResult.retryable(
            f"下载产物检查失败，等待后重试: {str(exc)[:1800]}",
            task_retry_after(settings, context.attempts + 1),
        )

    if existing_remote:
        if not _remote_item_ready(existing_remote):
            retry_after = task_retry_after_minutes(1)
            _upsert_submission(
                settings=settings,
                release=release,
                status="remote_downloading",
                target_directory=remote_target,
                normalized_name=str(existing_remote.get("name") or remote_name),
                provider_file_id=download_file_id(existing_remote),
                retry_after=retry_after,
                last_error="",
                total_size=_remote_item_size(existing_remote),
            )
            return ProcessorResult.retryable("远端文件已出现但大小未知，等待云存储完成", retry_after)
        asset_id = upsert_download_artifact_for_release(release_id, existing_remote, settings) or 0
        if asset_id <= 0:
            return ProcessorResult.retryable(
                "下载器已有完成文件，但下载产物暂未登记成功，等待后重试",
                task_retry_after(settings, context.attempts + 1),
            )
        _upsert_submission(
            settings=settings,
            release=release,
            status="remote_completed",
            target_directory=remote_target,
            normalized_name=str(existing_remote.get("name") or remote_name),
            provider_file_id=download_file_id(existing_remote),
            total_size=_remote_item_size(existing_remote),
        )
        log(
            "info",
            f"下载器远端产物命中: release_id={release_id} entry_id={entry_id} episode={episode_number} "
            f"target_dir={remote_target} expected={remote_name} actual={existing_remote.get('name') or '-'} "
            f"download_artifact_id={asset_id}",
        )
        return ProcessorResult.success(
            "同集下载产物已存在，跳过重复提交",
            data={"release_id": release_id, "entry_id": entry_id, "download_artifact_id": asset_id},
            next_payload={
                "_subject_type": "entry",
                "_subject_id": entry_id,
                "entry_id": entry_id,
                "download_artifact_id": asset_id,
            },
        )

    log(
        "info",
        f"下载产物未命中: release_id={release_id} entry_id={entry_id} episode={episode_number} "
        f"target_dir={remote_target} expected={remote_name}",
    )
    return ProcessorResult.success(
        "下载产物不存在，进入下载器提交",
        data={"release_id": release_id, "entry_id": entry_id, "episode_number": episode_number},
        next_payload={
            "_subject_type": "release",
            "_subject_id": release_id,
            "release_id": release_id,
            "entry_id": entry_id,
            "episode_number": episode_number,
        },
    )


async def process_download_submit(context: ProcessorContext, payload: dict) -> ProcessorResult:
    release_id = _release_subject(context, payload)
    if release_id <= 0:
        return ProcessorResult.terminal("下载提交缺少 release_id")
    base_settings = get_settings()
    settings = settings_for_attempt(base_settings, context.attempts)
    release = _release_row(release_id)
    if not release:
        return ProcessorResult.terminal(f"发布不存在: {release_id}")

    source = str(release["magnet"] or release["torrent_url"] or "")
    remote_target = target_dir(dict(release), settings)
    remote_name = _remote_name_for_release(release, settings)
    await runtime_store.update_task_progress(context.task_id, 5, "检查下载器远端是否已有文件")
    if not source:
        retry_after = task_retry_after(settings, context.attempts + 1)
        _upsert_submission(
            settings=settings,
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
            f"下载产物检查失败，等待后重试: {str(exc)[:1800]}",
            task_retry_after(settings, context.attempts + 1),
        )
    if existing_remote:
        if not _remote_item_ready(existing_remote):
            retry_after = task_retry_after_minutes(1)
            _upsert_submission(
                settings=settings,
                release=release,
                status="remote_downloading",
                target_directory=remote_target,
                normalized_name=str(existing_remote.get("name") or remote_name),
                provider_file_id=download_file_id(existing_remote),
                retry_after=retry_after,
                last_error="",
                total_size=_remote_item_size(existing_remote),
            )
            log(
                "info",
                f"下载提交前命中远端文件但大小未知: release_id={release_id} entry_id={release['entry_id']} "
                f"episode={release['episode_number']} name={existing_remote.get('name') or '-'}",
            )
            return ProcessorResult.retryable("远端文件已出现但大小未知，等待云存储完成", retry_after)
        asset_id = upsert_download_artifact_for_release(release_id, existing_remote, settings) or 0
        if asset_id <= 0:
            return ProcessorResult.retryable(
                "下载器已有完成文件，但下载产物暂未登记成功，等待后重试",
                task_retry_after(settings, context.attempts + 1),
            )
        _upsert_submission(
            settings=settings,
            release=release,
            status="completed",
            target_directory=remote_target,
            normalized_name=str(existing_remote.get("name") or remote_name),
            provider_file_id=download_file_id(existing_remote),
            total_size=_remote_item_size(existing_remote),
        )
        log(
            "info",
            f"下载提交前命中远端产物: release_id={release_id} entry_id={release['entry_id']} "
            f"episode={release['episode_number']} expected={remote_name} actual={existing_remote.get('name') or '-'} "
            f"download_artifact_id={asset_id}",
        )
        return ProcessorResult.skipped(
            "同集下载产物已存在，跳过重复提交",
            data={"release_id": release_id, "entry_id": int(release["entry_id"]), "download_artifact_id": asset_id},
            next_payload={
                "_subject_type": "release",
                "_subject_id": release_id,
                "release_id": release_id,
                "entry_id": int(release["entry_id"]),
                "download_artifact_id": asset_id,
            },
        )

    existing_submission = _submission_for_release(release_id)
    if existing_submission and canonical_download_status(str(existing_submission["status"] or "")) in {"submitting", "remote_downloading", "remote_completed", "completed"}:
        status = canonical_download_status(str(existing_submission["status"] or ""))
        log(
            "info",
            f"下载提交复用同集记录: release_id={release_id} entry_id={release['entry_id']} "
            f"episode={release['episode_number']} submission_status={status} "
            f"submission_id={existing_submission['submission_id'] or '-'} file_id={existing_submission['provider_file_id'] or '-'}",
        )
        return ProcessorResult.success(
            "同集已有下载记录，跳过重复提交",
            data={"release_id": release_id, "entry_id": int(release["entry_id"]), "status": status},
            next_payload={
                "_subject_type": "release",
                "_subject_id": release_id,
                "release_id": release_id,
                "entry_id": int(release["entry_id"]),
            },
        )

    _upsert_submission(
        settings=settings,
        release=release,
        status="submitting",
        target_directory=remote_target,
        normalized_name=remote_name,
    )
    try:
        await runtime_store.update_task_progress(context.task_id, 10, "正在提交下载器任务")
        result = await submit_download(settings, source, remote_target, remote_name)
        task_id = extract_task_id(result) if isinstance(result, dict) else ""
        file_id = extract_file_id(result) if isinstance(result, dict) else ""
    except Exception as exc:
        next_attempt = context.attempts + 1
        if is_rate_limited_error(exc):
            retry_after = retry_after_time(settings)
            message = f"PikPak 限流，等待后自动重试: {str(exc)[:1800]}"
        else:
            retry_after = task_retry_after(settings, next_attempt)
            message = f"提交失败，等待后自动重试: {str(exc)[:1800]}"
        terminal = failover_exhausted(base_settings, next_attempt)
        _upsert_submission(
            settings=settings,
            release=release,
            status="failed" if terminal else "pending",
            target_directory=remote_target,
            normalized_name=remote_name,
            retry_after="" if terminal else retry_after,
            last_error=message,
        )
        if terminal:
            return ProcessorResult.terminal(f"{message}；已尝试所有可执行下载器，需要手动处理")
        return ProcessorResult.retryable(message, retry_after)

    status = "remote_completed" if file_id and not task_id and not needs_poll(settings) else "remote_downloading"
    _upsert_submission(
        settings=settings,
        release=release,
        status=status,
        target_directory=remote_target,
        normalized_name=remote_name,
        submission_id=task_id,
        provider_file_id=file_id,
    )
    log(
        "info",
        f"已提交下载器: release_id={release_id} entry_id={release['entry_id']} episode={release['episode_number']} "
        f"target_dir={remote_target} normalized_name={remote_name} pikpak_task_id={task_id or '-'} file_id={file_id or '-'}",
    )
    return ProcessorResult.success(
        "下载提交完成" if status == "remote_completed" else "下载任务已提交",
        data={"release_id": release_id, "entry_id": int(release["entry_id"]), "status": status},
        next_payload={
            "_subject_type": "release",
            "_subject_id": release_id,
            "release_id": release_id,
            "entry_id": int(release["entry_id"]),
        },
    )


async def process_download(context: ProcessorContext, payload: dict) -> ProcessorResult:
    release_id = _release_subject(context, payload)
    if release_id <= 0:
        return ProcessorResult.terminal("下载处理器缺少 release_id")
    settings = get_settings()
    release = _release_row(release_id)
    if not release:
        return ProcessorResult.terminal(f"发布不存在: {release_id}")

    entry_id = int(release["entry_id"] or 0)
    episode_number = int(release["episode_number"] or 0)
    local_asset = _local_asset_for_episode(entry_id, episode_number)
    if _local_asset_exists(local_asset):
        local_asset_id = int(local_asset["id"] or 0)
        log(
            "info",
            f"下载跳过: 本地文件已存在 entry_id={entry_id} episode={episode_number} "
            f"local_asset_id={local_asset_id} target={local_asset['local_path']}",
        )
        return ProcessorResult.skipped(
            "同集本地文件已存在，跳过重复下载",
            data={"release_id": release_id, "entry_id": entry_id, "local_asset_id": local_asset_id},
        )

    submission = _submission_for_release(release_id)
    submission_status = canonical_download_status(str(submission["status"] or "")) if submission else ""
    if not submission or submission_status not in {"submitting", "remote_downloading", "remote_completed", "completed"}:
        submit_result = await process_download_submit(context, payload)
        if submit_result.status == "failed_retryable":
            return submit_result
        if submit_result.status in {"failed_terminal", "conflict"}:
            return submit_result
        asset_id = int(
            (submit_result.next_payload or {}).get("download_artifact_id")
            or submit_result.data.get("download_artifact_id")
            or 0
        )
        if asset_id > 0:
            return await _sync_completed_artifact(context, payload, asset_id)
        retry_after = task_retry_after(settings, context.attempts + 1)
        return ProcessorResult.retryable(
            submit_result.message or "下载任务已提交，等待完成",
            retry_after,
            data=submit_result.data,
        )

    poll_result = await process_download_poll(context, payload)
    if poll_result.status == "failed_retryable":
        return poll_result
    if poll_result.status in {"failed_terminal", "conflict"}:
        return poll_result
    asset_id = int(
        (poll_result.next_payload or {}).get("download_artifact_id")
        or poll_result.data.get("download_artifact_id")
        or 0
    )
    if asset_id <= 0:
        return ProcessorResult.retryable(
            "下载任务已完成，但缺少下载产物记录，等待后重试",
            task_retry_after(settings, context.attempts + 1),
            data=poll_result.data,
        )
    return await _sync_completed_artifact(context, payload, asset_id)


async def _sync_completed_artifact(context: ProcessorContext, payload: dict, download_artifact_id: int) -> ProcessorResult:
    from .sync import sync_download_artifact_to_local

    await runtime_store.update_task_progress(context.task_id, 35, "下载器已完成，开始整理到本地")
    local_result = await sync_download_artifact_to_local(context, payload, download_artifact_id)
    if local_result.status != "success":
        return local_result
    log(
        "info",
        f"下载处理器完成: download_artifact_id={download_artifact_id} "
        f"local_asset_id={local_result.data.get('local_asset_id') or 0}",
    )
    return ProcessorResult.success(
        "下载到本地完成",
        data=local_result.data,
        next_payload=local_result.next_payload,
    )


async def process_download_poll(context: ProcessorContext, payload: dict) -> ProcessorResult:
    release_id = _release_subject(context, payload)
    if release_id <= 0:
        return ProcessorResult.terminal("下载状态轮询缺少 release_id")
    base_settings = get_settings()
    release = _release_row(release_id)
    if not release:
        return ProcessorResult.terminal(f"发布不存在: {release_id}")
    submission = _submission_for_release(release_id)
    settings = settings_for_provider(base_settings, str(submission["provider"] or "")) if submission else settings_for_attempt(base_settings, context.attempts)
    if not submission:
        return ProcessorResult.retryable("下载记录尚未生成，等待后重试", task_retry_after(settings, context.attempts + 1))

    await runtime_store.update_task_progress(context.task_id, 20, "正在轮询下载器完成状态")
    remote_target = str(submission["target_dir"] or target_dir(dict(release), settings))
    remote_name = _stored_or_expected_remote_name(release, settings, str(submission["normalized_name"] or ""))
    status = canonical_download_status(str(submission["status"] or "remote_downloading"))
    file_id = str(submission["provider_file_id"] or "")
    matched = None
    message = ""
    try:
        if status != "remote_completed":
            if needs_poll(settings):
                matched = await find_existing_remote_episode(settings, remote_target, remote_name, int(release["episode_number"] or 0))
                if matched:
                    file_id = download_file_id(matched)
                    if _remote_item_ready(matched):
                        status = "remote_completed"
                    else:
                        message = "远端文件已出现但大小未知，等待云存储完成"
                else:
                    message = "下载器已提交，目标目录暂未发现完成文件"
            elif file_id and not str(submission["submission_id"] or ""):
                status = "remote_completed"
            else:
                remote_tasks = await list_tasks(settings)
                remote = next((item for item in remote_tasks if item.get("id") == submission["submission_id"]), None)
                if not remote:
                    message = "PikPak 暂未返回该离线任务，等待后重试"
                else:
                    phase = remote.get("phase", "")
                    file_id = str(remote.get("file_id") or remote.get("reference_resource", {}).get("id", "") or file_id)
                    matched = remote
                    if phase == "PHASE_TYPE_COMPLETE":
                        status = "remote_completed"
                    elif phase == "PHASE_TYPE_ERROR":
                        status = "failed"
                        message = str(remote.get("message") or "PikPak 离线任务失败")
                    else:
                        message = "下载任务尚未完成，等待后继续轮询"
    except Exception as exc:
        return ProcessorResult.retryable(str(exc)[:2000], task_retry_after(settings, context.attempts + 1))

    if status != "remote_completed":
        retry_after = task_retry_after(settings, context.attempts + 1)
        if matched:
            retry_after = task_retry_after_minutes(1)
        _upsert_submission(
            settings=settings,
            release=release,
            status="failed" if status == "failed" else "remote_downloading",
            target_directory=remote_target,
            normalized_name=remote_name,
            submission_id=str(submission["submission_id"] or ""),
            provider_file_id=file_id,
            retry_after=retry_after,
            last_error="" if matched and status != "failed" else message,
            total_size=_remote_item_size(matched) if matched else 0,
        )
        log(
            "info" if status != "failed" else "warn",
            f"下载状态轮询等待: release_id={release_id} entry_id={release['entry_id']} "
            f"episode={release['episode_number']} status={status} retry_after={retry_after} message={message or '-'}",
        )
        return ProcessorResult.retryable(message or "下载任务尚未完成，等待后继续轮询", retry_after)

    item = matched or _asset_item(remote_target, remote_name, file_id)
    asset_id = upsert_download_artifact_for_release(release_id, item, settings) or 0
    if asset_id <= 0:
        return ProcessorResult.retryable(
            "下载任务已完成，但下载产物暂未登记成功，等待后重试",
            task_retry_after(settings, context.attempts + 1),
        )
    actual_name = str(item.get("name") or remote_name)
    _upsert_submission(
        settings=settings,
        release=release,
        status="remote_completed",
        target_directory=remote_target,
        normalized_name=actual_name,
        submission_id=str(submission["submission_id"] or ""),
        provider_file_id=download_file_id(item) or file_id,
        total_size=int(item.get("size") or item.get("Size") or 0),
    )
    log(
        "info",
        f"下载状态轮询完成: release_id={release_id} entry_id={release['entry_id']} "
        f"episode={release['episode_number']} actual_name={actual_name} file_id={download_file_id(item) or file_id or '-'} "
        f"download_artifact_id={asset_id}",
    )
    return ProcessorResult.success(
        "下载任务已完成",
        data={"release_id": release_id, "entry_id": int(release["entry_id"]), "download_artifact_id": asset_id},
        next_payload={
            "_subject_type": "release",
            "_subject_id": release_id,
            "release_id": release_id,
            "entry_id": int(release["entry_id"]),
            "download_artifact_id": asset_id,
        },
    )


async def process_download_artifact_register(context: ProcessorContext, payload: dict) -> ProcessorResult:
    release_id = _release_subject(context, payload)
    if release_id <= 0:
        return ProcessorResult.terminal("下载产物登记缺少 release_id")
    base_settings = get_settings()
    release = _release_row(release_id)
    if not release:
        return ProcessorResult.terminal(f"发布不存在: {release_id}")
    submission = _submission_for_release(release_id)
    settings = settings_for_provider(base_settings, str(submission["provider"] or "")) if submission else settings_for_attempt(base_settings, context.attempts)
    if not submission or canonical_download_status(str(submission["status"] or "")) not in {"remote_completed", "completed"}:
        return ProcessorResult.retryable("下载尚未完成，等待后登记产物", task_retry_after(settings, context.attempts + 1))
    item = _asset_item(
        str(submission["target_dir"] or target_dir(dict(release), settings)),
        _stored_or_expected_remote_name(release, settings, str(submission["normalized_name"] or "")),
        str(submission["provider_file_id"] or ""),
    )
    try:
        asset_id = upsert_download_artifact_for_release(release_id, item, settings) or 0
    except Exception as exc:
        return ProcessorResult.retryable(str(exc)[:2000], task_retry_after(settings, context.attempts + 1))
    if not asset_id:
        return ProcessorResult.retryable("下载产物暂未可登记，等待后重试", task_retry_after(settings, context.attempts + 1))
    return ProcessorResult.success(
        "下载产物登记完成",
        data={"release_id": release_id, "download_artifact_id": asset_id, "entry_id": int(release["entry_id"])},
        next_payload={
            "_subject_type": "entry",
            "_subject_id": int(release["entry_id"]),
            "entry_id": int(release["entry_id"]),
            "download_artifact_id": asset_id,
        },
    )



