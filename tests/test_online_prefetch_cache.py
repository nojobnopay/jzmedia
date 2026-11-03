"""Deterministic download scheduling with isolated sessions and no network I/O."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.network.targets import TargetPolicy
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
    def __init__(self, **overrides):
        defaults = dict(max_segment_response_bytes=8, prefetch_session_bytes=64,
                        prefetch_global_bytes=128, max_session_bytes=4096,
                        max_reserved_bytes=1024, max_session_active=8)
        self.registry = SessionRegistry(replace(SessionLimits(), **(defaults | overrides)))
        self.jobs, self.reads, self.failures = [], [], set()
        self.cache = self.registry.cached_media(lambda: CacheStore(self.registry, self.fetch, self.jobs.append))

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
