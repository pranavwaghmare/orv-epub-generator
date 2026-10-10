"""Automated tests for second-stage EPUB formatting rules."""

from pathlib import Path
from bs4 import BeautifulSoup
import pytest

from src.chapter_refiner import (
    BANNER_FILE_SIZE,
    BANNER_SHA256,
    extract_episode_group,
    format_interactive_footnotes,
    format_system_windows,
    is_recurring_banner,
    refine_chapter_html,
    remove_duplicate_headings,
    remove_recurring_banners,
    remove_translator_credits,
)


def test_remove_recurring_banner_and_preserve_story_images(tmp_path: Path):
    job_dir = tmp_path / "job_banner"
    img_dir = job_dir / "images"
    img_dir.mkdir(parents=True)

    # 1. Create dummy recurring banner image
    banner_file = img_dir / "ch_1_banner.png"
    # Write exact dummy banner matching size
    banner_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * (BANNER_FILE_SIZE - 8))

    # 2. Create legitimate story illustration
    story_img = img_dir / "ch_1_story.png"
    story_img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\xff" * 500)

    html = f"""<div class="chapter-body">
        <p>Before</p>
        <figure><img src="../images/ch_1_banner.png" /></figure>
        <p>Middle</p>
        <figure><img src="../images/ch_1_story.png" /></figure>
        <p>After</p>
    </div>"""

    soup = BeautifulSoup(html, "html.parser")
    removed = remove_recurring_banners(soup, job_dir)

    assert removed == 1
    imgs = soup.find_all("img")
    assert len(imgs) == 1
    assert "ch_1_story.png" in imgs[0]["src"]


def test_remove_translator_credits():
    html = """<div class="chapter-body">
        <p><strong>WE TRIED TRANSLATIONS</strong></p>
        <p><br/></p>
        <p><strong>Translator/Editor: Ryuu</strong></p>
        <p><strong>Discord: </strong><a href="https://dsc.gg/wetried">https://dsc.gg/wetried</a></p>
        <p><br/></p>
        <hr/>
        <p>“I’m Dokja.”</p>
        <p>This was how I often introduced myself to people.</p>
        <p>Join our discord at https://dsc.gg/wetried</p>
    </div>"""

    soup = BeautifulSoup(html, "html.parser")
    remove_translator_credits(soup)

    text = soup.get_text()
    assert "WE TRIED TRANSLATIONS" not in text
    assert "Ryuu" not in text
    assert "dsc.gg/wetried" not in text
    assert "I’m Dokja." in text
    assert "This was how I often introduced myself" in text


def test_remove_duplicate_headings():
    html = """<div class="chapter-body">
        <p>◈ Omniscient Reader’s Viewpoint ◈</p>
        <p><br/></p>
        <p>Chapter 2: Episode 1 - Launch of the Paywall (1)</p>
        <hr/>
        <p>“I’m Dokja.”</p>
    </div>"""

    soup = BeautifulSoup(html, "html.parser")
    remove_duplicate_headings(soup, "Chapter 2: Episode 1 - Launch of the Paywall (1)")

    text = soup.get_text()
    assert "◈" not in text
    assert "Chapter 2:" not in text
    assert "I’m Dokja." in text


def test_system_window_paired_delimiters():
    html = """<div class="chapter-body">
        <p>Some prose before quest.</p>
        <p>+</p>
        <p>Category: Main</p>
        <p>Difficulty: F</p>
        <p>Clear conditions: Kill one or more living beings.</p>
        <p>+</p>
        <p>Prose after quest.</p>
    </div>"""

    soup = BeautifulSoup(html, "html.parser")
    count = format_system_windows(soup)

    assert count == 1
    win = soup.find(class_="system-window")
    assert win is not None
    assert "Category: Main" in win.get_text()
    assert "Prose before quest." not in win.get_text()
    assert "Prose after quest." not in win.get_text()


def test_system_window_with_header():
    html = """<div class="chapter-body">
        <p>+&lt;Character Details&gt;</p>
        <p>Name: Lee Hyeonseong</p>
        <p>Age: 28</p>
        <p>+</p>
    </div>"""

    soup = BeautifulSoup(html, "html.parser")
    count = format_system_windows(soup)

    assert count == 1
    win = soup.find(class_="system-window")
    assert win is not None
    header = win.find(class_="system-window-header")
    assert header is not None
    assert "<Character Details>" in header.get_text()
    assert "Name: Lee Hyeonseong" in win.get_text()


def test_system_window_lone_plus_safeguard():
    """Unmatched plus signs or math expressions must not create broken system windows."""
    html = """<div class="chapter-body">
        <p>He gained +5 Strength and 1 + 1 coins.</p>
        <p>No closing delimiter in this chapter.</p>
    </div>"""

    soup = BeautifulSoup(html, "html.parser")
    count = format_system_windows(soup)

    assert count == 0
    assert len(soup.find_all(class_="system-window")) == 0
    assert "+5 Strength" in soup.get_text()


def test_interactive_footnotes():
    html = """<div class="chapter-body">
        <p>A 50,000-won[2] gift card or a 3,000-won[1] candy.</p>
        <p>He was in carriage [3807] of the subway.</p>
        <p>Footnotes:</p>
        <p>[1] 3,000 won is approximately 2 USD.</p>
        <p>[2] 50,000 won is approximately 37 USD.</p>
    </div>"""

    soup = BeautifulSoup(html, "html.parser")
    refs_count = format_interactive_footnotes(soup, 1.0)

    assert refs_count == 2

    # Check noteref links
    noterefs = soup.find_all(class_="noteref")
    assert len(noterefs) == 2
    assert noterefs[0]["href"] == "#fn-ch1-2"
    assert noterefs[1]["href"] == "#fn-ch1-1"

    # [3807] must NOT be converted to a footnote reference
    assert "[3807]" in soup.get_text()

    # Check footnotes section
    fn_section = soup.find(class_="chapter-footnotes")
    assert fn_section is not None
    assert fn_section["epub:type"] == "footnotes"
    items = fn_section.find_all(class_="footnote")
    assert len(items) == 2
    assert "3,000 won is approximately 2 USD." in items[0].get_text()
    assert "50,000 won is approximately 37 USD." in items[1].get_text()


def test_extract_episode_group():
    assert extract_episode_group("Chapter 1: Prologue - Three Ways to Survive the Apocalypse") == "Prologue: Three Ways to Survive the Apocalypse"
    assert extract_episode_group("Chapter 2: Episode 1 - Launch of the Paywall (1)") == "Episode 1: Launch of the Paywall"
    assert extract_episode_group("Chapter 6: Episode 1 - Launch of the Paywall (5)") == "Episode 1: Launch of the Paywall"
    assert extract_episode_group("Chapter 7: Episode 2 - Protagonist (1)") == "Episode 2: Protagonist"
    assert extract_episode_group("Chapter 166: Episode 32 — Kim Dokja's Love (3)") == "Episode 32: Kim Dokja's Love"
    assert extract_episode_group("Side Story: The Beginning") == "Side Story: The Beginning"
    assert extract_episode_group("Chapter 552: Side Story 1 - The World After the Fall (1)") == "Side Story 1: The World After the Fall"
    assert extract_episode_group("Special Note: Author Afterword") == "Chapters"


def test_system_window_unwraps_stray_containers():
    """Flamecomics sometimes inserts stray <main> or <center> wrappers in the DOM."""
    html = """<div class="chapter-body">
        <p>Before quest.</p>
        <p>+</p>
        <main value="">
            <p><strong>Category:</strong> Main</p>
            <p><strong>Difficulty:</strong> F</p>
        </main>
        <p>+</p>
        <p>After quest.</p>
    </div>"""

    soup = BeautifulSoup(html, "html.parser")
    count = format_system_windows(soup)

    assert count == 1
    win = soup.find(class_="system-window")
    assert win is not None
    assert "Category:" in win.get_text()
    assert "Difficulty:" in win.get_text()


def test_multiple_references_to_same_footnote():
    """Multiple references to [1] within the same chapter must create distinct ref IDs with return links."""
    html = """<div class="chapter-body">
        <p>First mention of Dokkaebi[1] here, and another mention of Dokkaebi[1] later.</p>
        <p>Footnotes:</p>
        <p>[1] A mythical Korean goblin.</p>
    </div>"""

    soup = BeautifulSoup(html, "html.parser")
    refs_count = format_interactive_footnotes(soup, 3.0)

    assert refs_count == 2
    refs = soup.find_all(class_="noteref")
    assert len(refs) == 2
    assert refs[0]["id"] == "fnref-ch3-1-1"
    assert refs[1]["id"] == "fnref-ch3-1-2"
    assert refs[0]["href"] == "#fn-ch3-1"
    assert refs[1]["href"] == "#fn-ch3-1"

    fn_sec = soup.find(class_="chapter-footnotes")
    assert fn_sec is not None
    assert fn_sec.find(id="fn-ch3-1") is not None
    backrefs = fn_sec.find_all(class_="footnote-backref")
    assert len(backrefs) >= 1


def test_clean_empty_paragraphs():
    from src.chapter_refiner import clean_empty_paragraphs
    html = """<div class="chapter-body">
        <p>Paragraph 1</p>
        <p><br/></p>
        <p>&nbsp;</p>
        <p></p>
        <p>Paragraph 2</p>
    </div>"""
    soup = BeautifulSoup(html, "html.parser")
    removed = clean_empty_paragraphs(soup)
    assert removed == 3
    ps = soup.find_all("p")
    assert len(ps) == 2
    assert ps[0].get_text() == "Paragraph 1"
    assert ps[1].get_text() == "Paragraph 2"


def test_clean_chapter_intro_and_preserve_volume_cover():
    from src.chapter_refiner import clean_chapter_intro
    html = """<div class="chapter-body">
        <hr/>
        <figure><img src="cover.png"/></figure>
        <hr/>
        <hr/>
        <p>The story begins here.</p>
        <p>Second story paragraph.</p>
    </div>"""
    soup = BeautifulSoup(html, "html.parser")
    clean_chapter_intro(soup)
    # The figure must be preserved, all hr tags before story must be gone
    assert soup.find("figure") is not None
    assert len(soup.find_all("hr")) == 0
    first_p = soup.find("p")
    assert first_p.get_text() == "The story begins here."


def test_footnotes_zero_definitions_removes_heading_and_trailing_lines():
    html = """<div class="chapter-body">
        <p>Prose at the end of chapter.</p>
        <hr/>
        <p>Footnotes:</p>
        <p>Join our discord at https://dsc.gg/wetried</p>
    </div>"""
    soup = BeautifulSoup(html, "html.parser")
    count = format_interactive_footnotes(soup, 7.0)
    assert count == 0
    # No Footnotes: heading, no section, no trailing hr
    assert soup.find(class_="chapter-footnotes") is None
    assert "Footnotes" not in soup.get_text()
    assert len(soup.find_all("hr")) == 0
    assert "discord" not in soup.get_text().lower()


def test_footnote_preserves_formatting_tags_and_prevents_stuck_words():
    """Footnote definitions must preserve <strong>/<em> tags and not stick words together."""
    html = """<div class="chapter-body">
        <p>A dokkaebi[1] appeared before Cheolsu hyung[2].</p>
        <p>Footnotes:</p>
        <p>[1] <strong>Dokkaebi</strong> or dogkagkwi (Korean: 도깨비) are mythical creatures.</p>
        <p>[2] The Korean word <strong>hyung</strong> (형) is an honorific.</p>
    </div>"""
    soup = BeautifulSoup(html, "html.parser")
    count = format_interactive_footnotes(soup, 3.0)
    assert count == 2

    fn_sec = soup.find(class_="chapter-footnotes")
    assert fn_sec is not None
    items = fn_sec.find_all("li")
    assert len(items) == 2

    # Check that strong tags are preserved
    assert items[0].find("strong") is not None
    assert items[0].find("strong").get_text() == "Dokkaebi"
    assert items[1].find("strong") is not None
    assert items[1].find("strong").get_text() == "hyung"

    # Crucially verify words are NOT stuck together
    item0_text = items[0].get_text()
    assert "Dokkaebior" not in item0_text
    assert "Dokkaebi or" in item0_text

    item1_text = items[1].get_text()
    assert "wordhung" not in item1_text
    assert "wordhyung" not in item1_text
    assert "word hyung (형)" in item1_text


def test_system_window_escaped_character_details_header():
    """Double-escaped &amp;lt;Character Details&amp;gt; in HTML must be parsed as a system window header."""
    from src.chapter_refiner import refine_chapter_html
    raw_html = """<div class="chapter-body">
        <p>A window appeared before my eyes.</p>
        <p>+</p>
        <p>&amp;lt;Character Details&amp;gt;</p>
        <p>Name: Kim Namwoon</p>
        <p>Age: 18</p>
        <p>+</p>
        <p>Story resumes here.</p>
    </div>"""
    refined = refine_chapter_html(raw_html, "Chapter 6", 6.0, Path("."))
    soup = BeautifulSoup(refined, "html.parser")

    win = soup.find(class_="system-window")
    assert win is not None
    hdr = win.find(class_="system-window-header")
    assert hdr is not None
    assert "<Character Details>" in hdr.get_text()

    # Verify &amp;lt; was not leaked as a paragraph inside the window body
    body_ps = win.find(class_="system-window-body").find_all("p")
    assert not any("Character Details" in p.get_text() for p in body_ps)
    assert any("Name: Kim Namwoon" in p.get_text() for p in body_ps)


def test_clean_chapter_intro_divider_line():
    """Divider line paragraphs like <p><strong>──────</strong></p> must be removed from chapter start."""
    from src.chapter_refiner import refine_chapter_html
    raw_html = """<div class="chapter-body">
        <p><strong>◈ Omniscient Reader’s Viewpoint ◈</strong></p>
        <p><strong>Chapter 191: Episode 36 — Horizon of the story (1)</strong></p>
        <p><strong>──────</strong></p>
        <p>That day, Seoul Dome was bathed in dazzling light.</p>
        <p>[Someone has cleared the tenth Main Scenario.]</p>
    </div>"""
    refined = refine_chapter_html(raw_html, "Chapter 191: Episode 36 — Horizon of the story (1)", 191.0, Path("."))
    soup = BeautifulSoup(refined, "html.parser")

    ps = [p.get_text(strip=True) for p in soup.find_all("p")]
    assert "──────" not in ps
    assert "◈" not in ps[0]
    assert ps[0] == "That day, Seoul Dome was bathed in dazzling light."


def test_format_scene_breaks():
    """Asterisk markers (* * *, ***) must be converted into styled semantic scene breaks."""
    from src.chapter_refiner import refine_chapter_html
    raw_html = """<div class="chapter-body">
        <p>Before the scene change.</p>
        <p style="text-align:center;justify-self:center">* * *</p>
        <p>After the scene change.</p>
        <p>***</p>
        <p>Final paragraph.</p>
    </div>"""
    refined = refine_chapter_html(raw_html, "Chapter 10", 10.0, Path("."))
    soup = BeautifulSoup(refined, "html.parser")

    scene_breaks = soup.find_all(class_="scene-break")
    assert len(scene_breaks) == 2
    for sb in scene_breaks:
        assert sb.get_text(strip=True) == "◆ ◆ ◆"
        assert not sb.has_attr("style")




