from __future__ import annotations

from typing import Callable

from services.engines.base import PlatformEngine
from utils.logger import error_logger

class SoundCloudEngine(PlatformEngine):
    name = "SoundCloudEnginePro"
    techniques = (
        "short-link canonicalization",
        "audio-format ranking",
        "playlist diagnostics",
    )
    capabilities = frozenset({"audio"})
    domains = ("soundcloud.com", "m.soundcloud.com", "on.soundcloud.com")

    async def analyze(self, url: str) -> dict | None:
        """SoundCloud Pro analyzer: short-link normalization + audio ranking + playlist diagnostics."""
        from services import downloader
        from services.soundcloud_scraper import scrape_soundcloud
        from middlewares.concurrency import get_executor
        import asyncio

        loop = asyncio.get_running_loop()
        try:
            sc = await loop.run_in_executor(get_executor(), scrape_soundcloud, url)
            if sc and (sc.get("audio_formats") or sc.get("album_items") or sc.get("error_reason")):
                sc.setdefault("platform", "SoundCloud")
                sc["engine_profile"] = sc.get("engine_profile") or "yt-dlp-soundcloud-profiles+audio-ranking"
                return self._tag(sc)
        except Exception as exc:
            error_logger.error("SoundCloudEnginePro scraper analyze failed: %s", exc)

        fallback = await downloader._generic_analyze_url(url)
        if fallback:
            fallback["engine_profile"] = "yt-dlp-generic-fallback"
        return self._tag(fallback)

    async def download_audio(self, url: str, progress_callback: Callable | None = None) -> str | None:
        from services import downloader
        return await downloader._generic_download_audio(url, progress_callback)

class SpotifyEngine(PlatformEngine):
    name = "SpotifyEnginePro"
    techniques = ("metadata-only (DRM-licensed -> fail-fast)",)
    capabilities = frozenset()
    domains = ("spotify.com", "open.spotify.com", "play.spotify.com")

    async def analyze(self, url: str) -> dict | None:
        """Spotify Pro analyzer: metadata-first, explicit non-direct-download UI state."""
        from services.spotify_scraper import scrape_spotify
        from middlewares.concurrency import get_executor
        import asyncio

        loop = asyncio.get_running_loop()
        try:
            sp = await loop.run_in_executor(get_executor(), scrape_spotify, url)
            if sp:
                sp.setdefault("platform", "Spotify")
                return self._tag(sp)
        except Exception as exc:
            error_logger.error("SpotifyEnginePro analyze failed: %s", exc)
        return None

    async def download_audio(self, url: str, progress_callback: Callable | None = None) -> str | None:
        """Spotify is DRM/licensed: fail fast so UI can explain instead of spinning."""
        raise RuntimeError("spotify_metadata_only")

