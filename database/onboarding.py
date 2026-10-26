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
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_reengagement (
                user_id BIGINT PRIMARY KEY,
                stage INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT DEFAULT (datetime('now'))
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_start_greetings (
                user_id BIGINT PRIMARY KEY,
                last_index INTEGER NOT NULL DEFAULT -1,
                updated_at TEXT DEFAULT (datetime('now'))
            )
        """)


def next_start_greeting_index(user_id: int, total: int) -> int:
    """Return a per-user rotating greeting index, persisted across restarts."""
    total = int(total)
    if total < 1:
        raise ValueError("total must be positive")
    with db_cursor() as cursor:
        cursor.execute("""
            INSERT INTO user_start_greetings (user_id, last_index)
            VALUES (?, 0)
            ON CONFLICT (user_id) DO UPDATE SET
                last_index = MOD(user_start_greetings.last_index + 1, ?),
                updated_at = datetime('now')
            RETURNING last_index
        """, (int(user_id), total))
        row = cursor.fetchone()
    return int(row["last_index"])


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



def record_user_activity(user_id: int) -> None:
    """Update the last-use timestamp and reset inactivity reminders."""
    user_id = int(user_id)
    with db_cursor() as cursor:
        cursor.execute(
            "UPDATE users SET last_seen = datetime('now') WHERE user_id = ?",
            (user_id,),
        )
        if cursor.rowcount:
            cursor.execute("""
                INSERT INTO user_reengagement (user_id, stage)
                VALUES (?, 0)
                ON CONFLICT (user_id) DO UPDATE SET
                    stage = 0,
                    updated_at = datetime('now')
            """, (user_id,))


def get_due_reengagement_users(limit: int = 200) -> list[dict]:
    """Return inactive, non-banned regular users due for their next reminder.

    If the worker was offline for several days, send only the latest eligible
    stage once instead of delivering a burst of old reminders.
    """
    with db_cursor() as cursor:
        cursor.execute("""
            SELECT u.user_id,
                   CASE
                       WHEN u.last_seen <= datetime('now', '-14 days') THEN 3
                       WHEN u.last_seen <= datetime('now', '-7 days') THEN 2
                       WHEN u.last_seen <= datetime('now', '-3 days') THEN 1
                       ELSE 0
                   END AS due_stage,
                   COALESCE(r.stage, 0) AS current_stage
            FROM users u
            LEFT JOIN user_reengagement r ON r.user_id = u.user_id
            WHERE u.is_banned = 0
              AND COALESCE(u.is_admin, 0) = 0
              AND u.last_seen <= datetime('now', '-3 days')
              AND COALESCE(r.stage, 0) < CASE
                       WHEN u.last_seen <= datetime('now', '-14 days') THEN 3
                       WHEN u.last_seen <= datetime('now', '-7 days') THEN 2
                       WHEN u.last_seen <= datetime('now', '-3 days') THEN 1
                       ELSE 0
                   END
            ORDER BY u.last_seen ASC
            LIMIT ?
        """, (max(1, int(limit)),))
        return [dict(row) for row in cursor.fetchall()]


def mark_reengagement_stage(user_id: int, stage: int) -> None:
    """Persist a reminder stage after an attempt, preventing repeat sends."""
    stage = max(0, min(3, int(stage)))
    with db_cursor() as cursor:
        cursor.execute("""
            INSERT INTO user_reengagement (user_id, stage)
            VALUES (?, ?)
            ON CONFLICT (user_id) DO UPDATE SET
                stage = GREATEST(user_reengagement.stage, EXCLUDED.stage),
                updated_at = datetime('now')
        """, (int(user_id), stage))
