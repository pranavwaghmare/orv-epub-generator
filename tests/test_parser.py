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
