"""Tests for HTML parsing, content extraction, and link discovery."""

import pytest
from src.parser import (
    ChapterParseError,
    discover_next_chapter_url,
    extract_chapter_number_from_text,
    format_chapter_display,
    parse_chapter_html,
    rewrite_html_image_srcs,
)


def test_extract_chapter_number():
    assert extract_chapter_number_from_text("Chapter 166: Episode 32") == 166.0
    assert extract_chapter_number_from_text("Ch. 5.5 Side Story") == 5.5
    assert extract_chapter_number_from_text("Episode 1 - The Start") == 1.0
    assert extract_chapter_number_from_text("1: Prologue") == 1.0
    assert extract_chapter_number_from_text("No numbers here") is None


def test_format_chapter_display():
    assert format_chapter_display(1.0) == "1"
    assert format_chapter_display(166.0) == "166"
    assert format_chapter_display(1.5) == "1.5"


def test_parse_flamecomics_chapter(flamecomics_ch1_html):
    base_url = "https://flamecomics.xyz/novel/8/580e7d866052dfd8"
    parsed = parse_chapter_html(flamecomics_ch1_html, base_url)

    assert parsed.chapter_number == 1.0
    assert "Prologue" in parsed.title
    assert parsed.next_url == "https://flamecomics.xyz/novel/8/db6ecd64465f6b07"
    assert len(parsed.images) == 1
    assert parsed.images[0].is_base64 is True
    assert "There are three ways to survive" in parsed.content_html
    # Ensure ads, reactions, and scripts were stripped
    assert "<script" not in parsed.content_html
    assert "reaction-box" not in parsed.content_html
    assert "Comments here" not in parsed.content_html
    assert "Login" not in parsed.content_html


def test_parse_generic_relative_next(generic_html_chapter):
    base_url = "https://example.com/novel/8/ch10"
    parsed = parse_chapter_html(generic_html_chapter, base_url)

    assert parsed.chapter_number == 10.0
    assert "Chapter 10" in parsed.title
    assert parsed.next_url == "https://example.com/novel/8/ch11"
    assert len(parsed.images) == 1
    assert parsed.images[0].is_base64 is False
    assert parsed.images[0].src == "/assets/ch10_art.png"


def test_rewrite_html_image_srcs():
    raw_html = '<p>Text 1</p><img src="http://example.com/art.png" /><p>Text 2</p>'
    image_map = {0: "../images/ch_1_0.png"}
    rewritten = rewrite_html_image_srcs(raw_html, image_map)

    assert 'src="../images/ch_1_0.png"' in rewritten
    assert "Text 1" in rewritten
    assert "Text 2" in rewritten


def test_parse_invalid_html():
    with pytest.raises(ChapterParseError):
        parse_chapter_html("", "https://example.com")

    with pytest.raises(ChapterParseError):
        parse_chapter_html("<html><body><p>No chapter number anywhere</p></body></html>", "https://example.com")


def test_parse_wetriedtls_chapter_html():
    sample_html = """<!DOCTYPE html>
    <html>
    <head><title>Chapter 1 - Omniscient Reader's Viewpoint - We Tried</title></head>
    <body>
        <h1>Chapter 1</h1>
        <div class="hidden max-h-12"><img src="/thumbnail.png"/></div>
        <article class="reader-article">
            <p>WE TRIED TRANSLATIONS</p>
            <p>Translator: ZERO_SUGAR</p>
            <p>◈ Omniscient Reader’s Viewpoint ◈</p>
            <p>Chapter 1: Prologue - Three Ways to Survive the Apocalypse</p>
            <div class="image"><img src="https://media.reaperscans.net/file/7BSHk1m/rw9dmol9scdfn7socu0eja6p.jpg" /></div>
            <p>There are Three Ways to Survive the Apocalypse.</p>
            <img src="/_next/image?url=%2Fwetried_footer.png&w=640&q=75" />
        </article>
        <a rel="next" href="/series/omniscient-readers-viewpoint/chapter-2">Next</a>
    </body>
    </html>
    """
    parsed = parse_chapter_html(sample_html, "https://wetriedtls.com/series/omniscient-readers-viewpoint/chapter-1")

    assert parsed.chapter_number == 1.0
    assert "Prologue" in parsed.title
    assert parsed.next_url == "https://wetriedtls.com/series/omniscient-readers-viewpoint/chapter-2"
    # Story image preserved, footer image stripped
    assert len(parsed.images) == 1
    assert "rw9dmol9scdfn7socu0eja6p.jpg" in parsed.images[0].src
    assert "wetried_footer" not in parsed.content_html


def test_enrich_chapter_with_wetried_images_mocked(monkeypatch):
    from src.parser import ParsedChapter, ParsedImage, enrich_chapter_with_wetried_images

    # Mock fetch_wetried_chapter_images
    def mock_fetch(ch_num, downloader=None, timeout=10):
        return [
            ("https://example.com/illustration1.jpg", "There are three ways to survive")
        ]

    import src.parser as p_module
    monkeypatch.setattr(p_module, "fetch_wetried_chapter_images", mock_fetch)

    chapter = ParsedChapter(
        chapter_number=1.0,
        title="Chapter 1: Prologue",
        content_html="<p>There are three ways to survive the apocalypse.</p><p>Second paragraph.</p>",
        next_url="https://example.com/ch2",
        images=[],
    )

    injected = enrich_chapter_with_wetried_images(chapter)
    assert injected == 1
    assert len(chapter.images) == 1
    assert chapter.images[0].src == "https://example.com/illustration1.jpg"
    assert '<img alt="Illustration" src="https://example.com/illustration1.jpg"/>' in chapter.content_html
