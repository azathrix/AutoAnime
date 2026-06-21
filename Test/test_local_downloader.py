from __future__ import annotations

import asyncio
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
from app.db import init_db, save_settings
from app.import_service import commit_torrent_import, preview_torrent_import
from app.pipeline_models import ProcessorContext
from app.processors.download import process_download


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
                    "local_downloader_root": str(remote_root),
                    "local_library_root": str(local_root),
                    "library_root": "/Downloads",
                }
            )
            with connect() as conn:
                conn.execute(
                    "UPDATE media_libraries SET root_path=? WHERE key='anime_library'",
                    (str(local_root),),
                )
            candidate = preview_torrent_import(
                title="[Manual] Local Provider Anime - 07 [1080p][简体]",
                magnet="magnet:?xt=urn:btih:local-provider",
            )
            imported = commit_torrent_import(candidate, {"title_cn": "Local Provider Anime", "year": 2026})
            release_id = int(imported["release_id"])
            entry_id = int(imported["entry_id"])

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
