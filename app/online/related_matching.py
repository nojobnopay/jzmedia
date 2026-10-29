"""Conservative, bounded inferences over one source's already-read metadata.

These groups are navigation hints, never playback identities or automatic
continuations. An absent season suffix deliberately remains an absent season.
"""

from collections import Counter
import re
import unicodedata


MAX_CARDS = 12
_SEASON = re.compile(
    r"(?:第(?P<chinese>[0-9零〇一二两三四五六七八九十百]+)季"
    r"|(?<![a-z0-9])s\s*(?P<short>[0-9]{1,3})"
    r"|(?<![a-z0-9])season\s+(?P<english>[0-9]{1,3}))\s*\Z", re.IGNORECASE)
_DIGITS = dict(zip("零〇一二两三四五六七八九", (0, 0, 1, 2, 2, 3, 4, 5, 6, 7, 8, 9)))


def _number(value):
    if value.isascii() and value.isdigit():
        selected = int(value)
    elif not any(unit in value for unit in "十百"):
        selected = int("".join(str(_DIGITS[char]) for char in value))
    else:
        selected, pending, last_unit = 0, None, 1000
        for char in value:
            if char in _DIGITS:
                digit = _DIGITS[char]
                if pending is not None and pending != 0:
                    return None
                pending = digit
                continue
            unit = {"十": 10, "百": 100}[char]
            if unit >= last_unit or pending == 0:
                return None
            selected += (1 if pending is None else pending) * unit
            pending, last_unit = None, unit
        selected += pending or 0
    return selected if 1 <= selected <= 999 else None


def normalize(value):
    return "".join(unicodedata.normalize("NFKC", value).casefold().split()).strip("-_:：·.,，")


def title_parts(title):
    """Return the original search root, its comparison key and explicit season."""
    title = unicodedata.normalize("NFKC", title).strip()
    match = _SEASON.search(title)
    if match is not None:
        season = _number(next(group for group in match.groups() if group is not None))
        root = title[:match.start()].strip(" \t-_:：·.,，")
        if season is not None and normalize(root):
            return root, normalize(root), season
    return title, normalize(title), None


def search_root(current):
    root, key, _season = title_parts(current["title"])
    # A broad one-character query cannot establish a useful relation. Provider
    # query limits also apply to roots obtained from provider-owned titles.
    return root if 2 <= sum(char.isalnum() for char in key) and len(root) <= 100 else ""


def _values(item, field):
    return {normalize(value) for value in item.get(field, []) if value}


def _short_root_evidence(current, candidate):
    return (len(_values(current, "actors") & _values(candidate, "actors")) >= 2
            and bool(_values(current, "directors") & _values(candidate, "directors")))


def _region(value):
    aliases = {"中国大陆": "大陆", "内地": "大陆", "大陆": "大陆", "中国内地": "大陆",
               "美国": "美国", "日本": "日本", "韩国": "韩国", "英国": "英国",
               "中国香港": "香港", "香港": "香港", "中国台湾": "台湾", "台湾": "台湾"}
    return {aliases[part] for part in re.split(r"[,，/、|\s]+", value) if part in aliases}


def _language(value):
    aliases = {"汉语普通话": "普通话", "普通话": "普通话", "国语": "普通话", "汉语": "普通话",
               "英语": "英语", "日语": "日语", "韩语": "韩语", "粤语": "粤语"}
    return {aliases[part] for part in re.split(r"[,，/、|\s]+", value) if part in aliases}


def _identity_conflict(current, candidate):
    # A new cast or a new release year is normal between seasons. Only the
    # combination of explicit, incompatible region AND language downgrades it.
    first, second = _region(current.get("area", "")), _region(candidate.get("area", ""))
    languages = _language(current.get("language", "")), _language(candidate.get("language", ""))
    return bool(first and second and first.isdisjoint(second)
                and all(languages) and languages[0].isdisjoint(languages[1]))


def _card(item, reason):
    return {**item, "reason": reason, "season": title_parts(item["title"])[2]}


def select(current, candidates, category_items=()):
    """Return disjoint groups, preserving each source-specific external ID."""
    _root, root, current_season = title_parts(current["title"])
    root_length = sum(char.isalnum() for char in root)
    seen = {current["external_id"]}
    possible, names = [], []
    for item in candidates:
        identity = item["external_id"]
        if identity in seen:
            continue
        seen.add(identity)
        _original, candidate_root, season = title_parts(item["title"])
        same_root = bool(root and candidate_root == root)
        same_category = bool(current.get("category_id")
                             and item.get("category_id") == current["category_id"])
        strong_root = root_length >= 4 or root_length >= 2 and _short_root_evidence(current, item)
        if (same_root and current_season is not None and season is not None
                and season != current_season and same_category and strong_root
                and not _identity_conflict(current, item)):
            possible.append(_card(item, "inferred_season"))
        elif (same_root or root_length >= 4 and root in candidate_root
              or root_length >= 4 and candidate_root and candidate_root in root):
            names.append(_card(item, "name_related"))

    # Different IDs claiming the same season may be cuts, dubs or remakes. Do
    # not choose one as the season's canonical identity from a search summary.
    counts = Counter(item["season"] for item in possible)
    confirmed = []
    for item in possible:
        if counts[item["season"]] == 1:
            confirmed.append(item)
        else:
            names.append({**item, "reason": "name_related"})
    confirmed.sort(key=lambda item: (item["season"], item["external_id"]))
    confirmed = confirmed[:8]
    selected = {current["external_id"], *(item["external_id"] for item in possible),
                *(item["external_id"] for item in names)}
    related = []
    for item in category_items:
        identity = item["external_id"]
        if identity in selected or not current.get("category_id") or item.get("category_id") != current["category_id"]:
            continue
        selected.add(identity)
        related.append(_card(item, "same_category"))
    # Stable provider order breaks ties. Metadata overlap helps the one-page
    # fallback without any per-candidate detail or external recommendation API.
    related.sort(key=lambda item: -(
        2 * len(_values(current, "genres") & _values(item, "genres"))
        + len(_values(current, "actors") & _values(item, "actors"))))
    names.sort(key=lambda item: title_parts(item["title"])[1] != root)
    names = names[:min(4, MAX_CARDS - len(confirmed))]
    related = related[:min(8, MAX_CARDS - len(confirmed) - len(names))]
    return {"current_season": current_season, "series": confirmed,
            "related": related, "name_related": names}
