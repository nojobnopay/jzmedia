"""Image connections retain only bounded, current-scope successful readers."""

from contextlib import ExitStack
from types import SimpleNamespace
import time

import pytest

from app.network.client import NetworkClient, NetworkRequestError
from app.network.guard import NetworkOperation, NetworkOperationError, operation_context
from app.online import api_pool, image_pool


SOURCE = {"id": "src_" + "a" * 32, "api_base_url": "https://api.example/vod",
          "allowed_hosts": ["images.example", "other.example"]}
URL = "https://images.example/poster.jpg?signature=isolated"
ACCESS = SimpleNamespace(owner="device", generation=1, protection_revision=1)
CONFIGURATION = "a" * 64


class Reader(NetworkClient):
    def __init__(self):
        self.closed = 0
        self.eligible = True
        self.matches = True
        self.snapshot = SimpleNamespace(purpose="image", fingerprint=CONFIGURATION)
        self._online_health_context = object()

    def close(self):
        assert image_pool.pool._lock.acquire(blocking=False), "No I/O or close holds the registry lock"
        image_pool.pool._lock.release()
        self.closed += 1

    def can_reuse(self, fresh, _url):
        assert not self.closed and fresh is not self
        return self.matches

    def reusable_for(self, _url):
        return self.eligible and not self.closed

    def accept_image_response(self, response):
        return response == "validated-closed-image"


@pytest.fixture
def environment(monkeypatch):
    clock = [0.0]
    pool = image_pool.ClientPool(clock=lambda: clock[0])
    monkeypatch.setattr(image_pool, "pool", pool)
    readers = []

    def factory(source_id, purpose):
        assert source_id.startswith("src_") and purpose == "image"
        reader = Reader()
        readers.append(reader)
        return reader

    yield SimpleNamespace(clock=clock, pool=pool, readers=readers, factory=factory)
    image_pool.shutdown()
    assert pool.stats() == {"idle": 0, "active": 0}


def borrow(environment, *, access=ACCESS, source=SOURCE, configuration=CONFIGURATION, url=URL, check=lambda: None):
    return image_pool.borrow(access, source, configuration, url, check, environment.factory)


def use(environment, **kwargs):
    with borrow(environment, **kwargs) as lease:
        lease.accept("validated-closed-image")
        return lease.client


def test_success_reuses_connection_and_rebinds_current_health_context(environment):
    api_before = api_pool.pool.stats()
    first = use(environment)
    second = use(environment)
    assert first is second and first.closed == 0
    assert environment.readers[-1].closed == 1
    assert first._online_health_context is environment.readers[-1]._online_health_context
    assert environment.pool.stats() == {"idle": 1, "active": 0}
    assert api_pool.pool.stats() == api_before, "Image leases never use API admission or connections"


@pytest.mark.parametrize("failure", ["not_accepted", "invalid_response", "exception", "non_reusable"])
def test_failed_or_unaccepted_image_never_returns_a_reader(environment, failure):
    first = use(environment)
    try:
        with borrow(environment) as lease:
            assert lease.client is first
            if failure == "exception":
                raise NetworkRequestError("network_timeout")
            if failure == "non_reusable":
                first.eligible = False
            if failure != "not_accepted":
                lease.accept("invalid" if failure == "invalid_response" else "validated-closed-image")
    except NetworkRequestError:
        assert failure == "exception"
    assert first.closed == 1 and environment.pool.stats() == {"idle": 0, "active": 0}


@pytest.mark.parametrize("partition", ["owner", "generation", "protection_revision", "source", "configuration", "origin", "scheme", "port"])
def test_viewing_and_configuration_and_exact_image_origin_partition_connections(environment, partition):
    first = use(environment)
    access, source, configuration, url = ACCESS, SOURCE, CONFIGURATION, URL
    if partition in {"owner", "generation", "protection_revision"}:
        values = vars(ACCESS).copy()
        values[partition] = "another-device" if partition == "owner" else 2
        access = SimpleNamespace(**values)
    elif partition == "source":
        source = {**SOURCE, "id": "src_" + "b" * 32}
    elif partition == "configuration":
        configuration = "b" * 64
    elif partition == "origin":
        url = "https://other.example/poster.jpg"
    elif partition == "scheme":
        url = "http://images.example/poster.jpg"
    else:
        url = "https://images.example:8443/poster.jpg"
    assert use(environment, access=access, source=source, configuration=configuration, url=url) is not first


def test_different_images_and_explicit_default_port_share_only_the_same_origin(environment):
    first = use(environment)
    assert use(environment, url="https://images.example:443/different.webp?signature=new") is first


def test_two_concurrent_readers_keep_two_distinct_idle_connections_for_one_origin(environment):
    with ExitStack() as stack:
        first = stack.enter_context(borrow(environment))
        second = stack.enter_context(borrow(environment))
        assert first.client is not second.client
        assert environment.pool.stats() == {"idle": 0, "active": 2}
        first.accept("validated-closed-image")
        second.accept("validated-closed-image")
    assert environment.pool.stats() == {"idle": 2, "active": 0}
    with ExitStack() as stack:
        reused = [stack.enter_context(borrow(environment)) for _ in range(2)]
        assert {id(value.client) for value in reused} == {id(first.client), id(second.client)}
        for lease in reused:
            lease.accept("validated-closed-image")
    assert environment.pool.stats() == {"idle": 2, "active": 0}


def test_pool_reservations_stay_bounded_and_unreserved_workers_are_never_retained(environment):
    # Actual image admission remains the artwork semaphore. A future incorrect
    # caller cannot increase the retained registry or its reservations.
    with ExitStack() as stack:
        leases = [stack.enter_context(borrow(environment)) for _ in range(3)]
        assert environment.pool.stats()["active"] == image_pool.MAX_ACTIVE
        for lease in leases:
            lease.accept("validated-closed-image")
    assert leases[2].client.closed == 1
    assert environment.pool.stats() == {"idle": 2, "active": 0}


def test_idle_expiry_absolute_lifetime_and_global_capacity_remain_bounded(environment):
    first = use(environment)
    for now in range(4, 29, 4):
        environment.clock[0] = now
        assert use(environment) is first
    environment.clock[0] = 30
    second = use(environment)
    assert second is not first and first.closed == 1
    environment.clock[0] = 35
    image_pool.sweep()
    assert second.closed == 1 and environment.pool.stats() == {"idle": 0, "active": 0}
    readers = [use(environment, configuration=f"{index:064x}") for index in range(image_pool.MAX_IDLE + 1)]
    assert environment.pool.stats() == {"idle": image_pool.MAX_IDLE, "active": 0}
    assert readers[0].closed == 1 and all(reader.closed == 0 for reader in readers[1:])


def test_shutdown_retires_but_never_closes_active_io_until_its_worker_exits(environment):
    with borrow(environment) as lease:
        image_pool.shutdown()
        assert lease.client.closed == 0
        lease.accept("validated-closed-image")
    assert lease.client.closed == 1 and environment.pool.stats() == {"idle": 0, "active": 0}
    image_pool.start()
    assert use(environment).closed == 0


def test_failed_authorization_or_dns_never_falls_back_to_an_old_socket(environment, monkeypatch):
    first = use(environment)

    def reject(*_args):
        raise NetworkRequestError("unsafe_target_address")

    monkeypatch.setattr(first, "can_reuse", reject)
    with pytest.raises(NetworkRequestError, match="unsafe_target_address"):
        use(environment)
    assert all(reader.closed == 1 for reader in environment.readers)
    assert environment.pool.stats() == {"idle": 0, "active": 0}
    calls = [0]

    def check():
        calls[0] += 1
        if calls[0] > 3:
            raise NetworkRequestError("online_source_changed")

    with pytest.raises(NetworkRequestError, match="online_source_changed"):
        use(environment, check=check)
    assert environment.pool.stats() == {"idle": 0, "active": 0}


def test_cancellation_after_validation_cannot_return_the_reader(environment):
    operation = NetworkOperation(time.monotonic() + 5)
    try:
        with operation_context(operation), pytest.raises(NetworkOperationError):
            with borrow(environment) as lease:
                lease.accept("validated-closed-image")
                operation.abort()
    finally:
        operation.close()
    assert environment.pool.stats() == {"idle": 0, "active": 0}
    assert environment.readers[0].closed == 1


def test_one_partition_failure_retires_its_other_idle_connection(environment):
    with borrow(environment) as first:
        with borrow(environment) as second:
            first.accept("validated-closed-image")
            second.accept("validated-closed-image")
    assert environment.pool.stats()["idle"] == 2
    with pytest.raises(ValueError):
        with borrow(environment):
            raise ValueError("isolated image validation failure")
    assert environment.pool.stats() == {"idle": 0, "active": 0}
    assert all(reader.closed == 1 for reader in environment.readers)


def test_non_network_reader_keeps_its_original_context_and_final_guard(environment):
    class Minimal:
        closed = False

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.closed = True

    reader = Minimal()
    with image_pool.borrow(object(), SOURCE, CONFIGURATION, URL, lambda: None, lambda *_args: reader) as lease:
        assert lease.client is reader
        lease.accept(object())
    assert reader.closed and lease.finished
    assert environment.pool.stats() == {"idle": 0, "active": 0}
