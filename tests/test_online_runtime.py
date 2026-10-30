"""Maintenance has one task, revokes on failed checks and stops cleanly."""

import asyncio
import threading

import pytest

from app.online import runtime


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
