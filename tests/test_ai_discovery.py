"""Read-only result search with isolated model, records, device scopes and public catalogue."""
from datetime import date
import json

from fastapi.testclient import TestClient
import pytest

from app import library_paths, store, viewing
from app.ai import discovery, discovery_public, discovery_sessions
from app.ai.client import AiUnavailable
from app.main import app

URL = "/api/ai/search/results"


@pytest.fixture
def web(tmp_path, monkeypatch):
    monkeypatch.setattr(store._base, "DB_PATH", str(tmp_path / "discover.db"))
    store.init_db()
    library_paths.invalidate_cache()
    yield TestClient(app)
    library_paths.invalidate_cache()


def movie(title, **fields):
    row = store.upsert_movie_by_path("private-nas/path/" + title + ".mkv", library_id=1)
    store.update_movie_meta(row, title=title, **fields)
    return row


def provider(monkeypatch, *steps):
    calls = []
    def answer(task, payload, instruction):
        assert len(calls) < len(steps), (task, calls)
        calls.append((task, payload))
        step = steps[len(calls) - 1]
        return step(payload) if callable(step) else step
    monkeypatch.setattr(discovery, "call_json", answer)
    return calls


def run(web, q, **params):
    resp = web.post(URL, json={"q": q, **params})
    assert resp.status_code == 200, resp.text
    assert resp.headers["cache-control"] == "no-store"
    return resp.json()


def test_top_n_real_ranking_refresh_and_missing_scores(web, monkeypatch):
    year = date.today().year
    ids = [movie(f"电影{i}", year=year, tmdb_rating=i / 2 + 2) for i in range(13)]
    movie("缺少评分", year=year)
    movie("往年冠军", year=year-1, tmdb_rating=10)
    calls = provider(monkeypatch, {"intent": "ranking", "limit": 10, "constraints": {"years": [year]},
                                   "clues": {"terms": ["今年", "评分最高", "Top 10"]}, "preferences": ["高分"]})
    got = run(web, "我想看今年评分最高的10部电影")
    assert [r["id"] for r in got["items"]] == ids[::-1][:10]
    assert str(year) in got["summary"] and "TMDB 评分从高到低" in got["summary"]
    assert len(calls) == 1 and calls[0][1]["today"] == date.today().isoformat()
    assert got["excluded_unknown"] == 1 and all(r["in_library"] for r in got["items"])
    assert "private-nas" not in json.dumps(got, ensure_ascii=False)
    restored = web.post(URL + "/refresh", json={"session_id": got["session_id"]}).json()
    assert [r["reason"] for r in restored["items"]] == [r["reason"] for r in got["items"]]
    assert restored["items"][0]["detail_path"] == f"/m/{ids[-1]}"


def test_identify_uses_scene_before_asking_about_an_unverified_guess(web, monkeypatch):
    title = movie("孤岛信号", overview="守岛的姑娘每晚打开电台，寻找没能返航的父亲。")
    plan = {"intent": "identify", "constraints": {"kinds": ["movie"]},
            "clues": {"titles": ["猜测译名"], "terms": ["失联水手"]}, "clarification": "会不会是《猜测译名》？"}
    provider(monkeypatch, plan, {"choices": [{"key": "c0", "fields": ["overview"], "reason": "她用电台寻找父亲。"}]})
    result = run(web, "记得一位女儿在岛上用无线电呼叫海上的父亲，叫什么？")
    assert result["status"] == "results" and [r["id"] for r in result["items"]] == [title]
    assert result["items"][0]["evidence"] == ["守岛的姑娘每晚打开电台，寻找没能返航的父亲。"]
    provider(monkeypatch, plan, {"choices": [{"key": "c0", "fields": ["made_up"], "reason": "无依据"}]})
    assert run(web, "找岛上的电台")["code"] == "invalid_result"


def test_identification_and_semantic_recommendations_are_grounded(web, monkeypatch):
    movie("少林足球", overview="球队组队参加足球联赛", person_names="周星驰", tmdb_rating=7.6)
    quiet = movie("井边", overview="一对老友在旧城重逢，走过平淡的日常生活。")
    movie("已看文艺", overview="日常生活", watched=1)
    movie("在看文艺", overview="日常生活")
    started = store.upsert_movie_by_path("private-nas/path/在看文艺.mkv", library_id=1)
    store.save_progress(started, 300, 3600)
    def select(title, evidence):
        def response(payload):
            row = next(r for r in payload["candidates"] if r["title"] == title)
            return {"choices": [{"key": row["key"], "evidence": [evidence], "reason": "作品资料：" + evidence}]}
        return response
    calls = provider(monkeypatch, {"intent": "identify", "clues": {"titles": ["少林足球"], "people": ["周星驰"]}},
                     select("少林足球", "周星驰"),
                     {"intent": "recommend", "constraints": {"watch_state": "unseen"},
                      "preferences": ["文艺"], "clues": {"terms": ["日常生活"]}}, select("井边", "旧城重逢"))
    first = run(web, "周星驰扮演足球运动员的那部电影")
    assert first["items"][0]["title"] == "少林足球"
    restored = web.post(URL + "/refresh", json={"session_id": first["session_id"]}).json()
    assert restored["items"][0]["evidence"] == ["周星驰"]
    result = run(web, "帮我推荐一下我没看过的文艺片")
    assert [r["id"] for r in result["items"]] == [quiet]
    sent = json.dumps(calls[-1][1], ensure_ascii=False)
    assert all(text not in sent for text in ("已看文艺", "在看文艺", "private-nas", "detail_path",
                                              "library_id", "watch_state", "tmdb_id"))


def test_followups_replace_more_compare_and_device_scope(web, monkeypatch):
    ids = [movie(f"片目{i}", year=2024, tmdb_rating=9 - i / 10,
                 overview="海岸散步" if i == 0 else "山中寻亲") for i in range(7)]
    movie("其他年份", year=2023, tmdb_rating=10)
    calls = provider(monkeypatch,
        {"intent": "ranking", "limit": 3, "constraints": {"years": [2024]}},
        {"intent": "ranking", "action": "replace", "positions": [2]},
        {"intent": "ranking", "action": "compare", "positions": [1, 3]},
        {"entries": [{"key": "c0", "evidence": ["海岸散步"], "reason": "更适合喜欢海岸散步题材的观众"},
                     {"key": "c1", "evidence": ["山中寻亲"], "reason": "更偏向山中寻亲的旅途"}],
         "conclusion": "更喜欢海岸散步就选第一部。"},
        {"intent": "ranking", "action": "more"})
    first = run(web, "2024年评分最高的三部")
    sid = first["session_id"]
    stolen = TestClient(app).post(URL + "/refresh", json={"session_id": sid})
    assert stolen.json()["code"] == "session_expired"
    assert run(web, "换一批", media_library_id=999, session_id=sid)["code"] == "session_expired"
    second = run(web, "只换第二部", session_id=sid)
    assert [r["id"] for r in second["items"]] == [ids[0], ids[3], ids[2]]
    third = run(web, "比较第一部和第三部，想看看海边的风景", session_id=second["session_id"])
    assert third["status"] == "comparison" and "海岸散步" in third["summary"]
    assert [r["id"] for r in third["items"]] == [ids[0], ids[3], ids[2]]
    last = run(web, "再换一批", session_id=third["session_id"])
    assert [r["id"] for r in last["items"]] == ids[4:]
    assert len(calls) == 5


def test_wall_conditions_and_kind_cannot_be_broadened(web, monkeypatch, tmp_path):
    movie("高分", genres=["喜剧"], tmdb_rating=9)
    movie("低分", genres=["喜剧"], tmdb_rating=6)
    movie("爱情", genres=["爱情"], tmdb_rating=9.8)
    (tmp_path / "shows").mkdir()
    tv = store.create_library(name="剧集", path=str(tmp_path / "shows"), kind="tv")
    sid = store.upsert_show(tv["id"], "剧集", 2024)
    store.update_show_meta(sid, tmdb_rating=10, genres=["喜剧"])
    provider(monkeypatch, {"intent": "ranking", "kinds": ["tv", "movie"], "constraints": {"min_rating": 4}})
    result = run(web, "排一下", filters={"genre": ["喜剧"], "min_rating": 8}, kinds=["movie"])
    assert [r["title"] for r in result["items"]] == ["高分"]


def test_untrusted_rank_falls_back_only_to_actual_matches(web, monkeypatch):
    movie("旧城", tags=["生活"], overview="旧城有个码头")
    movie("库内无关影片", overview="极速追击")
    calls = provider(monkeypatch, {"intent": "recommend", "clues": {"terms": ["生活"]}},
                     {"choices": [{"key": "c99", "evidence": ["伪造"]}]})
    result = run(web, "推荐有生活气息的")
    assert result["status"] == "partial" and [r["title"] for r in result["items"]] == ["旧城"]
    assert "未" in result["items"][0]["reason"]
    assert len(calls) == 2


def test_public_results_need_local_identity_and_watch_evidence(web, monkeypatch):
    local = movie("同名", tmdb_id=100, tmdb_rating=8)
    seen = movie("之前看过", tmdb_id=101, watched=1)
    assert seen != local
    public = [{"kind": "movie", "tmdb_id": i, "title": "同名", "rating": 9,
               "rating_source": "tmdb", "evidence": [], "external_url": f"https://www.themoviedb.org/movie/{i}"}
              for i in range(100, 103)]
    sources = []
    def remote(*args):
        sources.append(args)
        return {"items": public, "total": 3, "excluded_unknown": 0}
    monkeypatch.setattr(discovery_public, "discover_public", remote)
    provider(monkeypatch, {"intent": "ranking", "source": "library", "constraints": {"watch_state": "unseen"}},
             {"intent": "ranking", "source": "public"})
    result = run(web, "评分排名，只看没看过的", source="public")
    assert [r["id"] for r in result["items"]] == [local] and result["excluded_unknown"] == 1
    assert result["items"][0]["in_library"] and result["source"] == "public"
    local_result = run(web, "今年评分最高的", source="library")
    assert local_result["source"] == "library" and len(sources) == 1
    # A source switch keeps the entire prior plan without a new interpretation.
    monkeypatch.setenv("AI_ENABLED", "true")
    monkeypatch.setenv("AI_API_KEY", "isolated-provider-fixture")
    calls = provider(monkeypatch, {"intent": "ranking", "limit": 1, "order": "rating_asc",
                                   "constraints": {"years": [2025], "watch_state": "unseen"}})
    previous = run(web, "2025年没看过的电影，按评分从低到高，给我一部")
    switched = run(web, "按刚才的要求查公开片目", source="public", session_id=previous["session_id"], reuse_plan=True)
    assert switched["source"] == "public" and len(calls) == 1
    assert sources[-1][1]["years"] == [2025] and sources[-1][3] == "rating_asc"


def test_invalid_model_output_clarification_and_auth(web, monkeypatch):
    calls = provider(monkeypatch, {"intent": "search", "sql": "untrusted"},
                     {"intent": "search", "unsupported": ["导演是否曾获私人奖项"]})
    assert run(web, "找电影")["code"] == "invalid_result"
    question = run(web, "导演是否曾获私人奖项")
    assert question["status"] == "clarification" and not question["items"]
    assert len(calls) == 2
    store.set_setting("jzmedia_token", "discovery-token")
    assert web.post(URL, json={"q": "任意"}).status_code == 401
    assert len(calls) == 2


def test_children_and_stale_protection_prevent_returning_or_sending_candidates(web, monkeypatch):
    movie("隐藏线索", overview="隐私数据")
    assert web.post("/api/viewing/password", json={"new_password": "isolated-search-password"}).is_success
    assert web.post("/api/viewing/mode", json={"mode": "children"}).is_success
    calls = provider(monkeypatch)
    for url in (URL, "/api/ai/search"):
        result = web.post(url, json={"q": "公开推荐"})
        assert result.status_code == 403 and result.json()["code"] == "children_restricted"
    assert not calls
    assert web.post("/api/viewing/mode", json={"mode": "normal", "password": "isolated-search-password"}).is_success
    # A user locks/changes protection while the first provider call is in progress.
    def invalidate(payload):
        with store._base._conn() as c:
            viewing.invalidate_content_access(c)
        return {"intent": "recommend", "preferences": ["文艺"]}
    provider(monkeypatch, invalidate)
    result = web.post(URL, json={"q": "找片"})
    assert result.status_code == 403


def test_model_failure_and_expired_refresh_are_distinct(web, monkeypatch):
    def failure(_):
        raise AiUnavailable("timeout", "模型超时")
    provider(monkeypatch, failure)
    assert run(web, "找片")["code"] == "timeout"
    monkeypatch.setattr(discovery_sessions, "_TTL", -1)
    provider(monkeypatch, {"intent": "search"})
    result = run(web, "找片")
    assert result["ok"] and result["status"] == "empty"
    assert web.post(URL + "/refresh", json={"session_id": result["session_id"]}).json()["code"] == "session_expired"


def test_late_watch_change_is_rechecked_before_emitting_result(web, monkeypatch):
    mid = movie("码头", overview="日常生活")
    def change(_):
        store.update_movie_meta(mid, watched=1)
        return {"choices": [{"key": "c0", "evidence": ["日常生活"], "reason": "日常生活题材。"}]}
    provider(monkeypatch, {"intent": "recommend", "constraints": {"watch_state": "unseen"}}, change)
    result = run(web, "推荐没看过的")
    assert result["items"] == [] and result["status"] == "empty" and "已更新" in result["summary"]


def test_scoped_replacement_and_explicitly_relaxed_next_search(web, monkeypatch):
    ids = [movie("电影" + str(i), tmdb_rating=9 - i / 10, genres=[genre], year=2024)
           for i, genre in enumerate(("剧情", "喜剧", "动作", "喜剧"))]
    next_year = movie("之后的新片", tmdb_rating=10, year=2025)
    provider(monkeypatch, {"intent": "ranking", "limit": 3, "constraints": {"years": [2024]}},
             {"intent": "ranking", "action": "replace", "positions": [2], "constraints": {"genres": ["喜剧"]}},
             {"intent": "ranking", "action": "refine", "remove_constraints": ["years"]})
    first = run(web, "2024年的前三部")
    second = run(web, "只替换第二部，换成喜剧", session_id=first["session_id"])
    assert [r["id"] for r in second["items"]] == [ids[0], ids[3], ids[2]]
    third = web.post(URL + "/refresh", json={"session_id": second["session_id"]}).json()
    assert [r["id"] for r in third["items"]] == [ids[0], ids[3], ids[2]]
    fourth = run(web, "不限年份了，重新排前3", session_id=third["session_id"])
    assert [r["id"] for r in fourth["items"]] == [next_year, ids[0], ids[1]]


def test_filtered_wall_is_authoritative_with_many_selected_facets(web, monkeypatch):
    genres = ["分类" + str(i) for i in range(20)]
    wanted = movie("严格匹配", genres=[genres[-1]], year=2000, tmdb_rating=6)
    movie("其他类", genres=["动作"], year=2000, tmdb_rating=9)
    movie("其他年份", genres=[genres[-1]], year=2024, tmdb_rating=7)
    steps = ({"intent": "ranking", "limit": 1}, {"intent": "ranking", "action": "refine"})
    calls = provider(monkeypatch, *steps)
    first = run(web, "最高分", filters={"genre": genres, "year": list(range(1980, 2001))})
    assert [r["id"] for r in first["items"]] == [wanted]
    assert len(calls[0][1]["current_filters"]["genre"]) == 12
    second = run(web, "仍然排最高", session_id=first["session_id"])
    assert [r["id"] for r in second["items"]] == [wanted]


def test_same_title_reference_clarification_and_followup(web, monkeypatch):
    for name, year in (("one.mkv", 1995), ("two.mkv", 2010)):
        seed = store.upsert_movie_by_path(name, library_id=1)
        store.update_movie_meta(seed, title="旧日", year=year, genres=["剧情"])
    target = movie("小城", overview="小城的日常", genres=["剧情"])
    def select(payload):
        row = next(r for r in payload["candidates"] if r["title"] == "小城")
        return {"choices": [{"key": row["key"], "evidence": ["小城的日常"], "reason": "采用日常叙事。"}]}
    calls = provider(monkeypatch, {"intent": "recommend", "references": ["旧日"]},
                     {"intent": "recommend", "action": "refine", "references": ["旧日 (1995)"]}, select)
    first = run(web, "推荐像旧日的")
    assert not first["items"] and first["status"] == "clarification" and "1995" in first["clarification"]
    second = run(web, "指的是第一部，1995年的", session_id=first["session_id"])
    assert calls[1][1]["last_turn"]["clarification"] == first["clarification"]
    assert [r["id"] for r in second["items"]] == [target]


def test_refreshed_ordinals_address_current_list_after_watch_change(web, monkeypatch):
    ids = [movie(f"作品{i}", year=2024, tmdb_rating=9-i/10) for i in range(3)]
    provider(monkeypatch, {"intent": "ranking", "constraints": {"watch_state": "unseen"}},
             {"intent": "ranking", "action": "compare", "positions": [1, 2]},
             {"entries": [{"key": "c0", "evidence": ["作品1"]}, {"key": "c1", "evidence": ["作品2"]}]})
    first = run(web, "未看过的影片按评价排列")
    store.update_movie_meta(ids[0], watched=1)
    fresh = web.post(URL + "/refresh", json={"session_id": first["session_id"]}).json()
    assert fresh["session_id"] != first["session_id"] and [r["id"] for r in fresh["items"]] == ids[1:]
    compared = run(web, "比较第一二部", session_id=fresh["session_id"])
    assert [r["title"] for r in compared["comparison"]] == ["作品1", "作品2"]


def test_rank_topic_before_limiting_candidates(web, monkeypatch):
    for i in range(30):
        movie("山间旧事" + str(i), tmdb_rating=9.5)
    wanted = movie("海上竞速", tmdb_rating=7.8, overview="一场帆船竞赛")
    def pick(payload):
        assert [row["title"] for row in payload["candidates"]] == ["海上竞速"]
        return {"choices": [{"key": "c0", "evidence": ["帆船竞赛"]}]}
    provider(monkeypatch, {"intent": "ranking", "preferences": ["帆船运动"],
                          "clues": {"terms": ["帆船"]}, "limit": 1}, pick)
    result = run(web, "找评分最高的一部帆船运动电影")
    assert [r["id"] for r in result["items"]] == [wanted]


def test_failed_subjective_ranking_does_not_report_no_results(web, monkeypatch):
    movie("资料很少的片目")
    def unavailable(_):
        raise AiUnavailable("timeout", "模型响应超时")
    provider(monkeypatch, {"intent": "recommend", "preferences": ["看起来轻松"]}, unavailable)
    result = run(web, "推荐轻松一点的")
    assert result["ok"] is False and result["code"] == "timeout"
