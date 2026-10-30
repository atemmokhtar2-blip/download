from concurrent.futures import ThreadPoolExecutor

import middlewares.rate_limiter as rate_limiter


def _clear_user(user_id: int) -> None:
    with rate_limiter._lock:
        rate_limiter._recent.pop(user_id, None)
        rate_limiter._downloads_hour.pop(user_id, None)
        rate_limiter._downloads_day.pop(user_id, None)


def test_mark_download_enforces_burst_limit_atomically(monkeypatch) -> None:
    user_id = 918273645
    _clear_user(user_id)
    monkeypatch.setattr(rate_limiter, "RATE_LIMIT_SECONDS", 60)
    monkeypatch.setattr(rate_limiter, "RATE_LIMIT_BURST", 1)
    monkeypatch.setattr(rate_limiter, "HOURLY_DOWNLOAD_LIMIT", 100)
    monkeypatch.setattr(rate_limiter, "DAILY_DOWNLOAD_LIMIT", 100)
    try:
        with ThreadPoolExecutor(max_workers=16) as pool:
            results = list(pool.map(rate_limiter.mark_download, [user_id] * 40))
        assert sum(results) == 1
        result = rate_limiter.check_rate_limit_detailed(user_id)
        assert not result.allowed
        assert result.reason == "burst"
        assert result.wait_seconds > 0
    finally:
        _clear_user(user_id)


def test_expired_burst_window_allows_next_download(monkeypatch) -> None:
    user_id = 918273646
    _clear_user(user_id)
    monkeypatch.setattr(rate_limiter, "RATE_LIMIT_SECONDS", 10)
    monkeypatch.setattr(rate_limiter, "RATE_LIMIT_BURST", 1)
    monkeypatch.setattr(rate_limiter, "HOURLY_DOWNLOAD_LIMIT", 100)
    monkeypatch.setattr(rate_limiter, "DAILY_DOWNLOAD_LIMIT", 100)
    now = [100.0]
    monkeypatch.setattr(rate_limiter.time, "monotonic", lambda: now[0])
    try:
        assert rate_limiter.mark_download(user_id)
        assert not rate_limiter.mark_download(user_id)
        now[0] = 111.0
        assert rate_limiter.mark_download(user_id)
    finally:
        _clear_user(user_id)


# ---------------------------------------------------------------------------
# Distributed (Redis) backend — the Lua scripts are faked so these assert the
# decision logic + bucket priority without a live Redis server. In-memory
# fallback remains implicitly covered by the two tests above (REDIS_URL is
# unset in CI, so _get_redis() returns None and the old path is exercised).
# ---------------------------------------------------------------------------

def _configure_rate_limits(monkeypatch, *, burst, hourly, daily, seconds) -> None:
    monkeypatch.setattr(rate_limiter, "RATE_LIMIT_BURST", burst)
    monkeypatch.setattr(rate_limiter, "HOURLY_DOWNLOAD_LIMIT", hourly)
    monkeypatch.setattr(rate_limiter, "DAILY_DOWNLOAD_LIMIT", daily)
    monkeypatch.setattr(rate_limiter, "RATE_LIMIT_SECONDS", seconds)


def test_check_rate_limit_redis_checks_daily_first_and_short_circuits(monkeypatch) -> None:
    _configure_rate_limits(monkeypatch, burst=5, hourly=30, daily=10, seconds=60)
    keys_seen = []

    class FakeScript:
        def __call__(self, keys=None, args=None):
            keys_seen.append(keys)
            return [10, 100.0]  # daily bucket is full

    monkeypatch.setattr(rate_limiter, "_check_script", FakeScript())
    result = rate_limiter._check_rate_limit_redis(918273649)
    assert result.allowed is False
    assert result.reason == "daily"
    assert len(keys_seen) == 1  # never probes hourly/burst once daily blocks


def test_check_rate_limit_redis_blocks_on_burst(monkeypatch) -> None:
    _configure_rate_limits(monkeypatch, burst=1, hourly=30, daily=10, seconds=60)
    results = iter([[0, 0], [0, 0], [1, 30.0]])

    class FakeScript:
        def __call__(self, keys=None, args=None):
            return next(results)

    monkeypatch.setattr(rate_limiter, "_check_script", FakeScript())
    result = rate_limiter._check_rate_limit_redis(918273650)
    assert result.allowed is False
    assert result.reason == "burst"
    assert result.wait_seconds > 0


def test_check_rate_limit_redis_allows_when_all_buckets_under_limit(monkeypatch) -> None:
    _configure_rate_limits(monkeypatch, burst=5, hourly=30, daily=10, seconds=60)

    class FakeScript:
        def __call__(self, keys=None, args=None):
            return [0, 0]

    monkeypatch.setattr(rate_limiter, "_check_script", FakeScript())
    result = rate_limiter._check_rate_limit_redis(918273651)
    assert result.allowed is True


def test_mark_download_redis_returns_script_decision(monkeypatch) -> None:
    _configure_rate_limits(monkeypatch, burst=2, hourly=30, daily=100, seconds=60)

    class FakeScript:
        def __init__(self):
            self.args = None

        def __call__(self, keys=None, args=None):
            self.args = args
            return 1

    monkeypatch.setattr(rate_limiter, "_mark_script", FakeScript())
    assert rate_limiter._mark_download_redis(918273652) is True


def test_rate_limit_falls_back_to_memory_on_redis_error(monkeypatch) -> None:
    user_id = 918273653
    _clear_user(user_id)
    _configure_rate_limits(monkeypatch, burst=10, hourly=100, daily=100, seconds=60)
    monkeypatch.setattr(rate_limiter, "_get_redis", lambda: object())

    def _boom(*_a, **_k):
        raise RuntimeError("redis down")

    monkeypatch.setattr(rate_limiter, "_check_rate_limit_redis", _boom)
    result = rate_limiter.check_rate_limit_detailed(user_id)
    assert result.allowed is True  # in-memory bucket untouched -> allowed
