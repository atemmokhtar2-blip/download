"""PostgreSQL-only main database.

EVERYTHING — users, downloads, cache, settings, tickets, audit logs — lives
in PostgreSQL (the DSN database/pg.py resolves: NEON_DATABASE_URL /
POSTGRES_URL / postgres DATABASE_URL). The former SQLite fallback has been
removed completely; without a DSN the database layer now fails loudly with
an actionable error instead of silently degrading.

All SQL below (and in every database/*.py module) is written in the portable
shorthand dialect; _pg_translate() maps it to PostgreSQL on the fly —
datetime modifiers, INSERT OR IGNORE, AUTOINCREMENT DDL, date() helpers and
`?` placeholders are all translated before the statement is executed.
"""

import logging
import re
from contextlib import contextmanager

logger = logging.getLogger(__name__)


_PG_TS_FMT = "YYYY-MM-DD HH24:MI:SS"

_MISSING_DSN_ERROR = (
    "PostgreSQL is required but no DSN is configured. Set NEON_DATABASE_URL "
    "(or POSTGRES_URL / DATABASE_URL) to your PostgreSQL connection string."
)

# Tables whose PK is a serial ``id`` — INSERTs get RETURNING id appended so
# ``cursor.lastrowid`` keeps working.
_ID_RETURN_TABLES = {
    "downloads", "file_cache", "broadcast_log", "admin_audit_log",
    "support_tickets", "support_ticket_messages", "mandatory_channels",
}

_RE_DT_CONCAT_DAYS = re.compile(
    r"datetime\(\s*'now'\s*,\s*'-'\s*\|\|\s*\?\s*\|\|\s*' days'\s*\)", re.I)
_RE_DT_PARAM = re.compile(r"datetime\(\s*'now'\s*,\s*\?\s*\)", re.I)
_RE_DT_LITERAL = re.compile(r"datetime\(\s*'now'\s*,\s*'([^']*)'\s*\)", re.I)
_RE_DT_NOW = re.compile(r"datetime\(\s*'now'\s*\)", re.I)
_RE_DATE_NOW = re.compile(r"(?<![A-Za-z0-9_])date\(\s*'now'\s*\)", re.I)
_RE_DATE_COL = re.compile(r"(?<![A-Za-z0-9_])date\(\s*([A-Za-z_][A-Za-z0-9_.]*)\s*\)", re.I)
_RE_INSERT_OR_IGNORE = re.compile(r"INSERT\s+OR\s+IGNORE\s+INTO", re.I)
_RE_PK_AUTOINC = re.compile(r"INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT", re.I)
_RE_PK_PLAIN = re.compile(r"INTEGER\s+PRIMARY\s+KEY", re.I)
_RE_INSERT_INTO = re.compile(r"^\s*INSERT\s+INTO\s+([A-Za-z_][A-Za-z0-9_]*)", re.I)


def _pg_dsn() -> str:
    """The PostgreSQL DSN in force (resolved once by database/pg.py)."""
    from .pg import PG_DSN
    return PG_DSN


def backend_name() -> str:
    """The one and only backend."""
    return "postgres"


def _pg_now() -> str:
    return f"to_char(now() at time zone 'utc', '{_PG_TS_FMT}')"


def _interval_modifier(literal: str) -> str:
    """Convert a ``datetime('now', '<modifier>')`` literal to a PG interval.

    PostgreSQL casts these inline to ``interval``; a SQLite-only modifier such
    as ``localtime`` / ``utc`` / ``unixepoch`` / ``start of month`` is NOT a
    valid interval and would silently produce a runtime SQL error. Fail loudly
    instead of emitting an interval that Postgres can never parse.
    """
    stripped = literal.strip()
    if not re.match(r"^[+-]?\d", stripped):
        raise ValueError(
            f"_pg_translate cannot turn datetime modifier {literal!r} into a "
            "PostgreSQL interval; use a value like '-7 days' or '1 hour'"
        )
    return f"to_char((now() at time zone 'utc') + interval '{literal}', '{_PG_TS_FMT}')"


def _replace_placeholders(sql: str) -> str:
    """Rewrite ``?`` bind placeholders to PostgreSQL ``%s`` — literal-aware.

    A bare ``sql.replace("?", "%s")`` also rewrites a ``?`` that appears inside
    a ``'string literal'``, ``"quoted identifier"``, ``-- line comment`` or
    ``/* block comment */``, silently corrupting SQL that would otherwise be
    valid and shipping a broken/incorrect statement to PostgreSQL. This walks
    the statement and only rewrites ``?`` tokens in actual SQL code, leaving
    quoted/comment regions untouched.
    """
    out: list[str] = []
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        # Single-quoted string literal ('' is the escaped quote).
        if ch == "'":
            out.append(ch)
            i += 1
            while i < n:
                out.append(sql[i])
                if sql[i] == "'":
                    if i + 1 < n and sql[i + 1] == "'":
                        out.append(sql[i + 1])
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            continue
        # Double-quoted identifier (PostgreSQL folds case by default).
        if ch == '"':
            out.append(ch)
            i += 1
            while i < n:
                out.append(sql[i])
                if sql[i] == '"':
                    i += 1
                    break
                i += 1
            continue
        # Line comment (-- to end of line).
        if ch == "-" and i + 1 < n and sql[i + 1] == "-":
            j = sql.find("\n", i)
            if j == -1:
                j = n
            out.append(sql[i:j])
            i = j
            continue
        # Block comment (/* ... */).
        if ch == "/" and i + 1 < n and sql[i + 1] == "*":
            j = sql.find("*/", i + 2)
            if j == -1:
                j = n
            else:
                j += 2
            out.append(sql[i:j])
            i = j
            continue
        if ch == "?":
            out.append("%s")
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _pg_translate(sql: str) -> str:
    """Translate the portable shorthand dialect used across database/*.py to PG."""
    s = sql
    # datetime('now', '-' || ? || ' days')  ->  make_interval(days => ?)
    s = _RE_DT_CONCAT_DAYS.sub(
        f"to_char((now() at time zone 'utc') - make_interval(days => ?), '{_PG_TS_FMT}')", s)
    # datetime('now', ?)  — param is a modifier string like
    # '-86400 seconds' / '-30 days'; both are valid PG interval literals.
    s = _RE_DT_PARAM.sub(
        f"to_char((now() at time zone 'utc') + (?)::interval, '{_PG_TS_FMT}')", s)
    # datetime('now', '-7 days')  ->  literal interval
    s = _RE_DT_LITERAL.sub(lambda m: _interval_modifier(m.group(1)), s)
    # datetime('now')
    s = _RE_DT_NOW.sub(_pg_now(), s)
    # date('now') / date(col)
    s = _RE_DATE_NOW.sub("CURRENT_DATE", s)
    s = _RE_DATE_COL.sub(r"(\1)::date", s)
    # INSERT OR IGNORE -> INSERT ... ON CONFLICT DO NOTHING
    if _RE_INSERT_OR_IGNORE.search(s):
        s = _RE_INSERT_OR_IGNORE.sub("INSERT INTO", s)
        if "ON CONFLICT" not in s.upper():
            s = s.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
    # DDL types: AUTOINCREMENT -> PG SERIAL; widen INTEGER -> BIGINT
    # (Telegram ids exceed int4).
    s = _RE_PK_AUTOINC.sub("SERIAL PRIMARY KEY", s)
    s = _RE_PK_PLAIN.sub("BIGINT PRIMARY KEY", s)
    s = re.sub(r"\bINTEGER\b", "BIGINT", s)
    # Placeholders last — literal-aware (never rewrites `?` inside a string
    # literal, quoted identifier, or comment).
    s = _replace_placeholders(s)
    _validate_translation(s)
    return s


def _snippet(sql: str, pos: int, width: int = 80) -> str:
    """A short, diagnostic slice of ``sql`` centered on ``pos``."""
    start = max(0, pos - 20)
    end = min(len(sql), pos + width)
    return sql[start:end]


def _validate_translation(translated: str) -> None:
    """Fail loudly when a shorthand construct survived translation.

    ``_pg_translate`` previously relied on regexes that could silently miss a
    variant (unusual whitespace, casing, a nested call) and ship broken SQL to
    PostgreSQL — which then failed deep inside a handler with a confusing,
    unrelated error. Every shorthand construct has a deterministic PostgreSQL
    rewrite, so any survivor is a bug and must raise, never silently degrade.
    """
    checks = (
        (re.compile(r"datetime\s*\(", re.I), "datetime(...)"),
        (re.compile(r"\bINSERT\s+OR\s+IGNORE\b", re.I), "INSERT OR IGNORE"),
        (re.compile(r"\bAUTOINCREMENT\b", re.I), "AUTOINCREMENT"),
        (re.compile(r"\bdate\s*\(\s*'now'\s*\)", re.I), "date('now')"),
    )
    for pattern, label in checks:
        m = pattern.search(translated)
        if m:
            snippet = _snippet(translated, m.start())
            raise ValueError(
                f"_pg_translate failed to translate {label!r}; "
                f"refusing to send broken SQL to PostgreSQL: {snippet!r}"
            )


class _PGCompatCursor:
    """psycopg2 cursor wrapped to behave like the cursor the whole codebase
    expects: ``execute`` returns self (chaining), rows are dicts,
    ``lastrowid`` works, and ``executescript`` is available."""

    def __init__(self, raw):
        self._raw = raw
        self._lastrowid = None

    def execute(self, sql: str, params=()) -> "_PGCompatCursor":
        tsql = _pg_translate(sql)
        self._lastrowid = None
        m = _RE_INSERT_INTO.match(tsql)
        if (m and m.group(1).lower() in _ID_RETURN_TABLES
                and "RETURNING" not in tsql.upper()):
            tsql += " RETURNING id"
            self._raw.execute(tsql, tuple(params))
            row = self._raw.fetchone()
            self._lastrowid = int(row["id"]) if row else None
        else:
            self._raw.execute(tsql, tuple(params))
        return self

    def executemany(self, sql: str, seq) -> "_PGCompatCursor":
        self._raw.executemany(_pg_translate(sql), [tuple(p) for p in seq])
        return self

    def executescript(self, script: str) -> "_PGCompatCursor":
        for stmt in script.split(";"):
            if stmt.strip():
                self._raw.execute(_pg_translate(stmt))
        return self

    def fetchone(self):
        row = self._raw.fetchone()
        return dict(row) if row is not None else None

    def fetchall(self):
        return [dict(r) for r in self._raw.fetchall()]

    def fetchmany(self, size: int):
        return [dict(r) for r in self._raw.fetchmany(size)]

    @property
    def rowcount(self) -> int:
        return self._raw.rowcount

    @property
    def lastrowid(self):
        return self._lastrowid


def table_columns(table: str) -> set:
    """Column names of a table (information_schema)."""
    with db_cursor() as c:
        c.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = ?", (table,),
        )
        return {row["column_name"] for row in c.fetchall()}


@contextmanager
def db_cursor():
    from .pg import pooled_connection

    with pooled_connection(commit=True) as conn:
        import psycopg2.extras

        raw = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            yield _PGCompatCursor(raw)
        finally:
            try:
                raw.close()
            except Exception:
                pass


def init_db():
    logger.info("Main database backend: postgres (persistent)")
    with db_cursor() as c:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id     INTEGER PRIMARY KEY,
                username    TEXT,
                first_name  TEXT,
                last_name   TEXT,
                language    TEXT DEFAULT 'en',
                is_banned   INTEGER DEFAULT 0,
                is_admin    INTEGER DEFAULT 0,
                role        TEXT DEFAULT 'user',
                downloads   INTEGER DEFAULT 0,
                join_date   TEXT DEFAULT (datetime('now')),
                last_seen   TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS downloads (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                url         TEXT NOT NULL,
                title       TEXT,
                platform    TEXT,
                quality     TEXT,
                media_type  TEXT,
                file_size   INTEGER,
                created_at  TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            );

            CREATE TABLE IF NOT EXISTS file_cache (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                url_hash    TEXT NOT NULL,
                quality     TEXT NOT NULL,
                media_type  TEXT NOT NULL,
                file_id     TEXT NOT NULL,
                title       TEXT,
                platform    TEXT,
                created_at  TEXT DEFAULT (datetime('now')),
                hits        INTEGER DEFAULT 0,
                UNIQUE(url_hash, quality, media_type)
            );

            CREATE TABLE IF NOT EXISTS broadcast_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id    INTEGER,
                message     TEXT,
                total       INTEGER,
                success     INTEGER,
                failed      INTEGER,
                created_at  TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS admin_audit_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id    INTEGER,
                action      TEXT,
                target_id   INTEGER,
                detail      TEXT,
                created_at  TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS bot_settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS user_settings (
                user_id                INTEGER PRIMARY KEY,
                video_quality          TEXT    DEFAULT 'auto',
                audio_quality          TEXT    DEFAULT 'normal',
                max_file_size_mb       INTEGER DEFAULT 0,
                delivery_mode          TEXT    DEFAULT 'video',
                auto_cleanup           INTEGER DEFAULT 1,
                history_retention_days INTEGER DEFAULT 30,
                notify_complete        INTEGER DEFAULT 1,
                notify_failure         INTEGER DEFAULT 1,
                notify_progress        INTEGER DEFAULT 1,
                notify_queue           INTEGER DEFAULT 1,
                quiet_mode             INTEGER DEFAULT 0,
                updated_at             TEXT    DEFAULT (datetime('now')),
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            );

            CREATE INDEX IF NOT EXISTS idx_downloads_user    ON downloads(user_id);
            CREATE INDEX IF NOT EXISTS idx_downloads_date    ON downloads(created_at);
            CREATE INDEX IF NOT EXISTS idx_cache_hash        ON file_cache(url_hash);
            CREATE INDEX IF NOT EXISTS idx_admin_audit_date  ON admin_audit_log(created_at);

            CREATE TABLE IF NOT EXISTS support_tickets (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_no   TEXT NOT NULL UNIQUE,
                user_id     INTEGER NOT NULL,
                category    TEXT NOT NULL DEFAULT 'other',
                subject     TEXT NOT NULL DEFAULT '',
                status      TEXT NOT NULL DEFAULT 'open',
                created_at  TEXT DEFAULT (datetime('now')),
                updated_at  TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS support_ticket_messages (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id   INTEGER NOT NULL,
                sender      TEXT NOT NULL DEFAULT 'user',
                body        TEXT NOT NULL,
                created_at  TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (ticket_id) REFERENCES support_tickets(id)
            );

            CREATE INDEX IF NOT EXISTS idx_support_user ON support_tickets(user_id);
            CREATE INDEX IF NOT EXISTS idx_support_msg  ON support_ticket_messages(ticket_id);
        """)
    logger.info("Database initialized on backend=%s", backend_name())
    ensure_cache_schema()
    ensure_owner_schema()
    from .subscription import ensure_subscription_schema
    ensure_subscription_schema()
    from .onboarding import ensure_onboarding_schema
    ensure_onboarding_schema()


def ensure_owner_schema():
    """Additive migrations for the owner panel (safe on existing DBs).

    * ``users.ban_reason`` / ``users.banned_at`` — every ban carries a reason
      and a timestamp (shown in the owner's banned list and audit trail).
    * ``extra_admins`` — admins granted at runtime BY THE OWNER (never owners).
    """
    cols = table_columns("users")
    with db_cursor() as c:
        if "ban_reason" not in cols:
            c.execute("ALTER TABLE users ADD COLUMN ban_reason TEXT DEFAULT ''")
        if "banned_at" not in cols:
            c.execute("ALTER TABLE users ADD COLUMN banned_at TEXT")
        c.execute("""
            CREATE TABLE IF NOT EXISTS extra_admins (
                user_id    INTEGER PRIMARY KEY,
                role       TEXT DEFAULT 'general_supervisor',
                granted_by INTEGER,
                added_at   TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                note       TEXT DEFAULT ''
            )
        """)
        # RBAC migration: extend an OLD extra_admins table (pre-role) safely.
        a_cols = table_columns("extra_admins")
        if "role" not in a_cols:
            c.execute("ALTER TABLE extra_admins ADD COLUMN IF NOT EXISTS role TEXT DEFAULT 'general_supervisor'")
        if "granted_by" not in a_cols:
            c.execute("ALTER TABLE extra_admins ADD COLUMN IF NOT EXISTS granted_by INTEGER")
        if "updated_at" not in a_cols:
            c.execute("ALTER TABLE extra_admins ADD COLUMN IF NOT EXISTS updated_at TEXT")
        if "note" not in a_cols:
            c.execute("ALTER TABLE extra_admins ADD COLUMN IF NOT EXISTS note TEXT DEFAULT ''")


def ensure_cache_schema():
    """Additive migrations for fingerprint + vault columns (safe on existing DBs)."""
    cols = table_columns("file_cache")
    with db_cursor() as c:
        if "fingerprint" not in cols:
            c.execute("ALTER TABLE file_cache ADD COLUMN fingerprint TEXT DEFAULT ''")
        if "vault_chat_id" not in cols:
            c.execute("ALTER TABLE file_cache ADD COLUMN vault_chat_id INTEGER")
        if "vault_message_id" not in cols:
            c.execute("ALTER TABLE file_cache ADD COLUMN vault_message_id INTEGER")
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_cache_fingerprint ON file_cache(fingerprint)"
        )
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_cache_lookup ON file_cache(url_hash, quality, media_type)"
        )
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_cache_created_hits ON file_cache(created_at, hits)"
        )
        c.execute("""
            CREATE TABLE IF NOT EXISTS media_fingerprint_index (
                fingerprint TEXT NOT NULL,
                quality     TEXT NOT NULL,
                media_type  TEXT NOT NULL,
                url_hash    TEXT NOT NULL,
                file_id     TEXT NOT NULL,
                vault_chat_id INTEGER,
                vault_message_id INTEGER,
                updated_at  TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (fingerprint, quality, media_type)
            )
        """)
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_fp_url_hash ON media_fingerprint_index(url_hash)"
        )
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_fp_updated ON media_fingerprint_index(updated_at)"
        )
