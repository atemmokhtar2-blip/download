"""Audit the per-platform engine separation.

Guarantees every platform engine is a real, self-describing, independently
routable engine — not a thin alias onto the generic yt-dlp catch-all:

  * the registry stays at exactly 63 bespoke engines (routing stability),
  * every engine declares its OWN distinct technique list (no two engines share
    the same "techniques" signature, so the dashboard can never collapse to one
    generic description),
  * every engine that wires a dedicated native resolver references a real,
    callable function in ``services.light_scrapers``,
  * audio-only platforms (Mixcloud/Bandcamp/Audiomack/Deezer, ...) advertise
    audio-only capabilities so the UI never offers a phantom video button.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import services.light_scrapers as light_scrapers
from services.engines import registered_engines
from services.engines.generic import AudioFallbackEngine, EmbedFallbackEngine


def _is_generic_embed(engine) -> bool:
    """True only when an engine still rides the shared og:video/JSON-LD path."""
    return isinstance(engine, EmbedFallbackEngine) and getattr(
        engine, "resolver_name", ""
    ) == "resolve_embed_media"


def _has_distinct_strategy(engine) -> bool:
    if _is_generic_embed(engine):
        return False
    return True


def test_engine_count_stable() -> None:
    assert len(registered_engines()) == 63


def test_no_duplicate_engine_names() -> None:
    """Registry names are the routing/dashboard identity — a duplicate name is
    a silent mis-routing bug that corrupts ``first_match`` and the admin engine
    list without any loud error. Guard against it explicitly."""
    names = [engine.name for engine in registered_engines()]
    assert len(names) == len(set(names)), (
        f"duplicate engine names in registry: "
        f"{sorted({n for n in names if names.count(n) > 1})}"
    )


def test_no_identical_domains_between_engines() -> None:
    """Two engines sharing the exact same ``domains`` tuple is a copy-paste
    mistake (their resolvers would fight over the same URLs). Identical full
    tuples are forbidden; intentionally OVERLAPPING subsets remain fine."""
    by_domains: dict[tuple, list] = {}
    for engine in registered_engines():
        by_domains.setdefault(tuple(engine.domains), []).append(engine.name)
    collisions = {d: ns for d, ns in by_domains.items() if len(ns) > 1}
    assert not collisions, f"engines with identical domains tuples: {collisions}"


def test_every_engine_declares_distinct_techniques() -> None:
    seen: dict[tuple, str] = {}
    for engine in registered_engines():
        assert engine.techniques, f"{engine.name} has no techniques declared"
        signature = tuple(engine.techniques)
        assert signature not in seen, (
            f"{engine.name} shares an identical technique list with {seen[signature]} "
            f"({signature!r})"
        )
        seen[signature] = engine.name


def test_named_resolvers_exist() -> None:
    for engine in registered_engines():
        resolver = getattr(engine, "resolver_name", None)
        if not resolver or resolver == "resolve_embed_media":
            continue
        assert callable(getattr(light_scrapers, resolver, None)), (
            f"{engine.name} references missing resolver {resolver}"
        )


def test_audio_platforms_are_audio_only() -> None:
    audio_engines = [e for e in registered_engines() if isinstance(e, AudioFallbackEngine)]
    assert audio_engines, "expected at least one AudioFallbackEngine"
    for engine in audio_engines:
        assert "audio" in engine.capabilities, engine.name
        assert "video" not in engine.capabilities, engine.name


def test_bespoke_engines_outnumber_generic() -> None:
    """Most engines must carry a distinct strategy (native resolver / dedicated
    audio extractor / full scraper engine), not ride the generic embed path."""
    distinct = [e for e in registered_engines() if _has_distinct_strategy(e)]
    generic = [e for e in registered_engines() if _is_generic_embed(e)]
    assert len(distinct) > len(generic), (
        f"only {len(distinct)} distinct vs {len(generic)} generic engines"
    )


if __name__ == "__main__":
    test_engine_count_stable()
    test_no_duplicate_engine_names()
    test_no_identical_domains_between_engines()
    test_every_engine_declares_distinct_techniques()
    test_named_resolvers_exist()
    test_audio_platforms_are_audio_only()
    test_bespoke_engines_outnumber_generic()
    print("engine technique audit passed")
