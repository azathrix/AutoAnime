from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("APP_DATA_DIR", tempfile.mkdtemp(prefix="autoanime-media-contract-test-"))

from app.db import init_db, save_settings
from app.database import connect
from fastapi.testclient import TestClient

from app.main import MediaCreatePayload, app, create_media_entry, dashboard_data, settings_response


class MediaContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        init_db()

    def test_settings_response_exposes_only_new_settings_contract(self) -> None:
        save_settings(
            {
                "auto_download_unique": "false",
                "auto_download_by_priority": "false",
                "download_backend": "api",
                "local_library_root": "/legacy",
                "nfo_output_root": "/legacy-nfo",
                "downloaders_json": json.dumps(
                    [
                        {
                            "id": "api-main",
                            "name": "PikPak API",
                            "type": "pikpak_api",
                            "remote_dir": "/Temp",
                            "auth_mode": "token",
                            "access_token": "access",
                            "refresh_token": "refresh",
                            "enabled": True,
                        }
                    ]
                ),
            }
        )

        payload = settings_response()

        for key in [
            "auto_download_unique",
            "auto_download_by_priority",
            "download_backend",
            "local_library_root",
            "nfo_output_root",
        ]:
            self.assertNotIn(key, payload)
        self.assertEqual(payload["downloaders"][0]["type"], "pikpak_api")
        self.assertEqual(payload["downloaders"][0]["remote_dir"], "/Temp")

    def test_create_media_entry_returns_episode_resource_contract(self) -> None:
        detail = create_media_entry(
            "movie",
            MediaCreatePayload(
                mode="add",
                title="Contract Movie",
                tmdb_id="tmdb-contract",
                year=2026,
                month=4,
                episode_number=1,
                resource_title="Contract Movie 2026 1080p",
                source_ref="magnet:?xt=urn:btih:contractmovie",
                subtitle_group="Manual",
                resolution="1080p",
                language="简体",
                subtitle_format="embedded",
            ),
        )

        self.assertEqual(detail["entry"]["media_type"], "movie")
        self.assertEqual(detail["entry"]["month"], 4)
        self.assertEqual(len(detail["episode_resources"]), 1)
        self.assertEqual(detail["episode_resources"][0]["selected"], 1)
        self.assertGreater(int(detail["episode_resources"][0]["release_id"]), 0)
        self.assertEqual(detail["episode_resources"][0]["magnet"], "magnet:?xt=urn:btih:contractmovie")
        self.assertGreater(int(detail["download_run_id"]), 0)
        with connect() as conn:
            release = conn.execute(
                "SELECT selected, magnet FROM releases WHERE id=?",
                (int(detail["episode_resources"][0]["release_id"]),),
            ).fetchone()
        self.assertIsNotNone(release)
        self.assertEqual(release["selected"], 1)
        self.assertEqual(release["magnet"], "magnet:?xt=urn:btih:contractmovie")
        self.assertNotIn("releases", detail)
        self.assertNotIn("download_artifacts", detail)
        self.assertNotIn("local_assets", detail)
        self.assertNotIn("tasks", detail)
        for key in ["auto_download", "selected_group", "selected_resolution", "backfill_mode"]:
            self.assertNotIn(key, detail["entry"])

    def test_media_lists_do_not_expose_card_status_summaries(self) -> None:
        detail = create_media_entry(
            "movie",
            MediaCreatePayload(
                mode="add",
                title="Slim Card Movie",
                tmdb_id="tmdb-slim-card",
                year=2026,
                month=7,
                episode_number=1,
                resource_title="Slim Card Movie 2026 1080p",
                source_ref="",
            ),
        )

        payload = dashboard_data()
        row = next(item for item in payload["library_items"] if int(item["id"]) == int(detail["entry"]["id"]))
        self.assertEqual(row["month"], 7)
        for key in [
            "watch_status",
            "watch_status_label",
            "status_summary",
            "status_category",
            "status_level",
            "needs_attention",
            "has_failed_task",
            "auto_download",
            "selected_group",
            "selected_resolution",
            "backfill_mode",
        ]:
            self.assertNotIn(key, row)

    def test_legacy_entry_detail_routes_are_removed(self) -> None:
        client = TestClient(app)

        self.assertEqual(client.get("/api/seasonal/1").status_code, 404)
        self.assertEqual(client.put("/api/seasonal/1", json={}).status_code, 404)
        self.assertEqual(client.get("/api/library/1").status_code, 404)
        self.assertEqual(client.put("/api/library/1", json={}).status_code, 404)
        self.assertEqual(client.post("/api/import/local/preview", json={}).status_code, 404)
        self.assertEqual(client.post("/api/import/torrent/preview", json={}).status_code, 404)
        self.assertEqual(client.post("/api/import/local/commit", json={}).status_code, 404)
        self.assertEqual(client.post("/api/import/torrent/commit", json={}).status_code, 404)
        self.assertEqual(client.post("/api/library/import", json={}).status_code, 404)

    def test_media_detail_route_is_the_entry_detail_contract(self) -> None:
        detail = create_media_entry(
            "tv",
            MediaCreatePayload(
                mode="add",
                title="Contract TV",
                tmdb_id="tmdb-contract-tv",
                year=2026,
                month=10,
                episode_number=2,
                resource_title="Contract TV S01E02 1080p",
                source_ref="magnet:?xt=urn:btih:contracttv",
            ),
        )
        client = TestClient(app)

        response = client.get(f"/api/media/tv/{int(detail['entry']['id'])}")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["entry"]["media_type"], "tv")
        self.assertEqual(payload["entry"]["month"], 10)
        self.assertEqual(payload["episode_resources"][0]["episode_number"], 2)
        self.assertEqual(client.get(f"/api/media/movie/{int(detail['entry']['id'])}").status_code, 404)
        self.assertEqual(client.get("/api/media/anime/999999").status_code, 404)
        self.assertEqual(client.get("/api/entries/999999/episodes").status_code, 404)


if __name__ == "__main__":
    unittest.main()
