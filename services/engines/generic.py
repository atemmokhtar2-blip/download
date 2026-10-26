from __future__ import annotations

import os
from typing import Callable

from config.settings import AUDIO_BITRATE, TEMP_DIR
from utils.helpers import sanitize_filename

from services.engines.base import PlatformEngine
from utils.logger import download_logger, error_logger

class YtDlpFallbackEngine(PlatformEngine):
    """Lightweight named engine for platforms handled through yt-dlp fallback.

    Some supported platforms do not need a bespoke scraper yet, but routing them
    through a named engine improves diagnostics, dashboard visibility, and future
    extension points while preserving the robust generic yt-dlp path.
    """

    platform_label = "Generic"
    engine_profile = "yt-dlp-platform-fallback"
    techniques = ("yt-dlp generic extractor",)
    capabilities = frozenset({"video", "audio", "image", "album"})

    async def analyze(self, url: str) -> dict | None:
        from services import downloader

        fallback = await downloader._generic_analyze_url(url)
        if fallback:
            fallback["platform"] = self.platform_label
            fallback["engine_profile"] = self.engine_profile
        return self._tag(fallback)

class EmbedFallbackEngine(YtDlpFallbackEngine):
    """Named engine for platforms without a bespoke scraper.

    Runs a generic/dedicated direct resolver first (og:video / JSON-LD embeds,
    or a platform resolver such as Rumble/Dailymotion), downloads the best
    candidate through the central audio guard, then falls back to yt-dlp. Any
    silent or unverifiable direct file is dropped, so a delivery is either
    audited or never happens.
    """

    resolver_name = "resolve_embed_media"
    embed_headers: dict = {}
    techniques = (
        "direct resolver (og:video / JSON-LD / native API)",
        "CDN candidate walk (mp4 + HLS)",
        "yt-dlp fallback",
    )
    capabilities = frozenset({"video", "audio", "image", "album"})

    def _resolve(self, url: str):
        from services import light_scrapers

        resolver = getattr(light_scrapers, self.resolver_name, None) or light_scrapers.resolve_embed_media
        if self.resolver_name == "resolve_embed_media":
            return resolver(url, extra_headers=self.embed_headers or None)
        return resolver(url)

    async def analyze(self, url: str) -> dict | None:
        from middlewares.concurrency import get_executor
        import asyncio

        loop = asyncio.get_running_loop()
        try:
            meta = await loop.run_in_executor(get_executor(), self._resolve, url)
            if meta:
                meta["platform"] = self.platform_label
                meta["engine_profile"] = meta.get("engine_profile") or "direct-resolver+yt-dlp-fallback"
                return self._tag(meta)
        except Exception as exc:
            error_logger.error("%s direct resolve failed: %s", self.name, exc)
        return await super().analyze(url)

    async def download_video(
        self,
        url: str,
        format_id: str,
        quality_label: str,
        progress_callback: Callable | None = None,
        play_url: str | None = None,
    ) -> str | None:
        from services import downloader
        from middlewares.concurrency import get_executor
        import asyncio

        loop = asyncio.get_running_loop()
        candidates: list[str] = []
        resolved_url = url
        try:
            meta = await loop.run_in_executor(get_executor(), self._resolve, url)
            if meta:
                resolved_url = meta.get("url") or url
                candidates = list(meta.get("video_candidates") or [])
                if meta.get("play_url"):
                    candidates.insert(0, meta["play_url"])
        except Exception as exc:
            error_logger.error("%s resolve before download failed: %s", self.name, exc)

        seen: set[str] = set()
        for idx, candidate in enumerate(candidates, 1):
            candidate = (candidate or "").strip()
            if not candidate.startswith("http") or candidate in seen:
                continue
            seen.add(candidate)
            try:
                if ".m3u8" in candidate:
                    path = await loop.run_in_executor(
                        get_executor(),
                        downloader.download_hls_mp4,
                        candidate,
                        f"xdl-{self.platform_label.lower()}",
                        dict(self.embed_headers or {}),
                    )
                else:
                    path = await loop.run_in_executor(
                        get_executor(),
                        downloader._download_direct_sync,
                        candidate,
                        dict(self.embed_headers or {}),
                        60,
                        progress_callback,
                        loop,
                        f"xdl-{self.platform_label.lower()}-{idx}",
                    )
                path = await downloader.prepare_video_async(path, source=f"{self.name}:direct")
                if path:
                    return path
                download_logger.info("%s direct candidate failed/was silent idx=%s", self.name, idx)
            except downloader.FileTooLargeError:
                raise
            except downloader.DownloadCancelledError:
                raise
            except Exception as exc:
                error_logger.error("%s direct candidate failed idx=%s: %s", self.name, idx, exc)

        fallback_play = candidates[0] if candidates else play_url
        return await downloader._generic_download_video(resolved_url, format_id, quality_label, progress_callback, fallback_play)

class AudioFallbackEngine(YtDlpFallbackEngine):
    """Base for audio-only platforms (Mixcloud, Bandcamp, Audiomack, Deezer, ...).

    These sites expose no og:video/JSON-LD/video-embed player, so running the
    video embed resolver is pure wasted work (and can occasionally mis-classify
    a track page as video). This base inherits the yt-dlp-fallback path directly
    — its ``analyze`` and ``download_audio`` go straight to yt-dlp's own
    per-platform audio extractor, which IS the correct distinct technique for
    each site — and advertises audio-only capabilities so the dashboard and
    quality gate never offer a phantom "📹 video" button for a song.
    """

    techniques = ("yt-dlp native audio extractor (per-platform)",)
    capabilities = frozenset({"audio"})

    async def download_audio(self, url: str, progress_callback: Callable | None = None) -> str | None:
        from services import downloader
        return await downloader._generic_download_audio(url, progress_callback)

class GenericEngine(YtDlpFallbackEngine):
    name = "GenericEngine"
    techniques = ("yt-dlp",)
    capabilities = frozenset({"video", "audio", "image", "album"})
    domains = ()
    platform_label = "Generic"

class UniversalEngine(YtDlpFallbackEngine):
    """Catch-all engine for ANY site yt-dlp can extract (1000+ platforms).

    This is the routing fallback: when no bespoke platform engine matches, the
    universal engine owns the URL and runs the bullet-proof generic yt-dlp path.
    """
    name = "UniversalEngine"
    techniques = ("yt-dlp universal (1000+ sites, Chrome TLS impersonation)",)
    capabilities = frozenset({"video", "audio", "image", "album"})
    platform_label = "Universal"
    engine_profile = "yt-dlp-universal-fallback"
    domains = ()

