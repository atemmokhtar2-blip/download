"""HLS master-playlist resolution — pure parser + selector unit tests.

These tests pin the behaviour that stops the downloader from feeding a master
(variant) playlist straight into a single-input ffmpeg run, which would pick
one rendition on its own (often the highest-bandwidth VIDEO-ONLY variant) and
produce a silent MP4. They are pure: the parser/selector never touch the
network, so fixtures are plain HLS text.
"""
from __future__ import annotations

import services.hls as hls


# ---------------------------------------------------------------------------
# Codec classification + attribute parsing
# ---------------------------------------------------------------------------

def test_codec_kind_classification() -> None:
    assert hls._codec_kind("avc1.4d401f") == "video"
    assert hls._codec_kind("vp09.00.40.08") == "video"
    assert hls._codec_kind("hvc1.1.6.L93.B0") == "video"
    assert hls._codec_kind("mp4a.40.2") == "audio"
    assert hls._codec_kind("opus") == "audio"
    assert hls._codec_kind("ec-3") == "audio"
    assert hls._codec_kind("") is None
    assert hls._codec_kind("totally-unknown") is None


def test_parse_attrs_keeps_quoted_comma_intact() -> None:
    attrs = hls._parse_attrs(
        'BANDWIDTH=1280000,CODECS="avc1.4d401f,mp4a.40.2",RESOLUTION=1280x720'
    )
    assert attrs["BANDWIDTH"] == "1280000"
    assert attrs["CODECS"] == "avc1.4d401f,mp4a.40.2"
    assert attrs["RESOLUTION"] == "1280x720"


def test_parse_attrs_strips_quotes() -> None:
    attrs = hls._parse_attrs('GROUP-ID="audio",DEFAULT=YES,URI="audio/en.m3u8"')
    assert attrs["GROUP-ID"] == "audio"
    assert attrs["DEFAULT"] == "YES"
    assert attrs["URI"] == "audio/en.m3u8"


# ---------------------------------------------------------------------------
# Master playlist parsing
# ---------------------------------------------------------------------------

_MUXED_MASTER = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=1280000,RESOLUTION=1280x720,CODECS="avc1.4d401f,mp4a.40.2"
v720/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=400000,RESOLUTION=640x360,CODECS="avc1.4d401e,mp4a.40.2"
v360/index.m3u8
"""

_SPLIT_MASTER = """#EXTM3U
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio",NAME="English",DEFAULT=YES,AUTOSELECT=YES,URI="audio/en.m3u8"
#EXT-X-STREAM-INF:BANDWIDTH=2000000,RESOLUTION=1920x1080,CODECS="avc1.640028",AUDIO="audio"
v1080/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=1280x720,CODECS="avc1.4d401f",AUDIO="audio"
v720/index.m3u8
"""


def test_is_master_playlist() -> None:
    assert hls.is_master_playlist(_MUXED_MASTER) is True
    assert hls.is_master_playlist(_SPLIT_MASTER) is True
    media = "#EXTM3U\n#EXTINF:6.0,\nseg0.ts\n"
    assert hls.is_master_playlist(media) is False
    assert hls.is_master_playlist("") is False


def test_parse_muxed_master() -> None:
    variants, audio = hls.parse_master_playlist(_MUXED_MASTER, "https://cdn.example/hls/master.m3u8")
    assert audio == []
    assert len(variants) == 2

    top = variants[0]
    assert top.has_video is True
    assert top.has_audio is True
    assert top.height == 720
    assert top.bandwidth == 1280000
    assert top.uri == "https://cdn.example/hls/v720/index.m3u8"

    lower = variants[1]
    assert lower.has_video is True
    assert lower.has_audio is True
    assert lower.height == 360


def test_parse_split_master_video_only_and_audio() -> None:
    variants, audio = hls.parse_master_playlist(_SPLIT_MASTER, "https://cdn.example/master.m3u8")
    assert len(audio) == 1
    assert audio[0].uri == "https://cdn.example/audio/en.m3u8"
    assert audio[0].audio_group == "audio"
    assert audio[0].default is True

    assert len(variants) == 2
    for v in variants:
        assert v.has_video is True
        assert v.has_audio is False  # audio is a separate rendition
        assert v.audio_group == "audio"

    # Quality sorting is done in the selector, not the parser, but the parser
    # must preserve heights/bandwidths for the selector.
    assert variants[0].height == 1080
    assert variants[1].height == 720


def test_parse_resolves_relative_uri_against_base() -> None:
    variants, _ = hls.parse_master_playlist(
        "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1,CODECS=\"avc1.4d401f,mp4a.40.2\"\nsub/index.m3u8\n",
        "https://host.com/path/deep/master.m3u8",
    )
    assert variants[0].uri == "https://host.com/path/deep/sub/index.m3u8"


# ---------------------------------------------------------------------------
# Plan selection
# ---------------------------------------------------------------------------

def test_select_prefers_muxed_over_split() -> None:
    variants, audio = hls.parse_master_playlist(
        """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=500000,RESOLUTION=640x360,CODECS="avc1.4d401e,mp4a.40.2"
muxed.m3u8
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="a",URI="a.m3u8"
#EXT-X-STREAM-INF:BANDWIDTH=2000000,RESOLUTION=1920x1080,CODECS="avc1.640028",AUDIO="a"
video.m3u8
""",
        "https://cdn.example/master.m3u8",
    )
    plans = hls.select_hls_plans(variants, audio)
    assert plans and plans[0].kind == "muxed"
    assert plans[0].video_uri == "https://cdn.example/muxed.m3u8"
    assert plans[0].audio_uri is None


def test_select_split_uses_best_video_and_matching_audio() -> None:
    variants, audio = hls.parse_master_playlist(_SPLIT_MASTER, "https://cdn.example/master.m3u8")
    plans = hls.select_hls_plans(variants, audio)
    assert len(plans) == 1
    plan = plans[0]
    assert plan.kind == "split"
    assert plan.video_uri == "https://cdn.example/v1080/index.m3u8"  # highest height
    assert plan.audio_uri == "https://cdn.example/audio/en.m3u8"


def test_select_no_audio_rendition_yields_no_split() -> None:
    variants, audio = hls.parse_master_playlist(
        """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=2000000,RESOLUTION=1920x1080,CODECS="avc1.640028"
video_only.m3u8
""",
        "https://cdn.example/master.m3u8",
    )
    assert audio == []
    plans = hls.select_hls_plans(variants, audio)
    assert plans == []  # nothing muxed, and no audio to merge -> no safe plan


def test_select_orders_muxed_quality_desc() -> None:
    variants, audio = hls.parse_master_playlist(_MUXED_MASTER, "https://cdn.example/master.m3u8")
    plans = hls.select_hls_plans(variants, audio)
    assert [p.height for p in plans] == [720, 360]


def test_select_bounds_number_of_plans() -> None:
    rows = "\n".join(
        f'#EXT-X-STREAM-INF:BANDWIDTH={i * 100000},RESOLUTION={w}x{h},CODECS="avc1.x,mp4a.x"\nv{i}.m3u8'
        for i, (h, w) in enumerate([(2160, 3840), (1440, 2560), (1080, 1920), (720, 1280), (480, 854), (360, 640)])
    )
    variants, _ = hls.parse_master_playlist("#EXTM3U\n" + rows, "https://c.example/master.m3u8")
    plans = hls.select_hls_plans(variants)
    assert len(plans) == hls.MAX_PLANS
    assert plans[0].height == 2160


# ---------------------------------------------------------------------------
# Resolver integration (network fetch monkeypatched)
# ---------------------------------------------------------------------------

def test_resolve_returns_empty_for_media_playlist(monkeypatch) -> None:
    monkeypatch.setattr(hls, "fetch_playlist_text", lambda *a, **k: "#EXTM3U\n#EXTINF:6,\nseg0.ts\n")
    assert hls.resolve_hls_targets("https://cdn.example/stream.m3u8") == []


def test_resolve_builds_muxed_plan(monkeypatch) -> None:
    monkeypatch.setattr(hls, "fetch_playlist_text", lambda *a, **k: _MUXED_MASTER)
    plans = hls.resolve_hls_targets("https://cdn.example/master.m3u8")
    assert plans and plans[0].kind == "muxed"


def test_resolve_drops_unsafe_plan_uris(monkeypatch) -> None:
    monkeypatch.setattr(hls, "fetch_playlist_text", lambda *a, **k: _MUXED_MASTER)
    monkeypatch.setattr(hls, "_is_public", lambda url: False)
    assert hls.resolve_hls_targets("https://cdn.example/master.m3u8") == []


def test_select_audio_uri_prefers_default_in_group() -> None:
    renditions = [
        hls.HlsVariant(uri="https://c/a1.m3u8", audio_group="g", default=False, kind="audio"),
        hls.HlsVariant(uri="https://c/a2.m3u8", audio_group="g", default=True, kind="audio"),
    ]
    assert hls._select_audio_uri("g", renditions) == "https://c/a2.m3u8"


def test_select_audio_uri_falls_back_to_group_then_any() -> None:
    renditions = [
        hls.HlsVariant(uri="https://c/other.m3u8", audio_group="other", default=False, kind="audio"),
        hls.HlsVariant(uri="https://c/main.m3u8", audio_group="main", default=False, kind="audio"),
    ]
    assert hls._select_audio_uri("main", renditions) == "https://c/main.m3u8"
    assert hls._select_audio_uri("", renditions) == "https://c/other.m3u8"
    assert hls._select_audio_uri("g", []) is None
