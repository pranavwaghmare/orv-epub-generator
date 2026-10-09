"""Tests for JobManager state management and worker coordination."""

from pathlib import Path
import time
from unittest.mock import MagicMock, patch

from src.job_manager import JobManager


def test_job_manager_create_and_list(tmp_path: Path):
    manager = JobManager(base_jobs_dir=tmp_path)
    assert len(manager.list_jobs()) == 0

    job1 = manager.create_job(
        starting_url="https://flamecomics.xyz/novel/8/start",
        ending_chapter=5.0,
        detected_starting_chapter=1.0,
        job_name="Test Job A",
    )
    assert job1.job_id in [m.job_id for m in manager.list_jobs()]

    loaded = manager.get_job(job1.job_id)
    assert loaded is not None
    assert loaded.job_name == "Test Job A"


def test_job_manager_background_archival(tmp_path: Path, flamecomics_ch1_html: str):
    manager = JobManager(base_jobs_dir=tmp_path)
    job = manager.create_job(
        starting_url="https://flamecomics.xyz/novel/8/start",
        ending_chapter=1.0,
        detected_starting_chapter=1.0,
    )

    with patch("src.downloader.Downloader.fetch_html", return_value=flamecomics_ch1_html):
        started = manager.start_archival(job.job_id)
        assert started is True

        # Wait briefly for daemon worker thread to finish single chapter
        for _ in range(50):
            if not manager.is_busy():
                break
            time.sleep(0.05)

        assert not manager.is_busy()
        reloaded = manager.get_job(job.job_id)
        assert reloaded.status == "completed"
        assert len(reloaded.chapters) == 1
