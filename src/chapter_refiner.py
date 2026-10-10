"""Second-stage EPUB formatting refiner: cleans translator banners/credits,
implements interactive footnotes, styles system windows, fixes duplicate titles,
and handles episode grouping for ORV chapters.
"""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from xml.sax.saxutils import escape

from bs4 import BeautifulSoup, NavigableString, Tag
from PIL import Image

logger = logging.getLogger(__name__)

# Known signatures of the recurring WeTriedTLS translator banner image
BANNER_SHA256 = "183e9ae92f5c638d453f21849cd9d4b8d213631126b9a69c7dc0736f1dfa56a5"
BANNER_FILE_SIZE = 118661
BANNER_DIMENSIONS = (611, 310)
BANNER_B64_PREFIX = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAmMAAAE2CAY"


def is_known_recurring_banner(source_str: str) -> bool:
    """Identify whether a raw source string or data URI is the recurring translator banner."""
    if not source_str:
        return False
    if source_str.startswith(BANNER_B64_PREFIX):
        return True
    return False


def is_recurring_banner(img_tag: Tag, job_dir: Path) -> bool:
    """
    Identify whether an <img> tag is the recurring translator banner image
    based on asset identity (SHA256 hash, byte size, dimensions, or base64 prefix).
    Never relies merely on element position.
    """
    src = (img_tag.get("src") or "").strip()
    if not src:
        return False

    # Check Base64 prefix
    if src.startswith(BANNER_B64_PREFIX):
        return True

    # If src points to local image file
    clean_src = src.replace("../", "")
    target_file = job_dir / clean_src
    if target_file.exists() and target_file.is_file():
        # Quick size check
        if target_file.stat().st_size == BANNER_FILE_SIZE:
            return True
        # Hash check
        h = hashlib.sha256(target_file.read_bytes()).hexdigest()
        if h == BANNER_SHA256:
            return True
        # Dimensions check
        try:
            with Image.open(target_file) as im:
                if im.size == BANNER_DIMENSIONS:
                    return True
        except Exception:
            pass
    else:
        # Check if skipped during archival and recorded in manifest
        manifest_file = job_dir / "manifest.json"
        if manifest_file.exists():
            try:
                import json
                with open(manifest_file, "r", encoding="utf-8") as f:
                    mdata = json.load(f)
                for ch in mdata.get("chapters", []):
                    for img in ch.get("images", []):
                        if img.get("local_path") == clean_src:
                            if img.get("status") == "skipped_banner" or img.get("source_url", "").startswith(BANNER_B64_PREFIX):
                                return True
            except Exception:
                pass

    return False


def remove_recurring_banners(soup: BeautifulSoup, job_dir: Path) -> int:
    """Remove recurring translator banner images and their enclosing figure/paragraph tags."""
    removed = 0
    for img in soup.find_all("img"):
        if is_recurring_banner(img, job_dir):
            parent = img.parent
            # If wrapped in figure or p that has no other substantive content
            if parent and parent.name in ["figure", "p"]:
                # Check if parent contains any other text or images
                other_imgs = [i for i in parent.find_all("img") if i != img]
                other_text = parent.get_text(strip=True)
                if not other_imgs and not other_text:
                    parent.decompose()
                else:
                    img.decompose()
            else:
                img.decompose()
            removed += 1
    return removed


def remove_translator_credits(soup: BeautifulSoup) -> int:
    """
    Remove the recurring introductory and outro translator credit blocks:
      - WE TRIED TRANSLATIONS (or WE TRIED TRANSLATIONS x FLAME COMICS)
      - Translator/Editor: ...
      - Discord: https://dsc.gg/wetried
      - Join our discord at https://dsc.gg/wetried
    Cleans up redundant surrounding empty paragraphs or divider rules.
    """
    removed = 0
    body = soup.find(class_="chapter-body") or soup.body or soup

    # Pass 1: Introductory credit block in the first ~25 elements
    all_elements = list(body.children)
    intro_candidates = [el for el in all_elements if getattr(el, "name", None) in ["p", "div", "hr"]][:25]

    for el in intro_candidates:
        if not getattr(el, "name", None):
            continue
        text = el.get_text(strip=True)
        # Identify credit paragraphs
        is_credit = False
        if any(marker in text.lower() for marker in ["we tried translations", "wetried translations"]):
            is_credit = True
        elif any(marker in text.lower() for marker in ["translator/editor:", "translator:", "editor:"]) and len(text) < 80:
            is_credit = True
        elif "dsc.gg/wetried" in text.lower() or "discord.gg/wetried" in text.lower():
            is_credit = True

        if is_credit:
            el.decompose()
            removed += 1

    # Pass 2: Outro credit paragraph at bottom of chapter
    all_ps = body.find_all("p")
    for el in all_ps[-6:]:
        text = el.get_text(strip=True)
        if "dsc.gg/wetried" in text.lower() or "discord at https://" in text.lower():
            el.decompose()
            removed += 1

    # Clean up leading empty paragraphs and redundant dividers before the story begins
    for el in list(body.children)[:10]:
        if not getattr(el, "name", None):
            continue
        t = el.get_text(strip=True)
        if el.name == "p" and not t:
            el.decompose()
        elif el.name == "hr":
            # If hr appears right before story or after deleted credits
            next_sib = el.find_next_sibling()
            if next_sib and getattr(next_sib, "name", None) in ["p", "hr"] and not next_sib.get_text(strip=True):
                el.decompose()

    return removed


def remove_duplicate_headings(soup: BeautifulSoup, chapter_title: str) -> None:
    """
    Remove in-body duplicate headings and novel title ornaments at the top of the chapter:
      - '◈ Omniscient Reader’s Viewpoint ◈'
      - 'Chapter X: ...' when it repeats the chapter title already shown in <h1>.
    """
    body = soup.find(class_="chapter-body") or soup.body or soup
    ps = [el for el in body.children if getattr(el, "name", None) == "p"][:10]

    for el in ps:
        text = el.get_text(strip=True)
        if not text:
            continue

        # Check for book title ornament: ◈ Omniscient Reader’s Viewpoint ◈
        if "omniscient reader" in text.lower() and ("◈" in text or len(text) < 50):
            el.decompose()
            continue

        # Check for chapter title duplicate: e.g. 'Chapter 2: Episode 1 - Launch of the Paywall (1)'
        if text.lower().startswith("chapter ") or text.lower().startswith("ch. "):
            # If text matches the start or bulk of chapter_title
            clean_p = re.sub(r'[\s\-_—–:]', '', text.lower())
            clean_t = re.sub(r'[\s\-_—–:]', '', chapter_title.lower())
            if clean_p in clean_t or clean_t in clean_p:
                # Remove duplicate paragraph and any immediately following divider hr
                next_sib = el.find_next_sibling()
                el.decompose()
                if next_sib and getattr(next_sib, "name", None) == "hr":
                    next_sib.decompose()
                break


def format_system_windows(soup: BeautifulSoup) -> int:
    """
    Convert in-world system windows surrounded by paired '+' delimiters into
    semantic HTML blocks: <div class="system-window">...</div>.
    Safeguards against lone/unmatched plus signs.
    """
    body = soup.find(class_="chapter-body") or soup.body or soup
    # Flatten any stray wrapper tags (e.g. <main>, <article>, <center>)
    for wrapper in body.find_all(["main", "article", "center"]):
        wrapper.unwrap()

    children = [c for c in body.children if getattr(c, "name", None) in ["p", "div", "blockquote"]]

    windows_created = 0
    i = 0
    while i < len(children):
        el = children[i]
        t = el.get_text(strip=True)

        # Check for opening delimiter: '+' or '+<Header Title>'
        is_opening = (t == "+") or (t.startswith("+") and len(t) <= 40)
        if is_opening:
            header_text = ""
            m = re.search(r'\+\s*[<\[]([^>\]/]+)[>\]]', t)
            if m:
                header_text = m.group(1).strip()

            # Search forward for closing '+' delimiter (bounded to maximum 40 paragraphs)
            closing_idx = None
            for j in range(i + 1, min(i + 40, len(children))):
                cand = children[j].get_text(strip=True)
                if cand == "+" or (cand.startswith("+") and len(cand) == 1):
                    closing_idx = j
                    break

            if closing_idx is not None:
                # If no header was in delimiter, check if first enclosed paragraph is a bracketed title
                if not header_text:
                    for k in range(i + 1, closing_idx):
                        cand_first = children[k].get_text(strip=True)
                        if not cand_first:
                            continue
                        m_first = re.match(r'^[<\[]([^>\]/]+)[>\]]$', cand_first)
                        if m_first and len(cand_first) < 50:
                            header_text = m_first.group(1).strip()
                            children[k].decompose()
                        break

                # Found valid pair! Create system window
                win_div = soup.new_tag("div", attrs={"class": "system-window"})
                if header_text:
                    h_div = soup.new_tag("div", attrs={"class": "system-window-header"})
                    h_div.string = f"<{header_text}>"
                    win_div.append(h_div)

                content_div = soup.new_tag("div", attrs={"class": "system-window-body"})

                # Collect enclosed elements between i and closing_idx
                for k in range(i + 1, closing_idx):
                    child_to_move = children[k]
                    if child_to_move.parent is None:
                        continue
                    # Clean up empty lines inside window
                    if child_to_move.name == "p" and not child_to_move.get_text(strip=True):
                        continue
                    content_div.append(child_to_move.extract())

                win_div.append(content_div)

                # Replace opening element with win_div and decompose closing delimiter
                children[closing_idx].decompose()
                el.replace_with(win_div)

                windows_created += 1
                i = closing_idx + 1
                continue

        i += 1

    return windows_created


def clean_empty_paragraphs(soup: BeautifulSoup) -> int:
    """
    Remove all empty paragraphs (e.g. <p><br/></p>, <p>&nbsp;</p>, whitespace)
    throughout the entire chapter body. Paragraph spacing in EPUB must be governed
    by CSS margins rather than stray empty paragraph tags.
    """
    body = soup.find(class_="chapter-body") or soup.body or soup
    removed = 0
    for p in list(body.find_all("p")):
        if not p.find("img") and not p.get_text(strip=True).replace("\xa0", "").strip():
            p.decompose()
            removed += 1
    return removed


def clean_chapter_intro(soup: BeautifulSoup) -> int:
    """
    Remove all template dividing lines (<hr>) and empty paragraphs
    between the chapter heading and the first story paragraph.
    Preserves legitimate volume/chapter illustrations, but strips any
    dividers before or after them so content flows cleanly.
    """
    body = soup.find(class_="chapter-body") or soup.body or soup
    removed = 0
    for el in list(body.children):
        if not getattr(el, "name", None):
            continue
        if el.name == "hr":
            el.decompose()
            removed += 1
        elif el.name == "p" and el.get_text(strip=True):
            # Reached first actual story prose paragraph
            break
        elif el.name in ["div", "blockquote"] and "system-window" in el.get("class", []):
            break
    return removed


def format_interactive_footnotes(soup: BeautifulSoup, chapter_num: float) -> int:
    """
    Detect footnote references in prose and matching footnote definitions at the bottom.
    Converts references into clickable <a epub:type="noteref"> and builds semantic
    <section epub:type="footnotes"> with return links. Removes any trailing lines or
    empty "Footnotes" headings if no footnotes exist.
    """
    body = soup.find(class_="chapter-body") or soup.body or soup
    ps = body.find_all("p")

    # 1. Locate footnote section at the bottom of the chapter
    fn_heading_idx = None
    for idx, el in enumerate(ps):
        t = el.get_text(strip=True).lower()
        if t in ["footnotes:", "footnotes", "footnote:", "footnote", "t/n:", "translator notes:"]:
            fn_heading_idx = idx
            break

    # 2. Extract footnote definitions
    fn_defs: Dict[int, str] = {}
    nodes_to_remove: List[Tag] = []
    if fn_heading_idx is not None:
        nodes_to_remove.append(ps[fn_heading_idx])
        for el in ps[fn_heading_idx + 1:]:
            t = el.get_text(strip=True)
            if not t:
                nodes_to_remove.append(el)
                continue
            m = re.match(r'^\[(\d+)\]\s*(.*)$', t)
            if m:
                num = int(m.group(1))
                fn_defs[num] = m.group(2).strip()
                nodes_to_remove.append(el)
            elif any(marker in t.lower() for marker in ["dsc.gg", "discord", "we tried"]):
                nodes_to_remove.append(el)
            else:
                break

    # Decompose collected trailing nodes (including "Footnotes:" header even if list was empty)
    for node in nodes_to_remove:
        node.decompose()

    # Remove any trailing <hr> tags at the bottom of the chapter
    for el in reversed(list(body.children)):
        if not getattr(el, "name", None):
            continue
        if el.name == "hr":
            el.decompose()
        elif el.get_text(strip=True) or el.find("img"):
            break

    if not fn_defs:
        return 0

    # 3. Find and replace in-prose references [N] where N in fn_defs
    num_str = str(int(chapter_num)) if chapter_num.is_integer() else str(chapter_num)
    ref_counts: Dict[int, int] = {num: 0 for num in fn_defs}
    replaced_count = 0

    for text_node in list(body.find_all(string=True)):
        text_str = str(text_node)
        matches = list(re.finditer(r'\[(\d+)\]', text_str))
        if not matches:
            continue

        valid_matches = [m for m in matches if int(m.group(1)) in fn_defs]
        if not valid_matches:
            continue

        parent = text_node.parent
        if not parent or parent.name in ["script", "style", "a", "h1", "h2", "h3"]:
            continue

        new_contents = []
        last_pos = 0
        for m in valid_matches:
            fn_num = int(m.group(1))
            ref_counts[fn_num] += 1
            occ = ref_counts[fn_num]

            if m.start() > last_pos:
                new_contents.append(NavigableString(text_str[last_pos:m.start()]))

            a_ref = soup.new_tag(
                "a",
                href=f"#fn-ch{num_str}-{fn_num}",
                id=f"fnref-ch{num_str}-{fn_num}-{occ}",
                attrs={"class": "noteref", "epub:type": "noteref"}
            )
            sup = soup.new_tag("sup")
            sup.string = f"[{fn_num}]"
            a_ref.append(sup)
            new_contents.append(a_ref)

            last_pos = m.end()
            replaced_count += 1

        if last_pos < len(text_str):
            new_contents.append(NavigableString(text_str[last_pos:]))

        text_node.replace_with(*new_contents)

    # 4. Construct semantic footnotes section without redundant inner hr
    fn_section = soup.new_tag("section", attrs={"class": "chapter-footnotes", "epub:type": "footnotes"})
    title_h2 = soup.new_tag("h2", attrs={"class": "footnotes-title"})
    title_h2.string = "Footnotes"
    fn_section.append(title_h2)

    ol = soup.new_tag("ol", attrs={"class": "footnotes-list"})
    for fn_num in sorted(fn_defs.keys()):
        li = soup.new_tag("li", id=f"fn-ch{num_str}-{fn_num}", attrs={"class": "footnote", "epub:type": "footnote"})
        p_fn = soup.new_tag("p")
        p_fn.string = fn_defs[fn_num] + " "

        a_back = soup.new_tag(
            "a",
            href=f"#fnref-ch{num_str}-{fn_num}-1",
            attrs={"class": "footnote-backref", "title": "Return to text"}
        )
        a_back.string = "↩"
        p_fn.append(a_back)
        li.append(p_fn)
        ol.append(li)

    fn_section.append(ol)
    body.append(fn_section)

    return replaced_count


def extract_episode_group(chapter_title: str) -> str:
    """
    Extract the parent episode name from a chapter title for nested TOC grouping.
    Examples:
      - 'Chapter 2: Episode 1 - Launch of the Paywall (1)' -> 'Episode 1: Launch of the Paywall'
      - 'Chapter 1: Prologue - Three Ways to Survive the Apocalypse' -> 'Prologue: Three Ways to Survive the Apocalypse'
      - 'Chapter 7: Episode 2 - Protagonist (1)' -> 'Episode 2: Protagonist'
      - 'Chapter 166: Episode 32 — Kim Dokja\'s Love (3)' -> 'Episode 32: Kim Dokja\'s Love'
    """
    # Clean leading 'Chapter X:'
    t_clean = re.sub(r'^\s*Chapter\s+\d+(?:\.\d+)?\s*[:\-—–]\s*', '', chapter_title, flags=re.IGNORECASE).strip()

    # Pattern A: Episode N - Name (Part)
    m = re.match(r'^Episode\s+(\d+)\s*[:\-—–]\s*(.*?)(?:\s*\(\d+\))?\s*$', t_clean, re.IGNORECASE)
    if m:
        ep_num = m.group(1)
        ep_name = m.group(2).strip()
        return f"Episode {ep_num}: {ep_name}" if ep_name else f"Episode {ep_num}"

    # Pattern B: Prologue - Name
    m = re.match(r'^Prologue(?:\s*[:\-—–]\s*(.*?))?(?:\s*\(\d+\))?\s*$', t_clean, re.IGNORECASE)
    if m:
        p_name = m.group(1)
        return f"Prologue: {p_name.strip()}" if p_name else "Prologue"

    # Pattern C: Epilogue - Name
    m = re.match(r'^Epilogue(?:\s*[:\-—–]\s*(.*?))?(?:\s*\(\d+\))?\s*$', t_clean, re.IGNORECASE)
    if m:
        e_name = m.group(1)
        return f"Epilogue: {e_name.strip()}" if e_name else "Epilogue"

    # Pattern D: Fallback for other chapter titles
    return "Chapters"


def refine_chapter_html(
    raw_html: str,
    chapter_title: str,
    chapter_num: float,
    job_dir: Path,
) -> str:
    """
    Master transformation function applied during EPUB staging.
    Leaves original archived HTML files on disk completely untouched.
    """
    soup = BeautifulSoup(raw_html, "html.parser")

    # 1. Remove recurring translator banner image
    remove_recurring_banners(soup, job_dir)

    # 2. Remove translator credit blocks & promos
    remove_translator_credits(soup)

    # 3. Fix duplicate chapter headings and ornament paragraphs
    remove_duplicate_headings(soup, chapter_title)

    # 4. Clean all empty paragraphs throughout the chapter
    clean_empty_paragraphs(soup)

    # 5. Clean chapter intro: remove all dividing lines before story prose
    clean_chapter_intro(soup)

    # 6. Format system windows (+ ... +)
    format_system_windows(soup)

    # 7. Format interactive footnotes and clean trailing outro / lines
    format_interactive_footnotes(soup, chapter_num)

    return str(soup)
