"""One lightweight maintenance task for bounded online sessions and reads."""

import asyncio
from contextlib import suppress
import logging
import time

from fastapi.concurrency import run_in_threadpool

from . import api_pool, artwork, gateway, health, image_pool, service


_task = None
_logger = logging.getLogger("jzmedia.online")


async def _maintain():
    next_disk_sweep = 0
    while True:
        try:
            await run_in_threadpool(service.sweep)
        except Exception:
            # A failing permission/source read must not extend old leases.
            await run_in_threadpool(service.registry.close_all)
            _logger.warning("online session access check failed; sessions revoked")
        try:
            await run_in_threadpool(api_pool.sweep)
        except Exception:
            await run_in_threadpool(api_pool.clear)
            _logger.warning("online API connection maintenance failed; idle connections discarded")
        try:
            await run_in_threadpool(image_pool.sweep)
        except Exception:
            await run_in_threadpool(image_pool.clear)
            _logger.warning("online image connection maintenance failed; idle connections discarded")
        now = time.monotonic()
        if now >= next_disk_sweep:
            next_disk_sweep = now + 60
            try:
                await run_in_threadpool(artwork.disk_cache.sweep)
            except Exception:
                _logger.warning("online original image cache maintenance unavailable")
        await asyncio.sleep(1)


async def start():
    global _task
    gateway.start_workers()
    if _task is None or _task.done():
        api_pool.start()
        image_pool.start()
        _task = asyncio.create_task(_maintain(), name="online-session-maintenance")


async def shutdown():
    global _task
    task, _task = _task, None
    if task is not None:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
    await run_in_threadpool(service.registry.close_all)
    gateway.shutdown_workers()
    await run_in_threadpool(api_pool.shutdown)
    await run_in_threadpool(image_pool.shutdown)
    health.registry.clear()
