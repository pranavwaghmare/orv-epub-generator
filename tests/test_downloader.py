"""Tests for Downloader retry logic, rate limiting, and asset validation."""

from pathlib import Path
import pytest
import requests
from unittest.mock import MagicMock, patch

from src.downloader import (
    Downloader,
    HttpStatusError,
    TransientNetworkError,
)


def test_downloader_transient_retry_success():
    downloader = Downloader(request_delay=0.0, max_retries=3, backoff_factor=0.01)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = "<html><body><p>Recovered</p></body></html>"

    with patch.object(downloader.session, "get") as mock_get:
        # Fail once with Timeout, then succeed
        mock_get.side_effect = [requests.Timeout("Connection timed out"), mock_resp]
        html = downloader.fetch_html("https://example.com/ch1")
        assert "Recovered" in html
        assert mock_get.call_count == 2


def test_downloader_non_retryable_404():
    downloader = Downloader(request_delay=0.0, max_retries=3, backoff_factor=0.01)

    mock_resp = MagicMock()
    mock_resp.status_code = 404

    with patch.object(downloader.session, "get") as mock_get:
        mock_get.return_value = mock_resp
        with pytest.raises(HttpStatusError) as exc_info:
            downloader.fetch_html("https://example.com/missing")
        assert exc_info.value.status_code == 404
        # Should not retry 404 repeatedly
        assert mock_get.call_count == 1


def test_download_image_base64(tmp_path: Path, tiny_png_b64: str):
    downloader = Downloader(request_delay=0.0)
    dest = tmp_path / "images" / "test_inline.png"

    ok, err, mime = downloader.download_image(tiny_png_b64, dest)
    assert ok is True
    assert err is None
    assert mime == "image/png"
    assert dest.exists()
    assert dest.stat().st_size > 0


def test_download_image_corrupted(tmp_path: Path):
    downloader = Downloader(request_delay=0.0)
    dest = tmp_path / "corrupted.png"
    corrupt_b64 = "data:image/png;base64,bm90YW5pbWFnZQ=="  # "notanimage"

    ok, err, mime = downloader.download_image(corrupt_b64, dest)
    assert ok is False
    assert "Invalid image" in err
    assert not dest.exists()
