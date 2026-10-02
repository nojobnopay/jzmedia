"""Optional AI must stay bounded, keep credentials server-side, and fail without side effects."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
import pytest

from app import store
from app.ai import client as ai
from app.ai import settings
from app.routers.ai_settings import router

api_app = FastAPI()
api_app.include_router(router)
api = TestClient(api_app)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(store._base, "DB_PATH", str(tmp_path / "ai.db"))
    for key in settings.DEFAULTS:
        monkeypatch.delenv("AI_" + key.upper(), raising=False)
    ai.clear_cache()
    yield
    ai.clear_cache()


def configure(**changes):
    return settings.update({"enabled": True, "api_key": "private-api-secret", **changes})


def response(content=None, **extras):
    return httpx.Response(200, json={
        "choices": [{"message": {"content": json.dumps({"ok": True} if content is None else content)},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 25, "completion_tokens": 8}, **extras,
    })


def fake_network(monkeypatch, handler):
    original = httpx.AsyncClient
    def factory(**kwargs):
        return original(transport=httpx.MockTransport(handler), **kwargs)
    monkeypatch.setattr(ai.httpx, "AsyncClient", factory)


def test_default_disabled_settings_are_local_and_masked(monkeypatch):
    fake_network(monkeypatch, lambda request: pytest.fail("GET must not call network"))
    result = api.get("/api/ai/settings")
    assert result.status_code == 200
    data = result.json()
    assert not data["enabled"] and not data["api_key_set"]
    assert data["model"] == "deepseek-flash" and data["daily_limit"] == 100
    assert data["usage"]["requests"] == 0 and "api_key" not in data
    with pytest.raises(ai.AiUnavailable, match="尚未启用"):
        ai.call_json("search", {}, "JSON")


def test_db_overrides_env_secret_clear_restores_environment(monkeypatch):
    monkeypatch.setenv("AI_API_KEY", "environment-secret")
    monkeypatch.setenv("AI_ENABLED", "true")
    assert settings.effective().api_key == "environment-secret"
    data = api.patch("/api/ai/settings", json={"api_key": "database-secret", "enabled": False}).json()
    assert data["api_key_source"] == "db" and data["api_key_set"] and not data["enabled"]
    assert "database-secret" not in json.dumps(data) and "environment-secret" not in json.dumps(data)
    assert "database-secret" not in repr(settings.effective())
    assert settings.effective().api_key == "database-secret"
    api.patch("/api/ai/settings", json={"api_key": " "})
    assert settings.effective().api_key == "database-secret"
    data = api.patch("/api/ai/settings", json={"clear_api_key": True}).json()
    assert data["api_key_source"] == "env" and settings.effective().api_key == "environment-secret"
    monkeypatch.delenv("AI_API_KEY")
    assert not settings.public_settings()["api_key_set"]


@pytest.mark.parametrize("changes", [
    {"provider": "unknown"}, {"enabled": "false"}, {"timeout_seconds": 0},
    {"timeout_seconds": 61}, {"daily_limit": 0}, {"daily_limit": True},
    {"model": ""}, {"model": "\nmodel\ninvalid"}, {"unexpected": True},
    {"api_key": "****cret"}, {"api_key": "secret\nnew-header"},
    {"api_key": "secret", "clear_api_key": True}, {"clear_api_key": "yes"},
    {"base_url": "https://third-party.example/v1"},
])
def test_invalid_updates_are_atomic_and_never_echo_values(changes):
    configure()
    result = api.patch("/api/ai/settings", json={"model": "replacement", **changes})
    assert result.status_code == 400, result.text
    assert settings.effective().model == "deepseek-flash"
    assert settings.effective().api_key == "private-api-secret"
    assert "private-api-secret" not in result.text and "secret\nnew-header" not in result.text


@pytest.mark.parametrize("base_url", [
    "file:///tmp/data", "ftp://example.com", "http://user:secret@localhost/v1",
    "http://localhost/v1?key=secret", "http://localhost/v1#secret", "http://localhost/v1?",
    "http://localhost/v1#", "http://", "http://localhost:bad", "http://localhost:0",
    "http://localhost\n/secret", "http://localhost\\@example.com", "http://@localhost",
])
def test_endpoint_validation_rejects_credentials_and_non_base_urls(base_url):
    result = api.patch("/api/ai/settings", json={"provider": "compatible", "base_url": base_url})
    assert result.status_code == 400
    assert "secret" not in result.text


def test_compatible_local_http_no_key_and_explicit_version_path(monkeypatch):
    settings.update({"enabled": True, "provider": "compatible", "base_url": "http://localhost:11434/v1/",
                     "model": "qwen3:8b"})
    calls = []
    def handler(request):
        calls.append(request)
        body = json.loads(request.content)
        assert "authorization" not in request.headers and "thinking" not in body
        assert str(request.url) == "http://localhost:11434/v1/chat/completions"
        return response()
    fake_network(monkeypatch, handler)
    assert ai.call_json("search", {}, "JSON") == {"ok": True}
    assert len(calls) == 1


def test_deepseek_nonthinking_bounded_json_request_and_usage(monkeypatch):
    configure()
    seen = []
    def handler(request):
        seen.append(json.loads(request.content))
        assert request.headers["Authorization"] == "Bearer private-api-secret"
        assert str(request.url) == "https://api.deepseek.com/chat/completions"
        return response({"filters": {"watched": False}})
    fake_network(monkeypatch, handler)
    assert ai.call_json("search", {"text": "没看过的电影"}, "Return JSON") == {"filters": {"watched": False}}
    assert seen[0]["thinking"] == {"type": "disabled"}
    assert seen[0]["response_format"] == {"type": "json_object"}
    assert seen[0]["max_tokens"] == ai.MAX_OUTPUT_TOKENS and seen[0]["stream"] is False
    assert settings.usage() == {"date": settings._today(), "requests": 1, "input_tokens": 25, "output_tokens": 8}


def test_check_can_run_disabled_and_is_never_cached(monkeypatch):
    configure(enabled=False)
    seen = []
    def handler(request):
        seen.append(json.loads(request.content))
        return response()
    fake_network(monkeypatch, handler)
    for _ in range(2):
        result = api.post("/api/ai/check")
        assert result.status_code == 200 and result.json()["ok"]
    assert len(seen) == 2 and seen[0]["max_tokens"] == 32
    assert settings.usage()["requests"] == 2


def test_missing_key_never_calls_provider(monkeypatch):
    fake_network(monkeypatch, lambda request: pytest.fail("missing key must not call network"))
    result = api.post("/api/ai/check").json()
    assert result["code"] == "not_configured" and not result["ok"]
    assert settings.usage()["requests"] == 0


@pytest.mark.parametrize("outcome,code", [
    (401, "invalid_credentials"), (403, "invalid_credentials"), (429, "rate_limited"),
    (500, "upstream_error"), (302, "upstream_error"), ("timeout", "timeout"),
    ("network", "connection_error"), ("html", "invalid_response"),
    ("oversized", "invalid_response"), ("array", "invalid_response"),
    ("truncated", "invalid_response"), ("nested", "invalid_response"),
])
def test_failures_are_structured_redacted_not_retried(monkeypatch, caplog, outcome, code):
    configure()
    seen = []
    def handler(request):
        seen.append(request)
        if outcome == "timeout":
            raise httpx.ReadTimeout("private-api-secret private-upstream-body")
        if outcome == "network":
            raise httpx.ConnectError("private-api-secret private-upstream-body")
        if outcome == "html":
            return httpx.Response(200, text="private-api-secret private-upstream-body")
        if outcome == "oversized":
            return httpx.Response(200, content=b"x" * (ai.MAX_RESPONSE_BYTES + 1))
        if outcome == "array":
            return response([1, 2])
        if outcome == "nested":
            return httpx.Response(200, json=["private-upstream-body"])
        if outcome == "truncated":
            return response(choices=[{"message": {"content": '{"ok":true}'}, "finish_reason": "length"}])
        return httpx.Response(outcome, headers={"Location": "https://other.example/private"},
                              json={"error": "private-api-secret private-upstream-body"})
    fake_network(monkeypatch, handler)
    result = api.post("/api/ai/check")
    assert result.status_code == 200 and result.json()["code"] == code
    assert len(seen) == 1 and settings.usage()["requests"] == 1
    assert "private-api-secret" not in result.text + caplog.text
    assert "private-upstream-body" not in result.text + caplog.text


def test_total_deadline_cancels_slow_provider(monkeypatch):
    configure()
    actual = settings.effective()
    monkeypatch.setattr(settings, "effective", lambda: replace(actual, timeout_seconds=0.02))
    async def handler(request):
        await asyncio.sleep(0.1)
        return response()
    fake_network(monkeypatch, handler)
    assert api.post("/api/ai/check").json()["code"] == "timeout"


def test_cache_is_copied_configured_scoped_and_disabled_never_reuses(monkeypatch):
    configure()
    calls = []
    def handler(request):
        calls.append(request)
        return response({"filters": {"watched": False}})
    fake_network(monkeypatch, handler)
    first = ai.call_json("search", {"q": "test"}, "JSON")
    first["filters"]["watched"] = True
    assert ai.call_json("search", {"q": "test"}, "JSON")["filters"]["watched"] is False
    assert len(calls) == 1 and settings.usage()["requests"] == 1
    ai.discard_cached("search", {"q": "test"}, "JSON")
    ai.call_json("search", {"q": "test"}, "JSON")
    assert len(calls) == 2
    for change in ({"model": "new-model"}, {"api_key": "replacement-key"}):
        settings.update(change)
        ai.call_json("search", {"q": "test"}, "JSON")
    ai.call_json("search", {"q": "other"}, "JSON")
    ai.call_json("match", {"q": "test"}, "JSON")
    ai.call_json("search", {"q": "test"}, "different JSON instruction")
    assert len(calls) == 7
    settings.update({"enabled": False})
    with pytest.raises(ai.AiUnavailable) as error:
        ai.call_json("search", {"q": "test"}, "JSON")
    assert error.value.code == "disabled" and len(calls) == 7


def test_daily_cap_persists_and_cache_hits_do_not_consume_it(monkeypatch):
    configure(daily_limit=1)
    fake_network(monkeypatch, lambda request: response())
    ai.call_json("search", {}, "JSON")
    assert ai.call_json("search", {}, "JSON") == {"ok": True}
    ai.clear_cache()
    with pytest.raises(ai.AiUnavailable) as error:
        ai.call_json("search", {}, "JSON")
    assert error.value.code == "daily_limit" and settings.usage()["requests"] == 1
    monkeypatch.setattr(settings, "_today", lambda: "2099-01-01")
    assert ai.call_json("search", {}, "JSON") == {"ok": True}
    assert settings.usage()["requests"] == 1


def test_concurrent_reservations_cannot_exceed_persistent_cap():
    with ThreadPoolExecutor(max_workers=8) as pool:
        attempts = list(pool.map(lambda _: settings.reserve_request(5), range(30)))
    assert len([day for day in attempts if day]) == 5
    assert settings.usage()["requests"] == 5


def test_busy_and_large_input_do_not_consume_quota(monkeypatch):
    configure()
    fake_network(monkeypatch, lambda request: pytest.fail("request must stay local"))
    with pytest.raises(ai.AiUnavailable) as error:
        ai.call_json("search", {"q": "中" * ai.MAX_INPUT_BYTES}, "JSON")
    assert error.value.code == "input_too_large"
    assert ai._slots.acquire(blocking=False)
    assert ai._slots.acquire(blocking=False)
    try:
        with pytest.raises(ai.AiUnavailable) as error:
            ai.call_json("search", {}, "JSON")
        assert error.value.code == "busy"
    finally:
        ai._slots.release()
        ai._slots.release()
    assert settings.usage()["requests"] == 0
