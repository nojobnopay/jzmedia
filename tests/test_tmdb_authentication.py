"""Optional API Key authentication and safe provider failures need no remote service."""
import json
import logging

import httpx
import pytest

from app import config, store, tmdb
from app.metadata import chain, state
from app.routers import metadata


@pytest.mark.parametrize("name", ["movie_images", "tv_images", "tv_alternative_titles"])
@pytest.mark.parametrize("bearer", [False, True], ids=["api-key", "bearer"])
def test_image_and_alias_requests_use_configured_authentication(name, bearer, monkeypatch):
    monkeypatch.setattr(config, "effective_tmdb_read_token", lambda: "synthetic-bearer" if bearer else "")
    monkeypatch.setattr(config, "effective_tmdb_api_key", lambda: "synthetic-api-key")
    monkeypatch.setattr(config, "effective_tmdb_language", lambda: "zh-CN")
    monkeypatch.setattr(config, "effective_tmdb_proxy", lambda: "")
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"posters": [], "results": []})

    client_type = httpx.Client
    monkeypatch.setattr(tmdb.httpx, "Client", lambda **kwargs: client_type(
        transport=httpx.MockTransport(respond), **kwargs))
    getattr(tmdb, name)(123)
    request, = requests
    assert request.url.params["language"] == "zh-CN"
    if bearer:
        assert request.headers["Authorization"] == "Bearer synthetic-bearer"
        assert "api_key" not in request.url.params
    else:
        assert "Authorization" not in request.headers
        assert request.url.params["api_key"] == "synthetic-api-key"
    if name.endswith("images"):
        assert request.url.params["include_image_language"] == "zh,en,null"


@pytest.fixture()
def provider_state():
    saved = store.get_setting(state.SETTING_KEY)
    state.reset()
    yield
    store.set_setting(state.SETTING_KEY, saved)


def test_provider_failures_do_not_leak_credentials_in_state_api_or_logs(provider_state, monkeypatch, caplog):
    monkeypatch.setattr(chain, "chain_for", lambda library_id: ["tmdb"])
    request = httpx.Request("GET", "https://proxy-user:proxy-secret@example.invalid/search?api_key=synthetic-secret")
    response = httpx.Response(401, request=request)

    def fail(*args):
        response.raise_for_status()

    monkeypatch.setitem(chain._SEARCHERS, "tmdb", fail)
    with caplog.at_level(logging.DEBUG, logger="jzmedia.metadata.chain"):
        for _ in range(state.FAIL_THRESHOLD):
            assert chain.search("Example") == []
    result = metadata.list_providers()
    row = next(item for item in result["providers"] if item["name"] == "tmdb")
    assert "401" in row["last_error"] and not row["available"]
    visible = json.dumps(result) + store.get_setting(state.SETTING_KEY) + caplog.text
    for secret in ("synthetic-secret", "proxy-secret", "proxy-user"):
        assert secret not in visible


def test_legacy_provider_errors_are_scrubbed_before_return_and_rewritten(provider_state):
    legacy = {"tmdb": {"fails": 1, "last_error": (
        "401 for url 'https://user:old-password@example.invalid/search?api_key=old-api-key' "
        "token=old-token Authorization: Bearer old-bearer")}}
    store.set_setting(state.SETTING_KEY, json.dumps(legacy))
    result = metadata.list_providers()
    stored = store.get_setting(state.SETTING_KEY)
    assert "401" in stored
    for secret in ("old-password", "old-api-key", "old-token", "old-bearer"):
        assert secret not in json.dumps(result) + stored
    assert state.snapshot(["tmdb"])[0]["fails"] == 1
