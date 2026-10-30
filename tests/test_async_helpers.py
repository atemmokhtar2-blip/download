"""Logic tests for the event-loop offload helper (no live database needed)."""

import asyncio
import threading

from database.async_helpers import arun


def test_arun_returns_result():
    async def _run():
        return await arun(lambda a, b: a + b, 2, 3)

    assert asyncio.run(_run()) == 5


def test_arun_offloads_off_the_calling_thread():
    main_thread_id = threading.get_ident()

    async def _run():
        return await arun(threading.get_ident)

    worker_thread_id = asyncio.run(_run())

    # asyncio.to_thread uses the default worker pool, never the caller's thread.
    assert worker_thread_id != main_thread_id
