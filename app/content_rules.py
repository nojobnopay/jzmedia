"""Evidence-based children's catalogue rules; never changes a privacy choice.

Ratings describe age suitability, not the absence of every violent scene. Only
explicit country/media-specific low ratings can admit an item; risk evidence
can veto them. Missing evidence never becomes a positive safety assertion.
"""
import json
import re
import unicodedata


RULE_VERSION = 2
_MOVIE_RATINGS = {
    "US": ({"G"}, {"PG"}, {"PG-13", "R", "NC-17", "X"}),
    "GB": ({"U"}, {"PG"}, {"12", "12A", "15", "18", "R18"}),
    "HK": ({"I"}, set(), {"II", "IIA", "IIB", "III"}),
    "JP": ({"G"}, set(), {"PG12", "R15+", "R18+"}),
}
_TV_RATINGS = {
    "US": ({"TV-Y", "TV-G"}, {"TV-Y7"}, {"TV-PG", "TV-14", "TV-MA"}),
}
_UNRATED = {"", "NR", "N/A", "UNRATED", "NOT RATED", "NOT RATED YET", "未分级", "未分級"}
_COUNTRIES = {"UK": "GB", "USA": "US", "UNITED STATES": "US",
              "UNITED KINGDOM": "GB", "JAPAN": "JP", "HONG KONG": "HK"}
_HARD_RISK_GENRE_IDS = {27, 53, 10752, 10768}
_HARD_RISK_GENRES = {
    "horror", "thriller", "war", "war & politics", "恐怖", "惊悚", "驚悚", "战争", "戰爭",
}
_CONFLICT_GENRE_IDS = {28, 80, 10759}
_CONFLICT_GENRES = {"action", "action & adventure", "crime", "动作", "動作", "动作冒险", "動作冒險", "犯罪"}
_ANIMATION_GENRES = {"animation", "动画", "動畫"}
_FAMILY_GENRES = {"family", "kids", "children", "家庭", "儿童", "兒童", "少儿", "少兒"}
# Exact phrases only. A cartoon prefix must never hide a separate strong risk
# such as "cartoon violence and gore". Ratings and family/animation evidence
# are still required before any of these conflicts can be tolerated.
_CARTOON_CONFLICT = re.compile(
    r"(?:(?:very )?mild )?(?:cartoon|animated|comic|slapstick|fantasy) violence|"
    r"(?:轻微|輕微)?(?:卡通|动画|動畫|滑稽|幻想)(?:暴力|打斗|打鬥)", re.IGNORECASE)
_GENERAL_CONFLICT = re.compile(
    r"\b(?:combat|martial arts|fight(?:ing)?|battle|bullying|weapons?|guns?)\b", re.IGNORECASE)
_GENERAL_CONFLICT_CJK = re.compile(r"打斗|打鬥|武打|格斗|格鬥|斗殴|鬥毆|战斗|戰鬥")
# Applied to provider keywords/tags only, never a title or plot substring.
_RISK_WORDS = re.compile(
    r"\b(?:violen(?:ce|t)|gore|gory|blood(?:y|shed)?|torture|murder|killer|"
    r"slasher|rape|sexual(?:ity)?|sex|porn(?:ography|ographic)?|nudity|erotic|"
    r"horror|terrorism|suicide|war|combat|gunfight|shootout|martial arts|"
    r"fight(?:ing)?|battle|bullying|weapons?|guns?|massacre|zombies?|adult animation|"
    r"drug abuse|drug use|domestic abuse|child abuse)\b", re.IGNORECASE)
_STRONG_CONFLICT = re.compile(r"\b(?:graphic|brutal|sadistic|sexual|bloody|intense|strong)\b|"
                              r"血腥|残酷|殘酷|残忍|殘忍|激烈|强烈|強烈", re.IGNORECASE)
_RISK_CJK = re.compile(r"暴力|血腥|虐杀|虐殺|虐待|凶杀|兇殺|杀人|殺人|色情|情色|"
                       r"性侵|强奸|強姦|裸露|恐怖|战争|戰爭|枪战|槍戰|自杀|自殺|吸毒|"
                       r"打斗|打鬥|武打|格斗|格鬥|斗殴|鬥毆|战斗|戰鬥|丧尸|喪屍")
_TV_CODE = re.compile(r"^(TV-(?:Y7|Y|G|PG|14|MA))(?:[\s\-(/]+([A-Z ,/\-)]+))?$")


def _object(value) -> dict:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return {}
    return value if isinstance(value, dict) else {}


def _text(value) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).strip()


def _list(value) -> list:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return [value] if value else []
    return value if isinstance(value, list) else []


def normalize_content_evidence(value) -> dict:
    """Bound and normalize stored/provider evidence, accepting JSON at DB edges."""
    raw = _object(value)
    certs = set()
    for entry in _list(raw.get("certifications"))[:500]:
        if not isinstance(entry, dict):
            continue
        country = _text(entry.get("country") or entry.get("iso_3166_1")).upper()
        country = _COUNTRIES.get(country, country)
        code = _text(entry.get("certification") or entry.get("rating")).upper()
        if code:
            certs.add((country[:50], code[:100]))
    keywords = sorted({_text(k).lower()[:200] for k in _list(raw.get("keywords"))[:1000]
                       if isinstance(k, str) and _text(k)})
    genre_ids = set()
    for value in _list(raw.get("genre_ids"))[:100]:
        try:
            genre_ids.add(int(value))
        except (TypeError, ValueError):
            continue
    adult = raw.get("adult") if isinstance(raw.get("adult"), bool) else None
    available = raw.get("available")
    if not isinstance(available, list):
        available = [key for key in ("adult", "certifications", "keywords", "genre_ids") if key in raw]
    return {"version": RULE_VERSION, "source": _text(raw.get("source"))[:40],
            "source_id": _text(raw.get("source_id"))[:512], "adult": adult,
            "certifications": [{"country": country, "certification": code}
                               for country, code in sorted(certs)],
            "keywords": keywords, "genre_ids": sorted(genre_ids),
            "available": sorted({v for v in available if isinstance(v, str)}
                                & {"adult", "certifications", "keywords", "genre_ids"})}


def normalize_tmdb_evidence(kind: str, detail: dict) -> dict:
    """Read successful appended responses; missing/error sections stay unavailable."""
    if isinstance(detail.get("content_evidence"), dict):
        return normalize_content_evidence(detail["content_evidence"])
    raw = {"source": "tmdb", "source_id": str(detail.get("id") or detail.get("tmdb_id") or ""),
           "available": []}
    if isinstance(detail.get("adult"), bool):
        raw["adult"] = detail["adult"]
        raw["available"].append("adult")
    if isinstance(detail.get("genres"), list):
        raw["genre_ids"] = [item.get("id") for item in detail["genres"] if isinstance(item, dict)]
        raw["available"].append("genre_ids")
    tv = kind in ("tv", "show", "episode")
    ratings = detail.get("content_ratings" if tv else "release_dates")
    if isinstance(ratings, dict) and isinstance(ratings.get("results"), list):
        certs = []
        for region in ratings["results"]:
            if not isinstance(region, dict):
                continue
            entries = [region] if tv else _list(region.get("release_dates"))
            for entry in entries:
                if isinstance(entry, dict):
                    certs.append({"country": region.get("iso_3166_1"),
                                  "certification": entry.get("rating" if tv else "certification")})
        raw["certifications"] = certs
        raw["available"].append("certifications")
    keywords = detail.get("keywords")
    key = "results" if tv else "keywords"
    if isinstance(keywords, dict) and isinstance(keywords.get(key), list):
        raw["keywords"] = [item.get("name") for item in keywords[key] if isinstance(item, dict)]
        raw["available"].append("keywords")
    return normalize_content_evidence(raw)


def normalize_nfo_evidence(mpaa, tags=None, source_id: str = "") -> dict:
    """NFO ratings need an explicit country prefix; bare 'G' remains unknown.

    Kodi's mpaa is a free-form country-specific string. Preserve every code,
    including unrecognized ones, so a round trip cannot erase a conflicting
    rating. Slash separators are only split when followed by a country prefix.
    """
    certs = []
    for value in _list(mpaa):
        for part in re.split(r"\s*[/;|]\s*(?=[A-Za-z ]{2,20}:)", _text(value)):
            prefix, sep, code = part.partition(":")
            country = prefix.strip().upper() if sep else ""
            country = _COUNTRIES.get(country, country)
            code = re.sub(r"^RATED\s+", "", (code if sep else part).strip(), flags=re.I)
            if code:
                certs.append({"country": country, "certification": code})
    return normalize_content_evidence({
        "source": "nfo", "source_id": source_id, "certifications": certs,
        "keywords": _list(tags), "available": (["certifications"] if mpaa is not None else [])
        + (["keywords"] if tags is not None else [])})


def merge_content_evidence(previous, incoming) -> dict:
    """Keep missing sections only for the same source identity, not a new match."""
    old = normalize_content_evidence(previous)
    new = normalize_content_evidence(incoming)
    if old["source"] != new["source"] or old["source_id"] != new["source_id"]:
        return new
    for key in ("adult", "certifications", "keywords", "genre_ids"):
        if key not in new["available"] and key in old["available"]:
            new[key] = old[key]
            new["available"].append(key)
    return normalize_content_evidence(new)


def is_family_animation(evidence, genres=None, genre_ids=None) -> bool:
    """Recognize the intended child audience without relying on a film title.

    Remote genre evidence belongs to the matched identity. Display genres
    alone cannot turn an incomplete TMDB response into a PG admission. NFO
    genres are local metadata from the same work and can supply this context.
    """
    ev = normalize_content_evidence(evidence)
    ids = set(ev["genre_ids"])
    names = set()
    if ev["source"] != "tmdb":
        for value in _list(genre_ids):
            try:
                ids.add(int(value))
            except (TypeError, ValueError):
                continue
        names = {_text(g).lower() for g in _list(genres) if isinstance(g, str)}
    return (16 in ids or bool(names & _ANIMATION_GENRES)) and (
        bool(ids & {10751, 10762}) or bool(names & _FAMILY_GENRES))


def evaluate_content(kind, evidence, genres=None, genre_ids=None, *,
                     family_animation_context=False) -> tuple[str, str]:
    """Return allow/block/unknown and a short, user-facing explanation."""
    ev = normalize_content_evidence(evidence)
    if ev["adult"] is True:
        return "block", "元数据标记为成人内容"
    ids = set(ev["genre_ids"])
    for value in _list(genre_ids):
        try:
            ids.add(int(value))
        except (TypeError, ValueError):
            continue
    names = {_text(g).lower() for g in _list(genres) if isinstance(g, str)}
    if ids & _HARD_RISK_GENRE_IDS or names & _HARD_RISK_GENRES:
        return "block", "含战争、恐怖或惊悚等风险类型"
    family_animation = is_family_animation(ev, genres, genre_ids) or (
        kind == "episode" and family_animation_context)
    conflicts = bool(ids & _CONFLICT_GENRE_IDS or names & _CONFLICT_GENRES)
    for keyword in ev["keywords"]:
        if _CARTOON_CONFLICT.fullmatch(keyword):
            conflicts = True
            continue
        remaining = _GENERAL_CONFLICT.sub("", keyword)
        remaining = _GENERAL_CONFLICT_CJK.sub("", remaining)
        if (_RISK_WORDS.search(remaining) or _RISK_CJK.search(remaining)
                or _STRONG_CONFLICT.search(keyword) and remaining != keyword):
            return "block", f"含风险关键词：{keyword[:60]}"
        if remaining != keyword:
            conflicts = True
    table = _TV_RATINGS if kind in ("tv", "show", "episode") else _MOVIE_RATINGS
    allowed = []
    conditional = []
    unrecognized = False
    for cert in ev["certifications"]:
        country, code = cert["country"], cert["certification"]
        if code in _UNRATED:
            continue
        if country == "US" and table is _TV_RATINGS:
            match = _TV_CODE.fullmatch(code)
            if match:
                code = match[1]
                descriptors = set(re.findall(r"FV|[VSLD]", match[2] or ""))
                if descriptors - {"FV"} or "FV" in descriptors and code != "TV-Y7":
                    return "block", f"分级含内容风险提示：{cert['certification']}"
                if re.sub(r"FV|[VSLD\s,/()\-]", "", match[2] or ""):
                    unrecognized = True
        mapping = table.get(country)
        if not mapping:
            continue
        if code in mapping[2]:
            return "block", f"分级不符合儿童模式：{country}:{code}"
        if code in mapping[0]:
            allowed.append(f"{country}:{code}")
        elif code in mapping[1]:
            conditional.append(f"{country}:{cert['certification']}")
        else:
            unrecognized = True
    if unrecognized:
        return "unknown", "支持地区中存在无法识别的分级，请家长确认"
    # An appended request failure must not admit content without its risk data.
    if ev["source"] == "tmdb" and not {"adult", "certifications", "keywords"}.issubset(ev["available"]):
        return "unknown", "分级或风险资料尚不完整，暂不在儿童模式显示"
    if conditional and not family_animation:
        return "unknown", "PG 或 TV-Y7 分级需同时有家庭／儿童动画资料，请家长确认"
    if conflicts and not family_animation:
        return "block", "含动作、犯罪或打斗等提示，缺少家庭／儿童动画依据"
    if conditional or allowed and conflicts:
        ratings = "、".join(allowed + conditional)
        return "allow", f"家庭／儿童动画，分级：{ratings}；允许轻微卡通或幻想冲突，建议家长陪看"
    if allowed:
        return "allow", f"低风险分级：{'、'.join(allowed)}；未发现已知风险提示"
    return "unknown", "缺少可识别的儿童适宜分级，请家长确认"


def content_fields(kind, evidence, genres=None, genre_ids=None) -> dict:
    """Fields shared by metadata persistence; excludes all user-owned choices."""
    normalized = normalize_content_evidence(evidence)
    status, reason = evaluate_content(kind, normalized, genres, genre_ids)
    return {"content_evidence": normalized, "children_auto_status": status,
            "children_reason": reason}


def nfo_certification_text(evidence) -> str:
    """Country-qualified ratings only, never passwords or local mode choices."""
    certs = normalize_content_evidence(evidence)["certifications"]
    return " / ".join(f"{c['country']}:{c['certification']}" if c["country"]
                      else c["certification"] for c in certs)
