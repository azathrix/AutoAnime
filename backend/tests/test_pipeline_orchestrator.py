from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))


class PipelineOrchestratorTest(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        os.environ["APP_DATA_DIR"] = cls.tmp.name

        from app.db import init_db

        init_db()

    @classmethod
    def tearDownClass(cls) -> None:
        from app.database import engine

        engine.dispose()
        cls.tmp.cleanup()

    def setUp(self) -> None:
        from app.database import connect
        from app.processor_registry import clear_processors

        clear_processors()
        with connect() as conn:
            conn.execute("DELETE FROM processor_events")
            conn.execute("DELETE FROM processor_tasks")
            conn.execute("DELETE FROM pipeline_runs")

    async def test_success_result_enqueues_next_step_and_completes_run(self) -> None:
        from app.database import connect
        from app.pipeline_models import ProcessorResult
        from app.pipeline_orchestrator import run_ready_tasks, start_pipeline
        from app.processor_registry import register_processor

        async def nfo_generate(context, payload):
            self.assertEqual(context.step_key, "nfo_generate")
            self.assertEqual(payload["source"], "mikan")
            return ProcessorResult.success(
                "nfo ok",
                data={"release_count": 2},
                next_payload={"candidate_ids": [10, 11]},
            )

        async def local_presence(context, payload):
            self.assertEqual(context.step_key, "local_presence")
            self.assertEqual(payload["candidate_ids"], [10, 11])
            return ProcessorResult.success("presence ok", data={"checked": 2})

        register_processor("nfo", nfo_generate)
        register_processor("local_presence", local_presence)

        run_id = start_pipeline(
            "seasonal_mikan_tracking",
            trigger_source="test",
            first_step_key="nfo_generate",
            subject_type="rss_source",
            subject_id=1,
            payload={"source": "mikan"},
        )
        self.assertGreater(run_id, 0)

        processed = await run_ready_tasks(limit=2)
        self.assertEqual(processed, 2)

        with connect() as conn:
            tasks = conn.execute(
                """
                SELECT step_id, processor_key, status, result_json
                FROM processor_tasks
                WHERE run_id=?
                ORDER BY id ASC
                """,
                (run_id,),
            ).fetchall()
            self.assertEqual([row["processor_key"] for row in tasks], ["nfo", "local_presence"])
            self.assertEqual([row["status"] for row in tasks], ["completed", "completed"])

            run = conn.execute("SELECT status, progress FROM pipeline_runs WHERE id=?", (run_id,)).fetchone()
            self.assertEqual(run["status"], "completed")
            self.assertEqual(run["progress"], 100)

            event_count = conn.execute(
                "SELECT COUNT(*) AS count FROM processor_events WHERE run_id=?",
                (run_id,),
            ).fetchone()["count"]
            self.assertEqual(event_count, 2)

    async def test_retryable_result_keeps_task_pending_without_advancing(self) -> None:
        from app.database import connect
        from app.pipeline_models import ProcessorResult
        from app.pipeline_orchestrator import retry_after_seconds, run_ready_tasks, start_pipeline
        from app.processor_registry import register_processor

        async def nfo_generate(_context, _payload):
            return ProcessorResult.retryable("network cooldown", retry_after_seconds(60))

        register_processor("nfo", nfo_generate)

        run_id = start_pipeline(
            "seasonal_mikan_tracking",
            trigger_source="test",
            first_step_key="nfo_generate",
            subject_type="rss_source",
            subject_id=2,
            payload={"source": "mikan"},
        )

        processed = await run_ready_tasks(limit=5)
        self.assertEqual(processed, 1)

        with connect() as conn:
            tasks = conn.execute(
                "SELECT processor_key, status, retry_after FROM processor_tasks WHERE run_id=? ORDER BY id ASC",
                (run_id,),
            ).fetchall()
            self.assertEqual(len(tasks), 1)
            self.assertEqual(tasks[0]["processor_key"], "nfo")
            self.assertEqual(tasks[0]["status"], "pending")
            self.assertTrue(tasks[0]["retry_after"])

            run = conn.execute("SELECT status FROM pipeline_runs WHERE id=?", (run_id,)).fetchone()
            self.assertEqual(run["status"], "running")

    async def test_rss_mikan_metadata_pipeline_persists_entry_and_release(self) -> None:
        from app.database import connect
        from app.parser import ParsedRelease
        from app.pipeline_orchestrator import run_ready_tasks, start_pipeline
        from app.processors import register_builtin_processors

        register_builtin_processors()
        releases = [
            ParsedRelease(
                guid="guid-1",
                title="[GroupA] Test Anime - 01 [1080p][简体]",
                series_title="Test Anime",
                episode_number=1,
                subtitle_group="GroupA",
                resolution="1080p",
                language="简体",
                bangumi_id="",
                year=2026,
                torrent_url="https://example.test/1.torrent",
                magnet="magnet:?xt=urn:btih:1",
                page_url="https://mikanani.me/Home/Episode/guid-1",
                mikan_bangumi_id="",
                published_at="2026-06-18 10:00",
            ),
            ParsedRelease(
                guid="guid-2",
                title="[GroupA] Test Anime - 02 [1080p][简体]",
                series_title="Test Anime",
                episode_number=2,
                subtitle_group="GroupA",
                resolution="1080p",
                language="简体",
                bangumi_id="",
                year=2026,
                torrent_url="https://example.test/2.torrent",
                magnet="magnet:?xt=urn:btih:2",
                page_url="https://mikanani.me/Home/Episode/guid-2",
                mikan_bangumi_id="",
                published_at="2026-06-18 10:30",
            ),
        ]

        metadata = {"title_cn": "测试动画", "poster_url": "", "summary": "summary", "year": 2026}
        with (
            patch("app.processors.rss.fetch_entries", return_value=releases),
            patch("app.processors.mikan.fetch_mikan_match", return_value=("123", "456")),
            patch("app.processors.metadata.fetch_bangumi_metadata", return_value=metadata),
        ):
            run_id = start_pipeline(
                "seasonal_mikan_tracking",
                trigger_source="test",
                first_step_key="rss_fetch",
                subject_type="rss_source",
                subject_id=1,
                payload={"rss_url": "https://example.test/rss.xml"},
            )
            processed = await run_ready_tasks(limit=12)

        self.assertEqual(processed, 12)
        with connect() as conn:
            task_rows = conn.execute(
                """
                SELECT processor_key, status
                FROM processor_tasks
                WHERE run_id=?
                ORDER BY id ASC
                """,
                (run_id,),
            ).fetchall()
            self.assertEqual(
                [row["processor_key"] for row in task_rows[:11]],
                [
                    "rss_fetch",
                    "rss_candidate_persist",
                    "rss_candidate_persist",
                    "mikan_match",
                    "mikan_match",
                    "metadata",
                    "metadata",
                    "seasonal_merge",
                    "backfill",
                    "selection",
                    "cloud_presence",
                ],
            )
            self.assertTrue(all(row["status"] == "completed" for row in task_rows[:10]))

            candidates = conn.execute(
                "SELECT guid, title, episode_number, bangumi_id, mikan_bangumi_id FROM rss_candidates ORDER BY guid"
            ).fetchall()
            self.assertEqual([row["guid"] for row in candidates], ["guid-1", "guid-2"])
            self.assertEqual(candidates[0]["episode_number"], 1)
            self.assertEqual(candidates[1]["bangumi_id"], "123")
            self.assertEqual(candidates[1]["mikan_bangumi_id"], "456")

            entries = conn.execute("SELECT id, title_cn, bangumi_id FROM entries ORDER BY id").fetchall()
            releases_rows = conn.execute("SELECT guid, entry_id, episode_number FROM releases ORDER BY guid").fetchall()
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["title_cn"], "测试动画")
            self.assertEqual(entries[0]["bangumi_id"], "123")
            self.assertEqual([row["guid"] for row in releases_rows], ["guid-1", "guid-2"])
            self.assertTrue(all(row["entry_id"] == entries[0]["id"] for row in releases_rows))

            selected_count = conn.execute("SELECT COUNT(*) AS count FROM releases WHERE selected=1").fetchone()["count"]
            cloud_presence_tasks = conn.execute("SELECT release_id, status FROM cloud_presence_tasks ORDER BY release_id").fetchall()
            self.assertEqual(selected_count, 2)
            self.assertEqual(len(cloud_presence_tasks), 2)
            self.assertTrue(all(row["status"] == "pending" for row in cloud_presence_tasks))


if __name__ == "__main__":
    unittest.main()
