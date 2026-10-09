"""Robust HTTP retrieval, rate-limiting, retry handling, and asset downloading."""

from __future__ import annotations

import base64
import io
import logging
import mimetypes
import re
import time
from pathlib import Path
from typing import Optional, Tuple
from urllib.parse import urljoin

import requests
from PIL import Image

from src.manifest import safe_replace

logger = logging.getLogger(__name__)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Image magic bytes signatures for validation
IMAGE_SIGNATURES = {
    b"\x89PNG\r\n\x1a\n": "image/png",
    b"\xff\xd8\xff": "image/jpeg",
    b"GIF87a": "image/gif",
    b"GIF89a": "image/gif",
    b"RIFF": "image/webp",  # WebP starts with RIFF....WEBP
    b"\x00\x00\x01\x00": "image/x-icon",
}


class DownloadError(Exception):
    """Base class for download exceptions."""
    pass


class TransientNetworkError(DownloadError):
    """Network connection timeout or reset."""
    pass


class HttpStatusError(DownloadError):
    """HTTP error response (4xx or 5xx)."""
    def __init__(self, status_code: int, message: str):
        super().__init__(f"HTTP {status_code}: {message}")
        self.status_code = status_code


class InvalidAssetError(DownloadError):
    """Data received is corrupted or not a valid image."""
    pass


class Downloader:
    """
    Manages HTTP sessions, polite rate-limiting, retries,
    and binary image downloads.
    """

    def __init__(
        self,
        request_delay: float = 0.5,
        default_timeout: int = 15,
        max_retries: int = 3,
        backoff_factor: float = 1.0,
    ):
        self.request_delay = request_delay
        self.default_timeout = default_timeout
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor
        self._last_request_time: float = 0.0
        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)

    def _apply_rate_limit(self) -> None:
        """Enforce polite rate limiting delay between consecutive requests."""
        if self.request_delay > 0:
            elapsed = time.time() - self._last_request_time
            if elapsed < self.request_delay:
                time.sleep(self.request_delay - elapsed)
        self._last_request_time = time.time()

    def fetch_html(self, url: str) -> str:
        """
        Fetch HTML from URL with bounded exponential retries.
        Distinguishes transient errors from permanent HTTP errors.
        """
        last_error: Optional[Exception] = None

        for attempt in range(1, self.max_retries + 1):
            try:
                self._apply_rate_limit()
                logger.debug("Fetching %s (attempt %d/%d)", url, attempt, self.max_retries)
                response = self.session.get(url, timeout=self.default_timeout)

                if response.status_code == 200:
                    text = response.text
                    if not text or not text.strip():
                        raise InvalidAssetError("Empty HTML response received")
                    return text

                if response.status_code in [404, 403, 401]:
                    # Permanent client error
                    raise HttpStatusError(response.status_code, f"Non-retryable status for {url}")
                
                # 5xx or 429: transient server/rate-limit error, retry
                raise HttpStatusError(response.status_code, f"Server returned {response.status_code}")

            except (requests.ConnectionError, requests.Timeout) as e:
                last_error = TransientNetworkError(f"Connection failure fetching {url}: {e}")
                logger.warning("Transient error on attempt %d: %s", attempt, e)
            except HttpStatusError as e:
                last_error = e
                if e.status_code in [404, 403, 401]:
                    raise
                logger.warning("HTTP status error on attempt %d: %s", attempt, e)
            except Exception as e:
                last_error = e
                logger.warning("Unexpected error fetching %s: %s", url, e)

            if attempt < self.max_retries:
                sleep_sec = self.backoff_factor * (2 ** (attempt - 1))
                time.sleep(sleep_sec)

        raise last_error or DownloadError(f"Failed to fetch {url} after {self.max_retries} attempts")

    def download_image(
        self,
        source: str,
        dest_path: Path,
        base_url: str = "",
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Download or decode an image and save it to dest_path.
        Supports:
          - data:image/...;base64,... URIs
          - absolute HTTP/HTTPS URLs
          - relative URLs (resolved via base_url)
        Validates image data using PIL.
        Returns: (success, error_message, detected_mime_type)
        """
        try:
            dest_path.parent.mkdir(parents=True, exist_ok=True)

            # Case A: Data URI (base64)
            if source.startswith("data:image/"):
                match = re.match(r"^data:(image/[a-zA-Z0-9\+\-\.]+);base64,(.+)$", source, re.DOTALL)
                if not match:
                    return False, "Malformed base64 data URI", None
                
                mime_type = match.group(1)
                b64_data = match.group(2).strip()
                try:
                    image_bytes = base64.b64decode(b64_data)
                except Exception as e:
                    return False, f"Failed to decode base64: {e}", None

                return self._validate_and_save_image(image_bytes, dest_path, mime_type)

            # Case B: HTTP / HTTPS / Relative URL
            resolved_url = urljoin(base_url, source) if base_url else source
            if not (resolved_url.startswith("http://") or resolved_url.startswith("https://")):
                return False, f"Invalid image URL scheme: {resolved_url}", None

            image_bytes = self._fetch_binary_with_retry(resolved_url)
            return self._validate_and_save_image(image_bytes, dest_path)

        except Exception as e:
            logger.error("Error downloading image %s: %s", source[:60], e)
            return False, str(e), None

    def _fetch_binary_with_retry(self, url: str) -> bytes:
        """Fetch binary image bytes with retries and timeout."""
        last_error = None
        for attempt in range(1, self.max_retries + 1):
            try:
                self._apply_rate_limit()
                resp = self.session.get(url, timeout=self.default_timeout)
                if resp.status_code == 200:
                    data = resp.content
                    if not data:
                        raise InvalidAssetError("Zero bytes received for image")
                    return data
                raise HttpStatusError(resp.status_code, f"Status {resp.status_code} for image {url}")
            except (requests.ConnectionError, requests.Timeout) as e:
                last_error = TransientNetworkError(f"Network error on image {url}: {e}")
            except HttpStatusError as e:
                last_error = e
                if e.status_code in [404, 403]:
                    raise
            except Exception as e:
                last_error = e

            if attempt < self.max_retries:
                time.sleep(self.backoff_factor * (2 ** (attempt - 1)))

        raise last_error or DownloadError(f"Failed to download image {url}")

    def _validate_and_save_image(
        self,
        image_bytes: bytes,
        dest_path: Path,
        mime_hint: Optional[str] = None,
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """Validate image data with PIL and save to disk."""
        if len(image_bytes) < 8:
            return False, "Image data too small to be valid", None

        # Verify using Pillow
        try:
            with Image.open(io.BytesIO(image_bytes)) as img:
                img.verify()
                format_name = (img.format or "").lower()
                mime_type = mime_hint or f"image/{format_name}"
        except Exception as e:
            # Fallback signature check for WebP/PNG/JPEG
            detected = self._detect_mime_from_bytes(image_bytes)
            if detected:
                mime_type = detected
            else:
                return False, f"Invalid image bytes: {e}", None

        # Atomic write to temporary file then replace
        temp_dest = dest_path.with_suffix(dest_path.suffix + ".tmp")
        try:
            with open(temp_dest, "wb") as f:
                f.write(image_bytes)
            safe_replace(temp_dest, dest_path)
            return True, None, mime_type
        except Exception as e:
            if temp_dest.exists():
                temp_dest.unlink(missing_ok=True)
            return False, f"Failed saving image file: {e}", None

    @staticmethod
    def _detect_mime_from_bytes(data: bytes) -> Optional[str]:
        """Detect MIME type from file header bytes."""
        for sig, mime in IMAGE_SIGNATURES.items():
            if data.startswith(sig):
                if sig == b"RIFF" and b"WEBP" in data[:16]:
                    return "image/webp"
                elif sig != b"RIFF":
                    return mime
        return None
