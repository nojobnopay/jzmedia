"""Child filtering requires positive age evidence and handles missing data safely."""
import json

import pytest

from app.content_rules import (evaluate_content, merge_content_evidence,
                               normalize_content_evidence, normalize_nfo_evidence,
                               normalize_tmdb_evidence)


def evidence(*ratings, adult=False, keywords=None):
    return {"adult": adult, "keywords": keywords or [],
            "certifications": [{"country": c, "certification": r} for c, r in ratings]}


@pytest.mark.parametrize("kind,country,rating", [
    ("movie", "US", "G"), ("movie", "GB", "U"), ("movie", "HK", "I"),
    ("movie", "JP", "G"), ("tv", "US", "TV-Y"), ("tv", "US", "TV-G"),
])
def test_only_explicit_supported_low_ratings_admit(kind, country, rating):
    assert evaluate_content(kind, evidence((country, rating)))[0] == "allow"


@pytest.mark.parametrize("kind,ratings", [
    ("movie", [("US", "G"), ("GB", "12A")]),
    ("movie", [("HK", "I"), ("JP", "R15+")]),
    ("movie", [("US", "R")]), ("movie", [("US", "PG-13")]),
    ("movie", [("JP", "PG12")]), ("movie", [("HK", "IIA")]),
    ("tv", [("US", "TV-PG")]), ("tv", [("US", "TV-G V")]),
    ("tv", [("US", "TV-MA")]),
])
def test_higher_ratings_and_descriptors_veto_even_when_adult_false(kind, ratings):
    assert evaluate_content(kind, evidence(*ratings))[0] == "block"
    assert evaluate_content(kind, evidence(*reversed(ratings)))[0] == "block"


@pytest.mark.parametrize("ratings", [[], [("US", "NR")], [("", "G")],
                                     [("ZZ", "G")], [("US", "unexpected")]])
def test_unknown_and_animation_never_imply_suitable(ratings):
    assert evaluate_content("movie", evidence(*ratings), ["动画", "家庭"], [16, 10751])[0] == "unknown"


def test_unrecognized_supported_region_rating_cannot_be_masked_by_a_low_rating():
    assert evaluate_content("movie", evidence(("US", "G"), ("GB", "unexpected")))[0] == "unknown"
    assert evaluate_content("tv", evidence(("US", "TV-G"), ("US", "TV-UNKNOWN")))[0] == "unknown"
    assert evaluate_content("movie", evidence(("GB", "unexpected"), ("US", "R")))[0] == "block"


def test_movie_certification_is_not_a_television_rating():
    assert evaluate_content("tv", evidence(("JP", "G")))[0] == "unknown"
    assert evaluate_content("movie", evidence(("US", "TV-G")))[0] == "unknown"


@pytest.mark.parametrize("keyword", ["cartoon violence", "martial arts", "gore", "性侵", "枪战"])
def test_risk_keywords_veto_low_rating(keyword):
    assert evaluate_content("movie", evidence(("US", "G"), keywords=[keyword]))[0] == "block"


def test_risk_matching_does_not_match_arbitrary_substrings():
    assert evaluate_content("movie", evidence(("US", "G"), keywords=["reward", "essex"]))[0] == "allow"
    assert evaluate_content("movie", evidence(("US", "G"), adult=True))[0] == "block"
    assert evaluate_content("movie", evidence(("US", "G")), ["战争"])[0] == "block"
    assert evaluate_content("tv", evidence(("US", "TV-Y")), genre_ids=[10759])[0] == "block"


def test_remote_genre_evidence_vetoes_without_display_metadata_and_survives_partial_fetch():
    value = normalize_tmdb_evidence("movie", {
        "id": 55, "adult": False, "genres": [{"id": 28, "name": "Action"}],
        "release_dates": {"results": [{"iso_3166_1": "US", "release_dates": [{"certification": "G"}]}]},
        "keywords": {"keywords": []},
    })
    assert evaluate_content("movie", value)[0] == "block"
    partial = normalize_tmdb_evidence("movie", {"id": 55, "adult": False})
    assert merge_content_evidence(value, partial)["genre_ids"] == [28]


def test_missing_tmdb_sections_remain_unknown_and_can_merge_same_identity():
    complete = normalize_tmdb_evidence("movie", {
        "id": 55, "adult": False,
        "release_dates": {"results": [{"iso_3166_1": "US", "release_dates": [{"certification": "G"}]}]},
        "keywords": {"keywords": []},
    })
    partial = normalize_tmdb_evidence("movie", {"id": 55, "adult": False,
                                                "release_dates": {"success": False}})
    assert evaluate_content("movie", partial)[0] == "unknown"
    merged = merge_content_evidence(complete, partial)
    assert merged == complete
    assert evaluate_content("movie", merged)[0] == "allow"
    other = normalize_tmdb_evidence("movie", {"id": 56, "adult": False})
    assert evaluate_content("movie", merge_content_evidence(complete, other))[0] == "unknown"
    cleared = normalize_tmdb_evidence("movie", {"id": 55, "adult": False,
                                               "release_dates": {"results": []}, "keywords": {"keywords": []}})
    assert evaluate_content("movie", merge_content_evidence(complete, cleared))[0] == "unknown"


def test_nfo_country_qualification_and_conflict_are_preserved():
    assert evaluate_content("movie", normalize_nfo_evidence("G"))[0] == "unknown"
    assert evaluate_content("movie", normalize_nfo_evidence("UK:Rated U"))[0] == "allow"
    parsed = normalize_nfo_evidence("US:G / GB:12A")
    assert evaluate_content("movie", parsed)[0] == "block"
    assert parsed["certifications"] == [{"country": "GB", "certification": "12A"},
                                         {"country": "US", "certification": "G"}]


def test_evidence_accepts_database_json_and_bounds_malformed_values():
    value = evidence(("US", "G"))
    assert evaluate_content("movie", json.dumps(value))[0] == "allow"
    assert evaluate_content("movie", "not json")[0] == "unknown"
    assert normalize_content_evidence({"available": [None, {}, "adult"]})["available"] == ["adult"]


@pytest.mark.parametrize("example,ratings,genre_ids,keywords", [
    ("企鹅类家庭动作动画", [("US", "PG"), ("GB", "U")], [16, 10751, 28],
     ["cartoon violence", "martial arts", "weapons"]),
    ("马达加斯加类冒险动画", [("US", "PG"), ("GB", "U")], [16, 10751, 12],
     ["comic violence"]),
    ("神偷奶爸类喜剧动画", [("US", "PG"), ("GB", "U")], [16, 10751, 80, 35],
     ["supervillain", "slapstick violence"]),
    ("冰川时代类家庭动画", [("US", "PG")], [16, 10751, 12], ["fantasy violence"]),
    ("龙猫类全年龄动画", [("US", "G"), ("GB", "U"), ("JP", "G")], [16, 10751, 14],
     ["forest", "imaginary friend"]),
])
def test_family_animation_examples_are_admitted_by_evidence_not_title(example, ratings, genre_ids, keywords):
    # Synthetic genre/rating combinations represent these families of films;
    # the classifier deliberately receives no title, franchise or TMDB ID.
    value = {**evidence(*ratings, keywords=keywords), "genre_ids": genre_ids}
    status, reason = evaluate_content("movie", value)
    assert status == "allow", (example, reason)


@pytest.mark.parametrize("kind,rating", [
    ("movie", "PG"), ("tv", "TV-Y7"), ("tv", "TV-Y7-FV"), ("episode", "TV-Y7 (FV)"),
])
def test_guidance_ratings_require_family_animation(kind, rating):
    value = evidence(("US", rating))
    assert evaluate_content(kind, value)[0] == "unknown"
    assert evaluate_content(kind, value, genre_ids=[16])[0] == "unknown"
    assert evaluate_content(kind, value, genre_ids=[10751])[0] == "unknown"
    assert evaluate_content(kind, value, genre_ids=[16, 10751])[0] == "allow"


def test_tv_kids_genre_is_sufficient_child_audience_evidence_for_animation():
    value = evidence(("US", "TV-Y7-FV"), keywords=["fantasy violence", "fighting"])
    assert evaluate_content("tv", value, genre_ids=[16, 10762, 10759])[0] == "allow"
    for rating in ("TV-Y7-V", "TV-Y7-FV V", "TV-Y7 S", "TV-Y7 L", "TV-Y7 D", "TV-G FV"):
        assert evaluate_content("tv", evidence(("US", rating)), genre_ids=[16, 10762])[0] == "block"
    assert evaluate_content("tv", evidence(("US", "TV-Y7 XYZ")), genre_ids=[16, 10762])[0] == "unknown"


@pytest.mark.parametrize("keyword", [
    "cartoon violence and gore", "strong cartoon violence", "brutal fighting", "graphic combat",
    "sexual violence", "blood", "torture", "murder", "adult animation", "nudity", "sexuality",
    "drug use", "gunfight", "war", "血腥打斗", "性侵", "残酷格斗", "枪战", "裸露",
])
def test_family_animation_does_not_bypass_strong_risks(keyword):
    value = evidence(("US", "PG"), keywords=["cartoon violence", keyword])
    assert evaluate_content("movie", value, genre_ids=[16, 10751, 28])[0] == "block"


@pytest.mark.parametrize("genre_id", [27, 53, 10752, 10768])
def test_family_animation_does_not_bypass_horror_thriller_or_war(genre_id):
    assert evaluate_content("movie", evidence(("US", "PG")), genre_ids=[16, 10751, genre_id])[0] == "block"


@pytest.mark.parametrize("rating", ["PG-13", "R", "NC-17"])
def test_higher_rating_still_vetoes_family_animation_with_another_low_rating(rating):
    value = evidence(("US", rating), ("GB", "U"), keywords=["cartoon violence"])
    assert evaluate_content("movie", value, genre_ids=[16, 10751])[0] == "block"


def test_tmdb_pg_admission_requires_same_identity_genre_evidence_and_complete_sources():
    value = normalize_tmdb_evidence("movie", {
        "id": 88, "adult": False,
        "release_dates": {"results": [{"iso_3166_1": "US", "release_dates": [{"certification": "PG"}]}]},
        "keywords": {"keywords": []},
    })
    assert evaluate_content("movie", value, ["动画", "家庭"], [16, 10751])[0] == "unknown"
    value["genre_ids"] = [16, 10751]
    value["available"].append("genre_ids")
    assert evaluate_content("movie", value)[0] == "allow"
    value["available"].remove("keywords")
    assert evaluate_content("movie", value)[0] == "unknown"


def test_country_qualified_nfo_pg_uses_its_local_animation_and_family_genres():
    value = normalize_nfo_evidence("GB:PG", ["轻微卡通暴力"])
    assert evaluate_content("movie", value, ["动画", "家庭"])[0] == "allow"
    assert evaluate_content("movie", normalize_nfo_evidence("PG"), ["动画", "家庭"])[0] == "unknown"
    assert evaluate_content("movie", normalize_nfo_evidence("US:PG / GB:12A"), ["动画", "家庭"])[0] == "block"


def test_parent_audience_context_only_supplies_episode_genres_not_ratings_or_risk_clearance():
    episode = normalize_nfo_evidence("US:TV-G", ["cartoon violence"])
    assert evaluate_content("episode", episode)[0] == "block"
    assert evaluate_content("episode", episode, family_animation_context=True)[0] == "allow"
    assert evaluate_content("movie", normalize_nfo_evidence("US:PG"), family_animation_context=True)[0] == "unknown"
    assert evaluate_content("episode", normalize_nfo_evidence(""), family_animation_context=True)[0] == "unknown"
    for rating, keywords in (("US:TV-14", []), ("US:TV-G", ["gore"]), ("US:TV-G", ["sexuality"])):
        assert evaluate_content("episode", normalize_nfo_evidence(rating, keywords),
                                family_animation_context=True)[0] == "block"
