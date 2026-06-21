from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))
os.environ["APP_DATA_DIR"] = tempfile.mkdtemp(prefix="autoanime-test-")

from app.database import connect
from app.db import init_db, now
from app.episode_jobs import build_episode_jobs
from app.parser import ParsedRelease, parse_subtitle_format
from app.scanner import mark_selected_releases, resolve_entry_choice, upsert_release


def release(
    guid: str,
    *,
    group: str = "LoliHouse",
    title: str = "[LoliHouse] Test Anime - 01 [WebRip 1080p HEVC-10bit AAC][简繁内封字幕]",
    episode: int = 1,
    bangumi_id: str = "100001",
) -> ParsedRelease:
    return ParsedRelease(
        guid=guid,
        title=title,
        series_title="Test Anime",
        episode_number=episode,
        subtitle_group=group,
        resolution="1080p",
        language="简繁",
        subtitle_format=parse_subtitle_format(title),
        bangumi_id=bangumi_id,
        year=2026,
        torrent_url=f"https://example.test/{guid}.torrent",
        magnet=f"magnet:?xt=urn:btih:{guid}",
        page_url=f"https://example.test/{guid}",
        mikan_bangumi_id="200001",
        published_at=now(),
    )


class EpisodeJobTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        init_db()

    def test_subtitle_format_parser(self) -> None:
        self.assertEqual(parse_subtitle_format("[喵萌] Foo [简繁内封字幕]"), "embedded")
        self.assertEqual(parse_subtitle_format("[Group] Foo [外挂字幕][ASS]"), "external")
        self.assertEqual(parse_subtitle_format("[Group] Foo [1080p]"), "")

    def test_release_selection_prefers_priority_group(self) -> None:
        _, entry_id, _ = upsert_release(
            release(
                "select-ani",
                group="ANi",
                title="[ANi] Test Anime - 01 [1080p][简繁内嵌]",
                bangumi_id="100101",
            ),
            {"title_cn": "Test Anime", "year": 2026},
        )
        _, _, loli_id = upsert_release(
            release(
                "select-loli",
                group="LoliHouse",
                title="[LoliHouse] Test Anime - 01 [1080p][简繁内封字幕]",
                bangumi_id="100101",
            ),
            {"title_cn": "Test Anime", "year": 2026},
        )
        ids, choice = resolve_entry_choice(entry_id, {"auto_download_unique": "true", "auto_download_by_priority": "true", "subtitle_priority": "LoliHouse\nANi", "resolution_priority": "1080p", "language_priority": "简繁\n简体", "secondary_language_priority": "繁体"})
        self.assertIn(loli_id, ids)
        self.assertEqual(choice["selected_group"], "LoliHouse")
        with connect() as conn:
            row = conn.execute("SELECT subtitle_group FROM releases WHERE id=?", (loli_id,)).fetchone()
        self.assertEqual(row["subtitle_group"], "LoliHouse")

    def test_episode_job_progresses_from_selected_release_to_ready_local(self) -> None:
        _, entry_id, release_id = upsert_release(
            release("job-ready", bangumi_id="100201"),
            {"title_cn": "Test Anime", "year": 2026},
        )
        mark_selected_releases(entry_id, [release_id])

        pending_job = next(item for item in build_episode_jobs({}) if item["release_id"] == release_id)
        self.assertEqual(pending_job["stage"], "download")
        self.assertEqual(pending_job["status"], "pending")

        ts = now()
        with connect() as conn:
            conn.execute(
                """
                INSERT INTO download_artifacts
                  (task_id, release_id, series_id, entry_id, episode_number, provider,
                   provider_file_id, remote_path, artifact_name, status, created_at, updated_at)
                SELECT 9001, id, series_id, entry_id, episode_number, 'pikpak',
                  'file-9001', '/Temp/Test Anime - 01.mkv', 'Test Anime - 01.mkv',
                  'available', ?, ?
                FROM releases
                WHERE id=?
                """,
                (ts, ts, release_id),
            )
            artifact_id = int(conn.execute("SELECT id FROM download_artifacts WHERE release_id=?", (release_id,)).fetchone()["id"])
            conn.execute(
                """
                INSERT INTO local_assets
                  (download_artifact_id, release_id, series_id, entry_id, episode_number,
                   local_path, nfo_status, status, created_at, updated_at)
                SELECT ?, id, series_id, entry_id, episode_number,
                  '/media/Test Anime/Season 01/Test Anime - S01E01.mkv',
                  'generated', 'synced', ?, ?
                FROM releases
                WHERE id=?
                """,
                (artifact_id, ts, ts, release_id),
            )

        ready_job = next(item for item in build_episode_jobs({}) if item["release_id"] == release_id)
        self.assertEqual(ready_job["stage"], "done")
        self.assertEqual(ready_job["status"], "completed")

    def test_stale_runtime_task_does_not_move_episode_job_backwards(self) -> None:
        _, entry_id, release_id = upsert_release(
            release("job-stale-runtime", bangumi_id="100301"),
            {"title_cn": "Test Anime", "year": 2026},
        )
        mark_selected_releases(entry_id, [release_id])
        snapshot = {
            "queue_details": {
                "metadata": {
                    "items": [
                        {
                            "id": 7001,
                            "processor_key": "metadata",
                            "status": "waiting",
                            "entry_id": entry_id,
                            "release_id": release_id,
                            "episode_number": 1,
                            "last_error": "temporary bangumi error",
                        }
                    ]
                }
            }
        }

        job = next(item for item in build_episode_jobs(snapshot) if item["release_id"] == release_id)
        self.assertEqual(job["stage"], "download")
        self.assertEqual(job["status"], "pending")
        self.assertNotEqual(job.get("runtime_task_id"), 7001)


if __name__ == "__main__":
    unittest.main()
