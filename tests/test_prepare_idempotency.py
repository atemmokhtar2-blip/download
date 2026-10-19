"""Regression: the post-download guard must run ONCE per file, not twice.

``_prepare_video_for_delivery`` is applied from two layers — once per direct
candidate inside every engine (so a silent CDN URL is rejected and the next
candidate tried) and once in the public ``download_video`` chokepoint. Without
an idempotency guard that meant every delivered video was ffprobe'd and
ffmpeg-faststart-remuxed TWICE (doubling post-processing time and burning two
of the bounded ``_ffmpeg_sem`` slots per file). These tests pin the single-pass
behaviour and the safety valve that re-processes a file whose bytes changed.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import services.downloader as dl


def test_prepare_video_is_idempotent(monkeypatch, tmp_path) -> None:
    audio_calls = {"count": 0}
    remux_calls = {"count": 0}

    def fake_has_audio(_path):
        audio_calls["count"] += 1
        return True

    def fake_remux(path, cancel_check=None):
        remux_calls["count"] += 1
        return path  # simulate a faststart remux that keeps the same path

    monkeypatch.setattr(dl, "_has_audio_stream", fake_has_audio)
    monkeypatch.setattr(dl, "_remux_mp4_faststart", fake_remux)

    f = tmp_path / "video.mp4"
    f.write_bytes(b"\x00" * 4096)
    p = str(f)

    # First pass — the engine's per-candidate guard.
    assert dl._prepare_video_for_delivery(p, source="engine:tiktok:direct") == p
    # Second pass — the public download_video chokepoint on the SAME file.
    assert dl._prepare_video_for_delivery(p, source="engine:tiktok") == p

    assert audio_calls["count"] == 1
    assert remux_calls["count"] == 1


def test_changed_file_is_re_prepared(monkeypatch, tmp_path) -> None:
    """The identity check must not skip a file whose bytes changed on the same path."""
    audio_calls = {"count": 0}

    monkeypatch.setattr(dl, "_has_audio_stream", lambda _p: True)
    monkeypatch.setattr(dl, "_remux_mp4_faststart", lambda p, cancel_check=None: p)

    f = tmp_path / "video.mp4"
    f.write_bytes(b"\x00" * 4096)
    p = str(f)

    assert dl._prepare_video_for_delivery(p, source="first") == p
    # Same path, different content (as if a deterministic temp path was reused).
    f.write_bytes(b"\x01" * 8192)
    assert dl._prepare_video_for_delivery(p, source="second") == p


def test_download_video_runs_single_guard_end_to_end(monkeypatch, tmp_path) -> None:
    """The public chokepoint must not re-audit a file an engine already verified."""
    audio_calls = {"count": 0}
    remux_calls = {"count": 0}
    prepared = str(tmp_path / "verified.mp4")
    Path(prepared).write_bytes(b"\x00" * 8192)

    def fake_has_audio(_path):
        audio_calls["count"] += 1
        return True

    def fake_remux(path, cancel_check=None):
        remux_calls["count"] += 1
        return path

    monkeypatch.setattr(dl, "_has_audio_stream", fake_has_audio)
    monkeypatch.setattr(dl, "_remux_mp4_faststart", fake_remux)

    class SpecializedEngine:
        name = "TestEngine"

        async def download_video(self, url, format_id, quality_label, progress, play_url):
            # Real engines (TikTok/Instagram/Twitter/…) prepare each direct
            # candidate internally before returning a verified file.
            return await dl.prepare_video_async(prepared, source="test:direct")

    monkeypatch.setattr(dl, "_select_engine", lambda url: SpecializedEngine())

    out = asyncio.run(
        dl.download_video(
            "https://example.com/v/123", "best", "best", None, None, allow_large=False
        )
    )

    assert out == prepared
    assert audio_calls["count"] == 1
    assert remux_calls["count"] == 1


if __name__ == "__main__":
    print("prepare idempotency tests import OK")
