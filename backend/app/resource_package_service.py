from __future__ import annotations

import asyncio
import re
import shutil
from pathlib import Path
from typing import Any

from . import local_downloader_service, rclone_service
from .config import MEDIA_ROOT
from .database import connect
from .db import get_settings, log, now, upsert_calendar_entry
from .downloader_service import (
    backend_key,
    download_to_local,
    list_remote_files,
    provider_key,
    remote_file_id,
    settings_for_attempt,
    settings_for_provider,
    submit_download,
)
from .library import cn_number_to_int, expected_local_episode_path
from .nfo_service import generate_jellyfin_nfo_for_entry
from .parser import clean_name, parse_episode
from .schemas import DiscoveryPackageDownloadPayload, ResourcePackageApplyPayload, ResourcePackageTargetEntryPayload
from .sync_service import synthetic_task_id
from .utils import row_to_dict


VIDEO_SUFFIXES = {".mkv", ".mp4", ".avi", ".mov", ".wmv", ".ts", ".m2ts", ".flv", ".webm"}
SUBTITLE_SUFFIXES = {".ass", ".srt", ".ssa", ".vtt", ".sup", ".sub"}
PACKAGE_SCAN_POLL_ATTEMPTS = 24
PACKAGE_SCAN_POLL_INTERVAL_SECONDS = 15
PACKAGE_AUTO_MATCH_THRESHOLD = 0.7
PACKAGE_AUTO_CREATE_THRESHOLD = 0.85

_active_package_tasks: dict[int, asyncio.Task] = {}


def _compact_text(value: str) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", (value or "").lower())


def _package_is_multi_season(title: str) -> bool:
    text = title or ""
    compact = _compact_text(text)
    return bool(
        re.search(r"\bS\d{1,2}\s*[+＋&＆/]\s*S\d{1,2}\b", text, re.I)
        or re.search(r"全\s*([两二2三四五六七八九十\d]+)\s*季", text)
        or re.search(r"\b(season|s)\s*\d{1,2}\s*[+＋&＆/]\s*(season|s)?\s*\d{1,2}\b", text, re.I)
        or ("afterstory" in compact and re.search(r"[+＋&＆/]", text))
    )


def _season_aliases_from_title(title: str) -> dict[str, int]:
    aliases: dict[str, int] = {}
    text = title or ""
    parts = [part.strip() for part in re.split(r"[+＋&＆/]", text) if part.strip()]
    for index, part in enumerate(parts, start=1):
        season = _parse_explicit_season(part) or index
        cleaned = re.sub(r"\bS\d{1,2}\b|\bSeason\s*\d{1,2}\b|第[一二三四五六七八九十百零两\d]+季", " ", part, flags=re.I)
        compact = _compact_text(cleaned)
        if len(compact) >= 5:
            aliases[compact] = season
    compact_title = _compact_text(text)
    if "afterstory" in compact_title and re.search(r"[+＋&＆/]", text):
        aliases["afterstory"] = 2
        aliases["clannadafterstory"] = 2
    return aliases


def _parse_explicit_season(text: str) -> int:
    value = text or ""
    match = re.search(r"\bS(\d{1,2})E\d{1,3}\b", value, re.I)
    if match:
        return int(match.group(1))
    match = re.search(r"\bSeason\s*(\d{1,2})\b", value, re.I)
    if match:
        return int(match.group(1))
    match = re.search(r"(?:^|[\s._\-\[\]()【】])S(\d{1,2})(?:$|[\s._\-\[\]()【】])", value, re.I)
    if match:
        return int(match.group(1))
    match = re.search(r"第([一二三四五六七八九十百零两\d]+)季", value)
    if match:
        return cn_number_to_int(match.group(1))
    return 0


def _parse_episode_for_package(text: str, media_type: str) -> int:
    value = text or ""
    match = re.search(r"\bS\d{1,2}E(\d{1,3})\b", value, re.I)
    if match:
        return int(match.group(1))
    match = re.search(r"\bSP\s*(\d{1,3})\b", value, re.I)
    if match:
        return int(match.group(1))
    parsed = parse_episode(value)
    if parsed > 0:
        return parsed
    if media_type == "movie":
        return 1
    return 0


def _is_special_file(text: str) -> bool:
    return bool(re.search(r"\b(ova|oad|sp|specials?)\b|番外|特别篇|特別篇", text or "", re.I))


def _resource_ref(row: Any) -> str:
    return str(row["resource_ref"] or "").strip()


def _file_kind(name: str) -> str:
    suffix = Path(name or "").suffix.lower()
    if suffix in VIDEO_SUFFIXES:
        return "video"
    if suffix in SUBTITLE_SUFFIXES:
        return "subtitle"
    return "other"


def _package_base_dir(settings: dict[str, str], entry_id: int, package_id: int, work_id: int = 0) -> str:
    backend = backend_key(settings)
    owner = f"work-{work_id}" if work_id > 0 else str(entry_id)
    if backend in {"aria2", "qb"}:
        return str((Path(MEDIA_ROOT) / ".anitrack-staging" / owner / str(package_id)).resolve())
    return f"/.anitrack-staging/{owner}/{package_id}"


def _child_dir(base_dir: str, item_id: int) -> str:
    if Path(base_dir).is_absolute() and ":" in base_dir[:4]:
        return str(Path(base_dir) / f"resource-{item_id}")
    if Path(base_dir).is_absolute() and not base_dir.startswith("/.anitrack-staging"):
        return str(Path(base_dir) / f"resource-{item_id}")
    return f"{base_dir.rstrip('/')}/resource-{item_id}"


def _ensure_entry_for_package(payload: DiscoveryPackageDownloadPayload) -> dict[str, Any]:
    entry_id = int(payload.entry_id or 0)
    if entry_id <= 0:
        raise ValueError("请先匹配或收录作品后再下载资源包")
    with connect() as conn:
        entry = conn.execute("SELECT * FROM entries WHERE id=?", (entry_id,)).fetchone()
    if not entry:
        raise ValueError("绑定的媒体条目不存在")
    return row_to_dict(entry)


def create_package_from_discovery(result_id: int, payload: DiscoveryPackageDownloadPayload) -> dict[str, Any]:
    with connect() as conn:
        result_row = conn.execute("SELECT * FROM discovery_results WHERE id=?", (result_id,)).fetchone()
        if not result_row:
            raise ValueError("发现结果不存在")
        resource_rows = conn.execute(
            """
            SELECT *
            FROM discovery_resources
            WHERE result_id=? AND resource_ref!=''
            ORDER BY source_id ASC, episode_number ASC, id ASC
            """,
            (result_id,),
        ).fetchall()
    result = row_to_dict(result_row)
    refs: set[str] = set()
    resources = []
    for row in resource_rows:
        ref = _resource_ref(row)
        if not ref or ref in refs:
            continue
        refs.add(ref)
        resources.append(row_to_dict(row))
    if not resources:
        raise ValueError("该发现结果没有可下载的种子或磁链")

    settings = settings_for_attempt(get_settings(), 0)
    provider = provider_key(settings)
    ts = now()
    title = result.get("title") or result.get("original_title") or "资源包"
    entry = _ensure_entry_for_package(payload)
    entry_id = int(entry.get("id") or 0)
    work_id = int(entry.get("work_id") or 0)
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO resource_packages
              (entry_id, work_id, result_id, search_id, title, media_type, provider, status, match_status,
               total_resources, completed_resources, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', 'pending', ?, 0, ?, ?)
            """,
            (
                entry_id,
                work_id,
                result_id,
                int(result.get("search_id") or 0),
                title,
                result.get("media_type") or "anime",
                provider,
                len(resources),
                ts,
                ts,
            ),
        )
        package_id = int(conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"])
        base_dir = _package_base_dir(settings, entry_id, package_id, work_id)
        conn.execute(
            "UPDATE resource_packages SET target_dir=?, updated_at=? WHERE id=?",
            (base_dir, ts, package_id),
        )
        for resource in resources:
            conn.execute(
                """
                INSERT INTO resource_package_items
                  (package_id, discovery_resource_id, source_ref, source_title, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'queued', ?, ?)
                """,
                (
                    package_id,
                    int(resource.get("id") or 0),
                    resource.get("resource_ref") or "",
                    resource.get("source_title") or "",
                    ts,
                    ts,
                ),
            )
            item_id = int(conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"])
            conn.execute(
                "UPDATE resource_package_items SET target_dir=?, updated_at=? WHERE id=?",
                (_child_dir(base_dir, item_id), ts, item_id),
            )
    trigger_package_download(package_id)
    return package_detail(package_id)


def trigger_package_download(package_id: int) -> None:
    if package_id <= 0:
        return
    task = _active_package_tasks.get(package_id)
    if task and not task.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = loop.create_task(run_package_download(package_id))
    _active_package_tasks[package_id] = task
    task.add_done_callback(lambda finished, key=package_id: _active_package_tasks.pop(key, None))


async def run_package_download(package_id: int) -> None:
    with connect() as conn:
        package = conn.execute("SELECT * FROM resource_packages WHERE id=?", (package_id,)).fetchone()
        items = conn.execute("SELECT * FROM resource_package_items WHERE package_id=? ORDER BY id", (package_id,)).fetchall()
    if not package:
        return
    settings = settings_for_provider(get_settings(), str(package["provider"] or ""))
    ts = now()
    with connect() as conn:
        conn.execute(
            "UPDATE resource_packages SET status='downloading', last_error='', updated_at=? WHERE id=?",
            (ts, package_id),
        )
    completed = 0
    for item in items:
        item_id = int(item["id"] or 0)
        source = str(item["source_ref"] or "")
        target_dir = str(item["target_dir"] or "")
        try:
            with connect() as conn:
                conn.execute(
                    "UPDATE resource_package_items SET status='submitting', last_error='', updated_at=? WHERE id=?",
                    (now(), item_id),
                )
            result = await submit_download(settings, source, target_dir, "")
            submission_id = str(result.get("task_id") or result.get("id") or "") if isinstance(result, dict) else ""
            provider_file_id = str(result.get("file_id") or result.get("id") or "") if isinstance(result, dict) else ""
            with connect() as conn:
                conn.execute(
                    """
                    UPDATE resource_package_items
                    SET status='downloading', submission_id=?, provider_file_id=?, updated_at=?
                    WHERE id=?
                    """,
                    (submission_id, provider_file_id, now(), item_id),
                )
            completed += 1
        except Exception as exc:
            message = str(exc)[:2000]
            log("error", f"资源包下载提交失败: package_id={package_id} item_id={item_id} error={message}")
            with connect() as conn:
                conn.execute(
                    "UPDATE resource_package_items SET status='failed', last_error=?, updated_at=? WHERE id=?",
                    (message, now(), item_id),
                )
    status = "downloading" if completed else "failed"
    with connect() as conn:
        conn.execute(
            """
            UPDATE resource_packages
            SET status=?, completed_resources=?, last_error=CASE WHEN ?=0 THEN '资源包下载提交失败' ELSE '' END, updated_at=?
            WHERE id=?
            """,
            (status, completed, completed, now(), package_id),
        )
    if completed:
        await wait_for_package_files(package_id)


async def wait_for_package_files(package_id: int) -> int:
    for attempt in range(PACKAGE_SCAN_POLL_ATTEMPTS):
        try:
            detail = await scan_package_async(package_id)
        except Exception as exc:
            log("warn", f"资源包扫描等待失败: package_id={package_id} attempt={attempt + 1} error={str(exc)[:1000]}")
            detail = {}
        files = detail.get("files") or []
        if files:
            return len(files)
        if attempt < PACKAGE_SCAN_POLL_ATTEMPTS - 1:
            await asyncio.sleep(PACKAGE_SCAN_POLL_INTERVAL_SECONDS)
    with connect() as conn:
        conn.execute(
            """
            UPDATE resource_packages
            SET status='downloading',
                match_status='pending',
                last_error='离线下载已提交，暂未发现文件；请稍后手动扫描资源包',
                updated_at=?
            WHERE id=?
            """,
            (now(), package_id),
        )
    return 0


def list_entry_packages(entry_id: int) -> dict[str, Any]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM resource_packages WHERE entry_id=? ORDER BY updated_at DESC, id DESC",
            (entry_id,),
        ).fetchall()
    return {"items": [row_to_dict(row) for row in rows]}


def _package_or_error(package_id: int) -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM resource_packages WHERE id=?", (package_id,)).fetchone()
    if not row:
        raise ValueError("资源包不存在")
    return row_to_dict(row)


def _target_entries_for_package(conn, package: dict[str, Any]) -> list[dict[str, Any]]:
    work_id = int(package.get("work_id") or 0)
    entry_id = int(package.get("entry_id") or 0)
    if work_id > 0:
        rows = conn.execute(
            """
            SELECT id, work_id, display_title, title_cn, title_raw, title_root, media_type, season_number, entry_kind
            FROM entries
            WHERE work_id=?
            ORDER BY season_number ASC, id ASC
            """,
            (work_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT id, work_id, display_title, title_cn, title_raw, title_root, media_type, season_number, entry_kind
            FROM entries
            WHERE id=?
            """,
            (entry_id,),
        ).fetchall()
    return [row_to_dict(row) for row in rows]


def _entry_title(entry: dict[str, Any], season_number: int = 0, title: str = "") -> str:
    if title.strip():
        return clean_name(title.strip())
    root = str(entry.get("title_root") or entry.get("title_cn") or entry.get("display_title") or entry.get("title_raw") or "未命名作品")
    if season_number > 1:
        return clean_name(f"{root} Season {season_number:02d}")
    return clean_name(root)


def _ensure_target_entry(conn, package: dict[str, Any], season_number: int, title: str = "") -> dict[str, Any]:
    season_number = max(1, int(season_number or 1))
    base = conn.execute("SELECT * FROM entries WHERE id=?", (int(package.get("entry_id") or 0),)).fetchone()
    if not base:
        raise ValueError("资源包默认媒体条目不存在")
    base_entry = row_to_dict(base)
    work_id = int(package.get("work_id") or base_entry.get("work_id") or 0)
    if work_id <= 0:
        raise ValueError("资源包缺少作品 work_id")
    existing = conn.execute(
        "SELECT * FROM entries WHERE work_id=? AND season_number=? ORDER BY id ASC LIMIT 1",
        (work_id, season_number),
    ).fetchone()
    if existing:
        return row_to_dict(existing)

    ts = now()
    display_title = _entry_title(base_entry, season_number, title)
    title_root = str(base_entry.get("title_root") or base_entry.get("title_cn") or base_entry.get("display_title") or display_title)
    fingerprint = f"package:{work_id}:season:{season_number}"
    conn.execute(
        """
        INSERT INTO entries
          (work_id, fingerprint, domain_kind, media_type, region, source_provider, metadata_provider,
           external_id, target_library_id, display_title, title_root, season_label, title_raw, title_cn,
           bangumi_id, tmdb_id, bangumi_score, tmdb_score, year, month, season_number, poster_url, summary,
           genres_json, tags_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, 'package', 'manual', '', ?, ?, ?, ?, ?, ?, '', '', 0, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(fingerprint) DO UPDATE SET
          display_title=excluded.display_title,
          title_cn=excluded.title_cn,
          season_number=excluded.season_number,
          poster_url=CASE WHEN entries.poster_url='' THEN excluded.poster_url ELSE entries.poster_url END,
          summary=CASE WHEN entries.summary='' THEN excluded.summary ELSE entries.summary END,
          updated_at=excluded.updated_at
        """,
        (
            work_id,
            fingerprint,
            base_entry.get("domain_kind") or "library",
            base_entry.get("media_type") or package.get("media_type") or "anime",
            base_entry.get("region") or "jp",
            int(base_entry.get("target_library_id") or 0),
            display_title,
            title_root,
            f"Season {season_number:02d}" if season_number > 1 else "",
            display_title,
            display_title,
            int(base_entry.get("year") or 0),
            int(base_entry.get("month") or 0),
            season_number,
            base_entry.get("poster_url") or "",
            base_entry.get("summary") or "",
            base_entry.get("genres_json") or "[]",
            base_entry.get("tags_json") or "[]",
            ts,
            ts,
        ),
    )
    entry = conn.execute("SELECT * FROM entries WHERE fingerprint=?", (fingerprint,)).fetchone()
    if not entry:
        raise ValueError("目标季创建失败")
    entry_id = int(entry["id"] or 0)
    if str(base_entry.get("domain_kind") or "library") == "seasonal":
        conn.execute(
            """
            INSERT INTO seasonal_entries (entry_id, source_type, source_ref, following, sync_enabled, archived, created_at, updated_at)
            VALUES (?, 'package', '', 1, 1, 0, ?, ?)
            ON CONFLICT(entry_id) DO UPDATE SET updated_at=excluded.updated_at
            """,
            (entry_id, ts, ts),
        )
    else:
        conn.execute(
            """
            INSERT INTO library_entries (entry_id, source_type, source_ref, wanted, archived, created_at, updated_at)
            VALUES (?, 'package', '', 1, 0, ?, ?)
            ON CONFLICT(entry_id) DO UPDATE SET wanted=1, updated_at=excluded.updated_at
            """,
            (entry_id, ts, ts),
        )
    log("info", f"资源包自动创建目标季: package_id={package.get('id')} entry_id={entry_id} season={season_number} title={display_title}")
    return row_to_dict(entry)


def create_package_target_entry(package_id: int, payload: ResourcePackageTargetEntryPayload) -> dict[str, Any]:
    package = _package_or_error(package_id)
    with connect() as conn:
        target = _ensure_target_entry(conn, package, int(payload.season_number or 1), payload.title or "")
        if int(package.get("work_id") or 0) <= 0:
            conn.execute("UPDATE resource_packages SET work_id=?, updated_at=? WHERE id=?", (int(target.get("work_id") or 0), now(), package_id))
    return package_detail(package_id)


def package_detail(package_id: int) -> dict[str, Any]:
    package = _package_or_error(package_id)
    with connect() as conn:
        entry = conn.execute("SELECT id, work_id, display_title, title_cn, title_raw, title_root, season_number, media_type FROM entries WHERE id=?", (package["entry_id"],)).fetchone()
        items = conn.execute("SELECT * FROM resource_package_items WHERE package_id=? ORDER BY id", (package_id,)).fetchall()
        files = conn.execute(
            "SELECT * FROM resource_package_files WHERE package_id=? ORDER BY file_kind DESC, target_entry_id ASC, episode_number ASC, file_name ASC",
            (package_id,),
        ).fetchall()
        target_entries = _target_entries_for_package(conn, package)
    return {
        "package": package,
        "entry": row_to_dict(entry),
        "items": [row_to_dict(row) for row in items],
        "files": [row_to_dict(row) for row in files],
        "target_entries": target_entries,
        "active": package_id in _active_package_tasks,
    }


async def scan_package_async(package_id: int) -> dict[str, Any]:
    package = _package_or_error(package_id)
    settings = settings_for_provider(get_settings(), str(package.get("provider") or ""))
    with connect() as conn:
        items = conn.execute("SELECT * FROM resource_package_items WHERE package_id=? ORDER BY id", (package_id,)).fetchall()
    seen = 0
    for item in items:
        item_id = int(item["id"] or 0)
        target_dir = str(item["target_dir"] or "")
        try:
            files = await list_remote_files(settings, target_dir, recursive=True)
        except Exception as exc:
            log("warn", f"资源包目录扫描失败: package_id={package_id} item_id={item_id} error={str(exc)[:1000]}")
            continue
        for file_item in files:
            if file_item.get("is_dir"):
                continue
            _upsert_scanned_file(package_id, item_id, file_item)
            seen += 1
    _refresh_package_counts(package_id, "scanned" if seen else str(package.get("status") or "downloading"))
    return package_detail(package_id)


def _infer_file_target(conn, package: dict[str, Any], item: dict[str, Any], kind: str, file_path: str, name: str) -> dict[str, Any]:
    default_entry_id = int(package.get("entry_id") or 0)
    default_entry = conn.execute("SELECT * FROM entries WHERE id=?", (default_entry_id,)).fetchone()
    if not default_entry:
        return {
            "target_entry_id": 0,
            "inferred_season_number": 0,
            "episode_number": 0,
            "match_confidence": 0,
            "match_note": "默认媒体条目不存在",
        }
    default = row_to_dict(default_entry)
    media_type = str(default.get("media_type") or package.get("media_type") or "anime")
    text = " ".join([file_path, name, str(item.get("source_title") or "")])
    episode_number = _parse_episode_for_package(text, media_type) if kind in {"video", "subtitle"} else 0
    explicit_season = _parse_explicit_season(text)
    multi_package = _package_is_multi_season(str(package.get("title") or ""))
    alias_season = 0
    compact = _compact_text(text)
    for alias, season in _season_aliases_from_title(str(package.get("title") or "")).items():
        if alias and alias in compact:
            alias_season = season
            break

    confidence = 0.0
    note = ""
    target_entry_id = 0
    inferred_season = 0
    if kind == "other":
        return {
            "target_entry_id": 0,
            "inferred_season_number": 0,
            "episode_number": 0,
            "match_confidence": 0,
            "match_note": "非视频或字幕文件，默认忽略",
        }
    if explicit_season > 0:
        inferred_season = explicit_season
        confidence = 0.95
        note = f"文件名识别为第 {explicit_season} 季"
    elif alias_season > 0:
        inferred_season = alias_season
        confidence = 0.88
        note = f"资源标题段识别为第 {alias_season} 季"
    elif _is_special_file(text):
        inferred_season = int(default.get("season_number") or 1)
        confidence = 0.55
        note = "识别为 SP/OVA/番外，需要确认目标季和集数"
    elif multi_package:
        inferred_season = 0
        confidence = 0.45 if episode_number > 0 else 0.2
        note = "合集包含多季，但文件名未明确季号"
    else:
        inferred_season = int(default.get("season_number") or 1)
        confidence = 0.78 if episode_number > 0 else 0.25
        note = "按默认收录季匹配" if episode_number > 0 else "未识别集数"

    if inferred_season > 0 and confidence >= PACKAGE_AUTO_MATCH_THRESHOLD:
        existing = conn.execute(
            "SELECT * FROM entries WHERE work_id=? AND season_number=? ORDER BY id ASC LIMIT 1",
            (int(default.get("work_id") or package.get("work_id") or 0), inferred_season),
        ).fetchone()
        if existing:
            target_entry_id = int(existing["id"] or 0)
        elif confidence >= PACKAGE_AUTO_CREATE_THRESHOLD:
            target = _ensure_target_entry(conn, package, inferred_season)
            target_entry_id = int(target.get("id") or 0)
        else:
            target_entry_id = default_entry_id if inferred_season == int(default.get("season_number") or 1) else 0
    return {
        "target_entry_id": target_entry_id,
        "inferred_season_number": inferred_season,
        "episode_number": episode_number,
        "match_confidence": confidence,
        "match_note": note,
    }


def _upsert_scanned_file(package_id: int, item_id: int, file_item: dict[str, Any]) -> None:
    file_path = str(file_item.get("remote_path") or "")
    name = str(file_item.get("name") or Path(file_path).name)
    if not file_path and not name:
        return
    kind = _file_kind(name)
    role = kind if kind in {"video", "subtitle"} else ""
    ts = now()
    with connect() as conn:
        package_row = conn.execute("SELECT * FROM resource_packages WHERE id=?", (package_id,)).fetchone()
        item_row = conn.execute("SELECT * FROM resource_package_items WHERE id=?", (item_id,)).fetchone()
        if not package_row or not item_row:
            return
        package = row_to_dict(package_row)
        item = row_to_dict(item_row)
        match = _infer_file_target(conn, package, item, kind, file_path, name)
    inferred = int(match.get("episode_number") or 0)
    target_entry_id = int(match.get("target_entry_id") or 0)
    status = "ignored" if kind == "other" else "matched" if inferred > 0 and target_entry_id > 0 and float(match.get("match_confidence") or 0) >= PACKAGE_AUTO_MATCH_THRESHOLD else "pending"
    ignored = 1 if kind == "other" else 0
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO resource_package_files
              (package_id, item_id, file_path, provider_file_id, file_name, file_kind, size,
               target_entry_id, inferred_season_number, inferred_episode_number, episode_number, role,
               match_confidence, match_note, status, ignored, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(package_id, file_path) DO UPDATE SET
              item_id=excluded.item_id,
              provider_file_id=excluded.provider_file_id,
              file_name=excluded.file_name,
              file_kind=excluded.file_kind,
              size=excluded.size,
              target_entry_id=CASE
                WHEN resource_package_files.target_entry_id > 0 THEN resource_package_files.target_entry_id
                ELSE excluded.target_entry_id
              END,
              inferred_season_number=excluded.inferred_season_number,
              inferred_episode_number=excluded.inferred_episode_number,
              episode_number=CASE
                WHEN resource_package_files.episode_number > 0 THEN resource_package_files.episode_number
                ELSE excluded.episode_number
              END,
              role=CASE
                WHEN resource_package_files.role != '' THEN resource_package_files.role
                ELSE excluded.role
              END,
              status=CASE
                WHEN resource_package_files.status IN ('applied', 'skipped', 'ignored') THEN resource_package_files.status
                ELSE excluded.status
              END,
              match_confidence=excluded.match_confidence,
              match_note=excluded.match_note,
              ignored=CASE
                WHEN resource_package_files.ignored=1 THEN 1
                ELSE excluded.ignored
              END,
              updated_at=excluded.updated_at
            """,
            (
                package_id,
                item_id,
                file_path or name,
                remote_file_id(file_item),
                name,
                kind,
                int(file_item.get("size") or 0),
                target_entry_id,
                int(match.get("inferred_season_number") or 0),
                inferred,
                inferred,
                role,
                float(match.get("match_confidence") or 0),
                str(match.get("match_note") or ""),
                status,
                ignored,
                ts,
                ts,
            ),
        )


def _refresh_package_counts(package_id: int, status: str = "") -> None:
    with connect() as conn:
        counts = conn.execute(
            """
            SELECT
              COUNT(*) AS total,
              SUM(CASE WHEN file_kind IN ('video', 'subtitle') AND ignored=0 THEN 1 ELSE 0 END) AS actionable,
              SUM(CASE WHEN status IN ('matched', 'applied', 'skipped') AND ignored=0 AND target_entry_id>0 AND episode_number>0 THEN 1 ELSE 0 END) AS matched,
              SUM(CASE WHEN file_kind IN ('video', 'subtitle') AND ignored=0 AND (target_entry_id<=0 OR episode_number<=0 OR status='pending') THEN 1 ELSE 0 END) AS unmatched
            FROM resource_package_files
            WHERE package_id=?
            """,
            (package_id,),
        ).fetchone()
        total_files = int(counts["total"] or 0) if counts else 0
        actionable_files = int(counts["actionable"] or 0) if counts else 0
        fields = {
            "matched_files": int(counts["matched"] or 0) if counts else 0,
            "unmatched_files": int(counts["unmatched"] or 0) if counts else 0,
            "updated_at": now(),
        }
        if status:
            fields["status"] = status
            if status in {"queued", "downloading"} and total_files == 0:
                fields["match_status"] = "pending"
            elif actionable_files <= 0:
                fields["match_status"] = "needs_review"
            else:
                fields["match_status"] = "ready" if fields["unmatched_files"] == 0 else "needs_review"
        assignments = ", ".join(f"{key}=?" for key in fields)
        conn.execute(f"UPDATE resource_packages SET {assignments} WHERE id=?", [*fields.values(), package_id])


def _ensure_episode(conn, entry_id: int, episode_number: int, ts: str):
    conn.execute(
        """
        INSERT INTO episodes (series_id, entry_id, episode_number, title, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, 'configured', ?, ?)
        ON CONFLICT(series_id, episode_number) DO UPDATE SET
          entry_id=excluded.entry_id,
          updated_at=excluded.updated_at
        """,
        (entry_id, entry_id, episode_number, f"第{episode_number:02d}话", ts, ts),
    )
    return conn.execute(
        "SELECT * FROM episodes WHERE entry_id=? AND episode_number=? ORDER BY id DESC LIMIT 1",
        (entry_id, episode_number),
    ).fetchone()


def _existing_watchable_path(conn, entry_id: int, episode_number: int) -> str:
    row = conn.execute(
        """
        SELECT local_path
        FROM local_assets
        WHERE entry_id=? AND episode_number=? AND status='synced'
        ORDER BY updated_at DESC, id DESC
        LIMIT 1
        """,
        (entry_id, episode_number),
    ).fetchone()
    path = str(row["local_path"] or "") if row else ""
    if path and Path(path).exists():
        return path
    return ""


async def apply_package_match(package_id: int, payload: ResourcePackageApplyPayload) -> dict[str, Any]:
    package = _package_or_error(package_id)
    default_entry_id = int(package.get("entry_id") or 0)
    if default_entry_id <= 0:
        raise ValueError("资源包未绑定媒体条目")
    ts = now()
    with connect() as conn:
        for item in payload.files:
            file_id = int(item.file_id or 0)
            if file_id <= 0:
                continue
            ignored = 1 if item.ignored else 0
            role = str(item.role or "").strip().lower()
            if role not in {"video", "subtitle", ""}:
                role = ""
            target_entry_id = int(item.target_entry_id or 0)
            status = "ignored" if ignored else "matched" if target_entry_id > 0 and int(item.episode_number or 0) > 0 and role in {"video", "subtitle"} else "pending"
            conn.execute(
                """
                UPDATE resource_package_files
                SET target_entry_id=?, episode_number=?, role=?, ignored=?, status=?, updated_at=?
                WHERE id=? AND package_id=?
                """,
                (target_entry_id, int(item.episode_number or 0), role, ignored, status, ts, file_id, package_id),
            )
        files = conn.execute(
            """
            SELECT *
            FROM resource_package_files
            WHERE package_id=? AND ignored=0 AND target_entry_id>0 AND episode_number>0 AND role IN ('video', 'subtitle')
            ORDER BY target_entry_id ASC, role DESC, episode_number ASC, id ASC
            """,
            (package_id,),
        ).fetchall()
    settings = settings_for_provider(get_settings(), str(package.get("provider") or ""))
    applied = 0
    skipped = 0
    subtitles = 0
    touched_entries: set[int] = set()
    entry_cache: dict[int, dict[str, Any]] = {}
    for row in files:
        role = str(row["role"] or "")
        entry_id = int(row["target_entry_id"] or 0)
        episode_number = int(row["episode_number"] or 0)
        source_path = str(row["file_path"] or "")
        provider_file_id = str(row["provider_file_id"] or "")
        suffix = Path(str(row["file_name"] or source_path)).suffix or ".mkv"
        if entry_id <= 0:
            continue
        if entry_id not in entry_cache:
            with connect() as conn:
                entry_row = conn.execute("SELECT * FROM entries WHERE id=?", (entry_id,)).fetchone()
            if not entry_row:
                with connect() as conn:
                    conn.execute(
                        "UPDATE resource_package_files SET status='pending', match_note='目标作品不存在', updated_at=? WHERE id=?",
                        (ts, int(row["id"] or 0)),
                    )
                continue
            entry_cache[entry_id] = row_to_dict(entry_row)
        entry = entry_cache[entry_id]
        with connect() as conn:
            episode = _ensure_episode(conn, entry_id, episode_number, ts)
            existing_path = _existing_watchable_path(conn, entry_id, episode_number)
        if role == "video":
            if existing_path:
                final_path = existing_path
                skipped += 1
                status = "skipped"
            else:
                final_path = expected_local_episode_path(dict(entry), episode_number, suffix, get_settings())
                if not final_path:
                    continue
                await download_to_local(settings, provider_file_id, source_path, final_path)
                status = "applied"
                applied += 1
            with connect() as conn:
                episode = _ensure_episode(conn, entry_id, episode_number, ts)
                episode_id = int(episode["id"] or 0)
                digest_source = f"package:{package_id}:file:{int(row['id'] or 0)}"
                asset_id = synthetic_task_id(digest_source)
                conn.execute("UPDATE episode_resources SET selected=0 WHERE entry_id=? AND episode_number=?", (entry_id, episode_number))
                conn.execute(
                    """
                    INSERT INTO episode_resources
                      (entry_id, episode_id, episode_number, source_type, source_ref, title,
                       selected, downloaded, local_path, status, created_at, updated_at)
                    VALUES (?, ?, ?, 'package', ?, ?, 1, 1, ?, 'downloaded', ?, ?)
                    ON CONFLICT(entry_id, episode_number, source_type, source_ref) DO UPDATE SET
                      episode_id=excluded.episode_id,
                      title=excluded.title,
                      selected=1,
                      downloaded=1,
                      local_path=excluded.local_path,
                      status='downloaded',
                      updated_at=excluded.updated_at
                    """,
                    (entry_id, episode_id, episode_number, source_path, row["file_name"] or source_path, final_path, ts, ts),
                )
                conn.execute(
                    """
                    INSERT INTO local_assets
                      (download_artifact_id, release_id, series_id, entry_id, episode_number, local_path,
                       nfo_status, status, created_at, updated_at)
                    VALUES (?, 0, ?, ?, ?, ?, 'pending', 'synced', ?, ?)
                    ON CONFLICT(download_artifact_id) DO UPDATE SET
                      local_path=excluded.local_path,
                      status='synced',
                      updated_at=excluded.updated_at
                    """,
                    (asset_id, entry_id, entry_id, episode_number, final_path, ts, ts),
                )
                conn.execute(
                    """
                    UPDATE episodes
                    SET local_path=?, watchable=1, status='downloaded', source_type='package', updated_at=?
                    WHERE id=?
                    """,
                    (final_path, ts, episode_id),
                )
                upsert_calendar_entry(conn, entry_id, episode_number, ts, True)
                conn.execute(
                    "UPDATE resource_package_files SET status=?, final_path=?, updated_at=? WHERE id=?",
                    (status, final_path, ts, int(row["id"] or 0)),
                )
            touched_entries.add(entry_id)
        elif role == "subtitle":
            with connect() as conn:
                episode = _ensure_episode(conn, entry_id, episode_number, ts)
                video_path = str(episode["local_path"] or "") or _existing_watchable_path(conn, entry_id, episode_number)
            if not video_path:
                skipped += 1
                with connect() as conn:
                    conn.execute(
                        "UPDATE resource_package_files SET status='pending', updated_at=? WHERE id=?",
                        (ts, int(row["id"] or 0)),
                    )
                continue
            final_path = str(Path(video_path).with_suffix(suffix))
            await download_to_local(settings, provider_file_id, source_path, final_path)
            with connect() as conn:
                episode = _ensure_episode(conn, entry_id, episode_number, ts)
                episode_id = int(episode["id"] or 0)
                conn.execute("UPDATE episode_subtitles SET selected=0 WHERE entry_id=? AND episode_number=?", (entry_id, episode_number))
                conn.execute(
                    """
                    INSERT INTO episode_subtitles
                      (episode_id, entry_id, episode_number, language, subtitle_format, subtitle_path,
                       file_name, embedded, selected, created_at, updated_at)
                    VALUES (?, ?, ?, '', 'external', ?, ?, 0, 1, ?, ?)
                    """,
                    (episode_id, entry_id, episode_number, final_path, Path(final_path).name, ts, ts),
                )
                conn.execute("UPDATE episodes SET subtitle_path=?, updated_at=? WHERE id=?", (final_path, ts, episode_id))
                conn.execute(
                    "UPDATE resource_package_files SET status='applied', final_path=?, updated_at=? WHERE id=?",
                    (final_path, ts, int(row["id"] or 0)),
                )
            subtitles += 1
            touched_entries.add(entry_id)
    for entry_id in sorted(touched_entries):
        generate_jellyfin_nfo_for_entry(entry_id, get_settings())
    with connect() as conn:
        unresolved = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM resource_package_files
            WHERE package_id=?
              AND file_kind IN ('video', 'subtitle')
              AND ignored=0
              AND (target_entry_id<=0 OR episode_number<=0 OR status='pending')
            """,
            (package_id,),
        ).fetchone()
        has_unresolved = int(unresolved["c"] or 0) > 0 if unresolved else False
        conn.execute(
            """
            UPDATE resource_packages
            SET status=?, match_status=?, updated_at=?
            WHERE id=?
            """,
            ("scanned" if has_unresolved else "organized", "needs_review" if has_unresolved else "applied", now(), package_id),
        )
    _refresh_package_counts(package_id)
    detail = package_detail(package_id)
    detail["result"] = {"applied": applied, "skipped": skipped, "subtitles": subtitles}
    return detail


def list_resource_package_tasks(limit: int = 100) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT rp.*, e.display_title, e.title_cn, e.title_root, e.media_type AS entry_media_type
            FROM resource_packages rp
            LEFT JOIN entries e ON e.id=rp.entry_id
            WHERE rp.status NOT IN ('cleaned')
            ORDER BY rp.updated_at DESC, rp.id DESC
            LIMIT ?
            """,
            (max(1, int(limit or 100)),),
        ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        item = row_to_dict(row)
        status = str(item.get("status") or "")
        total = max(1, int(item.get("total_resources") or 0))
        completed = int(item.get("completed_resources") or 0)
        progress = min(100, max(0, round((completed / total) * 70)))
        task_status = {
            "queued": "pending",
            "downloading": "running",
            "scanned": "pending",
            "organized": "completed",
            "failed": "failed",
        }.get(status, status or "pending")
        if status == "scanned":
            progress = 85
        elif status == "organized":
            progress = 100
        title = str(item.get("display_title") or item.get("title_cn") or item.get("title_root") or item.get("title") or "资源包")
        message = str(item.get("last_error") or "")
        if not message:
            message = f"资源包 {status or 'pending'} · 已提交 {completed}/{total} 个种子"
        items.append(
            {
                "id": f"resource_package:{int(item.get('id') or 0)}",
                "raw_id": int(item.get("id") or 0),
                "type": "download",
                "type_name": "资源包下载",
                "title": title,
                "status": task_status,
                "status_text": {
                    "pending": "等待中",
                    "running": "下载中",
                    "completed": "已完成",
                    "failed": "失败",
                }.get(task_status, task_status),
                "progress": progress,
                "message": message,
                "updated_at": item.get("updated_at") or "",
                "source": "resource_package",
                "entry_id": int(item.get("entry_id") or 0),
                "episode_number": "",
            }
        )
    return items


async def cleanup_package_async(package_id: int) -> dict[str, Any]:
    package = _package_or_error(package_id)
    target_dir = str(package.get("target_dir") or "")
    settings = settings_for_provider(get_settings(), str(package.get("provider") or ""))
    backend = backend_key(settings)
    removed = False
    if backend in {"aria2", "qb"}:
        target = Path(target_dir).resolve()
        root = (Path(MEDIA_ROOT) / ".anitrack-staging").resolve()
        if target == root or root not in target.parents:
            raise ValueError("资源包清理路径不在 staging 目录内")
        if target.exists():
            shutil.rmtree(target)
            removed = True
    elif backend == "local":
        target = local_downloader_service.safe_path(settings, target_dir)
        root = local_downloader_service.safe_path(settings, "/.anitrack-staging")
        if target == root or root not in target.parents:
            raise ValueError("资源包清理路径不在本地下载器 staging 目录内")
        if target.exists():
            shutil.rmtree(target)
            removed = True
    elif backend == "rclone":
        await rclone_service.run_rclone(settings, ["purge", rclone_service.remote_path(settings, target_dir)], timeout=300)
        removed = True
    else:
        raise ValueError("当前下载器暂不支持自动清理资源包临时目录")
    with connect() as conn:
        conn.execute(
            "UPDATE resource_packages SET status='cleaned', match_status='cleaned', updated_at=? WHERE id=?",
            (now(), package_id),
        )
    return {"status": "cleaned", "removed": removed, "package": package_detail(package_id)["package"]}
