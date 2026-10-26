from __future__ import annotations

import os
from typing import Callable

from config.settings import AUDIO_BITRATE, TEMP_DIR
from utils.helpers import sanitize_filename

from services.engines.base import PlatformEngine
from utils.logger import download_logger, error_logger
from services.engines.generic import EmbedFallbackEngine, YtDlpFallbackEngine

class ThreadsEngine(EmbedFallbackEngine):
    name = "ThreadsEnginePro"
    platform_label = "Threads"
    domains = ("threads.net", "www.threads.net")
    resolver_name = "resolve_threads"
    embed_headers = {"Referer": "https://www.threads.net/"}

class VimeoEngine(EmbedFallbackEngine):
    """Vimeo: player-config JSON resolver (progressive mp4 w/ audio) + yt-dlp fallback."""
    name = "VimeoEnginePro"
    platform_label = "Vimeo"
    domains = ("vimeo.com", "player.vimeo.com")
    resolver_name = "resolve_vimeo"
    embed_headers = {"Referer": "https://vimeo.com/"}


class DailymotionEngine(EmbedFallbackEngine):
    name = "DailymotionEnginePro"
    platform_label = "Dailymotion"
    domains = ("dailymotion.com", "dai.ly")
    resolver_name = "resolve_dailymotion"
    embed_headers = {"Referer": "https://www.dailymotion.com/"}

class SnapchatEngine(EmbedFallbackEngine):
    name = "SnapchatEnginePro"
    platform_label = "Snapchat"
    domains = ("snapchat.com", "story.snapchat.com")
    resolver_name = "resolve_snapchat"
    embed_headers = {"Referer": "https://www.snapchat.com/"}

class TelegramPublicEngine(EmbedFallbackEngine):
    name = "TelegramPublicEnginePro"
    platform_label = "Telegram"
    domains = ("t.me", "telegram.me", "telegram.dog")
    resolver_name = "resolve_telegram"
    embed_headers = {"Referer": "https://t.me/"}

class LikeeEngine(EmbedFallbackEngine):
    name = "LikeeEnginePro"
    platform_label = "Likee"
    domains = ("likee.video", "likee.com", "l.likee.video", "mobile.likee.video")
    embed_headers = {"Referer": "https://likee.video/"}

class KwaiEngine(EmbedFallbackEngine):
    name = "KwaiEnginePro"
    platform_label = "Kwai"
    domains = ("kwai.com", "kw.ai", "s.kw.ai", "m.kwai.com", "kwai-video.com")
    embed_headers = {"Referer": "https://www.kwai.com/"}

class TwitchEngine(EmbedFallbackEngine):
    name = "TwitchEnginePro"
    platform_label = "Twitch"
    domains = ("twitch.tv", "clips.twitch.tv", "m.twitch.tv", "player.twitch.tv")
    resolver_name = "resolve_twitch"
    embed_headers = {"Referer": "https://clips.twitch.tv/"}

class BilibiliEngine(EmbedFallbackEngine):
    name = "BilibiliEnginePro"
    platform_label = "Bilibili"
    domains = ("bilibili.com", "b23.tv", "bilibili.tv", "m.bilibili.com")
    resolver_name = "resolve_bilibili"
    embed_headers = {"Referer": "https://www.bilibili.com/"}

class VkEngine(EmbedFallbackEngine):
    name = "VkEnginePro"
    platform_label = "VK"
    domains = ("vk.com", "vkvideo.ru", "vk.cc", "m.vk.com", "vkvideo.cc")
    resolver_name = "resolve_vk"
    embed_headers = {"Referer": "https://vk.com/"}

class TumblrEngine(EmbedFallbackEngine):
    name = "TumblrEnginePro"
    platform_label = "Tumblr"
    domains = ("tumblr.com", "www.tumblr.com", "at.tumblr.com", "tmblr.co")
    resolver_name = "resolve_tumblr"
    embed_headers = {"Referer": "https://www.tumblr.com/"}

class ImgurEngine(EmbedFallbackEngine):
    """Imgur: direct mp4 resolver for video/GIF posts; images stay on yt-dlp."""
    name = "ImgurEnginePro"
    platform_label = "Imgur"
    domains = ("imgur.com", "i.imgur.com", "m.imgur.com", "stack.imgur.com")
    resolver_name = "resolve_imgur"
    embed_headers = {"Referer": "https://imgur.com/"}


class StreamableEngine(EmbedFallbackEngine):
    """Streamable: public JSON API + HTML og:video resolver, then yt-dlp fallback."""
    name = "StreamableEnginePro"
    platform_label = "Streamable"
    domains = ("streamable.com", "www.streamable.com")
    resolver_name = "resolve_streamable"
    embed_headers = {"Referer": "https://streamable.com/"}


class CoubEngine(EmbedFallbackEngine):
    """Coub: public API v2 html5 file_versions resolver, then yt-dlp fallback."""
    name = "CoubEnginePro"
    platform_label = "Coub"
    domains = ("coub.com", "www.coub.com")
    resolver_name = "resolve_coub"
    embed_headers = {"Referer": "https://coub.com/"}


class OdnoklassnikiEngine(EmbedFallbackEngine):
    name = "OdnoklassnikiEnginePro"
    platform_label = "Odnoklassniki"
    domains = ("ok.ru", "m.ok.ru", "odnoklassniki.ru", "m.odnoklassniki.ru")
    resolver_name = "resolve_odnoklassniki"
    embed_headers = {"Referer": "https://ok.ru/"}

class RumbleEngine(EmbedFallbackEngine):
    name = "RumbleEnginePro"
    platform_label = "Rumble"
    domains = ("rumble.com", "www.rumble.com")
    resolver_name = "resolve_rumble"
    embed_headers = {"Referer": "https://rumble.com/"}

class OdyseeEngine(EmbedFallbackEngine):
    name = "OdyseeEnginePro"
    platform_label = "Odysee"
    domains = ("odysee.com", "www.odysee.com")
    resolver_name = "resolve_odysee"
    embed_headers = {"Referer": "https://odysee.com/"}

class NiconicoEngine(EmbedFallbackEngine):
    name = "NiconicoEnginePro"
    platform_label = "Niconico"
    domains = ("nicovideo.jp", "nico.ms", "www.nicovideo.jp")
    embed_headers = {"Referer": "https://www.nicovideo.jp/"}

class DouyinEngine(EmbedFallbackEngine):
    name = "DouyinEnginePro"
    platform_label = "Douyin"
    domains = ("douyin.com", "v.douyin.com", "www.douyin.com", "m.douyin.com")
    embed_headers = {"Referer": "https://www.douyin.com/"}

class XiaohongshuEngine(EmbedFallbackEngine):
    name = "XiaohongshuEnginePro"
    platform_label = "Xiaohongshu"
    domains = ("xiaohongshu.com", "xhslink.com", "www.xiaohongshu.com", "m.xiaohongshu.com")
    embed_headers = {"Referer": "https://www.xiaohongshu.com/"}

class BlueskyEngine(EmbedFallbackEngine):
    name = "BlueskyEnginePro"
    platform_label = "Bluesky"
    domains = ("bsky.app", "staging.bsky.app")
    resolver_name = "resolve_bluesky"
    embed_headers = {"Referer": "https://bsky.app/"}

class KickEngine(EmbedFallbackEngine):
    name = "KickEnginePro"
    platform_label = "Kick"
    domains = ("kick.com", "www.kick.com")
    resolver_name = "resolve_kick"
    embed_headers = {"Referer": "https://kick.com/"}

class RutubeEngine(EmbedFallbackEngine):
    name = "RutubeEnginePro"
    platform_label = "Rutube"
    domains = ("rutube.ru", "www.rutube.ru")
    resolver_name = "resolve_rutube"
    embed_headers = {"Referer": "https://rutube.ru/"}

class KuaishouEngine(EmbedFallbackEngine):
    name = "KuaishouEnginePro"
    platform_label = "Kuaishou"
    domains = (
        "kuaishou.com", "v.kuaishou.com", "www.kuaishou.com",
        "m.kuaishou.com", "gifshow.com", "kuaishouapp.com",
    )
    embed_headers = {"Referer": "https://www.kuaishou.com/"}

class NaverTVEngine(EmbedFallbackEngine):
    name = "NaverTVEnginePro"
    platform_label = "Naver TV"
    domains = ("tv.naver.com", "m.tv.naver.com")
    embed_headers = {"Referer": "https://tv.naver.com/"}

class KakaoTVEngine(EmbedFallbackEngine):
    name = "KakaoTVEnginePro"
    platform_label = "KakaoTV"
    domains = ("tv.kakao.com", "m.tv.kakao.com")
    embed_headers = {"Referer": "https://tv.kakao.com/"}

class MastodonEngine(EmbedFallbackEngine):
    name = "MastodonEnginePro"
    platform_label = "Mastodon"
    domains = (
        "mastodon.social", "mas.to", "mastodon.online", "mstdn.social",
        "fosstodon.org", "mastodon.world", "techhub.social", "mastodon.art",
        "universeodon.com", "infosec.exchange",
    )

class WeiboEngine(EmbedFallbackEngine):
    name = "WeiboEnginePro"
    platform_label = "Weibo"
    domains = ("weibo.com", "weibo.tv", "m.weibo.cn", "www.weibo.com")
    resolver_name = "resolve_weibo"
    embed_headers = {"Referer": "https://weibo.com/"}

class NineGagEngine(EmbedFallbackEngine):
    name = "NineGagEnginePro"
    platform_label = "9GAG"
    domains = ("9gag.com", "www.9gag.com", "m.9gag.com")
    embed_headers = {"Referer": "https://9gag.com/"}

class Lemon8Engine(EmbedFallbackEngine):
    name = "Lemon8EnginePro"
    platform_label = "Lemon8"
    domains = ("lemon8-app.com", "www.lemon8-app.com")
    embed_headers = {"Referer": "https://www.lemon8-app.com/"}

class DzenEngine(EmbedFallbackEngine):
    name = "DzenEnginePro"
    platform_label = "Dzen"
    domains = ("dzen.ru", "zen.yandex.ru", "www.dzen.ru")
    embed_headers = {"Referer": "https://dzen.ru/"}

class TedEngine(EmbedFallbackEngine):
    name = "TedEnginePro"
    platform_label = "TED"
    domains = ("ted.com", "www.ted.com")
    embed_headers = {"Referer": "https://www.ted.com/"}

class MixcloudEngine(EmbedFallbackEngine):
    name = "MixcloudEnginePro"
    platform_label = "Mixcloud"
    domains = ("mixcloud.com", "www.mixcloud.com")
    embed_headers = {"Referer": "https://www.mixcloud.com/"}

class BandcampEngine(EmbedFallbackEngine):
    name = "BandcampEnginePro"
    platform_label = "Bandcamp"
    domains = ("bandcamp.com",)
    embed_headers = {"Referer": "https://bandcamp.com/"}

class ChzzkEngine(EmbedFallbackEngine):
    name = "ChzzkEnginePro"
    platform_label = "Chzzk"
    domains = ("chzzk.naver.com",)
    resolver_name = "resolve_chzzk"
    embed_headers = {"Referer": "https://chzzk.naver.com/"}

class NaverBlogEngine(EmbedFallbackEngine):
    name = "NaverBlogEnginePro"
    platform_label = "Naver Blog"
    domains = ("blog.naver.com",)
    embed_headers = {"Referer": "https://blog.naver.com/"}

class NaverCafeEngine(EmbedFallbackEngine):
    name = "NaverCafeEnginePro"
    platform_label = "Naver Cafe"
    domains = ("cafe.naver.com",)
    embed_headers = {"Referer": "https://cafe.naver.com/"}

class NaverPostEngine(EmbedFallbackEngine):
    name = "NaverPostEnginePro"
    platform_label = "Naver Post"
    domains = ("post.naver.com",)
    embed_headers = {"Referer": "https://post.naver.com/"}

class AfreecaTVEngine(EmbedFallbackEngine):
    name = "AfreecaTVEnginePro"
    platform_label = "AfreecaTV / SOOP"
    domains = ("afreecatv.com", "soop.co.kr", "sooplive.co.kr")
    embed_headers = {"Referer": "https://www.soop.co.kr/"}

class AparatEngine(EmbedFallbackEngine):
    name = "AparatEnginePro"
    platform_label = "Aparat"
    domains = ("aparat.com",)
    embed_headers = {"Referer": "https://www.aparat.com/"}

class FC2VideoEngine(EmbedFallbackEngine):
    name = "FC2VideoEnginePro"
    platform_label = "FC2 Video"
    domains = ("video.fc2.com",)
    embed_headers = {"Referer": "https://video.fc2.com/"}

class BitChuteEngine(EmbedFallbackEngine):
    name = "BitChuteEnginePro"
    platform_label = "BitChute"
    domains = ("bitchute.com", "www.bitchute.com")
    embed_headers = {"Referer": "https://www.bitchute.com/"}

class TVerEngine(EmbedFallbackEngine):
    name = "TVerEnginePro"
    platform_label = "TVer"
    domains = ("tver.jp",)
    embed_headers = {"Referer": "https://tver.jp/"}

class OpenrecEngine(EmbedFallbackEngine):
    name = "OpenrecEnginePro"
    platform_label = "OPENREC"
    domains = ("openrec.tv", "www.openrec.tv")
    embed_headers = {"Referer": "https://www.openrec.tv/"}

class IQiyiEngine(EmbedFallbackEngine):
    name = "IQiyiEnginePro"
    platform_label = "iQIYI"
    domains = ("iq.com", "iqiyi.com", "www.iq.com")
    embed_headers = {"Referer": "https://www.iq.com/"}

class TencentVideoEngine(EmbedFallbackEngine):
    name = "TencentVideoEnginePro"
    platform_label = "Tencent Video"
    domains = ("v.qq.com",)
    embed_headers = {"Referer": "https://v.qq.com/"}

class YoukuEngine(EmbedFallbackEngine):
    name = "YoukuEnginePro"
    platform_label = "Youku"
    domains = ("youku.com", "v.youku.com")
    embed_headers = {"Referer": "https://www.youku.com/"}

class MangoTVEngine(EmbedFallbackEngine):
    name = "MangoTVEnginePro"
    platform_label = "MangoTV"
    domains = ("mgtv.com", "hunantv.com", "www.mgtv.com")
    embed_headers = {"Referer": "https://www.mgtv.com/"}

class ShareChatEngine(EmbedFallbackEngine):
    name = "ShareChatEnginePro"
    platform_label = "ShareChat"
    domains = ("sharechat.com", "www.sharechat.com")
    embed_headers = {"Referer": "https://sharechat.com/"}

class MojEngine(EmbedFallbackEngine):
    name = "MojEnginePro"
    platform_label = "Moj"
    domains = ("mojapp.in", "www.mojapp.in")
    embed_headers = {"Referer": "https://mojapp.in/"}

class NamashaEngine(EmbedFallbackEngine):
    name = "NamashaEnginePro"
    platform_label = "Namasha"
    domains = ("namasha.com", "www.namasha.com")
    embed_headers = {"Referer": "https://www.namasha.com/"}

class AudiomackEngine(EmbedFallbackEngine):
    name = "AudiomackEnginePro"
    platform_label = "Audiomack"
    domains = ("audiomack.com", "www.audiomack.com")
    embed_headers = {"Referer": "https://audiomack.com/"}

class DeezerEngine(EmbedFallbackEngine):
    name = "DeezerEnginePro"
    platform_label = "Deezer"
    domains = ("deezer.com", "www.deezer.com")
    embed_headers = {"Referer": "https://www.deezer.com/"}

class PeerTubeEngine(EmbedFallbackEngine):
    name = "PeerTubeEnginePro"
    platform_label = "PeerTube"
    domains = (
        "video.blender.org", "diode.zone", "spectra.video", "peertube.stream",
    )
    embed_headers = {"Referer": "https://joinpeertube.org/"}


