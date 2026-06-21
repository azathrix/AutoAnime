from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("APP_DATA_DIR", tempfile.mkdtemp(prefix="autoanime-import-commit-test-"))

from app.database import connect
from app.db import init_db
from app.episode_jobs import build_episode_jobs
from app.import_service import commit_local_import, commit_torrent_import, preview_local_import, preview_torrent_import


class ImportCommitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        init_db()

    def test_local_commit_creates_library_entry_and_local_asset(self) -> None:
        with tempfile.TemporaryDirectory(prefix="autoanime-local-commit-") as tmp:
            video = Path(tmp) / "[LoliHouse] Commit Local Anime - 05 [1080p][简繁内封字幕].mkv"
            video.write_bytes(b"fake")
            candidate = preview_local_import(str(video))[0]
            result = commit_local_import([candidate], {"title_cn": "Commit Local Anime", "year": 2026})

        self.assertEqual(result["imported_count"], 1)
        imported = result["imported"][0]
        self.assertGreater(imported["entry_id"], 0)
        self.assertGreater(imported["release_id"], 0)
        self.assertGreater(imported["local_asset_id"], 0)

        with connect() as conn:
            entry = conn.execute("SELECT domain_kind, display_title FROM entries WHERE id=?", (imported["entry_id"],)).fetchone()
            local_asset = conn.execute("SELECT status FROM local_assets WHERE id=?", (imported["local_asset_id"],)).fetchone()
        self.assertEqual(entry["domain_kind"], "library")
        self.assertEqual(entry["display_title"], "Commit Local Anime")
        self.assertEqual(local_asset["status"], "synced")

        job = next(item for item in build_episode_jobs({}) if item["release_id"] == imported["release_id"])
        self.assertEqual(job["stage"], "nfo")
        self.assertEqual(job["status"], "pending")

    def test_torrent_commit_creates_selected_release(self) -> None:
        candidate = preview_torrent_import(
            title="[ANi] Commit Torrent Anime - 08 [1080p][简体][外挂字幕]",
            magnet="magnet:?xt=urn:btih:commit-torrent",
        )
        imported = commit_torrent_import(candidate, {"title_cn": "Commit Torrent Anime", "year": 2026})

        self.assertGreater(imported["entry_id"], 0)
        self.assertGreater(imported["release_id"], 0)
        with connect() as conn:
            release = conn.execute("SELECT selected, magnet, subtitle_format FROM releases WHERE id=?", (imported["release_id"],)).fetchone()
        self.assertEqual(release["selected"], 1)
        self.assertEqual(release["magnet"], "magnet:?xt=urn:btih:commit-torrent")
        self.assertEqual(release["subtitle_format"], "external")


if __name__ == "__main__":
    unittest.main()
