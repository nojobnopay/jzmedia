"""Successful source taxonomy is small, private, cancellable metadata only."""

from concurrent.futures import ThreadPoolExecutor
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from app import viewing
from app.network.guard import NetworkOperationError, current_operation
from app.online import catalog, maccms, taxonomy_cache
from app.online.providers import ProviderError
from app.store import online, online_network
from test_online_catalog import environment as environment


TREE = [{"id": "20", "name": "电影片", "parent_id": ""},
        {"id": "21", "name": "动作片", "parent_id": "20"}]
CLASSES = [{"type_id": "20", "type_name": "电影片", "type_pid": "0"},
           {"type_id": "21", "type_name": "动作片", "type_pid": "20"}]
SOURCE = {"id": "src_" + "1" * 32, "provider_options": {"detail_action": "detail", "search_action": "list"}}
ACCESS = SimpleNamespace(owner="isolated-viewer", generation=1, protection_revision=0)
CONFIGURATION = "a" * 64


def key(number):
    return format(number, "064x")


@pytest.fixture(autouse=True)
def clean_cache():
    taxonomy_cache.cache.clear()
    yield
    taxonomy_cache.cache.clear()


def test_expiry_never_renews_on_hits_and_every_reader_owns_its_copy():
    now = [0]
    cache = taxonomy_cache.TaxonomyCache(clock=lambda: now[0], ttl=10)
    value = [dict(row) for row in TREE]
    assert cache.put(key(1), value)
    value[0]["name"] = "调用者修改"
    now[0] = 9
    first = cache.get(key(1))
    assert first == TREE
    first[0]["name"] = "读取者修改"
    assert cache.get(key(1)) == TREE
    now[0] = 10
    assert cache.get(key(1)) is None
    assert cache.stats() == {"entries": 0, "bytes": 0}


def test_lru_and_real_utf8_bytes_keep_the_original_aggregate_budget():
    size = len(json.dumps(TREE, ensure_ascii=False, separators=(",", ":")).encode())
    cache = taxonomy_cache.TaxonomyCache(max_entries=2, max_bytes=size * 2)
    for number in (1, 2):
        assert cache.put(key(number), TREE)
    assert cache.get(key(1)) == TREE
    assert cache.put(key(3), TREE)
    assert cache.get(key(2)) is None
    assert cache.stats() == {"entries": 2, "bytes": size * 2}
    larger = [{"id": str(number), "name": "中文分类" * 20, "parent_id": ""} for number in range(4)]
    assert cache.put(key(1), larger) is False
    assert cache.get(key(1)) is None
    assert cache.stats() == {"entries": 1, "bytes": size}


@pytest.mark.parametrize("value", [[], None, {}, [None], [{**TREE[0], "url": "https://private.example/"}],
    [TREE[0], TREE[0]], [{**TREE[0], "name": "x" * 129}], [{**TREE[0], "name": "https://private.example/"}],
    [{**TREE[0], "id": "not,a,stable,id"}], [{**TREE[0], "parent_id": "missing"}],
    [{**TREE[0], "parent_id": "20"}], [{"id": str(number), "name": "分类", "parent_id": ""} for number in range(257)]])
def test_empty_malformed_oversized_or_non_metadata_trees_are_never_cached(value):
    cache = taxonomy_cache.TaxonomyCache()
    assert cache.put(key(1), value) is False
    assert cache.stats() == {"entries": 0, "bytes": 0}


def test_bad_limit_or_nonopaque_keys_cannot_expand_or_leak_cache_partitions():
    for values in ({"max_entries": 33}, {"max_bytes": taxonomy_cache.MAX_BYTES + 1},
                   {"ttl": 301}, {"ttl": float("nan")}, {"ttl": True}):
        with pytest.raises(ValueError):
            taxonomy_cache.TaxonomyCache(**values)
    assert taxonomy_cache.cache.put("https://api.example/?secret=private", TREE) is False
    assert taxonomy_cache.cache.get("not-a-key") is None


def test_parallel_readers_and_writers_remain_bounded_and_do_not_share_mutable_trees():
    cache = taxonomy_cache.TaxonomyCache(max_entries=4, max_bytes=1024)

    def write(number):
        cache.put(key(number), TREE)
        value = cache.get(key(number))
        if value is not None:
            assert value == TREE
            value[0]["name"] = "本读取者修改"

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(write, range(64)))
    assert cache.stats()["entries"] <= 4 and cache.stats()["bytes"] <= 1024
    for number in range(64):
        assert cache.get(key(number)) in (None, TREE)


def test_only_an_explicit_successful_foreground_context_commits_plain_metadata():
    taxonomy_cache.stage(SOURCE, TREE)
    assert taxonomy_cache.get(SOURCE) is None
    assert taxonomy_cache.cache.stats()["entries"] == 0
    with taxonomy_cache.foreground(ACCESS, SOURCE, CONFIGURATION, lambda: None):
        taxonomy_cache.stage(SOURCE, TREE)
        assert taxonomy_cache.cache.stats()["entries"] == 0
    assert taxonomy_cache.cache.stats()["entries"] == 1
    assert all(len(partition) == 64 for partition in taxonomy_cache.cache._items)
    assert b"https://" not in b"".join(body for _expires, body in taxonomy_cache.cache._items.values())
    with pytest.raises(ProviderError):
        with taxonomy_cache.foreground(ACCESS, SOURCE, "b" * 64, lambda: None):
            taxonomy_cache.stage(SOURCE, TREE)
            raise ProviderError("invalid_provider_response")
    assert taxonomy_cache.cache.stats()["entries"] == 1


def test_cache_partition_includes_device_generation_protection_source_actions_and_full_configuration():
    with taxonomy_cache.foreground(ACCESS, SOURCE, CONFIGURATION, lambda: None):
        taxonomy_cache.stage(SOURCE, TREE)
    for access, source, configuration in (
        (SimpleNamespace(owner="other", generation=1, protection_revision=0), SOURCE, CONFIGURATION),
        (SimpleNamespace(owner=ACCESS.owner, generation=2, protection_revision=0), SOURCE, CONFIGURATION),
        (SimpleNamespace(owner=ACCESS.owner, generation=1, protection_revision=1), SOURCE, CONFIGURATION),
        (ACCESS, {**SOURCE, "id": "src_" + "2" * 32}, CONFIGURATION),
        (ACCESS, {**SOURCE, "provider_options": {"detail_action": "other"}}, CONFIGURATION),
        (ACCESS, SOURCE, "b" * 64),
    ):
        with taxonomy_cache.foreground(access, source, configuration, lambda: None):
            assert taxonomy_cache.get(source) is None
    with taxonomy_cache.foreground(ACCESS, SOURCE, CONFIGURATION, lambda: None):
        assert taxonomy_cache.get(SOURCE) == TREE
        other = {**SOURCE, "id": "src_" + "2" * 32}
        assert taxonomy_cache.get(other) is None
        taxonomy_cache.stage(other, TREE)


@pytest.mark.parametrize("hook", ["get", "put"])
def test_cache_implementation_failure_is_optional_but_its_access_side_effect_is_not(monkeypatch, hook):
    denied = [False]

    def confirm():
        if denied[0]:
            raise catalog.CatalogError("online_source_changed", 409)

    def broken(*_args):
        raise RuntimeError("optional cache unavailable")

    monkeypatch.setattr(taxonomy_cache.cache, hook, broken)
    with taxonomy_cache.foreground(ACCESS, SOURCE, CONFIGURATION, confirm):
        assert taxonomy_cache.get(SOURCE) is None
        taxonomy_cache.stage(SOURCE, TREE)

    def revoke(*_args):
        denied[0] = True
        raise RuntimeError("optional cache unavailable")

    monkeypatch.setattr(taxonomy_cache.cache, hook, revoke)
    with pytest.raises(catalog.CatalogError) as caught:
        with taxonomy_cache.foreground(ACCESS, SOURCE, CONFIGURATION, confirm):
            taxonomy_cache.get(SOURCE)
            taxonomy_cache.stage(SOURCE, TREE)
    assert caught.value.category == "online_source_changed"


@pytest.mark.parametrize("error", [catalog.CatalogError("online_access_revoked", 401),
                                  NetworkOperationError("operation_cancelled")])
def test_categorized_or_cancellation_hook_errors_are_never_treated_as_cache_misses(monkeypatch, error):
    def fail(_key):
        raise error

    monkeypatch.setattr(taxonomy_cache.cache, "get", fail)
    with pytest.raises(type(error)):
        with taxonomy_cache.foreground(ACCESS, SOURCE, CONFIGURATION, lambda: None):
            taxonomy_cache.get(SOURCE)


def upstream(environment, monkeypatch):
    state = SimpleNamespace(calls=[], main_classes=None, discovered=CLASSES, primary_error=None, malformed=False)
    environment.movie.update(type_id="21")

    def fetch(source, params):
        assert source["id"] == environment.source["id"]
        state.calls.append(dict(params))
        if params == {"ac": "list", "pg": "1"}:
            if isinstance(state.discovered, Exception):
                raise state.discovered
            return {"code": 1, "list": [], "class": state.discovered}
        if state.primary_error is not None:
            raise state.primary_error
        page = int(params.get("pg", "1"))
        result = {"code": 1, "list": [environment.movie], "page": page + int(state.malformed),
                  "pagecount": 5, "total": 5}
        if state.main_classes is not None:
            result["class"] = state.main_classes
        return result

    monkeypatch.setattr(maccms, "fetch", fetch)
    return state


def browse(environment, page=1, client=None):
    return (client or environment.client).get("/api/online/catalog/browse", params={
        "source_id": environment.source["id"], "category_id": "21", "page": page})


def test_foreground_browse_always_reads_the_primary_but_reuses_successful_taxonomy(environment, monkeypatch):
    state = upstream(environment, monkeypatch)
    first = browse(environment)
    assert first.status_code == 200 and first.json()["categories"] == TREE
    assert state.calls == [{"ac": "detail", "pg": "1", "t": "21"}, {"ac": "list", "pg": "1"}]
    second = browse(environment, 2)
    assert second.status_code == 200 and second.json()["categories"] == TREE
    assert state.calls[-1] == {"ac": "detail", "pg": "2", "t": "21"} and len(state.calls) == 3
    assert second.json()["items"][0]["parent_category_id"] == "20"
    assert taxonomy_cache.cache.stats()["entries"] == 1
    state.primary_error = ProviderError("provider_timeout")
    failed = browse(environment, 3)
    assert failed.status_code == 504 and "items" not in failed.json()
    assert len(state.calls) == 4


def test_cache_hits_do_not_renew_taxonomy_expiry_and_failed_discovery_is_never_cached(environment, monkeypatch):
    now = [0]
    monkeypatch.setattr(taxonomy_cache, "cache", taxonomy_cache.TaxonomyCache(clock=lambda: now[0], ttl=10))
    state = upstream(environment, monkeypatch)
    assert browse(environment).status_code == 200
    now[0] = 9
    assert browse(environment, 2).status_code == 200 and len(state.calls) == 3
    now[0] = 10
    state.discovered = ProviderError("provider_timeout")
    response = browse(environment, 3)
    assert response.status_code == 200 and response.json()["categories"] == []
    assert len(state.calls) == 5 and taxonomy_cache.cache.stats()["entries"] == 0
    assert browse(environment, 4).status_code == 200
    assert len(state.calls) == 7 and taxonomy_cache.cache.stats()["entries"] == 0


def test_fresh_primary_taxonomy_replaces_a_cached_tree_and_invalid_primary_never_populates(environment, monkeypatch):
    state = upstream(environment, monkeypatch)
    assert browse(environment).json()["categories"] == TREE
    state.main_classes = [{"type_id": "30", "type_name": "剧集"}]
    assert browse(environment, 2).json()["categories"] == [{"id": "30", "name": "剧集", "parent_id": ""}]
    state.main_classes = None
    assert browse(environment, 3).json()["categories"] == [{"id": "30", "name": "剧集", "parent_id": ""}]
    taxonomy_cache.cache.clear()
    state.main_classes = CLASSES
    state.malformed = True
    result = browse(environment)
    assert result.status_code == 502 and result.json()["code"] == "invalid_provider_response"
    assert taxonomy_cache.cache.stats()["entries"] == 0
    assert state.calls[-1] == {"ac": "detail", "pg": "1", "t": "21"}


def test_unsupported_background_adapter_reads_do_not_borrow_foreground_cache(environment, monkeypatch):
    state = upstream(environment, monkeypatch)
    assert browse(environment).status_code == 200
    assert len(state.calls) == 2
    result = maccms.browse(environment.source, 2, "21")
    assert result["categories"] == TREE
    assert len(state.calls) == 4, "a direct/background adapter read still performs its own optional discovery"


def test_cache_cannot_cross_devices_or_changed_source_network_and_protection(environment, monkeypatch):
    state = upstream(environment, monkeypatch)
    assert browse(environment).status_code == 200
    other = TestClient(environment.client.app)
    try:
        assert other.get("/api/viewing/session").status_code == 200
        assert browse(environment, client=other).status_code == 200
    finally:
        other.close()
    assert len(state.calls) == 4
    online_network.update_online_network_default({"mode": "direct"})
    assert browse(environment).status_code == 200 and len(state.calls) == 6
    online.update_online_source(environment.source["id"], {"name": "新的来源名称"})
    assert browse(environment).status_code == 200 and len(state.calls) == 8
    viewing.set_password("isolated-taxonomy-protection")
    assert environment.client.get("/api/viewing/session").status_code == 200
    assert browse(environment).status_code == 200 and len(state.calls) == 10
    online.update_online_source(environment.source["id"], {"enabled": False})
    assert browse(environment).status_code == 503 and len(state.calls) == 10


@pytest.mark.parametrize(("change", "status", "code"), [
    ("source", 503, "online_source_disabled"), ("network", 409, "online_source_changed"),
    ("access", 401, "online_access_revoked"), ("cancel", 410, "provider_cancelled"),
])
def test_cache_commit_cannot_suppress_configuration_access_or_cancellation_changes(
        environment, monkeypatch, change, status, code):
    upstream(environment, monkeypatch)
    put = taxonomy_cache.cache.put

    def changed(*args):
        put(*args)
        if change == "source":
            online.update_online_source(environment.source["id"], {"enabled": False})
        elif change == "network":
            online_network.update_online_network_default({"mode": "direct"})
        elif change == "access":
            viewing.set_password("isolated-taxonomy-protection")
        else:
            current_operation().abort()
        raise RuntimeError("cache bookkeeping interrupted")

    monkeypatch.setattr(taxonomy_cache.cache, "put", changed)
    result = browse(environment)
    assert result.status_code == status and result.json()["code"] == code
    assert taxonomy_cache.cache.stats()["entries"] == 0
    assert catalog.admission_stats() == {"active": 0, "owners": 0, "background": 0, "preempt_waiters": 0}


def test_cache_hit_rechecks_access_after_an_optional_hook_failure(environment, monkeypatch):
    state = upstream(environment, monkeypatch)
    assert browse(environment).status_code == 200
    get = taxonomy_cache.cache.get

    def changed(*args):
        get(*args)
        online.update_online_source(environment.source["id"], {"enabled": False})
        raise RuntimeError("cache bookkeeping interrupted")

    monkeypatch.setattr(taxonomy_cache.cache, "get", changed)
    result = browse(environment, 2)
    assert result.status_code == 503 and result.json()["code"] == "online_source_disabled"
    assert len(state.calls) == 3, "revoked cache access must not fall through to another upstream read"
