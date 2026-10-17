"""Mandatory channel subscription — storage layer.

Global channels live under ``instance_id = 0`` (the main bot). The column
already exists per row so clone-specific channel lists can be layered on
later WITHOUT another migration — the whole feature is forward-compatible
with per-instance overrides.

Design (owner-approved):
  * Flexible channel count (no fixed cap).
  * A global on/off switch stored in ``bot_settings``.
  * Each channel row is independently enabled/disabled.
"""

from __future__ import annotations

from .db import db_cursor

_SUB_ENABLED_KEY = "forced_subscription_enabled"


def ensure_subscription_schema() -> None:
    """Create the subscription tables (safe on existing databases)."""
    with db_cursor() as c:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS mandatory_channels (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                instance_id INTEGER DEFAULT 0,
                channel_ref TEXT NOT NULL,
                title       TEXT DEFAULT '',
                invite_link TEXT DEFAULT '',
                enabled     INTEGER DEFAULT 1,
                added_by    INTEGER,
                added_at    TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_mandatory_channels_instance
                ON mandatory_channels(instance_id, enabled);
        """)


def subscription_enabled() -> bool:
    from .bot_settings import get_setting
    return get_setting(_SUB_ENABLED_KEY, "0") == "1"


def set_subscription_enabled(on: bool) -> None:
    from .bot_settings import set_setting
    set_setting(_SUB_ENABLED_KEY, "1" if on else "0")


def list_channels(instance_id: int = 0, enabled_only: bool = False) -> list[dict]:
    sql = "SELECT * FROM mandatory_channels WHERE instance_id = ?"
    if enabled_only:
        sql += " AND enabled = 1"
    sql += " ORDER BY id ASC"
    with db_cursor() as c:
        c.execute(sql, (int(instance_id or 0),))
        return [dict(row) for row in c.fetchall()]


def get_channel(channel_id: int) -> dict | None:
    with db_cursor() as c:
        c.execute("SELECT * FROM mandatory_channels WHERE id = ?", (int(channel_id),))
        row = c.fetchone()
        return dict(row) if row else None


def add_channel(instance_id: int, channel_ref: str, title: str = "",
                invite_link: str = "", added_by: int | None = None) -> int:
    with db_cursor() as c:
        c.execute("""
            INSERT INTO mandatory_channels
                (instance_id, channel_ref, title, invite_link, added_by)
            VALUES (?, ?, ?, ?, ?)
        """, (int(instance_id or 0), str(channel_ref or "").strip(),
              str(title or "").strip(), str(invite_link or "").strip(), added_by))
        return int(c.lastrowid or 0)


def remove_channel(channel_id: int) -> None:
    with db_cursor() as c:
        c.execute("DELETE FROM mandatory_channels WHERE id = ?", (int(channel_id),))


def set_channel_enabled(channel_id: int, on: bool) -> None:
    with db_cursor() as c:
        c.execute(
            "UPDATE mandatory_channels SET enabled = ? WHERE id = ?",
            (1 if on else 0, int(channel_id)),
        )
