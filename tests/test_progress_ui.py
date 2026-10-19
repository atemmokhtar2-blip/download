"""Tests for the real progress UI + cancel-button lifecycle.

Covers the four requirements the progress refactor promised:
  1. Real percentage/size/speed/ETA (including unknown-size streams).
  2. The 🛑 button survives quiet mode and is removed on terminal cards.
  3. Distinct phase messages (downloading -> processing -> uploading -> sent).
  4. Cancel semantics: flag check, end-of-download drop, stale-press isolation.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from handlers.download import (  # noqa: E402
    _ProgressUI,
    _format_eta,
    _final_edit,
    EMPTY_MARKUP,
)
from middlewares.cancellation import (  # noqa: E402
    DownloadCancelledError,
    request_cancel,
    clear_cancel,
)
from services.downloader import _rate_metrics  # noqa: E402


class _Recorder:
    """Bare stand-in for a bound ``message.edit_*`` method."""

    def __init__(self):
        self.calls = []

    async def __call__(self, *args, **kwargs):
        self.calls.append({"args": args, "kwargs": kwargs})
        return None


def _run(coro):
    return asyncio.run(coro)


def _make_ui(show_progress=True):
    rec = _Recorder()
    ui = _ProgressUI(None, rec, "MARKUP", "en", 999, show_progress=show_progress)
    return ui, rec


# ---------------------------------------------------------------- ETA format
def test_format_eta_values() -> None:
    assert _format_eta(None) == "—"
    assert _format_eta(0) == "—"
    assert _format_eta(-5) == "—"
    assert _format_eta(42) == "42s"
    assert _format_eta(65) == "1:05"
    assert _format_eta(3725) == "1:02:05"


# ------------------------------------------------------------- lenient parse
def test_parse_accepts_all_engine_shapes() -> None:
    full = _ProgressUI._parse({"pct": 50, "downloaded": 100, "total": 200,
                               "speed": 25, "eta": 4})
    assert full["pct"] == 50.0
    assert full["downloaded"] == 100
    assert full["total"] == 200
    assert full["speed"] == 25.0
    assert full["eta"] == 4

    partial = _ProgressUI._parse({"pct": 70})
    assert partial["pct"] == 70.0
    assert partial["downloaded"] == 0
    assert partial["total"] == 0

    nums = _ProgressUI._parse({"pct": "80", "downloaded": "1024", "total": "2048"})
    assert nums["pct"] == 80.0
    assert nums["downloaded"] == 1024
    assert nums["total"] == 2048

    bare_int = _ProgressUI._parse(35)
    assert bare_int["pct"] == 35.0
    assert _ProgressUI._parse("not-a-number") is None


# -------------------------------------------------------- real progress card
def test_real_progress_line_contains_sizes_speed_eta() -> None:
    ui, rec = _make_ui()
    ui.start_download()
    _run(ui({"pct": 50, "downloaded": 100 * 1024 * 1024,
             "total": 200 * 1024 * 1024, "speed": 1024 * 1024, "eta": 100}))

    text = rec.calls[0]["args"][0]
    assert "50%" in text
    assert "100.0 MB" in text
    assert "200.0 MB" in text
    assert "1.0 MB/s" in text
    assert "1:40" in text          # 100s as human ETA
    assert rec.calls[0]["kwargs"]["reply_markup"] == "MARKUP"


def test_unknown_total_shows_size_not_fake_percent() -> None:
    ui, rec = _make_ui()
    ui.start_download()
    _run(ui({"pct": 0, "downloaded": 5 * 1024 * 1024,
             "total": 0, "speed": 512 * 1024, "eta": 0}))

    text = rec.calls[0]["args"][0]
    assert "غير معروف" in text
    assert "5.0 MB" in text
    assert "512.0 KB/s" in text
    assert "%" not in text        # never fake a percentage bar for unknown size


def test_100_percent_becomes_processing_not_delivery_done() -> None:
    ui, rec = _make_ui()
    ui.start_download()
    _run(ui({"pct": 100, "downloaded": 2048, "total": 2048,
             "speed": 0, "eta": 0}))

    text = rec.calls[0]["args"][0]
    assert "المعالجة" in text
    assert "100%" not in text


# ---------------------------------------------------------- edit throttling
def test_progress_edit_is_throttled() -> None:
    ui, rec = _make_ui()
    ui.start_download()
    _run(ui({"pct": 10, "downloaded": 10, "total": 100, "speed": 1, "eta": 90}))
    _run(ui({"pct": 20, "downloaded": 20, "total": 100, "speed": 1, "eta": 80}))
    assert len(rec.calls) == 1


def test_distinct_phase_signatures_are_not_throttled() -> None:
    ui, rec = _make_ui()
    _run(ui.status("preparing", signature="preparing"))
    _run(ui.status("queue", signature="queue"))
    _run(ui.status("uploading", signature="uploading"))
    assert len(rec.calls) == 3


# ----------------------------------------------- phase gating vs late events
def test_end_download_drops_late_progress_but_keeps_status() -> None:
    ui, rec = _make_ui()
    ui.start_download()
    _run(ui({"pct": 45, "downloaded": 45, "total": 100, "speed": 1, "eta": 55}))
    assert len(rec.calls) == 1

    ui.end_download()
    _run(ui({"pct": 100, "downloaded": 100, "total": 100, "speed": 0, "eta": 0}))
    assert len(rec.calls) == 1          # late 100% dropped

    _run(ui.status("uploading", signature="uploading"))
    assert len(rec.calls) == 2          # essential phase still goes through


def test_finish_drops_everything() -> None:
    ui, rec = _make_ui()
    ui.start_download()
    ui.finish()
    _run(ui({"pct": 50, "downloaded": 1, "total": 2}))
    _run(ui.status("uploading", signature="uploading"))
    assert rec.calls == []


# ------------------------------------------------------- quiet-mode semantics
def test_quiet_mode_hides_percent_but_keeps_cancel_on_phases() -> None:
    ui, rec = _make_ui(show_progress=False)
    ui.start_download()
    _run(ui({"pct": 50, "downloaded": 1, "total": 2}))
    assert rec.calls == []

    _run(ui.status("preparing", signature="preparing"))
    assert len(rec.calls) == 1
    assert rec.calls[0]["kwargs"]["reply_markup"] == "MARKUP"


# ----------------------------------------------------------- cancel plumbing
def test_cancel_check_raises_only_when_flagged() -> None:
    ui, _rec = _make_ui()
    ui.cancel_check()  # no flag -> no raise

    request_cancel(999)
    try:
        with pytest.raises(DownloadCancelledError):
            ui.cancel_check()
    finally:
        clear_cancel(999)


def test_rate_metrics_speed_and_eta() -> None:
    speed, eta = _rate_metrics(1024, 2048, 2.0)
    assert speed == 512.0
    assert eta == 2

    speed, eta = _rate_metrics(1024, 0, 2.0)   # unknown total -> no ETA
    assert speed == 512.0
    assert eta == 0

    speed, eta = _rate_metrics(1024, 2048, 0)  # zero elapsed -> clamped, no ZeroDivisionError
    assert speed > 0


# --------------------------------------------------- terminal cards drop 🛑
def test_final_edit_removes_cancel_markup() -> None:
    rec = _Recorder()
    _run(_final_edit(rec, "done"))
    assert rec.calls[0]["kwargs"]["parse_mode"] == "HTML"
    assert rec.calls[0]["kwargs"]["reply_markup"] is EMPTY_MARKUP


# ---------------------------------------------- image download cancel check
class _FakeImageResponse:
    headers = {"Content-Type": "image/jpeg", "Content-Length": "2000000"}

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size):
        yield b"x" * 300000
        yield b"y" * 300000

    def close(self):
        pass


class _CancellingProgress:
    def cancel_check(self):
        raise DownloadCancelledError("user pressed stop")


def test_download_image_stops_and_cleans_on_cancel(monkeypatch, tmp_path) -> None:
    import services.downloader as dl

    monkeypatch.setattr("utils.url_safety.is_public_url", lambda *_a, **_k: (True, None))
    monkeypatch.setattr("services.downloader.tls.browser_headers", lambda: {})
    monkeypatch.setattr("services.downloader.tls.get", lambda *_a, **_k: _FakeImageResponse())
    monkeypatch.setattr("services.downloader.TEMP_DIR", str(tmp_path))

    with pytest.raises(DownloadCancelledError):
        asyncio.run(dl.download_image("https://cdn.example/img.jpg", None, _CancellingProgress()))

    assert list(tmp_path.iterdir()) == []
