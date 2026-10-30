"""Versioned, idempotent, auditable schema migration runner.

PostgreSQL is the only backend, so every additive schema change lives in one
ordered registry and runs exactly once. The applied set is persisted in the
``schema_migrations`` table, which makes the Telegram bot, the Gradio web app
and the background workers all safe to boot in ANY order: a migration that
already ran is skipped, a new one runs once, and a partially-applied migration
(crash between its body and the record insert) simply re-runs next boot because
every migration is idempotent.

    * ``register(id, description, fn)`` — append a migration to the registry
      (deduplicated by id, so re-booting in one process never double-runs).
    * ``run_migrations()``           — apply all unapplied migrations in order.

Each migration MUST be additive and idempotent on purpose: ``CREATE TABLE IF
NOT EXISTS``, ``ADD COLUMN IF NOT EXISTS``, or a defensive column check.
Destructive migrations are intentionally unsupported here and must be planned
as an explicit, one-off release step.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Ordered (id, description, callable) registry.
_REGISTRY: list[tuple[str, str, object]] = []
_REGISTERED_IDS: set[str] = set()


def register(migration_id: str, description: str, fn) -> None:
    """Append an idempotent migration to the ordered registry (dedup by id)."""
    if migration_id in _REGISTERED_IDS:
        return
    _REGISTERED_IDS.add(migration_id)
    _REGISTRY.append((migration_id, description, fn))


# Lazily reach into database.pg so this module can be imported (and its logic
# tested) without resolving a DSN or importing psycopg2.
def _execute(sql: str, params: tuple = ()) -> None:
    from .pg import execute

    execute(sql, params)


def _fetchall(sql: str, params: tuple = ()) -> list[dict]:
    from .pg import fetchall

    return fetchall(sql, params)


def _ensure_tracking_table() -> None:
    _execute(
        """CREATE TABLE IF NOT EXISTS schema_migrations (
            id TEXT PRIMARY KEY,
            description TEXT DEFAULT '',
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )"""
    )


def applied_migration_ids() -> set[str]:
    """Ids recorded in ``schema_migrations`` (reads the live database)."""
    rows = _fetchall("SELECT id FROM schema_migrations")
    return {str(r.get("id")) for r in rows}


def run_migrations() -> None:
    """Apply every unapplied migration in order; record each success.

    The record insert happens AFTER the migration body returns, so a migration
    that raises is never marked applied and retries on the next boot.
    """
    _ensure_tracking_table()
    applied = applied_migration_ids()
    for migration_id, description, fn in _REGISTRY:
        if migration_id in applied:
            continue
        fn()
        _execute(
            "INSERT INTO schema_migrations (id, description) VALUES (?, ?) "
            "ON CONFLICT (id) DO NOTHING",
            (migration_id, description),
        )
        logger.info("Applied migration %s — %s", migration_id, description)
