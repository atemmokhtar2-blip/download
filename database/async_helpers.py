"""Helpers to keep blocking (synchronous) work off the Telegram event loop.

python-telegram-bot v22 runs every handler on a single asyncio event loop. A
blocking psycopg2 query inside a handler stalls EVERY user's update while it
waits on the network + PostgreSQL round-trip (worst on Neon cold starts). Wrap
such calls with :func:`arun` to run them on a worker thread instead, exactly
like ``asyncio.to_thread`` but with a single, greppable name for the pattern.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, TypeVar

T = TypeVar("T")


async def arun(fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Run the blocking callable ``fn`` off the event loop; await its result."""
    return await asyncio.to_thread(fn, *args, **kwargs)
