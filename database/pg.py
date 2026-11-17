"""PostgreSQL persistence for the referral & clone-instance network.

PostgreSQL is the ONLY backend (the former SQLite fallback has been removed
completely). The DSN is resolved ONCE at import time from
``NEON_DATABASE_URL`` (preferred), ``POSTGRES_URL``, or a postgres
``DATABASE_URL``. Without a DSN every operation fails loudly with an
actionable error instead of silently degrading — one database, no split
brain between environments.

SQL in this module is written with ``?`` placeholders; they are translated
to ``%s`` before execution. Rows always come back as plain dicts. Ids that
need to be returned use ``insert_returning_id`` (PostgreSQL ``RETURNING``).

A thread-safe ``psycopg2`` connection pool reuses connections across calls
(see ``pooled_connection``) instead of paying Neon/serverless-Postgres auth +
TLS + backend-fork overhead on every query. Pool sizing is tunable via
``PG_MIN_CONN`` / ``PG_MAX_CONN``.
"""

from __future__ import annotations

import logging
import os
import threading
from contextlib import contextmanager

logger = logging.getLogger(__name__)

_local = threading.local()

MISSING_DSN_ERROR = (
    "PostgreSQL is the only database backend, but no DSN is configured. "
    "Set NEON_DATABASE_URL (or POSTGRES_URL / DATABASE_URL) to your "
    "PostgreSQL connection string."
)


def _resolve_pg_dsn() -> str:
    for name in ("NEON_DATABASE_URL", "POSTGRES_URL", "DATABASE_URL"):
        dsn = os.getenv(name, "").strip()
        if dsn.startswith("postgres"):
            return dsn
    return ""


PG_DSN = _resolve_pg_dsn()
BACKEND = "postgres"


def backend_name() -> str:
    return BACKEND


def _translate(sql: str) -> str:
    """Convert portable `?` placeholders to psycopg2's `%s`."""
    return sql.replace("?", "%s")


class _Cursor:
    """Wraps a psycopg2 cursor into the dict-row interface callers expect."""

    def __init__(self, raw):
        self._raw = raw

    def execute(self, sql: str, params: tuple = ()) -> "_Cursor":
        self._raw.execute(_translate(sql), tuple(params))
        return self

    def fetchone(self) -> dict | None:
        row = self._raw.fetchone()
        if row is None:
            return None
        return dict(row)

    def fetchall(self) -> list[dict]:
        return [dict(r) for r in self._raw.fetchall()]

    @property
    def rowcount(self) -> int:
        return self._raw.rowcount


# ---------------------------------------------------------------------------
# Connection pooling ---------------------------------------------------------
# Connections used to be opened and CLOSED for every single statement, which
# is the #1 bottleneck on Neon / serverless Postgres (auth + TLS + backend
# fork overhead on each query). A thread-safe psycopg2 ThreadedConnectionPool
# is created lazily and reused by every call. It is keyed by DSN because the
# test harness mutates ``PG_DSN`` per isolated database — a stale pool pointing
# at a dropped database must never be reused.
# ---------------------------------------------------------------------------

PG_MIN_CONN = max(1, int(os.getenv("PG_MIN_CONN", "1")))
PG_MAX_CONN = max(PG_MIN_CONN, int(os.getenv("PG_MAX_CONN", "10")))

_pool = None
_pool_dsn: str = ""
_pool_lock = threading.Lock()


def _get_pool():
    global _pool, _pool_dsn
    if not PG_DSN:
        raise RuntimeError(MISSING_DSN_ERROR)
    with _pool_lock:
        if _pool is None or _pool_dsn != PG_DSN:
            if _pool is not None:
                try:
                    _pool.closeall()
                except Exception:
                    pass
            from psycopg2 import pool as _psycopg2_pool

            _pool = _psycopg2_pool.ThreadedConnectionPool(
                PG_MIN_CONN, PG_MAX_CONN, PG_DSN, connect_timeout=15
            )
            _pool_dsn = PG_DSN
        return _pool


def _return_conn(pool, conn) -> None:
    try:
        pool.putconn(conn)
    except Exception:
        try:
            conn.close()
        except Exception:
            pass


@contextmanager
def pooled_connection(commit: bool = True):
    """Yield a pooled psycopg2 connection; commit on success, rollback on error.

    Read-only callers (``commit=False``) end their transaction with a rollback
    so a snapshot / lock is never leaked back into the pool. Rollback after a
    successful commit is a no-op, so this is always safe.
    """
    pool = _get_pool()
    conn = pool.getconn()
    try:
        yield conn
        if commit:
            conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        try:
            conn.rollback()
        except Exception:
            pass
        _return_conn(pool, conn)


@contextmanager
def ref_cursor(commit: bool = True):
    """Yield a dict-row cursor backed by a pooled connection."""
    with pooled_connection(commit=commit) as conn:
        import psycopg2.extras

        raw = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            yield _Cursor(raw)
        finally:
            try:
                raw.close()
            except Exception:
                pass


def execute(sql: str, params: tuple = ()) -> int:
    """Run a statement; returns affected rowcount."""
    with ref_cursor() as c:
        c.execute(sql, params)
        return c.rowcount


def fetchone(sql: str, params: tuple = ()) -> dict | None:
    with ref_cursor(commit=False) as c:
        c.execute(sql, params)
        return c.fetchone()


def fetchall(sql: str, params: tuple = ()) -> list[dict]:
    with ref_cursor(commit=False) as c:
        c.execute(sql, params)
        return c.fetchall()


def insert_returning_id(sql: str, params: tuple = ()) -> int | None:
    """INSERT and return the new row id (PostgreSQL ``RETURNING``).

    ``sql`` must NOT include a RETURNING clause; it is appended here.
    """
    with ref_cursor() as c:
        c.execute(sql + " RETURNING id", params)
        row = c.fetchone()
        return int(row["id"]) if row else None


# ---- schema -----------------------------------------------------------------

def _pk_col() -> str:
    return "SERIAL PRIMARY KEY"


def _bigpk() -> str:
    return "BIGINT PRIMARY KEY"


def _bigint() -> str:
    return "BIGINT"


def init_referral_schema() -> None:
    """Create referral/instance tables (idempotent). Safe to call on boot."""
    pk = _pk_col()
    bpk = _bigpk()
    bi = _bigint()
    ddl = [
        # Mirror of Telegram users known to the referral network.
        f"""CREATE TABLE IF NOT EXISTS ref_users (
            telegram_id {bpk},
            username TEXT DEFAULT '',
            first_name TEXT DEFAULT '',
            status TEXT DEFAULT 'active',
            source TEXT DEFAULT 'main',
            registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_active_at TIMESTAMP,
            activity_days TEXT DEFAULT ''
        )""",
        # Per-instance membership is separate from the global referral-user
        # mirror. A Telegram user may use multiple clones without their
        # membership being overwritten or leaked between instances.
        f"""CREATE TABLE IF NOT EXISTS clone_users (
            instance_id {bi} NOT NULL,
            telegram_id {bi} NOT NULL,
            username TEXT DEFAULT '',
            first_name TEXT DEFAULT '',
            registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_active_at TIMESTAMP,
            PRIMARY KEY (instance_id, telegram_id)
        )""",
        # One permanent referral code per owner.
        f"""CREATE TABLE IF NOT EXISTS referral_codes (
            owner_id {bi} NOT NULL UNIQUE,
            code TEXT NOT NULL UNIQUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        # referrer -> referred bindings. referred_id is UNIQUE: every account
        # can only ever be attributed to ONE referrer, once.
        f"""CREATE TABLE IF NOT EXISTS referrals (
            id {pk},
            referrer_id {bi} NOT NULL,
            referred_id {bi} NOT NULL UNIQUE,
            code TEXT DEFAULT '',
            status TEXT DEFAULT 'pending',
            bound_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            qualified_at TIMESTAMP
        )""",
        f"""CREATE TABLE IF NOT EXISTS referral_rewards (
            user_id {bpk},
            qualified_count INTEGER DEFAULT 0,
            status TEXT DEFAULT 'progress',
            notified_milestone INTEGER DEFAULT 0,
            claimed_at TIMESTAMP,
            instance_id {bi}
        )""",
        f"""CREATE TABLE IF NOT EXISTS bot_instances (
            id {pk},
            owner_id {bi} NOT NULL,
            bot_id {bi} UNIQUE,
            bot_username TEXT DEFAULT '',
            status TEXT DEFAULT 'pending_token',
            plan TEXT DEFAULT 'basic',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            activated_at TIMESTAMP,
            expires_at TIMESTAMP,
            last_error TEXT DEFAULT ''
        )""",
        # Encrypted bot tokens. token_hash (sha256) enforces "never linked
        # twice" without ever storing or comparing plaintext.
        f"""CREATE TABLE IF NOT EXISTS bot_secrets (
            instance_id {bi} PRIMARY KEY,
            token_encrypted TEXT NOT NULL,
            token_hash TEXT NOT NULL UNIQUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_used_at TIMESTAMP
        )""",
        # Per-instance clone configuration. NOTE: deliberately named
        # ``clone_settings`` (NOT ``bot_settings``): the main database has
        # its OWN global key/value ``bot_settings`` table (database/db.py) —
        # same name meant whichever DDL ran first broke the other's
        # ON CONFLICT clauses.
        f"""CREATE TABLE IF NOT EXISTS clone_settings (
            instance_id {bi} NOT NULL,
            key TEXT NOT NULL,
            value TEXT DEFAULT '',
            PRIMARY KEY (instance_id, key)
        )""",
        f"""CREATE TABLE IF NOT EXISTS activation_log (
            id {pk},
            owner_id {bi},
            instance_id {bi},
            action TEXT DEFAULT '',
            details TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        # Owner-issued redemption codes ("أكواد التفعيل"). One row per code;
        # uses_consumed counts RESERVED+CONFIRMED activations and is guarded
        # atomically (UPDATE ... WHERE uses_consumed < max_uses) so a code can
        # never be over-used, even by simultaneous redemptions.
        f"""CREATE TABLE IF NOT EXISTS redemption_codes (
            id {pk},
            code TEXT NOT NULL UNIQUE,
            plan TEXT DEFAULT 'basic',
            runtime_days INTEGER DEFAULT 30,
            max_uses INTEGER DEFAULT 1,
            uses_consumed INTEGER DEFAULT 0,
            valid_days INTEGER DEFAULT 7,
            valid_hours INTEGER,
            status TEXT DEFAULT 'active',
            note TEXT DEFAULT '',
            created_by {bi},
            targeted_user_id {bi},
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP
        )""",
        # 24-hour free trial registry — ONE trial per Telegram user, ever.
        # The row is inserted atomically (ON CONFLICT DO NOTHING) when the
        # trial starts, so double-taps can never grant two trials.
        f"""CREATE TABLE IF NOT EXISTS clone_trials (
            user_id {bpk},
            instance_id {bi},
            plan TEXT DEFAULT 'basic',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        # Append-only audit trail for codes: created / reserved / used /
        # released / revoked / exhausted. Full code text and bot tokens are
        # NEVER written here — only ids, prefixes and safe metadata.
        f"""CREATE TABLE IF NOT EXISTS redemption_log (
            id {pk},
            code_id {bi} NOT NULL,
            action TEXT DEFAULT '',
            user_id {bi},
            instance_id {bi},
            details TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
    ]
    indexes = [
        "CREATE INDEX IF NOT EXISTS idx_referrals_referrer ON referrals (referrer_id, status)",
        "CREATE INDEX IF NOT EXISTS idx_clone_users_instance_registered ON clone_users (instance_id, registered_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_instances_owner ON bot_instances (owner_id)",
        "CREATE INDEX IF NOT EXISTS idx_instances_status ON bot_instances (status)",
        "CREATE INDEX IF NOT EXISTS idx_redemption_log_code ON redemption_log (code_id, action)",
    ]
    with ref_cursor() as c:
        for stmt in ddl:
            c.execute(stmt)
        for stmt in indexes:
            try:
                c.execute(stmt)
            except Exception:
                logger.debug("Index skipped: %s", stmt[:60])
    # Additive schema changes go through the versioned, tracked migration
    # runner so they run exactly once, in order, and are recorded in
    # schema_migrations for auditability across restarts.
    from .migrations import register, run_migrations

    register("reward_milestone", "referral_rewards.notified_milestone", ensure_reward_milestone_column)
    register("instance_plan_columns", "bot_instances.plan + expires_at", ensure_instance_plan_columns)
    register("instance_runtime_status", "bot_instances.runtime_status", ensure_instance_runtime_status_column)
    register("redemption_targeted_user", "redemption_codes.targeted_user_id", ensure_redemption_targeted_user_column)
    register("redemption_valid_hours", "redemption_codes.valid_hours", ensure_redemption_valid_hours_column)
    register("clone_settings_migration", "legacy bot_settings -> clone_settings", ensure_clone_settings_migration)
    register("plan_schema", "clone plan tables + downloads.instance_id", ensure_plan_schema)
    register("clone_users_migration", "backfill clone_users memberships", ensure_clone_users_migration)
    run_migrations()
    logger.info("Referral schema ready on backend=%s", BACKEND)


def ensure_plan_schema() -> None:
    """Plan-enforcement tables + ``downloads.instance_id`` (idempotent).

    Basic vs Pro is enforced server-side per clone instance (see
    services/plan_policy.py), so:
      * every download must be attributable to the instance that served it
        (``downloads.instance_id`` — 0 = the main bot);
      * each clone needs its OWN admins / bans / audit log / broadcast
        history — scoped by ``instance_id``, never the global ADMIN_IDS.
    ``CREATE TABLE IF NOT EXISTS`` never evolves an existing table, hence
    the additive ALTER for the downloads column.
    """
    bi = _bigint()
    pk = _pk_col()
    try:
        execute("ALTER TABLE downloads ADD COLUMN IF NOT EXISTS instance_id BIGINT DEFAULT 0")
        execute("CREATE INDEX IF NOT EXISTS idx_downloads_instance_user "
                "ON downloads(instance_id, user_id)")
        execute(f"""CREATE TABLE IF NOT EXISTS clone_admins (
            instance_id {bi} NOT NULL,
            user_id {bi} NOT NULL,
            can_ban BOOLEAN DEFAULT FALSE,
            can_broadcast BOOLEAN DEFAULT FALSE,
            can_stats BOOLEAN DEFAULT TRUE,
            added_by {bi},
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (instance_id, user_id)
        )""")
        execute(f"""CREATE TABLE IF NOT EXISTS clone_bans (
            instance_id {bi} NOT NULL,
            user_id {bi} NOT NULL,
            banned_by {bi},
            reason TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (instance_id, user_id)
        )""")
        execute(f"""CREATE TABLE IF NOT EXISTS clone_admin_log (
            id {pk},
            instance_id {bi} NOT NULL,
            actor_id {bi} NOT NULL,
            action TEXT NOT NULL DEFAULT '',
            detail TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")
        execute("CREATE INDEX IF NOT EXISTS idx_clone_admin_log "
                "ON clone_admin_log(instance_id, id)")
        execute(f"""CREATE TABLE IF NOT EXISTS clone_broadcasts (
            id {pk},
            instance_id {bi} NOT NULL,
            actor_id {bi} NOT NULL,
            recipients INTEGER DEFAULT 0,
            sent INTEGER DEFAULT 0,
            failed INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")
        execute(f"""CREATE TABLE IF NOT EXISTS download_failures (
            id {pk},
            instance_id {bi} DEFAULT 0,
            user_id {bi} NOT NULL,
            platform TEXT DEFAULT '',
            reason TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")
        execute("CREATE INDEX IF NOT EXISTS idx_dl_failures_inst "
                "ON download_failures(instance_id, created_at)")
    except Exception:
        logger.exception("plan schema init failed")


def ensure_clone_settings_migration() -> None:
    """Migrate legacy per-instance settings out of the colliding ``bot_settings``.

    Databases created before the rename may hold a ``bot_settings`` table in
    the REFERRAL shape (instance_id, key, value), which hijacks the main
    database's global key/value ``bot_settings`` and breaks
    ``ON CONFLICT(key)`` upserts. This copies any legacy rows into
    ``clone_settings`` and drops the mis-shaped table so the main schema can
    recreate its own. Idempotent.
    """
    try:
        row = fetchone("SELECT to_regclass('public.bot_settings') AS t")
        if row and row.get("t"):
            cols = fetchall(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'bot_settings'"
            )
            names = {c["column_name"] for c in cols}
            if "instance_id" in names:
                execute(
                    "INSERT INTO clone_settings (instance_id, key, value) "
                    "SELECT instance_id, key, value FROM bot_settings "
                    "ON CONFLICT (instance_id, key) DO NOTHING"
                )
                execute("DROP TABLE bot_settings")
                logger.info("Migrated legacy referral bot_settings -> clone_settings")
    except Exception:
        logger.exception("clone_settings migration failed")


def ensure_instance_plan_columns() -> None:
    """Additive migration: clone plan (basic/pro) + validity-window columns.

    New installs get them from the CREATE TABLE DDL above; this upgrades
    databases created before the plan system existed.
    """
    try:
        execute("ALTER TABLE bot_instances ADD COLUMN IF NOT EXISTS plan TEXT DEFAULT 'basic'")
        execute("ALTER TABLE bot_instances ADD COLUMN IF NOT EXISTS expires_at TIMESTAMP")
    except Exception:
        logger.exception("instance plan columns migration failed")


def ensure_instance_runtime_status_column() -> None:
    """Track whether a persisted clone is pending, running, stopped, or failed."""
    try:
        execute("ALTER TABLE bot_instances ADD COLUMN IF NOT EXISTS runtime_status TEXT DEFAULT 'pending'")
    except Exception:
        logger.exception("instance runtime-status migration failed")


def ensure_reward_milestone_column() -> None:
    """Additive migration for the ``notified_milestone`` reward column.

    New installs already get it from the CREATE TABLE DDL above; this
    upgrades databases created before milestone notifications existed.
    """
    try:
        execute(
            "ALTER TABLE referral_rewards "
            "ADD COLUMN IF NOT EXISTS notified_milestone INTEGER DEFAULT 0"
        )
    except Exception:
        logger.exception("referral milestone column migration failed")


def ensure_redemption_targeted_user_column() -> None:
    """Add the optional user restriction to redemption codes on older DBs.

    ``CREATE TABLE IF NOT EXISTS`` does not evolve a table already created by
    an earlier release, so targeted-code deployments need this additive
    migration before any redemption/list query selects the column.
    """
    try:
        execute(
            "ALTER TABLE redemption_codes "
            "ADD COLUMN IF NOT EXISTS targeted_user_id BIGINT"
        )
    except Exception:
        logger.exception("redemption targeted-user column migration failed")


def ensure_redemption_valid_hours_column() -> None:
    """Add hour-granularity pre-use validity to existing redemption codes.

    Lets an owner issue a code valid for a few HOURS (e.g. "ينتهي بعد ساعة")
    instead of whole days. ``expires_at`` remains the precise enforcement
    store; ``valid_hours`` only keeps the chosen unit for display + audits.
    """
    try:
        execute(
            "ALTER TABLE redemption_codes "
            "ADD COLUMN IF NOT EXISTS valid_hours INTEGER"
        )
    except Exception:
        logger.exception("redemption valid-hours column migration failed")


def ensure_clone_users_migration() -> None:
    """Backfill per-instance memberships from legacy clone source markers."""
    try:
        execute(
            """
            INSERT INTO clone_users
                (instance_id, telegram_id, username, first_name, registered_at, last_active_at)
            SELECT bi.id, ru.telegram_id, ru.username, ru.first_name,
                   COALESCE(ru.registered_at, CURRENT_TIMESTAMP),
                   COALESCE(ru.last_active_at, ru.registered_at, CURRENT_TIMESTAMP)
            FROM ref_users ru
            JOIN bot_instances bi
              ON ru.source = 'clone:' || CAST(bi.bot_id AS TEXT)
            WHERE bi.bot_id IS NOT NULL
            ON CONFLICT (instance_id, telegram_id) DO NOTHING
            """
        )
        # Recover members whose previous source stayed "main" but whose
        # download history proves they used a specific clone instance.
        execute(
            """
            INSERT INTO clone_users
                (instance_id, telegram_id, username, first_name, registered_at, last_active_at)
            SELECT d.instance_id, ru.telegram_id, ru.username, ru.first_name,
                   MIN(d.created_at), MAX(d.created_at)
            FROM downloads d
            JOIN ref_users ru ON ru.telegram_id = d.user_id
            JOIN bot_instances bi ON bi.id = d.instance_id
            WHERE d.instance_id > 0
            GROUP BY d.instance_id, ru.telegram_id, ru.username, ru.first_name
            ON CONFLICT (instance_id, telegram_id) DO NOTHING
            """
        )
    except Exception:
        logger.exception("clone users legacy membership migration failed")
