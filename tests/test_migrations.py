"""Logic tests for the versioned migration runner (no live database needed)."""

import pytest

from database import migrations


@pytest.fixture(autouse=True)
def _fresh_registry(monkeypatch):
    """Each test starts from an empty registry so ids never leak across tests."""
    monkeypatch.setattr(migrations, "_REGISTRY", [])
    monkeypatch.setattr(migrations, "_REGISTERED_IDS", set())


class _FakeDB:
    """Minimal in-memory stand-in for the pg.execute/fetchall the runner uses."""

    def __init__(self, applied=()):
        self.applied = set(applied)
        self.executed = []

    def execute(self, sql, params=()):
        self.executed.append((sql, tuple(params)))
        if sql.lstrip().upper().startswith("INSERT INTO SCHEMA_MIGRATIONS"):
            self.applied.add(tuple(params)[0])

    def fetchall(self, sql, params=()):
        assert "schema_migrations" in sql
        return [{"id": mid} for mid in sorted(self.applied)]


def test_applies_unapplied_in_order(monkeypatch):
    db = _FakeDB()
    monkeypatch.setattr(migrations, "_execute", db.execute)
    monkeypatch.setattr(migrations, "_fetchall", db.fetchall)

    order = []
    migrations.register("a", "first", lambda: order.append("a"))
    migrations.register("b", "second", lambda: order.append("b"))

    migrations.run_migrations()

    assert order == ["a", "b"]
    assert db.applied == {"a", "b"}


def test_skips_already_applied(monkeypatch):
    db = _FakeDB(applied={"a"})
    monkeypatch.setattr(migrations, "_execute", db.execute)
    monkeypatch.setattr(migrations, "_fetchall", db.fetchall)

    order = []
    migrations.register("a", "first", lambda: order.append("a"))
    migrations.register("b", "second", lambda: order.append("b"))

    migrations.run_migrations()

    # "a" was already applied → only "b" runs.
    assert order == ["b"]
    assert db.applied == {"a", "b"}


def test_failed_migration_is_not_recorded_and_reruns(monkeypatch):
    db = _FakeDB()
    monkeypatch.setattr(migrations, "_execute", db.execute)
    monkeypatch.setattr(migrations, "_fetchall", db.fetchall)

    calls = []

    def flaky():
        calls.append("attempt")
        if len(calls) == 1:
            raise RuntimeError("boom")

    migrations.register("x", "flaky", flaky)

    with pytest.raises(RuntimeError):
        migrations.run_migrations()

    # The failure was NOT recorded, so the next boot retries and succeeds.
    assert "x" not in db.applied
    migrations.run_migrations()
    assert "x" in db.applied
    assert calls == ["attempt", "attempt"]


def test_register_is_deduplicated(monkeypatch):
    db = _FakeDB()
    monkeypatch.setattr(migrations, "_execute", db.execute)
    monkeypatch.setattr(migrations, "_fetchall", db.fetchall)

    order = []
    migrations.register("dup", "desc", lambda: order.append("dup"))
    migrations.register("dup", "desc again", lambda: order.append("dup-again"))

    migrations.run_migrations()

    assert order == ["dup"]
