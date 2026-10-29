"""Stage observations preserve I/O semantics and contain no network secrets."""

from contextlib import contextmanager
import json
import logging
import socket
import threading
import time
from types import SimpleNamespace

import pytest

from app.network import stages
from app.network.client import NetworkRequestError
from app.network.policy import resolve_online_policy
from test_network_transport import (LoopbackPolicy, bind, certificate as certificate,
                                    create_client, logical_url, origin as origin,
                                    profile, running_server, source as source)


POLICY = "a" * 64


def snapshot(purpose="api", fingerprint=POLICY):
    return SimpleNamespace(purpose=purpose, fingerprint=fingerprint)


def records(caplog):
    return [json.loads(record.getMessage().split("network stages ", 1)[1])
            for record in caplog.records if record.name == "jzmedia.network.stages"]


def test_nested_timings_keep_the_innermost_tls_failure_without_double_counting(monkeypatch, caplog):
    clock = [0.0]
    observation = stages.StageObservation("api", POLICY, clock=lambda: clock[0])
    monkeypatch.setattr(stages, "StageObservation", lambda *_args: observation)
    original = TimeoutError("https://private.example/?signature=secret")
    with caplog.at_level(logging.INFO, logger="jzmedia.network.stages"):
        with pytest.raises(TimeoutError) as caught:
            with stages.observe_request(snapshot()):
                clock[0] = .01
                with stages.stage("response_headers"):
                    clock[0] = .02
                    with stages.stage("tcp"):
                        clock[0] = .03
                    clock[0] = .04
                    with stages.stage("tls"):
                        clock[0] = .041
                        with stages.stage("tls"):
                            clock[0] = .05
                            raise original
    assert caught.value is original
    result, = records(caplog)
    assert result["outcome"] == "timeout" and result["failure_stage"] == "tls"
    assert result["stages"]["tls"] == {"count": 1, "failures": 1, "ms": 10.0}
    assert result["stages"]["tcp"]["ms"] == 10.0
    assert result["stages"]["response_headers"]["ms"] == 40.0
    assert result["elapsed_ms"] == 50.0
    assert "private" not in caplog.text and "secret" not in caplog.text
    assert stages._current.get() is None


def test_recovered_tcp_attempt_does_not_replace_a_later_tls_failure(caplog):
    with caplog.at_level(logging.INFO, logger="jzmedia.network.stages"):
        with pytest.raises(NetworkRequestError):
            with stages.observe_request(snapshot()):
                with stages.stage("response_headers"):
                    try:
                        with stages.stage("tcp"):
                            raise TimeoutError("private-address")
                    except TimeoutError:
                        pass
                    with stages.stage("tcp"):
                        pass
                    try:
                        with stages.stage("tls"):
                            raise TimeoutError("private-certificate")
                    except TimeoutError:
                        raise NetworkRequestError("network_timeout") from None
    result, = records(caplog)
    assert result["failure_stage"] == "tls"
    assert result["stages"]["tcp"]["count"] == 2
    assert result["stages"]["tcp"]["failures"] == 1
    assert "private" not in caplog.text


def test_observation_is_fixed_size_and_status_never_contains_arbitrary_text(caplog):
    with caplog.at_level(logging.INFO, logger="jzmedia.network.stages"):
        with stages.observe_request(snapshot(fingerprint="https://private/?secret=password")):
            for _index in range(1100):
                with stages.stage("tcp"):
                    pass
            with stages.stage("unknown-private-stage"):
                pass
            stages.response_status("private-status")
            stages.response_status(503)
    result, = records(caplog)
    assert set(result["stages"]) == set(stages.STAGES)
    assert result["stages"]["tcp"]["count"] == 1024
    assert result["policy"] == "unavailable"
    assert result["status"] == 503 and result["outcome"] == "http_error"
    assert len(json.dumps(result)) < 1500
    assert "private" not in caplog.text and "password" not in caplog.text


def test_nested_request_tokens_restore_and_media_success_does_not_add_info_logs(caplog):
    with caplog.at_level(logging.INFO, logger="jzmedia.network.stages"):
        with stages.observe_request(snapshot("media")):
            parent = stages._current.get()
            with stages.observe_request(snapshot("api")):
                assert stages._current.get() is not parent
                stages.response_status(200)
            assert stages._current.get() is parent
    result, = records(caplog)
    assert result["purpose"] == "api" and result["outcome"] == "ok"
    assert stages._current.get() is None


def test_clock_or_logging_failures_never_replace_success_or_original_exception(monkeypatch):
    def unavailable(*_args, **_kwargs):
        raise RuntimeError("private-observer-failure")

    observation = stages.StageObservation("api", POLICY)
    monkeypatch.setattr(observation, "begin", unavailable)
    monkeypatch.setattr(stages, "StageObservation", lambda *_args: observation)
    monkeypatch.setattr(stages._logger, "log", unavailable)
    with stages.observe_request(snapshot()):
        with stages.stage("dns"):
            assert 2 + 2 == 4
    original = NetworkRequestError("network_timeout")
    with pytest.raises(NetworkRequestError) as caught:
        with stages.observe_request(snapshot()):
            with stages.stage("tls"):
                raise original
    assert caught.value is original
    assert stages._current.get() is None


def test_failed_nested_observer_does_not_measure_against_the_parent(monkeypatch):
    with stages.observe_request(snapshot()):
        parent = stages._current.get()
        monkeypatch.setattr(stages, "StageObservation", lambda *_args: 1 / 0)
        with stages.observe_request(snapshot()):
            assert stages._current.get() is None
            with stages.stage("dns"):
                pass
        assert stages._current.get() is parent
        assert parent._values["dns"]["count"] == 0
    assert stages._current.get() is None


def test_real_tls_requests_report_stages_and_reused_connection_without_targets(source, origin, certificate, caplog):
    bind(source, "api", profile(ca_file=certificate["path"]))
    with caplog.at_level(logging.INFO, logger="jzmedia.network.stages"):
        with running_server(origin["handler"], certificate["context"]) as url:
            targets = LoopbackPolicy(source, ["fixture.test"], resolver=lambda _host, _port: ("127.0.0.1",))
            with create_client(resolve_online_policy(source, "api"), targets=targets) as client:
                assert client.request("GET", logical_url(url, "fixture.test") + "/first?signature=private-secret").status_code == 200
                assert client.request("GET", logical_url(url, "fixture.test") + "/second?signature=private-secret").status_code == 200
    first, second = records(caplog)
    assert first["stages"]["tcp"]["count"] == first["stages"]["tls"]["count"] == 1
    assert first["stages"]["dns"]["count"] == 1
    assert first["stages"]["peer"]["count"] == 1
    assert second["stages"]["tcp"]["count"] == second["stages"]["tls"]["count"] == 0
    assert first["stages"]["response_headers"]["count"] == second["stages"]["body"]["count"] == 1
    assert first["status"] == second["status"] == 200
    assert "127.0.0.1" not in caplog.text and "fixture.test" not in caplog.text
    assert "signature" not in caplog.text
    assert "private-secret" not in caplog.text and stages._current.get() is None


def test_real_certificate_rejection_stays_a_network_error_and_is_observed_as_tls(source, origin, certificate, caplog):
    with caplog.at_level(logging.INFO, logger="jzmedia.network.stages"):
        with running_server(origin["handler"], certificate["context"]) as url:
            with create_client(resolve_online_policy(source, "api")) as client:
                with pytest.raises(NetworkRequestError) as caught:
                    client.request("GET", url + "/must-not-arrive?private-secret")
    assert caught.value.category == "network_request_failed"
    result, = records(caplog)
    assert result["failure_stage"] == "tls" and result["outcome"] == "certificate_rejected"
    assert origin["records"] == []
    assert "private-secret" not in caplog.text and stages._current.get() is None


@contextmanager
def silent_tls_peer():
    ready, release = threading.Event(), threading.Event()
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(2)
    port = listener.getsockname()[1]

    def serve():
        connection = None
        try:
            ready.set()
            connection, _address = listener.accept()
            release.wait(2)
        except OSError:
            pass
        finally:
            if connection is not None:
                connection.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    assert ready.wait(1)
    try:
        yield f"https://127.0.0.1:{port}/private-signature"
    finally:
        release.set()
        listener.close()
        thread.join(timeout=2)


def test_real_tls_handshake_timeout_preserves_original_category_and_deadline(source, caplog):
    bind(source, "api", profile(connect_timeout_seconds=1, read_timeout_seconds=1))
    with caplog.at_level(logging.INFO, logger="jzmedia.network.stages"):
        with silent_tls_peer() as url:
            started = time.monotonic()
            with create_client(resolve_online_policy(source, "api")) as client:
                with pytest.raises(NetworkRequestError) as caught:
                    client.request("GET", url)
            assert time.monotonic() - started < 2.5
    assert caught.value.category == "network_timeout"
    result, = records(caplog)
    assert result["outcome"] == "timeout" and result["failure_stage"] == "tls"
    assert result["stages"]["tls"]["failures"] == 1
    assert "127.0.0.1" not in caplog.text and "private-signature" not in caplog.text
