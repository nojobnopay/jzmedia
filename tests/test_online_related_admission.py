"""Optional catalogue work cannot delay or release quotas ahead of playback."""

from contextlib import ExitStack
import threading
import time

import pytest

from app.network.guard import NetworkOperation
from app.online import catalog, related
from app.online.providers import ProviderError
from test_online_related import capture, related_environment as related_environment
from test_online_catalog import environment as environment


class BackgroundWorker:
    def __init__(self, owner):
        self.operation = NetworkOperation(time.monotonic() + 5)
        self.entered, self.aborted, self.release, self.finished = (threading.Event() for _ in range(4))
        self.errors = []
        self.thread = threading.Thread(target=self._run, args=(owner,))

    def _run(self, owner):
        class Socket:
            def shutdown(_self, _how):
                self.aborted.set()
        try:
            with catalog._admit(owner, self.operation, background=True):
                self.operation.bind_socket(Socket())
                self.entered.set()
                assert self.release.wait(3), "test must release the original worker"
                self.operation.check()
        except ProviderError as error:
            self.errors.append(error.category)
        finally:
            self.finished.set()

    def start(self):
        self.thread.start()
        assert self.entered.wait(1)
        return self

    def close(self):
        self.release.set()
        self.thread.join(2)
        assert not self.thread.is_alive()


def foreground(owner, *, timeout=3):
    entered, release, finished = (threading.Event() for _ in range(3))
    errors = []

    def run():
        operation = NetworkOperation(time.monotonic() + timeout)
        try:
            with catalog._admit(owner, operation):
                entered.set()
                assert release.wait(2)
        except ProviderError as error:
            errors.append(error.category)
        finally:
            finished.set()

    thread = threading.Thread(target=run)
    return thread, entered, release, finished, errors


@pytest.mark.parametrize("full_global", [False, True])
def test_foreground_aborts_background_and_waits_for_actual_worker_finally(full_global):
    with ExitStack() as stack:
        if full_global:
            for index in range(3):
                stack.enter_context(catalog._admit(f"existing-{index}"))
        background = BackgroundWorker("optional").start()
        thread, entered, release, finished, errors = foreground("fourth" if full_global else "optional")
        try:
            thread.start()
            assert background.aborted.wait(1)
            assert not entered.wait(0.03)
            stats = catalog.admission_stats()
            assert stats["active"] == (4 if full_global else 1)
            assert stats["background"] == 1 and stats["preempt_waiters"] == 1
            # A second optional request never queues behind a waiting foreground.
            with pytest.raises(catalog.BackgroundDeferred):
                with catalog._admit("other-optional", background=True):
                    pytest.fail("optional work must defer")
            if not full_global:
                # The normal same-owner foreground conflict remains 429 while
                # one foreground request owns the bounded preemption wait.
                with pytest.raises(ProviderError) as error:
                    with catalog._admit("optional"):
                        pytest.fail("duplicate foreground must not queue")
                assert error.value.category == "provider_catalog_busy"
            background.release.set()
            assert entered.wait(1) and background.finished.wait(1)
            assert catalog.admission_stats()["background"] == 0
            assert catalog.admission_stats()["active"] == (4 if full_global else 1)
            assert background.errors == ["provider_cancelled"]
            release.set()
            assert finished.wait(1) and errors == []
        finally:
            background.close()
            release.set()
            thread.join(2)
            assert not thread.is_alive()
    assert catalog.admission_stats() == {"active": 0, "owners": 0, "background": 0, "preempt_waiters": 0}


def test_only_one_background_globally_and_foreground_saturation_is_quiet():
    with catalog._admit("optional", background=True):
        with pytest.raises(catalog.BackgroundDeferred):
            with catalog._admit("other", background=True):
                pytest.fail("global optional cap is one")
        assert catalog.admission_stats()["active"] == 1
    with catalog._admit("foreground"):
        with pytest.raises(catalog.BackgroundDeferred):
            with catalog._admit("foreground", background=True):
                pytest.fail("same owner foreground has priority")
    with ExitStack() as stack:
        for index in range(4):
            stack.enter_context(catalog._admit(f"foreground-{index}"))
        with pytest.raises(catalog.BackgroundDeferred):
            with catalog._admit("optional", background=True):
                pytest.fail("global foreground saturation must defer")
    assert catalog.admission_stats()["active"] == 0


def test_cancelled_foreground_waiter_cleans_up_but_never_frees_background_early():
    background = BackgroundWorker("optional").start()
    operation = NetworkOperation(time.monotonic() + 2)
    errors = []
    finished = threading.Event()

    def run():
        try:
            with catalog._admit("optional", operation):
                pytest.fail("cancelled waiter must not enter")
        except ProviderError as error:
            errors.append(error.category)
        finally:
            finished.set()

    thread = threading.Thread(target=run)
    try:
        thread.start()
        assert background.aborted.wait(1)
        operation.abort()
        assert finished.wait(1)
        assert errors == ["provider_cancelled"]
        assert catalog.admission_stats() == {"active": 1, "owners": 1, "background": 1, "preempt_waiters": 0}
    finally:
        background.close()
        thread.join(2)
    assert catalog.admission_stats()["active"] == 0


@pytest.mark.parametrize("full_global", [False, True])
def test_new_foreground_respects_reserved_slot_but_can_use_unreserved_capacity(full_global):
    paused, resume, entered = (threading.Event() for _ in range(3))
    errors = []

    class PausedWaiter(NetworkOperation):
        calls = 0

        def check(self):
            super().check()
            self.calls += 1
            if self.calls == 2:
                paused.set()
                assert resume.wait(2)

    operation = PausedWaiter(time.monotonic() + 3)

    def run():
        try:
            with catalog._admit("reserved-waiter" if full_global else "optional", operation):
                entered.set()
        except ProviderError as error:
            errors.append(error.category)

    with ExitStack() as stack:
        for index in range(3 if full_global else 0):
            stack.enter_context(catalog._admit(f"foreground-{index}"))
        background = BackgroundWorker("optional").start()
        thread = threading.Thread(target=run)
        try:
            thread.start()
            assert background.aborted.wait(1)
            background.release.set()
            assert background.finished.wait(1) and paused.wait(1)
            assert catalog.admission_stats()["active"] == (3 if full_global else 0)
            if full_global:
                with pytest.raises(ProviderError) as error:
                    with catalog._admit("newcomer"):
                        pytest.fail("released slot belongs to the preemption waiter")
                assert error.value.category == "provider_catalog_busy"
            else:
                with catalog._admit("newcomer"):
                    assert catalog.admission_stats()["active"] == 1
            resume.set()
            thread.join(2)
            assert entered.is_set() and errors == [] and not thread.is_alive()
        finally:
            resume.set()
            background.close()
            thread.join(2)


def test_preemption_waiters_have_a_global_bound_and_remain_deadline_bounded(monkeypatch):
    monkeypatch.setattr(catalog, "MAX_PREEMPT_WAITERS", 1)
    with ExitStack() as stack:
        for index in range(3):
            stack.enter_context(catalog._admit(f"foreground-{index}"))
        background = BackgroundWorker("optional").start()
        thread, entered, release, finished, errors = foreground("first-waiter", timeout=0.15)
        try:
            thread.start()
            assert background.aborted.wait(1)
            with pytest.raises(ProviderError) as error:
                with catalog._admit("second-waiter"):
                    pytest.fail("foreground waiters must stay bounded")
            assert error.value.category == "provider_catalog_busy"
            assert finished.wait(1) and not entered.is_set()
            assert errors == ["provider_timeout"]
            assert catalog.admission_stats()["active"] == 4
            assert catalog.admission_stats()["preempt_waiters"] == 0
        finally:
            background.close()
            release.set()
            thread.join(2)


def test_related_saturation_returns_deferred_without_provider_io_or_cache(related_environment):
    state = related_environment
    access = capture(state)
    with catalog._admit(access.owner):
        result = related.read(access, state.source["id"], "s2")
        assert result["status"] == "deferred" and result["series"] == []
        assert state.calls == [] and related.cache.stats()["entries"] == 0
