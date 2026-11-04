"""Deterministic download scheduling with isolated sessions and no network I/O."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.network.client import NetworkRequestError
from app.network.targets import TargetPolicy
from app.online.gateway import GatewayError
from app.online.hls import MediaSegment
from app.online.models import OnlinePlaybackIdentity
from app.online.prefetch import CacheStore, CacheUnavailable, CachedSegment
from app.online.sessions import SessionError, SessionLimits, SessionRegistry


SOURCE = "src_" + "a" * 32


class Client:
    def __init__(self):
        self.snapshot = SimpleNamespace(source_id=SOURCE)
        self.targets = TargetPolicy(SOURCE, ["media.example"], resolver=lambda *_: ("8.8.8.8",))

    def close(self):
        pass


class Harness:
    def __init__(self, *, clock=None, **overrides):
        defaults = dict(max_segment_response_bytes=8, prefetch_session_bytes=64,
                        prefetch_global_bytes=128, max_session_bytes=4096,
                        max_reserved_bytes=1024, max_session_active=8)
        timing = {} if clock is None else {"clock": clock}
        self.registry = SessionRegistry(replace(SessionLimits(), **(defaults | overrides)), **timing)
        self.jobs, self.reads, self.failures = [], [], set()
        self.cache = self.registry.cached_media(lambda: CacheStore(self.registry, self.fetch, self.jobs.append,
                                                                  **timing))

    def session(self, name="one"):
        identity = OnlinePlaybackIdentity(source_id=SOURCE, item_id="itm_" + "b" * 32, kind="movie")
        return self.registry.create(identity, name, Client(), "https://media.example/index.m3u8", "hls")

    def resources(self, session, count, *, prefix="seg", kind="segment"):
        return [self.registry.register(session, f"https://media.example/{prefix}{i}.bin", kind)
                for i in range(count)]

    def remember(self, session, resources, manifest="main", duration=10):
        self.cache.remember(session, manifest, tuple(MediaSegment(r.path(session.id), duration) for r in resources))

    def fetch(self, session, resource, lease):
        self.reads.append((session.id, resource.id, lease.background))
        if resource.id in self.failures:
            raise RuntimeError("isolated_failure")
        lease.consume(4)
        return CachedSegment(200, {"Content-Length": "4", "Cache-Control": "no-store"}, b"data")

    def run(self, index=0):
        self.jobs.pop(index)()

    def drain(self):
        for _ in range(100):
            if not self.jobs:
                return
            self.run()
        pytest.fail("unbounded background scheduling")


class RetryClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class RetryHarness(Harness):
    def __init__(self, **overrides):
        self.clock = RetryClock()
        self.errors = {}
        super().__init__(clock=self.clock, **({"max_session_active": 2} | overrides))

    def fetch(self, session, resource, lease):
        errors = self.errors.get(resource.id)
        if errors:
            self.reads.append((session.id, resource.id, lease.background))
            lease.consume(2)  # Bytes read before a failed body remain charged.
            raise errors.pop(0)
        return super().fetch(session, resource, lease)

    def maintain_after(self, seconds):
        self.clock.advance(seconds)
        self.registry.maintain_prefetch()


@pytest.fixture
def retry_factory():
    harnesses = []

    def create(**overrides):
        result = RetryHarness(**overrides)
        harnesses.append(result)
        return result

    yield create
    for result in harnesses:
        result.registry.close_all()
        result.drain()


@pytest.fixture
def harness():
    result = Harness()
    yield result
    result.registry.close_all()
    result.drain()


def test_remember_is_passive_and_real_demand_starts_at_most_two_downloads(harness):
    session = harness.session()
    resources = harness.resources(session, 4)
    harness.remember(session, resources)
    assert harness.jobs == [] and harness.registry.stats()["active_requests"] == 0
    ticket = harness.cache.demand(session, resources[0])
    assert len(harness.jobs) == 2 and harness.cache.stats()["active"] == 2
    harness.drain()
    assert ticket.future.result().body == b"data"
    assert [read[1] for read in harness.reads] == [r.id for r in resources]
    assert harness.cache.stats()["prefetch_completed"] == 3
    assert harness.cache.stats()["download_bytes"] == 16
    assert harness.cache.stats()["download_seconds"] >= 0
    harness.cache.release(ticket)


def test_longer_default_window_caches_more_realistic_segments_within_budgets():
    # A 3-second segment near the size observed in a real playback sample.
    # Reuse the immutable bytes to test accounting without allocating a movie.
    body = b"x" * 880_404
    defaults = SessionLimits()

    def download_window(**changes):
        limits = replace(defaults, **changes)
        harness = Harness(max_segment_response_bytes=limits.max_segment_response_bytes,
                          prefetch_seconds=limits.prefetch_seconds,
                          prefetch_max_segments=limits.prefetch_max_segments,
                          prefetch_session_bytes=limits.prefetch_session_bytes,
                          prefetch_global_bytes=limits.prefetch_global_bytes,
                          max_session_bytes=limits.max_session_bytes,
                          max_reserved_bytes=limits.max_reserved_bytes,
                          max_session_active=limits.max_session_active)
        session = harness.session()
        resources = harness.resources(session, 80)
        harness.remember(session, resources, duration=3)
        reads = []

        def fetch(owner, resource, lease):
            reads.append(resource.id)
            assert harness.cache.set_length(owner, resource, lease, len(body))
            lease.consume(len(body))
            return CachedSegment(200, {"Content-Length": str(len(body))}, body)

        harness.cache.fetch = fetch
        ticket = harness.cache.demand(session, resources[0])
        peak_active = 0
        for _ in range(100):
            peak_active = max(peak_active, harness.registry.stats()["active_requests"])
            assert harness.cache.stats()["bytes"] <= limits.prefetch_session_bytes
            assert harness.cache.stats()["bytes"] <= limits.prefetch_global_bytes
            assert harness.registry.stats()["reserved_bytes"] <= limits.max_reserved_bytes
            if not harness.jobs:
                break
            harness.run()
        else:
            pytest.fail("prefetch did not stop at the window boundary")
        assert ticket.future.result().body == body
        assert reads == [resource.id for resource in resources[:len(reads)]]
        assert harness.registry.stats()["consumed_bytes"] == len(reads) * len(body)
        assert harness.registry.stats()["active_requests"] == 0
        assert harness.registry.stats()["reserved_bytes"] == 0
        harness.cache.release(ticket)
        harness.registry.close_all()
        assert harness.cache.stats()["bytes"] == 0
        return len(reads) - 1, peak_active

    old = download_window(prefetch_seconds=90, prefetch_max_segments=24,
                          prefetch_session_bytes=64 * 1024**2,
                          prefetch_global_bytes=128 * 1024**2)
    new = download_window()
    assert old == (24, 2)
    assert new == (60, 2)  # 60 complete three-second segments, not just a larger setting.


def test_seek_and_close_retire_a_long_default_window():
    harness = Harness(max_session_active=2)
    session = harness.session()
    resources = harness.resources(session, 80)
    harness.remember(session, resources, duration=3)
    first = harness.cache.demand(session, resources[0])
    harness.run()  # Current segment completes while the old prefetch is pending.
    seek = harness.cache.demand(session, resources[70])
    harness.run()  # The obsolete background worker exits before releasing its slot.
    harness.run()  # The new foreground request completes.
    assert seek.future.result().body == b"data"
    assert [read[1] for read in harness.reads] == [resources[0].id, resources[70].id]
    assert harness.jobs, "the new position should have its own lookahead"
    harness.registry.close(session.id, session.owner)
    harness.cache.release(first)
    harness.cache.release(seek)
    harness.drain()
    assert [read[1] for read in harness.reads] == [resources[0].id, resources[70].id]
    assert harness.cache.stats()["entries"] == harness.cache.stats()["bytes"] == 0
    assert harness.registry.stats()["active_requests"] == harness.registry.stats()["reserved_bytes"] == 0


def test_short_segments_remain_bounded_by_the_default_fragment_limit():
    harness = Harness(prefetch_session_bytes=4096, prefetch_global_bytes=8192,
                      max_session_active=2)
    session = harness.session()
    resources = harness.resources(session, 120)
    harness.remember(session, resources, duration=1)
    ticket = harness.cache.demand(session, resources[0])
    harness.drain()
    assert [read[1] for read in harness.reads] == [resource.id for resource in resources[:97]]
    assert harness.cache.stats()["prefetch_completed"] == 96
    harness.cache.release(ticket)
    harness.registry.close_all()


def test_long_windows_share_global_budget_and_survive_other_session_close():
    limits = SessionLimits()
    harness = Harness(max_segment_response_bytes=limits.max_segment_response_bytes,
                      prefetch_session_bytes=limits.prefetch_session_bytes,
                      prefetch_global_bytes=limits.prefetch_global_bytes,
                      max_session_bytes=limits.max_session_bytes,
                      max_reserved_bytes=limits.max_reserved_bytes,
                      max_session_active=limits.max_session_active)
    first, second = harness.session("first"), harness.session("second")
    first_resources = harness.resources(first, 6, prefix="first")
    second_resources = harness.resources(second, 6, prefix="second")
    harness.remember(first, first_resources, duration=3)
    harness.remember(second, second_resources, duration=3)
    # A maximum-sized segment stresses both limits without allocating one copy
    # per cache entry. This tests accounting, not process RSS.
    body = b"x" * limits.max_segment_response_bytes
    reads = []

    def fetch(owner, resource, lease):
        reads.append((owner.id, resource.id))
        assert harness.cache.set_length(owner, resource, lease, len(body))
        lease.consume(len(body))
        return CachedSegment(200, {"Content-Length": str(len(body))}, body)

    harness.cache.fetch = fetch
    first_ticket = harness.cache.demand(first, first_resources[0])
    second_ticket = harness.cache.demand(second, second_resources[0])

    def drain_bounded():
        for _ in range(100):
            cache = harness.cache.stats()
            assert cache["bytes"] <= limits.prefetch_global_bytes
            assert cache["entries"] <= limits.prefetch_max_entries
            for session in (first, second):
                assert harness.cache._size_locked(session.id) <= limits.prefetch_session_bytes
            assert harness.registry.stats()["reserved_bytes"] <= limits.max_reserved_bytes
            if not harness.jobs:
                return
            harness.run()
        pytest.fail("shared cache did not stop at its budget")

    drain_bounded()
    assert harness.cache.stats()["bytes"] == limits.prefetch_global_bytes
    assert first_ticket.future.result().body is body
    assert second_ticket.future.result().body is body
    second_reads = sum(owner == second.id for owner, _resource in reads)
    harness.registry.close(first.id, first.owner)
    harness.cache.release(first_ticket)
    assert second_ticket.future.result().body is body
    harness.cache.release(second_ticket)
    drain_bounded()
    assert sum(owner == second.id for owner, _resource in reads) > second_reads
    harness.registry.close(second.id, second.owner)
    assert harness.cache.stats()["entries"] == harness.cache.stats()["bytes"] == 0
    assert harness.registry.stats()["active_requests"] == harness.registry.stats()["reserved_bytes"] == 0


def test_foreground_coalesces_and_cache_hit_does_not_read_upstream_again(harness):
    session = harness.session()
    resource = harness.resources(session, 1)[0]
    first = harness.cache.demand(session, resource)
    second = harness.cache.demand(session, resource)
    assert first.future is second.future and len(harness.jobs) == 1
    harness.run()
    harness.cache.release(first)
    harness.cache.release(second)
    cached = harness.cache.demand(session, resource)
    assert cached.cached and cached.future.result().body == b"data"
    assert len(harness.reads) == 1 and harness.cache.stats()["hits"] == 1
    harness.cache.release(cached)


def test_waiter_count_cannot_bypass_per_session_admission():
    harness = Harness(max_session_active=2)
    session = harness.session()
    resource = harness.resources(session, 1)[0]
    first = harness.cache.demand(session, resource)
    second = harness.cache.demand(session, resource)
    with pytest.raises(SessionError, match="request_limit"):
        harness.cache.demand(session, resource)
    harness.cache.release(first)
    assert not first._entry.lease.cancelled.is_set()
    harness.cache.release(second)
    assert first._entry.lease.cancelled.is_set()
    assert harness.cache.stats()["bytes"] == 8
    harness.drain()
    assert harness.cache.stats()["bytes"] == 0
    assert harness.registry.stats()["active_requests"] == 0
    harness.registry.close_all()


def test_foreground_promotes_inflight_background_before_joining(harness):
    session = harness.session()
    resources = harness.resources(session, 3)
    harness.remember(session, resources)
    first = harness.cache.demand(session, resources[0])
    next_ticket = harness.cache.demand(session, resources[1])
    assert not next_ticket._entry.lease.background
    assert len(harness.jobs) == 2
    harness.drain()
    assert next_ticket.future.result().body == b"data"
    harness.cache.release(first)
    harness.cache.release(next_ticket)


def test_seek_cancels_old_window_without_freeing_its_slot_early(harness):
    session = harness.session()
    resources = harness.resources(session, 8)
    harness.remember(session, resources)
    first = harness.cache.demand(session, resources[0])
    harness.run()  # Foreground complete; one old-window download remains.
    seek = harness.cache.demand(session, resources[-1])
    assert harness.cache.stats()["active"] == 2
    assert harness.registry.stats()["active_requests"] == 2
    harness.run()  # Cancelled old-window job must exit before its lease releases.
    assert harness.registry.stats()["active_requests"] == 1
    harness.run()
    assert seek.future.result().body == b"data"
    assert [read[1] for read in harness.reads] == [resources[0].id, resources[-1].id]
    harness.cache.release(first)
    harness.cache.release(seek)


def test_gap_map_and_repeat_paths_are_handled_without_speculative_guessing(harness):
    session = harness.session()
    resources = harness.resources(session, 3)
    init = harness.resources(session, 1, prefix="init", kind="map")[0]
    harness.cache.remember(session, "main", (
        MediaSegment(resources[0].path(session.id), 10),
        MediaSegment(resources[1].path(session.id), 10, gap=True),
        MediaSegment(resources[2].path(session.id), 10, init.path(session.id)),
    ))
    ticket = harness.cache.demand(session, resources[0])
    harness.drain()
    assert [read[1] for read in harness.reads] == [resources[0].id, init.id, resources[2].id]
    harness.cache.release(ticket)
    harness.cache.remember(session, "repeated", (
        MediaSegment(resources[0].path(session.id), 10), MediaSegment(resources[0].path(session.id), 10),
    ))
    ticket = harness.cache.demand(session, resources[0])
    assert harness.jobs == []
    harness.cache.release(ticket)


def test_background_failure_has_no_automatic_retry_and_foreground_can_retry(harness):
    session = harness.session()
    resources = harness.resources(session, 2)
    harness.remember(session, resources)
    harness.failures.add(resources[1].id)
    first = harness.cache.demand(session, resources[0])
    harness.drain()
    assert first.future.result().body == b"data"
    assert len(harness.reads) == 2 and harness.jobs == []
    assert harness.cache.stats()["failures"] == 1
    harness.failures.clear()
    retry = harness.cache.demand(session, resources[1])
    harness.drain()
    assert retry.future.result().body == b"data" and len(harness.reads) == 3
    harness.cache.release(first)
    harness.cache.release(retry)


@pytest.mark.parametrize("error_type,category", [
    (GatewayError, "network_timeout"),
    (GatewayError, "network_request_failed"),
    (GatewayError, "operation_timeout"),
    (NetworkRequestError, "network_request_failed"),
])
def test_transient_near_segment_recovers_after_cooldown_before_foreground_demand(retry_factory,
                                                                                error_type, category):
    harness = retry_factory()
    session = harness.session()
    resources = harness.resources(session, 4)
    harness.remember(session, resources)
    harness.errors[resources[1].id] = [error_type(category)]
    first = harness.cache.demand(session, resources[0])
    harness.drain()
    harness.cache.release(first)
    assert [read[1] for read in harness.reads] == [resources[0].id, resources[1].id]
    assert harness.cache.stats()["retry_pending"] == 1
    assert harness.registry.stats()["consumed_bytes"] == 6
    assert harness.registry.stats()["reserved_bytes"] == 0

    harness.maintain_after(.5)
    assert not harness.jobs, "A cooling near segment must keep the far tail from taking its lane"
    harness.maintain_after(.5)
    assert len(harness.jobs) == harness.cache.stats()["active"] == 2
    assert harness.registry.stats()["active_requests"] == 2
    assert harness.registry.stats()["reserved_bytes"] == 16
    assert harness.cache.stats()["bytes"] == 20  # Complete current body plus two reservations.
    harness.drain()
    assert [read[1] for read in harness.reads] == [
        resources[0].id, resources[1].id, resources[1].id, resources[2].id, resources[3].id]
    before = harness.registry.stats()["consumed_bytes"]
    recovered = harness.cache.demand(session, resources[1])
    assert recovered.cached and recovered.future.result().body == b"data"
    harness.cache.release(recovered)
    assert harness.registry.stats()["consumed_bytes"] == before == 18
    assert harness.cache.stats()["download_bytes"] == 16
    assert harness.cache.stats()["bytes"] == 16
    assert harness.cache.stats()["waiters"] == harness.cache.stats()["active"] == 0
    assert harness.cache.stats()["retry_pending"] == 0
    assert harness.cache.stats()["automatic_retries"] == 1
    assert harness.registry.stats()["active_requests"] == harness.registry.stats()["reserved_bytes"] == 0


def test_persistent_transient_failure_gets_only_one_extra_background_attempt(retry_factory):
    harness = retry_factory()
    session = harness.session()
    resources = harness.resources(session, 2)
    harness.remember(session, resources)
    harness.errors[resources[1].id] = [GatewayError("network_timeout") for _ in range(4)]
    first = harness.cache.demand(session, resources[0])
    harness.drain()
    harness.cache.release(first)
    harness.maintain_after(1)
    harness.drain()
    for _ in range(4):
        harness.maintain_after(2)
        harness.drain()
    assert [read[1] for read in harness.reads].count(resources[1].id) == 2
    assert harness.cache.stats()["automatic_retries"] == 1
    assert harness.cache.stats()["retry_pending"] == 0
    assert harness.cache.stats()["failures"] == 2
    assert harness.registry.stats()["consumed_bytes"] == 8
    assert harness.registry.stats()["active_requests"] == harness.registry.stats()["reserved_bytes"] == 0


@pytest.mark.parametrize("error", [
    GatewayError("proxy_connection_failed"), GatewayError("online_upstream_status"),
    GatewayError("unsupported_upstream_content_type"), GatewayError("upstream_length_mismatch"),
    GatewayError("target_host_not_allowed"), GatewayError("response_byte_limit"),
    SessionError("request_limit"), SessionError("request_cancelled"), SessionError("request_timeout"),
    RuntimeError("network_timeout"),
])
def test_non_transient_failures_never_get_automatic_background_retries(retry_factory, error):
    harness = retry_factory()
    session = harness.session()
    resources = harness.resources(session, 2)
    harness.remember(session, resources)
    harness.errors[resources[1].id] = [error]
    first = harness.cache.demand(session, resources[0])
    harness.drain()
    harness.cache.release(first)
    harness.maintain_after(5)
    harness.drain()
    assert [read[1] for read in harness.reads].count(resources[1].id) == 1
    assert harness.cache.stats()["automatic_retries"] == harness.cache.stats()["retry_pending"] == 0


def test_foreground_recovery_clears_pending_without_waiting_for_cooldown(retry_factory):
    harness = retry_factory()
    session = harness.session()
    resources = harness.resources(session, 2)
    harness.remember(session, resources)
    harness.errors[resources[1].id] = [GatewayError("network_request_failed")]
    first = harness.cache.demand(session, resources[0])
    harness.drain()
    harness.cache.release(first)
    assert harness.cache.stats()["retry_pending"] == 1
    foreground = harness.cache.demand(session, resources[1])
    assert harness.cache.stats()["retry_pending"] == 0
    harness.drain()
    assert foreground.future.result().body == b"data"
    harness.cache.release(foreground)
    harness.maintain_after(3)
    assert len(harness.reads) == 3
    assert harness.reads[-1][2] is False
    assert harness.cache.stats()["automatic_retries"] == 0


def test_foreground_recovery_and_cache_eviction_do_not_replenish_spent_retry_budget(retry_factory):
    harness = retry_factory(prefetch_seconds=10, prefetch_session_bytes=12, prefetch_global_bytes=24)
    session = harness.session()
    resources = harness.resources(session, 4)
    harness.remember(session, resources)
    harness.errors[resources[1].id] = [GatewayError("network_timeout"), GatewayError("network_timeout")]
    first = harness.cache.demand(session, resources[0])
    harness.drain()
    harness.cache.release(first)
    harness.maintain_after(1)
    harness.drain()
    foreground = harness.cache.demand(session, resources[1])
    harness.drain()
    assert foreground.future.result().body == b"data"
    harness.cache.release(foreground)
    assert harness.cache.stats()["retry_used"] == 1

    # Two actual seeks and bounded cache pressure evict the recovered near body.
    far = harness.cache.demand(session, resources[3])
    harness.drain()
    harness.cache.release(far)
    harness.errors[resources[1].id] = [GatewayError("network_request_failed")]
    back = harness.cache.demand(session, resources[0])
    harness.drain()
    harness.cache.release(back)
    harness.maintain_after(5)
    harness.drain()
    near_reads = [read for read in harness.reads if read[1] == resources[1].id]
    assert [read[2] for read in near_reads] == [True, True, False, True]
    assert harness.cache.stats()["automatic_retries"] == harness.cache.stats()["retry_used"] == 1
    assert harness.cache.stats()["retry_pending"] == 0


@pytest.mark.parametrize("retirement", ["seek", "manifest", "close", "expiry"])
def test_retired_retry_windows_never_restart_downloads(retry_factory, retirement):
    harness = retry_factory(idle_seconds=10)
    session = harness.session()
    resources = harness.resources(session, 4)
    harness.remember(session, resources)
    harness.errors[resources[1].id] = [GatewayError("network_timeout")]
    first = harness.cache.demand(session, resources[0])
    harness.drain()
    harness.cache.release(first)
    assert harness.cache.stats()["retry_pending"] == 1
    if retirement == "seek":
        seek = harness.cache.demand(session, resources[-1])
        harness.drain()
        harness.cache.release(seek)
    elif retirement == "manifest":
        harness.remember(session, resources[-2:])
    elif retirement == "close":
        harness.registry.close(session.id, session.owner)
    else:
        harness.clock.advance(11)
        harness.registry.sweep()
    harness.maintain_after(2)
    harness.drain()
    assert [read[1] for read in harness.reads].count(resources[1].id) == 1
    assert harness.cache.stats()["retry_pending"] == 0
    assert harness.cache.stats()["automatic_retries"] == 0
    if retirement in {"close", "expiry"}:
        assert harness.cache.stats()["bytes"] == harness.cache.stats()["entries"] == 0
        assert harness.cache.stats()["retry_used"] == harness.cache.stats()["timelines"] == 0
    assert harness.registry.stats()["active_requests"] == harness.registry.stats()["reserved_bytes"] == 0


def test_cancelled_background_transient_error_does_not_schedule_recovery(retry_factory):
    harness = retry_factory()
    session = harness.session()
    resources = harness.resources(session, 2)
    harness.remember(session, resources)
    first = harness.cache.demand(session, resources[0])
    harness.run()
    harness.cache.release(first)

    def cancelled_fetch(current, resource, lease):
        harness.reads.append((current.id, resource.id, lease.background))
        lease.abort()
        raise GatewayError("network_timeout")

    harness.cache.fetch = cancelled_fetch
    harness.drain()
    harness.maintain_after(3)
    harness.drain()
    assert [read[1] for read in harness.reads].count(resources[1].id) == 1
    assert harness.cache.stats()["cancelled"] == 1
    assert harness.cache.stats()["retry_pending"] == harness.cache.stats()["automatic_retries"] == 0
    assert harness.registry.stats()["active_requests"] == harness.registry.stats()["reserved_bytes"] == 0


def test_promoted_map_failure_does_not_add_a_second_foreground_retry_policy(retry_factory):
    harness = retry_factory()
    session = harness.session()
    resources = harness.resources(session, 2)
    init = harness.resources(session, 1, prefix="init", kind="map")[0]
    harness.cache.remember(session, "main", (
        MediaSegment(resources[0].path(session.id), 10),
        MediaSegment(resources[1].path(session.id), 10, init.path(session.id)),
    ))
    harness.errors[init.id] = [GatewayError("network_timeout")]
    first = harness.cache.demand(session, resources[0])
    harness.run()
    promoted = harness.cache.demand(session, init)
    assert promoted._entry.prefetched and not promoted._entry.lease.background
    harness.drain()
    with pytest.raises(GatewayError, match="^network_timeout$"):
        promoted.future.result()
    harness.cache.release(promoted)
    harness.cache.release(first)
    harness.maintain_after(2)
    harness.drain()
    assert [read[1] for read in harness.reads].count(init.id) == 1
    assert harness.cache.stats()["retry_pending"] == harness.cache.stats()["automatic_retries"] == 0


def test_retry_admission_respects_existing_slots_and_spends_budget_only_after_admission(retry_factory):
    harness = retry_factory()
    session = harness.session()
    resources = harness.resources(session, 2)
    harness.remember(session, resources)
    harness.errors[resources[1].id] = [GatewayError("network_timeout")]
    first = harness.cache.demand(session, resources[0])
    harness.drain()
    harness.cache.release(first)
    leases = [harness.registry.acquire(session, 8, kind="segment") for _ in range(2)]
    try:
        harness.maintain_after(1)
        assert not harness.jobs
        assert harness.cache.stats()["retry_pending"] == 1
        assert harness.cache.stats()["retry_used"] == 0
        assert harness.registry.stats()["active_requests"] == 2
        assert harness.registry.stats()["reserved_bytes"] == 16
    finally:
        for lease in leases:
            lease.close()
    harness.registry.maintain_prefetch()
    assert len(harness.jobs) == 1
    harness.drain()
    assert harness.cache.stats()["automatic_retries"] == 1
    assert harness.registry.stats()["consumed_bytes"] == 10
    assert harness.registry.stats()["active_requests"] == harness.registry.stats()["reserved_bytes"] == 0


def test_retry_cannot_bypass_session_byte_budget(retry_factory):
    harness = retry_factory(max_session_bytes=12)
    session = harness.session()
    resources = harness.resources(session, 2)
    harness.remember(session, resources)
    harness.errors[resources[1].id] = [GatewayError("network_timeout")]
    first = harness.cache.demand(session, resources[0])
    harness.drain()
    harness.cache.release(first)
    harness.maintain_after(1)
    harness.maintain_after(1)
    assert not harness.jobs
    assert len(harness.reads) == 2
    assert harness.registry.stats()["consumed_bytes"] == 6
    assert harness.registry.stats()["reserved_bytes"] == 0
    assert harness.cache.stats()["automatic_retries"] == 0


def test_queued_retry_keeps_existing_request_deadline(retry_factory):
    harness = retry_factory()
    session = harness.session()
    resources = harness.resources(session, 2)
    harness.remember(session, resources)
    harness.errors[resources[1].id] = [GatewayError("network_timeout")]
    first = harness.cache.demand(session, resources[0])
    harness.drain()
    harness.cache.release(first)
    harness.maintain_after(1)
    retry = harness.cache._entries[(session.id, resources[1].id)]
    assert retry.lease.deadline == min(session.expires_monotonic,
                                       harness.clock.now + harness.registry.limits.request_seconds)
    harness.clock.advance(harness.registry.limits.request_seconds + 1)
    harness.drain()
    harness.registry.maintain_prefetch()
    assert len(harness.reads) == 2, "Expired queued work must not open a new upstream read"
    assert harness.cache.stats()["automatic_retries"] == 1
    assert harness.cache.stats()["retry_pending"] == 0
    assert harness.registry.stats()["active_requests"] == harness.registry.stats()["reserved_bytes"] == 0


def test_capacity_reserves_full_response_and_holds_live_delivery_bytes_until_release():
    harness = Harness(prefetch_session_bytes=8, prefetch_global_bytes=8)
    session = harness.session()
    resources = harness.resources(session, 2)
    first = harness.cache.demand(session, resources[0])
    with pytest.raises(CacheUnavailable):
        harness.cache.demand(session, resources[1])
    assert harness.cache.stats()["bytes"] == 8
    harness.run()
    assert harness.cache.stats()["bytes"] == 4
    harness.registry.close(session.id, session.owner)
    assert harness.cache.stats()["bytes"] == 4
    harness.cache.release(first)
    assert harness.cache.stats()["bytes"] == 0
    assert harness.cache.stats()["entries"] == 0


def test_entry_cap_evicts_unheld_complete_entries_but_not_current_delivery():
    harness = Harness(prefetch_max_entries=1)
    session = harness.session()
    resources = harness.resources(session, 2)
    first = harness.cache.demand(session, resources[0])
    harness.run()
    with pytest.raises(CacheUnavailable):
        harness.cache.demand(session, resources[1])
    harness.cache.release(first)
    second = harness.cache.demand(session, resources[1])
    harness.run()
    assert second.future.result().body == b"data"
    assert harness.cache.stats()["entries"] == 1
    harness.cache.release(second)
    harness.registry.close_all()


def test_close_cancels_downloads_and_keeps_reservation_until_workers_exit(harness):
    session = harness.session()
    resources = harness.resources(session, 3)
    harness.remember(session, resources)
    ticket = harness.cache.demand(session, resources[0])
    harness.registry.close(session.id, session.owner)
    assert harness.cache.stats()["timelines"] == 0
    assert harness.cache.stats()["bytes"] == 16
    harness.drain()
    with pytest.raises(SessionError):
        ticket.future.result()
    harness.cache.release(ticket)
    assert harness.cache.stats()["entries"] == harness.cache.stats()["bytes"] == 0
    assert harness.cache.stats()["cancelled"] == 2
    assert not harness.cache._failed, "late cancelled workers must not recreate retired session metadata"


def test_new_session_foreground_has_capacity_despite_another_session_prefetch(harness):
    first_session = harness.session("first")
    first_resources = harness.resources(first_session, 5, prefix="first")
    harness.remember(first_session, first_resources)
    first = harness.cache.demand(first_session, first_resources[0])
    second_session = harness.session("second")
    second_resource = harness.resources(second_session, 1, prefix="second")[0]
    second = harness.cache.demand(second_session, second_resource)
    assert harness.registry.stats()["active_requests"] == 3
    harness.drain()
    assert first.future.result().body == second.future.result().body == b"data"
    harness.cache.release(first)
    harness.cache.release(second)


def test_global_capacity_applies_across_sessions_and_lru_reclaims_only_released_payloads():
    harness = Harness(prefetch_global_bytes=8)
    one, two = harness.session("one"), harness.session("two")
    first_resource = harness.resources(one, 1, prefix="one")[0]
    second_resource = harness.resources(two, 1, prefix="two")[0]
    first = harness.cache.demand(one, first_resource)
    with pytest.raises(CacheUnavailable):
        harness.cache.demand(two, second_resource)
    harness.run()
    with pytest.raises(CacheUnavailable):
        harness.cache.demand(two, second_resource)
    harness.cache.release(first)
    second = harness.cache.demand(two, second_resource)
    assert harness.cache.stats()["bytes"] == 8
    harness.run()
    assert second.future.result().body == b"data"
    harness.cache.release(second)
    harness.registry.close_all()


def test_timeline_metadata_cannot_outgrow_resource_budget_with_repeated_references():
    harness = Harness(max_resources=5)
    session = harness.session()
    resources = harness.resources(session, 3)
    for index in range(10):
        harness.remember(session, resources, manifest=f"list{index}")
    assert harness.cache.stats()["timelines"] == 1
    assert harness.jobs == []
    harness.registry.close_all()


def test_repeated_segment_path_does_not_start_ambiguous_lookahead(harness):
    session = harness.session()
    resources = harness.resources(session, 2)
    harness.remember(session, [resources[0], resources[0], resources[1]])
    ticket = harness.cache.demand(session, resources[0])
    assert len(harness.jobs) == 1
    harness.drain()
    assert len(harness.reads) == 1
    harness.cache.release(ticket)


def test_validated_length_restores_two_downloads_while_wanted_cached_segments_remain():
    harness = Harness(prefetch_session_bytes=16, prefetch_global_bytes=16)
    session = harness.session()
    resources = harness.resources(session, 5)
    harness.remember(session, resources)
    current = harness.cache.demand(session, resources[0])
    harness.run()  # Current response complete; first lookahead still in flight.
    harness.run()  # First wanted response cached; second lookahead admitted.
    harness.cache.release(current)
    # A wanted 4-byte cache entry plus the 8-byte speculative reservation leave
    # insufficient space for a second 8-byte reservation under the 16-byte cap.
    assert harness.cache.stats()["active"] == 1
    second = harness.cache._entries[(session.id, resources[2].id)]
    assert not second.future.done()
    assert second.lease.reserved_bytes == 8
    assert harness.cache.set_length(session, resources[2], second.lease, 4)
    assert harness.cache.stats()["active"] == 2
    assert harness.cache.stats()["bytes"] == 16
    assert harness.registry.stats()["reserved_bytes"] == 16
    assert not second.future.done(), "validated headers must never publish a partial response"
    assert len(harness.jobs) == 2
    harness.drain()
    assert second.future.result().body == b"data"
    assert harness.cache.stats()["active"] == 0
    harness.registry.close_all()


@pytest.mark.parametrize("length", [-1, 9, True, 1.5, None])
def test_invalid_announced_length_cannot_shrink_reservation_or_publish(length, harness):
    session = harness.session()
    resource = harness.resources(session, 1)[0]
    ticket = harness.cache.demand(session, resource)
    with pytest.raises(CacheUnavailable, match="online_cache_length"):
        harness.cache.set_length(session, resource, ticket._entry.lease, length)
    assert harness.cache.stats()["bytes"] == 8
    assert not ticket.future.done()
    harness.drain()
    harness.cache.release(ticket)


def test_announced_length_does_not_allow_oversized_body_or_late_worker_resizing(harness):
    session = harness.session()
    resource = harness.resources(session, 1)[0]
    ticket = harness.cache.demand(session, resource)
    assert not harness.cache.set_length(session, resource, object(), 2)
    assert harness.cache.stats()["bytes"] == 8
    assert harness.cache.set_length(session, resource, ticket._entry.lease, 2)
    harness.run()
    with pytest.raises(CacheUnavailable, match="online_cache_response"):
        ticket.future.result()
    assert harness.cache.stats()["bytes"] == 0
    retry = harness.cache.demand(session, resource)
    assert not harness.cache.set_length(session, resource, ticket._entry.lease, 0)
    assert harness.cache.stats()["bytes"] == 8
    harness.run()
    assert retry.future.result().body == b"data"
    harness.cache.release(ticket)
    harness.cache.release(retry)


def test_zero_length_can_release_cache_reservation_without_releasing_upstream_lease(harness):
    session = harness.session()
    resource = harness.resources(session, 1)[0]
    ticket = harness.cache.demand(session, resource)
    assert harness.cache.set_length(session, resource, ticket._entry.lease, 0)
    assert harness.cache.stats()["bytes"] == 0
    assert harness.registry.stats()["reserved_bytes"] == 8
    assert not ticket.future.done()
    harness.cache.release(ticket)
    harness.drain()


@pytest.mark.parametrize("category", ["request_cancelled", "request_timeout"])
def test_promotion_preemption_race_retries_same_resource_without_releasing_worker_early(harness, category):
    session = harness.session()
    resources = harness.resources(session, 3)
    harness.remember(session, resources)
    first = harness.cache.demand(session, resources[0])
    entry = harness.cache._entries[(session.id, resources[1].id)]
    promote = harness.registry.promote

    def preempt_then_promote(lease):
        lease.abort(category)
        promote(lease)

    harness.registry.promote = preempt_then_promote
    with pytest.raises(SessionError, match="^prefetch_busy$"):
        harness.cache.demand(session, resources[1])
    assert entry.obsolete and entry.waiters == 0 and not entry.future.done()
    assert harness.registry.stats()["active_requests"] == 2
    assert harness.registry.stats()["reserved_bytes"] == 16
    harness.run(1)  # The cancelled worker actually exits; only now release its slot.
    assert harness.registry.stats()["active_requests"] == 1
    harness.registry.promote = promote
    retry = harness.cache.demand(session, resources[1])
    assert retry.future is not entry.future
    harness.drain()
    assert retry.future.result().body == b"data"
    assert [read[1] for read in harness.reads].count(resources[1].id) == 1
    harness.cache.release(first)
    harness.cache.release(retry)


def test_session_revocation_during_promotion_is_not_hidden_as_prefetch_retry(harness):
    session = harness.session()
    resources = harness.resources(session, 3)
    harness.remember(session, resources)
    first = harness.cache.demand(session, resources[0])
    promote = harness.registry.promote

    def close_then_promote(lease):
        harness.registry.close(session.id, session.owner)
        promote(lease)

    harness.registry.promote = close_then_promote
    with pytest.raises(SessionError, match="^session_closed$"):
        harness.cache.demand(session, resources[1])
    assert session.cancelled.is_set()
    harness.cache.release(first)
    harness.drain()
    assert harness.registry.stats()["active_requests"] == 0
