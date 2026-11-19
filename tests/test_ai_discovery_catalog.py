"""Discovery uses a visible, version-aware catalogue without touching media files."""
from datetime import date
import json

import pytest

from app import library_paths, store, viewing
from app.store.ai_discovery import discover_local


@pytest.fixture
def libraries(monkeypatch, tmp_path):
    monkeypatch.setattr(store._base, "DB_PATH", str(tmp_path / "discovery.db"))
    store.init_db()
    library_paths.invalidate_cache()
    (tmp_path / "tv").mkdir()
    (tmp_path / "movies").mkdir()
    tv = store.create_library(name="电视", path=str(tmp_path / "tv"), kind="tv")
    other = store.create_library(name="电影二", path=str(tmp_path / "movies"), kind="movie")
    yield [1, other["id"], tv["id"]]
    library_paths.invalidate_cache()


def movie(title, path=None, library=1, **meta):
    mid = store.upsert_movie_by_path(path or (title + ".mkv"), library_id=library)
    store.update_movie_meta(mid, title=title, **meta)
    return mid


def test_full_year_ranking_dedup_source_limit_and_visibility(libraries):
    ids = [movie(f"电影{i}", tmdb_id=100+i, tmdb_rating=i/2+3, year=2026) for i in range(13)]
    movie("另一个版本", tmdb_id=112, tmdb_rating=9, year=2026)
    movie("去年", tmdb_rating=10, year=2025)
    movie("没有评分", year=2026)
    movie("另一媒体库", library=libraries[1], tmdb_rating=10, year=2026)
    mid = movie("仅私密", tmdb_rating=10, year=2026)
    with store._base._conn() as c:
        c.execute("UPDATE movies SET is_private=1 WHERE id=?", (mid,))
    token = viewing.current.set(viewing.ContentAccessContext("normal"))
    try:
        result = discover_local([1], kinds=["movie"], constraints={"years": [2026]}, order="rating_desc", limit=10)
        assert [r["id"] for r in result["items"]] == list(reversed(ids))[:10]
        assert (result["total"], result["excluded_unknown"]) == (13, 1)
        assert result["items"][0]["version_count"] == 2
        assert not discover_local([], constraints={"tmdb_ids": [112]})["items"]
        assert not discover_local([1], constraints={"ids": [mid]})["items"]
        assert not discover_local([1], constraints={"rating_source": "douban"}, order="rating_desc")["items"]
    finally:
        viewing.current.reset(token)


def test_unseen_honours_other_versions_and_partial_shows(libraries):
    film = movie("双版本", tmdb_id=66)
    version = movie("分辨率二", tmdb_id=66, library=libraries[1])
    store.save_progress(version, 16, 600)
    unseen = movie("没有开播")
    sid = store.upsert_show(libraries[2], "追了一集", 2025)
    e1 = store.upsert_episode(sid, libraries[2], "e1.mkv", 1, 1, episode_end=2)
    e2 = store.upsert_episode(sid, libraries[2], "e3.mkv", 1, 3)
    store.mark_episode_watched(e1)
    store.upsert_episode(sid, libraries[2], "alt-e2.mkv", 1, 2)
    result = discover_local(libraries, constraints={"watch_state": "unseen"})
    assert [(r["kind"], r["id"]) for r in result["items"]] == [("movie", unseen)]
    started = discover_local(libraries, constraints={"watch_state": "in_progress"})
    assert {(r["kind"], r["id"]) for r in started["items"]} == {("movie", film), ("tv", sid)}
    store.mark_episode_watched(e2)
    assert discover_local(libraries, kinds=["tv"], constraints={"watch_state": "watched"})["total"] == 1


def test_current_year_chart_excludes_known_future_premieres(libraries):
    existing = movie("已经上映", year=2026, tmdb_rating=8.5)
    soon = movie("尚待上映", year=2026, tmdb_id=79, tmdb_rating=9.8)
    store.upsert_tmdb_cache(79, {"premiered": "2026-12-20"})
    def ranking(**criteria):
        data = discover_local(libraries, kinds=["movie"], order="rating_desc", today=date(2026, 11, 19),
                              constraints={"years": [2026], **criteria})
        return [row["id"] for row in data["items"]]
    assert ranking() == [existing]
    assert ranking(date_from="2026-12-01") == [soon]


def test_all_language_and_length_constraints_match_one_version(libraries):
    first = movie("晚风", path="a.mkv", tmdb_id=15, original_language="fr")
    second = movie("晚风", path="b.mkv", tmdb_id=15)
    def tracks(mid, lang, subs, duration):
        store.upsert_media_info(mid, {"playable": True, "duration": duration*60,
                                     "audio": [{"lang": lang}], "subs": [{"lang": subs}]})
    tracks(first, "cmn", "eng", 90)
    tracks(second, "fra", "chi", 85)
    conditions = {"runtime_max": 95, "audio_languages": ["cmn"], "subtitle_languages": ["zh"]}
    assert discover_local([1], kinds=["movie"], constraints=conditions)["items"] == []
    tracks(second, "cmn", "chi", 92)
    row = discover_local([1], kinds=["movie"], constraints=conditions)["items"][0]
    assert row["detail_path"] == f"/m/{second}" and row["version_id"] == second
    assert row["runtime"] == 92 and set(row["audio_languages"]) == {"cmn", "zh"}
    assert not discover_local([1], constraints={"original_languages": ["cmn"]})["items"]
    tracks(second, "cmn", "chi", 0)
    assert not discover_local([1], kinds=["movie"], constraints=conditions)["items"]
    tracks(second, "chi", "chi", 92)
    assert not discover_local([1], kinds=["movie"], constraints=conditions)["items"]


def test_description_only_semantics_reference_aliases_and_unknown(libraries):
    quiet = movie("渡口", overview="调走后重返小城，在江边和离散的家人重新过起日常生活。",
                  tmdb_id=32, genres=["剧情"], year=1980)
    seed = movie("家书", tmdb_id=31, genres=["剧情"], tags=["日常"])
    for i in range(12):
        movie(f"爆破现场{i}", tmdb_rating=9, year=2026, genres=["动作"])
    store.upsert_match_entry("tmdb", "movie:31", title="家书", tmdb_id=31, alt_titles="旧日来信")
    result = discover_local([1], kinds=["movie"], clues={"terms": ["日常生活", "家庭关系"]},
                            references=["旧日来信"], limit=2)
    assert result["items"][0]["id"] == quiet and seed not in [r["id"] for r in result["items"]]
    assert "剧情线索吻合" in result["items"][0]["evidence"]
    unknown = movie("旧磁带", year=2020)
    assert not discover_local([1], constraints={"ids": [unknown], "exclude_genres": ["恐怖"]})["items"]
    assert not discover_local([1], constraints={"ids": [quiet], "date_from": "1980-02-10"})["items"]
    assert discover_local([1], constraints={"tmdb_ids": [32]})["items"][0]["id"] == quiet
    assert not discover_local([1], constraints={"tmdb_ids": [32], "exclude_ids": {"movie": [quiet]}})["items"]


def test_episode_season_lookup_evidence_and_routes(libraries):
    lib = libraries[2]
    sid = store.upsert_show(lib, "小城记", 2020, hints={"tmdb": 100})
    ep = store.upsert_episode(sid, lib, "pack.mkv", 2, 3, episode_end=4, absolute_number=12)
    store.update_episode_meta(ep, title="雨夜", overview="邮差冒雨送信。", air_date="2021-04-03",
                              episode_credits=json.dumps({"guests": [{"name": "陈甲"}]}))
    store.upsert_episode(sid, lib, "first.mkv", 1, 1)
    store.upsert_season(sid, lib, 2, name="归乡", overview="多年后的故乡。", air_date="2021-04-01")
    result = discover_local(libraries, kinds=["episode"], constraints={
        "season": 2, "episode": 4, "actors": ["陈甲"], "date_from": "2021-04-02"},
        clues={"titles": ["小城记"]})
    row = result["items"][0]
    assert row["id"] == ep and row["detail_path"] == f"/tv/{sid}/s/2/e/{ep}"
    assert row["title"] == "雨夜" and row["year"] == 2021
    assert discover_local(libraries, kinds=["episode"], constraints={"absolute_number": 12})["total"] == 1
    seasons = discover_local(libraries, kinds=["season"], constraints={"season": 2})["items"]
    assert len(seasons) == 1 and seasons[0]["overview"] == "多年后的故乡。"
    assert seasons[0]["detail_path"] == f"/tv/{sid}/s/2"


def test_paraphrased_scene_enters_the_shortlist_ahead_of_high_scoring_filler(libraries):
    movie("站台纪录", year=2020, tmdb_rating=9.5, overview="车站纪实，记录不同旅客。")
    match = movie("末班电车", year=1998, overview="一位失眠的女司机在深夜开车。")
    data = discover_local(libraries, kinds=["movie"], clues={"terms": ["失眠女司机"]}, explore=True, limit=1)
    assert data["items"][0]["id"] == match
    assert "剧情含相关用词" in data["items"][0]["evidence"]


def test_wall_query_and_independent_score_source_apply_before_ranking(libraries):
    wanted = movie("星海", year=2025, tmdb_rating=9, douban_rating=8, genres=["科幻"])
    movie("星海高分续作", year=2025, tmdb_rating=9.9, douban_rating=6, genres=["科幻"])
    movie("无名", year=2025, tmdb_rating=9.9, douban_rating=9, genres=["科幻"])
    criteria = {"_wall_q": "星海", "_wall": {"genres": ["科幻"], "rating_source": "douban", "min_rating": 7},
                "rating_source": "tmdb", "date_from": "2025-01-01", "date_to": "2025-12-31"}
    data = discover_local(libraries, constraints=criteria, kinds=["movie"], order="rating_desc", limit=1)
    assert data["total"] == 1 and data["items"][0]["id"] == wanted
    assert discover_local([1], clues={"titles": ["根本没有这部片"]})["items"] == []
    assert discover_local([1], clues={"terms": ["没有此关键词"]}, explore=True)["total"] == 3


def test_disabled_library_and_children_scope_exclude_titles_and_aliases(libraries):
    keep = movie("公开作品", tmdb_id=101, genres=["家庭"])
    hidden = movie("隐藏作品", tmdb_id=102, genres=["剧情"])
    movie("禁用视频库的作品", library=libraries[1], tmdb_id=103, genres=["家庭"])
    store.upsert_match_entry("tmdb", "movie:102", tmdb_id=102, alt_titles="库外别名")
    with store._base._conn() as c:
        c.execute("UPDATE libraries SET enabled=0 WHERE id=?", (libraries[1],))
        c.execute("UPDATE movies SET children_override='allow' WHERE id=?", (keep,))
        c.execute("UPDATE movies SET children_override='block' WHERE id=?", (hidden,))
    token = viewing.current.set(viewing.ContentAccessContext("children"))
    try:
        rows = discover_local(libraries, kinds=["movie"])["items"]
        assert [r["id"] for r in rows] == [keep] and "库外别名" not in str(rows)
        assert not discover_local(libraries, kinds=["movie"], constraints={"tmdb_ids": [102, 103]})["items"]
    finally:
        viewing.current.reset(token)


def test_reference_title_year_disambiguation_uses_visible_records(libraries):
    a = movie("归途", path="1995.mkv", year=1995, genres=["剧情"])
    movie("归途", path="2014.mkv", year=2014, genres=["爱情"])
    other = movie("北上", genres=["剧情"], tmdb_rating=7.5)
    movie("异星", genres=["科幻"], tmdb_rating=9.7)
    result = discover_local([1], kinds=["movie"], references=["归途"], explore=True)
    assert result["items"] == [] and "1995" in result["clarification"] and "2014" in result["clarification"]
    result = discover_local([1], kinds=["movie"], references=["《归途》（1995）"], explore=True)
    assert result["items"][0]["id"] == other and a not in [r["id"] for r in result["items"]]
    # A narrowed follow-up still resolves its original reference outside the returned IDs.
    refetched = discover_local([1], kinds=["movie"], constraints={"ids": [other]},
                               references=["归途 (1995)"], explore=True)
    assert "与参考作品类型相近" in refetched["items"][0]["evidence"]
    ranked = discover_local([1], kinds=["movie"], references=["归途 1995"], order="rating_desc")
    assert [r["id"] for r in ranked["items"]] == [other]


def test_show_and_season_preserve_specific_guest_and_director_roles(libraries):
    lib = libraries[2]
    directed = store.upsert_show(lib, "实景剧", 2020)
    guest_only = store.upsert_show(lib, "客串剧", 2021)
    creator_only = store.upsert_show(lib, "创作剧", 2022)
    store.update_show_meta(creator_only, created_by=[{"name": "陈甲"}], person_names="陈甲")
    for sid, season, credits in (
            (directed, 1, {"directors": [{"name": "陈甲"}]}),
            (directed, 2, {"guests": [{"name": "陈甲", "character": "医生"}]}),
            (guest_only, 1, {"guests": [{"name": "陈甲"}]}),
            (creator_only, 1, {})):
        episode = store.upsert_episode(sid, lib, f"{sid}-s{season}.mkv", season, 1)
        store.update_episode_meta(episode, episode_credits=json.dumps(credits))
    rows = discover_local([lib], kinds=["tv"], constraints={"directors": ["陈甲"]})["items"]
    assert [r["id"] for r in rows] == [directed]
    assert "第1季第1集，陈甲担任分集导演" in rows[0]["evidence"]
    seasons = discover_local([lib], kinds=["season"], constraints={"directors": ["陈甲"]})["items"]
    assert [(r["show_id"], r["season"]) for r in seasons] == [(directed, 1)]
    guest_roles = discover_local([lib], kinds=["season"], constraints={"actors": ["陈甲"]})["items"]
    assert {(r["show_id"], r["season"]) for r in guest_roles} == {(directed, 2), (guest_only, 1)}
    assert all("客串" in r["evidence"][0] for r in guest_roles)
    cast = json.dumps([{"name": "林乙", "character": "乘务员"}])
    store.upsert_season(directed, lib, 2, cast_json=cast)
    regular = discover_local([lib], kinds=["tv"], constraints={"actors": ["林乙"]})["items"]
    assert [r["id"] for r in regular] == [directed] and "第2季常驻" in regular[0]["evidence"][0]
