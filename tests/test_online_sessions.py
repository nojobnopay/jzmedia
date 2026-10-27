"""Opaque session isolation and bounded accounting without sockets or media I/O."""

from dataclasses import replace
import threading
from types import SimpleNamespace

import pytest

from app.network.targets import TargetPolicy, TargetPolicyError
from app.online.models import OnlinePlaybackIdentity
from app.online.sessions import CHUNK_BYTES, SessionError, SessionLimits, SessionRegistry


SOURCE = "src_" + "a" * 32
OTHER_SOURCE = "src_" + "b" * 32
OWNER = "owner-secret-a"
OTHER_OWNER = "owner-secret-b"
ENTRY = "https://media.example/start?token=upstream-secret"


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class Client:
    def __init__(self, source=SOURCE, targets=None):
        self.snapshot = SimpleNamespace(source_id=source)
        self.targets = targets or TargetPolicy(source, ["media.example"], resolver=lambda _h, _p: ("8.8.8.8",))
        self.closes = 0
        self.on_close = None

    def close(self):
        self.closes += 1
        if self.on_close:
            self.on_close()


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def limits():
    return SessionLimits(ttl_seconds=20, idle_seconds=10, request_seconds=5,
                         max_session_bytes=50, max_reserved_bytes=40,
                         max_mp4_response_bytes=20, max_segment_response_bytes=30,
                         max_manifest_bytes=16, max_subtitle_bytes=16,
                         max_resources=4, max_global_resources=8, max_resource_url_bytes=2048)


@pytest.fixture
def registry(clock, limits):
    result = SessionRegistry(limits, clock=clock, wall_clock=lambda: 1700000000)
    yield result
    result.close_all()


def identity(source=SOURCE):
    return OnlinePlaybackIdentity(source_id=source, item_id="itm_" + "c" * 32, kind="movie")


def create(registry, *, owner=OWNER, source=SOURCE, client=None, **kwargs):
    return registry.create(identity(source), owner, client or Client(source), ENTRY, "mp4", **kwargs)


def test_descriptor_and_reprs_never_expose_upstream_or_owner(registry):
    context = {"private_access_data": "access-secret"}
    session = create(registry, duration_seconds=120.0, context=context)
    descriptor = session.descriptor.model_dump()
    assert descriptor["media_url"] == f"/api/online/sessions/{session.id}/media"
    assert descriptor["identity"]["source_id"] == SOURCE
    assert (descriptor["transport"], descriptor["processing"], descriptor["time_offset_seconds"]) == ("mp4", "passthrough", 0)
    assert descriptor["expires_at"] == 1700000020
    assert session.context is context
    assert session.entry.path(session.id).startswith(f"/api/online/sessions/{session.id}/resources/")
    for public in (repr(session), repr(session.entry), str(descriptor), str(registry.stats())):
        assert not any(secret in public for secret in ("upstream-secret", "owner-secret", "access-secret", "media.example"))
    assert registry.sessions() == (session,)


@pytest.mark.parametrize("change", [{"transport": "file"}, {"owner": ""}, {"owner": "é"},
                                      {"duration_seconds": float("inf")}, {"seekable": "yes"}])
def test_invalid_creation_closes_its_unpublished_client(registry, change):
    client = Client()
    options = {"identity": identity(), "owner": OWNER, "client": client, "entry_url": ENTRY,
               "transport": "mp4", **change}
    with pytest.raises(SessionError, match="^invalid_session_configuration$"):
        registry.create(**options)
    assert client.closes == 1
    assert registry.stats()["pending_sessions"] == registry.stats()["sessions"] == 0


def test_mismatched_source_and_rejected_target_close_before_publication(registry):
    mismatched = Client(OTHER_SOURCE)
    with pytest.raises(SessionError, match="^invalid_session_configuration$"):
        create(registry, client=mismatched)
    assert mismatched.closes == 1

    def reject(*_args, **_kwargs):
        raise TargetPolicyError("non_public_target_address")

    rejected = Client(targets=SimpleNamespace(source_id=SOURCE, resolve=reject))
    with pytest.raises(SessionError, match="^non_public_target_address$"):
        create(registry, client=rejected)
    assert rejected.closes == 1
    assert registry.stats()["pending_sessions"] == registry.stats()["resources"] == 0


@pytest.mark.parametrize("field", ["max_sessions", "max_owner_sessions", "max_source_sessions"])
def test_session_caps_close_rejected_client_without_an_extra_target_lookup(clock, limits, field):
    registry = SessionRegistry(replace(limits, **{field: 1}), clock=clock)
    try:
        create(registry)

        def unexpected(*_args, **_kwargs):
            pytest.fail("session admission must happen before target resolution")

        rejected = Client(targets=SimpleNamespace(source_id=SOURCE, resolve=unexpected))
        with pytest.raises(SessionError, match="^session_limit$"):
            create(registry, client=rejected)
        assert rejected.closes == 1
        assert registry.stats()["sessions"] == 1
    finally:
        registry.close_all()


def test_owners_cannot_read_or_close_each_others_sessions_or_resources(registry):
    first = create(registry)
    second = create(registry, owner=OTHER_OWNER)
    assert first.id != second.id and first.entry.id != second.entry.id
    for operation in (registry.get, registry.close):
        with pytest.raises(SessionError, match="^session_not_found$"):
            operation(first.id, OTHER_OWNER)
        with pytest.raises(SessionError, match="^session_not_found$"):
            operation("missing", OWNER)
    with pytest.raises(SessionError, match="^resource_not_found$"):
        registry.resource(second, first.entry.id)
    registry.close(first.id, OWNER)
    assert registry.get(second.id, OTHER_OWNER) is second
    assert first.cancelled.is_set() and not second.cancelled.is_set()
    assert first.client.closes == 1 and second.client.closes == 0


def test_resources_deduplicate_only_within_the_same_kind_and_range(registry):
    session = create(registry)
    first = registry.register(session, "segment.bin", "segment", base_url=ENTRY, byte_range=(0, 9))
    repeated = registry.register(session, "segment.bin", "segment", base_url=ENTRY, byte_range=(0, 9))
    next_range = registry.register(session, "segment.bin", "segment", base_url=ENTRY, byte_range=(10, 19))
    initialization = registry.register(session, "segment.bin", "map", base_url=ENTRY, byte_range=(0, 9))
    assert repeated is first
    assert len({first.id, next_range.id, initialization.id}) == 3
    assert registry.resource(session, first.id).byte_range == (0, 9)
    assert registry.stats()["resources"] == 4
    with pytest.raises(SessionError, match="^resource_limit$"):
        registry.register(session, "fifth.ts", "segment", base_url=ENTRY)
    assert registry.stats()["resources"] == 4


@pytest.mark.parametrize("kind,byte_range", [("unknown", None), ("manifest", (0, 1)),
                                           ("segment", (-1, 1)), ("segment", (2, 1)),
                                           ("map", (False, 2)), ("segment", (0, 2**63)),
                                           ("segment", [0, 1])])
def test_invalid_resource_configuration_does_not_register(registry, kind, byte_range):
    session = create(registry)
    with pytest.raises(SessionError, match="^invalid_resource_configuration$"):
        registry.register(session, "https://media.example/new", kind, byte_range=byte_range)
    assert registry.stats()["resources"] == 1


def test_global_resource_and_url_storage_are_bounded_and_returned_on_close(clock, limits):
    registry = SessionRegistry(replace(limits, max_global_resources=2), clock=clock)
    first = create(registry)
    second = create(registry, owner=OTHER_OWNER)
    with pytest.raises(SessionError, match="^resource_limit$"):
        registry.register(first, "https://media.example/next", "segment")
    registry.close(first.id, OWNER)
    registry.register(second, "https://media.example/next", "segment")
    registry.close_all()
    assert registry.stats()["resources"] == registry.stats()["resource_url_bytes"] == 0

    tiny = SessionRegistry(replace(limits, max_resource_url_bytes=1), clock=clock)
    rejected = Client()
    with pytest.raises(SessionError, match="^resource_limit$"):
        create(tiny, client=rejected)
    assert rejected.closes == 1
    assert tiny.stats()["sessions"] == tiny.stats()["pending_sessions"] == 0


@pytest.mark.parametrize("field", ["max_active", "max_source_active", "max_session_active"])
def test_active_request_caps_do_not_queue_or_lose_the_original_slot(clock, limits, field):
    registry = SessionRegistry(replace(limits, **{field: 1}), clock=clock)
    first = create(registry)
    second = first if field == "max_session_active" else create(registry, owner=OTHER_OWNER)
    try:
        lease = registry.acquire(first, 10)
        with pytest.raises(SessionError, match="^request_limit$"):
            registry.acquire(second, 10)
        assert registry.stats()["active_requests"] == 1
        lease.close()
        with registry.acquire(second, 10):
            assert registry.stats()["active_requests"] == 1
        assert registry.stats()["active_requests"] == 0
    finally:
        registry.close_all()


def test_concurrent_reservations_are_atomic_and_actual_bytes_stay_charged(registry):
    session = create(registry)
    ready = threading.Barrier(3)
    admitted, rejected = [], []

    def reserve():
        ready.wait(timeout=2)
        try:
            admitted.append(registry.acquire(session, 30))
        except SessionError as error:
            rejected.append(str(error))

    workers = [threading.Thread(target=reserve) for _index in range(2)]
    for worker in workers:
        worker.start()
    ready.wait(timeout=2)
    for worker in workers:
        worker.join(timeout=2)
        assert not worker.is_alive()
    assert len(admitted) == 1 and rejected == ["byte_budget_exhausted"]
    lease = admitted[0]
    lease.consume(17)
    assert session.bytes_consumed == registry.stats()["consumed_bytes"] == 17
    assert session.bytes_reserved == registry.stats()["reserved_bytes"] == 13
    lease.close()
    lease.close()
    assert registry.stats()["reserved_bytes"] == 0
    with registry.acquire(session, 30) as following:
        following.consume(20)
    with pytest.raises(SessionError, match="^byte_budget_exhausted$"):
        registry.acquire(session, 20)
    assert session.bytes_consumed == 37


def test_response_cap_is_checked_before_reservation_and_overread_is_rejected(registry):
    session = create(registry)
    for value in (-1, True, 31):
        with pytest.raises(SessionError, match="^response_byte_limit$"):
            registry.acquire(session, value)
    with pytest.raises(SessionError, match="^response_byte_limit$"):
        registry.acquire(session, 21, kind="mp4")
    with registry.acquire(session, 10) as lease:
        with pytest.raises(SessionError, match="^response_byte_limit$"):
            lease.consume(11)
        with pytest.raises(SessionError, match="^invalid_byte_count$"):
            lease.consume(-1)
        lease.consume(10)
    assert session.bytes_consumed == 10 and registry.stats()["reserved_bytes"] == 0


def test_unknown_length_manifest_can_reserve_and_charge_only_one_extra_chunk(clock, limits):
    reserve = limits.max_manifest_bytes + CHUNK_BYTES
    registry = SessionRegistry(replace(limits, max_session_bytes=reserve, max_reserved_bytes=reserve), clock=clock)
    session = create(registry)
    with registry.acquire(session, reserve, kind="manifest") as lease:
        lease.consume(limits.max_manifest_bytes)
        lease.consume(CHUNK_BYTES)
    assert session.bytes_consumed == registry.stats()["consumed_bytes"] == reserve
    assert registry.stats()["reserved_bytes"] == 0
    with pytest.raises(SessionError, match="^response_byte_limit$"):
        registry.acquire(session, reserve + 1, kind="manifest")
    with pytest.raises(SessionError, match="^response_byte_limit$"):
        registry.acquire(session, limits.max_mp4_response_bytes + 1, kind="mp4")
    registry.close_all()


def test_abort_interrupts_once_but_keeps_worker_slot_until_close(registry):
    session = create(registry)
    lease = registry.acquire(session, 10)
    calls = []
    detach = lease.attach(lambda: calls.append("detached"))
    detach()
    detach()
    lease.attach(lambda: calls.append("active"))
    lease.abort()
    lease.abort()
    assert calls == ["active"] and lease.cancelled.is_set()
    assert registry.stats()["active_requests"] == 1
    assert registry.stats()["reserved_bytes"] == 10
    lease.attach(lambda: calls.append("late"))()
    assert calls == ["active", "late"]
    # These are actual upstream bytes which raced with cancellation, so they
    # remain charged even though the response may no longer deliver them.
    with pytest.raises(SessionError, match="^request_cancelled$"):
        lease.consume(3)
    lease.close()
    assert session.bytes_consumed == 3
    assert registry.stats()["active_requests"] == registry.stats()["reserved_bytes"] == 0


def test_normal_completion_forgets_interrupt_hooks_without_aborting_keepalive(registry):
    session = create(registry)
    calls = []
    with registry.acquire(session, 10) as lease:
        lease.attach(lambda: calls.append("must not run"))
        lease.consume(2)
    assert calls == [] and not lease.cancelled.is_set()
    assert session.client.closes == 0
    lease.abort()
    assert calls == []


def test_deadline_cancels_socket_without_releasing_a_still_running_worker(clock, limits):
    registry = SessionRegistry(replace(limits, max_active=1), clock=clock)
    session = create(registry)
    lease = registry.acquire(session, 10)
    calls = []
    lease.attach(lambda: calls.append("shutdown"))
    clock.now += 5
    registry.sweep()
    assert calls == ["shutdown"] and lease.cancelled.is_set()
    with pytest.raises(SessionError, match="^request_timeout$"):
        lease.check()
    with pytest.raises(SessionError, match="^request_limit$"):
        registry.acquire(session, 10)
    lease.close()
    with registry.acquire(session, 10):
        pass
    registry.close_all()


@pytest.mark.parametrize("hard_ttl", [False, True])
def test_expiry_reclaims_sessions_and_clients_but_holds_active_slots_until_worker_exits(registry, clock, hard_ttl):
    session = create(registry)
    lease = registry.acquire(session, 10)
    if hard_ttl:
        clock.now += 9
        registry.get(session.id, OWNER)
        clock.now += 9
        registry.get(session.id, OWNER)
        clock.now += 2
    else:
        clock.now += 10
    registry.sweep()
    registry.sweep()
    assert session.cancelled.is_set() and lease.cancelled.is_set()
    assert session.client.closes == 1
    assert registry.stats()["sessions"] == registry.stats()["resources"] == 0
    assert registry.stats()["active_requests"] == 1
    with pytest.raises(SessionError, match="^session_not_found$"):
        registry.get(session.id, OWNER)
    lease.close()
    assert registry.stats()["active_requests"] == registry.stats()["reserved_bytes"] == 0


def test_source_and_owner_revocation_are_scoped_and_cleanup_runs_outside_lock(registry):
    first = create(registry)
    second = create(registry, source=OTHER_SOURCE)
    third = create(registry, source=OTHER_SOURCE, owner=OTHER_OWNER)
    first.client.on_close = registry.stats
    registry.close_owner(OWNER)
    assert first.cancelled.is_set() and second.cancelled.is_set() and not third.cancelled.is_set()
    assert registry.sessions() == (third,)
    registry.revoke_source(OTHER_SOURCE)
    assert third.cancelled.is_set()
    assert all(session.client.closes == 1 for session in (first, second, third))


@pytest.mark.parametrize("revoke", ["source", "owner", "all"])
def test_pending_dns_creation_is_admitted_before_lookup_and_can_be_revoked(clock, limits, revoke):
    registry = SessionRegistry(replace(limits, max_sessions=1), clock=clock)
    entered, release = threading.Event(), threading.Event()
    outcomes = []

    def resolve(url, **_kwargs):
        entered.set()
        assert release.wait(timeout=3)
        return SimpleNamespace(url=url)

    client = Client(targets=SimpleNamespace(source_id=SOURCE, resolve=resolve))

    def pending():
        try:
            outcomes.append(create(registry, client=client))
        except SessionError as error:
            outcomes.append(str(error))

    worker = threading.Thread(target=pending)
    worker.start()
    try:
        assert entered.wait(timeout=2)
        assert registry.stats()["pending_sessions"] == 1
        rejected = Client()
        with pytest.raises(SessionError, match="^session_limit$"):
            create(registry, owner=OTHER_OWNER, client=rejected)
        assert rejected.closes == 1
        {"source": lambda: registry.revoke_source(SOURCE), "owner": lambda: registry.close_owner(OWNER),
         "all": registry.close_all}[revoke]()
    finally:
        release.set()
        worker.join(timeout=3)
        assert not worker.is_alive()
    assert outcomes == ["session_closed"]
    assert client.closes == 1
    assert registry.stats()["sessions"] == registry.stats()["pending_sessions"] == 0
    assert len(registry._cancelled_pending) == 0


def test_cleanup_continues_after_a_broken_interrupt_callback(registry):
    session = create(registry)
    lease = registry.acquire(session, 10)
    calls = []

    def broken():
        raise RuntimeError("private upstream material")

    lease.attach(broken)
    lease.attach(lambda: calls.append("continued"))
    registry.close_all()
    assert calls == ["continued"] and session.client.closes == 1
    assert registry.stats()["active_requests"] == 1
    lease.close()
    assert registry.stats()["reserved_bytes"] == 0


@pytest.mark.parametrize("field,value", [("ttl_seconds", float("inf")), ("idle_seconds", 0),
                                       ("request_seconds", True), ("max_sessions", -1),
                                       ("max_reserved_bytes", 1.5), ("max_resources", False)])
def test_limits_require_finite_positive_values(field, value):
    with pytest.raises(SessionError, match="^invalid_session_limits$"):
        SessionLimits(**{field: value})
