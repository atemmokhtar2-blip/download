"""Regression coverage for the owner panel's inline-button routing.

This statically compares literal callback_data values in the panel's keyboards
against the owner_callback dispatcher. It catches visible buttons that have no
matching route, without making live Telegram API calls.
"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
PANEL = ROOT / "handlers" / "owner_panel.py"


def test_every_literal_owner_button_has_a_dispatch_route():
    source = PANEL.read_text(encoding="utf-8")
    callback_values = set(
        re.findall(r'callback_data="(own_[^"]+)"', source)
    )
    assert callback_values, "expected to find owner-panel callback buttons"

    dispatch = source[source.index("async def owner_callback"):]
    exact = set(re.findall(r'data == "([^"]+)"', dispatch))
    prefixes = set(re.findall(r'data\.startswith\("([^"]+)"\)', dispatch))

    missing = sorted(
        value for value in callback_values
        if value not in exact and not any(value.startswith(prefix) for prefix in prefixes)
    )
    assert not missing, "owner-panel buttons without dispatcher routes: " + ", ".join(missing)


def test_owner_panel_does_not_answer_callbacks_with_raw_query_answer():
    source = PANEL.read_text(encoding="utf-8")
    dispatch = source[source.index("async def owner_callback"):]
    assert "await query.answer(" not in dispatch, (
        "owner callback routes must use safe_answer() to handle expired Telegram callback IDs"
    )
    assert "await safe_answer(query," in dispatch
