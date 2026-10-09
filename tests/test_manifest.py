"""Tests for manifest persistence, atomic saves, and state recovery."""

from pathlib import Path
import pytest
from src.manifest import ChapterRecord, ImageRecord, Manifest


def test_manifest_atomic_save_and_reload(tmp_path: Path):
    job_dir = tmp_path / "test_job"
    manifest = Manifest(
        job_dir=job_dir,
        job_id="test_job_1",
        job_name="Test Job 1",
        starting_url="https://flamecomics.xyz/novel/8/start",
        requested_ending_chapter=5.0,
        detected_starting_chapter=1.0,
    )
    ch1 = ChapterRecord(
        chapter_number=1.0,
        discovery_index=0,
        title="Chapter 1: Start",
        source_url="https://flamecomics.xyz/novel/8/start",
        next_url="https://flamecomics.xyz/novel/8/ch2",
        status="completed",
        html_path="chapters/chapter_1.html",
    )
    manifest.add_or_update_chapter(ch1)
    manifest.save()

    assert (job_dir / "manifest.json").exists()
    assert not (job_dir / "manifest.json.tmp").exists()

    # Create dummy chapter file so validation passes
    (job_dir / "chapters").mkdir(parents=True)
    (job_dir / "chapters" / "chapter_1.html").write_text("<p>Hello</p>" * 20, encoding="utf-8")

    loaded = Manifest.load(job_dir)
    assert loaded.job_id == "test_job_1"
    assert loaded.requested_ending_chapter == 5.0
    assert len(loaded.chapters) == 1
    assert loaded.chapters[0].title == "Chapter 1: Start"
    assert loaded.chapters[0].status == "completed"


def test_reconcile_state_from_disk(tmp_path: Path):
    job_dir = tmp_path / "recovery_job"
    job_dir.mkdir()
    (job_dir / "chapters").mkdir()

    manifest = Manifest(
        job_dir=job_dir,
        job_id="recovery_job",
        job_name="Recovery Job",
        starting_url="https://flamecomics.xyz/novel/8/start",
        requested_ending_chapter=3.0,
    )
    manifest.status = "in_progress"

    # Ch 1: was in_progress, but HTML file was written successfully
    ch1_file = job_dir / "chapters" / "chapter_1.html"
    ch1_file.write_text("<p>Valid content</p>" * 10, encoding="utf-8")
    ch1 = ChapterRecord(
        chapter_number=1.0,
        discovery_index=0,
        title="Chapter 1",
        source_url="https://flamecomics.xyz/novel/8/1",
        status="in_progress",
        html_path="chapters/chapter_1.html",
    )

    # Ch 2: was in_progress, but HTML was never written (crashed during fetch)
    ch2 = ChapterRecord(
        chapter_number=2.0,
        discovery_index=1,
        title="Chapter 2",
        source_url="https://flamecomics.xyz/novel/8/2",
        status="in_progress",
        html_path="chapters/chapter_2.html",
    )

    manifest.add_or_update_chapter(ch1)
    manifest.add_or_update_chapter(ch2)
    manifest.save()

    # Load and verify reconciliation
    reloaded = Manifest.load(job_dir)
    assert reloaded.status == "paused"
    ch1_rec = reloaded.get_chapter(1.0)
    ch2_rec = reloaded.get_chapter(2.0)

    assert ch1_rec.status == "completed"
    assert ch2_rec.status == "pending"


def test_manifest_stats(tmp_path: Path):
    job_dir = tmp_path / "stats_job"
    manifest = Manifest(
        job_dir=job_dir,
        job_id="stats_job",
        job_name="Stats Job",
        starting_url="https://flamecomics.xyz/novel/8/start",
        requested_ending_chapter=2.0,
    )
    ch1 = ChapterRecord(
        chapter_number=1.0,
        discovery_index=0,
        title="Chapter 1",
        source_url="https://example.com/1",
        status="completed",
        images=[
            ImageRecord(index=0, source_url="img1", local_path="images/img1.png", status="completed"),
            ImageRecord(index=1, source_url="img2", local_path="images/img2.png", status="failed", error="404"),
        ],
    )
    ch2 = ChapterRecord(
        chapter_number=2.0,
        discovery_index=1,
        title="Chapter 2",
        source_url="https://example.com/2",
        status="failed",
        error="Connection refused",
    )
    manifest.add_or_update_chapter(ch1)
    manifest.add_or_update_chapter(ch2)

    stats = manifest.get_stats()
    assert stats["total_chapters"] == 2
    assert stats["completed_chapters"] == 1
    assert stats["failed_chapters"] == 1
    assert stats["total_images"] == 2
    assert stats["completed_images"] == 1
    assert stats["failed_images"] == 1
