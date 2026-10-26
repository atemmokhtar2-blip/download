"""Persistent one-time onboarding state for newly registered users."""

from database.db import db_cursor


def ensure_onboarding_schema() -> None:
    """Create the onboarding table; safe to call repeatedly."""
    with db_cursor() as cursor:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_onboarding (
                user_id BIGINT PRIMARY KEY,
                help_pending INTEGER NOT NULL DEFAULT 0,
                help_shown INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT DEFAULT (datetime('now'))
            )
        """)


def mark_help_pending(user_id: int) -> None:
    """Remember that a new user's first-run guide is due after subscription."""
    with db_cursor() as cursor:
        cursor.execute("""
            INSERT INTO user_onboarding (user_id, help_pending, help_shown)
            VALUES (?, 1, 0)
            ON CONFLICT (user_id) DO NOTHING
        """, (int(user_id),))


def help_is_pending(user_id: int) -> bool:
    with db_cursor() as cursor:
        cursor.execute(
            "SELECT help_pending, help_shown FROM user_onboarding WHERE user_id = ?",
            (int(user_id),),
        )
        row = cursor.fetchone()
    return bool(row and row.get("help_pending") and not row.get("help_shown"))


def mark_help_shown(user_id: int) -> None:
    """Persist that the one-time guide invitation was successfully sent."""
    with db_cursor() as cursor:
        cursor.execute("""
            UPDATE user_onboarding
            SET help_pending = 0, help_shown = 1, updated_at = datetime('now')
            WHERE user_id = ? AND help_pending = 1 AND help_shown = 0
        """, (int(user_id),))
