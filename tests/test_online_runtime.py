"""Maintenance has one task, revokes on failed checks and stops cleanly."""

import asyncio
import threading
from types import SimpleNamespace

import pytest

from app.online import runtime
from app.online.sessions import SessionRegistry


def test_maintenance_is_singleton_fails_closed_and_has_clean_shutdown(monkeypatch, caplog):
    checked = threading.Event()
    revoked = threading.Event()
    calls = []

    def sweep():
        calls.append("check")
        checked.set()
        raise RuntimeError("private-secret upstream URL must not enter logs")

    def close_all():
        calls.append("revoke")
        revoked.set()

    monkeypatch.setattr(runtime, "_task", None)
    monkeypatch.setattr(runtime.service, "sweep", sweep)
    monkeypatch.setattr(runtime.service.registry, "close_all", close_all)
    monkeypatch.setattr(runtime.gateway, "start_workers", lambda: None)
    monkeypatch.setattr(runtime.gateway, "shutdown_workers", lambda: calls.append("stop"))
    monkeypatch.setattr(runtime.cache, "start", lambda: None)
    monkeypatch.setattr(runtime.cache, "shutdown", lambda: None)

    async def scenario():
        await runtime.start()
        task = runtime._task
        await runtime.start()
        assert runtime._task is task
        assert await asyncio.to_thread(checked.wait, 2)
        assert await asyncio.to_thread(revoked.wait, 2)
        await runtime.shutdown()
        assert task.done()
        assert runtime._task is None

    asyncio.run(scenario())
    assert calls == ["check", "revoke", "revoke", "stop"]
    assert "private-secret" not in caplog.text
    assert "sessions revoked" in caplog.text


@pytest.mark.parametrize("pool", [runtime.api_pool, runtime.image_pool])
def test_connection_maintenance_failure_discards_idle_clients_without_revoking_sessions(monkeypatch, caplog, pool):
    discarded = threading.Event()
    calls = []

    def fail_sweep():
        raise RuntimeError("private-secret proxy credentials must not enter logs")

    def clear():
        calls.append("discard")
        discarded.set()

    monkeypatch.setattr(runtime, "_task", None)
    monkeypatch.setattr(runtime.service, "sweep", lambda: None)
    monkeypatch.setattr(runtime.service.registry, "close_all", lambda: calls.append("revoke"))
    monkeypatch.setattr(runtime.gateway, "start_workers", lambda: None)
    monkeypatch.setattr(runtime.gateway, "shutdown_workers", lambda: None)
    monkeypatch.setattr(runtime.cache, "start", lambda: None)
    monkeypatch.setattr(runtime.cache, "shutdown", lambda: None)
    monkeypatch.setattr(pool, "start", lambda: calls.append("start"))
    monkeypatch.setattr(pool, "sweep", fail_sweep)
    monkeypatch.setattr(pool, "clear", clear)
    monkeypatch.setattr(pool, "shutdown", lambda: calls.append("shutdown"))

    async def scenario():
        await runtime.start()
        task = runtime._task
        await runtime.start()
        assert runtime._task is task
        assert await asyncio.to_thread(discarded.wait, 2)
        assert calls == ["start", "discard"]
        await runtime.shutdown()
        assert task.done() and runtime._task is None

    asyncio.run(scenario())
    assert calls == ["start", "discard", "revoke", "shutdown"]
    assert "private-secret" not in caplog.text
    assert "idle connections discarded" in caplog.text


def test_prefetch_maintenance_does_not_create_an_unused_cache():
    registry = SessionRegistry()
    assert registry._media_cache is None
    registry.maintain_prefetch()
    assert registry._media_cache is None
    assert registry.stats()["sessions"] == registry.stats()["active_requests"] == 0


@pytest.mark.parametrize("failing", [False, True])
def test_each_existing_maintenance_pass_pumps_prefetch_without_revoking_on_prefetch_failure(monkeypatch,
                                                                                         caplog, failing):
    calls, sleeps = [], []

    def prefetch():
        calls.append("prefetch")
        if failing:
            raise RuntimeError("private-secret signed upstream URL must not enter logs")

    async def invoke(function):
        return function()

    async def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(runtime, "run_in_threadpool", invoke)
    monkeypatch.setattr(runtime, "asyncio", SimpleNamespace(sleep=sleep))
    monkeypatch.setattr(runtime.service, "sweep", lambda: calls.append("access"))
    monkeypatch.setattr(runtime.service.registry, "maintain_prefetch", prefetch)
    monkeypatch.setattr(runtime.service.registry, "close_all", lambda: calls.append("revoke"))
    monkeypatch.setattr(runtime.api_pool, "sweep", lambda: calls.append("api"))
    monkeypatch.setattr(runtime.image_pool, "sweep", lambda: calls.append("image"))
    monkeypatch.setattr(runtime.artwork.disk_cache, "sweep", lambda: calls.append("disk"))

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(runtime._maintain())
    assert sleeps == [1, 1]
    assert calls == ["access", "prefetch", "api", "image", "disk", "access", "prefetch", "api", "image"]
    assert "private-secret" not in caplog.text
    assert "sessions revoked" not in caplog.text
