from contextlib import nullcontext
from datetime import date

import pytest

from app.ai import discovery_public as public
from app.ai.client import AiUnavailable


def catalog(monkeypatch, respond):
    monkeypatch.setattr(public.config, "effective_tmdb_read_token", lambda: "fixture")
    monkeypatch.setattr(public.tmdb, "_client", lambda: nullcontext(None))
    monkeypatch.setattr(public.tmdb, "catalog_page", lambda client, path, params, deadline: respond(path, params))


def movie(i, score=8.3, votes=120, **overrides):
    return dict({"id": i, "title": f"候选{i}", "release_date": "2026-03-03", "vote_average": score,
                 "vote_count": votes, "genre_ids": [18]}, **overrides)


def test_public_chart_requests_date_range_and_votes_then_merges_types(monkeypatch):
    def respond(path, params):
        if path == "/discover/movie":
            assert params["primary_release_date.gte"] == "2026-01-01"
            assert params["primary_release_date.lte"] == "2026-11-19"
            assert params["vote_count.gte"] == 100 and params["sort_by"] == "vote_average.desc"
            return {"results": [movie(1), movie(2, 10, 1), movie(3, 9.6, release_date="2027-01-01")]}
        assert path == "/discover/tv" and params["first_air_date.gte"] == "2026-01-01"
        return {"results": [{"id": 1, "name": "另一部作品", "first_air_date": "2026-05-01",
                             "vote_average": 9.1, "vote_count": 200}]}
    catalog(monkeypatch, respond)
    result = public.discover_public(["movie", "tv"], {"years": [2026]}, {}, "rating_desc", 10, date(2026, 11, 19))
    assert [i["kind"] for i in result["items"]] == ["tv", "movie"]
    assert all(i["id"] is None and i["watch_state"] == "unknown" for i in result["items"])
    assert "100" in result["warnings"][0]


def test_public_title_verifies_runtime_and_genre(monkeypatch):
    def respond(path, params):
        if path == "/genre/movie/list":
            return {"genres": [{"id": 18, "name": "剧情"}]}
        if path == "/search/movie":
            return {"results": [movie(3), movie(4), movie(5, genre_ids=[28])]}
        return {"runtime": 90 if path == "/movie/3" else None}
    catalog(monkeypatch, respond)
    result = public.discover_public(["movie"], {"genres": ["剧情"], "runtime_max": 100},
                                    {"titles": ["片名"]}, "relevance", 10)
    assert [i["tmdb_id"] for i in result["items"]] == [3]


def test_public_catalog_unverifiable_constraint_and_missing_key(monkeypatch):
    monkeypatch.setattr(public.config, "effective_tmdb_read_token", lambda: "")
    monkeypatch.setattr(public.config, "effective_tmdb_api_key", lambda: "")
    result = public.discover_public(["movie"], {"audio_languages": ["zh"]}, {}, "relevance", 6)
    assert result["items"] == [] and "音轨" in result["warnings"][0]
    with pytest.raises(AiUnavailable) as error:
        public.discover_public(["movie"], {}, {}, "relevance", 6)
    assert error.value.code == "tmdb_not_configured"


def test_topic_chart_uses_verified_keyword_in_database_order(monkeypatch):
    def respond(path, params):
        if path == "/search/keyword":
            return {"results": [{"id": 51, "name": "dinosaur"}]}
        assert path == "/discover/movie" and params["with_keywords"] == "51"
        assert params["sort_by"] == "vote_average.desc"
        return {"results": [movie(1), movie(9, 9.1)]}
    catalog(monkeypatch, respond)
    data = public.discover_public(["movie"], {}, {"terms": ["dinosaur"]}, "rating_desc", 10)
    assert [row["tmdb_id"] for row in data["items"]] == [9, 1]
    empty = public.discover_public(["movie"], {}, {"terms": ["童年滤镜"]}, "rating_desc", 10)
    assert empty["items"] == [] and "关键词" in empty["warnings"][0]


def test_director_chart_uses_directing_credits_only(monkeypatch):
    def respond(path, params):
        if path == "/search/person":
            return {"results": [{"id": 31, "name": "演示导演"}]}
        assert path == "/person/31/movie_credits"
        return {"crew": [movie(1, 8.2, job="Director"), movie(2, 9.8, job="Producer")]}
    catalog(monkeypatch, respond)
    result = public.discover_public(["movie"], {"directors": ["演示导演"]}, {}, "rating_desc", 6)
    assert [i["tmdb_id"] for i in result["items"]] == [1]


def test_chart_with_title_checks_later_pages_before_sorting(monkeypatch):
    def respond(path, params):
        assert path == "/search/movie" and params["query"] == "示例片名"
        page = params["page"]
        return {"total_pages": 2, "results": [movie(page, 8 if page == 1 else 9)]}
    catalog(monkeypatch, respond)
    result = public.discover_public(["movie"], {}, {"titles": ["示例片名"]}, "rating_desc", 1)
    assert [i["tmdb_id"] for i in result["items"]] == [2]


def test_actor_and_another_credit_intersect_without_exchanging_roles(monkeypatch):
    def respond(path, params):
        if path == "/search/person":
            name = params["query"]
            return {"results": [{"id": 10 if name == "甲" else 20, "name": name}]}
        if path == "/person/10/movie_credits":
            return {"cast": [movie(1), movie(2)]}
        assert path == "/person/20/movie_credits"
        return {"crew": [movie(2)], "cast": [movie(3)]}
    catalog(monkeypatch, respond)
    result = public.discover_public(["movie"], {"actors": ["甲"]}, {"people": ["乙"]}, "rating_desc", 8)
    assert [r["tmdb_id"] for r in result["items"]] == [2]


def test_topic_search_does_not_mix_in_the_unfiltered_popular_list(monkeypatch):
    def respond(path, params):
        if path == "/search/keyword":
            return {"results": [{"id": 19, "name": "gardening"}]}
        assert path == "/discover/movie" and params["with_keywords"] == "19"
        return {"results": [movie(7)]}
    catalog(monkeypatch, respond)
    result = public.discover_public(["movie"], {}, {"terms": ["gardening"]}, "relevance", 8)
    assert [r["tmdb_id"] for r in result["items"]] == [7]


def test_multilanguage_chart_merges_single_language_directories(monkeypatch):
    def respond(path, params):
        language = params["with_original_language"]
        assert path == "/discover/movie" and language in ("zh", "ja")
        return {"results": [movie(1 if language == "zh" else 2, 8 if language == "zh" else 9,
                                  original_language=language)]}
    catalog(monkeypatch, respond)
    result = public.discover_public(["movie"], {"original_languages": ["zh", "ja"]}, {}, "rating_desc", 1)
    assert [r["tmdb_id"] for r in result["items"]] == [2]
