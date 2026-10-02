from fastapi.testclient import TestClient

from app.main import app
from app.metadata import chain


def test_search_uses_saved_library_order_and_kind(monkeypatch):
    monkeypatch.setattr("app.routers.metadata.store.get_library",
                        lambda lid: {"id": lid, "kind": "tv"})
    monkeypatch.setattr(chain, "chain_for", lambda lid: ["bgm", "local"])
    called = []
    monkeypatch.setattr(chain.state, "available", lambda name: True)
    monkeypatch.setattr(chain.state, "note_ok", lambda name: None)

    def bangumi(term, year, kind, limit):
        called.append((term, kind))
        return [chain.Candidate(title="测试剧", source="bgm", source_id="1")]

    monkeypatch.setitem(chain._SEARCHERS, "bgm", bangumi)
    monkeypatch.setitem(chain._SEARCHERS, "local",
                        lambda *args: (_ for _ in ()).throw(AssertionError("must stop after first result")))
    response = TestClient(app).get("/api/metadata/test-search", params={"library": 9, "q": " 测试剧 "})
    assert response.status_code == 200
    result = response.json()
    assert called == [("测试剧", "tv")]
    assert result["library_id"] == 9
    assert result["source"] == "bgm"
    assert result["chain"] == ["bgm", "local"]
    assert result["items"][0]["title"] == "测试剧"


def test_search_empty_and_invalid_library_are_distinct(monkeypatch):
    monkeypatch.setattr("app.routers.metadata.store.get_library",
                        lambda lid: {"id": lid, "kind": "movie"} if lid == 1 else None)
    monkeypatch.setattr(chain, "search", lambda *args, **kwargs: [])
    monkeypatch.setattr(chain, "chain_for", lambda lid: ["local"])
    client = TestClient(app)
    result = client.get("/api/metadata/test-search", params={"library": 1, "q": "无结果"}).json()
    assert result["items"] == [] and result["source"] is None
    assert client.get("/api/metadata/test-search", params={"library": 99, "q": "测试"}).status_code == 404
    assert client.get("/api/metadata/test-search", params={"library": 1, "q": "  "}).status_code == 422
    assert client.get("/api/metadata/test-search", params={"q": "测试"}).status_code == 422
