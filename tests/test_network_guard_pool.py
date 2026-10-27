"""Real keep-alive reuse outlives a response's cancelled downstream owner."""

import time

import pytest

from app.network.client import NetworkRequestError
from app.network.guard import NetworkOperation
from app.network.policy import resolve_online_policy
from test_network_transport import bind, create_client, profile


pytest_plugins = ["test_network_transport"]


class Attachments:
    def __init__(self):
        self.active, self.seen = [], []

    def attach(self, callback):
        self.active.append(callback)
        self.seen.append(callback)

        def detach():
            self.active.remove(callback)

        return detach


def test_completed_response_socket_can_be_reused_before_its_old_operation_finishes(source, origin):
    configured = profile(max_connections=1, connect_timeout_seconds=2, read_timeout_seconds=2)
    bind(source, "media", configured)
    previous_callbacks, current_callbacks = Attachments(), Attachments()
    previous = NetworkOperation(time.monotonic() + 5, attach=previous_callbacks.attach)
    current = NetworkOperation(time.monotonic() + 5, attach=current_callbacks.attach)
    try:
        with create_client(resolve_online_policy(source, "media")) as client:
            # The old operation deliberately stays open to model HLS parsing
            # or slow downstream delivery after HTTPX has consumed its body.
            with pytest.raises(NetworkRequestError, match="^operation_cancelled$"):
                with client.guarded_stream("GET", origin["url"] + "/complete", previous) as complete:
                    first_socket = complete.extensions["network_stream"].get_extra_info("socket")
                    assert b"".join(complete.iter_raw()) == b"isolated upstream"
                    assert complete.is_closed and previous_callbacks.active == []
                    assert len(previous_callbacks.seen) == 1
                    queued_old_callback = previous_callbacks.seen[0]
                    assert not previous._closed
                    with pytest.raises(NetworkRequestError, match="^operation_cancelled$"):
                        with client.guarded_stream("GET", origin["url"] + "/stream", current) as response:
                            reused_socket = response.extensions["network_stream"].get_extra_info("socket")
                            # Same actual socket and a one-connection pool rule
                            # out a fresh connection masking the cancellation bug.
                            assert reused_socket is first_socket
                            assert len(current_callbacks.active) == 1
                            previous.abort()
                            queued_old_callback()
                            assert reused_socket.fileno() >= 0
                            body = response.iter_raw()
                            assert next(body) == b"0"
                            current.abort()
                            next(body)
            assert previous_callbacks.active == current_callbacks.active == []
    finally:
        origin["release"].set()
        previous.close()
        current.close()
    assert [row["path"] for row in origin["records"]] == ["/complete", "/stream"]


def test_redirect_release_invalidates_its_old_callback_before_same_operation_reuses_socket(source, origin):
    configured = profile(max_connections=1, connect_timeout_seconds=2, read_timeout_seconds=2)
    bind(source, "api", configured)
    callbacks = Attachments()
    operation = NetworkOperation(time.monotonic() + 5, attach=callbacks.attach)
    with create_client(resolve_online_policy(source, "api")) as client:
        with client.guarded_stream("GET", origin["url"] + "/redirect", operation) as response:
            # Both hops use the same origin/pool. The old closure must check
            # its own registration rather than just the socket dictionary key.
            assert len(callbacks.seen) == 2 and len(callbacks.active) == 1
            callbacks.seen[0]()
            assert b"".join(response.iter_raw()) == b"isolated upstream"
            assert callbacks.active == []
    assert [row["path"] for row in origin["records"]] == ["/redirect", "/followed"]
