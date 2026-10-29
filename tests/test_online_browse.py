"""Discovery uses the same bounded provider and access boundary as search."""

from concurrent.futures import ThreadPoolExecutor
import threading
import time

import pytest

from app.network.guard import (NetworkOperation, NetworkOperationError,
                               current_operation, operation_context)
from app.online import maccms
from app.online.providers import ProviderError
from app.store import online
from test_online_catalog import environment as environment


def test_latest_browse_includes_real_categories_and_forwards_category_filter(environment, monkeypatch):
    calls = []

    def fetch(_source, params):
        calls.append(params)
        return {"code": 1, "list": [environment.movie], "page": 1, "pagecount": 3, "total": 42,
                "class": [{"type_id": 7, "type_name": "<b>动作片</b>"},
                          {"type_id": "8", "type_name": "国产剧"}]}

    monkeypatch.setattr(maccms, "fetch", fetch)
    response = environment.client.get("/api/online/catalog/browse", params={
        "source_id": environment.source["id"], "category_id": "7"})
    assert response.status_code == 200
    body = response.json()
    assert body["categories"] == [{"id": "7", "name": "动作片", "parent_id": ""},
                                  {"id": "8", "name": "国产剧", "parent_id": ""}]
    assert body["total"] == 42 and body["page_count"] == 3
    assert calls == [{"ac": "detail", "pg": "1", "t": "7"}]
    assert "cdn.example" not in response.text


def test_category_discovery_can_use_list_without_treating_it_as_search(environment, monkeypatch):
    calls = []

    def fetch(_source, params):
        calls.append(params)
        if params["ac"] == "list":
            return {"code": 1, "list": [], "class": [{"type_id": 1, "type_name": "电影"}]}
        return {"code": 1, "list": [environment.movie], "page": 1}

    monkeypatch.setattr(maccms, "fetch", fetch)
    response = environment.client.get("/api/online/catalog/browse", params={"source_id": environment.source["id"]})
    assert response.status_code == 200
    assert response.json()["categories"] == [{"id": "1", "name": "电影", "parent_id": ""}]
    assert [call["ac"] for call in calls] == ["detail", "list"]
    assert all("wd" not in call for call in calls)


def test_catalogue_artwork_exposes_only_opaque_same_origin_path(environment):
    environment.movie["vod_pic"] = "https://cdn.example/poster.jpg?private-signature=hidden"
    response = environment.client.get("/api/online/catalog/search", params={
        "source_id": environment.source["id"], "q": "合成"})
    assert response.status_code == 200
    assert response.json()["items"][0]["poster_path"].startswith("/api/online/catalog/artwork/")
    assert "private-signature" not in response.text and "cdn.example" not in response.text
    assert "_poster_url" not in response.text
    detail = environment.client.get("/api/online/catalog/detail", params={
        "source_id": environment.source["id"], "external_id": "movie-1"})
    assert detail.status_code == 200
    assert detail.json()["poster_path"] == response.json()["items"][0]["poster_path"]


def test_browse_validates_paging_and_category_before_provider_io(environment):
    for params in ({"page": 0}, {"page": 1001}, {"category_id": "https://private.example/"}):
        response = environment.client.get("/api/online/catalog/browse", params={"source_id": environment.source["id"], **params})
        assert response.status_code == 422
    assert environment.requests == []


def test_optional_category_timeout_preserves_fetched_movies_and_parent_operation(environment, monkeypatch):
    parent = NetworkOperation(time.monotonic() + 30)
    children = []

    def fetch(_source, params):
        if params["ac"] == "detail":
            return {"code": 1, "list": [environment.movie], "page": 1}
        child = current_operation()
        children.append(child)
        assert child is not parent and child.cancelled is not parent.cancelled
        assert 0 < child.remaining() <= 3
        child.abort("operation_timeout")
        raise ProviderError("provider_timeout")

    monkeypatch.setattr(maccms, "fetch", fetch)
    try:
        with operation_context(parent):
            result = maccms.browse(environment.source, 1)
        assert result["items"][0]["external_id"] == "movie-1"
        assert result["categories"] == []
        parent.check()
        assert parent.cancelled.is_set() is False
        assert parent._sockets == {}
        assert children and children[0]._closed
    finally:
        parent.close()


def test_parent_cancellation_immediately_aborts_category_socket_and_releases_its_link(environment, monkeypatch):
    parent = NetworkOperation(time.monotonic() + 30)
    started, interrupted = threading.Event(), threading.Event()
    children = []

    class Socket:
        def shutdown(self, _how):
            interrupted.set()

    def fetch(_source, params):
        if params["ac"] == "detail":
            return {"code": 1, "list": [environment.movie], "page": 1}
        child = current_operation()
        children.append(child)
        sock = Socket()
        child.bind_socket(sock)
        try:
            started.set()
            assert interrupted.wait(1), "parent abort must shut the child's socket, not wait for its timeout"
            child.check()
            raise AssertionError("cancelled category fetch may not succeed")
        except NetworkOperationError:
            raise ProviderError("provider_cancelled") from None
        finally:
            child.release_socket(sock)

    def browse():
        with operation_context(parent):
            return maccms.browse(environment.source, 1)

    monkeypatch.setattr(maccms, "fetch", fetch)
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(browse)
            assert started.wait(1)
            parent.abort()
            assert interrupted.wait(1)
            with pytest.raises(NetworkOperationError, match="operation_cancelled"):
                pending.result(timeout=2)
        assert parent._sockets == {}
        assert children and children[0]._closed
    finally:
        parent.abort()
        parent.close()


def test_optional_category_failure_cannot_hide_the_parent_deadline(environment, monkeypatch):
    parent = NetworkOperation(time.monotonic() + 30)

    def fetch(_source, params):
        if params["ac"] == "detail":
            return {"code": 1, "list": [environment.movie], "page": 1}
        parent.deadline = time.monotonic() - 1
        raise ProviderError("provider_timeout")

    monkeypatch.setattr(maccms, "fetch", fetch)
    try:
        with operation_context(parent), pytest.raises(NetworkOperationError, match="operation_timeout"):
            maccms.browse(environment.source, 1)
        assert parent.cancelled.is_set()
        assert parent._sockets == {}
    finally:
        parent.close()


def test_category_tree_keeps_real_parents_and_detaches_orphans_self_links_and_cycles(environment, monkeypatch):
    categories = [
        {"type_id": 58, "type_pid": 0, "type_name": "短剧"},
        {"type_id": 71, "type_pid": 58, "type_name": "都市脑洞"},
        {"type_id": 71, "type_pid": 99, "type_name": "重复分类不可覆盖"},
        {"type_id": "orphan", "type_pid": 999, "type_name": "孤立"},
        {"type_id": "self", "type_pid": "self", "type_name": "自环"},
        {"type_id": "cycle-a", "type_pid": "cycle-b", "type_name": "环甲"},
        {"type_id": "cycle-b", "type_pid": "cycle-a", "type_name": "环乙"},
        {"type_id": "child", "type_pid": "cycle-a", "type_name": "环节点的子类"},
        {"type_id": "invalid", "type_pid": {"url": "https://private.invalid/"}, "type_name": "无效可选父类"},
    ]
    row = {**environment.movie, "type_id": 71}
    monkeypatch.setattr(maccms, "fetch", lambda *_args: {"list": [row], "class": categories})
    response = environment.client.get("/api/online/catalog/browse", params={"source_id": environment.source["id"]})
    assert response.status_code == 200
    by_id = {category["id"]: category for category in response.json()["categories"]}
    assert len(by_id) == 8
    assert by_id["71"] == {"id": "71", "name": "都市脑洞", "parent_id": "58"}
    assert response.json()["items"][0]["parent_category_id"] == "58"
    for identity in ("58", "orphan", "self", "cycle-a", "cycle-b", "invalid"):
        assert by_id[identity]["parent_id"] == ""
    assert by_id["child"]["parent_id"] == "cycle-a"
    assert "private.invalid" not in response.text


@pytest.mark.parametrize("categories", [None, {}, [None], [{"type_id": "bad\n", "type_name": "bad"}],
                                         [{"type_id": value, "type_name": "many"} for value in range(257)]])
def test_category_tree_rejects_unbounded_or_invalid_required_nodes(environment, monkeypatch, categories):
    monkeypatch.setattr(maccms, "fetch", lambda *_args: {"list": [environment.movie], "class": categories})
    response = environment.client.get("/api/online/catalog/browse", params={"source_id": environment.source["id"]})
    # None/empty invalid payloads trigger the existing optional discovery; its
    # returned non-list class is still rejected rather than traversed.
    assert response.status_code == 502


def test_parent_category_is_one_exact_upstream_query_not_an_implicit_aggregate(environment, monkeypatch):
    calls = []
    def fetch(_source, params):
        calls.append(params)
        return {"list": [{**environment.movie, "type_id": 58}], "total": 1,
                "class": [{"type_id": 58, "type_pid": 0, "type_name": "短剧"},
                          {"type_id": 71, "type_pid": 58, "type_name": "都市脑洞"}]}
    monkeypatch.setattr(maccms, "fetch", fetch)
    response = environment.client.get("/api/online/catalog/browse", params={
        "source_id": environment.source["id"], "category_id": "58"})
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["category_id"] == "58"
    assert calls == [{"ac": "detail", "pg": "1", "t": "58"}]


def test_list_and_detail_share_bounded_plain_metadata_without_changing_identities(environment):
    row = environment.show
    row.update(type_id=31, type_id_1=30, vod_remarks="<b>更新至第2集</b>",
               vod_class="剧情/都市，剧情", vod_actor="<b>张三</b>,李四,张三,<script>secret</script>",
               vod_director="导演甲、导演乙", vod_area="中国大陆", vod_lang="国语",
               vod_sub="<i>别名</i>", vod_total=999, vod_isend=1)
    searched = environment.client.get("/api/online/catalog/search", params={
        "source_id": environment.source["id"], "q": "合成"})
    assert searched.status_code == 200
    item = searched.json()["items"][1]
    expected = {"category": "国产剧", "category_id": "31", "parent_category_id": "30",
                "remarks": "更新至第2集", "genres": ["剧情", "都市"], "actors": ["张三", "李四"],
                "directors": ["导演甲", "导演乙"], "area": "中国大陆", "language": "国语", "subtitle": "别名"}
    assert {key: item[key] for key in expected} == expected
    params = {"source_id": environment.source["id"], "external_id": "show-1"}
    first = environment.client.get("/api/online/catalog/detail", params=params).json()
    assert {key: first[key] for key in expected} == expected
    assert [len(line["entries"]) for line in first["lines"]] == [2, 2]
    row.update(type_id=71, type_id_1=58, vod_actor="不同演员", vod_remarks="完结")
    second = environment.client.get("/api/online/catalog/detail", params=params).json()
    assert second["item"] == first["item"]
    assert second["lines"] == first["lines"]
    assert "cdn.example" not in str(second) and "vod_play_url" not in str(second)


def test_bad_optional_metadata_does_not_hide_playable_entries(environment):
    environment.movie.update(vod_actor={"url": "https://private.invalid/secret"},
                             vod_director=123, vod_class=["<b>剧情</b>", {}, None],
                             vod_area=None, vod_lang=["zh"], vod_sub="x" * 20000,
                             vod_remarks=["bad"], type_id=["bad"], type_id_1={})
    response = environment.client.get("/api/online/catalog/detail", params={
        "source_id": environment.source["id"], "external_id": "movie-1"})
    assert response.status_code == 200
    item = response.json()
    assert item["actors"] == item["directors"] == []
    assert item["genres"] == ["剧情"]
    assert item["category_id"] == item["parent_category_id"] == item["remarks"] == ""
    assert item["area"] == item["language"] == item["subtitle"] == ""
    assert all(line["entries"][0]["supported"] for line in item["lines"])
    assert "private.invalid" not in response.text


def test_metadata_name_lists_have_entry_and_text_limits(environment):
    names = [str(value) + "x" * 200 for value in range(100)]
    environment.movie.update(vod_actor=names, vod_director=names, vod_class=names)
    response = environment.client.get("/api/online/catalog/detail", params={
        "source_id": environment.source["id"], "external_id": "movie-1"})
    assert response.status_code == 200
    item = response.json()
    for field, count, limit in (("actors", 24, 80), ("directors", 12, 80), ("genres", 16, 64)):
        assert len(item[field]) == count
        assert all(len(value) <= limit for value in item[field])


@pytest.mark.parametrize("url,options,provider,supported", [
    ("https://bfzyapi.com/api.php/provide/vod/", {}, "maccms", True),
    ("https://bfzyapi.com/api.php/provide/vod", {}, "maccms", True),
    ("https://BFZYAPI.COM:443/api.php/provide/vod/", {"detail_action": "detail"}, "maccms", True),
    ("http://bfzyapi.com/api.php/provide/vod/", {}, "maccms", False),
    ("https://bfzyapi.com:444/api.php/provide/vod/", {}, "maccms", False),
    ("https://bfzyapi.com.evil.invalid/api.php/provide/vod/", {}, "maccms", False),
    ("https://bfzyapi.com/other", {}, "maccms", False),
    ("https://bfzyapi.com/api.php/provide/vod//", {}, "maccms", False),
    ("https://bfzyapi.com/api.php/provide/vod/?ac=detail", {}, "maccms", False),
    ("https://bfzyapi.com/api.php/provide/vod/#fragment", {}, "maccms", False),
    ("https://user:password@bfzyapi.com/api.php/provide/vod/", {}, "maccms", False),
    ("https://bfzyapi.com/api.php/provide/vod/\n", {}, "maccms", False),
    ("https://bfzyapi.com/api.php/provide/vod/", {"detail_action": "list"}, "maccms", False),
    ("https://bfzyapi.com/api.php/provide/vod/", {}, "open_catalog", False),
])
def test_year_capability_is_specific_to_the_verified_endpoint(url, options, provider, supported):
    assert maccms.supports_year_filter({"provider": provider, "provider_options": options, "api_base_url": url}) is supported


def baofeng_source():
    return online.create_online_source({"name": "已核对年份的隔离接口", "provider": "maccms",
        "api_base_url": "https://bfzyapi.com/api.php/provide/vod/", "allowed_hosts": ["cdn.example"], "enabled": True})


def test_sources_declare_year_support_only_for_verified_endpoint_without_network(environment):
    checked = baofeng_source()
    response = environment.client.get("/api/online/catalog/sources")
    assert response.status_code == 200
    values = {item["id"]: item["capabilities"]["year_filter"] for item in response.json()["items"]}
    assert values[checked["id"]] is True
    assert values[environment.source["id"]] is False
    assert environment.requests == []


@pytest.mark.parametrize("year", ["1000", "2026", "9999"])
def test_year_is_forwarded_to_the_real_upstream_query(environment, monkeypatch, year):
    source = baofeng_source()
    calls = []
    def fetch(_source, params):
        calls.append(params)
        return {"list": [{**environment.movie, "vod_year": year}], "total": 7,
                "class": [{"type_id": 71, "type_name": "都市脑洞", "type_pid": 0}]}
    monkeypatch.setattr(maccms, "fetch", fetch)
    response = environment.client.get("/api/online/catalog/browse", params={
        "source_id": source["id"], "category_id": "71", "year": year})
    assert response.status_code == 200
    assert response.json()["total"] == 7 and response.json()["items"][0]["year"] == year
    assert calls == [{"ac": "detail", "pg": "1", "t": "71", "year": year}]


@pytest.mark.parametrize("year", ["99", "0000", "20260", "٢٠٢٦", "2026\n", " 2026", "2e26", "-001"])
def test_year_validation_rejects_bad_values_before_provider_io(environment, year):
    response = environment.client.get("/api/online/catalog/browse", params={
        "source_id": environment.source["id"], "year": year})
    assert response.status_code == 422
    assert environment.requests == []


def test_unknown_sources_and_search_never_silently_ignore_year(environment):
    response = environment.client.get("/api/online/catalog/browse", params={
        "source_id": environment.source["id"], "year": "2026"})
    assert response.status_code == 422 and response.json()["code"] == "online_year_filter_unsupported"
    source = online.create_online_source({"name": "开放样例", "provider": "open_catalog",
        "api_base_url": "https://www.bigbuckbunny.org/", "allowed_hosts": ["video.blender.org"], "enabled": True})
    assert environment.client.get("/api/online/catalog/browse", params={"source_id": source["id"], "year": "2026"}).status_code == 422
    assert environment.client.get("/api/online/catalog/search", params={
        "source_id": environment.source["id"], "q": "合成", "year": "2026"}).status_code == 422
    assert environment.requests == []


def test_duplicate_year_parameters_are_rejected_without_network(environment):
    response = environment.client.get("/api/online/catalog/browse", params=[
        ("source_id", environment.source["id"]), ("year", "2026"), ("year", "2025")])
    assert response.status_code == 422
    assert environment.requests == []
