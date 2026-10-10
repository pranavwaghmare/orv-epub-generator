"""Tests for Archiver discovery pipeline, loop detection, pause, and retry."""

from pathlib import Path
import pytest
from unittest.mock import MagicMock

from src.downloader import Downloader
from src.manifest import ChapterRecord, ImageRecord, Manifest
from src.scraper import Archiver


def test_archiver_two_chapters_success(tmp_path: Path, flamecomics_ch1_html: str, flamecomics_ch2_html: str):
    job_dir = tmp_path / "job_scrape"
    manifest = Manifest(
        job_dir=job_dir,
        job_id="job_scrape",
        job_name="Test Scrape",
        starting_url="https://flamecomics.xyz/novel/8/580e7d866052dfd8",
        requested_ending_chapter=2.0,
    )

    downloader = Downloader(request_delay=0.0)
    downloader.fetch_html = MagicMock(side_effect=[flamecomics_ch1_html, flamecomics_ch2_html])

    archiver = Archiver(manifest=manifest, downloader=downloader, enrich_missing_images=False)
    archiver.run()

    assert manifest.status == "completed"
    assert len(manifest.chapters) == 2
    assert manifest.chapters[0].chapter_number == 1.0
    assert manifest.chapters[1].chapter_number == 2.0
    assert manifest.chapters[0].status == "completed"
    assert manifest.chapters[1].status == "completed"

    # Verify HTML files were written
    assert (job_dir / "chapters" / "chapter_1.html").exists()
    assert (job_dir / "chapters" / "chapter_2.html").exists()

    # Verify inline illustration was saved
    assert len(manifest.chapters[0].images) == 1
    assert (job_dir / manifest.chapters[0].images[0].local_path).exists()


def test_archiver_pause_resumable(tmp_path: Path, flamecomics_ch1_html: str, flamecomics_ch2_html: str):
    job_dir = tmp_path / "job_pause"
    manifest = Manifest(
        job_dir=job_dir,
        job_id="job_pause",
        job_name="Pause Test",
        starting_url="https://flamecomics.xyz/novel/8/580e7d866052dfd8",
        requested_ending_chapter=2.0,
    )

    downloader = Downloader(request_delay=0.0)
    downloader.fetch_html = MagicMock(side_effect=[flamecomics_ch1_html, flamecomics_ch2_html])

    # Pause after chapter 1 completes
    pause_trigger = False

    def check_pause():
        nonlocal pause_trigger
        return pause_trigger

    archiver = Archiver(manifest=manifest, downloader=downloader, should_pause=check_pause, enrich_missing_images=False)

    # First run: archive chapter 1, then trigger pause before chapter 2
    def on_progress(op, ratio, log):
        nonlocal pause_trigger
        if "Completed Chapter 1" in op:
            pause_trigger = True

    archiver.on_progress = on_progress
    archiver.run()

    assert manifest.status == "paused"
    assert len(manifest.chapters) == 1
    assert manifest.chapters[0].chapter_number == 1.0

    # Resume: unpause and run again
    pause_trigger = False
    archiver_resumed = Archiver(manifest=manifest, downloader=downloader, should_pause=lambda: False, enrich_missing_images=False)
    archiver_resumed.run()

    assert manifest.status == "completed"
    assert len(manifest.chapters) == 2
    assert manifest.chapters[1].chapter_number == 2.0


def test_archiver_loop_detection(tmp_path: Path, flamecomics_ch1_html: str):
    job_dir = tmp_path / "job_loop"
    manifest = Manifest(
        job_dir=job_dir,
        job_id="job_loop",
        job_name="Loop Test",
        starting_url="https://flamecomics.xyz/novel/8/loop",
        requested_ending_chapter=5.0,
    )

    # Returning same page that points to itself or duplicate URL
    downloader = Downloader(request_delay=0.0)
    downloader.fetch_html = MagicMock(return_value=flamecomics_ch1_html)

    archiver = Archiver(manifest=manifest, downloader=downloader)
    archiver.run()

    # Loop detected on second iteration
    assert len(manifest.chapters) <= 2


def test_archiver_missing_next_link(tmp_path: Path, flamecomics_ch2_html: str):
    """Verify archiver handles missing next link safely."""
    job_dir = tmp_path / "job_missing_next"
    manifest = Manifest(
        job_dir=job_dir,
        job_id="job_missing_next",
        job_name="Missing Next Test",
        starting_url="https://flamecomics.xyz/novel/8/ch2",
        requested_ending_chapter=5.0,  # Target 5, but ch 2 has next=None
    )

    downloader = Downloader(request_delay=0.0)
    downloader.fetch_html = MagicMock(return_value=flamecomics_ch2_html)

    archiver = Archiver(manifest=manifest, downloader=downloader)
    archiver.run()

    assert len(manifest.chapters) == 1
    assert manifest.chapters[0].chapter_number == 2.0
    assert manifest.chapters[0].next_url is None
    assert manifest.status in ["paused", "completed"]


def test_archiver_resume_skips_completed_chapters(tmp_path: Path, flamecomics_ch1_html: str, flamecomics_ch2_html: str):
    """Resumed job must reuse completed chapters without downloading them again."""
    job_dir = tmp_path / "job_reuse"
    chapters_dir = job_dir / "chapters"
    chapters_dir.mkdir(parents=True)
    (chapters_dir / "chapter_1.html").write_text("<p>Ch1 text</p>", encoding="utf-8")

    manifest = Manifest(
        job_dir=job_dir,
        job_id="job_reuse",
        job_name="Reuse Test",
        starting_url="https://flamecomics.xyz/novel/8/ch1",
        requested_ending_chapter=2.0,
        detected_starting_chapter=1.0,
    )
    ch1 = ChapterRecord(
        chapter_number=1.0,
        discovery_index=0,
        title="Chapter 1",
        source_url="https://flamecomics.xyz/novel/8/ch1",
        next_url="https://flamecomics.xyz/novel/8/ch2",
        status="completed",
        html_path="chapters/chapter_1.html",
    )
    manifest.add_or_update_chapter(ch1)
    manifest.save()

    downloader = Downloader(request_delay=0.0)
    # Mock should ONLY be called for chapter 2, NOT chapter 1!
    downloader.fetch_html = MagicMock(return_value=flamecomics_ch2_html)

    archiver = Archiver(manifest=manifest, downloader=downloader, enrich_missing_images=False)
    archiver.run()

    # fetch_html called only once for Ch 2
    assert downloader.fetch_html.call_count == 1
    assert manifest.status == "completed"
    assert len(manifest.chapters) == 2


def test_archiver_retry_failed_items(tmp_path: Path, flamecomics_ch1_html: str, tiny_png_b64: str):
    """Retry failed images without redownloading completed chapters."""
    job_dir = tmp_path / "job_retry"
    chapters_dir = job_dir / "chapters"
    images_dir = job_dir / "images"
    chapters_dir.mkdir(parents=True)
    images_dir.mkdir(parents=True)

    # Ch 1 HTML with image
    html_content = f'<p>Ch 1</p><img src="{tiny_png_b64}" /><p>After</p>'
    (chapters_dir / "chapter_1.html").write_text(html_content, encoding="utf-8")

    manifest = Manifest(
        job_dir=job_dir,
        job_id="job_retry",
        job_name="Retry Test",
        starting_url="https://flamecomics.xyz/novel/8/ch1",
        requested_ending_chapter=1.0,
    )
    # Chapter is completed, but its image failed on previous run
    ch1 = ChapterRecord(
        chapter_number=1.0,
        discovery_index=0,
        title="Chapter 1",
        source_url="https://flamecomics.xyz/novel/8/ch1",
        status="completed",
        html_path="chapters/chapter_1.html",
        images=[
            ImageRecord(
                index=0,
                source_url=tiny_png_b64,
                local_path="images/ch_1_0.png",
                status="failed",
                error="Timed out previously",
            )
        ],
    )
    manifest.add_or_update_chapter(ch1)
    manifest.save()

    downloader = Downloader(request_delay=0.0)
    archiver = Archiver(manifest=manifest, downloader=downloader)
    archiver.retry_failed()

    # Image should now be completed
    assert ch1.images[0].status == "completed"
    assert (job_dir / "images" / "ch_1_0.png").exists()


def test_archiver_reuses_overlapping_chapter_from_other_job(tmp_path: Path):
    """If another job already archived a chapter, a new job must copy it locally without network fetch."""
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()

    # Setup Job A with completed Chapter 1
    job_a_dir = jobs_dir / "job_a"
    (job_a_dir / "chapters").mkdir(parents=True)
    (job_a_dir / "images").mkdir(parents=True)
    (job_a_dir / "chapters" / "chapter_1.html").write_text("<p>Chapter 1 text</p>", encoding="utf-8")
    (job_a_dir / "images" / "ch_1_0.png").write_bytes(b"fake_image_bytes")

    manifest_a = Manifest(
        job_dir=job_a_dir,
        job_id="job_a",
        job_name="Job A",
        starting_url="https://flamecomics.xyz/novel/8/ch1",
        requested_ending_chapter=1.0,
    )
    ch1 = ChapterRecord(
        chapter_number=1.0,
        discovery_index=0,
        title="Chapter 1: The Start",
        source_url="https://flamecomics.xyz/novel/8/ch1",
        next_url="https://flamecomics.xyz/novel/8/ch2",
        status="completed",
        html_path="chapters/chapter_1.html",
        images=[
            ImageRecord(
                index=0,
                source_url="https://example.com/img0.png",
                local_path="images/ch_1_0.png",
                status="completed",
            )
        ],
    )
    manifest_a.add_or_update_chapter(ch1)
    manifest_a.status = "completed"
    manifest_a.save()

    # Setup Job B starting at Chapter 1
    job_b_dir = jobs_dir / "job_b"
    manifest_b = Manifest(
        job_dir=job_b_dir,
        job_id="job_b",
        job_name="Job B",
        starting_url="https://flamecomics.xyz/novel/8/ch1",
        requested_ending_chapter=1.0,
    )
    manifest_b.save()

    # Downloader that fails if fetch_html is called for Chapter 1
    class NetworkForbiddenDownloader(Downloader):
        def fetch_html(self, url: str) -> str:
            raise AssertionError(f"Network request attempted for already archived URL: {url}")

    archiver = Archiver(manifest=manifest_b, downloader=NetworkForbiddenDownloader())
    archiver.run()

    # Job B must have completed Chapter 1 by copying files locally
    assert manifest_b.status == "completed"
    assert len(manifest_b.chapters) == 1
    ch_b = manifest_b.chapters[0]
    assert ch_b.status == "completed"
    assert ch_b.chapter_number == 1.0
    assert (job_b_dir / ch_b.html_path).exists()
    assert (job_b_dir / ch_b.html_path).read_text(encoding="utf-8") == "<p>Chapter 1 text</p>"
    assert (job_b_dir / "images" / "ch_1_0.png").exists()

