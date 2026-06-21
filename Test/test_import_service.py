from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("APP_DATA_DIR", tempfile.mkdtemp(prefix="autoanime-import-test-"))

from app.import_service import preview_local_import, preview_torrent_import


class ImportServiceTests(unittest.TestCase):
    def test_local_preview_parses_video_files(self) -> None:
        with tempfile.TemporaryDirectory(prefix="autoanime-local-import-") as tmp:
            root = Path(tmp)
            video = root / "[LoliHouse] Test Anime - 03 [WebRip 1080p][简繁内封字幕].mkv"
            video.write_bytes(b"fake")
            (root / "ignore.txt").write_text("skip", encoding="utf-8")

            items = preview_local_import(str(root))

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["episode_number"], 3)
        self.assertEqual(items[0]["subtitle_format"], "embedded")
        self.assertEqual(items[0]["resolution"], "1080p")

    def test_torrent_preview_parses_release_title(self) -> None:
        item = preview_torrent_import(
            title="[ANi] Test Anime - 12 [1080p][简体][外挂字幕]",
            magnet="magnet:?xt=urn:btih:abc",
        )

        self.assertEqual(item["source_type"], "torrent")
        self.assertEqual(item["episode_number"], 12)
        self.assertEqual(item["subtitle_group"], "ANi")
        self.assertEqual(item["subtitle_format"], "external")


if __name__ == "__main__":
    unittest.main()
