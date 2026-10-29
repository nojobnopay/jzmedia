"""Bounded lease ownership, foreground partitioning and shutdown behavior."""

from types import SimpleNamespace

import pytest

from app.network.client import NetworkClient, NetworkRequestError
from app.network.guard import NetworkOperation, NetworkOperationError, operation_context
from app.online import api_pool


SOURCE = {"id": "src_" + "a" * 32, "api_base_url": "https://api.example/vod", "provider": "maccms"}
ACCESS = SimpleNamespace(owner="device", generation=1, protection_revision=1)
CONFIGURATION = "a" * 64


class Reader(NetworkClient):
    def __init__(self):
        self.closed = 0
        self.eligible = True
        self.matches = True
        self.snapshot = SimpleNamespace(purpose="api", fingerprint=CONFIGURATION)
        self._online_health_context = object()

    def close(self):
        assert api_pool.pool._lock.acquire(blocking=False), "Client close must not hold the pool lock"
        api_pool.pool._lock.release()
        self.closed += 1

    def can_reuse(self, fresh, _url):
        assert not self.closed and fresh is not self
        return self.matches

    def reusable_for(self, _url):
        return self.eligible and not self.closed


@pytest.fixture
def environment(monkeypatch):
    clock = [0.0]
    pool = api_pool.ClientPool(clock=lambda: clock[0])
    monkeypatch.setattr(api_pool, "pool", pool)
    readers = []

    def factory(*_args):
        reader = Reader()
        readers.append(reader)
        return reader

    yield SimpleNamespace(clock=clock, pool=pool, readers=readers, factory=factory)
    api_pool.shutdown()
    assert pool.stats() == {"idle": 0, "active": 0}
    assert api_pool._current.get() is None


def use(environment, *, access=ACCESS, source=SOURCE, configuration=CONFIGURATION, fresh=False, check=lambda: None):
    with api_pool.scope(access, source, configuration, check):
        with api_pool.borrow(source, environment.factory, fresh=fresh) as lease:
            lease.accept()
            return lease.client


def test_success_transfers_ownership_and_rebinds_current_health_context(environment):
    first = use(environment)
    assert first.closed == 0 and environment.pool.stats() == {"idle": 1, "active": 0}
    second = use(environment)
    candidate = environment.readers[-1]
    assert second is first and first.closed == 0
    assert candidate.closed == 1
    assert second._online_health_context is candidate._online_health_context
    assert environment.pool.stats() == {"idle": 1, "active": 0}


@pytest.mark.parametrize("failure", ["not_accepted", "exception", "non_reusable"])
def test_only_complete_accepted_success_can_return_a_client(environment, failure):
    first = use(environment)
    with api_pool.scope(ACCESS, SOURCE, CONFIGURATION, lambda: None):
        try:
            with api_pool.borrow(SOURCE, environment.factory) as lease:
                assert lease.client is first
                if failure == "exception":
                    raise NetworkRequestError("network_timeout")
                if failure == "non_reusable":
                    first.eligible = False
                    lease.accept()
        except NetworkRequestError:
            assert failure == "exception"
    assert first.closed == 1
    assert environment.pool.stats() == {"idle": 0, "active": 0}


def test_forced_retry_closes_old_idle_reader_and_uses_a_new_one(environment):
    first = use(environment)
    second = use(environment, fresh=True)
    assert second is not first and first.closed == 1 and second.closed == 0


@pytest.mark.parametrize("partition", ["owner", "generation", "protection_revision", "source", "configuration"])
def test_viewing_source_and_complete_configuration_partitions_never_share(environment, partition):
    first = use(environment)
    access, source, configuration = ACCESS, SOURCE, CONFIGURATION
    if partition in {"owner", "generation", "protection_revision"}:
        values = vars(ACCESS).copy()
        values[partition] = "other-device" if partition == "owner" else 2
        access = SimpleNamespace(**values)
    elif partition == "source":
        source = {**SOURCE, "api_base_url": "https://api.example/new-api"}
    else:
        configuration = "b" * 64
    second = use(environment, access=access, source=source, configuration=configuration)
    assert second is not first


def test_idle_expiry_and_absolute_lifetime_are_not_extended_by_reuse(environment):
    first = use(environment)
    for value in range(4, 29, 4):
        environment.clock[0] = value
        assert use(environment) is first
    environment.clock[0] = 30
    second = use(environment)
    assert second is not first and first.closed == 1
    environment.clock[0] = 35
    api_pool.sweep()
    assert second.closed == 1 and environment.pool.stats()["idle"] == 0


def test_idle_capacity_evicts_oldest_without_closing_an_active_reader(environment):
    readers = []
    for index in range(api_pool.MAX_IDLE + 1):
        readers.append(use(environment, configuration=f"{index:064x}"))
    assert environment.pool.stats() == {"idle": api_pool.MAX_IDLE, "active": 0}
    assert readers[0].closed == 1 and all(reader.closed == 0 for reader in readers[1:])
    with api_pool.scope(ACCESS, SOURCE, "1" * 64, lambda: None):
        with api_pool.borrow(SOURCE, environment.factory) as lease:
            api_pool.shutdown()
            assert lease.client.closed == 0, "Shutdown retires a worker without closing its active I/O"
            lease.accept()
        assert lease.client.closed == 1
    assert environment.pool.stats() == {"idle": 0, "active": 0}
    api_pool.start()
    assert use(environment).closed == 0


def test_final_scope_guard_rejects_a_previously_returned_success(environment):
    current = [True]

    def check():
        if not current[0]:
            raise NetworkRequestError("online_source_changed")

    with pytest.raises(NetworkRequestError, match="online_source_changed"):
        with api_pool.scope(ACCESS, SOURCE, CONFIGURATION, check):
            with api_pool.borrow(SOURCE, environment.factory) as lease:
                lease.accept()
            current[0] = False
    assert all(reader.closed == 1 for reader in environment.readers)
    assert environment.pool.stats() == {"idle": 0, "active": 0}


def test_current_cancellation_retires_the_reader_and_reservation(environment):
    import time

    operation = NetworkOperation(time.monotonic() + 10)
    with operation_context(operation), pytest.raises(NetworkOperationError):
        with api_pool.scope(ACCESS, SOURCE, CONFIGURATION, lambda: None):
            with api_pool.borrow(SOURCE, environment.factory) as lease:
                lease.accept()
                operation.abort()
    operation.close()
    assert environment.pool.stats() == {"idle": 0, "active": 0}
    assert environment.readers[0].closed == 1


def test_dns_preflight_failure_cannot_fall_back_to_the_old_socket(environment, monkeypatch):
    first = use(environment)

    def reject(*_args):
        raise NetworkRequestError("unsafe_target_address")

    monkeypatch.setattr(first, "can_reuse", reject)
    with pytest.raises(NetworkRequestError, match="unsafe_target_address"):
        use(environment)
    assert all(reader.closed == 1 for reader in environment.readers)
    assert environment.pool.stats() == {"idle": 0, "active": 0}
