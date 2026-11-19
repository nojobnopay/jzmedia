"""Public reference search resolves real identities and filters the relationship feeds."""
from contextlib import nullcontext
from datetime import date

from app.ai import discovery_public as public


def service(monkeypatch, reply):
    monkeypatch.setattr(public.config, "effective_tmdb_read_token", lambda: "fixture")
    monkeypatch.setattr(public.tmdb, "_client", lambda: nullcontext(None))
    calls = []
    def respond(_, path, params, deadline):
        calls.append((path, params))
        return reply(path, params)
    monkeypatch.setattr(public.tmdb, "catalog_page", respond)
    return calls


def record(mid, title, year, kind="movie", **extra):
    base = {"id": mid, "media_type": kind, "vote_average": 8.2, "vote_count": 500,
            "genre_ids": [18], "original_language": "fr"}
    if kind == "tv":
        base.update(name=title, first_air_date=f"{year}-01-10")
    else:
        base.update(title=title, release_date=f"{year}-01-10")
    return {**base, **extra}


def test_same_title_requires_clarification_then_exact_year_and_kind(monkeypatch):
    sources = [record(1, "窗景", 1995), record(2, "窗景", 2012), record(1, "窗景", 1995, "tv")]
    def respond(path, params):
        if path == "/search/multi":
            assert params["query"] == "窗景"
            return {"results": sources}
        if path in ("/movie/1/recommendations", "/movie/1/similar"):
            return {"results": [sources[0], record(7, "江边", 2000)]}
        raise AssertionError(path)
    calls = service(monkeypatch, respond)
    data = public.discover_public(["movie"], {}, {}, "relevance", 4, references=["窗景"])
    assert not data["items"] and "1995" in data["clarification"] and "2012" in data["clarification"]
    assert [path for path, _ in calls] == ["/search/multi"]
    same_year = public.discover_public(["movie"], {}, {}, "relevance", 4, references=["窗景（1995）"])
    assert "电影" in same_year["clarification"] and "剧集" in same_year["clarification"]
    data = public.discover_public(["movie"], {}, {}, "relevance", 4, references=["窗景（1995，电影）"])
    assert [item["tmdb_id"] for item in data["items"]] == [7]
    assert data["items"][0]["evidence"] == ["列入 TMDB《窗景》的推荐作品", "列入 TMDB《窗景》的相似作品"]


def test_anchored_feeds_preserve_role_country_runtime_year_votes_and_exclusions(monkeypatch):
    seed = record(4, "旧印象", 1990)
    eligible = record(10, "车站", 2025, vote_average=8.3)
    second_page = record(11, "山谷", 2025, vote_average=9.1)
    wrong_genre = record(12, "夜行", 2025, genre_ids=[27])
    long = record(13, "远方", 2025)
    cast_only = record(14, "客串", 2025, vote_average=10.0)
    old = record(15, "往事", 2004)
    few = record(16, "少票", 2025, vote_count=1)
    abroad = record(17, "越境", 2025)
    def respond(path, params):
        if path == "/search/multi":
            return {"results": [seed]}
        if path == "/genre/movie/list":
            return {"genres": [{"id": 27, "name": "恐怖"}]}
        if path == "/search/person":
            return {"results": [{"id": 20, "name": "演示导演"}]}
        if path == "/person/20/movie_credits":
            return {"cast": [cast_only], "crew": [dict(row, job="Director") for row in (
                eligible, second_page, wrong_genre, long, old, few, abroad)]}
        if path == "/movie/4/recommendations":
            rows = [seed, wrong_genre, long, cast_only, old, few, abroad] if params["page"] == 1 else [second_page]
            return {"results": rows, "total_pages": 2}
        if path == "/movie/4/similar":
            return {"results": [eligible]}
        if path.startswith("/movie/"):
            return {"runtime": 160 if path.endswith("/13") else 90,
                    "origin_country": ["US" if path.endswith("/17") else "FR"]}
        raise AssertionError(path)
    calls = service(monkeypatch, respond)
    hard = {"exclude_genres": ["恐怖"], "directors": ["演示导演"], "countries": ["FR"],
            "runtime_max": 100, "years": [2025], "original_languages": ["fr"]}
    data = public.discover_public(["movie"], hard, {}, "rating_desc", 10, date(2026, 11, 19),
                                  allow_exploration=True, references=["旧印象"])
    assert [row["tmdb_id"] for row in data["items"]] == [11, 10]
    assert any("参考片" in warning for warning in data["warnings"])
    assert not any("/discover/" in path for path, _ in calls)


def test_reference_type_and_unverified_alias_never_expand_to_general_discovery(monkeypatch):
    tv = record(22, "午夜电车", 2017, "tv")
    def respond(path, params):
        if path == "/search/multi":
            return {"results": [tv]}
        if path.startswith("/tv/22/"):
            return {"results": [tv, record(30, "月光站台", 2019, "tv")]}
        raise AssertionError(path)
    calls = service(monkeypatch, respond)
    unsupported = public.discover_public(["movie"], {}, {}, "relevance", 5, references=["午夜电车"])
    assert not unsupported["items"] and "电影和剧集" in unsupported["warnings"][0]
    unresolved = public.discover_public(["tv"], {}, {}, "relevance", 5, references=["电影版未知别名"])
    assert not unresolved["items"] and "clarification" in unresolved
    mixed = public.discover_public(["movie", "tv"], {}, {}, "relevance", 5, references=["午夜电车"])
    assert [r["tmdb_id"] for r in mixed["items"]] == [30] and mixed["warnings"]
    assert all("/discover/" not in path for path, _ in calls)
