"""Persistent, atomic, and thread-safe JSON manifest management for archival jobs."""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "1.0.0"


def safe_replace(src: Path, dst: Path, retries: int = 12, initial_delay: float = 0.04) -> None:
    """
    Safely replace dst with src on Windows and POSIX.
    Handles transient [WinError 5] Access Denied or [WinError 32] Sharing Violations
    caused by concurrent readers, file watchers (Streamlit/watchdog), or antivirus scanners.
    """
    for attempt in range(retries):
        try:
            src.replace(dst)
            return
        except (PermissionError, OSError) as e:
            if attempt == retries - 1:
                try:
                    shutil.copy2(src, dst)
                    src.unlink(missing_ok=True)
                    return
                except Exception:
                    raise e
            time.sleep(initial_delay * (attempt + 1))


def utc_now_iso() -> str:
    """Return current UTC timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ImageRecord:
    """Archival status of an individual image asset."""
    index: int
    source_url: str
    local_path: str  # relative to job dir (e.g., 'images/ch_1_0.png')
    status: str = "pending"  # pending, completed, failed
    mime_type: Optional[str] = None
    error: Optional[str] = None
    retry_count: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> ImageRecord:
        return cls(
            index=int(data.get("index", 0)),
            source_url=str(data.get("source_url", "")),
            local_path=str(data.get("local_path", "")),
            status=str(data.get("status", "pending")),
            mime_type=data.get("mime_type"),
            error=data.get("error"),
            retry_count=int(data.get("retry_count", 0)),
        )


@dataclass
class ChapterRecord:
    """Archival record for a single chapter."""
    chapter_number: float
    discovery_index: int
    title: str
    source_url: str
    next_url: Optional[str] = None
    status: str = "pending"  # pending, in_progress, completed, failed, paused
    html_path: Optional[str] = None  # relative to job dir (e.g. 'chapters/chapter_1.html')
    images: List[ImageRecord] = field(default_factory=list)
    error: Optional[str] = None
    retrieved_at: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "chapter_number": self.chapter_number,
            "discovery_index": self.discovery_index,
            "title": self.title,
            "source_url": self.source_url,
            "next_url": self.next_url,
            "status": self.status,
            "html_path": self.html_path,
            "images": [img.to_dict() for img in self.images],
            "error": self.error,
            "retrieved_at": self.retrieved_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ChapterRecord:
        imgs = [ImageRecord.from_dict(d) for d in data.get("images", [])]
        return cls(
            chapter_number=float(data.get("chapter_number", 0.0)),
            discovery_index=int(data.get("discovery_index", 0)),
            title=str(data.get("title", "")),
            source_url=str(data.get("source_url", "")),
            next_url=data.get("next_url"),
            status=str(data.get("status", "pending")),
            html_path=data.get("html_path"),
            images=imgs,
            error=data.get("error"),
            retrieved_at=data.get("retrieved_at"),
        )


class Manifest:
    """
    Manages atomic persistence and in-memory representation of job manifest.json.
    """

    def __init__(
        self,
        job_dir: Path,
        job_id: str,
        job_name: str,
        starting_url: str,
        requested_ending_chapter: float,
        detected_starting_chapter: Optional[float] = None,
    ):
        self.job_dir = Path(job_dir).resolve()
        self.job_id = job_id
        self.job_name = job_name
        self.starting_url = starting_url
        self.requested_ending_chapter = requested_ending_chapter
        self.detected_starting_chapter = detected_starting_chapter
        self.schema_version = SCHEMA_VERSION
        self.created_at = utc_now_iso()
        self.updated_at = self.created_at
        self.current_phase = "idle"  # idle, archival, conversion
        self.status = "pending"  # pending, in_progress, completed, failed, paused
        self.chapters: List[ChapterRecord] = []
        self.conversion: Dict[str, Any] = {
            "status": "pending",
            "staged_chapters": [],
            "epub_path": None,
            "error": None,
        }
        self.last_checkpoint: str = "initialized"
        self._lock = threading.Lock()

    @property
    def manifest_path(self) -> Path:
        return self.job_dir / "manifest.json"

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "job_id": self.job_id,
            "job_name": self.job_name,
            "starting_url": self.starting_url,
            "requested_ending_chapter": self.requested_ending_chapter,
            "detected_starting_chapter": self.detected_starting_chapter,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "current_phase": self.current_phase,
            "status": self.status,
            "last_checkpoint": self.last_checkpoint,
            "conversion": self.conversion,
            "chapters": [ch.to_dict() for ch in self.chapters],
        }

    def save(self) -> None:
        """
        Thread-safe, atomic write of manifest to disk.
        Writes to .tmp file first, then atomically replaces manifest.json with retry logic.
        """
        with self._lock:
            self.updated_at = utc_now_iso()
            self.job_dir.mkdir(parents=True, exist_ok=True)
            tmp_path = self.manifest_path.with_suffix(".json.tmp")
            data = self.to_dict()
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            
            safe_replace(tmp_path, self.manifest_path)

    @classmethod
    def load(cls, job_dir: Path) -> Manifest:
        """
        Load and validate manifest from job_dir/manifest.json.
        Retries on Windows if the file is momentarily locked during atomic replace.
        """
        job_dir = Path(job_dir).resolve()
        manifest_file = job_dir / "manifest.json"
        if not manifest_file.exists():
            raise FileNotFoundError(f"Manifest not found at {manifest_file}")

        data = None
        for attempt in range(10):
            try:
                with open(manifest_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                break
            except (PermissionError, json.JSONDecodeError, OSError):
                if attempt == 9:
                    raise
                time.sleep(0.04 * (attempt + 1))

        manifest = cls(
            job_dir=job_dir,
            job_id=data.get("job_id", job_dir.name),
            job_name=data.get("job_name", job_dir.name),
            starting_url=data.get("starting_url", ""),
            requested_ending_chapter=float(data.get("requested_ending_chapter", 0.0)),
            detected_starting_chapter=(
                float(data["detected_starting_chapter"])
                if data.get("detected_starting_chapter") is not None
                else None
            ),
        )
        manifest.schema_version = data.get("schema_version", SCHEMA_VERSION)
        manifest.created_at = data.get("created_at", utc_now_iso())
        manifest.updated_at = data.get("updated_at", manifest.created_at)
        manifest.current_phase = data.get("current_phase", "idle")
        manifest.status = data.get("status", "pending")
        manifest.last_checkpoint = data.get("last_checkpoint", "loaded")
        manifest.conversion = data.get("conversion", {
            "status": "pending",
            "staged_chapters": [],
            "epub_path": None,
            "error": None,
        })
        manifest.chapters = [ChapterRecord.from_dict(c) for c in data.get("chapters", [])]
        
        # Reconcile disk state after loading to recover safely from any ungraceful shutdowns
        manifest.reconcile_state_from_disk()
        return manifest

    def reconcile_state_from_disk(self) -> None:
        """
        Reconstruct and reconcile state from disk after application restart or crash.
        Ensures any items left in_progress are verified against actual files.
        """
        with self._lock:
            # Reconcile chapters
            for ch in self.chapters:
                if ch.status == "in_progress":
                    # Check if HTML file exists and has content
                    if ch.html_path:
                        html_full = self.job_dir / ch.html_path
                        if html_full.exists() and html_full.stat().st_size > 50:
                            # Verify images
                            all_imgs_ok = all(
                                (self.job_dir / img.local_path).exists()
                                for img in ch.images
                                if img.status == "completed" and img.local_path
                            )
                            if all_imgs_ok:
                                ch.status = "completed"
                            else:
                                ch.status = "failed"
                                ch.error = "Interrupted during image archival"
                        else:
                            ch.status = "pending"
                            ch.error = "Interrupted before chapter HTML finished writing"
                    else:
                        ch.status = "pending"

                elif ch.status == "completed":
                    # Verify file actually exists on disk
                    if ch.html_path:
                        html_full = self.job_dir / ch.html_path
                        if not html_full.exists() or html_full.stat().st_size == 0:
                            ch.status = "failed"
                            ch.error = "Archived HTML file missing or empty on disk"

            # Reconcile overall job status
            if self.status == "in_progress":
                self.status = "paused"
                self.last_checkpoint = "recovered_after_interruption"

            if self.conversion.get("status") == "in_progress":
                self.conversion["status"] = "paused"

    def get_chapter(self, chapter_number: float) -> Optional[ChapterRecord]:
        """Find chapter record by chapter number."""
        with self._lock:
            for ch in self.chapters:
                if ch.chapter_number == chapter_number:
                    return ch
        return None

    def add_or_update_chapter(self, chapter: ChapterRecord) -> None:
        """Add or update chapter record in discovery order."""
        with self._lock:
            for idx, existing in enumerate(self.chapters):
                if existing.chapter_number == chapter.chapter_number:
                    self.chapters[idx] = chapter
                    return
            self.chapters.append(chapter)

    def get_stats(self) -> dict:
        """Compute live counts of completed, pending, and failed chapters/assets."""
        with self._lock:
            total_chapters = len(self.chapters)
            completed_chapters = sum(1 for c in self.chapters if c.status == "completed")
            failed_chapters = sum(1 for c in self.chapters if c.status == "failed")
            pending_chapters = sum(1 for c in self.chapters if c.status == "pending")

            total_images = sum(len(c.images) for c in self.chapters)
            completed_images = sum(sum(1 for img in c.images if img.status == "completed") for c in self.chapters)
            failed_images = sum(sum(1 for img in c.images if img.status == "failed") for c in self.chapters)

            return {
                "total_chapters": total_chapters,
                "completed_chapters": completed_chapters,
                "failed_chapters": failed_chapters,
                "pending_chapters": pending_chapters,
                "total_images": total_images,
                "completed_images": completed_images,
                "failed_images": failed_images,
                "epub_ready": (
                    self.conversion.get("status") == "completed"
                    and bool(self.conversion.get("epub_path"))
                    and (self.job_dir / self.conversion["epub_path"]).exists()
                ),
            }
