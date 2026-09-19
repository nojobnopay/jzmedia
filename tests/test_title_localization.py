"""标题本地化（2026-09 用户反馈）：TMDB 缺中文主标题时用 alternative_titles 的中文别名。

实证：Top Gun: Maverick（361743）的 zh-CN 记录 title 回退英文原名，但简介/tagline/
系列/类型均为中文，中文名《壮志凌云*》只在别名表里（TMDB 行为，Plex 也读别名）。
"""
from app.scanner.match import meta_from_detail, pick_display_title


def _alt(*pairs):
    return {"alternative_titles": {
        "titles": [{"iso_3166_1": a, "title": b} for a, b in pairs]}}


def test_cjk_primary_kept():
    d = {"title": "告白", **_alt(("CN", "别的名字"))}
    assert pick_display_title(d, "zh-CN") == "告白"


def test_english_primary_uses_zh_cn_alt():
    d = {"title": "Top Gun: Maverick",
         **_alt(("TW", "捍衛戰士：獨行俠"), ("CN", "壮志凌云：独行侠"))}
    assert pick_display_title(d, "zh-CN") == "壮志凌云：独行侠"


def test_configured_region_first():
    d = {"title": "X", **_alt(("CN", "大陆名"), ("TW", "台名"))}
    assert pick_display_title(d, "zh-TW") == "台名"


def test_fallback_to_other_zh_region():
    d = {"title": "X", **_alt(("TW", "台名"))}
    assert pick_display_title(d, "zh-CN") == "台名"


def test_non_zh_language_untouched():
    d = {"title": "Top Gun: Maverick", **_alt(("CN", "壮志凌云：独行侠"))}
    assert pick_display_title(d, "en-US") == "Top Gun: Maverick"


def test_empty_or_same_or_non_cjk_alt_ignored():
    assert pick_display_title({"title": "X"}, "zh-CN") == "X"
    assert pick_display_title({"title": "X", **_alt(("CN", ""))}, "zh-CN") == "X"
    assert pick_display_title({"title": "X", **_alt(("CN", "X"))}, "zh-CN") == "X"
    assert pick_display_title({"title": "X", **_alt(("CN", "Plain English"))},
                              "zh-CN") == "X"


def test_meta_from_detail_sets_title_keeps_original():
    d = {"id": 361743, "title": "Top Gun: Maverick",
         "original_title": "Top Gun: Maverick", "release_date": "2022-05-21",
         "genres": [], "production_countries": [], "original_language": "en",
         **_alt(("CN", "壮志凌云：独行侠"))}
    meta = meta_from_detail(d)
    assert meta["title"] == "壮志凌云：独行侠"
    assert meta["original_title"] == "Top Gun: Maverick"
    assert meta["year"] == 2022


def test_meta_from_detail_original_fallback():
    """original_title 缺失时回退主标题（不丢英文原名，供匹配/别名展示）。"""
    d = {"id": 1, "title": "Some Movie", "release_date": "2001-01-01",
         "genres": [], "production_countries": [], "original_language": "en",
         "alternative_titles": {"titles": []}}
    meta = meta_from_detail(d)
    assert meta["title"] == "Some Movie"
    assert meta["original_title"] == "Some Movie"
