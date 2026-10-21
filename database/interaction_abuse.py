"""Persistent anti-spam guard for repeated download-entry actions in the main bot.

Only repeated presses of the main Download menu button or download callbacks are
counted. Normal text, settings, and unrelated callbacks do not accrue strikes.
"""
from __future__ import annotations

import threading
from .db import db_cursor

_init_lock = threading.Lock()
_initialized = False
_WINDOW_SECONDS = 10
_SUPPRESS_AFTER = 3
_BAN_AFTER = 6
_BAN_MINUTES = 5


def _ensure_schema() -> None:
    global _initialized
    if _initialized:
        return
    with _init_lock:
        if _initialized:
            return
        with db_cursor() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS interaction_abuse (
                    user_id BIGINT PRIMARY KEY,
                    event_count INTEGER NOT NULL DEFAULT 0,
                    window_started_at TEXT NOT NULL DEFAULT (datetime('now')),
                    ban_until TEXT,
                    last_event TEXT NOT NULL DEFAULT (datetime('now')),
                    last_reason TEXT NOT NULL DEFAULT ''
                )
            """)
        _initialized = True


def active_ban_seconds(user_id: int) -> int:
    """Return remaining temporary-ban seconds, or zero when not currently banned."""
    _ensure_schema()
    with db_cursor() as c:
        c.execute(
            "SELECT ban_until FROM interaction_abuse WHERE user_id = ?",
            (int(user_id),),
        )
        row = c.fetchone()
        if not row or not row.get("ban_until"):
            return 0
        c.execute(
            "SELECT CASE WHEN CAST(? AS TIMESTAMP) > CURRENT_TIMESTAMP "
            "THEN GREATEST(1, CEIL(EXTRACT(EPOCH FROM (CAST(? AS TIMESTAMP) - CURRENT_TIMESTAMP)))) "
            "ELSE 0 END AS remaining",
            (row["ban_until"], row["ban_until"]),
        )
        return int((c.fetchone() or {}).get("remaining") or 0)


def check_repeated_action(user_id: int, reason: str) -> tuple[str, int]:
    """Return (allow|suppress|banned, seconds_remaining), persisting across restarts."""
    _ensure_schema()
    with db_cursor() as c:
        c.execute(
            "SELECT event_count, window_started_at, ban_until FROM interaction_abuse WHERE user_id = ?",
            (int(user_id),),
        )
        row = c.fetchone()
        if row and row.get("ban_until"):
            c.execute(
                "SELECT CASE WHEN CAST(? AS TIMESTAMP) > CURRENT_TIMESTAMP "
                "THEN GREATEST(1, CEIL(EXTRACT(EPOCH FROM (CAST(? AS TIMESTAMP) - CURRENT_TIMESTAMP)))) "
                "ELSE 0 END AS remaining",
                (row["ban_until"], row["ban_until"]),
            )
            remaining = int((c.fetchone() or {}).get("remaining") or 0)
            if remaining > 0:
                return "banned", remaining

        c.execute("""
            INSERT INTO interaction_abuse
                (user_id, event_count, window_started_at, ban_until, last_event, last_reason)
            VALUES (?, 1, datetime('now'), NULL, datetime('now'), ?)
            ON CONFLICT (user_id) DO UPDATE SET
                event_count = CASE
                    WHEN interaction_abuse.window_started_at <= datetime('now', '-10 seconds')
                    THEN 1 ELSE interaction_abuse.event_count + 1 END,
                window_started_at = CASE
                    WHEN interaction_abuse.window_started_at <= datetime('now', '-10 seconds')
                    THEN datetime('now') ELSE interaction_abuse.window_started_at END,
                last_event = datetime('now'),
                last_reason = EXCLUDED.last_reason
        """, (int(user_id), (reason or "repeat")[:80]))

        c.execute("SELECT event_count FROM interaction_abuse WHERE user_id = ?", (int(user_id),))
        count = int((c.fetchone() or {}).get("event_count") or 1)
        if count >= _BAN_AFTER:
            c.execute("""
                UPDATE interaction_abuse
                SET ban_until = (CURRENT_TIMESTAMP + INTERVAL '5 minutes')::text
                WHERE user_id = ?
            """, (int(user_id),))
            return "banned", _BAN_MINUTES * 60
        if count > _SUPPRESS_AFTER:
            return "suppress", 0
        return "allow", 0
