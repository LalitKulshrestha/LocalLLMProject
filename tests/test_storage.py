import tempfile
import unittest
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from localforge.config import settings
from localforge.db import create_job, initialize, retry_job, update_job
from localforge.main import _job_output, _old_completed_jobs, _open_in_finder, _remove_job_files


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_data_dir = settings.localforge_data_dir
        self.old_output_dir = settings.localforge_output_dir
        settings.localforge_data_dir = Path(self.temp.name) / "data"
        settings.localforge_output_dir = Path(self.temp.name) / "exports"
        settings.prepare()
        initialize()

    def tearDown(self):
        settings.localforge_data_dir = self.old_data_dir
        settings.localforge_output_dir = self.old_output_dir
        self.temp.cleanup()

    def test_job_cleanup_only_removes_owned_files(self):
        job = create_job("face_swap", {}, {}, status="completed")
        output = settings.localforge_output_dir / f"face-swap-{job['id'][:12]}.mp4"
        output.write_bytes(b"video")
        outside = Path(self.temp.name) / "keep.mp4"
        outside.write_bytes(b"keep")
        upload_dir = settings.localforge_data_dir / "uploads" / job["id"]
        upload_dir.mkdir()
        (upload_dir / "source.png").write_bytes(b"image")
        job["outputs"] = [str(output), str(outside)]

        self.assertEqual(_job_output(job, 0), output.resolve())
        with self.assertRaises(HTTPException):
            _job_output(job, 1)
        _remove_job_files(job)

        self.assertFalse(output.exists())
        self.assertFalse(upload_dir.exists())
        self.assertTrue(outside.exists())

    def test_retry_requires_failed_status(self):
        job = create_job("face_swap", {}, {}, status="completed")
        self.assertIsNone(retry_job(job["id"]))
        update_job(job["id"], status="failed")
        self.assertEqual(retry_job(job["id"])["status"], "queued")

    def test_old_cleanup_selects_only_completed_jobs_over_30_days(self):
        old = create_job("face_swap", {}, {}, status="completed")
        recent = create_job("face_swap", {}, {}, status="completed")
        failed = create_job("face_swap", {}, {}, status="failed")
        old_time = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
        with sqlite3.connect(settings.localforge_data_dir / "localforge.sqlite3") as db:
            db.execute(
                "UPDATE jobs SET updated_at = ? WHERE id IN (?, ?)",
                (old_time, old["id"], failed["id"]),
            )
        self.assertEqual([job["id"] for job in _old_completed_jobs()], [old["id"]])
        self.assertNotEqual(old["id"], recent["id"])

    def test_finder_action_reveals_existing_path_without_copying(self):
        target = settings.localforge_output_dir / "result.mp4"
        target.write_bytes(b"video")
        with patch("localforge.main.subprocess.run") as run:
            _open_in_finder(target, reveal=True)
        run.assert_called_once_with(
            ["/usr/bin/open", "-R", str(target)],
            check=True,
            capture_output=True,
            timeout=10,
        )
        self.assertEqual(target.read_bytes(), b"video")

    def test_existing_job_database_gains_progress_stage(self):
        legacy_dir = Path(self.temp.name) / "legacy"
        legacy_dir.mkdir()
        with sqlite3.connect(legacy_dir / "localforge.sqlite3") as db:
            db.execute("""CREATE TABLE jobs (
                id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL,
                progress REAL NOT NULL DEFAULT 0, params TEXT NOT NULL,
                inputs TEXT NOT NULL, outputs TEXT NOT NULL DEFAULT '[]',
                error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )""")
        current_data_dir = settings.localforge_data_dir
        try:
            settings.localforge_data_dir = legacy_dir
            initialize()
            job = create_job("face_swap", {}, {})
            self.assertEqual(job["stage"], "Queued")
            with sqlite3.connect(legacy_dir / "localforge.sqlite3") as db:
                columns = {row[1] for row in db.execute("PRAGMA table_info(jobs)")}
            self.assertIn("stage", columns)
        finally:
            settings.localforge_data_dir = current_data_dir


if __name__ == "__main__":
    unittest.main()
