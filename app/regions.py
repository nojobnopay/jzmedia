"""产地归一：存原始事实（ISO国家+语言），大区只做派生视图。

约定：
- DB 存 origin_country（主产地ISO，如 CN/HK/US，空=未知）、origin_countries（JSON全量）、original_language。
- region 为派生大区：华语 / 日本 / 韩国 / 欧美 / 其他亚洲 / 其他 / 未知。
- 映射表集中在此文件，改分组不用重爬，只需重算 region（回填口）。
"""

REGION_HUAYU = "华语"
REGION_JAPAN = "日本"
REGION_KOREA = "韩国"
REGION_WEST = "欧美"
REGION_ASIA_OTHER = "其他亚洲"
REGION_OTHER = "其他"
REGION_UNKNOWN = "未知"

REGION_ORDER = [REGION_HUAYU, REGION_JAPAN, REGION_KOREA, REGION_WEST,
                REGION_ASIA_OTHER, REGION_OTHER, REGION_UNKNOWN]

HUAYU_COUNTRIES = {"CN", "HK", "TW", "MO"}

# 其他亚洲：东南亚 + 南亚 + 中亚 + 日韩中之外的亚洲（含中东）
ASIA_OTHER_COUNTRIES = {
    "TH", "SG", "MY", "ID", "PH", "VN", "MM", "KH", "LA", "BN", "TL",
    "IN", "NP", "PK", "BD", "LK", "BT", "MV",
    "MN", "KZ", "UZ", "KG", "TJ", "TM", "AF",
    "IR", "IQ", "SA", "AE", "QA", "KW", "BH", "OM", "YE", "JO", "LB",
    "SY", "IL", "PS", "TR", "GE", "AM", "AZ",
}

# 欧美：北美 + 欧洲 + 大洋洲（拉美/非洲归“其他”，片量极少时避免大区失衡）
WEST_COUNTRIES = {
    "US", "CA",
    "GB", "IE", "FR", "DE", "IT", "ES", "PT", "NL", "BE", "LU", "CH", "AT",
    "SE", "NO", "DK", "FI", "IS",
    "GR", "PL", "CZ", "SK", "SI", "HR", "RS", "BA", "ME", "MK", "AL",
    "BG", "RO", "HU", "EE", "LV", "LT", "UA", "BY", "MD", "RU",
    "AU", "NZ",
}

# 华语细分（CN/HK/TW/MO）中文名统一在下方 COUNTRY_NAMES；不再单独维护（评审 R10-D5）

# 常用国家中文名（facets展示用，未收录回退ISO码）
COUNTRY_NAMES = {
    "CN": "中国大陆", "HK": "香港", "TW": "台湾", "MO": "澳门",
    "JP": "日本", "KR": "韩国",
    "US": "美国", "GB": "英国", "FR": "法国", "DE": "德国", "IT": "意大利",
    "ES": "西班牙", "CA": "加拿大", "AU": "澳大利亚", "RU": "俄罗斯",
    "TH": "泰国", "SG": "新加坡", "MY": "马来西亚", "ID": "印度尼西亚",
    "PH": "菲律宾", "VN": "越南", "IN": "印度",
    "BR": "巴西", "MX": "墨西哥", "NZ": "新西兰", "IE": "爱尔兰",
    "NL": "荷兰", "BE": "比利时", "CH": "瑞士", "SE": "瑞典", "NO": "挪威",
    "DK": "丹麦", "FI": "芬兰", "PL": "波兰", "CZ": "捷克", "GR": "希腊",
    "PT": "葡萄牙", "AT": "奥地利",
}

# original_language 回退（无 production_countries 时用；覆盖有限，未收录语言→未知，
# 属预期：TMDB 绝大多数条目都有 production_countries，评审 R10-B5 仅注记）
LANG_FALLBACK = {
    "zh": "CN", "ja": "JP", "ko": "KR", "en": "US",
    "fr": "FR", "de": "DE", "it": "IT", "es": "ES", "th": "TH",
    "ru": "RU",
}


def country_to_region(code: str) -> str:
    if not code:
        return REGION_UNKNOWN
    code = code.upper()
    if code in HUAYU_COUNTRIES:
        return REGION_HUAYU
    if code == "JP":
        return REGION_JAPAN
    if code == "KR":
        return REGION_KOREA
    if code in WEST_COUNTRIES:
        return REGION_WEST
    if code in ASIA_OTHER_COUNTRIES:
        return REGION_ASIA_OTHER
    return REGION_OTHER


def resolve(countries: list, original_language: str | None = None) -> tuple[str, str]:
    """返回 (主产地ISO, 大区)。countries 为 TMDB production_countries ISO 列表。"""
    codes = [str(c or "").upper() for c in (countries or []) if str(c or "").strip()]
    primary = codes[0] if codes else ""
    if not primary and original_language:
        lang = str(original_language).split("-")[0].lower()
        primary = LANG_FALLBACK.get(lang, "")
    if not primary:
        return "", REGION_UNKNOWN
    return primary, country_to_region(primary)


def country_name(code: str) -> str:
    """ISO → 中文名；未收录回退大写 ISO；入参防御（评审 B7/R10-B3：非 str 不再 AttributeError）"""
    c = str(code or "").strip()
    if not c:
        return REGION_UNKNOWN
    return COUNTRY_NAMES.get(c.upper(), c.upper())


def normalize_tags(tags) -> list[str]:
    """自定义标签归一：去首尾空格/压内部空白/去重保序/截断防脏数据。"""
    if not isinstance(tags, list):
        return []
    out, seen = [], set()
    for t in tags:
        s = " ".join(str(t or "").split())
        if not s or s in seen:
            continue
        if len(s) > 20:
            # 先截断后去重（评审 R10-B6）：超长标签截断后若与已有重复，seen 会挡住后者，
            # 保留先出现的截断值；正常标签远短于 20，无实际影响
            s = s[:20]
        seen.add(s)
        out.append(s)
        if len(out) >= 20:
            break
    return out
