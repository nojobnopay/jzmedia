"""Bounded MacCMS Provide JSON reader; no player scripts or parser services."""

from contextlib import contextmanager
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
_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


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
        with _operation() as operation, create_online_client(source["id"], "api") as client:
            try:
                response = client.request("GET", source["api_base_url"], params=params,
                                          headers={"Accept": "application/json"})
            except NetworkRequestError:
                # Socket aborts may surface as ordinary HTTPX read errors;
                # retain the server operation's actual cancellation/deadline.
                operation.check()
                raise
            operation.check()
            if response.status_code == 429:
                raise ProviderError("provider_rate_limited")
            if response.status_code in {401, 403}:
                raise ProviderError("provider_auth_failed")
            if response.status_code == 404:
                raise ProviderError("provider_not_found")
            if response.status_code != 200:
                raise ProviderError("provider_upstream_error")
            return decode(response.content)
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


def search(source, query, page):
    action = source.get("provider_options", {}).get("search_action", "list")
    payload = fetch(source, {"ac": action, "wd": query, "pg": str(page)})
    result, seen = [], set()
    for row in payload["list"]:
        identity = external_id(row.get("vod_id"))
        if identity in seen:
            raise ProviderError("invalid_provider_response")
        seen.add(identity)
        result.append({"external_id": identity, "title": plain(row.get("vod_name"), required=True),
                       "kind": _kind(row), "year": plain(row.get("vod_year"), 16),
                       "description": plain(row.get("vod_content"), 4000)})
    selected_page = _number(payload.get("page"), maximum=1000, default=page)
    count = _number(payload.get("pagecount"), maximum=1000000, default=1 if result else 0)
    total = _number(payload.get("total"), maximum=1000000000, default=len(result))
    if selected_page != page or result and count < page:
        raise ProviderError("invalid_provider_response")
    return {"items": result, "page": page, "page_count": min(count, 1000), "total": total}


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
                      plain(row.get("vod_content"), 4000), plain(row.get("vod_year"), 16), ())
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
                  plain(row.get("vod_year"), 16), tuple(lines))
