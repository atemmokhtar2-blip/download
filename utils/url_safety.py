"""Server-side request forgery (SSRF) guard for user-supplied media URLs.

The bot downloads media from arbitrary http(s) URLs. Without a guard, an
attacker could point the server at internal addresses (``127.0.0.1``, cloud
metadata ``169.254.169.254``, internal dashboards, etc.) and make the bot
fetch/reflect data that should never leave the host.

Every entry point must call :func:`is_public_url` before analysis or download.
The function is synchronous (it may resolve DNS) — callers should run it in a
worker thread, e.g. ``asyncio.wait_for(asyncio.to_thread(is_public_url, url), 6)``.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


def _ipv4_legacy_canonical(host: str) -> str | None:
    """Return the canonical dotted-quad for an IPv4 literal in ANY recognised
    inet_aton form — decimal, octal (``0177.0.0.1``), hex (``0x7f.0.0.1``),
    shorthand (``127.1``) and single-integer (``2130706433``). Returns None for
    hostnames and IPv6 literals, which inet_aton rejects.

    ``ipaddress.ip_address`` only accepts the strict dotted-quad form, so
    obfuscated IPv4 literals would otherwise be mistaken for hostnames and leak
    through to the DNS stage. Normalizing here closes that gap independently of
    how the platform resolver treats legacy numeric forms.
    """
    try:
        return socket.inet_ntoa(socket.inet_aton(host))
    except OSError:
        return None


def is_private_ip(host_or_ip: str) -> bool:
    """True when the value is an IP literal inside a non-public range.

    Covers IPv4 + IPv6 loopback, private, link-local (incl. cloud metadata
    ``169.254.169.254``), multicast, unspecified and reserved ranges.
    Returns False for hostnames and public IPs.
    """
    try:
        ip = ipaddress.ip_address(str(host_or_ip).strip().strip("[]"))
    except ValueError:
        return False  # hostname, not an IP literal
    return (
        ip.is_loopback
        or ip.is_link_local
        or ip.is_private
        or ip.is_multicast
        or ip.is_unspecified
        or ip.is_reserved
    )


def resolve_host_ips(host: str) -> list[str]:
    """Return the unique IPs a hostname resolves to (empty when unresolvable)."""
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
        return list({info[4][0] for info in infos})
    except OSError:
        return []


def is_public_url(url: str) -> tuple[bool, str]:
    """Validate that a URL points exclusively at public internet infrastructure.

    Blocks, in order:
      * non http(s) schemes and URLs with embedded ``user:pass@`` credentials
      * IPv4 literals in any form (dotted, octal, hex, shorthand, single-int)
        resolving to a non-public address
      * IPv6 literals in a non-public range
      * hostnames whose DNS resolution yields ANY non-public address
        (covers localhost and also DNS-rebinding style attacks)

    Returns ``(is_safe, reason)``. A hostname that simply does not resolve is
    allowed (yt-dlp will fail it later) — the guard's job is to stop internal
    access, not to replicate the downloader's own error handling.
    """
    try:
        parsed = urlparse(str(url or "").strip())
    except Exception:
        return False, "invalid"

    if parsed.scheme not in {"http", "https"}:
        return False, "scheme"
    if parsed.username or parsed.password:
        return False, "userinfo"

    host = parsed.hostname
    if not host:
        return False, "invalid"

    # IPv4 literal in ANY recognised form (strict, octal, hex, shorthand,
    # single-int). Normalize explicitly and apply the private-range check —
    # never trust getaddrinfo to do the legacy conversion (it varies by OS).
    canonical_v4 = _ipv4_legacy_canonical(host)
    if canonical_v4 is not None:
        return (False, "private-ip") if is_private_ip(canonical_v4) else (True, "ok")

    # IPv6 literal in a non-public range (::1, fd00::1, fe80::1 …).
    if is_private_ip(host):
        return False, "private-ip"

    ips = resolve_host_ips(host)
    if not ips:
        return True, "unresolved"
    for ip in ips:
        if is_private_ip(ip):
            return False, f"private-ip:{ip}"
    return True, "ok"
