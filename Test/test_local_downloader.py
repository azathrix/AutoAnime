from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("APP_DATA_DIR", tempfile.mkdtemp(prefix="autoanime-local-downloader-test-"))

from app.database import connect
from app.db import init_db, now, save_settings
from app.parser import ParsedRelease
from app.pipeline_models import ProcessorContext
from app.processors.download import process_download
from app.scanner import upsert_release


class LocalDownloaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        init_db()

    def test_local_downloader_completes_manual_torrent_without_bangumi(self) -> None:
        with tempfile.TemporaryDirectory(prefix="autoanime-local-provider-") as tmp:
            remote_root = Path(tmp) / "remote"
            local_root = Path(tmp) / "library"
            save_settings(
                {
                    "download_backend": "local",
                    "downloaders_json": json.dumps(
                        [
                            {
                                "id": "local-test",
                                "name": "Local Test",
                                "type": "local",
                                "enabled": True,
                                "remote_dir": "/Downloads",
                            }
                        ]
                    ),
                    "local_downloader_root": str(remote_root),
                    "library_root": "/Downloads",
                }
            )
            with connect() as conn:
                conn.execute("UPDATE media_libraries SET root_path=?", (str(local_root),))
            _, entry_id, release_id = upsert_release(
                ParsedRelease(
                    guid="local-provider",
                    title="[Manual] Local Provider Anime - 07 [1080p][简体]",
                    series_title="Local Provider Anime",
                    episode_number=7,
                    subtitle_group="Manual",
                    resolution="1080p",
                    language="简体",
                    subtitle_format="",
                    bangumi_id="",
                    year=2026,
                    torrent_url="",
                    magnet="magnet:?xt=urn:btih:local-provider",
                    page_url="",
                    mikan_bangumi_id="",
                    published_at=now(),
                ),
                {"title_cn": "Local Provider Anime", "year": 2026},
            )

            first = asyncio.run(process_download(self._context(release_id, 1), {"release_id": release_id, "entry_id": entry_id}))
            self.assertEqual(first.status, "failed_retryable")

            second = asyncio.run(process_download(self._context(release_id, 2), {"release_id": release_id, "entry_id": entry_id}))
            self.assertEqual(second.status, "success")

            with connect() as conn:
                entry = conn.execute("SELECT bangumi_id FROM entries WHERE id=?", (entry_id,)).fetchone()
                asset = conn.execute("SELECT local_path, status FROM local_assets WHERE release_id=?", (release_id,)).fetchone()
            self.assertEqual(entry["bangumi_id"], "")
            self.assertEqual(asset["status"], "synced")
            self.assertTrue(Path(asset["local_path"]).exists())

    @staticmethod
    def _context(release_id: int, attempts: int) -> ProcessorContext:
        return ProcessorContext(
            task_id=release_id + attempts,
            pipeline_id=1,
            run_id=1,
            step_id=1,
            step_key="download",
            processor_key="download",
            domain_kind="library",
            subject_type="release",
            subject_id=release_id,
            attempts=attempts,
        )


if __name__ == "__main__":
    unittest.main()
