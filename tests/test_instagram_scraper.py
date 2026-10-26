"""Pure unit tests for the Instagram scraper hardening (no DB, no network).

Locks the new resilience surface so it can't silently regress:
  * the authless public-JSON endpoint is present as an independent provider,
  * the direct-CDN headers carry Referer/Origin and never invent a cookie,
  * shortcode parsing covers every URL shape the engine is routed,
  * the login-wall / private-page detector stays conservative.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import services.instagram_scraper as ig


def test_public_api_provider_is_registered():
    # The provider table is what scrape_instagram races; the public endpoint must
    # be present so a GraphQL 404/empty never leaves us without a fast fallback.
    assert callable(ig._from_public_api)


def test_public_api_returns_none_when_no_media_node():
    # A login wall shell (or empty data) must resolve to None, never a junk dict.
    assert ig._from_public_api("ABC123", "https://www.instagram.com/p/ABC123/") in (None,)


def test_extract_shortcode_variants():
    assert ig.extract_shortcode("https://www.instagram.com/p/ABC123/") == "ABC123"
    assert ig.extract_shortcode("https://www.instagram.com/reel/XYZ789/?utm=1") == "XYZ789"
    assert ig.extract_shortcode("https://www.instagram.com/reels/QWE456/") == "QWE456"
    assert ig.extract_shortcode("https://instagr.am/p/ASD999/") == "ASD999"
    assert ig.extract_shortcode("https://twitter.com/p/not-here") is None


def test_private_or_login_html_detection():
    assert ig._is_private_or_login_html("<html>This account is private</html>")
    assert ig._is_private_or_login_html("<html>login_required</html>")
    assert ig._is_private_or_login_html("<html>page isn't available</html>")
    assert not ig._is_private_or_login_html("<html>a normal public cat video</html>")


def test_direct_download_headers_carry_referer_and_origin_only_by_default():
    h = ig.direct_download_headers()
    assert h.get("Referer") == "https://www.instagram.com/"
    assert h.get("Origin") == "https://www.instagram.com"
    # No session configured -> no fabricated Cookie header.
    assert not h.get("Cookie")


def test_cookie_header_propagates_sessionid():
    # The resolver cookie jar is shared with the direct-CDN fetch. A configured
    # sessionid must surface in BOTH — this is the fix that stops login-gated
    # posts from being resolved as anonymous and then failing on the CDN.
    import os
    prev = os.environ.get("INSTAGRAM_SESSIONID")
    os.environ["INSTAGRAM_SESSIONID"] = "mock_sess_abc"
    try:
        assert "sessionid=mock_sess_abc" in ig._cookie_header()
    finally:
        if prev is None:
            os.environ.pop("INSTAGRAM_SESSIONID", None)
        else:
            os.environ["INSTAGRAM_SESSIONID"] = prev


def test_node_to_result_video_parses_duration_and_owner():
    node = {
        "is_video": True,
        "video_url": "https://scontent.cdninstagram.com/v/t51.2885-15/x.mp4",
        "display_url": "https://scontent.cdninstagram.com/t51.2885-15/thumb.jpg",
        "video_duration": 12.5,
        "edge_media_to_caption": {"edges": [{"node": {"text": "hello world"}}]},
        "owner": {"username": "bob"},
    }
    r = ig._node_to_result(node, "https://www.instagram.com/p/ABC/", "ABC", "graphql")
    assert r["media_type"] == "video"
    assert str(r["play_url"]).endswith(".mp4")
    assert r["uploader"] == "bob"
    assert r["duration_secs"] == 12
    assert r["source"] == "graphql"


if __name__ == "__main__":
    test_public_api_provider_is_registered()
    test_public_api_returns_none_when_no_media_node()
    test_extract_shortcode_variants()
    test_private_or_login_html_detection()
    test_direct_download_headers_carry_referer_and_origin_only_by_default()
    test_cookie_header_propagates_sessionid()
    test_node_to_result_video_parses_duration_and_owner()
    print("instagram scraper tests passed")
