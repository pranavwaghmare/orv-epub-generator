"""Tests for offline EPUB conversion, staging, packaging, and archive inspection."""

from pathlib import Path
import zipfile
import pytest

from src.epub_converter import EpubConverter, EpubConversionError
from src.manifest import ChapterRecord, ImageRecord, Manifest


def test_epub_full_conversion(tmp_path: Path, tiny_png_bytes: bytes):
    job_dir = tmp_path / "epub_job"
    chapters_dir = job_dir / "chapters"
    images_dir = job_dir / "images"
    chapters_dir.mkdir(parents=True)
    images_dir.mkdir(parents=True)

    # Write chapter HTML with embedded image reference
    img_rel_path = "images/ch_1_0.png"
    (images_dir / "ch_1_0.png").write_bytes(tiny_png_bytes)

    ch1_html = f"""<!DOCTYPE html>
<html>
<head><title>Chapter 1: The Start</title></head>
<body>
    <article class="chapter-content">
        <header><h1 class="chapter-title">Chapter 1: The Start</h1></header>
        <div class="chapter-body">
            <p>First paragraph of chapter one.</p>
            <img src="../{img_rel_path}" alt="Illustration 1" />
            <p>Second paragraph after image.</p>
        </div>
    </article>
</body>
</html>
"""
    (chapters_dir / "chapter_1.html").write_text(ch1_html, encoding="utf-8")

    ch2_html = """<!DOCTYPE html>
<html>
<head><title>Chapter 2: The Next Step</title></head>
<body>
    <article class="chapter-content">
        <header><h1 class="chapter-title">Chapter 2: The Next Step</h1></header>
        <div class="chapter-body">
            <p>Dokja begins his journey.</p>
        </div>
    </article>
</body>
</html>
"""
    (chapters_dir / "chapter_2.html").write_text(ch2_html, encoding="utf-8")

    manifest = Manifest(
        job_dir=job_dir,
        job_id="epub_job",
        job_name="EPUB Test Job",
        starting_url="https://flamecomics.xyz/novel/8/start",
        requested_ending_chapter=2.0,
        detected_starting_chapter=1.0,
    )

    ch1_rec = ChapterRecord(
        chapter_number=1.0,
        discovery_index=0,
        title="Chapter 1: The Start",
        source_url="https://flamecomics.xyz/novel/8/1",
        status="completed",
        html_path="chapters/chapter_1.html",
        images=[
            ImageRecord(index=0, source_url="src", local_path=img_rel_path, status="completed", mime_type="image/png")
        ],
    )
    ch2_rec = ChapterRecord(
        chapter_number=2.0,
        discovery_index=1,
        title="Chapter 2: The Next Step",
        source_url="https://flamecomics.xyz/novel/8/2",
        status="completed",
        html_path="chapters/chapter_2.html",
    )
    manifest.add_or_update_chapter(ch1_rec)
    manifest.add_or_update_chapter(ch2_rec)
    manifest.save()

    converter = EpubConverter(manifest=manifest)
    epub_path = converter.convert(force_override=False)

    assert epub_path.exists()
    assert epub_path.suffix == ".epub"

    # Inspect generated EPUB ZIP contents
    with zipfile.ZipFile(epub_path, "r") as z:
        names = z.namelist()
        assert "mimetype" in names
        assert any("container.xml" in n for n in names)
        # Check Cover image
        assert any("cover.webp" in n for n in names)
        # Check Title page
        assert any("title_page.xhtml" in n for n in names)
        tp_item_name = next(n for n in names if "title_page.xhtml" in n)
        tp_content = z.read(tp_item_name).decode("utf-8")
        assert "Omniscient Reader’s Viewpoint" in tp_content
        assert "Sing Shong" in tp_content
        assert "Chapters 1–2" in tp_content

        # Verify Title page precedes TOC ('nav') in spine
        opf_name = next(n for n in names if n.endswith(".opf"))
        opf_content = z.read(opf_name).decode("utf-8")
        spine_match = opf_content[opf_content.find("<spine"):opf_content.find("</spine>")]
        tp_pos = spine_match.find("title_page")
        nav_pos = spine_match.find("nav")
        assert tp_pos != -1 and nav_pos != -1
        assert tp_pos < nav_pos

        # Check XHTML chapters
        assert any("ch_1.xhtml" in n for n in names)
        assert any("ch_2.xhtml" in n for n in names)
        # Check embedded image
        assert any("ch_1_0.png" in n for n in names)

        # Inspect ch_1.xhtml contents
        ch1_item_name = next(n for n in names if "ch_1.xhtml" in n)
        ch1_content = z.read(ch1_item_name).decode("utf-8")
        assert "First paragraph of chapter one" in ch1_content
        assert 'src="images/ch_1_0.png"' in ch1_content
        assert "Second paragraph after image" in ch1_content


def test_epub_override_incomplete_archive(tmp_path: Path):
    job_dir = tmp_path / "incomplete_job"
    chapters_dir = job_dir / "chapters"
    chapters_dir.mkdir(parents=True)

    (chapters_dir / "chapter_1.html").write_text("<p>Chapter 1 text</p>", encoding="utf-8")

    manifest = Manifest(
        job_dir=job_dir,
        job_id="incomplete_job",
        job_name="Incomplete Job",
        starting_url="https://flamecomics.xyz/novel/8/start",
        requested_ending_chapter=5.0,  # Missing chapters 2, 3, 4, 5
        detected_starting_chapter=1.0,
    )
    ch1 = ChapterRecord(
        chapter_number=1.0,
        discovery_index=0,
        title="Chapter 1",
        source_url="https://flamecomics.xyz/novel/8/1",
        status="completed",
        html_path="chapters/chapter_1.html",
    )
    manifest.add_or_update_chapter(ch1)
    manifest.save()

    converter = EpubConverter(manifest=manifest)

    # Without override, conversion must be blocked
    with pytest.raises(EpubConversionError) as exc_info:
        converter.convert(force_override=False)
    assert "validation failed" in str(exc_info.value).lower()

    # With override, conversion should succeed
    epub_path = converter.convert(force_override=True)
    assert epub_path.exists()
    assert manifest.conversion["status"] == "completed"


def test_epub_resumable_staging(tmp_path: Path):
    job_dir = tmp_path / "staging_job"
    chapters_dir = job_dir / "chapters"
    chapters_dir.mkdir(parents=True)

    (chapters_dir / "chapter_1.html").write_text("<p>Chapter 1 text</p>", encoding="utf-8")
    (chapters_dir / "chapter_2.html").write_text("<p>Chapter 2 text</p>", encoding="utf-8")

    manifest = Manifest(
        job_dir=job_dir,
        job_id="staging_job",
        job_name="Staging Job",
        starting_url="https://flamecomics.xyz/novel/8/start",
        requested_ending_chapter=2.0,
        detected_starting_chapter=1.0,
    )
    ch1 = ChapterRecord(
        chapter_number=1.0,
        discovery_index=0,
        title="Chapter 1",
        source_url="https://flamecomics.xyz/novel/8/1",
        status="completed",
        html_path="chapters/chapter_1.html",
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
    manifest.save()

    # Pre-stage Chapter 1 manually
    staging_dir = job_dir / "conversion" / "staging"
    staging_dir.mkdir(parents=True)
    (staging_dir / "ch_1.xhtml").write_text("<p>Pre-staged Ch 1</p>", encoding="utf-8")
    manifest.conversion["staged_chapters"] = [1.0]
    manifest.save()

    converter = EpubConverter(manifest=manifest)
    epub_path = converter.convert(force_override=False)
    assert epub_path.exists()

    # Verify that staged ch_1 was reused
    with zipfile.ZipFile(epub_path, "r") as z:
        ch1_item_name = next(n for n in z.namelist() if "ch_1.xhtml" in n)
        content = z.read(ch1_item_name).decode("utf-8")
        assert "Pre-staged Ch 1" in content
