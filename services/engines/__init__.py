from __future__ import annotations

from services.engines.audio import SoundCloudEngine, SpotifyEngine
from services.engines.direct import (
    AfreecaTVEngine, AparatEngine, AudiomackEngine, BandcampEngine, BilibiliEngine,
    BitChuteEngine, BlueskyEngine, ChzzkEngine, CoubEngine, DailymotionEngine,
    DeezerEngine, DouyinEngine, DzenEngine, FC2VideoEngine, ImgurEngine, IQiyiEngine,
    KakaoTVEngine, KickEngine, KuaishouEngine, KwaiEngine, Lemon8Engine, LikeeEngine,
    MangoTVEngine, MastodonEngine, MixcloudEngine, MojEngine, NamashaEngine,
    NaverBlogEngine, NaverCafeEngine, NaverPostEngine, NaverTVEngine, NiconicoEngine,
    NineGagEngine, OdnoklassnikiEngine, OdyseeEngine, OpenrecEngine, PeerTubeEngine,
    RumbleEngine, RutubeEngine, ShareChatEngine, SnapchatEngine, StreamableEngine,
    TedEngine, TelegramPublicEngine, TencentVideoEngine, ThreadsEngine, TumblrEngine,
    TVerEngine, TwitchEngine, VimeoEngine, VkEngine, WeiboEngine, XiaohongshuEngine,
    YoukuEngine,
)
from services.engines.generic import GenericEngine, UniversalEngine
from services.engines.major import (
    FacebookEngine, InstagramEngine, PinterestEngine, RedditEngine, TikTokEngine,
    TwitterEngine, YouTubeEngine,
)


def registered_engines():
    """Return the real platform engines enabled by the downloader router.

    Keep this as the single source of truth for platform routing, dashboard
    engine status, and future smoke tests. The order is stable and mirrors the
    historical dashboard order so ``list_engines`` stays deterministic.
    """
    return [
        YouTubeEngine(), TikTokEngine(), InstagramEngine(), FacebookEngine(),
        TwitterEngine(), RedditEngine(), PinterestEngine(), ThreadsEngine(),
        SnapchatEngine(), VimeoEngine(), DailymotionEngine(), TelegramPublicEngine(),
        SoundCloudEngine(), SpotifyEngine(), LikeeEngine(), KwaiEngine(), TwitchEngine(),
        KickEngine(), BilibiliEngine(), BlueskyEngine(), VkEngine(), TumblrEngine(),
        ImgurEngine(), StreamableEngine(), CoubEngine(), OdnoklassnikiEngine(),
        RumbleEngine(), RutubeEngine(), OdyseeEngine(), NiconicoEngine(), DouyinEngine(),
        XiaohongshuEngine(), KuaishouEngine(), NaverTVEngine(), NaverBlogEngine(),
        NaverCafeEngine(), NaverPostEngine(), KakaoTVEngine(), ChzzkEngine(),
        AfreecaTVEngine(), MastodonEngine(), WeiboEngine(), NineGagEngine(),
        Lemon8Engine(), DzenEngine(), TedEngine(), MixcloudEngine(), BandcampEngine(),
        AparatEngine(), FC2VideoEngine(), BitChuteEngine(), TVerEngine(), OpenrecEngine(),
        IQiyiEngine(), TencentVideoEngine(), YoukuEngine(), MangoTVEngine(),
        ShareChatEngine(), MojEngine(), NamashaEngine(), AudiomackEngine(),
        DeezerEngine(), PeerTubeEngine(),
    ]


# Backward-compatible private alias for any older internal imports.
_registered_engines = registered_engines


def list_engines() -> list[dict]:
    rows = [
        {
            "name": engine.name,
            "status": "ready",
            "domains": len(getattr(engine, "domains", ()) or ()),
            "techniques": list(getattr(engine, "techniques", ()) or ()),
        }
        for engine in _registered_engines()
    ]
    # Show the universal catch-all explicitly so dashboards/status screens
    # communicate that ANY yt-dlp-supported site (1000+) is downloadable.
    rows.append({
        "name": "UniversalEngine",
        "status": "ready",
        "domains": "any",
        "note": "أي منصة يدعمها yt-dlp (1000+)",
    })
    return rows
