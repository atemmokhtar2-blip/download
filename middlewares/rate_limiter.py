from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass
from collections import defaultdict, deque

from config.settings import (
    RATE_LIMIT_SECONDS,
    RATE_LIMIT_BURST,
    HOURLY_DOWNLOAD_LIMIT,
    DAILY_DOWNLOAD_LIMIT,
    REDIS_URL,
)

logger = logging.getLogger(__name__)

_recent: dict[int, deque[float]] = defaultdict(deque)
_downloads_hour: dict[int, deque[float]] = defaultdict(deque)
_downloads_day: dict[int, deque[float]] = defaultdict(deque)
# Check and record requests atomically: Telegram updates may be handled concurrently.
_lock = threading.RLock()

# Per-user deques only trim on that user's NEXT access; a one-time user would
# otherwise linger in memory forever. Once the tracker grows past this cap,
# entries whose windows have fully expired are evicted — dropping them is a
# no-op for enforcement (they can never be "counted" again outside their
# window) but keeps the three dicts bounded on a long-running bot.
_MAX_TRACKED_USERS = 100_000


def _evict_stale_entries(now: float | None = None) -> int:
    """Drop idle, fully-expired user entries when the dicts are oversized.

    MUST be called with ``_lock`` held. Returns the number of user ids evicted
    from the burst window (the other two buckets are swept alongside it).
    """
    if len(_recent) <= _MAX_TRACKED_USERS:
        return 0
    current = time.monotonic() if now is None else now
    stale: set[int] = set()
    for uid, q in _recent.items():
        if not q or current - q[-1] >= 86400:
            stale.add(uid)
    for uid in stale:
        _recent.pop(uid, None)
        _downloads_hour.pop(uid, None)
        _downloads_day.pop(uid, None)
    return len(stale)


@dataclass
class RateLimitResult:
    allowed: bool
    reason: str = ""
    wait_seconds: int = 0


def _trim(q: deque[float], window: int, now: float | None = None) -> None:
    current = time.monotonic() if now is None else now
    while q and current - q[0] >= window:
        q.popleft()


def _check_rate_limit_locked(user_id: int, now: float) -> RateLimitResult:
    _evict_stale_entries(now)
    recent = _recent[user_id]
    hour = _downloads_hour[user_id]
    day = _downloads_day[user_id]
    _trim(recent, RATE_LIMIT_SECONDS, now)
    _trim(hour, 3600, now)
    _trim(day, 86400, now)

    if len(day) >= DAILY_DOWNLOAD_LIMIT:
        return RateLimitResult(False, "daily", max(1, int(86400 - (now - day[0]))))
    if len(hour) >= HOURLY_DOWNLOAD_LIMIT:
        return RateLimitResult(False, "hourly", max(1, int(3600 - (now - hour[0]))))
    if len(recent) >= RATE_LIMIT_BURST:
        return RateLimitResult(False, "burst", max(1, int(RATE_LIMIT_SECONDS - (now - recent[0]))))
    return RateLimitResult(True)


# ---------------------------------------------------------------------------
# Distributed backend (Redis)
# ---------------------------------------------------------------------------
# The in-memory deques above only enforce limits PER PROCESS — the instant the
# bot scales to multiple workers, "burst / hourly / daily" quotas collapse
# because each worker keeps its own counters. When REDIS_URL is configured (and
# reachable) the limiter instead uses a sliding window implemented as a Redis
# sorted set per (user, bucket), and a Lua script runs trim → count → record
# atomically so no two workers can ever both pass a full window. On any Redis
# error we fall back to the in-memory path (availability over strict
# distribution for the transient outage) rather than throwing and breaking a
# download.
# ---------------------------------------------------------------------------

_RL_KEY_PREFIX = "rate_limit"

# Bucket priority order matches _check_rate_limit_locked(): daily → hourly →
# burst. Limits/windows are read from the LIVE module globals at call time
# (never frozen into a tuple at import) so config and tests remain the single
# source of truth.
_MARK_LUA = """
local now = tonumber(ARGV[1])
local member = ARGV[2]
-- limits (burst, hourly, daily) then windows (burst, hourly, daily)
local limits = {tonumber(ARGV[3]), tonumber(ARGV[4]), tonumber(ARGV[5])}
local windows = {tonumber(ARGV[6]), tonumber(ARGV[7]), tonumber(ARGV[8])}
for i = 1, 3 do
    local cutoff = now - windows[i]
    redis.call('ZREMRANGEBYSCORE', KEYS[i], '-inf', cutoff)
    local count = redis.call('ZCARD', KEYS[i])
    if count >= limits[i] then
        return 0
    end
end
for i = 1, 3 do
    redis.call('ZADD', KEYS[i], now, member)
    redis.call('PEXPIRE', KEYS[i], math.ceil(windows[i] * 1000) + 1000)
end
return 1
"""

_CHECK_LUA = """
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now - window)
local count = redis.call('ZCARD', KEYS[1])
if count == 0 then
    return {0, 0}
end
local oldest = redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')
return {count, tonumber(oldest[2])}
"""

_redis = None
_redis_checked = False
_redis_lock = threading.Lock()
_mark_script = None
_check_script = None


def _rl_key(user_id: int, bucket: str) -> str:
    return f"{_RL_KEY_PREFIX}:{bucket}:{user_id}"


def _get_redis():
    global _redis, _redis_checked, _mark_script, _check_script
    if _redis_checked:
        return _redis
    with _redis_lock:
        if _redis_checked:
            return _redis
        _redis_checked = True
        if not REDIS_URL:
            return None
        try:
            import redis

            client = redis.Redis.from_url(
                REDIS_URL,
                decode_responses=False,
                socket_connect_timeout=1.5,
                socket_timeout=2.0,
            )
            client.ping()
            _mark_script = client.register_script(_MARK_LUA)
            _check_script = client.register_script(_CHECK_LUA)
            _redis = client
            logger.info("Rate limiter: distributed backend (Redis) enabled")
        except Exception as e:
            logger.warning(
                "Rate limiter: Redis unavailable, falling back to in-memory (%s)", e
            )
            _redis = None
        return _redis


def _check_rate_limit_redis(user_id: int) -> RateLimitResult:
    now = time.time()
    spec = (
        ("day", "daily", DAILY_DOWNLOAD_LIMIT, 86400),
        ("hour", "hourly", HOURLY_DOWNLOAD_LIMIT, 3600),
        ("burst", "burst", RATE_LIMIT_BURST, RATE_LIMIT_SECONDS),
    )
    for suffix, reason, limit, window in spec:
        result = _check_script(keys=[_rl_key(user_id, suffix)], args=[now, window])
        count = int(result[0])
        oldest = float(result[1])
        if count >= limit:
            wait = max(1, int(window - (now - oldest)))
            return RateLimitResult(False, reason, wait)
    return RateLimitResult(True)


def _mark_download_redis(user_id: int) -> bool:
    now = time.time()
    member = f"{user_id}:{now:.6f}:{uuid.uuid4().hex}"
    result = _mark_script(
        keys=[
            _rl_key(user_id, "burst"),
            _rl_key(user_id, "hour"),
            _rl_key(user_id, "day"),
        ],
        args=[
            now,
            member,
            RATE_LIMIT_BURST,
            HOURLY_DOWNLOAD_LIMIT,
            DAILY_DOWNLOAD_LIMIT,
            RATE_LIMIT_SECONDS,
            3600,
            86400,
        ],
    )
    return bool(int(result) == 1)


def check_rate_limit_detailed(user_id: int) -> RateLimitResult:
    client = _get_redis()
    if client is not None:
        try:
            return _check_rate_limit_redis(user_id)
        except Exception as e:
            logger.warning(
                "Rate limiter Redis error, using in-memory fallback (%s)", e
            )
    with _lock:
        return _check_rate_limit_locked(user_id, time.monotonic())


def mark_download(user_id: int) -> bool:
    client = _get_redis()
    if client is not None:
        try:
            return _mark_download_redis(user_id)
        except Exception as e:
            logger.warning(
                "Rate limiter Redis error, using in-memory fallback (%s)", e
            )
    # Keep the lock held through the check and all three appends. Otherwise two
    # concurrent requests can both pass the check and exceed the configured limit.
    with _lock:
        now = time.monotonic()
        result = _check_rate_limit_locked(user_id, now)
        if not result.allowed:
            return False
        _recent[user_id].append(now)
        _downloads_hour[user_id].append(now)
        _downloads_day[user_id].append(now)
        return True
