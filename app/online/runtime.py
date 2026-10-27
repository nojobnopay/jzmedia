"""One lightweight maintenance task for bounded online sessions and reads."""

import asyncio
from contextlib import suppress
import logging

from fastapi.concurrency import run_in_threadpool

from . import gateway, health, service


_task = None
_logger = logging.getLogger("jzmedia.online")


async def _maintain():
    while True:
        try:
            await run_in_threadpool(service.sweep)
        except Exception:
            # A failing permission/source read must not extend old leases.
            await run_in_threadpool(service.registry.close_all)
            _logger.warning("online session access check failed; sessions revoked")
        await asyncio.sleep(1)


async def start():
    global _task
    gateway.start_workers()
    if _task is None or _task.done():
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
    health.registry.clear()
