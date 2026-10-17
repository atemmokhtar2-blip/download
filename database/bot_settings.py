"""Owner-controlled runtime settings + dynamic admin registry.

Everything here is stored in the main PostgreSQL database (``bot_settings``
key/value table and ``extra_admins`` table created by ``ensure_owner_schema``).

Security model:
  * ``OWNER_ID`` (env) is IMMUTABLE at runtime — nobody can promote themselves
    to owner through the bot.
  * ``extra_admins`` only ever GRANTS the regular admin role; it can never
    grant ownership. Only the owner panel (owner-gated in code) writes here.
"""

from __future__ import annotations

from .db import db_cursor


# ---- key/value settings ------------------------------------------------------

def get_setting(key: str, default: str = "") -> str:
    with db_cursor() as c:
        c.execute("SELECT value FROM bot_settings WHERE key = ?", (key,))
        row = c.fetchone()
        return str(row["value"]) if row else default


def set_setting(key: str, value: str) -> None:
    with db_cursor() as c:
        c.execute(
            "INSERT INTO bot_settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )


def delete_setting(key: str) -> None:
    with db_cursor() as c:
        c.execute("DELETE FROM bot_settings WHERE key = ?", (key,))


# ---- dynamic admin staff (granted by the owner, revocable, role-based) ---------

def list_extra_admins() -> list[dict]:
    with db_cursor() as c:
        c.execute("""
            SELECT user_id, role, granted_by, added_at, updated_at
            FROM extra_admins ORDER BY added_at DESC
        """)
        return [dict(row) for row in c.fetchall()]


def get_extra_admin(user_id: int) -> dict | None:
    with db_cursor() as c:
        c.execute("SELECT * FROM extra_admins WHERE user_id = ?", (int(user_id),))
        row = c.fetchone()
        return dict(row) if row else None


def add_extra_admin(user_id: int, role: str = "general_supervisor",
                    granted_by: int | None = None) -> None:
    """Grant or update a staff role (upsert). Only the owner panel writes here."""
    with db_cursor() as c:
        c.execute("""
            INSERT INTO extra_admins (user_id, role, granted_by)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                role = excluded.role,
                granted_by = COALESCE(excluded.granted_by, extra_admins.granted_by),
                updated_at = datetime('now')
        """, (int(user_id), role, granted_by))


def remove_extra_admin(user_id: int) -> None:
    with db_cursor() as c:
        c.execute("DELETE FROM extra_admins WHERE user_id = ?", (int(user_id),))


def is_extra_admin(user_id: int | None) -> bool:
    if not user_id:
        return False
    with db_cursor() as c:
        c.execute("SELECT 1 AS x FROM extra_admins WHERE user_id = ?", (int(user_id),))
        return c.fetchone() is not None
