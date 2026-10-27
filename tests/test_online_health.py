"""Health observations do no I/O and never confuse routes with decoding."""

from copy import deepcopy
import json
from types import SimpleNamespace

from app.online import health
from app.online.health_models import SourceHealth
from app.online.providers import ProviderError


SOURCE = "src_" + "1" * 32


def state():
    inherit = {"mode": "inherit", "profile_id": None}
    return {"online_enabled": True,
            "source_config": {"id": SOURCE, "provider": "maccms", "name": "Isolated source", "enabled": True,
                "api_base_url": "https://private-api.example.invalid/provide/vod/", "allowed_hosts": ["media.example.invalid"],
                "provider_options": {"search_action": "list", "detail_action": "detail"}},
            "application": {"rule": {"mode": "direct", "profile_id": None}, "revision": 0},
            "source": {"source_id": SOURCE, "policy": {key: dict(inherit) for key in ("default", *health.PURPOSES)}, "revision": 0},
            "profiles": {}}


def context(values, purpose="api"):
    fingerprint, mode = health.configuration(values, purpose)
    return health.ReadContext(values["source_config"]["id"], purpose, fingerprint, mode)


def registry(**changes):
    clock = [100.0]
    return health.HealthRegistry(clock=lambda: clock[0], wall_clock=lambda: 1700000000, **changes), clock


def test_success_is_specific_to_the_observed_purpose_and_does_not_leak_configuration():
    values = state()
    cache, _clock = registry()
    cache.finish(health.Observation("catalogue", 99.0, context(values)))
    api, media = cache.summary(values, "api"), cache.summary(values, "media")
    assert api["state"] == "success" and api["stage"] == "catalogue" and api["mode"] == "direct"
    assert media["state"] == "unknown"
    response = SourceHealth.model_validate({"source_id": SOURCE, "purposes": {"api": api, "media": media}})
    serialized = response.model_dump_json()
    assert "private-api" not in serialized and "media.example" not in serialized
    assert "http" not in serialized and "credentials" not in serialized


def test_failure_categories_are_fixed_and_cancellation_does_not_mark_a_source_failed():
    values = state()
    cache, clock = registry()
    cache.finish(health.Observation("catalogue", 90.0, context(values)), ProviderError("provider_timeout"))
    assert cache.summary(values, "api")["category"] == "provider_timeout"
    clock[0] += 1
    cache.finish(health.Observation("catalogue", 101.0, context(values)), ProviderError("provider_cancelled"))
    assert cache.summary(values, "api")["category"] == "provider_timeout"
    cache.finish(health.Observation("catalogue", 102.0, context(values)), RuntimeError("https://secret/path?token=private"))
    result = cache.summary(values, "api")
    assert result["category"] == "online_read_failed"
    assert "secret" not in json.dumps(result) and "private" not in json.dumps(result)


def test_source_or_network_changes_make_old_results_unknown_without_new_requests():
    values = state()
    cache, _clock = registry()
    cache.finish(health.Observation("catalogue", 90.0, context(values)))
    changed = deepcopy(values)
    changed["source_config"]["api_base_url"] += "other/"
    assert cache.summary(changed, "api")["reason"] == "configuration_changed"
    proxy = deepcopy(values)
    proxy["source"]["policy"]["media"] = {"mode": "proxy", "profile_id": "np_" + "2" * 32}
    proxy["source"]["revision"] += 1
    proxy["profiles"] = {"np_" + "2" * 32: {"mode": "proxy", "proxy_url": "http://internal-proxy.invalid/",
        "credentials_encrypted": "do-not-expose-this-ciphertext", "revision": 1}}
    result = cache.summary(proxy, "media")
    assert result["mode"] == "proxy" and result["state"] == "unknown"
    assert "internal-proxy" not in json.dumps(result) and "ciphertext" not in json.dumps(result)


def test_local_gateway_budget_does_not_turn_into_an_upstream_health_failure():
    class BudgetError(Exception):
        category = "byte_budget_exhausted"

    values = state()
    cache, _clock = registry()
    cache.finish(health.Observation("bytes", 90.0, context(values, "media")), BudgetError())
    assert cache.summary(values, "media")["state"] == "unknown"


def test_expiry_disable_and_process_restart_never_leave_a_false_success():
    values = state()
    cache, clock = registry(ttl=30)
    cache.finish(health.Observation("catalogue", 90.0, context(values)))
    clock[0] += 31
    assert cache.summary(values, "api")["state"] == "stale"
    values["source_config"]["enabled"] = False
    assert cache.summary(values, "api")["state"] == "disabled"
    values["source_config"]["enabled"] = True
    cache.clear()
    assert cache.summary(values, "api")["state"] == "unknown"
    values["source_config"]["provider"] = "open_catalog"
    assert cache.summary(values, "api")["state"] == "local"


def test_a_late_read_cannot_replace_a_newer_configuration_observation():
    before = state()
    after = deepcopy(before)
    after["source"]["revision"] += 1
    cache, _clock = registry()
    cache.finish(health.Observation("catalogue", 99.0, context(after)))
    cache.finish(health.Observation("catalogue", 90.0, context(before)), ProviderError("provider_timeout"))
    assert cache.summary(after, "api")["state"] == "success"


def test_registry_capacity_is_bounded_and_media_stage_only_means_bytes_were_read():
    values = state()
    cache, _clock = registry(capacity=2)
    for number in range(3):
        values["source_config"]["id"] = "src_" + str(number) * 32
        cache.finish(health.Observation("bytes", 99.0, context(values, "media")))
    values["source_config"]["id"] = "src_" + "0" * 32
    assert cache.summary(values, "media")["state"] == "unknown"
    values["source_config"]["id"] = "src_" + "2" * 32
    result = cache.summary(values, "media")
    assert result["state"] == "success" and result["stage"] == "bytes"
    assert "decoded" not in result and "playable" not in result


def test_catalogue_success_is_recorded_after_semantic_parsing_and_failure_is_observed(monkeypatch):
    values = state()
    cache = health.HealthRegistry()
    monkeypatch.setattr(health, "registry", cache)
    client = SimpleNamespace(snapshot=SimpleNamespace(source_id=SOURCE, purpose="api"))
    with health.observe_catalogue():
        health.bind_client(client, values)
        assert cache.summary(values, "api")["state"] == "unknown"
    assert cache.summary(values, "api")["state"] == "success"
    try:
        with health.observe_catalogue():
            health.bind_client(client, values)
            raise ProviderError("invalid_provider_response")
    except ProviderError:
        pass
    assert cache.summary(values, "api")["category"] == "invalid_provider_response"
    assert health.begin_media(SimpleNamespace(), "bytes") is None
