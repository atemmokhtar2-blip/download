from __future__ import annotations

import os
from typing import Callable

from config.settings import AUDIO_BITRATE, TEMP_DIR
from utils.helpers import sanitize_filename

from services.engines.base import PlatformEngine
from utils.logger import download_logger, error_logger

class YouTubeEngine(PlatformEngine):
    name = "YouTubeEnginePro"
    techniques = (
        "yt-dlp player-client ladder (ios/android/tv/web_embedded)",
        "YOUTUBE_PO_TOKEN / visitor_data injection",
        "cookies-from-browser support",
        "safe quality selectors (real 1080p+ DASH/HLS)",
    )
    capabilities = frozenset({"video", "audio", "album"})
    domains = ("youtube.com", "youtu.be", "youtube-nocookie.com", "music.youtube.com", "m.youtube.com")

    async def analyze(self, url: str) -> dict | None:
        """YouTube Pro analyzer: normalized URLs + yt-dlp client profiles + safe quality selectors."""
        from services import downloader
        from services.youtube_scraper import scrape_youtube
        from middlewares.concurrency import get_executor
        import asyncio

        loop = asyncio.get_running_loop()
        try:
            yt = await loop.run_in_executor(get_executor(), scrape_youtube, url)
            if yt and (yt.get("qualities") or yt.get("requires_login") or yt.get("error_reason")):
                yt.setdefault("platform", "YouTube")
                yt["engine_profile"] = yt.get("engine_profile") or "yt-dlp-youtube-profiles+safe-quality-selectors"
                return self._tag(yt)
        except Exception as exc:
            error_logger.error("YouTubeEnginePro scraper analyze failed: %s", exc)

        fallback = await downloader._generic_analyze_url(url)
        if fallback:
            fallback["engine_profile"] = "yt-dlp-generic-fallback"
        return self._tag(fallback)

    async def download_video(
        self,
        url: str,
        format_id: str,
        quality_label: str,
        progress_callback: Callable | None = None,
        play_url: str | None = None,
    ) -> str | None:
        from services import downloader
        return await downloader._generic_download_video(url, format_id, quality_label, progress_callback, play_url)

    async def download_audio(self, url: str, progress_callback: Callable | None = None) -> str | None:
        from services import downloader
        return await downloader._generic_download_audio(url, progress_callback)

class TikTokEngine(PlatformEngine):
    name = "TikTokEnginePro"
    techniques = (
        "multi-provider race (yt-dlp / tiktok api / aweme / cdn)",
        "no-watermark direct CDN probe",
        "photo & slideshow (album) mode",
        "direct + page-url fallback ladder",
    )
    capabilities = frozenset({"video", "audio", "image", "album"})
    domains = ("tiktok.com", "vt.tiktok.com", "vm.tiktok.com", "m.tiktok.com")

    async def analyze(self, url: str) -> dict | None:
        """TikTok Pro analyzer: providers race + CDN probing + photo-mode support."""
        from services import downloader
        from services.tiktok_scraper import scrape_tiktok
        from middlewares.concurrency import get_executor
        import asyncio

        loop = asyncio.get_running_loop()
        try:
            tk = await loop.run_in_executor(get_executor(), scrape_tiktok, url)
            if tk and (tk.get("play_url") or tk.get("images") or tk.get("album_items") or tk.get("image_url")):
                normalized = downloader._normalize_tiktok_info(tk, url)
                normalized["engine_profile"] = "providers-race+cdn-probe+photo-mode"
                normalized["source"] = tk.get("source") or normalized.get("source")
                normalized["provider_score"] = tk.get("provider_score")
                normalized["provider_candidates"] = tk.get("provider_candidates") or []
                return self._tag(normalized)
        except Exception as exc:
            error_logger.error("TikTokEnginePro scraper analyze failed: %s", exc)

        fallback = await downloader._generic_analyze_url(url)
        if fallback:
            fallback["engine_profile"] = "yt-dlp-generic-fallback"
        return self._tag(fallback)

    async def download_video(
        self,
        url: str,
        format_id: str,
        quality_label: str,
        progress_callback: Callable | None = None,
        play_url: str | None = None,
    ) -> str | None:
        """Try verified no-watermark/direct CDN first, then fall back to generic yt-dlp."""
        from services import downloader
        from services.tiktok_scraper import download_tiktok_direct, scrape_tiktok
        from middlewares.concurrency import get_executor
        import asyncio

        loop = asyncio.get_running_loop()
        safe_name = sanitize_filename(f"tiktok_{hash(url) % 100000}_{quality_label}")
        direct_out = os.path.join(TEMP_DIR, safe_name)

        candidate_play = play_url
        direct_candidates: list[dict] = []
        resolved_page_url = url
        if candidate_play:
            direct_candidates.append({"provider": "telegram-play-url", "play_url": candidate_play, "score": 9999})
        try:
            meta = await loop.run_in_executor(get_executor(), scrape_tiktok, url)
            if meta:
                resolved_page_url = meta.get("webpage_url") or meta.get("url") or url
                for item in meta.get("download_candidates") or []:
                    if item.get("play_url"):
                        direct_candidates.append(item)
                if meta.get("play_url"):
                    direct_candidates.append({
                        "provider": meta.get("source") or "scrape_tiktok",
                        "play_url": meta.get("play_url"),
                        "score": meta.get("provider_score") or 0,
                    })
                download_logger.info(
                    "TikTokEnginePro selected source=%s score=%s candidates=%s",
                    meta.get("source"), meta.get("provider_score"), len(direct_candidates),
                )
        except Exception as exc:
            error_logger.error("TikTokEnginePro resolve before download failed: %s", exc)

        seen_direct: set[str] = set()
        cancel_check = getattr(progress_callback, "cancel_check", None)
        for idx, item in enumerate(sorted(direct_candidates, key=lambda x: int(x.get("score") or 0), reverse=True), start=1):
            candidate = str(item.get("play_url") or "")
            if not candidate or candidate in seen_direct:
                continue
            if cancel_check is not None:
                cancel_check()
            seen_direct.add(candidate)
            try:
                path = await loop.run_in_executor(
                    get_executor(), download_tiktok_direct, candidate, f"{direct_out}-{idx}"
                )
                if path:
                    path = await downloader.prepare_video_async(path, source=f"tiktok-direct:{item.get('provider')}")
                    if path:
                        if progress_callback:
                            try:
                                await progress_callback({"pct": 100, "downloaded": 1, "total": 1, "speed": 0, "eta": 0})
                            except Exception:
                                pass
                        return downloader._enforce_max_file_size(path)
                download_logger.info("TikTok direct candidate failed/was silent provider=%s idx=%s", item.get("provider"), idx)
            except downloader.FileTooLargeError:
                raise
            except downloader.DownloadCancelledError:
                raise
            except Exception as exc:
                error_logger.error("TikTokEnginePro direct candidate failed provider=%s: %s", item.get("provider"), exc)

        # Final fallback must use the page URL, not the failed CDN play_url, so yt-dlp
        # can re-extract/merge with its own TikTok logic.
        for page_url in dict.fromkeys([resolved_page_url, url]):
            if cancel_check is not None:
                cancel_check()
            try:
                path = await downloader._generic_download_video(page_url, format_id, quality_label, progress_callback, None)
                if path:
                    return path
            except downloader.FileTooLargeError:
                raise
            except downloader.DownloadCancelledError:
                raise
            except Exception as exc:
                error_logger.error("TikTokEnginePro page fallback failed for %s: %s", page_url[:90], exc)
        return None

    async def download_audio(self, url: str, progress_callback: Callable | None = None) -> str | None:
        # Keep audio on yt-dlp/ffmpeg path for reliable mp3 conversion.
        from services import downloader
        return await downloader._generic_download_audio(url, progress_callback)

class InstagramEngine(PlatformEngine):
    name = "InstagramEnginePro"
    techniques = (
        "GraphQL + embed + gallery-dl + instaloader resolver race",
        "direct CDN probe with Referer/Origin",
        "audio-track verification before delivery",
        "video + extract-audio fallback",
    )
    capabilities = frozenset({"video", "audio", "image", "album"})
    domains = ("instagram.com", "instagr.am", "cdninstagram.com")

    async def analyze(self, url: str) -> dict | None:
        from services import downloader
        from services.instagram_scraper import scrape_instagram
        from middlewares.concurrency import get_executor
        import asyncio

        loop = asyncio.get_running_loop()
        try:
            ig = await loop.run_in_executor(get_executor(), scrape_instagram, url)
            if ig and (ig.get("image_url") or ig.get("play_url") or ig.get("album_items")):
                ig.setdefault("platform", "Instagram")
                ig["engine_profile"] = "graphql+embed+gallery-dl+instaloader+cdn-probe"
                return self._tag(ig)
            if ig and ig.get("requires_login"):
                return self._tag(ig)
        except Exception as exc:
            error_logger.error("InstagramEnginePro scraper analyze failed: %s", exc)

        fallback = await downloader._generic_analyze_url(url)
        if fallback:
            fallback["engine_profile"] = "yt-dlp-generic-fallback"
        return self._tag(fallback)

    async def download_video(
        self,
        url: str,
        format_id: str,
        quality_label: str,
        progress_callback: Callable | None = None,
        play_url: str | None = None,
    ) -> str | None:
        """Instagram direct-CDN download with audio verification + yt-dlp fallback.

        Instagram CDN URLs expire quickly and yt-dlp's Instagram extractor is
        often login-walled. Prefer the resolver's fresh play_url candidates
        (GraphQL/embed/gallery-dl/instaloader) downloaded directly with proper
        Referer headers, verify the audio stream, then fall back to the robust
        generic merge path on the canonical page URL.
        """
        from services import downloader
        from services.instagram_scraper import scrape_instagram, direct_download_headers
        from middlewares.concurrency import get_executor
        import asyncio

        loop = asyncio.get_running_loop()
        candidates: list[tuple[str, str]] = []
        resolved_url = url
        seen: set[str] = set()

        def _add(candidate: str | None, source: str) -> None:
            candidate = (candidate or "").strip()
            if candidate and candidate.startswith("http") and candidate not in seen:
                seen.add(candidate)
                candidates.append((candidate, source))

        _add(play_url, "cached-play-url")
        try:
            meta = await loop.run_in_executor(get_executor(), scrape_instagram, url)
            if meta:
                resolved_url = meta.get("webpage_url") or meta.get("url") or url
                _add(meta.get("play_url"), meta.get("source") or "instagram-direct")
                # Include video slides from albums as alternates when the top
                # candidate URL has expired or carries no audio.
                for item in meta.get("album_items") or []:
                    if item.get("type") == "video":
                        _add(item.get("play_url") or item.get("url"), "instagram-slide")
            download_logger.info(
                "InstagramEnginePro candidates=%s resolved=%s",
                len(candidates), resolved_url[:100],
            )
        except Exception as exc:
            error_logger.error("InstagramEnginePro resolve before download failed: %s", exc)

        async def _try_download(candidate: str, source: str, prefix: str) -> str | None:
            path = await loop.run_in_executor(
                get_executor(),
                downloader._download_direct_sync,
                candidate,
                direct_download_headers(),
                60,
                progress_callback,
                loop,
                prefix,
            )
            return await downloader.prepare_video_async(path, source=f"instagram-direct:{source}")

        async def _walk(items) -> str | None:
            for candidate, source, prefix in items:
                try:
                    path = await _try_download(candidate, source, prefix)
                    if path:
                        return path
                    download_logger.info("Instagram direct candidate failed/was silent source=%s", source)
                except downloader.FileTooLargeError:
                    raise
                except downloader.DownloadCancelledError:
                    raise
                except Exception as exc:
                    error_logger.error("InstagramEnginePro direct candidate failed source=%s: %s", source, exc)
            return None

        path = await _walk([(c, s, "xdl-instagram") for c, s in candidates])
        if path:
            return path

        # Instagram CDN URLs are short-lived. One fresh re-resolve may surface a
        # new signed URL (the shared seen-set means only genuinely NEW candidates
        # are walked) before the heavier yt-dlp merge path is attempted.
        before_refresh = len(candidates)
        try:
            meta2 = await loop.run_in_executor(get_executor(), scrape_instagram, resolved_url)
            if meta2:
                resolved_url = meta2.get("webpage_url") or meta2.get("url") or resolved_url
                _add(meta2.get("play_url"), meta2.get("source") or "instagram-direct-refresh")
                for item in meta2.get("album_items") or []:
                    if item.get("type") == "video":
                        _add(item.get("play_url") or item.get("url"), "instagram-slide-refresh")
        except Exception as exc:
            error_logger.error("InstagramEnginePro refresh resolve failed: %s", exc)
        if len(candidates) > before_refresh:
            path = await _walk([(c, s, "xdl-instagram-refresh") for c, s in candidates[before_refresh:]])
            if path:
                return path

        # Final fallback uses the canonical page URL; pass the best CDN URL as a
        # first direct target so yt-dlp can also attempt to merge it.
        fallback_play = candidates[0][0] if candidates else None
        return await downloader._generic_download_video(
            resolved_url, format_id, quality_label, progress_callback, fallback_play
        )

    async def download_audio(self, url: str, progress_callback: Callable | None = None) -> str | None:
        """YouTube-grade audio for Instagram: yt-dlp first, then video+extract fallback."""
        from services import downloader
        from middlewares.concurrency import get_executor
        import asyncio

        try:
            return await downloader._generic_download_audio(url, progress_callback)
        except downloader.FileTooLargeError:
            raise
        except downloader.DownloadCancelledError:
            # The stop button must abort the fallback too — never swallow it
            # into a video->audio retry that would hit the same cancellation.
            raise
        except Exception as exc:
            error_logger.warning(
                "InstagramEnginePro generic audio failed (%s); falling back to video+extract for %s",
                exc, url[:120],
            )

        # Instagram audio-only extraction via yt-dlp is the weakest link of the
        # engine. If it fails, grab the verified video through the strong video
        # path, then transcode its audio track locally with ffmpeg.
        loop = asyncio.get_running_loop()
        video_path = await downloader.download_video(url, "best", "best", progress_callback)
        if not video_path:
            raise RuntimeError("Instagram audio fallback: no video available to extract from")
        cancel_check = getattr(progress_callback, "cancel_check", None)
        return await loop.run_in_executor(
            get_executor(), downloader.extract_audio_mp3, video_path, AUDIO_BITRATE, cancel_check
        )

class PinterestEngine(PlatformEngine):
    name = "PinterestEnginePro"
    techniques = (
        "pin.it short-link expansion",
        "PinResource + gallery-dl + HTML/OG",
        "split HLS video+audio merge",
        "full rendition walk (mp4 -> m3u8) before yt-dlp",
    )
    capabilities = frozenset({"video", "image", "album"})
    domains = ("pinterest.", "pin.it", "pinimg.com")

    async def analyze(self, url: str) -> dict | None:
        """Pinterest Pro analyzer: pin.it expansion + PinResource + gallery-dl + HTML + CDN probe."""
        from services import downloader
        from services.pinterest_scraper import scrape_pinterest
        from middlewares.concurrency import get_executor
        import asyncio

        loop = asyncio.get_running_loop()
        try:
            pin = await loop.run_in_executor(get_executor(), scrape_pinterest, url)
            if pin and (pin.get("image_url") or pin.get("play_url") or pin.get("album_items")):
                pin.setdefault("platform", "Pinterest")
                pin["engine_profile"] = pin.get("engine_profile") or "pinterest-downloader+pin-resource+gallery-dl+html+cdn-probe"
                return self._tag(pin)
        except Exception as exc:
            error_logger.error("PinterestEnginePro scraper analyze failed: %s", exc)
        fallback = await downloader._generic_analyze_url(url)
        if fallback:
            fallback["engine_profile"] = "yt-dlp-generic-fallback"
        return self._tag(fallback)

    async def download_video(
        self,
        url: str,
        format_id: str,
        quality_label: str,
        progress_callback: Callable | None = None,
        play_url: str | None = None,
    ) -> str | None:
        from services import downloader
        from services.pinterest_scraper import scrape_pinterest, download_hls_video
        from middlewares.concurrency import get_executor
        import asyncio

        loop = asyncio.get_running_loop()
        cancel_check = getattr(progress_callback, "cancel_check", None)
        candidates: list[dict] = []
        seen: set[str] = set()

        def _add(candidate: str | None, provider: str, score: int = 0) -> None:
            candidate = (candidate or "").strip()
            if candidate.startswith("http") and candidate not in seen:
                seen.add(candidate)
                candidates.append({
                    "url": candidate,
                    "provider": provider,
                    "score": int(score or 0),
                })

        _add(play_url, "cached-play-url", 9999)
        try:
            meta = await loop.run_in_executor(get_executor(), scrape_pinterest, url)
            if meta:
                for item in meta.get("download_candidates") or []:
                    _add(
                        item.get("url") or item.get("play_url"),
                        f"pin-resource:{item.get('kind') or 'mp4'}",
                        int(item.get("score") or 0),
                    )
                _add(
                    meta.get("play_url"),
                    meta.get("source") or "pin-resource-best",
                    int(meta.get("provider_score") or 0),
                )
                download_logger.info(
                    "PinterestEnginePro selected source=%s score=%s direct-candidates=%s",
                    meta.get("source"), meta.get("provider_score"), len(candidates),
                )
        except Exception as exc:
            error_logger.error("PinterestEnginePro resolve before download failed: %s", exc)

        # Walk EVERY rendition (progressive mp4 first, then HLS) before yt-dlp.
        # A single expired/silent CDN URL can no longer sink the whole download
        # into the misleading "Requested format is not available" yt-dlp error.
        for candidate in sorted(candidates, key=lambda c: (0 if ".m3u8" not in c["url"] else 1, -int(c["score"] or 0))):
            target = candidate["url"]
            try:
                if ".m3u8" in target:
                    safe_name = sanitize_filename(f"pinterest_hls_{hash(target) % 100000}")
                    hls_out = os.path.join(TEMP_DIR, safe_name)
                    if cancel_check is not None:
                        path = await loop.run_in_executor(
                            get_executor(),
                            download_hls_video,
                            target,
                            hls_out,
                            cancel_check,
                        )
                    else:
                        path = await loop.run_in_executor(
                            get_executor(),
                            download_hls_video,
                            target,
                            hls_out,
                        )
                    path = await downloader.prepare_video_async(
                        path,
                        source=f"pinterest-hls:{candidate['provider']}",
                        cancel_check=cancel_check,
                    )
                else:
                    path = await loop.run_in_executor(
                        get_executor(),
                        downloader._download_direct_sync,
                        target,
                        {"Referer": "https://www.pinterest.com/"},
                        60,
                        progress_callback,
                        loop,
                        "xdl-pinterest",
                    )
                    path = await downloader.prepare_video_async(
                        path,
                        source=f"pinterest-direct:{candidate['provider']}",
                        cancel_check=cancel_check,
                    )
                if path:
                    return path
                download_logger.info(
                    "Pinterest candidate rejected (silent/unverifiable) provider=%s",
                    candidate["provider"],
                )
            except downloader.FileTooLargeError:
                raise
            except downloader.DownloadCancelledError:
                raise
            except Exception as exc:
                error_logger.error(
                    "PinterestEnginePro direct candidate failed provider=%s: %s",
                    candidate["provider"], exc,
                )

        # Final yt-dlp fallback keeps the strongest direct CDN URL as the first
        # target, so a video pin can never degrade into its silent poster image.
        fallback_play = candidates[0]["url"] if candidates else play_url
        return await downloader._generic_download_video(
            url, format_id, quality_label, progress_callback, fallback_play
        )

class FacebookEngine(PlatformEngine):
    name = "FacebookEnginePro"
    techniques = (
        "page-merge strategy (never trust a video-only CDN URL)",
        "m.facebook / mbasic.facebook host fallback",
        "yt-dlp bestvideo+bestaudio DASH merge",
    )
    capabilities = frozenset({"video", "audio", "image"})
    domains = ("facebook.com", "fb.watch", "fb.com", "facebookreel.com")

    async def analyze(self, url: str) -> dict | None:
        """Facebook Pro analyzer: fb.watch expansion + HTML/OG + yt-dlp + CDN probe."""
        from services import downloader
        from services.facebook_scraper import scrape_facebook
        from middlewares.concurrency import get_executor
        import asyncio

        loop = asyncio.get_running_loop()
        try:
            fb = await loop.run_in_executor(get_executor(), scrape_facebook, url)
            if fb and fb.get("play_url"):
                fb.setdefault("platform", "Facebook")
                fb["engine_profile"] = fb.get("engine_profile") or "yt-dlp+html+mobile+cdn-probe"
                return self._tag(fb)
            if fb and fb.get("requires_login"):
                return self._tag(fb)
        except Exception as exc:
            error_logger.error("FacebookEnginePro scraper analyze failed: %s", exc)

        fallback = await downloader._generic_analyze_url(url)
        if fallback:
            fallback["engine_profile"] = "yt-dlp-generic-fallback"
        return self._tag(fallback)

    async def download_video(
        self,
        url: str,
        format_id: str,
        quality_label: str,
        progress_callback: Callable | None = None,
        play_url: str | None = None,
    ) -> str | None:
        from services import downloader
        from services.facebook_scraper import scrape_facebook
        from middlewares.concurrency import get_executor
        import asyncio

        loop = asyncio.get_running_loop()

        # Facebook stores video and audio as SEPARATE DASH streams:
        # browser_native_hd_url / playable_url_quality_hd / og:video are
        # VIDEO-ONLY. Radical fix: NEVER download a direct Facebook CDN URL in
        # this engine. Everything goes through yt-dlp on the PAGE URL so it can
        # merge bestvideo+bestaudio, and the central audio guard
        # (_prepare_video_for_delivery) verifies the merged file. No silent
        # video can leave this path — if the merge produces no audio the guard
        # rejects it and the next page variant is tried.
        normalized_url = url
        try:
            meta = await loop.run_in_executor(get_executor(), scrape_facebook, url)
            if meta:
                normalized_url = meta.get("webpage_url") or meta.get("url") or url
                download_logger.info(
                    "FacebookEnginePro page-merge path source=%s score=%s direct_has_audio=%s prefer_page=%s (direct CDN skipped by design)",
                    meta.get("source"), meta.get("provider_score"),
                    bool(meta.get("direct_has_audio")), bool(meta.get("prefer_page_download")),
                )
        except Exception as exc:
            error_logger.error(
                "FacebookEnginePro scrape before download failed (continuing with page merge): %s", exc)

        for page_url in dict.fromkeys([
            normalized_url,
            url,
            url.replace("://www.facebook.com", "://m.facebook.com"),
            url.replace("://www.facebook.com", "://mbasic.facebook.com"),
        ]):
            if not page_url:
                continue
            try:
                path = await downloader._generic_download_video(page_url, format_id, quality_label, progress_callback)
                if path:
                    return path
            except downloader.FileTooLargeError:
                raise
            except downloader.DownloadCancelledError:
                raise
            except Exception as exc:
                error_logger.error("FacebookEnginePro page fallback failed for %s: %s", page_url[:90], exc)
        return None

class TwitterEngine(PlatformEngine):
    name = "TwitterEnginePro"
    techniques = (
        "x.com/t.co/vxtwitter/fxtwitter mirror normalization",
        "HTML + API multi-provider resolve",
        "progressive mp4 -> HLS master resolve + split audio merge",
    )
    capabilities = frozenset({"video", "audio", "image", "album"})
    domains = ("twitter.com", "x.com", "t.co", "fxtwitter.com", "fixupx.com", "vxtwitter.com", "nitter.net")

    async def analyze(self, url: str) -> dict | None:
        """Twitter/X Pro analyzer: x.com/t.co normalization + video/GIF/photo multi-provider resolver."""
        from services import downloader
        from services.twitter_scraper import scrape_twitter
        from middlewares.concurrency import get_executor
        import asyncio

        loop = asyncio.get_running_loop()
        try:
            tw = await loop.run_in_executor(get_executor(), scrape_twitter, url)
            if tw and (tw.get("play_url") or tw.get("image_url") or tw.get("album_items")):
                tw.setdefault("platform", "Twitter/X")
                tw["engine_profile"] = tw.get("engine_profile") or "yt-dlp+vxtwitter+html+cdn-probe"
                return self._tag(tw)
            if tw and tw.get("requires_login"):
                return self._tag(tw)
        except Exception as exc:
            error_logger.error("TwitterEnginePro scraper analyze failed: %s", exc)

        fallback = await downloader._generic_analyze_url(url)
        if fallback:
            fallback["engine_profile"] = "yt-dlp-generic-fallback"
        return self._tag(fallback)

    async def download_video(
        self,
        url: str,
        format_id: str,
        quality_label: str,
        progress_callback: Callable | None = None,
        play_url: str | None = None,
    ) -> str | None:
        """Walk every direct Twitter/X candidate before yt-dlp's login-walled pass.

        The multi-provider race already ranks yt-dlp / vxtwitter / fxtwitter /
        HTML extraction. This engine now walks the FULL ordered candidate list
        (progressive mp4 first, HLS second) instead of trusting a single
        ``play_url`` that may be expired or silent. The central audio guard still
        verifies each successful download before delivery.
        """
        from services import downloader
        from services.twitter_scraper import scrape_twitter, normalize_twitter_url
        from middlewares.concurrency import get_executor
        import asyncio

        loop = asyncio.get_running_loop()
        cancel_check = getattr(progress_callback, "cancel_check", None)
        candidates: list[tuple[str, str, int]] = []  # (url, source, score)
        seen: set[str] = set()

        def _add(candidate: str | None, source: str, score: int = 0) -> None:
            candidate = (candidate or "").strip()
            if candidate.startswith("http") and candidate not in seen:
                seen.add(candidate)
                candidates.append((candidate, source, int(score or 0)))

        _add(play_url, "cached-play-url", 9999)
        resolved_url = url
        try:
            meta = await loop.run_in_executor(get_executor(), scrape_twitter, url)
            if meta:
                resolved_url = meta.get("webpage_url") or normalize_twitter_url(url)
                _add(
                    meta.get("play_url"),
                    meta.get("source") or "twitter-best",
                    int(meta.get("provider_score") or 0),
                )
                # The scraper aggregates EVERY provider's direct URLs here
                # (vxtwitter/fxtwitter/yt-dlp/html) so a dead "best" URL can
                # never sink the whole download.
                for item in meta.get("download_candidates") or []:
                    _add(
                        item.get("url"),
                        f"twitter:{item.get('provider') or 'alt'}",
                        int(item.get("score") or 0),
                    )
                download_logger.info(
                    "TwitterEnginePro candidates=%s provider=%s score=%s",
                    len(candidates), meta.get("source"), meta.get("provider_score"),
                )
        except Exception as exc:
            error_logger.error("TwitterEnginePro resolve before download failed: %s", exc)

        for candidate, source, _score in sorted(
            candidates, key=lambda c: (0 if ".m3u8" not in c[0] else 1, -c[2])
        ):
            if cancel_check is not None:
                cancel_check()
            try:
                if ".m3u8" in candidate:
                    direct_path = await loop.run_in_executor(
                        get_executor(),
                        downloader.download_hls_mp4,
                        candidate,
                        "xdl-twitter",
                        {"Referer": "https://twitter.com/"},
                    )
                else:
                    direct_path = await loop.run_in_executor(
                        get_executor(),
                        downloader._download_direct_sync,
                        candidate,
                        {"Referer": "https://twitter.com/"},
                        60,
                        progress_callback,
                        loop,
                        "xdl-twitter",
                    )
                direct_path = await downloader.prepare_video_async(
                    direct_path, source=f"twitter-direct:{source}", cancel_check=cancel_check
                )
                if direct_path:
                    return direct_path
                download_logger.info("Twitter direct candidate failed/was silent source=%s", source)
            except downloader.FileTooLargeError:
                raise
            except downloader.DownloadCancelledError:
                raise
            except Exception as exc:
                error_logger.error("TwitterEnginePro direct candidate failed source=%s: %s", source, exc)

        # Final fallbacks through page URLs: the canonical URL plus the public
        # vxtwitter/fxtwitter mirrors, whose extractors stay unauthenticated.
        for page_url in dict.fromkeys([
            resolved_url,
            url,
            url.replace("https://x.com", "https://twitter.com"),
            url.replace("https://twitter.com", "https://fxtwitter.com"),
            url.replace("https://twitter.com", "https://vxtwitter.com"),
        ]):
            if not page_url:
                continue
            if cancel_check is not None:
                cancel_check()
            try:
                path = await downloader._generic_download_video(
                    page_url, format_id, quality_label, progress_callback
                )
                if path:
                    return path
            except downloader.FileTooLargeError:
                raise
            except downloader.DownloadCancelledError:
                raise
            except Exception as exc:
                error_logger.error(
                    "TwitterEnginePro page fallback failed for %s: %s", page_url[:90], exc
                )
        raise RuntimeError(
            "The media stream returned zero data — the host CDN rejected or expired the segment URLs."
        )

class RedditEngine(PlatformEngine):
    name = "RedditEnginePro"
    techniques = (
        "public JSON API (no auth)",
        "v.redd.it DASH rendition ranking (progressive > DASH)",
        "gallery album support",
        "oembed + yt-dlp fallback",
    )
    capabilities = frozenset({"video", "audio", "image", "album"})
    domains = ("reddit.com", "redd.it", "v.redd.it", "old.reddit.com", "new.reddit.com", "np.reddit.com")

    async def analyze(self, url: str) -> dict | None:
        """Reddit Pro analyzer: JSON API + v.redd.it DASH + galleries + yt-dlp fallback."""
        from services import downloader
        from services.reddit_scraper import scrape_reddit
        from middlewares.concurrency import get_executor
        import asyncio

        loop = asyncio.get_running_loop()
        try:
            rd = await loop.run_in_executor(get_executor(), scrape_reddit, url)
            if rd and (rd.get("play_url") or rd.get("image_url") or rd.get("album_items")):
                rd.setdefault("platform", "Reddit")
                rd["engine_profile"] = rd.get("engine_profile") or "reddit-json+ytdlp+oembed+cdn-probe"
                return self._tag(rd)
            if rd and (rd.get("requires_login") or rd.get("error_reason")):
                return self._tag(rd)
        except Exception as exc:
            error_logger.error("RedditEnginePro scraper analyze failed: %s", exc)

        fallback = await downloader._generic_analyze_url(url)
        if fallback:
            fallback["engine_profile"] = "yt-dlp-generic-fallback"
        return self._tag(fallback)

    async def download_video(
        self,
        url: str,
        format_id: str,
        quality_label: str,
        progress_callback: Callable | None = None,
        play_url: str | None = None,
    ) -> str | None:
        from services import downloader
        from services.reddit_scraper import scrape_reddit
        from middlewares.concurrency import get_executor
        import asyncio

        loop = asyncio.get_running_loop()
        candidates: list[tuple[str, str]] = []
        seen: set[str] = set()

        def _add(candidate: str | None, source: str) -> None:
            candidate = (candidate or "").strip()
            if candidate and candidate.startswith("http") and candidate not in seen:
                seen.add(candidate)
                candidates.append((candidate, source))

        _add(play_url, "cached-play-url")
        try:
            meta = await loop.run_in_executor(get_executor(), scrape_reddit, url)
            if meta:
                # The resolver keeps every playable variant in `formats`; prefer
                # progressive/fallback mp4s (which carry audio) over DASH-only
                # video streams so the audio guard doesn't reject our best guess.
                for item in meta.get("formats") or []:
                    _add(item.get("url"), "reddit-json")
                _add(meta.get("play_url"), "reddit-json-best")
            download_logger.info("RedditEnginePro candidates=%s", len(candidates))
        except Exception as exc:
            error_logger.error("RedditEnginePro resolve before download failed: %s", exc)

        def _direct_score(entry: tuple[str, str]) -> int:
            u = entry[0].lower()
            score = 0
            if ".mp4" in u:
                score += 120
            if "fallback" in u:
                score += 90
            if "scrubber" in u:
                score += 70
            if "DASH_" in u:
                score -= 120
            if ".m3u8" in u:
                score += 20
            if ".mpd" in u:
                score += 10
            return score

        for candidate, source in sorted(candidates, key=_direct_score, reverse=True):
            if ".mp4" not in candidate and ".m3u8" not in candidate:
                continue
            try:
                if ".m3u8" in candidate:
                    direct_path = await loop.run_in_executor(
                        get_executor(),
                        downloader.download_hls_mp4,
                        candidate,
                        "xdl-reddit",
                        {"Referer": "https://www.reddit.com/"},
                    )
                else:
                    direct_path = await loop.run_in_executor(
                        get_executor(),
                        downloader._download_direct_sync,
                        candidate,
                        {"Referer": "https://www.reddit.com/"},
                        90,
                        progress_callback,
                        loop,
                        "xdl-reddit",
                    )
                direct_path = await downloader.prepare_video_async(direct_path, source=f"reddit-direct:{source}")
                if direct_path:
                    return direct_path
                download_logger.info("Reddit direct candidate failed/was silent source=%s", source)
            except downloader.FileTooLargeError:
                raise
            except downloader.DownloadCancelledError:
                raise
            except Exception as exc:
                error_logger.error("RedditEnginePro direct candidate failed source=%s: %s", source, exc)

        return await downloader._generic_download_video(url, format_id, quality_label, progress_callback)

