"""Offline archive validator: verifies chapter continuity, files, and assets before EPUB conversion."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from src.manifest import Manifest
from src.parser import format_chapter_display

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    """Detailed results of archive validation."""
    is_valid: bool
    can_override: bool
    total_chapters_recorded: int
    completed_chapters: int
    failed_chapters: int
    missing_chapter_numbers: List[float] = field(default_factory=list)
    missing_html_files: List[str] = field(default_factory=list)
    missing_image_files: List[str] = field(default_factory=list)
    corrupt_or_empty_files: List[str] = field(default_factory=list)
    order_or_sequence_issues: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    detailed_report: List[str] = field(default_factory=list)

    @property
    def has_any_content(self) -> bool:
        return self.completed_chapters > 0


def validate_archive(manifest: Manifest) -> ValidationResult:
    """
    Validates the local archive without requiring external network connectivity.
    Checks:
      1. Chapter range completeness against requested target.
      2. Monotonic chapter ordering and absence of duplicates.
      3. HTML file readability and size (>0).
      4. Local image asset availability and readability.
      5. Any unresolved or failed records.
    """
    job_dir = manifest.job_dir
    chapters = manifest.chapters

    missing_chapters: List[float] = []
    missing_html: List[str] = []
    missing_images: List[str] = []
    corrupt_files: List[str] = []
    order_issues: List[str] = []
    warnings: List[str] = []
    detailed_report: List[str] = []

    start_num = manifest.detected_starting_chapter or (chapters[0].chapter_number if chapters else 1.0)
    end_num = manifest.requested_ending_chapter

    completed_count = 0
    failed_count = 0

    seen_numbers = set()
    prev_num: Optional[float] = None

    for idx, ch in enumerate(chapters):
        c_num = ch.chapter_number
        
        # Check duplicates
        if c_num in seen_numbers:
            msg = f"Duplicate chapter number detected: Chapter {format_chapter_display(c_num)}"
            order_issues.append(msg)
            detailed_report.append(f"❌ {msg}")
        seen_numbers.add(c_num)

        # Check sequence order
        if prev_num is not None and c_num <= prev_num:
            msg = f"Out-of-order chapter: Chapter {format_chapter_display(c_num)} follows Chapter {format_chapter_display(prev_num)}"
            order_issues.append(msg)
            detailed_report.append(f"⚠️ {msg}")
        elif prev_num is not None and (c_num - prev_num) > 1.0:
            msg = f"Gap detected between Chapter {format_chapter_display(prev_num)} and Chapter {format_chapter_display(c_num)}"
            warnings.append(msg)
            detailed_report.append(f"ℹ️ {msg}")

        prev_num = c_num

        # Check status
        if ch.status == "completed":
            completed_count += 1
            # Verify HTML file on disk
            if not ch.html_path:
                msg = f"Chapter {format_chapter_display(c_num)} marked completed but html_path is null"
                missing_html.append(msg)
                detailed_report.append(f"❌ {msg}")
            else:
                html_file = job_dir / ch.html_path
                if not html_file.exists():
                    msg = f"Chapter {format_chapter_display(c_num)} HTML file missing: {ch.html_path}"
                    missing_html.append(msg)
                    detailed_report.append(f"❌ {msg}")
                elif html_file.stat().st_size == 0:
                    msg = f"Chapter {format_chapter_display(c_num)} HTML file is empty: {ch.html_path}"
                    corrupt_files.append(msg)
                    detailed_report.append(f"❌ {msg}")

            # Verify images
            for img in ch.images:
                if img.status == "completed":
                    if not img.local_path:
                        msg = f"Chapter {format_chapter_display(c_num)} image #{img.index} local_path is null"
                        missing_images.append(msg)
                        detailed_report.append(f"❌ {msg}")
                    else:
                        img_file = job_dir / img.local_path
                        if not img_file.exists():
                            msg = f"Chapter {format_chapter_display(c_num)} image #{img.index} missing: {img.local_path}"
                            missing_images.append(msg)
                            detailed_report.append(f"❌ {msg}")
                        elif img_file.stat().st_size == 0:
                            msg = f"Chapter {format_chapter_display(c_num)} image #{img.index} is empty (0 bytes): {img.local_path}"
                            corrupt_files.append(msg)
                            detailed_report.append(f"❌ {msg}")
                elif img.status == "failed":
                    msg = f"Chapter {format_chapter_display(c_num)} image #{img.index} failed download: {img.error or 'unknown'}"
                    warnings.append(msg)
                    detailed_report.append(f"⚠️ {msg}")

        elif ch.status == "failed":
            failed_count += 1
            msg = f"Chapter {format_chapter_display(c_num)} marked as failed: {ch.error or 'unknown'}"
            detailed_report.append(f"❌ {msg}")
        else:
            msg = f"Chapter {format_chapter_display(c_num)} is incomplete (status={ch.status})"
            detailed_report.append(f"⚠️ {msg}")

    # Check if end chapter was reached
    if not chapters or (prev_num is not None and prev_num < end_num):
        last_found = prev_num if prev_num is not None else 0.0
        msg = f"Requested range incomplete: ended at Chapter {format_chapter_display(last_found)}, target was Chapter {format_chapter_display(end_num)}"
        warnings.append(msg)
        detailed_report.append(f"⚠️ {msg}")

    # Check for missing integer chapters in range
    if start_num <= end_num:
        curr = int(start_num)
        end_int = int(end_num)
        while curr <= end_int:
            if float(curr) not in seen_numbers:
                missing_chapters.append(float(curr))
            curr += 1

    is_valid = (
        len(missing_html) == 0
        and len(missing_images) == 0
        and len(corrupt_files) == 0
        and len(order_issues) == 0
        and failed_count == 0
        and completed_count > 0
        and len(missing_chapters) == 0
    )

    can_override = completed_count > 0

    return ValidationResult(
        is_valid=is_valid,
        can_override=can_override,
        total_chapters_recorded=len(chapters),
        completed_chapters=completed_count,
        failed_chapters=failed_count,
        missing_chapter_numbers=missing_chapters,
        missing_html_files=missing_html,
        missing_image_files=missing_images,
        corrupt_or_empty_files=corrupt_files,
        order_or_sequence_issues=order_issues,
        warnings=warnings,
        detailed_report=detailed_report,
    )
