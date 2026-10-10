"""HTML parsing and content extraction for Flamecomics ORV chapters."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup, Tag


@dataclass
class ParsedImage:
    """Metadata for an image found in chapter content."""
    index: int
    src: str
    alt: str = ""
    is_base64: bool = False
    original_tag_html: str = ""


@dataclass
class ParsedChapter:
    """Structured extraction of a chapter from raw HTML."""
    chapter_number: float
    title: str
    content_html: str
    next_url: Optional[str]
    images: List[ParsedImage] = field(default_factory=list)
    source_url: str = ""
    novel_id: Optional[str] = None
    raw_heading: str = ""


class ChapterParseError(Exception):
    """Raised when chapter content, title, or number cannot be parsed."""
    pass


def extract_chapter_number_from_text(text: str) -> Optional[float]:
    """
    Extract chapter number from arbitrary text.
    Handles 'Chapter 166: ...', 'Ch. 166.5', 'Chapter 1', etc.
    """
    if not text:
        return None
    
    # Priority 1: 'Chapter 166' or 'Ch. 166' or 'Ch 166'
    match = re.search(r'\b(?:chapter|ch|episode|ep)\.?\s*(\d+(?:\.\d+)?)', text, re.IGNORECASE)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            pass

    # Priority 2: Leading or standalone digits followed by colon or title
    match = re.search(r'^\s*(\d+(?:\.\d+)?)\s*[:\-\—]', text)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            pass

    return None


def format_chapter_display(num: float) -> str:
    """Format chapter number for display (e.g., 1.0 -> '1', 1.5 -> '1.5')."""
    if num.is_integer():
        return str(int(num))
    return str(num)


def extract_next_data_json(soup: BeautifulSoup) -> Optional[dict]:
    """Extract Next.js __NEXT_DATA__ JSON payload if present."""
    tag = soup.find("script", id="__NEXT_DATA__")
    if tag and tag.string:
        try:
            return json.loads(tag.string)
        except json.JSONDecodeError:
            return None
    return None


def discover_next_chapter_url(soup: BeautifulSoup, base_url: str, next_data: Optional[dict] = None) -> Optional[str]:
    """
    Dynamically discover the next chapter URL.
    Checks Next.js hydration payload and actual DOM links/buttons.
    Never relies on incrementing numeric IDs.
    """
    # 1. Check Next.js __NEXT_DATA__ pageProps
    if next_data:
        page_props = next_data.get("props", {}).get("pageProps", {})
        next_token = page_props.get("next")
        novel_id = page_props.get("chapter", {}).get("novel_id") or page_props.get("novel", {}).get("id")
        
        if next_token and isinstance(next_token, str) and next_token.strip():
            next_token = next_token.strip()
            # If next_token is a full URL
            if next_token.startswith("http://") or next_token.startswith("https://"):
                return next_token
            # If novel_id is known, construct standard route
            if novel_id:
                return urljoin(base_url, f"/novel/{novel_id}/{next_token}")
            # Otherwise construct relative to current path parent
            parsed = urlparse(base_url)
            path_parts = parsed.path.rstrip("/").rsplit("/", 1)
            if len(path_parts) > 1:
                new_path = f"{path_parts[0]}/{next_token}"
                return urljoin(base_url, new_path)
            return urljoin(base_url, next_token)

    # 2. Check DOM <a> tags with explicit rel="next"
    for a in soup.find_all("a", href=True):
        rel = a.get("rel", [])
        if isinstance(rel, list):
            rel_str = " ".join(rel).lower()
        else:
            rel_str = str(rel).lower()
        if "next" in rel_str:
            href = a["href"].strip()
            if href and href != "#":
                return urljoin(base_url, href)

    # 3. Check DOM <a> tags with text or title or aria-label containing "next"
    for a in soup.find_all("a", href=True):
        text = a.get_text(strip=True).lower()
        aria = (a.get("aria-label") or "").lower()
        title = (a.get("title") or "").lower()
        if any("next" in val for val in [text, aria, title]) and "page" not in text:
            href = a["href"].strip()
            if href and href != "#" and not href.startswith("javascript:"):
                return urljoin(base_url, href)

    # 4. Check <button> with data attributes or rel="next" containing links
    for b in soup.find_all(["button", "div"]):
        for attr in ["data-href", "data-url", "data-next"]:
            val = b.get(attr)
            if val:
                return urljoin(base_url, val)

    # 5. Check <link rel="next"> in document head
    link_next = soup.find("link", rel=lambda r: r and "next" in (r if isinstance(r, list) else [r]))
    if link_next and link_next.get("href"):
        return urljoin(base_url, link_next["href"])

    return None


def extract_title_and_number(soup: BeautifulSoup, next_data: Optional[dict] = None) -> Tuple[Optional[float], str, str]:
    """
    Extract authoritative chapter number, display title, and raw heading text.
    Returns (chapter_number, clean_title, raw_heading).
    """
    chapter_num: Optional[float] = None
    clean_title: str = ""
    raw_heading: str = ""

    # Priority 1: Check Next.js __NEXT_DATA__
    if next_data:
        chapter_dict = next_data.get("props", {}).get("pageProps", {}) .get("chapter", {})
        raw_ch = chapter_dict.get("chapter")
        if raw_ch is not None:
            try:
                chapter_num = float(raw_ch)
            except (ValueError, TypeError):
                pass
        
        ch_title = chapter_dict.get("chapter_title") or chapter_dict.get("title")
        if ch_title:
            raw_heading = str(ch_title).strip()

    # Priority 2: Check DOM top navbar or headings for chapter title
    # Example: <p class="TopChapterNavbar-module...chapter_title">Chapter 166</p>
    nav_title_el = soup.find(lambda tag: tag.name in ["p", "span", "h1", "h2", "h3", "div"] 
                             and any("chapter_title" in c or "chapterTitle" in c for c in tag.get("class", [])))
    if nav_title_el:
        nav_text = nav_title_el.get_text(strip=True)
        if nav_text:
            extracted_num = extract_chapter_number_from_text(nav_text)
            if extracted_num is not None and chapter_num is None:
                chapter_num = extracted_num

    # Priority 3: Search paragraphs in the chapter content for "Chapter X: ..."
    # Many web novel releases include the chapter heading as a prominent paragraph
    # e.g.: "Chapter 166: Episode 32 — Kim Dokja's Love (3)" or "Chapter 1: Prologue..."
    content_container = find_content_container(soup)
    if content_container:
        for p in content_container.find_all(["p", "h1", "h2", "h3", "h4"]):
            p_text = p.get_text(strip=True)
            if p_text.lower().startswith("chapter") or "episode" in p_text.lower() or "prologue" in p_text.lower() or "epilogue" in p_text.lower():
                extracted_num = extract_chapter_number_from_text(p_text)
                if extracted_num is not None:
                    if chapter_num is None:
                        chapter_num = extracted_num
                    raw_heading = p_text
                    # If this heading has more substance than just "Chapter X" or "Ch. X", stop scanning
                    clean_check = re.sub(r'^(?:chapter|ch\.?)\s*\d+(?:\.\d+)?\s*[:\-—–]?\s*', '', p_text, flags=re.IGNORECASE).strip()
                    if clean_check:
                        break

    # Priority 4: Document <title> tag
    # Example: "Omniscient Reader's Viewpoint - Episode 32 — Kim Dokja's Love (3) - Chapter 166 - Flame Comics"
    if soup.title and soup.title.string:
        title_text = soup.title.string.strip()
        if chapter_num is None:
            chapter_num = extract_chapter_number_from_text(title_text)
        if not raw_heading:
            raw_heading = title_text

    # Synthesize clean title
    if raw_heading:
        clean_title = raw_heading
        # Strip common site brandings
        clean_title = re.sub(r'\s*-\s*Flame\s*Comics.*$', '', clean_title, flags=re.IGNORECASE)
        clean_title = re.sub(r'\s*-\s*We\s*Tried.*$', '', clean_title, flags=re.IGNORECASE)
        clean_title = re.sub(r'^\s*Omniscient Reader\'?s Viewpoint\s*-\s*', '', clean_title, flags=re.IGNORECASE)
        clean_title = clean_title.strip()

    if not clean_title and chapter_num is not None:
        clean_title = f"Chapter {format_chapter_display(chapter_num)}"
    elif not clean_title:
        clean_title = "Untitled Chapter"

    # Ensure clean_title begins with Chapter X if it doesn't already
    if chapter_num is not None and not re.search(r'\bchapter\s*' + re.escape(format_chapter_display(chapter_num)), clean_title, re.IGNORECASE):
        clean_title = f"Chapter {format_chapter_display(chapter_num)}: {clean_title}"

    return chapter_num, clean_title, raw_heading


def find_content_container(soup: BeautifulSoup) -> Optional[Tag]:
    """
    Locate the main chapter content container element in the DOM.
    Excludes comments, reaction boxes, sidebars, and navigation headers.
    """
    def _class_str(tag: Tag) -> str:
        c = tag.get("class", [])
        if isinstance(c, (list, tuple)):
            return " ".join(c).lower()
        return str(c).lower()

    # 0. Dedicated reader article or surface container (e.g. WeTriedTLS reader-article)
    article = soup.find("article", class_=lambda c: c and "reader-article" in c)
    if article:
        return article
    reader_surface = soup.find("div", id="reader-container")
    if reader_surface:
        return reader_surface

    # 1. Exact Mantine CSS module class: chapterContent
    for div in soup.find_all("div"):
        c_str = _class_str(div)
        if "chaptercontent" in c_str and "contentpaper" not in c_str:
            return div

    # 2. Broader chapter content patterns
    for tag in soup.find_all(["div", "article", "section"]):
        c_str = _class_str(tag)
        if any(k in c_str for k in ["chaptercontent", "chapter-content", "entry-content", "novel-content", "reading-content"]):
            return tag

    # 3. TypographyStyles wrapper
    for tag in soup.find_all("div"):
        c_str = _class_str(tag)
        if "typographystyles" in c_str:
            return tag

    # 4. Fallback: Find container with highest paragraph count
    best_candidate: Optional[Tag] = None
    max_paragraphs = 0
    for tag in soup.find_all(["div", "article", "main"]):
        c_str = _class_str(tag)
        if any(skip in c_str for skip in ["comment", "footer", "header", "nav", "disqus", "modal"]):
            continue
        p_count = len(tag.find_all("p"))
        if p_count > max_paragraphs:
            max_paragraphs = p_count
            best_candidate = tag

    if max_paragraphs >= 2:
        return best_candidate

    return None


def clean_content_dom(container: Tag) -> Tag:
    """
    Remove ads, comments, scripts, style, buttons, and reaction widgets
    while strictly preserving paragraphs, headings, blockquotes, and inline illustrations.
    """
    # Remove script, style, form, button, iframe, noscript, svg
    for unwanted in container.find_all(["script", "style", "form", "button", "iframe", "noscript", "svg"]):
        unwanted.decompose()

    # Remove site branding/footer images (e.g. WeTriedTLS wetried_only, wetried_footer, logos)
    for img in container.find_all("img"):
        src = (img.get("src") or img.get("data-src") or "").lower()
        if any(bad in src for bad in ["wetried_only", "wetried_footer", "logo", "avatar"]):
            parent = img.parent
            img.decompose()
            if parent and parent.name in ["p", "figure", "div"] and not parent.get_text(strip=True) and not parent.find("img"):
                parent.decompose()

    # Remove elements matching ad, comment, or reaction patterns
    for tag in container.find_all(True):
        classes = " ".join(tag.get("class", [])).lower()
        tag_id = (tag.get("id") or "").lower()
        if any(bad in classes or bad in tag_id for bad in ["advertisement", "adsbygoogle", "reaction", "comment", "share-box"]):
            tag.decompose()

    return container


def extract_images_from_container(container: Tag) -> List[ParsedImage]:
    """
    Locate all inline images in the container, recording their attributes and positions.
    Handles data URIs (base64) as well as regular HTTP(S) and relative URLs.
    """
    parsed_images: List[ParsedImage] = []
    
    for idx, img in enumerate(container.find_all("img")):
        src = img.get("src") or img.get("data-src") or ""
        src = src.strip()
        alt = img.get("alt") or ""
        is_base64 = src.startswith("data:image/")
        
        parsed_img = ParsedImage(
            index=idx,
            src=src,
            alt=alt,
            is_base64=is_base64,
            original_tag_html=str(img)
        )
        parsed_images.append(parsed_img)

    return parsed_images


def rewrite_html_image_srcs(content_html: str, image_map: Dict[int, str]) -> str:
    """
    Rewrite <img> src attributes in the archived HTML to local relative paths.
    image_map: maps image index (0, 1, 2...) -> local relative path (e.g. '../images/ch1_0.png')
    Preserves exact inline position.
    """
    soup = BeautifulSoup(content_html, "html.parser")
    imgs = soup.find_all("img")
    
    for idx, img in enumerate(imgs):
        if idx in image_map:
            img["src"] = image_map[idx]
            # Clean up lazy loading and srcset attributes that could interfere with EPUB/offline display
            if img.get("srcset"):
                del img["srcset"]
            if img.get("data-src"):
                del img["data-src"]
            if img.get("loading"):
                del img["loading"]

    return str(soup)


def build_standalone_html(title: str, chapter_num: float, content_body: str) -> str:
    """
    Wrap extracted chapter content in a clean, self-contained HTML5 document.
    """
    num_str = format_chapter_display(chapter_num)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
</head>
<body>
    <article class="chapter-content">
        <header>
            <h1 class="chapter-title">{title}</h1>
        </header>
        <div class="chapter-body">
{content_body}
        </div>
    </article>
</body>
</html>
"""


def parse_chapter_html(html: str, base_url: str) -> ParsedChapter:
    """
    Master parse function: extracts chapter number, title, cleaned content,
    next chapter URL, and inline image metadata.
    """
    if not html or not html.strip():
        raise ChapterParseError(f"Received empty HTML for {base_url}")

    soup = BeautifulSoup(html, "html.parser")
    next_data = extract_next_data_json(soup)

    # 1. Discover next chapter URL
    next_url = discover_next_chapter_url(soup, base_url, next_data)

    # 2. Extract chapter number and title
    chapter_num, clean_title, raw_heading = extract_title_and_number(soup, next_data)
    if chapter_num is None:
        raise ChapterParseError(f"Could not identify chapter number for {base_url}")

    # 3. Locate content container
    content_container = find_content_container(soup)
    if not content_container:
        raise ChapterParseError(f"Could not locate chapter content container for {base_url}")

    # 4. Clean content container
    cleaned_container = clean_content_dom(content_container)

    # 5. Extract images
    images = extract_images_from_container(cleaned_container)

    # 6. Extract inner HTML
    content_html = cleaned_container.decode_contents()

    novel_id = None
    if next_data:
        novel_id = str(next_data.get("props", {}).get("pageProps", {}).get("chapter", {}).get("novel_id") or "")

    return ParsedChapter(
        chapter_number=chapter_num,
        title=clean_title,
        content_html=content_html,
        next_url=next_url,
        images=images,
        source_url=base_url,
        novel_id=novel_id,
        raw_heading=raw_heading,
    )


WETRIED_BANNER_MARKERS = [
    "kp91bgfq6bv414pnvayz1fis",
    "oxjv7l3jg8r52p2iqnwqgvhg",
    "abz8jc9v1hnn92rm71uh9wje",
    "whundeq4yvaxklucovndsqb7",
    "wetried_only",
    "wetried_footer",
    "logo",
    "avatar",
]


def fetch_wetried_chapter_images(chapter_num: float, downloader: Optional[Any] = None, timeout: int = 10) -> List[Tuple[str, str]]:
    """
    Query WeTriedTLS (https://wetriedtls.com/series/omniscient-readers-viewpoint/chapter-X)
    for the given chapter number to find any official story illustrations along with their
    preceding anchor paragraph text.
    Returns a list of (image_url, anchor_text) tuples.
    Excludes site brandings, footers, and recurring translator banners.
    """
    ch_display = format_chapter_display(chapter_num)
    url = f"https://wetriedtls.com/series/omniscient-readers-viewpoint/chapter-{ch_display}"
    try:
        if downloader is not None and hasattr(downloader, "fetch_html"):
            html = downloader.fetch_html(url)
        else:
            import requests
            resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=timeout)
            if resp.status_code != 200:
                return []
            html = resp.text
    except Exception:
        return []

    soup = BeautifulSoup(html, "html.parser")
    article = soup.find("article", class_=lambda c: c and "reader-article" in c) or soup.find("div", id="reader-container")
    if not article:
        return []

    results: List[Tuple[str, str]] = []
    # Search for images inside article
    for img in article.find_all("img"):
        src = (img.get("src") or img.get("data-src") or "").strip()
        if not src or any(m in src for m in WETRIED_BANNER_MARKERS):
            continue

        # Look for preceding non-empty anchor text
        anchor_text = ""
        wrapper = img.parent
        while wrapper and wrapper.parent and wrapper.parent != article:
            wrapper = wrapper.parent

        if wrapper:
            for sib in wrapper.previous_siblings:
                t = getattr(sib, "get_text", lambda **k: "")(strip=True)
                if t and len(t) >= 15:
                    anchor_text = t
                    break

        results.append((src, anchor_text))

    return results


def enrich_chapter_with_wetried_images(parsed: ParsedChapter, downloader: Optional[Any] = None, timeout: int = 10) -> int:
    """
    Enrich a parsed chapter with missing story illustrations from WeTriedTLS.
    If the chapter already has genuine story illustrations, no changes are made.
    Otherwise, queries WeTriedTLS, locates matching anchor paragraphs in the chapter prose,
    injects the illustration tags, and updates parsed.images and parsed.content_html.
    Returns the number of images injected.
    """
    if parsed.chapter_number is None:
        return 0

    # If chapter already contains non-base64 images that are not banners, skip
    story_images = [img for img in parsed.images if not img.is_base64 and not any(m in img.src for m in WETRIED_BANNER_MARKERS)]
    if story_images:
        return 0

    wetried_imgs = fetch_wetried_chapter_images(parsed.chapter_number, downloader=downloader, timeout=timeout)
    if not wetried_imgs:
        return 0

    soup = BeautifulSoup(parsed.content_html, "html.parser")
    injected_count = 0

    for src, anchor in wetried_imgs:
        target_p = None
        if anchor:
            anchor_prefix = anchor[:30].strip().lower()
            for p in soup.find_all("p"):
                if anchor_prefix and anchor_prefix in p.get_text().lower():
                    target_p = p
                    break

        new_div = soup.new_tag("div", attrs={"class": "image"})
        new_img = soup.new_tag("img", src=src, alt="Illustration")
        new_div.append(new_img)

        if target_p:
            target_p.insert_after(new_div)
        else:
            first_p = soup.find("p")
            if first_p and ("prologue" in parsed.title.lower() or parsed.chapter_number == 1.0):
                first_p.insert_before(new_div)
            else:
                soup.append(new_div)

        new_parsed_img = ParsedImage(
            index=len(parsed.images),
            src=src,
            alt="Illustration",
            is_base64=False,
            original_tag_html=str(new_img),
        )
        parsed.images.append(new_parsed_img)
        injected_count += 1

    if injected_count > 0:
        parsed.content_html = str(soup)

    return injected_count
