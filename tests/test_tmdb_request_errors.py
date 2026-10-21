"""TMDB failures keep HTTP semantics without exposing credentials in caller errors."""
import logging
import traceback

import httpx
import pytest
from fastapi import HTTPException

from app import config, tmdb
from app.routers import persons


@pytest.fixture()
def upstream(monkeypatch):
    monkeypatch.setattr(config, "effective_tmdb_read_token", lambda: "")
    monkeypatch.setattr(config, "effective_tmdb_api_key", lambda: "isolated-private-api-key")
    monkeypatch.setattr(config, "effective_tmdb_language", lambda: "zh-CN")
    monkeypatch.setattr(config, "effective_tmdb_proxy", lambda: "")
    requests, waits = [], []

    def configure(statuses):
        sequence = iter(statuses)

        def respond(request):
            requests.append(request)
            return httpx.Response(next(sequence), json={"id": 123, "biography": "Example"})

        client_type = httpx.Client
        monkeypatch.setattr(tmdb, "_client", lambda: client_type(
            base_url=tmdb.API_BASE, transport=httpx.MockTransport(respond)))
        return requests, waits

    monkeypatch.setattr(tmdb.time, "sleep", waits.append)
    return configure


@pytest.mark.parametrize("fetch", [
    lambda: tmdb.movie_detail(123),
    lambda: tmdb.person_detail(123),
    lambda: tmdb._tv_light("/tv/123"),
], ids=["movie", "person", "tv-background"])
def test_authentication_error_keeps_status_but_hides_url_and_key(upstream, fetch):
    requests, waits = upstream([401])
    with pytest.raises(httpx.HTTPStatusError) as error:
        fetch()
    assert len(requests) == 1 and waits == []
    assert error.value.response.status_code == 401
    assert error.value.request is requests[0]
    assert "401" in str(error.value)
    visible = str(error.value) + "".join(traceback.format_exception(error.value))
    assert "isolated-private-api-key" not in visible
    assert "api_key=" not in visible


def test_person_refresh_api_does_not_echo_upstream_key(upstream, monkeypatch):
    upstream([401])
    monkeypatch.setattr(persons.store, "person_exists", lambda _: True)
    monkeypatch.setattr(persons, "_rate_ok", lambda _: True)
    with pytest.raises(HTTPException) as error:
        persons.refresh_person(123)
    assert error.value.status_code == 502
    assert "401" in error.value.detail
    assert "isolated-private-api-key" not in error.value.detail


def test_transient_http_errors_still_retry_and_succeed(upstream):
    requests, waits = upstream([503, 429, 200])
    assert tmdb.movie_detail(123)["id"] == 123
    assert len(requests) == 3 and waits == [0.8, 1.6]


def test_exhausted_transient_error_keeps_status_and_attempt_limit(upstream):
    requests, waits = upstream([503, 503, 503])
    with pytest.raises(httpx.HTTPStatusError) as error:
        tmdb.movie_detail(123)
    assert error.value.response.status_code == 503
    assert len(requests) == 3 and waits == [0.8, 1.6]
    assert "isolated-private-api-key" not in str(error.value)


def test_image_download_logs_do_not_echo_url_credentials(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(config, "effective_tmdb_proxy", lambda: "")
    client_type = httpx.Client
    monkeypatch.setattr(tmdb.httpx, "Client", lambda **kwargs: client_type(
        transport=httpx.MockTransport(lambda _: httpx.Response(401)), **kwargs))
    dest = tmp_path / "poster.jpg"
    with caplog.at_level(logging.WARNING, logger="jzmedia.tmdb"):
        assert not tmdb.download_url(
            "https://image-user:image-password@example.invalid/poster.jpg?token=image-token", str(dest))
    assert not dest.exists()
    assert "401" in caplog.text
    for secret in ("image-user", "image-password", "image-token"):
        assert secret not in caplog.text
