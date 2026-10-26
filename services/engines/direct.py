from __future__ import annotations

import os
from typing import Callable

from config.settings import AUDIO_BITRATE, TEMP_DIR
from utils.helpers import sanitize_filename

from services.engines.base import PlatformEngine
from utils.logger import download_logger, error_logger
from services.engines.generic import AudioFallbackEngine, EmbedFallbackEngine, YtDlpFallbackEngine


class ThreadsEngine(EmbedFallbackEngine):
    name = "ThreadsEnginePro"
    platform_label = "Threads"
    domains = ("threads.net", "www.threads.net")
    resolver_name = "resolve_threads"
    embed_headers = {"Referer": "https://www.threads.net/"}
    techniques = (
        "Threads /embed player (og:video + JSON-LD + inline script CDN mining)",
        "browser-TLS impersonation (desktop + mobile UA)",
        "CDN candidate walk with audio verification",
        "yt-dlp fallback",
    )


class VimeoEngine(EmbedFallbackEngine):
    """Vimeo: player-config JSON resolver (progressive mp4 w/ audio) + yt-dlp fallback."""
    name = "VimeoEnginePro"
    platform_label = "Vimeo"
    domains = ("vimeo.com", "player.vimeo.com")
    resolver_name = "resolve_vimeo"
    embed_headers = {"Referer": "https://vimeo.com/"}
    techniques = (
        "player.vimeo.com/{id}/config JSON -> progressive mp4 (with audio)",
        "adaptive/DASH rendition ranking",
        "CDN candidate walk with audio verification",
        "yt-dlp fallback",
    )


class DailymotionEngine(EmbedFallbackEngine):
    name = "DailymotionEnginePro"
    platform_label = "Dailymotion"
    domains = ("dailymotion.com", "dai.ly")
    resolver_name = "resolve_dailymotion"
    embed_headers = {"Referer": "https://www.dailymotion.com/"}
    techniques = (
        "Dailymotion player/metadata JSON (progressive mp4 + HLS ranks)",
        "oEmbed title/thumbnail augmentation",
        "CDN candidate walk with audio verification",
        "yt-dlp fallback",
    )


class SnapchatEngine(EmbedFallbackEngine):
    name = "SnapchatEnginePro"
    platform_label = "Snapchat"
    domains = ("snapchat.com", "story.snapchat.com")
    resolver_name = "resolve_snapchat"
    embed_headers = {"Referer": "https://www.snapchat.com/"}
    techniques = (
        "Snapchat Spotlight canonical embed og:video",
        "short-link canonicalization",
        "CDN candidate walk with audio verification",
        "yt-dlp fallback",
    )


class TelegramPublicEngine(EmbedFallbackEngine):
    name = "TelegramPublicEnginePro"
    platform_label = "Telegram"
    domains = ("t.me", "telegram.me", "telegram.dog")
    resolver_name = "resolve_telegram"
    embed_headers = {"Referer": "https://t.me/"}
    techniques = (
        "t.me embed og:video (?embed=1&mode=tme)",
        "oEmbed auto-discovery fallback",
        "CDN candidate walk with audio verification",
        "yt-dlp fallback",
    )


class LikeeEngine(EmbedFallbackEngine):
    name = "LikeeEnginePro"
    platform_label = "Likee"
    domains = ("likee.video", "likee.com", "l.likee.video", "mobile.likee.video")
    embed_headers = {"Referer": "https://likee.video/"}
    techniques = (
        "Likee embed resolver (og:video/JSON-LD/<video>/script/oEmbed, browser TLS)",
        "CDN candidate walk with audio verification",
        "yt-dlp Likee extractor fallback",
    )


class KwaiEngine(EmbedFallbackEngine):
    name = "KwaiEnginePro"
    platform_label = "Kwai"
    domains = ("kwai.com", "kw.ai", "s.kw.ai", "m.kwai.com", "kwai-video.com")
    embed_headers = {"Referer": "https://www.kwai.com/"}
    techniques = (
        "Kwai embed resolver (og:video/JSON-LD/<video>/script/oEmbed, browser TLS)",
        "CDN candidate walk with audio verification",
        "yt-dlp Kwai extractor fallback",
    )


class TwitchEngine(EmbedFallbackEngine):
    name = "TwitchEnginePro"
    platform_label = "Twitch"
    domains = ("twitch.tv", "clips.twitch.tv", "m.twitch.tv", "player.twitch.tv")
    resolver_name = "resolve_twitch"
    embed_headers = {"Referer": "https://clips.twitch.tv/"}
    techniques = (
        "Twitch GQL clips API (clip.videoQualities sourceURL)",
        "progressive mp4 rendition ranking",
        "CDN candidate walk with audio verification",
        "yt-dlp fallback (VODs / channels)",
    )


class BilibiliEngine(EmbedFallbackEngine):
    name = "BilibiliEnginePro"
    platform_label = "Bilibili"
    domains = ("bilibili.com", "b23.tv", "bilibili.tv", "m.bilibili.com")
    resolver_name = "resolve_bilibili"
    embed_headers = {"Referer": "https://www.bilibili.com/"}
    techniques = (
        "Bilibili window.__playinfo__ durl (progressive mp4 + audio)",
        "DASH backup_url walk",
        "b23.tv short-link expansion",
        "yt-dlp fallback",
    )


class VkEngine(EmbedFallbackEngine):
    name = "VkEnginePro"
    platform_label = "VK"
    domains = ("vk.com", "vkvideo.ru", "vk.cc", "m.vk.com", "vkvideo.cc")
    resolver_name = "resolve_vk"
    embed_headers = {"Referer": "https://vk.com/"}
    techniques = (
        "VK video_ext.php JSON (progressive mp4 payload)",
        "script-CDN mining for clips",
        "CDN candidate walk with audio verification",
        "yt-dlp fallback",
    )


class TumblrEngine(EmbedFallbackEngine):
    name = "TumblrEnginePro"
    platform_label = "Tumblr"
    domains = ("tumblr.com", "www.tumblr.com", "at.tumblr.com", "tmblr.co")
    resolver_name = "resolve_tumblr"
    embed_headers = {"Referer": "https://www.tumblr.com/"}
    techniques = (
        "Tumblr public oEmbed JSON API (<video><source mp4>)",
        "HTML og/JSON-LD fallback",
        "CDN candidate walk with audio verification",
        "yt-dlp fallback",
    )


class ImgurEngine(EmbedFallbackEngine):
    """Imgur: direct mp4 resolver for video/GIF posts; images stay on yt-dlp."""
    name = "ImgurEnginePro"
    platform_label = "Imgur"
    domains = ("imgur.com", "i.imgur.com", "m.imgur.com", "stack.imgur.com")
    resolver_name = "resolve_imgur"
    embed_headers = {"Referer": "https://imgur.com/"}
    techniques = (
        "Imgur i.imgur.com direct mp4 (HEAD probe for GIF/video)",
        "oEmbed metadata augmentation",
        "CDN candidate walk with audio verification",
        "yt-dlp fallback",
    )


class StreamableEngine(EmbedFallbackEngine):
    """Streamable: public JSON API + HTML og:video resolver, then yt-dlp fallback."""
    name = "StreamableEnginePro"
    platform_label = "Streamable"
    domains = ("streamable.com", "www.streamable.com")
    resolver_name = "resolve_streamable"
    embed_headers = {"Referer": "https://streamable.com/"}
    techniques = (
        "Streamable public JSON API + HTML og:video fallback",
        "CDN candidate rank (mp4 > mobile)",
        "CDN candidate walk with audio verification",
        "yt-dlp fallback",
    )


class CoubEngine(EmbedFallbackEngine):
    """Coub: public API v2 html5 file_versions resolver, then yt-dlp fallback."""
    name = "CoubEnginePro"
    platform_label = "Coub"
    domains = ("coub.com", "www.coub.com")
    resolver_name = "resolve_coub"
    embed_headers = {"Referer": "https://coub.com/"}
    techniques = (
        "Coub API v2 html5 file_versions (high/med/low mp4)",
        "CDN candidate walk with audio verification",
        "yt-dlp fallback",
    )


class OdnoklassnikiEngine(EmbedFallbackEngine):
    name = "OdnoklassnikiEnginePro"
    platform_label = "Odnoklassniki"
    domains = ("ok.ru", "m.ok.ru", "odnoklassniki.ru", "m.odnoklassniki.ru")
    resolver_name = "resolve_odnoklassniki"
    embed_headers = {"Referer": "https://ok.ru/"}
    techniques = (
        "ok.ru/videoembed JSON (progressive mp4 renditions)",
        "quality-height ranking",
        "CDN candidate walk with audio verification",
        "yt-dlp fallback",
    )


class RumbleEngine(EmbedFallbackEngine):
    name = "RumbleEnginePro"
    platform_label = "Rumble"
    domains = ("rumble.com", "www.rumble.com")
    resolver_name = "resolve_rumble"
    embed_headers = {"Referer": "https://rumble.com/"}
    techniques = (
        "Rumble embedJS JSON (u3 mpeg quality ladder)",
        "oEmbed title/thumbnail augmentation",
        "CDN candidate walk with audio verification",
        "yt-dlp fallback",
    )


class OdyseeEngine(EmbedFallbackEngine):
    name = "OdyseeEnginePro"
    platform_label = "Odysee"
    domains = ("odysee.com", "www.odysee.com")
    resolver_name = "resolve_odysee"
    embed_headers = {"Referer": "https://odysee.com/"}
    techniques = (
        "LBRY JSON-RPC get() streaming_url (blockchain playback)",
        "og:title/og:image metadata",
        "CDN candidate walk with audio verification",
        "yt-dlp fallback",
    )


class NiconicoEngine(EmbedFallbackEngine):
    name = "NiconicoEnginePro"
    platform_label = "Niconico"
    domains = ("nicovideo.jp", "nico.ms", "www.nicovideo.jp")
    embed_headers = {"Referer": "https://www.nicovideo.jp/"}
    techniques = (
        "Niconico embed resolver (og:video/JSON-LD/<video>/script/oEmbed, browser TLS)",
        "CDN candidate walk with audio verification",
        "yt-dlp Niconico extractor fallback",
    )


class DouyinEngine(EmbedFallbackEngine):
    name = "DouyinEnginePro"
    platform_label = "Douyin"
    domains = ("douyin.com", "v.douyin.com", "www.douyin.com", "m.douyin.com")
    embed_headers = {"Referer": "https://www.douyin.com/"}
    techniques = (
        "Douyin embed resolver (og:video/JSON-LD/<video>/script/oEmbed, browser TLS)",
        "CDN candidate walk with audio verification",
        "yt-dlp Douyin extractor fallback",
    )


class XiaohongshuEngine(EmbedFallbackEngine):
    name = "XiaohongshuEnginePro"
    platform_label = "Xiaohongshu"
    domains = ("xiaohongshu.com", "xhslink.com", "www.xiaohongshu.com", "m.xiaohongshu.com")
    embed_headers = {"Referer": "https://www.xiaohongshu.com/"}
    techniques = (
        "Xiaohongshu embed resolver (og:video/JSON-LD/<video>/script/oEmbed, browser TLS)",
        "CDN candidate walk with audio verification",
        "yt-dlp Xiaohongshu extractor fallback",
    )


class BlueskyEngine(EmbedFallbackEngine):
    name = "BlueskyEnginePro"
    platform_label = "Bluesky"
    domains = ("bsky.app", "staging.bsky.app")
    resolver_name = "resolve_bluesky"
    embed_headers = {"Referer": "https://bsky.app/"}
    techniques = (
        "Bluesky public xrpc API (resolveHandle + getPostThread)",
        "video embed playlist walk (mp4/m3u8)",
        "CDN candidate walk with audio verification",
        "yt-dlp fallback",
    )


class KickEngine(EmbedFallbackEngine):
    name = "KickEnginePro"
    platform_label = "Kick"
    domains = ("kick.com", "www.kick.com")
    resolver_name = "resolve_kick"
    embed_headers = {"Referer": "https://kick.com/"}
    techniques = (
        "Kick public JSON API v2 (video playback_url / clip clip_url)",
        "og:title/og:image metadata",
        "CDN candidate walk with audio verification",
        "yt-dlp fallback",
    )


class RutubeEngine(EmbedFallbackEngine):
    name = "RutubeEnginePro"
    platform_label = "Rutube"
    domains = ("rutube.ru", "www.rutube.ru")
    resolver_name = "resolve_rutube"
    embed_headers = {"Referer": "https://rutube.ru/"}
    techniques = (
        "Rutube play/options JSON (video_balancer.m3u8)",
        "title/thumbnail/duration extraction",
        "native HLS fast path",
        "yt-dlp fallback",
    )


class KuaishouEngine(EmbedFallbackEngine):
    name = "KuaishouEnginePro"
    platform_label = "Kuaishou"
    domains = (
        "kuaishou.com", "v.kuaishou.com", "www.kuaishou.com",
        "m.kuaishou.com", "gifshow.com", "kuaishouapp.com",
    )
    embed_headers = {"Referer": "https://www.kuaishou.com/"}
    techniques = (
        "Kuaishou embed resolver (og:video/JSON-LD/<video>/script/oEmbed, browser TLS)",
        "CDN candidate walk with audio verification",
        "yt-dlp Kuaishou extractor fallback",
    )


class NaverTVEngine(EmbedFallbackEngine):
    name = "NaverTVEnginePro"
    platform_label = "Naver TV"
    domains = ("tv.naver.com", "m.tv.naver.com")
    embed_headers = {"Referer": "https://tv.naver.com/"}
    techniques = (
        "Naver TV embed resolver (og:video/JSON-LD/<video>/script/oEmbed, browser TLS)",
        "CDN candidate walk with audio verification",
        "yt-dlp Naver extractor fallback",
    )


class KakaoTVEngine(EmbedFallbackEngine):
    name = "KakaoTVEnginePro"
    platform_label = "KakaoTV"
    domains = ("tv.kakao.com", "m.tv.kakao.com")
    embed_headers = {"Referer": "https://tv.kakao.com/"}
    techniques = (
        "KakaoTV embed resolver (og:video/JSON-LD/<video>/script/oEmbed, browser TLS)",
        "CDN candidate walk with audio verification",
        "yt-dlp Kakao extractor fallback",
    )


class MastodonEngine(EmbedFallbackEngine):
    name = "MastodonEnginePro"
    platform_label = "Mastodon"
    domains = (
        "mastodon.social", "mas.to", "mastodon.online", "mstdn.social",
        "fosstodon.org", "mastodon.world", "techhub.social", "mastodon.art",
        "universeodon.com", "infosec.exchange",
    )
    techniques = (
        "Mastodon embed resolver (og:video/JSON-LD/<video>/script/oEmbed, browser TLS)",
        "CDN candidate walk with audio verification",
        "yt-dlp generic attachment extractor fallback",
    )


class WeiboEngine(EmbedFallbackEngine):
    name = "WeiboEnginePro"
    platform_label = "Weibo"
    domains = ("weibo.com", "weibo.tv", "m.weibo.cn", "www.weibo.com")
    resolver_name = "resolve_weibo"
    embed_headers = {"Referer": "https://weibo.com/"}
    techniques = (
        "Weibo render_data CDN mining (f.video.weibocdn.com)",
        "og/JSON-LD/<video> fallback collector",
        "CDN candidate walk with audio verification",
        "yt-dlp fallback",
    )


class NineGagEngine(EmbedFallbackEngine):
    name = "NineGagEnginePro"
    platform_label = "9GAG"
    domains = ("9gag.com", "www.9gag.com", "m.9gag.com")
    embed_headers = {"Referer": "https://9gag.com/"}
    techniques = (
        "9GAG embed resolver (og:video/JSON-LD/<video>/script/oEmbed, browser TLS)",
        "CDN candidate walk with audio verification",
        "yt-dlp fallback",
    )


class Lemon8Engine(EmbedFallbackEngine):
    name = "Lemon8EnginePro"
    platform_label = "Lemon8"
    domains = ("lemon8-app.com", "www.lemon8-app.com")
    embed_headers = {"Referer": "https://www.lemon8-app.com/"}
    techniques = (
        "Lemon8 embed resolver (og:video/JSON-LD/<video>/script/oEmbed, browser TLS)",
        "CDN candidate walk with audio verification",
        "yt-dlp fallback",
    )


class DzenEngine(EmbedFallbackEngine):
    name = "DzenEnginePro"
    platform_label = "Dzen"
    domains = ("dzen.ru", "zen.yandex.ru", "www.dzen.ru")
    embed_headers = {"Referer": "https://dzen.ru/"}
    techniques = (
        "Dzen embed resolver (og:video/JSON-LD/<video>/script/oEmbed, browser TLS)",
        "CDN candidate walk with audio verification",
        "yt-dlp Dzen extractor fallback",
    )


class TedEngine(EmbedFallbackEngine):
    name = "TedEnginePro"
    platform_label = "TED"
    domains = ("ted.com", "www.ted.com")
    embed_headers = {"Referer": "https://www.ted.com/"}
    techniques = (
        "TED embed resolver (og:video/JSON-LD/<video>/script/oEmbed, browser TLS)",
        "CDN candidate walk with audio verification",
        "yt-dlp TED extractor fallback",
    )


class MixcloudEngine(AudioFallbackEngine):
    name = "MixcloudEnginePro"
    platform_label = "Mixcloud"
    domains = ("mixcloud.com", "www.mixcloud.com")
    techniques = ("yt-dlp Mixcloud extractor (cloudcast audio stream + metadata)",)


class BandcampEngine(AudioFallbackEngine):
    name = "BandcampEnginePro"
    platform_label = "Bandcamp"
    domains = ("bandcamp.com",)
    techniques = ("yt-dlp Bandcamp extractor (lossless/MP3 audio + metadata)",)


class ChzzkEngine(EmbedFallbackEngine):
    name = "ChzzkEnginePro"
    platform_label = "Chzzk"
    domains = ("chzzk.naver.com",)
    resolver_name = "resolve_chzzk"
    embed_headers = {"Referer": "https://chzzk.naver.com/"}
    techniques = (
        "Chzzk public JSON API (VOD / live-detail metadata)",
        "SPA render/livePlaybackJson media mining",
        "CDN candidate walk with audio verification",
        "yt-dlp fallback",
    )


class NaverBlogEngine(EmbedFallbackEngine):
    name = "NaverBlogEnginePro"
    platform_label = "Naver Blog"
    domains = ("blog.naver.com",)
    embed_headers = {"Referer": "https://blog.naver.com/"}
    techniques = (
        "Naver Blog embed resolver (og:video/JSON-LD/<video>/script/oEmbed, browser TLS)",
        "CDN candidate walk with audio verification",
        "yt-dlp fallback",
    )


class NaverCafeEngine(EmbedFallbackEngine):
    name = "NaverCafeEnginePro"
    platform_label = "Naver Cafe"
    domains = ("cafe.naver.com",)
    embed_headers = {"Referer": "https://cafe.naver.com/"}
    techniques = (
        "Naver Cafe embed resolver (og:video/JSON-LD/<video>/script/oEmbed, browser TLS)",
        "CDN candidate walk with audio verification",
        "yt-dlp fallback",
    )


class NaverPostEngine(EmbedFallbackEngine):
    name = "NaverPostEnginePro"
    platform_label = "Naver Post"
    domains = ("post.naver.com",)
    embed_headers = {"Referer": "https://post.naver.com/"}
    techniques = (
        "Naver Post embed resolver (og:video/JSON-LD/<video>/script/oEmbed, browser TLS)",
        "CDN candidate walk with audio verification",
        "yt-dlp fallback",
    )


class AfreecaTVEngine(EmbedFallbackEngine):
    name = "AfreecaTVEnginePro"
    platform_label = "AfreecaTV / SOOP"
    domains = ("afreecatv.com", "soop.co.kr", "sooplive.co.kr")
    embed_headers = {"Referer": "https://www.soop.co.kr/"}
    techniques = (
        "AfreecaTV/SOOP embed resolver (og:video/JSON-LD/<video>/script/oEmbed, browser TLS)",
        "CDN candidate walk with audio verification",
        "yt-dlp fallback",
    )


class AparatEngine(EmbedFallbackEngine):
    name = "AparatEnginePro"
    platform_label = "Aparat"
    domains = ("aparat.com",)
    embed_headers = {"Referer": "https://www.aparat.com/"}
    techniques = (
        "Aparat embed resolver (og:video/JSON-LD/<video>/script/oEmbed, browser TLS)",
        "CDN candidate walk with audio verification",
        "yt-dlp Aparat extractor fallback",
    )


class FC2VideoEngine(EmbedFallbackEngine):
    name = "FC2VideoEnginePro"
    platform_label = "FC2 Video"
    domains = ("video.fc2.com",)
    embed_headers = {"Referer": "https://video.fc2.com/"}
    techniques = (
        "FC2 Video embed resolver (og:video/JSON-LD/<video>/script/oEmbed, browser TLS)",
        "CDN candidate walk with audio verification",
        "yt-dlp FC2 extractor fallback",
    )


class BitChuteEngine(EmbedFallbackEngine):
    name = "BitChuteEnginePro"
    platform_label = "BitChute"
    domains = ("bitchute.com", "www.bitchute.com")
    embed_headers = {"Referer": "https://www.bitchute.com/"}
    techniques = (
        "BitChute embed resolver (og:video/JSON-LD/<video>/script/oEmbed, browser TLS)",
        "CDN candidate walk with audio verification",
        "yt-dlp BitChute extractor fallback",
    )


class TVerEngine(EmbedFallbackEngine):
    name = "TVerEnginePro"
    platform_label = "TVer"
    domains = ("tver.jp",)
    embed_headers = {"Referer": "https://tver.jp/"}
    techniques = (
        "TVer embed resolver (og:video/JSON-LD/<video>/script/oEmbed, browser TLS)",
        "CDN candidate walk with audio verification",
        "yt-dlp fallback",
    )


class OpenrecEngine(EmbedFallbackEngine):
    name = "OpenrecEnginePro"
    platform_label = "OPENREC"
    domains = ("openrec.tv", "www.openrec.tv")
    embed_headers = {"Referer": "https://www.openrec.tv/"}
    techniques = (
        "OPENREC embed resolver (og:video/JSON-LD/<video>/script/oEmbed, browser TLS)",
        "CDN candidate walk with audio verification",
        "yt-dlp fallback",
    )


class IQiyiEngine(EmbedFallbackEngine):
    name = "IQiyiEnginePro"
    platform_label = "iQIYI"
    domains = ("iq.com", "iqiyi.com", "www.iq.com")
    embed_headers = {"Referer": "https://www.iq.com/"}
    techniques = (
        "iQIYI embed resolver (og:video/JSON-LD/<video>/script/oEmbed, browser TLS)",
        "CDN candidate walk with audio verification",
        "yt-dlp iQIYI extractor fallback",
    )


class TencentVideoEngine(EmbedFallbackEngine):
    name = "TencentVideoEnginePro"
    platform_label = "Tencent Video"
    domains = ("v.qq.com",)
    embed_headers = {"Referer": "https://v.qq.com/"}
    techniques = (
        "Tencent Video embed resolver (og:video/JSON-LD/<video>/script/oEmbed, browser TLS)",
        "CDN candidate walk with audio verification",
        "yt-dlp fallback",
    )


class YoukuEngine(EmbedFallbackEngine):
    name = "YoukuEnginePro"
    platform_label = "Youku"
    domains = ("youku.com", "v.youku.com")
    embed_headers = {"Referer": "https://www.youku.com/"}
    techniques = (
        "Youku embed resolver (og:video/JSON-LD/<video>/script/oEmbed, browser TLS)",
        "CDN candidate walk with audio verification",
        "yt-dlp Youku extractor fallback",
    )


class MangoTVEngine(EmbedFallbackEngine):
    name = "MangoTVEnginePro"
    platform_label = "MangoTV"
    domains = ("mgtv.com", "hunantv.com", "www.mgtv.com")
    embed_headers = {"Referer": "https://www.mgtv.com/"}
    techniques = (
        "MangoTV embed resolver (og:video/JSON-LD/<video>/script/oEmbed, browser TLS)",
        "CDN candidate walk with audio verification",
        "yt-dlp fallback",
    )


class ShareChatEngine(EmbedFallbackEngine):
    name = "ShareChatEnginePro"
    platform_label = "ShareChat"
    domains = ("sharechat.com", "www.sharechat.com")
    embed_headers = {"Referer": "https://sharechat.com/"}
    techniques = (
        "ShareChat embed resolver (og:video/JSON-LD/<video>/script/oEmbed, browser TLS)",
        "CDN candidate walk with audio verification",
        "yt-dlp fallback",
    )


class MojEngine(EmbedFallbackEngine):
    name = "MojEnginePro"
    platform_label = "Moj"
    domains = ("mojapp.in", "www.mojapp.in")
    embed_headers = {"Referer": "https://mojapp.in/"}
    techniques = (
        "Moj embed resolver (og:video/JSON-LD/<video>/script/oEmbed, browser TLS)",
        "CDN candidate walk with audio verification",
        "yt-dlp fallback",
    )


class NamashaEngine(EmbedFallbackEngine):
    name = "NamashaEnginePro"
    platform_label = "Namasha"
    domains = ("namasha.com", "www.namasha.com")
    embed_headers = {"Referer": "https://www.namasha.com/"}
    techniques = (
        "Namasha embed resolver (og:video/JSON-LD/<video>/script/oEmbed, browser TLS)",
        "CDN candidate walk with audio verification",
        "yt-dlp fallback",
    )


class AudiomackEngine(AudioFallbackEngine):
    name = "AudiomackEnginePro"
    platform_label = "Audiomack"
    domains = ("audiomack.com", "www.audiomack.com")
    techniques = ("yt-dlp Audiomack extractor (audio stream + metadata)",)


class DeezerEngine(AudioFallbackEngine):
    name = "DeezerEnginePro"
    platform_label = "Deezer"
    domains = ("deezer.com", "www.deezer.com")
    techniques = ("yt-dlp Deezer extractor (preview/stream where licensed)",)


class PeerTubeEngine(EmbedFallbackEngine):
    name = "PeerTubeEnginePro"
    platform_label = "PeerTube"
    domains = (
        "video.blender.org", "diode.zone", "spectra.video", "peertube.stream",
    )
    embed_headers = {"Referer": "https://joinpeertube.org/"}
    techniques = (
        "PeerTube embed resolver (og:video/JSON-LD/<video>/script/oEmbed, browser TLS)",
        "CDN candidate walk with audio verification",
        "yt-dlp PeerTube extractor fallback",
    )
