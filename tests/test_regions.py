"""regions 产地映射与标签清洗回归网。"""
from app.regions import (REGION_ASIA_OTHER, REGION_HUAYU, REGION_JAPAN,
                         REGION_KOREA, REGION_OTHER, REGION_UNKNOWN,
                         REGION_WEST, country_name, country_to_region,
                         normalize_tags, resolve)


def test_country_to_region():
    assert country_to_region("CN") == REGION_HUAYU
    assert country_to_region("jp") == REGION_JAPAN
    assert country_to_region("KR") == REGION_KOREA
    assert country_to_region("US") == REGION_WEST
    assert country_to_region("TH") == REGION_ASIA_OTHER
    assert country_to_region("BR") == REGION_OTHER
    assert country_to_region("") == REGION_UNKNOWN


def test_resolve_primary_and_region():
    assert resolve(["US"], "en") == ("US", REGION_WEST)
    assert resolve([], "zh") == ("CN", REGION_HUAYU)
    assert resolve([], "xx") == ("", REGION_UNKNOWN)
    assert resolve(["fr", "de"], "fr") == ("FR", REGION_WEST)


def test_country_name_fallback():
    assert country_name("US") == "美国"
    assert country_name("ZZ") == "ZZ"
    assert country_name("") == REGION_UNKNOWN


def test_normalize_tags():
    assert normalize_tags([" 科幻 ", "科幻", "", None, "动作"]) == ["科幻", "动作"]
    assert normalize_tags(["x" * 30]) == ["x" * 20]
    assert len(normalize_tags([f"t{i}" for i in range(30)])) == 20
    assert normalize_tags("not-a-list") == []
