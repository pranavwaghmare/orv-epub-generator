"""Resumable EPUB generation from local archive using EbookLib."""

from __future__ import annotations

import io
import logging
import mimetypes
import shutil
import zipfile
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple
from xml.sax.saxutils import escape

from bs4 import BeautifulSoup
import ebooklib
from ebooklib import epub

from src.archive_validator import validate_archive, ValidationResult
from src.manifest import Manifest, safe_replace
from src.parser import format_chapter_display

logger = logging.getLogger(__name__)

DEFAULT_EPUB_CSS = """/* Clean, portable e-reader styling for ORV */
@namespace epub "http://www.idpf.org/2007/ops";

body {
    font-family: Georgia, serif;
    line-height: 1.6;
    margin: 5%;
    padding: 0;
    color: #1a1a1a;
}

h1.chapter-title {
    text-align: center;
    margin-top: 1em;
    margin-bottom: 1.5em;
    font-size: 1.5em;
    line-height: 1.3;
    font-weight: bold;
    border-bottom: 1px solid #ddd;
    padding-bottom: 0.5em;
}

p {
    margin-top: 0;
    margin-bottom: 0.85em;
    text-indent: 0;
}

img {
    max-width: 100%;
    height: auto;
    display: block;
    margin: 1.5em auto;
}

hr {
    border: none;
    border-top: 1px solid #ccc;
    margin: 2em auto;
    width: 60%;
}

blockquote {
    border-left: 3px solid #666;
    margin: 1em 0;
    padding-left: 1em;
    font-style: italic;
    color: #444;
}
"""


class EpubConversionError(Exception):
    """EPUB conversion failure."""
    pass


class EpubConverter:
    """
    Handles resumable EPUB conversion completely offline from local archives.
    Uses intermediate XHTML staging to support pausing and resumption.
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

    def convert(self, force_override: bool = False) -> Path:
        """
        Convert local archive to an EPUB file.
        force_override: Allows generating even if archive validation detected missing chapters or assets.
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

        self.manifest.current_phase = "conversion"
        self.manifest.conversion["status"] = "in_progress"
        self.manifest.save()

        # Step 2: Resumable Staging of Chapters to XHTML
        completed_chapters = [c for c in self.manifest.chapters if c.status == "completed"]
        completed_chapters.sort(key=lambda c: c.chapter_number)

        staged_nums = set(self.manifest.conversion.get("staged_chapters", []))
        total_chapters = len(completed_chapters)

        staged_items: List[Tuple[float, str, Path]] = []  # (num, title, staged_xhtml_path)
        all_staged_images: Dict[str, Tuple[Path, str]] = {}  # epup_rel_path -> (source_path, mime_type)

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
                self._notify(f"Staging Ch {num_str}", ratio, f"Normalizing and staging Chapter {num_str}: {ch.title}")
                self._stage_chapter(ch, staged_file)
                staged_nums.add(ch.chapter_number)
                self.manifest.conversion["staged_chapters"] = sorted(list(staged_nums))
                self.manifest.save()

            staged_items.append((ch.chapter_number, ch.title, staged_file))

            # Collect referenced images
            for img in ch.images:
                if img.status == "completed" and img.local_path:
                    src_img_file = self.job_dir / img.local_path
                    if src_img_file.exists():
                        epub_img_name = f"images/{src_img_file.name}"
                        mime = img.mime_type or self._guess_mime(src_img_file)
                        all_staged_images[epub_img_name] = (src_img_file, mime)

        # Step 3: Package the EPUB Book
        self._notify("Packaging EPUB", 0.85, "Creating EPUB package with metadata, navigation, and styles...")
        
        start_ch = format_chapter_display(completed_chapters[0].chapter_number)
        end_ch = format_chapter_display(completed_chapters[-1].chapter_number)
        book_title = f"Omniscient Reader's Viewpoint (Chapters {start_ch}-{end_ch})"

        book = epub.EpubBook()
        book.set_identifier(f"orv-flamecomics-{self.manifest.job_id}")
        book.set_title(book_title)
        book.set_language("en")
        book.add_author("sing N song")
        book.add_metadata("DC", "contributor", "WeTriedTLS / Flamecomics")

        # Add CSS
        style_item = epub.EpubItem(
            uid="style_default",
            file_name="style/default.css",
            media_type="text/css",
            content=DEFAULT_EPUB_CSS.encode("utf-8"),
        )
        book.add_item(style_item)

        # Add Images
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

        # Add XHTML Chapters
        epub_chapters: List[epub.EpubHtml] = []
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

        # Build Navigation Table of Contents and Spine
        book.toc = tuple(epub_chapters)
        book.add_item(epub.EpubNcx())
        book.add_item(epub.EpubNav())
        book.spine = ["nav"] + epub_chapters

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
        Adjusts image src to point to 'images/<name>'.
        """
        html_file = self.job_dir / ch.html_path
        if not html_file.exists():
            raise EpubConversionError(f"Chapter file {ch.html_path} missing on disk")

        with open(html_file, "r", encoding="utf-8") as f:
            raw_html = f.read()

        soup = BeautifulSoup(raw_html, "html.parser")
        body_div = soup.find(class_="chapter-body") or soup.find("article") or soup.body or soup

        # Rewrite local image paths to EPUB relative structure 'images/filename'
        for img in body_div.find_all("img"):
            src = img.get("src", "")
            clean_name = Path(src).name
            img["src"] = f"images/{clean_name}"
            if not img.get("alt"):
                img["alt"] = f"Illustration for {ch.title}"

        # Clean up any non-XHTML tags
        for unwanted in body_div.find_all(["script", "style", "meta", "link", "input", "button"]):
            unwanted.decompose()

        # Build clean markup without XML declaration so EbookLib parser functions seamlessly
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

    @staticmethod
    def _normalize_for_ebooklib(content: str, title: str) -> str:
        """Ensure content has standard <body> tags without xml declaration/namespaces that confuse EbookLib's body extractor."""
        import re
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
