"""Discovery uses the same bounded provider and access boundary as search."""

from app.online import maccms
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
