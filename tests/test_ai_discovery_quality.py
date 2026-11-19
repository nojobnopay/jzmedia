"""Live, opt-in comparison against the original natural-language filter workflow.

Run with JZMEDIA_AI_QUALITY=1 and the service's AI_* environment settings.
Use pytest -q -s to print per-question metrics and aggregate report to stdout.
All records below belong to a temporary SQLite database.
"""
from datetime import date
import json
import os
import time

from fastapi.testclient import TestClient
import pytest

from app import library_paths, store
from app.ai import client as ai_client, settings as ai_settings
from app.main import app


def _dataset(tmp_path):
    year = date.today().year
    facts = {"movie": {}, "tv": {}}

    def film(title, **values):
        mid = store.upsert_movie_by_path(title + ".mkv")
        store.update_movie_meta(mid, title=title, **values)
        store.resync_fts(mid)
        facts["movie"][mid] = store.get_movie(mid)
        return mid

    football = film("少林足球", original_title="Shaolin Soccer", year=2001,
                    person_names="周星驰，赵薇，吴孟达", genres=["喜剧", "动作"],
                    overview="一群少林师兄弟把各自的功夫用在足球场上，组队参加比赛。")
    film("功夫", year=2004, person_names="周星驰，元华", genres=["动作", "喜剧"],
         overview="贫民窟里的武林高手为保护居民，与斧头帮交手。")
    film("足球教练的夏天", year=2009, genres=["喜剧"], overview="退休教练带领女足队备战。")
    quiet = [
        film("渡口的晚风", year=2003, genres=["剧情"],
             overview="离乡多年的女儿回到江边小城。镜头缓缓记录她和父亲摆摊、煮饭、等待渡船的日常。"),
        film("空房间里的雨", year=2011, genres=["剧情"],
             overview="年迈的裁缝为即将搬走的邻居缝衣，克制的对白串起旧街区平凡人家的离别。"),
        film("山间邮路", year=1998, genres=["剧情"],
             overview="父子沿山路送最后一趟信，从陌生到和解。许多段落只有脚步声和山风。"),
    ]
    film("车站旁的小店", year=2007, genres=["剧情"], watched=1,
         overview="缓慢的长镜头记录老人独守小店、回忆妻子的日常。")
    ranking = [film(f"合成年度片{n:02}", year=year, tmdb_rating=round(5 + n * 0.32, 2),
                    genres=["动作"], overview="城市警队追捕劫匪，枪战、飞车和爆炸接连发生。")
               for n in range(13)]
    film("去年冠军", year=year-1, tmdb_rating=9.9)
    film("尚无评分的新片", year=year)
    tvroot = tmp_path / "shows"
    tvroot.mkdir()
    lib = store.create_library(name="测试剧集", path=str(tvroot), kind="tv")
    show = store.upsert_show(lib["id"], "最后一班电车", 2017)
    store.update_show_meta(show, genres=["悬疑"],
                           overview="一名失眠的女司机发现，每晚驶进废弃站台的末班电车里，坐着三十年前失踪的人。")
    similar = store.upsert_show(lib["id"], "站台日记", 2019)
    store.update_show_meta(similar, genres=["剧情"], overview="一群年轻铁路工人记录建设车站的生活。")
    facts["tv"] = {i: store.get_show_meta(i) for i in (show, similar)}
    cases = [
        ("football", "identify", "movie", 1, "周星驰在一部电影里踢足球，还把功夫用在了球场上，这是哪一部？", [football]),
        ("last_train", "identify", "tv", 1, "想找那部失眠女司机在废站发现失踪乘客的电视剧，叫什么？", [show]),
        ("year_top", "ranking", "movie", 10, "我想看今年评分最高的10部电影。", list(reversed(ranking))[:10]),
        ("quiet", "recommend", "movie", 3, "推荐3部我没看过的文艺片，想看节奏舒缓、关注普通人生活的作品。", quiet),
    ]
    return facts, cases


def _run(client, flow, kind, query):
    before = ai_settings.usage()["requests"]
    started = time.perf_counter()
    path = "/api/ai/search/results" if flow == "discovery" else "/api/ai/search"
    resp = client.post(path, json={"q": query, "kind": kind})
    resp.raise_for_status()
    body = resp.json()
    if flow == "legacy" and body.get("ok"):
        filters = body["filters"]
        params = [(key, str(v)) for key, value in filters.items() if value is not None
                  for v in (value if isinstance(value, list) else [value])]
        wall = "/api/search" if kind == "movie" else "/api/tv/shows"
        resp = client.get(wall, params=params)
        resp.raise_for_status()
        items = resp.json()["items"]
    else:
        items = body.get("items", [])
    return items, {
        "status": body.get("status") or body.get("code") or ("ok" if body.get("ok") else "failed"),
        "elapsed_s": round(time.perf_counter() - started, 3),
        "provider_calls": ai_settings.usage()["requests"] - before,
    }


def _measure(items, kind, truth, mode, expected, requested, flow):
    ids = [r.get("id") for r in items]
    prefix = ids[:requested]
    known = all(r.get("kind", kind) == kind and r.get("id") in truth for r in items)
    factual = known and all(
        r.get("title") == truth[r["id"]].get("title") and r.get("year") == truth[r["id"]].get("year")
        and (r.get("rating") is None or r["rating"] == truth[r["id"]].get((r.get("rating_source") or "tmdb") + "_rating"))
        for r in items)
    result = {"returned": len(ids), "top_titles": [r.get("title") for r in items[:requested]],
              "facts_verified": bool(items) and factual, "invalid_or_duplicate": not factual or len(set(ids)) != len(ids)}
    if mode == "identify":
        result.update(hit_at_1=expected[0] in ids[:1], hit_at_3=expected[0] in ids[:3])
    elif mode == "ranking":
        result.update(ordered_top_n=prefix == expected, exact_count=len(ids) == requested)
    else:
        relevant = len(set(prefix) & set(expected))
        result.update(precision_at_n=round(relevant / requested, 3),
                      recall_at_n=round(relevant / len(expected), 3),
                      seen_violations=sum(bool(truth[rid].get("watched")) for rid in ids if rid in truth))
    if flow == "discovery" and mode in {"identify", "recommend"}:
        result["reasons"] = [{k: r.get(k) for k in ("title", "evidence", "reason")} for r in items[:requested]]
        quoted = verified = 0
        for row in items[:requested]:
            fields = truth.get(row.get("id"), {})
            text = " ".join(str(fields.get(k) or "") for k in (
                "title", "original_title", "year", "overview", "genres", "tags", "person_names"))
            for excerpt in row.get("evidence") or []:
                quoted += 1
                verified += bool(excerpt and excerpt in text)
        result["source_quotes_verified"] = verified
        result["source_quotes_total"] = quoted
    return result


def _summary(rows):
    ids = [r for r in rows if "hit_at_1" in r]
    rec = next(r for r in rows if "precision_at_n" in r)
    rank = next(r for r in rows if "ordered_top_n" in r)
    return {"identify_hit_at_1": sum(r["hit_at_1"] for r in ids) / len(ids),
            "identify_hit_at_3": sum(r["hit_at_3"] for r in ids) / len(ids),
            "ranking_exact": bool(rank["ordered_top_n"] and rank["exact_count"]),
            "recommendation_precision_at_3": rec["precision_at_n"],
            "recommendation_recall_at_3": rec["recall_at_n"],
            "returned_facts_verified": all(r["facts_verified"] for r in rows),
            "provider_calls": sum(r["provider_calls"] for r in rows),
            "elapsed_s": round(sum(r["elapsed_s"] for r in rows), 3)}


pytestmark = pytest.mark.skipif(os.getenv("JZMEDIA_AI_QUALITY") != "1", reason="requires explicit live-provider opt-in")


@pytest.fixture
def live_catalog(monkeypatch, tmp_path):
    provider = os.getenv("AI_PROVIDER")
    if os.getenv("AI_ENABLED", "").lower() not in {"true", "1"} or not (provider and os.getenv("AI_MODEL")):
        pytest.skip("set AI_ENABLED=true, AI_PROVIDER and AI_MODEL for the explicit live evaluation")
    if provider == "compatible" and not os.getenv("AI_BASE_URL"):
        pytest.skip("a compatible test model requires its explicit AI_BASE_URL")
    if provider in ("deepseek", "opencode_go") and not os.getenv("AI_API_KEY"):
        pytest.skip("the requested provider requires an explicit AI_API_KEY")
    monkeypatch.setattr(store._base, "DB_PATH", str(tmp_path / "quality.db"))
    store.init_db()
    library_paths.invalidate_cache()
    ai_client.clear_cache()
    facts, cases = _dataset(tmp_path)
    client = TestClient(app)
    try:
        yield client, facts, cases
    finally:
        client.close()
        library_paths.invalidate_cache()


def test_live_chinese_discovery_quality(live_catalog):
    client, facts, cases = live_catalog
    by_flow = {"legacy": [], "discovery": []}
    for case, mode, kind, requested, question, expected in cases:
        for flow, rows in by_flow.items():
            found, timing = _run(client, flow, kind, question)
            rows.append({"case": case, "question": question, **timing,
                         **_measure(found, kind, facts[kind], mode, expected, requested, flow)})
    summary = {flow: _summary(rows) for flow, rows in by_flow.items()}
    print(json.dumps({"model": os.environ["AI_MODEL"], "summary": summary, "cases": by_flow},
                     ensure_ascii=False, indent=2))
    assert all(not row["invalid_or_duplicate"] for row in by_flow["discovery"])
    assert summary["discovery"]["identify_hit_at_3"] == 1
    assert summary["discovery"]["ranking_exact"]
    assert summary["discovery"]["recommendation_precision_at_3"] >= 0.66
    assert by_flow["discovery"][-1]["seen_violations"] == 0


def test_live_followups_preserve_versions_and_other_positions(live_catalog):
    client, facts, cases = live_catalog
    eligible = cases[-1][-1]
    for mid in facts["movie"]:
        store.upsert_media_info(mid, {"playable": True, "duration": (85 if mid in eligible else 110) * 60,
                                      "audio": [{"lang": "fra"}], "subs": [{"lang": "chi"}]})
    endpoint = "/api/ai/search/results"

    def turn(q, previous=None):
        body = {"kind": "movie", "q": q}
        if previous:
            body["session_id"] = previous["session_id"]
        reply = client.post(endpoint, json=body)
        reply.raise_for_status()
        result = reply.json()
        print(json.dumps({"query": q, "status": result.get("status") or result.get("code"),
                          "summary": result.get("summary"), "titles": [r["title"] for r in result.get("items", [])]},
                         ensure_ascii=False))
        return result

    first = turn("今晚只有90分钟。找两部没看过、带中文字幕、节奏舒缓的电影。")
    items = first.get("items", [])
    assert len(items) == 2 and all(r["id"] in eligible and r["version_id"] == r["id"] for r in items)
    compared = turn("比较第一部和第二部的叙事节奏，结合简介说说。", first)
    assert compared["status"] == "comparison" and len(compared["comparison"]) == 2
    assert [r["key"] for r in compared["items"]] == [r["key"] for r in items]
    replacement = turn("保留第一部，只把第二部换成另一个同样符合原要求的选择。", compared)
    updated = replacement["items"]
    assert len(updated) == 2 and updated[0]["key"] == items[0]["key"]
    assert updated[1]["id"] in set(eligible) - {r["id"] for r in items}
    assert updated[1]["version_id"] == updated[1]["id"]
