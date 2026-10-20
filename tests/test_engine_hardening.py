from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.downloader import (
    YOUTUBE_PLAYER_CLIENTS,
    _available_youtube_clients,
    _safe_youtube_clients,
    _youtube_client_profiles,
    _base_opts,
)


def test_youtube_client_set_matches_installed_ytdlp():
    """No stale client names can leak into yt-dlp — the authoritative set is
    read from the INSTALLED build (INNERTUBE_CLIENTS), never hardcoded guesses."""
    available = _available_youtube_clients()
    assert available, "installed yt-dlp must expose non-empty INNERTUBE_CLIENTS"
    # Require stable core surfaces, but allow newer yt-dlp releases to add
    # clients without making the test brittle.
    for client in ("ios", "android", "tv", "web_embedded", "web_music"):
        assert client in available, f"{client} missing from installed yt-dlp clients"
    # Every installed client is represented by the fallback registry so a
    # runtime introspection failure does not silently discard known surfaces.
    assert YOUTUBE_PLAYER_CLIENTS >= available


def test_youtube_safe_order_only_emits_real_clients():
    available = _available_youtube_clients()
    clients = _safe_youtube_clients()
    assert clients, "must emit at least the core ios/android/tv ladder"
    assert all(c in available for c in clients)
    # Never pass names a modern yt-dlp has dropped.
    for dead in ("mediaconnect", "android_music", "ios_music"):
        assert dead not in clients, f"stale client {dead} leaked into player_client"


def test_youtube_profiles_include_music_and_embedded():
    names = {name for name, _ in _youtube_client_profiles()}
    for required in ("music", "embedded"):
        assert required in names, f"profile {required} missing"
    available = _available_youtube_clients()
    for _, clients in _youtube_client_profiles():
        assert clients, f"empty profile: {_}"
        assert all(c in available for c in clients)


def test_base_opts_rotates_tiktok_api_hosts():
    opts = _base_opts()
    tiktok = (opts.get("extractor_args") or {}).get("tiktok") or {}
    hosts = tiktok.get("api_hostname") or []
    assert len(hosts) >= 4, f"expected >=4 TikTok API hosts, got {hosts}"


if __name__ == "__main__":
    test_youtube_client_set_has_music_and_embedded_surfaces()
    test_youtube_profiles_include_music_embedded_mediaconnect()
    test_base_opts_rotates_tiktok_api_hosts()
    print("engine hardening tests passed")
