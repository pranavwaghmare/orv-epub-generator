"""Archival pipeline: dynamic chapter discovery, validation, asset downloading, and recovery."""

from __future__ import annotations

import logging
import mimetypes
from pathlib import Path
from typing import Callable, Dict, Optional, Set
from urllib.parse import urlparse

from src.chapter_refiner import is_known_recurring_banner
from src.downloader import Downloader, DownloadError
from src.manifest import ChapterRecord, ImageRecord, Manifest, utc_now_iso, safe_replace
from src.parser import (
    build_standalone_html,
    format_chapter_display,
    parse_chapter_html,
    rewrite_html_image_srcs,
)

logger = logging.getLogger(__name__)


class ArchivalError(Exception):
    """Archival pipeline error."""
    pass


class Archiver:
    """
    Coordinates dynamic chapter retrieval, content extraction,
    image downloading, and persistent manifest checkpointing.
    """

    def __init__(
        self,
        manifest: Manifest,
        downloader: Optional[Downloader] = None,
        on_progress: Optional[Callable[[str, float, str], None]] = None,
        should_pause: Optional[Callable[[], bool]] = None,
        skip_translator_banners: bool = True,
    ):
        self.manifest = manifest
        self.downloader = downloader or Downloader()
        self.on_progress = on_progress or (lambda op, ratio, log: None)
        self.should_pause = should_pause or (lambda: False)
        self.skip_translator_banners = skip_translator_banners

        # Setup directories in job folder
        self.job_dir = manifest.job_dir
        self.chapters_dir = self.job_dir / "chapters"
        self.images_dir = self.job_dir / "images"
        self.chapters_dir.mkdir(parents=True, exist_ok=True)
        self.images_dir.mkdir(parents=True, exist_ok=True)

    def _notify(self, op: str, ratio: float, log_msg: str) -> None:
        logger.info(log_msg)
        self.on_progress(op, ratio, log_msg)

    def run(self) -> None:
        """
        Execute the archival process from starting URL to requested ending chapter.
        Supports resume without re-downloading completed chapters.
        """
        self.manifest.current_phase = "archival"
        self.manifest.status = "in_progress"
        self.manifest.save()

        visited_urls: Set[str] = set()
        seen_chapter_numbers: Set[float] = set()

        # Reconstruct visited URLs & chapters from existing manifest
        next_to_fetch: Optional[str] = None
        for ch in self.manifest.chapters:
            visited_urls.add(ch.source_url)
            seen_chapter_numbers.add(ch.chapter_number)
            if ch.status == "completed" and ch.next_url:
                next_to_fetch = ch.next_url

        # Determine where to begin
        current_url: str = next_to_fetch or self.manifest.starting_url
        discovery_index = len(self.manifest.chapters)

        self._notify("Starting", 0.0, f"Beginning archival from URL: {current_url}")

        while current_url:
            # 1. Check for pause request
            if self.should_pause():
                self._notify("Paused", 0.0, "Pause acknowledged by worker. Saving progress.")
                self.manifest.status = "paused"
                self.manifest.save()
                return

            # 2. Check for navigation loop
            if current_url in visited_urls:
                # If this chapter was already completed, we can safely advance
                existing = next((c for c in self.manifest.chapters if c.source_url == current_url), None)
                if existing and existing.status == "completed":
                    if existing.next_url and existing.next_url not in visited_urls:
                        current_url = existing.next_url
                        continue
                    else:
                        self._notify("Navigation loop", 1.0, f"Navigation loop detected at {current_url}. Stopping.")
                        break
                else:
                    self._notify("Loop detected", 1.0, f"Loop detected: already visited {current_url}. Stopping.")
                    break

            visited_urls.add(current_url)

            # 3. Check if already completed on disk
            existing_record = next((c for c in self.manifest.chapters if c.source_url == current_url), None)
            if existing_record and existing_record.status == "completed":
                self._notify(
                    f"Reusing Chapter {format_chapter_display(existing_record.chapter_number)}",
                    self._calc_progress(existing_record.chapter_number),
                    f"Reusing already completed Chapter {format_chapter_display(existing_record.chapter_number)}",
                )
                if existing_record.chapter_number >= self.manifest.requested_ending_chapter:
                    self._notify("Target reached", 1.0, f"Reached requested target chapter {self.manifest.requested_ending_chapter}.")
                    break
                if not existing_record.next_url:
                    self._notify("End of chapters", 1.0, "No next chapter URL available.")
                    break
                current_url = existing_record.next_url
                continue

            # 4. Fetch HTML
            self._notify("Fetching HTML", 0.0, f"Fetching chapter from {current_url}")
            try:
                html = self.downloader.fetch_html(current_url)
            except Exception as e:
                err_msg = f"Failed to retrieve HTML for {current_url}: {e}"
                logger.error(err_msg)
                self._record_chapter_failure(current_url, discovery_index, err_msg)
                break

            # 5. Parse Chapter
            try:
                parsed = parse_chapter_html(html, current_url)
            except Exception as e:
                err_msg = f"Parsing failed for {current_url}: {e}"
                logger.error(err_msg)
                self._record_chapter_failure(current_url, discovery_index, err_msg)
                break

            # Validate chapter number
            if self.manifest.detected_starting_chapter is None:
                self.manifest.detected_starting_chapter = parsed.chapter_number
                self.manifest.save()

            if parsed.chapter_number in seen_chapter_numbers:
                err_msg = f"Duplicate chapter number detected: {parsed.chapter_number} at {current_url}"
                self._record_chapter_failure(current_url, discovery_index, err_msg, parsed.chapter_number, parsed.title)
                break
            seen_chapter_numbers.add(parsed.chapter_number)

            progress_ratio = self._calc_progress(parsed.chapter_number)
            self._notify(
                f"Archiving Chapter {format_chapter_display(parsed.chapter_number)}",
                progress_ratio,
                f"Discovered {parsed.title} (Chapter {format_chapter_display(parsed.chapter_number)})",
            )

            # 6. Create chapter record in manifest (in_progress)
            chapter_rec = ChapterRecord(
                chapter_number=parsed.chapter_number,
                discovery_index=discovery_index,
                title=parsed.title,
                source_url=current_url,
                next_url=parsed.next_url,
                status="in_progress",
            )
            self.manifest.add_or_update_chapter(chapter_rec)
            self.manifest.save()

            # 7. Download and process images
            image_map: Dict[int, str] = {}
            for img_meta in parsed.images:
                if self.should_pause():
                    self.manifest.status = "paused"
                    self.manifest.save()
                    return

                # Local image path
                ext = ".png" if img_meta.is_base64 else self._guess_ext(img_meta.src)
                filename = f"ch_{format_chapter_display(parsed.chapter_number)}_{img_meta.index}{ext}"
                rel_path = f"images/{filename}"
                dest_file = self.job_dir / rel_path

                self._notify(
                    f"Downloading Image {img_meta.index + 1}/{len(parsed.images)}",
                    progress_ratio,
                    f"Downloading image {img_meta.index} for Chapter {format_chapter_display(parsed.chapter_number)}",
                )

                img_rec = ImageRecord(
                    index=img_meta.index,
                    source_url=img_meta.src[:200] if img_meta.is_base64 else img_meta.src,
                    local_path=rel_path,
                    status="pending",
                )

                if self.skip_translator_banners and is_known_recurring_banner(img_meta.src):
                    img_rec.status = "skipped_banner"
                    img_rec.error = "Identified as recurring translator banner; skipped download per archival configuration."
                    image_map[img_meta.index] = f"../{rel_path}"
                    self._notify(
                        f"Skip Banner {img_meta.index}",
                        progress_ratio,
                        f"Skipped downloading recurring translator banner #{img_meta.index} for Chapter {format_chapter_display(parsed.chapter_number)}"
                    )
                else:
                    ok, err, mime = self.downloader.download_image(img_meta.src, dest_file, base_url=current_url)
                    if ok:
                        img_rec.status = "completed"
                        img_rec.mime_type = mime
                        image_map[img_meta.index] = f"../{rel_path}"
                    else:
                        img_rec.status = "failed"
                        img_rec.error = err
                        logger.warning("Failed to download image %d for chapter %s: %s", img_meta.index, parsed.chapter_number, err)

                chapter_rec.images.append(img_rec)

            # 8. Rewrite image references in chapter HTML
            rewritten_content = rewrite_html_image_srcs(parsed.content_html, image_map)
            standalone_html = build_standalone_html(parsed.title, parsed.chapter_number, rewritten_content)

            # 9. Save HTML to disk
            html_filename = f"chapter_{format_chapter_display(parsed.chapter_number)}.html"
            rel_html_path = f"chapters/{html_filename}"
            html_dest = self.job_dir / rel_html_path

            tmp_html = html_dest.with_suffix(".html.tmp")
            with open(tmp_html, "w", encoding="utf-8") as f:
                f.write(standalone_html)
            safe_replace(tmp_html, html_dest)

            # 10. Finalize chapter record as completed
            chapter_rec.html_path = rel_html_path
            chapter_rec.status = "completed"
            chapter_rec.retrieved_at = utc_now_iso()
            self.manifest.last_checkpoint = f"archived_ch_{format_chapter_display(parsed.chapter_number)}"
            self.manifest.save()

            self._notify(
                f"Completed Chapter {format_chapter_display(parsed.chapter_number)}",
                self._calc_progress(parsed.chapter_number),
                f"Successfully archived Chapter {format_chapter_display(parsed.chapter_number)}: {parsed.title}",
            )

            # 11. Check termination condition
            if parsed.chapter_number >= self.manifest.requested_ending_chapter:
                self._notify("Finished", 1.0, f"Target chapter {self.manifest.requested_ending_chapter} reached. Archival complete!")
                self.manifest.status = "completed"
                self.manifest.save()
                break

            if not parsed.next_url:
                self._notify("No next link", 1.0, f"No next chapter link found on Chapter {parsed.chapter_number}. Stopping.")
                self.manifest.status = "completed" if parsed.chapter_number >= self.manifest.requested_ending_chapter else "paused"
                self.manifest.save()
                break

            current_url = parsed.next_url
            discovery_index += 1

    def retry_failed(self) -> None:
        """
        Retry only failed chapters and failed images without redoing completed chapters.
        """
        self.manifest.current_phase = "archival"
        self.manifest.status = "in_progress"
        self.manifest.save()

        # Step 1: Retry failed images in otherwise completed chapters
        for ch in self.manifest.chapters:
            if self.should_pause():
                self.manifest.status = "paused"
                self.manifest.save()
                return

            if ch.status == "completed":
                has_failed_img = any(img.status == "failed" for img in ch.images)
                if has_failed_img and ch.html_path:
                    html_file = self.job_dir / ch.html_path
                    if html_file.exists():
                        with open(html_file, "r", encoding="utf-8") as f:
                            content = f.read()

                        image_map: Dict[int, str] = {}
                        for img in ch.images:
                            if img.status == "failed":
                                self._notify("Retrying Image", 0.0, f"Retrying image #{img.index} in Chapter {ch.chapter_number}")
                                dest_file = self.job_dir / img.local_path
                                ok, err, mime = self.downloader.download_image(img.source_url, dest_file, base_url=ch.source_url)
                                if ok:
                                    img.status = "completed"
                                    img.mime_type = mime
                                    img.error = None
                                    image_map[img.index] = f"../{img.local_path}"
                                else:
                                    img.retry_count += 1
                                    img.error = err

                        if image_map:
                            updated_html = rewrite_html_image_srcs(content, image_map)
                            with open(html_file, "w", encoding="utf-8") as f:
                                f.write(updated_html)

                        self.manifest.save()

        # Step 2: Retry failed chapters
        failed_chapters = [c for c in self.manifest.chapters if c.status == "failed"]
        for ch in failed_chapters:
            if self.should_pause():
                self.manifest.status = "paused"
                self.manifest.save()
                return

            self._notify("Retrying Chapter", 0.0, f"Retrying Chapter {format_chapter_display(ch.chapter_number)}")
            try:
                html = self.downloader.fetch_html(ch.source_url)
                parsed = parse_chapter_html(html, ch.source_url)

                # Process images
                image_map = {}
                ch.images = []
                for img_meta in parsed.images:
                    ext = ".png" if img_meta.is_base64 else self._guess_ext(img_meta.src)
                    filename = f"ch_{format_chapter_display(parsed.chapter_number)}_{img_meta.index}{ext}"
                    rel_path = f"images/{filename}"
                    dest_file = self.job_dir / rel_path

                    img_rec = ImageRecord(
                        index=img_meta.index,
                        source_url=img_meta.src[:200] if img_meta.is_base64 else img_meta.src,
                        local_path=rel_path,
                        status="pending",
                    )
                    ok, err, mime = self.downloader.download_image(img_meta.src, dest_file, base_url=ch.source_url)
                    if ok:
                        img_rec.status = "completed"
                        img_rec.mime_type = mime
                        image_map[img_meta.index] = f"../{rel_path}"
                    else:
                        img_rec.status = "failed"
                        img_rec.error = err

                    ch.images.append(img_rec)

                # Save HTML
                rewritten_content = rewrite_html_image_srcs(parsed.content_html, image_map)
                standalone_html = build_standalone_html(parsed.title, parsed.chapter_number, rewritten_content)
                html_filename = f"chapter_{format_chapter_display(parsed.chapter_number)}.html"
                rel_html_path = f"chapters/{html_filename}"
                html_dest = self.job_dir / rel_html_path

                with open(html_dest, "w", encoding="utf-8") as f:
                    f.write(standalone_html)

                ch.html_path = rel_html_path
                ch.status = "completed"
                ch.error = None
                ch.retrieved_at = utc_now_iso()
                self.manifest.save()

            except Exception as e:
                ch.error = str(e)
                logger.error("Retry failed for chapter %s: %s", ch.chapter_number, e)
                self.manifest.save()

        # Check if remaining work exists
        stats = self.manifest.get_stats()
        if stats["failed_chapters"] == 0:
            self.manifest.status = "completed"
        else:
            self.manifest.status = "paused"
        self.manifest.save()
        self._notify("Retry complete", 1.0, f"Retry completed. Remaining failures: {stats['failed_chapters']}")

    def _calc_progress(self, current_ch: float) -> float:
        """Calculate progress ratio (0.0 to 1.0)."""
        start = self.manifest.detected_starting_chapter or 1.0
        end = self.manifest.requested_ending_chapter
        if end <= start:
            return 1.0
        return max(0.0, min(1.0, (current_ch - start + 1) / (end - start + 1)))

    def _record_chapter_failure(
        self,
        url: str,
        index: int,
        error_msg: str,
        chapter_num: Optional[float] = None,
        title: str = "Unknown",
    ) -> None:
        num = chapter_num if chapter_num is not None else float(index + 1)
        rec = ChapterRecord(
            chapter_number=num,
            discovery_index=index,
            title=title,
            source_url=url,
            status="failed",
            error=error_msg,
        )
        self.manifest.add_or_update_chapter(rec)
        self.manifest.status = "failed"
        self.manifest.save()
        self._notify("Failed", 0.0, f"Error: {error_msg}")

    @staticmethod
    def _guess_ext(url_or_src: str) -> str:
        parsed = urlparse(url_or_src)
        path = parsed.path
        ext = Path(path).suffix.lower()
        if ext in [".png", ".jpg", ".jpeg", ".gif", ".webp"]:
            return ext
        return ".png"
