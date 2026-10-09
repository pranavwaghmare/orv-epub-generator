"""Tests for offline archive validation and integrity verification."""

from pathlib import Path
from src.archive_validator import validate_archive
from src.manifest import ChapterRecord, ImageRecord, Manifest


def test_validator_perfect_archive(tmp_path: Path):
    job_dir = tmp_path / "valid_job"
    chapters_dir = job_dir / "chapters"
    images_dir = job_dir / "images"
    chapters_dir.mkdir(parents=True)
    images_dir.mkdir(parents=True)

    manifest = Manifest(
        job_dir=job_dir,
        job_id="valid_job",
        job_name="Valid",
        starting_url="https://flamecomics.xyz/novel/8/start",
        requested_ending_chapter=2.0,
        detected_starting_chapter=1.0,
    )

    (chapters_dir / "chapter_1.html").write_text("<p>Chapter 1 text</p>", encoding="utf-8")
    (chapters_dir / "chapter_2.html").write_text("<p>Chapter 2 text</p>", encoding="utf-8")
    (images_dir / "ch_1_0.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)

    ch1 = ChapterRecord(
        chapter_number=1.0,
        discovery_index=0,
        title="Chapter 1",
        source_url="https://flamecomics.xyz/novel/8/1",
        status="completed",
        html_path="chapters/chapter_1.html",
        images=[
            ImageRecord(index=0, source_url="src", local_path="images/ch_1_0.png", status="completed")
        ],
    )
    ch2 = ChapterRecord(
        chapter_number=2.0,
        discovery_index=1,
        title="Chapter 2",
        source_url="https://flamecomics.xyz/novel/8/2",
        status="completed",
        html_path="chapters/chapter_2.html",
    )
    manifest.add_or_update_chapter(ch1)
    manifest.add_or_update_chapter(ch2)

    res = validate_archive(manifest)
    assert res.is_valid is True
    assert res.completed_chapters == 2
    assert len(res.missing_html_files) == 0
    assert len(res.missing_image_files) == 0


def test_validator_missing_files_and_range_gap(tmp_path: Path):
    job_dir = tmp_path / "broken_job"
    manifest = Manifest(
        job_dir=job_dir,
        job_id="broken_job",
        job_name="Broken",
        starting_url="https://flamecomics.xyz/novel/8/start",
        requested_ending_chapter=5.0,
        detected_starting_chapter=1.0,
    )

    ch1 = ChapterRecord(
        chapter_number=1.0,
        discovery_index=0,
        title="Chapter 1",
        source_url="https://flamecomics.xyz/novel/8/1",
        status="completed",
        html_path="chapters/missing_chapter_1.html",  # Does not exist on disk
        images=[
            ImageRecord(index=0, source_url="src", local_path="images/missing.png", status="completed")
        ],
    )
    manifest.add_or_update_chapter(ch1)

    res = validate_archive(manifest)
    assert res.is_valid is False
    assert res.can_override is True  # Has 1 completed chapter record
    assert len(res.missing_html_files) == 1
    assert len(res.missing_image_files) == 1
    assert 2.0 in res.missing_chapter_numbers
    assert 5.0 in res.missing_chapter_numbers
