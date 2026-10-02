"""AI contracts on isolated libraries. Mock outputs test integration, not model quality."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app import config, library_paths, store, tmdb
from app.ai import match, search
from app.ai import client as ai_client, settings as ai_settings
from app.ai.client import AiUnavailable
from app.main import app
from app.metadata import chain, state
from app.metadata.base import Candidate

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(store._base, "DB_PATH", str(tmp_path / "ai.db"))
    store.init_db()
    library_paths.invalidate_cache()
    state.reset()
    for name in ai_settings.DEFAULTS:
        monkeypatch.delenv("AI_" + name.upper(), raising=False)
    ai_client.clear_cache()
    yield
    library_paths.invalidate_cache()
    ai_client.clear_cache()


def add_library(tmp_path, kind="movie"):
    root = tmp_path / kind
    root.mkdir(exist_ok=True)
    return store.create_library(name=kind, kind=kind, path=str(root))


def add_movie(lid=1, title="喜剧甲", **fields):
    mid = store.upsert_movie_by_path(f"{title} (1995)/{title}.1995.mkv", library_id=lid)
    store.update_movie_meta(mid, title=title, year=1995, **fields)
    return mid


def mocked(monkeypatch, filters, **extra):
    calls = []
    def request(task, payload, instruction):
        calls.append((task, payload))
        return {"filters": filters, **extra}
    monkeypatch.setattr(search, "call_json", request)
    return calls


def parse(q="没看过的90年代香港喜剧，TMDB7分以上", **kwargs):
    return client.post("/api/ai/search", json={"q": q, **kwargs})


def query_params(filters):
    return [(key, str(item)) for key, value in filters.items() if value is not None
            for item in (value if isinstance(value, list) else [value])]


def test_movie_interpretation_reuses_scoped_query_and_does_not_write(monkeypatch, tmp_path):
    own = add_library(tmp_path)
    wanted = add_movie(own["id"], "香港喜剧", genres=["喜剧"], origin_countries=["HK"],
                       tmdb_rating=8, watched=0)
    add_movie(1, "库外香港喜剧", genres=["喜剧"], origin_countries=["HK"], tmdb_rating=8)
    add_movie(own["id"], "已看喜剧", genres=["喜剧"], origin_countries=["HK"],
              tmdb_rating=8, watched=1)
    before = store.get_movie(wanted)
    calls = mocked(monkeypatch, {"decade": [1990], "country": ["HK"], "genre": ["喜剧"],
                                 "watched": 0, "min_rating": 7})
    response = parse(media_library_id=own["media_library_id"])
    assert response.status_code == 200, response.text
    proposal = response.json()
    assert proposal["ok"], proposal
    params = query_params(proposal["filters"]) + [("media_library", own["media_library_id"])]
    result = client.get("/api/search", params=params).json()
    assert [m["id"] for m in result["items"]] == [wanted]
    assert store.get_movie(wanted) == before
    payload = json.dumps(calls[0][1], ensure_ascii=False)
    assert "库外香港喜剧" not in payload and "file_path" not in payload


@pytest.mark.parametrize("q,filters", [
    ("2020年代的科幻", {"decade": [2020], "genre": ["科幻"]}),
    ("日本动画", {"country": ["JP"], "genre": ["动画"]}),
    ("周星驰的电影", {"q": "周星驰"}),
    ("自评8分以上，按评分排列", {"rating_source": "custom", "min_rating": 8, "sort": "rating"}),
    ("1994年上映的影片", {"year": [1994]}),
])
def test_fixed_chinese_contract_examples(monkeypatch, q, filters):
    mocked(monkeypatch, filters)
    result = parse(q).json()
    assert result["ok"], result
    assert all(result["filters"][key] == value for key, value in filters.items())


def test_scope_vocabulary_is_local_and_unknown_scope_never_calls_model(monkeypatch, tmp_path):
    own = add_library(tmp_path)
    add_movie(1, "外库", tags=["库外标签"])
    add_movie(own["id"], "内库", tags=["库内标签"])
    calls = mocked(monkeypatch, {"q": "内库"})
    assert parse(media_library_id=own["media_library_id"]).json()["ok"]
    assert calls[0][1]["vocabulary"]["tags"] == ["库内标签"]
    assert parse(media_library_id=999999).json()["code"] == "empty_scope"
    assert parse(library_id=999999).json()["code"] == "empty_scope"
    assert len(calls) == 1


@pytest.mark.parametrize("filters", [
    {"q": "星际", "library_id": 2}, {"sql": "SELECT * FROM movies"},
    {"min_rating": 11}, {"watched": True}, {"year": ["1990"]},
    {"decade": [1995]}, {"country": ["香港"]}, {"region": ["火星"]},
    {"genre": "喜剧"}, {"tag": ["a"] * 13}, {"q": "x" * 201},
    {"sort": "random()"}, {"runtime_max": 120},
])
def test_untrusted_model_conditions_fail_closed(monkeypatch, filters):
    mocked(monkeypatch, filters)
    result = parse().json()
    assert result["ok"] is False and result["code"] == "invalid_result"
    assert "filters" not in result


def test_unsupported_intent_explained_and_no_silent_query_expansion(monkeypatch):
    mocked(monkeypatch, {"genre": ["喜剧"]}, unsupported=["两小时以内", "适合儿童"])
    result = parse("两小时以内适合儿童的喜剧").json()
    assert "暂不支持：两小时以内" in result["warnings"]
    assert "暂不支持：适合儿童" in result["warnings"]
    mocked(monkeypatch, {}, unsupported=["推荐一部类似某电影的影片"])
    assert parse().json()["code"] == "cannot_interpret"


def test_movie_keyword_sort_explained(monkeypatch):
    mocked(monkeypatch, {"q": "周星驰", "sort": "rating"})
    assert any("相关度" in w for w in parse().json()["warnings"])


@pytest.mark.parametrize("filters", [{"sort": "year", "order": "desc"}, {"order": "asc"}])
def test_sort_only_request_is_a_valid_existing_wall_operation(monkeypatch, filters):
    mocked(monkeypatch, filters)
    proposal = parse("按年份倒序").json()
    assert proposal["ok"]
    assert all(proposal["filters"][key] == value for key, value in filters.items())


def test_tv_search_reuses_actual_tv_filters(monkeypatch, tmp_path):
    lib = add_library(tmp_path, "tv")
    sid = store.upsert_show(lib["id"], "测试日本剧", 2020)
    store.update_show_meta(sid, genres=["剧情"], origin_countries=["JP"], tmdb_rating=9, status="Ended")
    mocked(monkeypatch, {"country": ["JP"], "status": ["ended"], "min_rating": 8})
    result = parse("高分完结日剧", kind="tv", media_library_id=lib["media_library_id"]).json()
    assert result["ok"], result
    params = query_params(result["filters"]) + [("media_library", lib["media_library_id"])]
    actual = client.get("/api/tv/shows", params=params)
    assert actual.status_code == 200, actual.text
    assert [m["id"] for m in actual.json()["items"]] == [sid]
    mocked(monkeypatch, {"q": "测试", "rating_source": "douban"})
    assert parse(kind="tv").json()["code"] == "unsupported_filter"
    mocked(monkeypatch, {"status": ["ended"]})
    assert parse().json()["code"] == "unsupported_filter"


def test_unavailable_returns_safe_fallback(monkeypatch):
    def fail(*args):
        raise AiUnavailable("timeout", "模型响应超时，可以使用普通搜索")
    monkeypatch.setattr(search, "call_json", fail)
    result = parse().json()
    assert result == {"ok": False, "code": "timeout", "message": "模型响应超时，可以使用普通搜索"}
    assert client.get("/api/search?q=test").status_code == 200


def fake_match(monkeypatch, *, rank=None, candidates=None):
    calls, provider_calls = [], []
    def request(task, payload, instruction):
        calls.append((task, payload))
        if task == "match_identity":
            return {"title": "清理后的标题", "year": 1995, "reason": "提取已有标题"}
        if isinstance(rank, Exception):
            raise rank
        return rank if rank is not None else {
            "candidates": [{"key": "c0", "reason": "标题相近，但年份不同，需人工核对。"}],
            "summary": "请核对这部影片。"}
    def source(q, year, kind, library_id, limit):
        provider_calls.append((q, year, kind, library_id))
        return candidates if candidates is not None else [
            Candidate(title="原始标题", original_title="Original", year=2005,
                      tmdb_id=55, source="tmdb", source_id="55")]
    monkeypatch.setattr(match, "call_json", request)
    monkeypatch.setattr(chain, "search", source)
    return calls, provider_calls


def test_match_uses_real_candidates_and_row_scope_without_binding(monkeypatch, tmp_path):
    lib = add_library(tmp_path)
    mid = add_movie(lib["id"], "原始标题", tmdb_id=12, title_auto=0)
    before = store.get_movie(mid)
    calls, sources = fake_match(monkeypatch)
    result = client.post("/api/ai/match", json={"id": mid}).json()
    assert result["ok"], result
    assert result["candidates"][0]["tmdb_id"] == 55
    assert "年份冲突" in result["candidates"][0]["reason"]
    assert all(s[3] == lib["id"] for s in sources)
    assert all(s[2] == "movie" for s in sources)
    assert len(sources) == 2 and len(calls) == 2
    assert store.get_movie(mid) == before  # manual binding/title remains untouched
    serialized = json.dumps(calls, ensure_ascii=False)
    assert str(tmp_path) not in serialized and "file_path" not in serialized
    assert not list((tmp_path / "movie").iterdir())


def test_tv_match_preserves_show_episode_and_binding_rules(monkeypatch, tmp_path):
    lib = add_library(tmp_path, "tv")
    sid = store.upsert_show(lib["id"], "示例剧", 1995)
    eid = store.upsert_episode(sid, lib["id"], "Show/Season 02/S02E01.mkv", season=2, episode=1)
    store.update_show_meta(sid, title_auto=0, tmdb_id=111)
    with store._base._conn() as conn:
        conn.execute("INSERT INTO tv_directory_bindings(library_id,path,show_id,season,override_season) "
                     "VALUES(?,?,?,?,1)", (lib["id"], "Show/Season 02", sid, 2))
    rules = store.list_tv_bindings(show_id=sid)
    assert rules
    before_show, before_ep = store.get_show(sid), store.get_episode(eid)
    _, sources = fake_match(monkeypatch)
    result = client.post("/api/ai/match", json={"id": sid, "kind": "tv"}).json()
    assert result["ok"] and any("不会调整" in w for w in result["warnings"])
    assert all(s[2:4] == ("tv", lib["id"]) for s in sources)
    assert store.get_show(sid) == before_show and store.get_episode(eid) == before_ep
    assert store.list_tv_bindings(show_id=sid) == rules


def test_ai_rewrite_cannot_crowd_out_original_name_candidates(monkeypatch):
    mid = add_movie(title="原始标题")
    calls = []
    def model(task, payload, instruction):
        if task == "match_identity":
            return {"title": "误改名称", "year": 1995}
        return {"candidates": [{"key": c["key"], "reason": "待人工核对"} for c in payload["candidates"]]}
    def source(term, year, kind, library_id, limit):
        calls.append(term)
        offset = 100 if term == "原始标题" else 200
        return [Candidate(title=f"{term}{i}", source="tmdb", source_id=str(offset+i),
                          tmdb_id=offset+i, year=1995) for i in range(10)]
    monkeypatch.setattr(match, "call_json", model)
    monkeypatch.setattr(chain, "search", source)
    result = client.post("/api/ai/match", json={"id": mid}).json()
    ids = [candidate["tmdb_id"] for candidate in result["candidates"]]
    assert calls == ["原始标题", "误改名称"]
    assert ids == [100, 200, 101, 201, 102, 202, 103, 203, 104, 204]


@pytest.mark.parametrize("title,path,stored,proposed,expected,conflict", [
    ("原始标题", "原始标题.2160p.mkv", None, 2160, None, False),
    ("原始标题", "原始标题.1920x1080.mkv", None, 1920, None, False),
    ("原始标题", "收藏 (2024)/作品/原始标题.1994.mkv", 1994, 2024, 1994, False),
    ("原始标题", "收藏 (2024)/原始标题.mkv", None, 2024, None, False),
    ("原始标题", "原始标题 (1994)/原始标题.mkv", None, None, 1994, False),
    ("原始标题", "原始标题.1994.1080p.mkv", None, None, 1994, False),
    ("原始标题", "原始标题.1994.1080p.mkv", 1995, 1994, 1995, True),
    ("原始标题", "原始标题 (1995)/原始标题.1994.mkv", None, 1994, 1994, True),
    ("原始标题", "原始标题.mkv", None, 1994, None, False),
    ("1917", "1917.1080p.mkv", None, 1917, None, False),
])
def test_match_year_comes_from_identity_evidence_not_model_or_collection_folders(
        monkeypatch, title, path, stored, proposed, expected, conflict):
    mid = store.upsert_movie_by_path(path, library_id=1)
    store.update_movie_meta(mid, title=title, year=stored)
    calls = []
    def model(task, payload, instruction):
        return {"title": title, "year": proposed}
    def source(term, year, kind, library_id, limit):
        calls.append(year)
        return []
    monkeypatch.setattr(match, "call_json", model)
    monkeypatch.setattr(chain, "search", source)
    result = client.post("/api/ai/match", json={"id": mid}).json()
    assert result["ok"] and result["year"] == expected and calls == [expected]
    assert any("年份线索冲突" in warning for warning in result["warnings"]) is conflict
    assert store.get_movie(mid)["year"] == stored


def test_directory_binding_candidates_explain_lock_but_allow_same_tmdb(monkeypatch, tmp_path):
    lib = add_library(tmp_path, "tv")
    sid = store.upsert_show(lib["id"], "示例剧", 1995)
    store.update_show_meta(sid, tmdb_id=111)
    with store._base._conn() as conn:
        conn.execute("INSERT INTO tv_directory_bindings(library_id,path,show_id,season,override_season) "
                     "VALUES(?,?,?,?,1)", (lib["id"], "示例剧", sid, 1))
    fake_match(monkeypatch, candidates=[
        Candidate(title="示例剧", source="tmdb", source_id="111", tmdb_id=111),
        Candidate(title="其他剧", source="tmdb", source_id="222", tmdb_id=222),
        Candidate(title="外源剧", source="wikidata", source_id="Q333"),
    ], rank={"candidates": [{"key": f"c{i}", "reason": "请核对"} for i in range(3)]})
    result = client.post("/api/ai/match", json={"id": sid, "kind": "tv"}).json()
    assert result["ok"] and [c["bindable"] for c in result["candidates"]] == [True, False, False]
    assert all("归属与季号" in c["bind_reason"] for c in result["candidates"][1:])
    assert any("归属与季号" in warning for warning in result["warnings"])
    assert store.get_show_meta(sid)["tmdb_id"] == 111


@pytest.mark.parametrize("rank", [
    {"candidates": [{"key": "c99", "reason": "我确信"}]},
    {"candidates": [{"key": "c0", "tmdb_id": 999, "reason": "更换ID"}]},
    {"candidates": [{"key": "c0", "reason": "一"}, {"key": "c0", "reason": "二"}]},
    AiUnavailable("daily_limit", "今日次数已达上限"),
])
def test_bad_ranking_falls_back_to_real_unranked_candidates(monkeypatch, rank):
    mid = add_movie()
    fake_match(monkeypatch, rank=rank)
    result = client.post("/api/ai/match", json={"id": mid}).json()
    assert result["ok"] and result["candidates"][0]["tmdb_id"] == 55
    assert "尚未完成" in result["candidates"][0]["reason"]
    assert store.get_movie(mid)["tmdb_id"] is None


def test_no_candidates_gives_next_step_without_second_model_call(monkeypatch):
    mid = add_movie()
    calls, _ = fake_match(monkeypatch, candidates=[])
    result = client.post("/api/ai/match", json={"id": mid}).json()
    assert result["ok"] and result["candidates"] == []
    assert "检查资料来源" in result["summary"] and len(calls) == 1


def test_invalid_target_and_payload_do_not_call_model(monkeypatch):
    def forbidden(*args):
        raise AssertionError("must not call model")
    monkeypatch.setattr(match, "call_json", forbidden)
    assert client.post("/api/ai/match", json={"id": 9999}).status_code == 404
    assert client.post("/api/ai/match", json={"id": 1, "library_id": 2}).status_code == 422
    assert parse(" ").status_code == 422
    assert parse("a" * 501).status_code == 422
    assert parse(kind="episode").status_code == 422


def test_paid_routes_use_existing_application_auth(monkeypatch):
    monkeypatch.setattr(config, "effective_jzmedia_token", lambda: "instance-token")
    for path, body in (("search", {"q": "喜剧"}), ("match", {"id": 1}), ("check", {})):
        assert client.post("/api/ai/" + path, json=body).status_code == 401
    assert client.get("/api/search?q=喜剧").status_code == 200


def test_provider_chain_respects_tv_kind_and_configured_order(monkeypatch):
    monkeypatch.setattr(config, "effective_tmdb_api_key", lambda: "configured")
    monkeypatch.setattr(chain, "chain_for", lambda library_id=None: ["tmdb"])
    monkeypatch.setattr(tmdb, "search_tv", lambda q, year: [
        {"id": 321, "name": "剧名", "original_name": "Show", "first_air_date": "2020-01-01"}])
    def no_movies(*args):
        raise AssertionError("TV must not use movie search")
    monkeypatch.setattr(tmdb, "search_movie", no_movies)
    result = chain.search("剧名", 2020, "tv", library_id=1)
    assert result[0].title == "剧名" and result[0].year == 2020 and result[0].tmdb_id == 321


def test_full_search_http_contract_and_invalid_schema_retry(monkeypatch):
    """Exercise real config/client/router together, including invalid business-cache eviction."""
    ai_settings.update({"enabled": True, "api_key": "synthetic-test-key"})
    responses = [{"filters": {"q": "喜剧", "library_id": 99}},
                 {"filters": {"genre": ["喜剧"]}, "summary": "按类型筛选"}]
    requests = []
    def provider(request):
        body = json.loads(request.content)
        requests.append(body)
        assert body["thinking"] == {"type": "disabled"}
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps(responses.pop(0), ensure_ascii=False)}}],
            "usage": {"prompt_tokens": 42, "completion_tokens": 12}})
    monkeypatch.setattr(ai_client, "_http_client", lambda config: httpx.AsyncClient(
        transport=httpx.MockTransport(provider)))
    assert parse().json()["code"] == "invalid_result"
    assert parse().json()["ok"]
    assert parse().json()["ok"]  # validated successful output is reused
    assert len(requests) == 2
    assert ai_settings.usage()["requests"] == 2
    assert ai_settings.usage()["output_tokens"] == 24


def test_full_match_http_contract_and_source_candidates(monkeypatch):
    ai_settings.update({"enabled": True, "api_key": "synthetic-test-key"})
    mid = add_movie()
    monkeypatch.setattr(chain, "search", lambda *args, **kwargs: [
        Candidate(title="喜剧甲", year=1995, source="tmdb", source_id="987", tmdb_id=987)])
    def provider(request):
        payload = json.loads(json.loads(request.content)["messages"][1]["content"])
        answer = ({"candidates": [{"key": "c0", "reason": "标题和年份均相同"}]}
                  if "candidates" in payload else {"title": "喜剧甲", "year": 1995})
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(answer)}}]})
    monkeypatch.setattr(ai_client, "_http_client", lambda config: httpx.AsyncClient(
        transport=httpx.MockTransport(provider)))
    result = client.post("/api/ai/match", json={"id": mid}).json()
    assert result["ok"] and result["candidates"][0]["tmdb_id"] == 987
    assert result["candidates"][0]["bindable"]
    assert ai_settings.usage()["requests"] == 2
    assert store.get_movie(mid)["tmdb_id"] is None


def test_index_only_candidate_is_not_bindable(monkeypatch):
    mid = add_movie()
    fake_match(monkeypatch, candidates=[Candidate(title="只含索引", source="imdb",
                                                  source_id="tt0001", year=1995)])
    result = client.post("/api/ai/match", json={"id": mid}).json()
    assert result["ok"] and result["candidates"][0]["bindable"] is False


def test_disabled_does_not_read_sources_or_contact_model(monkeypatch):
    mid = add_movie()
    def forbidden(*args, **kwargs):
        raise AssertionError("disabled assistant must not contact any provider")
    monkeypatch.setattr(ai_client, "_http_client", forbidden)
    monkeypatch.setattr(chain, "search", forbidden)
    assert parse().json()["code"] == "disabled"
    assert client.post("/api/ai/match", json={"id": mid}).json()["code"] == "disabled"
    assert ai_settings.usage()["requests"] == 0
