"""Discovery uses the same bounded provider and access boundary as search."""

from concurrent.futures import ThreadPoolExecutor
import threading
import time

import pytest

from app.network.guard import (NetworkOperation, NetworkOperationError,
                               current_operation, operation_context)
from app.online import maccms
from app.online.providers import ProviderError
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
    assert body["categories"] == [{"id": "7", "name": "动作片"}, {"id": "8", "name": "国产剧"}]
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
    assert response.json()["categories"] == [{"id": "1", "name": "电影"}]
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
