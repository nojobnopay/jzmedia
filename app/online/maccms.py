"""Bounded MacCMS Provide JSON reader; no player scripts or parser services."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import hashlib
from html.parser import HTMLParser
import json
import re
import time
from urllib.parse import unquote, urlsplit

from ..network.client import create_online_client, NetworkRequestError
from ..network.guard import NetworkOperation, NetworkOperationError, current_operation, operation_context
from ..network.targets import TargetPolicy, TargetPolicyError
from .providers import ProviderError


MAX_ROWS = 100
MAX_LINES = 16
MAX_ENTRIES = 2000
MAX_LINE_ENTRIES = 500
MAX_BODY = 2 * 1024**2
CATEGORY_TIMEOUT_SECONDS = 3.0
_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_YEAR = re.compile(r"[1-9][0-9]{3}\Z")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_detail_retry = ContextVar("online_foreground_detail_retry", default=None)
MIN_RETRY_SECONDS = 1.0


@contextmanager
def foreground_detail_retry(configuration, check):
    """Opt in one foreground detail read, never a background or media read.

    The callback revalidates the caller's access and complete configuration;
    only its opaque digest, not credentials or configuration, is scoped here.
    """
    if (not isinstance(configuration, str) or re.fullmatch(r"[0-9a-f]{64}", configuration) is None
            or not callable(check)):
        raise ValueError("invalid foreground detail retry")
    token = _detail_retry.set((configuration, check))
    try:
        yield
    finally:
        _detail_retry.reset(token)


@dataclass(frozen=True)
class Entry:
    label: str
    url: str = field(repr=False)
    transport: str | None = None
    reason: str = ""


@dataclass(frozen=True)
class Line:
    name: str
    entries: tuple[Entry, ...]

    @property
    def id(self):
        return "ln_" + hashlib.sha256(self.name.encode()).hexdigest()[:32]


@dataclass(frozen=True)
class Detail:
    external_id: str
    title: str
    kind: str
    description: str
    year: str
    lines: tuple[Line, ...]
    attribution: tuple[tuple[str, str], ...] = ()
    duration_seconds: float | None = None
    poster_url: str = field(default="", repr=False)
    metadata: dict = field(default_factory=dict)


def external_id(value):
    if type(value) is int:
        value = str(value)
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ProviderError("invalid_provider_response")
    return value


def episode_external_id(line_name, label):
    # Neither a changing signed URL nor a display order is an identity.
    serialized = json.dumps([line_name, label], ensure_ascii=False, separators=(",", ":"))
    return "maccms_" + hashlib.sha256(serialized.encode()).hexdigest()


class _PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, _attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        elif tag in {"br", "p", "div", "li"}:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def plain(value, limit=512, *, required=False):
    if value is None:
        value = ""
    if type(value) is int:
        value = str(value)
    if not isinstance(value, str) or len(value) > 16384:
        raise ProviderError("invalid_provider_response")
    parser = _PlainText()
    try:
        parser.feed(value)
        parser.close()
    except (ValueError, RecursionError):
        raise ProviderError("invalid_provider_response") from None
    text = " ".join(_CONTROL.sub(" ", "".join(parser.parts)).split())
    if required and (not text or len(text) > limit):
        raise ProviderError("invalid_provider_response")
    return text[:limit]


def _number(value, *, maximum, default=None):
    if value is None and default is not None:
        return default
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,10}", value):
        value = int(value)
    if type(value) is not int or not 0 <= value <= maximum:
        raise ProviderError("invalid_provider_response")
    return value


def _pairs(values):
    result = {}
    for key, value in values:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _invalid_constant(_value):
    raise ValueError


def decode(body):
    if not isinstance(body, bytes) or len(body) > MAX_BODY:
        raise ProviderError("provider_response_limit")
    try:
        payload = json.loads(body.decode("utf-8-sig"), object_pairs_hook=_pairs,
                             parse_constant=_invalid_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise ProviderError("invalid_provider_response") from None
    if not isinstance(payload, dict):
        raise ProviderError("invalid_provider_response")
    code = payload.get("code")
    if type(code) not in {int, str}:
        raise ProviderError("invalid_provider_response")
    if code not in (1, "1"):
        raise ProviderError("provider_upstream_error")
    rows = payload.get("list")
    if not isinstance(rows, list) or len(rows) > MAX_ROWS or any(not isinstance(row, dict) for row in rows):
        raise ProviderError("invalid_provider_response")
    return payload


@contextmanager
def _operation():
    current = current_operation()
    if current is not None:
        current.check()
        yield current
        return
    operation = NetworkOperation(time.monotonic() + 30)
    try:
        with operation_context(operation):
            yield operation
    finally:
        operation.close()


def fetch(source, params):
    try:
        with _operation() as operation:
            context = _detail_retry.get()
            retry = context is not None and isinstance(params, dict) and set(params) == {"ac", "ids"}

            def current():
                operation.check()
                if context is not None:
                    configuration, check = context
                    check(source, configuration)
                operation.check()

            for attempt in range(2 if retry else 1):
                current()
                # A fresh guarded client retains the same explicit source API
                # route; it never falls back to environment proxies or media.
                with create_online_client(source["id"], "api") as client:
                    current()  # Factory reads may race a configuration change.
                    try:
                        response = client.request("GET", source["api_base_url"], params=params,
                                                  headers={"Accept": "application/json"})
                    except NetworkRequestError as error:
                        # Cancellation and expired total deadlines win over an
                        # apparent transport timeout caused by socket abort.
                        current()
                        if error.category != "network_timeout":
                            raise
                        failure = error
                    else:
                        current()
                        if response.status_code in {502, 503, 504}:
                            failure = ProviderError("provider_upstream_error")
                        else:
                            if response.status_code == 429:
                                raise ProviderError("provider_rate_limited")
                            if response.status_code in {401, 403}:
                                raise ProviderError("provider_auth_failed")
                            if response.status_code == 404:
                                raise ProviderError("provider_not_found")
                            if response.status_code != 200:
                                raise ProviderError("provider_upstream_error")
                            return decode(response.content)
                # Close the real first reader before retrying, retaining the
                # caller's admission and original absolute operation deadline.
                current()
                if not retry or attempt or operation.remaining() < MIN_RETRY_SECONDS:
                    raise failure
    except NetworkOperationError as error:
        raise ProviderError("provider_timeout" if str(error) == "operation_timeout" else "provider_cancelled") from None
    except NetworkRequestError as error:
        if error.category in {"network_timeout", "operation_timeout", "target_dns_timeout"}:
            raise ProviderError("provider_timeout") from None
        if error.category == "operation_cancelled":
            raise ProviderError("provider_cancelled") from None
        if error.category == "response_body_limit":
            raise ProviderError("provider_response_limit") from None
        raise ProviderError("provider_network_error") from None


def _kind(row, *, lines=()):
    category = plain(row.get("type_name"), 128)
    if "电影" in category or category.endswith("片"):
        return "movie"
    if any(word in category for word in ("剧", "动漫", "动画", "综艺", "连续")):
        return "tv"
    if any(len(line.entries) > 1 for line in lines):
        return "tv"
    if category:
        return "movie"
    return "movie" if lines else "unknown"


def poster_url(row):
    value = row.get("vod_pic", "")
    return value if isinstance(value, str) and len(value) <= 4096 and not _CONTROL.search(value) else ""


def supports_year_filter(source):
    """Only the exact provider endpoint verified to implement upstream year."""
    options = source.get("provider_options", {})
    value = source.get("api_base_url", "")
    if (source.get("provider") != "maccms" or not isinstance(options, dict)
            or options.get("detail_action", "detail") != "detail"
            or not isinstance(value, str) or _CONTROL.search(value)):
        return False
    try:
        parsed = urlsplit(value)
        return (parsed.scheme == "https" and parsed.hostname == "bfzyapi.com"
                and parsed.port in (None, 443) and parsed.username is None and parsed.password is None
                and parsed.path in {"/api.php/provide/vod", "/api.php/provide/vod/"}
                and not parsed.query and not parsed.fragment)
    except (TypeError, ValueError):
        return False


def valid_year(year):
    return isinstance(year, str) and (not year or _YEAR.fullmatch(year) is not None)


def _optional_text(value, limit):
    # Optional upstream metadata is deliberately tolerant: malformed cast or
    # tags cannot make otherwise playable entries disappear.
    if not isinstance(value, str):
        return ""
    try:
        return plain(value, limit)
    except ProviderError:
        return ""


def _optional_id(value):
    if value in (None, "", 0, "0") or isinstance(value, bool):
        return ""
    try:
        return external_id(value)
    except ProviderError:
        return ""


def _metadata_values(value, *, limit=24, text_limit=80, slash=False):
    if isinstance(value, str):
        if len(value) > 16384:
            return []
        values = re.split(r"[,，、/|]" if slash else r"[,，、]", value)
    elif isinstance(value, list):
        values = value[:limit]
    else:
        return []
    result, seen = [], set()
    for raw in values:
        selected = _optional_text(raw, text_limit)
        if selected and selected not in seen:
            seen.add(selected)
            result.append(selected)
            if len(result) >= limit:
                break
    return result


def metadata(row):
    identity = _optional_id(row.get("type_id"))
    parent = _optional_id(row.get("type_id_1"))
    return {"category": _optional_text(row.get("type_name"), 128),
            "category_id": identity, "parent_category_id": parent if parent != identity else "",
            "remarks": _optional_text(row.get("vod_remarks"), 128),
            "genres": _metadata_values(row.get("vod_class"), limit=16, text_limit=64, slash=True),
            "actors": _metadata_values(row.get("vod_actor")),
            "directors": _metadata_values(row.get("vod_director"), limit=12),
            "area": _optional_text(row.get("vod_area"), 128),
            "language": _optional_text(row.get("vod_lang"), 128),
            "subtitle": _optional_text(row.get("vod_sub"), 256)}


def _categories(rows):
    if not isinstance(rows, list) or len(rows) > 256:
        raise ProviderError("invalid_provider_response")
    categories = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ProviderError("invalid_provider_response")
        identity = external_id(row.get("type_id"))
        if identity not in categories:
            parent = _optional_id(row.get("type_pid"))
            categories[identity] = {"id": identity,
                "name": plain(row.get("type_name"), 128, required=True), "parent_id": parent}
    for identity, category in categories.items():
        if category["parent_id"] == identity or category["parent_id"] not in categories:
            category["parent_id"] = ""
    # At most 256 nodes: walk each chain and detach every member of a cycle.
    # Children of those nodes retain valid links to the newly rooted members.
    for identity in categories:
        path, indexes = [], {}
        selected = identity
        while selected:
            if selected in indexes:
                for member in path[indexes[selected]:]:
                    categories[member]["parent_id"] = ""
                break
            indexes[selected] = len(path)
            path.append(selected)
            selected = categories[selected]["parent_id"]
    return list(categories.values())


def _search_result(payload, page):
    result, seen = [], set()
    for row in payload["list"]:
        identity = external_id(row.get("vod_id"))
        if identity in seen:
            raise ProviderError("invalid_provider_response")
        seen.add(identity)
        result.append({"external_id": identity, "title": plain(row.get("vod_name"), required=True),
                       "kind": _kind(row), "year": plain(row.get("vod_year"), 16),
                       "description": plain(row.get("vod_content"), 4000),
                       **metadata(row), "_poster_url": poster_url(row)})
    selected_page = _number(payload.get("page"), maximum=1000, default=page)
    count = _number(payload.get("pagecount"), maximum=1000000, default=1 if result else 0)
    total = _number(payload.get("total"), maximum=1000000000, default=len(result))
    if selected_page != page or result and count < page:
        raise ProviderError("invalid_provider_response")
    return {"items": result, "page": page, "page_count": min(count, 1000), "total": total}


def search(source, query, page):
    action = source.get("provider_options", {}).get("search_action", "list")
    return _search_result(fetch(source, {"ac": action, "wd": query, "pg": str(page)}), page)


class _CategoryCancellation:
    """Expose the child's abort to the parent's socket cancellation registry."""

    def __init__(self, operation):
        self.operation = operation

    def shutdown(self, _how):
        self.operation.abort()


def _discover_categories(source):
    parent = current_operation()
    if parent is not None:
        parent.check()
    deadline = time.monotonic() + CATEGORY_TIMEOUT_SECONDS
    child = NetworkOperation(min(deadline, parent.deadline) if parent is not None else deadline)
    cancellation = _CategoryCancellation(child)
    try:
        if parent is not None:
            parent.bind_socket(cancellation)
        # A separate Event makes optional discovery timeouts independent. The
        # registered link still shuts its actual sockets immediately when the
        # parent HTTP request is cancelled or exhausts its own deadline.
        with operation_context(child):
            result = fetch(source, {"ac": "list", "pg": "1"}).get("class", [])
            child.check()
        if parent is not None:
            parent.check()
        return result
    except (ProviderError, NetworkOperationError):
        if parent is not None:
            parent.check()
        return []
    finally:
        if parent is not None:
            parent.release_socket(cancellation)
        child.close()


def browse(source, page, category_id="", year=""):
    if category_id:
        category_id = external_id(category_id)
    if not valid_year(year):
        raise ProviderError("invalid_provider_request")
    if year and not supports_year_filter(source):
        raise ProviderError("provider_filter_unsupported")
    action = source.get("provider_options", {}).get("detail_action", "detail")
    params = {"ac": action, "pg": str(page)}
    if category_id:
        params["t"] = category_id
    if year:
        params["year"] = year
    payload = fetch(source, params)
    categories = payload.get("class", [])
    if not categories:
        categories = _discover_categories(source)
    selected = _categories(categories)
    result = _search_result(payload, page)
    parents = {category["id"]: category["parent_id"] for category in selected}
    for item in result["items"]:
        if not item["parent_category_id"]:
            item["parent_category_id"] = parents.get(item["category_id"], "")
    return {**result, "categories": selected}


def _entry(source, label, url, target=None):
    if not isinstance(url, str) or not url or len(url) > 8192:
        return Entry(label, "", reason="unsupported_media")
    try:
        # Canonicalization is syntax/host authorization only. DNS and pinned
        # socket checks are deliberately deferred to the requested media, not
        # repeated thousands of times while parsing an episode directory.
        target = target or TargetPolicy(source["id"], source["allowed_hosts"])
        canonical = target._canonical_url(url)[0]
        path = unquote(urlsplit(canonical).path).lower()
    except (TargetPolicyError, ValueError, UnicodeError) as error:
        reason = "target_host_not_allowed" if str(error) == "target_host_not_allowed" else "unsupported_media"
        return Entry(label, "", reason=reason)
    transport = "hls" if path.endswith(".m3u8") else "mp4" if path.endswith(".mp4") else None
    return Entry(label, canonical if transport else "", transport,
                 "" if transport else "unsupported_media")


def detail(source, selected_id):
    selected_id = external_id(selected_id)
    action = source.get("provider_options", {}).get("detail_action", "detail")
    payload = fetch(source, {"ac": action, "ids": selected_id})
    if not payload["list"]:
        raise ProviderError("provider_not_found")
    if len(payload["list"]) != 1 or external_id(payload["list"][0].get("vod_id")) != selected_id:
        raise ProviderError("invalid_provider_response")
    return parse_detail(source, payload["list"][0])


def parse_detail(source, row):
    identity = external_id(row.get("vod_id"))
    title = plain(row.get("vod_name"), required=True)
    names, groups = row.get("vod_play_from", ""), row.get("vod_play_url", "")
    if not isinstance(names, str) or not isinstance(groups, str) or len(names) > 8192 or len(groups) > MAX_BODY:
        raise ProviderError("invalid_provider_response")
    if not names and not groups:
        return Detail(identity, title, _kind(row, lines=()) if _kind(row) != "unknown" else "movie",
                      plain(row.get("vod_content"), 4000), plain(row.get("vod_year"), 16), (),
                      poster_url=poster_url(row), metadata=metadata(row))
    split_names, split_groups = names.split("$$$"), groups.split("$$$")
    if len(split_names) != len(split_groups) or len(split_names) > MAX_LINES:
        raise ProviderError("invalid_provider_response")
    lines, known_lines, total = [], set(), 0
    target = TargetPolicy(source["id"], source["allowed_hosts"])
    for name, group in zip(split_names, split_groups):
        name = plain(name, 128, required=True)
        if name in known_lines:
            raise ProviderError("provider_ambiguous_identity")
        known_lines.add(name)
        raw_entries = group.split("#") if group else []
        if len(raw_entries) > MAX_LINE_ENTRIES:
            raise ProviderError("provider_response_limit")
        entries, labels = [], set()
        for raw in raw_entries:
            if not raw:
                raise ProviderError("invalid_provider_response")
            if "$" in raw:
                label, url = raw.split("$", 1)
                label = plain(label, 128, required=True)
            else:
                # A label-less singleton has an unambiguous stable identity;
                # multiple label-less URLs cannot acquire ordering identities.
                if len(raw_entries) != 1:
                    raise ProviderError("provider_ambiguous_identity")
                label, url = "正片", raw
            if label in labels:
                raise ProviderError("provider_ambiguous_identity")
            labels.add(label)
            entries.append(_entry(source, label, url, target))
            total += 1
            if total > MAX_ENTRIES:
                raise ProviderError("provider_response_limit")
        lines.append(Line(name, tuple(entries)))
    kind = _kind(row, lines=lines)
    if kind == "movie":
        # A movie identity selects a line, not one of several parts inside
        # that line. Never expose indistinguishable playable choices that
        # would all point at the same line identity.
        lines = [Line(line.name, tuple(Entry(entry.label, "", reason="unsupported_media")
                                      for entry in line.entries)) if len(line.entries) > 1 else line
                 for line in lines]
    return Detail(identity, title, kind, plain(row.get("vod_content"), 4000),
                  plain(row.get("vod_year"), 16), tuple(lines), poster_url=poster_url(row), metadata=metadata(row))
