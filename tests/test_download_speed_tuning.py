"""Tests for the advanced download-speed tuning layer.

Guards the four throughput optimizations:
  1. Fast-fail socket timeout + bounded yt-dlp retry backoff in ``_base_opts``.
  2. Higher parallel fragment concurrency + larger HTTP chunk size.
  3. Parallel album/carousel downloads (bounded, order-preserving).
  4. Temp-file cleanup when a parallel album item fails.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

import services.downloader as downloader  # noqa: E402
from config import settings  # noqa: E402


def test_base_opts_speed_tuning():
    opts = downloader._base_opts()

    assert opts["socket_timeout"] == settings.DOWNLOAD_SOCKET_TIMEOUT
    assert opts["concurrent_fragment_downloads"] == settings.MAX_FRAGMENT_CONCURRENCY
    assert opts["http_chunk_size"] == settings.HTTP_CHUNK_SIZE_MB * 1024 * 1024

    sleep_fns = opts["retry_sleep_functions"]
    for key in ("http", "extractor", "fragment", "file_access"):
        assert key in sleep_fns, f"missing retry_sleep_functions key: {key}"
    # Backoff must stay bounded so a failing CDN never stalls a download slot.
    for n in range(1, 12):
        assert sleep_fns["http"](n) <= 3.0
        assert sleep_fns["extractor"](n) <= 3.0
        assert sleep_fns["fragment"](n) <= 2.05
        assert sleep_fns["file_access"](n) <= 2.05


class _StaticProgress:
    """Async progress callable with a cancel_check attribute."""

    def __init__(self):
        self.cancel_check = None
        self.updates = []

    async def __call__(self, payload):
        self.updates.append(payload)


def test_download_album_parallel_and_ordered(monkeypatch):
    in_flight = 0
    max_in_flight = 0

    async def fake_video(url, *a, **k):
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0.05)
        in_flight -= 1
        return f"/tmp/{url}.mp4"

    async def fake_image(url, *a, **k):
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0.05)
        in_flight -= 1
        return f"/tmp/{url}.jpg"

    monkeypatch.setattr(downloader, "download_video", fake_video)
    monkeypatch.setattr(downloader, "download_image", fake_image)

    items = [
        {"type": "image", "url": "a", "title": "A"},
        {"type": "video", "url": "b", "title": "B"},
        {"type": "image", "url": "c", "title": "C"},
        {"type": "video", "url": "d", "title": "D"},
        {"type": "image", "url": "e", "title": "E"},
        {"type": "video", "url": "f", "title": "F"},
    ]
    progress = _StaticProgress()

    out = asyncio.run(downloader.download_album(items, progress))

    assert len(out) == 6
    assert [entry["title"] for entry in out] == ["A", "B", "C", "D", "E", "F"]
    # Concurrency actually happened (more than one item in flight at once).
    assert max_in_flight >= 2
    # Every item reported its original X/N index exactly once.
    assert sorted(p["album_index"] for p in progress.updates) == [1, 2, 3, 4, 5, 6]


def test_download_album_error_cleanup(monkeypatch):
    removed: list = []
    monkeypatch.setattr(downloader, "_safe_remove", lambda path: removed.append(path))

    async def good(url, *a, **k):
        await asyncio.sleep(0.01)
        return f"/tmp/{url}.ok"

    async def bad(url, *a, **k):
        await asyncio.sleep(0.01)
        raise RuntimeError("boom")

    monkeypatch.setattr(downloader, "download_image", good)
    monkeypatch.setattr(downloader, "download_video", bad)

    items = [
        {"type": "image", "url": "A", "title": "A"},
        {"type": "video", "url": "B", "title": "B"},
        {"type": "video", "url": "C", "title": "C"},
    ]

    with pytest.raises(RuntimeError):
        asyncio.run(downloader.download_album(items, _StaticProgress()))

    # The successful item's temp file must be cleaned up when the album aborts.
    assert "/tmp/A.ok" in removed
