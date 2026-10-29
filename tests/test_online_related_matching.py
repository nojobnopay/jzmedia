"""Metadata navigation hints never establish or merge playback identities."""

import pytest

from app.online.related_matching import search_root, select, title_parts


def item(identity, title, **metadata):
    return {"external_id": identity, "title": title, "category_id": "12", "category": "国产剧",
            "actors": [], "directors": [], "genres": [], **metadata}


@pytest.mark.parametrize(("title", "root", "season"), [
    ("青山书院第二季", "青山书院", 2), ("青山书院第十二季", "青山书院", 12),
    ("青山书院第一百零二季", "青山书院", 102), ("青山书院第九百九十九季", "青山书院", 999),
    ("The Academy S2", "The Academy", 2), ("The Academy Season 12", "The Academy", 12),
    ("青山书院Ｓ２", "青山书院", 2), ("青山书院", "青山书院", None),
    ("青山书院第0季", "青山书院第0季", None), ("青山书院第1000季", "青山书院第1000季", None),
    ("青山书院第二季 国语版", "青山书院第二季 国语版", None),
    ("青山书院第二季(2026)", "青山书院第二季(2026)", None),
    ("News2", "News2", None), ("青山书院第十十季", "青山书院第十十季", None),
])
def test_only_explicit_bounded_terminal_season_is_removed(title, root, season):
    selected_root, _key, selected_season = title_parts(title)
    assert (selected_root, selected_season) == (root, season)


def test_seasons_order_by_number_and_allow_year_and_cast_changes():
    current = item("s2", "青山书院第二季", year="2026", actors=["演员甲"], directors=["导演甲"])
    candidates = [item("s4", "青山书院第四季", year="2030", actors=["演员丁"]),
                  item("s1", "青山书院第一季", year="2020", actors=["演员乙"]), current,
                  item("s3", "青山书院第三季", year="2028", directors=["导演乙"])]
    result = select(current, candidates)
    assert result["current_season"] == 2
    assert [row["external_id"] for row in result["series"]] == ["s1", "s3", "s4"]
    assert [row["season"] for row in result["series"]] == [1, 3, 4]
    assert all(row["reason"] == "inferred_season" for row in result["series"])


def test_unmarked_titles_missing_first_season_and_cross_category_ids_stay_independent():
    current = item("s2", "青山书院第二季")
    unmarked = item("unmarked", "青山书院")
    cross_category = item("movie", "青山书院第一季", category_id="1", category="电影")
    result = select(current, [current, unmarked, cross_category, item("s3", "青山书院第三季")])
    assert [row["external_id"] for row in result["series"]] == ["s3"]
    assert {row["external_id"] for row in result["name_related"]} == {"unmarked", "movie"}
    assert select(unmarked, [current])["series"] == []
    assert select(unmarked, [current])["current_season"] is None


def test_short_roots_require_positive_extra_evidence():
    current = item("echo2", "回声第二季", actors=["演员甲", "演员乙"], directors=["导演甲"])
    weak = item("echo1", "回声第一季")
    assert select(current, [weak])["series"] == []
    assert select(current, [weak])["name_related"][0]["external_id"] == "echo1"
    strong = {**weak, "actors": ["演员甲", "演员乙"], "directors": ["导演甲"]}
    assert select(current, [strong])["series"][0]["external_id"] == "echo1"
    assert search_root(item("tiny", "影第二季")) == ""


def test_duplicate_versions_and_current_season_copies_are_not_canonical_seasons():
    current = item("s2", "青山书院第二季")
    candidates = [item("other-s2", current["title"]), item("s1-a", "青山书院第一季"),
                  item("s1-b", "青山书院第一季"), item("dub", "青山书院第三季 国语版"),
                  item("remake", "新版青山书院第三季"), item("s4", "青山书院第四季")]
    result = select(current, candidates)
    assert [row["external_id"] for row in result["series"]] == ["s4"]
    assert {row["external_id"] for row in result["name_related"]} <= {row["external_id"] for row in candidates}
    assert {"other-s2", "s1-a", "s1-b"} <= {row["external_id"] for row in result["name_related"]}


def test_combined_region_and_language_identity_conflict_only_downgrades():
    current = item("s2", "青山书院第二季", area="中国大陆", language="国语")
    dub = item("s1", "青山书院第一季", area="内地", language="粤语")
    conflict = item("s3", "青山书院第三季", area="日本", language="日语")
    result = select(current, [dub, conflict])
    assert [row["external_id"] for row in result["series"]] == ["s1"]
    assert result["name_related"][0]["external_id"] == "s3"


def test_fallback_is_one_specific_category_disjoint_and_bounded():
    current = item("current", "青山书院第二季", genres=["剧情"])
    seasons = [item(f"s{index}", f"青山书院第{index}季") for index in range(1, 11) if index != 2]
    category = [current, *seasons, item("wrong", "不同类别", category_id="1"),
                *(item(f"similar{index}", f"其它剧集{index}", genres=["剧情"]) for index in range(20))]
    result = select(current, seasons, category)
    cards = [row for group in ("series", "related", "name_related") for row in result[group]]
    assert len(cards) <= 12
    assert len({row["external_id"] for row in cards}) == len(cards)
    assert all(row["category_id"] == "12" for row in result["related"])
    assert not {"current", "wrong"} & {row["external_id"] for row in cards}
