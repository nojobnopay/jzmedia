"""Catalogue HTTP errors preserve the distinction between expiry and abort."""

import time

import pytest

from app import store
from app.network.guard import current_operation
from app.online import catalog, maccms
from test_online_catalog import environment as environment


@pytest.mark.parametrize("finish,status,code", [
    ("deadline", 504, "provider_timeout"),
    ("cancel", 410, "provider_cancelled"),
])
def test_catalogue_http_keeps_deadline_and_explicit_cancellation_distinct(
        environment, monkeypatch, finish, status, code):
    operations = []

    def interrupted_fetch(_source, _params):
        operation = current_operation()
        assert operation is not None
        operations.append(operation)
        if finish == "deadline":
            operation.deadline = time.monotonic() - 1
        else:
            operation.abort()
        operation.check()
        pytest.fail("an expired or aborted operation must not provide metadata")

    monkeypatch.setattr(maccms, "fetch", interrupted_fetch)
    response = environment.client.get("/api/online/catalog/detail", params={
        "source_id": environment.source["id"], "external_id": "movie-1",
    })

    assert response.status_code == status, response.text
    assert response.json() == {"detail": code, "code": code}
    assert response.headers["cache-control"] == "no-store"
    assert len(operations) == 1 and operations[0].cancelled.is_set()
    assert catalog.admission_stats() == {
        "active": 0, "owners": 0, "background": 0, "preempt_waiters": 0,
    }
    with store._base._conn() as connection:
        for table in ("online_items", "online_episodes"):
            assert connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
