"""Job manager: handles background thread worker, state synchronization, and job lifecycle."""

from __future__ import annotations

import logging
import re
import shutil
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from src.archive_validator import validate_archive, ValidationResult
from src.downloader import Downloader
from src.epub_converter import EpubConverter
from src.manifest import Manifest
from src.parser import parse_chapter_html, format_chapter_display
from src.scraper import Archiver

logger = logging.getLogger(__name__)


@dataclass
class JobState:
    """Thread-safe snapshot of live worker progress."""
    job_id: Optional[str] = None
    is_running: bool = False
    current_phase: str = "idle"  # idle, archival, conversion, retry
    current_op: str = "Idle"
    progress_ratio: float = 0.0
    current_chapter: Optional[float] = None
    current_title: str = ""
    logs: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


class JobManager:
    """
    Singleton-style coordinator managing active threads,
    job directories, and progress updates.
    """

    def __init__(self, base_jobs_dir: Path = Path("data/jobs")):
        self.base_jobs_dir = Path(base_jobs_dir).resolve()
        self.base_jobs_dir.mkdir(parents=True, exist_ok=True)

        self._lock = threading.Lock()
        self._pause_event = threading.Event()
        self._worker_thread: Optional[threading.Thread] = None
        self._state = JobState()

    def get_state(self) -> JobState:
        with self._lock:
            # Return copy of state
            return JobState(
                job_id=self._state.job_id,
                is_running=self._state.is_running,
                current_phase=self._state.current_phase,
                current_op=self._state.current_op,
                progress_ratio=self._state.progress_ratio,
                current_chapter=self._state.current_chapter,
                current_title=self._state.current_title,
                logs=list(self._state.logs),
                errors=list(self._state.errors),
            )

    def is_busy(self) -> bool:
        with self._lock:
            return self._state.is_running and self._worker_thread is not None and self._worker_thread.is_alive()

    def list_jobs(self) -> List[Manifest]:
        """Scan jobs directory and load all valid job manifests."""
        jobs: List[Manifest] = []
        if not self.base_jobs_dir.exists():
            return jobs

        for entry in self.base_jobs_dir.iterdir():
            if entry.is_dir() and (entry / "manifest.json").exists():
                try:
                    manifest = Manifest.load(entry)
                    jobs.append(manifest)
                except Exception as e:
                    logger.error("Failed loading manifest in %s: %s", entry, e)

        # Sort by updated_at descending
        jobs.sort(key=lambda m: m.updated_at, reverse=True)
        return jobs

    def get_job(self, job_id: str) -> Optional[Manifest]:
        """Load specific job by job_id."""
        job_dir = self.base_jobs_dir / job_id
        if job_dir.exists() and (job_dir / "manifest.json").exists():
            return Manifest.load(job_dir)
        return None

    def inspect_starting_url(self, url: str) -> Tuple[Optional[float], str, Optional[str]]:
        """
        Pre-flight probe to inspect starting URL, validate connectivity,
        and extract starting chapter number and title without starting a job.
        Returns: (chapter_number, title, error_message)
        """
        try:
            downloader = Downloader(request_delay=0.0, default_timeout=12, max_retries=2)
            html = downloader.fetch_html(url)
            parsed = parse_chapter_html(html, url)
            return parsed.chapter_number, parsed.title, None
        except Exception as e:
            return None, "", str(e)

    def create_job(
        self,
        starting_url: str,
        ending_chapter: float,
        detected_starting_chapter: Optional[float] = None,
        job_name: Optional[str] = None,
    ) -> Manifest:
        """Create a new isolated job directory and initialize its manifest."""
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        
        # Clean slug
        start_str = format_chapter_display(detected_starting_chapter) if detected_starting_chapter is not None else "start"
        end_str = format_chapter_display(ending_chapter)
        clean_name = re.sub(r'[^a-zA-Z0-9_\-]', '_', (job_name or f"ORV_Ch{start_str}-{end_str}")).strip("_")
        job_id = f"{timestamp}_{clean_name}"

        job_dir = self.base_jobs_dir / job_id
        job_dir.mkdir(parents=True, exist_ok=True)

        manifest = Manifest(
            job_dir=job_dir,
            job_id=job_id,
            job_name=job_name or f"ORV Chapters {start_str}-{end_str}",
            starting_url=starting_url,
            requested_ending_chapter=ending_chapter,
            detected_starting_chapter=detected_starting_chapter,
        )
        manifest.save()
        return manifest

    def start_archival(self, job_id: str) -> bool:
        """Launch archival in non-blocking background thread."""
        if self.is_busy():
            return False

        manifest = self.get_job(job_id)
        if not manifest:
            return False

        self._pause_event.clear()
        with self._lock:
            self._state = JobState(
                job_id=job_id,
                is_running=True,
                current_phase="archival",
                current_op="Starting Archival...",
                logs=[f"[{self._time_str()}] Archival process initiated."],
            )

        def worker():
            try:
                archiver = Archiver(
                    manifest=manifest,
                    on_progress=self._on_worker_progress,
                    should_pause=self._pause_event.is_set,
                )
                archiver.run()
            except Exception as e:
                logger.exception("Archival worker encountered an error")
                with self._lock:
                    self._state.errors.append(f"Archival failed: {e}")
                    self._state.logs.append(f"[{self._time_str()}] Archival failed: {e}")
            finally:
                with self._lock:
                    self._state.is_running = False
                    self._state.current_op = "Completed" if manifest.status == "completed" else manifest.status.capitalize()
                    self._state.logs.append(f"[{self._time_str()}] Worker finished. Status: {manifest.status}")

        self._worker_thread = threading.Thread(target=worker, daemon=True)
        self._worker_thread.start()
        return True

    def retry_failed(self, job_id: str) -> bool:
        """Launch retry of failed items in background thread."""
        if self.is_busy():
            return False

        manifest = self.get_job(job_id)
        if not manifest:
            return False

        self._pause_event.clear()
        with self._lock:
            self._state = JobState(
                job_id=job_id,
                is_running=True,
                current_phase="retry",
                current_op="Retrying Failed Items...",
                logs=[f"[{self._time_str()}] Retrying failed chapters and assets."],
            )

        def worker():
            try:
                archiver = Archiver(
                    manifest=manifest,
                    on_progress=self._on_worker_progress,
                    should_pause=self._pause_event.is_set,
                )
                archiver.retry_failed()
            except Exception as e:
                logger.exception("Retry worker encountered an error")
                with self._lock:
                    self._state.errors.append(f"Retry error: {e}")
                    self._state.logs.append(f"[{self._time_str()}] Retry error: {e}")
            finally:
                with self._lock:
                    self._state.is_running = False
                    self._state.current_op = "Finished"

        self._worker_thread = threading.Thread(target=worker, daemon=True)
        self._worker_thread.start()
        return True

    def start_conversion(
        self,
        job_id: str,
        force_override: bool = False,
        force_restage: bool = False,
    ) -> bool:
        """Launch offline EPUB conversion in background thread."""
        if self.is_busy():
            return False

        manifest = self.get_job(job_id)
        if not manifest:
            return False

        self._pause_event.clear()
        with self._lock:
            self._state = JobState(
                job_id=job_id,
                is_running=True,
                current_phase="conversion",
                current_op="Starting EPUB Conversion...",
                logs=[f"[{self._time_str()}] EPUB conversion initiated."],
            )

        def worker():
            try:
                converter = EpubConverter(
                    manifest=manifest,
                    on_progress=self._on_worker_progress,
                    should_pause=self._pause_event.is_set,
                )
                epub_path = converter.convert(force_override=force_override, force_restage=force_restage)
                with self._lock:
                    self._state.logs.append(f"[{self._time_str()}] EPUB generated successfully: {epub_path.name}")
            except Exception as e:
                logger.exception("EPUB conversion error")
                with self._lock:
                    self._state.errors.append(f"EPUB conversion error: {e}")
                    self._state.logs.append(f"[{self._time_str()}] Conversion error: {e}")
            finally:
                with self._lock:
                    self._state.is_running = False
                    self._state.current_op = "Finished"

        self._worker_thread = threading.Thread(target=worker, daemon=True)
        self._worker_thread.start()
        return True

    def request_pause(self) -> None:
        """Signal cooperative pause to active worker."""
        self._pause_event.set()
        with self._lock:
            self._state.logs.append(f"[{self._time_str()}] Pause requested. Waiting for current checkpoint...")

    def _on_worker_progress(self, op: str, ratio: float, log_msg: str) -> None:
        """Callback invoked by workers to report live progress."""
        with self._lock:
            self._state.current_op = op
            self._state.progress_ratio = max(0.0, min(1.0, ratio))
            log_line = f"[{self._time_str()}] {log_msg}"
            self._state.logs.append(log_line)
            # Cap log buffer to recent 200 items
            if len(self._state.logs) > 200:
                self._state.logs = self._state.logs[-200:]

    def delete_job(self, job_id: str) -> bool:
        """Permanently delete a job directory and its contents from disk."""
        with self._lock:
            if self._state.is_running and self._state.job_id == job_id:
                return False

        job_dir = self.base_jobs_dir / job_id
        if job_dir.exists() and job_dir.is_dir():
            shutil.rmtree(job_dir, ignore_errors=True)
            return True
        return False

    @staticmethod
    def _time_str() -> str:
        return datetime.now().strftime("%H:%M:%S")
