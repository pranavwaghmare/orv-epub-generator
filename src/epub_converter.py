"""Resumable EPUB generation from local archive using EbookLib with refined formatting."""

from __future__ import annotations

import io
import logging
import mimetypes
import re
import shutil
import zipfile
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple
from xml.sax.saxutils import escape

from bs4 import BeautifulSoup
import ebooklib
from ebooklib import epub

from src.archive_validator import validate_archive, ValidationResult
from src.chapter_refiner import (
    extract_episode_group,
    refine_chapter_html,
)
from src.manifest import Manifest, safe_replace
from src.parser import format_chapter_display

logger = logging.getLogger(__name__)

DEFAULT_EPUB_CSS = """/* Clean, portable, reflowable e-reader styling for ORV */
@namespace epub "http://www.idpf.org/2007/ops";

body {
    font-family: Georgia, "Times New Roman", serif;
    font-size: 1.05em;
    line-height: 1.65;
    margin: 5%;
    padding: 0;
    color: #1a1a1a;
}

/* Chapter Heading */
h1.chapter-title {
    text-align: center;
    margin-top: 1.2em;
    margin-bottom: 1.2em;
    font-size: 1.4em;
    line-height: 1.35;
    font-weight: bold;
    border-bottom: 1px solid #e2e8f0;
    padding-bottom: 0.5em;
}

p {
    margin-top: 0;
    margin-bottom: 0.55em;
    line-height: 1.6;
    text-indent: 0;
}

/* Illustrations */
img {
    max-width: 100%;
    height: auto;
    display: block;
    margin: 0.6em auto;
    border-radius: 4px;
}

figure {
    margin: 0.8em 0;
    text-align: center;
}

hr {
    border: none;
    border-top: 1px solid #e2e8f0;
    margin: 1.5em auto;
    width: 60%;
}

blockquote {
    border-left: 3px solid #718096;
    margin: 1.0em 0;
    padding-left: 1em;
    font-style: italic;
    color: #4a5568;
}

/* Dedicated Title Page */
.title-page-body {
    margin: 0;
    padding: 0;
    height: 100%;
}

.title-page-container {
    text-align: center;
    padding: 25% 1em 10% 1em;
    margin: auto;
}

.title-page-title {
    font-size: 2.1em;
    font-weight: bold;
    margin-bottom: 0.3em;
    line-height: 1.25;
    color: #1a202c;
}

.title-page-range {
    font-size: 1.1em;
    font-weight: 500;
    color: #4a5568;
    margin: 0.5em 0 1.2em 0;
}

.title-page-divider {
    width: 60px;
    height: 3px;
    background-color: #4a5568;
    margin: 1.8em auto;
    border-radius: 2px;
}

.title-page-author {
    font-size: 1.15em;
    font-style: italic;
    color: #4a5568;
    margin-bottom: 0.5em;
}

.title-page-translator {
    font-size: 0.95em;
    color: #718096;
    margin-top: 2em;
}

/* In-World System Windows (+ ... +) */
.system-window {
    margin: 0.8em 0;
    padding: 0.8em 1.1em;
    border: 1.5px solid #4a5568;
    border-radius: 6px;
    background-color: #f7fafc;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    font-size: 0.95em;
    line-height: 1.5;
}

.system-window-header {
    font-weight: bold;
    font-size: 1.0em;
    margin-bottom: 0.4em;
    text-align: center;
    border-bottom: 1px dashed #718096;
    padding-bottom: 0.3em;
    letter-spacing: 0.04em;
    color: #2d3748;
}

.system-window p {
    margin: 0.22em 0;
    text-indent: 0;
}

/* Interactive Footnotes */
a.noteref {
    text-decoration: none;
    font-weight: bold;
    color: #2b6cb0;
    padding: 0 0.15em;
}

a.noteref sup {
    font-size: 0.78em;
    line-height: 0;
}

.chapter-footnotes {
    margin-top: 1.8em;
    padding-top: 0.8em;
    font-size: 0.9em;
    border-top: 1px solid #e2e8f0;
}

.footnotes-title {
    font-size: 1.05em;
    font-weight: bold;
    margin-top: 0;
    margin-bottom: 0.5em;
    color: #4a5568;
}

.footnotes-list {
    padding-left: 1.4em;
    margin: 0;
}

.footnote {
    margin-bottom: 0.35em;
    line-height: 1.45;
}

.footnote-backref {
    text-decoration: none;
    color: #2b6cb0;
    margin-left: 0.35em;
    font-size: 0.9em;
}

/* Dark Mode Reader Compatibility */
@media (prefers-color-scheme: dark) {
    body {
        color: #e2e8f0;
        background-color: #1a202c;
    }
    h1.chapter-title {
        border-bottom-color: #4a5568;
        color: #f7fafc;
    }
    .title-page-title {
        color: #f7fafc;
    }
    .title-page-divider {
        background-color: #cbd5e0;
    }
    .title-page-author, .title-page-translator {
        color: #a0aec0;
    }
    .system-window {
        background-color: #2d3748;
        border-color: #718096;
        color: #edf2f7;
    }
    .system-window-header {
        border-bottom-color: #a0aec0;
        color: #f7fafc;
    }
    a.noteref, .footnote-backref {
        color: #63b3ed;
    }
    .chapter-footnotes {
        border-top-color: #4a5568;
    }
}
"""


class EpubConversionError(Exception):
    """EPUB conversion failure."""
    pass


class EpubConverter:
    """
    Handles resumable EPUB conversion completely offline from local archives.
    Uses intermediate XHTML staging to support pausing and resumption.
    Applies second-stage formatting refinements (banner removal, credits cleanup,
    interactive footnotes, system-window styling, and episode-nested TOC).
    """

    def __init__(
        self,
        manifest: Manifest,
        on_progress: Optional[Callable[[str, float, str], None]] = None,
        should_pause: Optional[Callable[[], bool]] = None,
    ):
        self.manifest = manifest
        self.on_progress = on_progress or (lambda op, ratio, log: None)
        self.should_pause = should_pause or (lambda: False)

        self.job_dir = manifest.job_dir
        self.staging_dir = self.job_dir / "conversion" / "staging"
        self.staging_images_dir = self.staging_dir / "images"
        self.output_dir = self.job_dir / "output"

        self.staging_dir.mkdir(parents=True, exist_ok=True)
        self.staging_images_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _notify(self, op: str, ratio: float, msg: str) -> None:
        logger.info(msg)
        self.on_progress(op, ratio, msg)

    def convert(self, force_override: bool = False, force_restage: bool = False) -> Path:
        """
        Convert local archive to a polished, readable EPUB file.
        force_override: Allows generating even if archive validation detected missing chapters or assets.
        force_restage: Force re-refining all chapters from scratch even if staged XHTML exists.
        """
        # Step 1: Validate Archive
        self._notify("Validating", 0.0, "Validating local archive prior to conversion...")
        validation = validate_archive(self.manifest)
        
        if not validation.is_valid and not force_override:
            err_summary = "; ".join(validation.detailed_report[:5])
            msg = f"Archive validation failed ({err_summary}). Conversion blocked unless 'Generate Anyway' is enabled."
            self.manifest.conversion["status"] = "failed"
            self.manifest.conversion["error"] = msg
            self.manifest.save()
            raise EpubConversionError(msg)

        if not validation.has_any_content:
            raise EpubConversionError("Cannot generate EPUB: no completed chapters found in archive.")

        # If previous conversion completed or force_restage requested, clear staged chapters
        # so updated formatting passes are freshly applied to all chapters.
        if force_restage or self.manifest.conversion.get("status") == "completed":
            self.manifest.conversion["staged_chapters"] = []

        self.manifest.current_phase = "conversion"
        self.manifest.conversion["status"] = "in_progress"
        self.manifest.save()

        # Step 2: Resumable Staging of Chapters to XHTML with Refined Formatting
        completed_chapters = [c for c in self.manifest.chapters if c.status == "completed"]
        completed_chapters.sort(key=lambda c: c.chapter_number)

        staged_nums = set(self.manifest.conversion.get("staged_chapters", []))
        total_chapters = len(completed_chapters)

        staged_items: List[Tuple[float, str, Path]] = []  # (num, title, staged_xhtml_path)
        referenced_images_set: Set[str] = set()

        for idx, ch in enumerate(completed_chapters):
            if self.should_pause():
                self._notify("Paused", 0.0, "Conversion paused by user request.")
                self.manifest.conversion["status"] = "paused"
                self.manifest.save()
                raise EpubConversionError("Conversion paused.")

            num_str = format_chapter_display(ch.chapter_number)
            staged_file = self.staging_dir / f"ch_{num_str}.xhtml"
            ratio = idx / max(1, total_chapters)

            if ch.chapter_number in staged_nums and staged_file.exists() and staged_file.stat().st_size > 0:
                self._notify(f"Reusing staged Ch {num_str}", ratio, f"Reusing already staged Chapter {num_str}")
            else:
                self._notify(f"Staging Ch {num_str}", ratio, f"Refining and staging Chapter {num_str}: {ch.title}")
                self._stage_chapter(ch, staged_file)
                staged_nums.add(ch.chapter_number)
                self.manifest.conversion["staged_chapters"] = sorted(list(staged_nums))
                self.manifest.save()

            staged_items.append((ch.chapter_number, ch.title, staged_file))

            # Inspect images actually referenced in the refined staged file
            with open(staged_file, "r", encoding="utf-8") as f:
                staged_soup = BeautifulSoup(f.read(), "html.parser")
            for img_el in staged_soup.find_all("img"):
                s = img_el.get("src", "")
                if s.startswith("images/"):
                    referenced_images_set.add(Path(s).name)

        # Collect only images actually referenced in reading content (excludes deleted banners)
        all_staged_images: Dict[str, Tuple[Path, str]] = {}
        for filename in referenced_images_set:
            img_path = self.job_dir / "images" / filename
            if img_path.exists():
                epub_rel = f"images/{filename}"
                all_staged_images[epub_rel] = (img_path, self._guess_mime(img_path))

        # Step 3: Package the EPUB Book
        self._notify("Packaging EPUB", 0.85, "Creating EPUB package with metadata, navigation, and styles...")
        
        start_ch = format_chapter_display(completed_chapters[0].chapter_number)
        end_ch = format_chapter_display(completed_chapters[-1].chapter_number)
        book_title = f"Omniscient Reader's Viewpoint (Chapters {start_ch}-{end_ch})"

        book = epub.EpubBook()
        book.set_identifier(f"orv-flamecomics-{self.manifest.job_id}")
        book.set_title(book_title)
        book.set_language("en")
        book.add_author("Sing Shong")
        book.add_metadata("DC", "contributor", "WeTriedTLS / Flamecomics")

        # Cover Image Registration
        candidates = [
            Path("cover.webp"),
            Path(__file__).resolve().parent.parent / "cover.webp",
            Path("/orv-flamecomics/cover.webp"),
        ]
        cover_path = next((p for p in candidates if p.exists()), None)
        if not cover_path:
            raise EpubConversionError(
                "Cover image not found at '/orv-flamecomics/cover.webp' or workspace root. "
                "Please ensure cover.webp exists."
            )

        with open(cover_path, "rb") as f:
            cover_data = f.read()
        book.set_cover("images/cover.webp", cover_data, create_page=False)
        cover_item = book.get_item_with_id("cover-img")
        if cover_item:
            cover_item.media_type = "image/webp"

        # Add CSS
        style_item = epub.EpubItem(
            uid="style_default",
            file_name="style/default.css",
            media_type="text/css",
            content=DEFAULT_EPUB_CSS.encode("utf-8"),
        )
        book.add_item(style_item)

        # Add Embedded Staged Images
        for epub_path, (src_file, mime) in all_staged_images.items():
            with open(src_file, "rb") as f:
                img_data = f.read()
            img_item = epub.EpubItem(
                uid=f"img_{Path(epub_path).stem}",
                file_name=epub_path,
                media_type=mime,
                content=img_data,
            )
            book.add_item(img_item)

        # Create Dedicated Title Page as First Reading Item
        title_page_item = self._create_title_page(style_item, start_ch, end_ch)
        book.add_item(title_page_item)

        # Add XHTML Chapters
        epub_chapters: List[epub.EpubHtml] = []
        staged_chapter_data: List[Tuple[float, str, epub.EpubHtml]] = []

        for num, title, xhtml_path in staged_items:
            with open(xhtml_path, "r", encoding="utf-8") as f:
                content = f.read()

            content = self._normalize_for_ebooklib(content, title)
            num_str = format_chapter_display(num)
            c_item = epub.EpubHtml(
                title=title,
                file_name=f"ch_{num_str}.xhtml",
                lang="en",
                content=content,
            )
            c_item.add_item(style_item)
            book.add_item(c_item)
            epub_chapters.append(c_item)
            staged_chapter_data.append((num, title, c_item))

        # Build Nested Episode-Wise Table of Contents
        episode_groups: Dict[str, List[epub.EpubHtml]] = {}
        for num, title, c_item in staged_chapter_data:
            ep_name = extract_episode_group(title)
            if ep_name not in episode_groups:
                episode_groups[ep_name] = []
            episode_groups[ep_name].append(c_item)

        toc_items = [title_page_item]
        for ep_name, group_chapters in episode_groups.items():
            sec = epub.Section(ep_name, href=group_chapters[0].file_name)
            toc_items.append((sec, tuple(group_chapters)))

        book.toc = tuple(toc_items)
        book.add_item(epub.EpubNcx())
        book.add_item(epub.EpubNav())

        # Spine: Title page is first reading document, followed by TOC ('nav') and chapters
        book.spine = [title_page_item, "nav"] + epub_chapters

        # Output File paths
        epub_filename = f"ORV_Ch{start_ch}-{end_ch}.epub"
        final_epub_path = self.output_dir / epub_filename
        temp_epub_path = self.output_dir / f"{epub_filename}.tmp"

        self._notify("Writing File", 0.95, f"Writing EPUB to temporary file: {temp_epub_path.name}")
        epub.write_epub(str(temp_epub_path), book, {})

        # Step 4: Verify generated EPUB package
        self._verify_epub_file(temp_epub_path)

        # Atomically publish final EPUB
        safe_replace(temp_epub_path, final_epub_path)

        rel_output_path = f"output/{epub_filename}"
        self.manifest.conversion["status"] = "completed"
        self.manifest.conversion["epub_path"] = rel_output_path
        self.manifest.conversion["error"] = None
        self.manifest.status = "completed"
        self.manifest.current_phase = "idle"
        self.manifest.save()

        self._notify("Complete", 1.0, f"EPUB successfully generated: {final_epub_path.name}")
        return final_epub_path

    def _stage_chapter(self, ch, target_xhtml_path: Path) -> None:
        """
        Normalize raw chapter HTML into strict, valid markup for EPUB packaging.
        Applies refine_chapter_html to remove recurring banners, clean credits,
        style system windows, and format interactive footnotes.
        """
        html_file = self.job_dir / ch.html_path
        if not html_file.exists():
            raise EpubConversionError(f"Chapter file {ch.html_path} missing on disk")

        with open(html_file, "r", encoding="utf-8") as f:
            raw_html = f.read()

        # Step 1: Second-Stage Formatting Pass
        refined_html = refine_chapter_html(raw_html, ch.title, ch.chapter_number, self.job_dir)

        soup = BeautifulSoup(refined_html, "html.parser")
        body_div = soup.find(class_="chapter-body") or soup.find("article") or soup.body or soup

        # Unwrap any redundant wrapper containers inside chapter-body
        for wrapper in body_div.find_all(["main", "section"]):
            if "chapter-footnotes" not in wrapper.get("class", []):
                wrapper.unwrap()

        # Step 2: Rewrite local image paths to EPUB relative structure 'images/filename'
        for img in body_div.find_all("img"):
            src = img.get("src", "")
            clean_name = Path(src).name
            img["src"] = f"images/{clean_name}"
            if not img.get("alt"):
                img["alt"] = f"Illustration for {ch.title}"

        # Step 3: Clean up non-XHTML tags
        for unwanted in body_div.find_all(["script", "style", "meta", "link", "input", "button"]):
            unwanted.decompose()

        # Step 4: Build clean markup with single authoritative heading followed by body
        xhtml_content = f"""<!DOCTYPE html>
<html>
<head>
    <title>{escape(ch.title)}</title>
    <link rel="stylesheet" type="text/css" href="style/default.css" />
</head>
<body>
    <h1 class="chapter-title">{escape(ch.title)}</h1>
    <div class="chapter-content">
{body_div.decode_contents()}
    </div>
</body>
</html>
"""
        with open(target_xhtml_path, "w", encoding="utf-8") as f:
            f.write(xhtml_content)

    def _create_title_page(self, style_item: epub.EpubItem, start_ch: str = "", end_ch: str = "") -> epub.EpubHtml:
        """Create a dedicated, clean, centered Title Page as the first reading document."""
        range_html = f'<p class="title-page-range">Chapters {escape(start_ch)}–{escape(end_ch)}</p>' if start_ch and end_ch else ""
        title_page_html = f"""<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" lang="en">
<head>
    <title>Title Page</title>
    <link rel="stylesheet" type="text/css" href="style/default.css" />
</head>
<body class="title-page-body">
    <div class="title-page-container">
        <h1 class="title-page-title">Omniscient Reader’s Viewpoint</h1>
        {range_html}
        <div class="title-page-divider"></div>
        <p class="title-page-author">Original novel by Sing Shong</p>
        <p class="title-page-translator">Translation by WeTriedTLS / Flamecomics</p>
    </div>
</body>
</html>
"""
        item = epub.EpubHtml(
            uid="title_page",
            title="Title Page",
            file_name="title_page.xhtml",
            lang="en",
            content=title_page_html,
        )
        item.add_item(style_item)
        return item

    @staticmethod
    def _normalize_for_ebooklib(content: str, title: str) -> str:
        """Ensure content has standard <body> tags without xml declaration/namespaces that confuse EbookLib's body extractor."""
        clean = re.sub(r'<\?xml[^>]*\?>', '', content, flags=re.IGNORECASE).strip()
        clean = re.sub(r'<html[^>]*>', '<html>', clean, flags=re.IGNORECASE)
        if "<body" not in clean.lower():
            clean = f"<html><head><title>{escape(title)}</title></head><body>{clean}</body></html>"
        return clean

    @staticmethod
    def _verify_epub_file(path: Path) -> None:
        """Verify that output file is a valid non-empty EPUB ZIP archive."""
        if not path.exists() or path.stat().st_size < 100:
            raise EpubConversionError("Generated EPUB file is missing or virtually empty")

        try:
            with zipfile.ZipFile(path, "r") as z:
                names = z.namelist()
                if "mimetype" not in names:
                    raise EpubConversionError("EPUB archive missing required 'mimetype' file")
                if not any("container.xml" in n for n in names):
                    raise EpubConversionError("EPUB archive missing META-INF/container.xml")
        except zipfile.BadZipFile as e:
            raise EpubConversionError(f"EPUB file is not a valid zip archive: {e}")

    @staticmethod
    def _guess_mime(path: Path) -> str:
        ext = path.suffix.lower()
        if ext == ".png":
            return "image/png"
        elif ext in [".jpg", ".jpeg"]:
            return "image/jpeg"
        elif ext == ".gif":
            return "image/gif"
        elif ext == ".webp":
            return "image/webp"
        return "application/octet-stream"
