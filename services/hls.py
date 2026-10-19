"""HLS playlist resolution — master (variant) playlist handling.

A ``.m3u8`` URL handed to the downloader is often a **master playlist**: a list
of ``#EXT-X-STREAM-INF`` variants (different resolutions / bandwidths) that
link to separate media playlists. Two things make a raw master playlist
dangerous to feed straight into a single-input downloader:

  1. The highest-bandwidth variant is frequently **video-only** (audio lives in
     a separate ``#EXT-X-MEDIA`` rendition) — so a naive ``ffmpeg -i master.m3u8``
     picks one rendition, commonly that silent top variant, and produces a
     silent MP4 that the central audio guard then rejects.
  2. The first-listed variant is not necessarily the best one for delivery.

This module is the single, well-tested place that turns a master playlist into
an ordered list of concrete *download plans*:

  * ``muxed``  — one rendition that carries BOTH video and audio (single URL);
  * ``split``  — the best video-only rendition + the best matching audio
                rendition, to be merged with ``merge_hls_video_audio``.

It is intentionally PURE for the parsing/selection half (no network, no I/O) so
the logic is unit-testable against fixture playlists. The only network touch is
``resolve_hls_targets``, which fetches the playlist text and then delegates to
the pure functions.

Everything here is *additive*: when the URL is a plain media playlist, when the
fetch fails, or when no sensible plan can be built, ``resolve_hls_targets``
returns ``[]`` and the caller keeps its existing raw-URL behaviour unchanged.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urljoin

# Codec identifiers (lower-cased, matched as prefixes) for classifying a
# rendition as carrying video, audio, or both. These are the codecs that show
# up in HLS ``CODECS`` attributes in the wild.
_VIDEO_CODECS = (
    "avc1", "avc3", "hev1", "hvc1", "vp8", "vp09", "vp9",
    "av01", "av1", "dvh1", "dvhe", "mp2v", "mp4v", "theora",
)
_AUDIO_CODECS = (
    "mp4a", "opus", "ac-3", "ac3", "ec-3", "ec3", "eac3",
    "aac", "alac", "flac", "vorbis", "mp3", "mlp",
)

# Never try more than this many plans — a master playlist can theoretically list
# dozens of renditions and we only want a short, quality-first ladder.
MAX_PLANS = 4


@dataclass
class HlsVariant:
    """A single rendition described by an HLS playlist."""

    uri: str
    bandwidth: int = 0
    width: int = 0
    height: int = 0
    codec_list: list[str] = field(default_factory=list)
    audio_group: str = ""
    has_video: bool = False
    has_audio: bool = False
    kind: str = "variant"  # "variant" | "audio"
    default: bool = False


@dataclass
class HlsPlan:
    """A concrete, ordered download target resolved from a master playlist."""

    kind: str  # "muxed" (single url, has audio) | "split" (video + audio merge)
    video_uri: str | None
    audio_uri: str | None
    height: int = 0
    bandwidth: int = 0


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def _codec_kind(codec: str) -> str | None:
    """Classify one CODECS token as "video", "audio", or None."""
    c = str(codec or "").strip().lower()
    if not c:
        return None
    if c.startswith(_VIDEO_CODECS):
        return "video"
    if c.startswith(_AUDIO_CODECS):
        return "audio"
    return None


def _split_attr(text: str) -> list[str]:
    """Split an HLS attribute list on commas that are NOT inside double quotes.

    ``CODECS="avc1.4d401f,mp4a.40.2"`` must stay a single attribute — a naive
    ``str.split(",")`` would tear it into two.
    """
    parts: list[str] = []
    buf: list[str] = []
    in_quotes = False
    for ch in text:
        if ch == '"':
            in_quotes = not in_quotes
            buf.append(ch)
        elif ch == "," and not in_quotes:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        parts.append("".join(buf))
    return parts


def _parse_attrs(attr_str: str) -> dict[str, str]:
    """Parse ``BANDWIDTH=1280000,CODECS="a,b",RESOLUTION=720x404`` into a dict."""
    attrs: dict[str, str] = {}
    for part in _split_attr(attr_str):
        part = part.strip()
        if not part or "=" not in part:
            continue
        key, _, value = part.partition("=")
        # The first attribute in a tag carries a leading ":" (e.g.
        # "#EXT-X-STREAM-INF:BANDWIDTH=...") which must not end up in the key.
        key = key.strip().upper().lstrip(":")
        value = value.strip()
        if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
            value = value[1:-1]
        attrs[key] = value
    return attrs


def _as_int(value) -> int:
    try:
        return int(float(str(value or "0").strip()))
    except (TypeError, ValueError):
        return 0


def _parse_resolution(resolution: str) -> tuple[int, int]:
    match = re.search(r"(\d{2,5})x(\d{2,5})", resolution or "")
    if match:
        return int(match.group(1)), int(match.group(2))
    return 0, 0


def _parse_codecs(codecs: str) -> list[str]:
    return [c.strip() for c in (codecs or "").split(",") if c.strip()]


def is_master_playlist(text: str) -> bool:
    """True when the playlist is a variant (master) playlist."""
    return bool(text) and "#EXT-X-STREAM-INF" in text


def parse_master_playlist(text: str, base_url: str) -> tuple[list[HlsVariant], list[HlsVariant]]:
    """Parse a master playlist into (variants, audio_renditions).

    Relative URIs are resolved against ``base_url``. A variant's audio/video-ness
    is derived from its ``CODECS`` (both -> muxed) and its ``AUDIO`` group-id
    (present -> video-only, audio is separate).
    """
    variants: list[HlsVariant] = []
    audio_renditions: list[HlsVariant] = []
    pending_attrs: dict[str, str] | None = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#EXT-X-STREAM-INF"):
            pending_attrs = _parse_attrs(line[len("#EXT-X-STREAM-INF"):])
            continue
        if line.startswith("#EXT-X-MEDIA"):
            attrs = _parse_attrs(line[len("#EXT-X-MEDIA"):])
            if (attrs.get("TYPE") or "").upper() == "AUDIO" and attrs.get("URI"):
                audio_renditions.append(HlsVariant(
                    uri=urljoin(base_url, attrs["URI"]),
                    audio_group=attrs.get("GROUP-ID", ""),
                    default=(attrs.get("DEFAULT") or "").upper() == "YES",
                    kind="audio",
                    has_audio=True,
                ))
            continue
        if line.startswith("#"):
            continue
        # First non-comment line after #EXT-X-STREAM-INF is the variant URI.
        if pending_attrs is not None:
            kinds = {_codec_kind(c) for c in _parse_codecs(pending_attrs.get("CODECS", ""))}
            variant = HlsVariant(uri=urljoin(base_url, line))
            variant.bandwidth = _as_int(pending_attrs.get("BANDWIDTH"))
            variant.width, variant.height = _parse_resolution(pending_attrs.get("RESOLUTION", ""))
            variant.codec_list = _parse_codecs(pending_attrs.get("CODECS", ""))
            variant.audio_group = pending_attrs.get("AUDIO", "").strip()
            variant.has_video = "video" in kinds
            variant.has_audio = "audio" in kinds
            variants.append(variant)
            pending_attrs = None

    return variants, audio_renditions


def _select_audio_uri(video_group: str, audio_renditions: list[HlsVariant]) -> str | None:
    """Pick the best audio URI for a video-only rendition.

    Prefer (in order): a rendering in the video's own audio group that is
    DEFAULT, any rendering in that group, then any audio at all.
    """
    if not audio_renditions:
        return None
    group = (video_group or "").strip()
    if group:
        grouped = [a for a in audio_renditions if (a.audio_group or "").strip() == group]
        matched = grouped or audio_renditions
    else:
        matched = audio_renditions
    default = next((a for a in matched if a.default), None)
    return (default or matched[0]).uri


def select_hls_plans(
    variants: list[HlsVariant],
    audio_renditions: list[HlsVariant] | None = None,
) -> list[HlsPlan]:
    """Build an ordered list of download plans from master-playlist variants.

    Muxed renditions (video+audio in one) come first and are quality-sorted.
    When no muxed rendition exists, a single "split" plan (best video-only +
    best matching audio) is appended so ``merge_hls_video_audio`` can assemble a
    properly-muxed file instead of delivering a silent one.
    """
    audio_renditions = list(audio_renditions or [])

    muxed = sorted(
        (v for v in variants if v.has_video and v.has_audio),
        key=lambda v: (v.height, v.bandwidth),
        reverse=True,
    )
    video_only = sorted(
        (v for v in variants if v.has_video and not v.has_audio),
        key=lambda v: (v.height, v.bandwidth),
        reverse=True,
    )

    plans: list[HlsPlan] = []
    for variant in muxed[:MAX_PLANS]:
        plans.append(HlsPlan(
            kind="muxed",
            video_uri=variant.uri,
            audio_uri=None,
            height=variant.height,
            bandwidth=variant.bandwidth,
        ))

    if video_only and len(plans) < MAX_PLANS:
        best_video = video_only[0]
        audio_uri = _select_audio_uri(best_video.audio_group, audio_renditions)
        if audio_uri:
            plans.append(HlsPlan(
                kind="split",
                video_uri=best_video.uri,
                audio_uri=audio_uri,
                height=best_video.height,
                bandwidth=best_video.bandwidth,
            ))

    return plans


# ---------------------------------------------------------------------------
# Network fetch + public resolver (the only I/O in this module)
# ---------------------------------------------------------------------------

def _is_public(url: str | None) -> bool:
    if not url:
        return False
    try:
        from utils.url_safety import is_public_url

        safe, _ = is_public_url(url)
        return bool(safe)
    except Exception:
        return False


def fetch_playlist_text(play_url: str, headers: dict | None = None, timeout: float = 30) -> str | None:
    """Fetch the text of an HLS playlist through the browser-grade TLS layer.

    Returns None on any failure (SSRF-guarded, non-http, network error, or a
    non-playlist response) so callers keep their silent fallback. Always sends a
    real browser UA + Accept so HLS CDNs don't fingerprint a Python client.
    """
    import requests

    from utils import tls

    if not play_url or not _is_public(play_url):
        return None

    effective = tls.browser_headers()
    if headers:
        effective.update(headers)
    effective["Accept"] = (
        "application/vnd.apple.mpegurl,application/x-mpegURL,"
        "application/octet-stream,*/*;q=0.7"
    )

    response = None
    try:
        try:
            response = tls.get(play_url, headers=effective, timeout=timeout)
            if response is None:
                response = requests.get(play_url, headers=effective, timeout=timeout)
        except Exception:
            response = requests.get(play_url, headers=effective, timeout=timeout)
        response.raise_for_status()
        data = getattr(response, "content", None)
        if data is None:
            data = getattr(response, "text", None)
        if isinstance(data, bytes):
            data = data.decode("utf-8", "replace")
        if not data:
            return None
        return str(data)
    except Exception:
        return None
    finally:
        if response is not None:
            try:
                response.close()
            except Exception:
                pass


def resolve_hls_targets(
    play_url: str,
    headers: dict | None = None,
    timeout: float = 30,
) -> list[HlsPlan]:
    """Fetch + parse a playlist and return ordered download plans.

    Returns ``[]`` when the URL is not a master playlist, cannot be fetched, or
    yields no safe plan — the caller then keeps its existing behaviour (native
    yt-dlp first, raw ffmpeg fallback). Every returned plan's URIs pass the SSRF
    guard, so a hostile playlist can never redirect the downloader to an
    internal address.
    """
    text = fetch_playlist_text(play_url, headers=headers, timeout=timeout)
    if not text or not is_master_playlist(text):
        return []

    variants, audio_renditions = parse_master_playlist(text, play_url)
    if not variants:
        return []

    # Audio-only variants are unusual (audio is normally an EXT-X-MEDIA entry)
    # but are cheap to include as merge candidates.
    audio_renditions = audio_renditions + [
        v for v in variants if v.has_audio and not v.has_video
    ]

    plans = select_hls_plans(variants, audio_renditions)
    safe_plans: list[HlsPlan] = []
    for plan in plans:
        if not plan.video_uri or not _is_public(plan.video_uri):
            continue
        if plan.audio_uri and not _is_public(plan.audio_uri):
            continue
        safe_plans.append(plan)
    return safe_plans
