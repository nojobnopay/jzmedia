"""Read the JSON catalogue slots on a recognized operator's published HTTPS help page."""

import time
from html.parser import HTMLParser
from urllib.parse import urlsplit
from xml.etree.ElementTree import Element, SubElement

from ..network.client import NetworkRequestError, create_client
from ..network.guard import NetworkOperationError, check_operation, child_operation, current_operation
from ..network.policy import NetworkSnapshot
from ..network.targets import TargetPolicy, TargetPolicyError
from .models import validate_api_base_url
from .source_templates import list_source_templates


_MAX_BYTES = 256 * 1024
_SECTIONS = {"liangzi": "接口总览 / JSON 采集接口", "baofeng": "接口总览 / JSON 采集接口",
             "feifan": "app json接口 / 视频列表与详情"}
_VOID = frozenset("area base br col embed hr img input link meta param source track wbr".split())


def _endpoint_key(url):
    parts = urlsplit(url)
    port = parts.port
    if port == (443 if parts.scheme == "https" else 80):
        port = None
    return parts.scheme, parts.hostname, port, parts.path.removesuffix("/"), parts.query, parts.fragment


def recognize_template(source: dict) -> dict | None:
    """Match an exact reviewed catalogue, irrespective of its user-defined display name."""
    if source.get("provider") != "maccms":
        return None
    try:
        key = _endpoint_key(validate_api_base_url(source.get("api_base_url")))
    except ValueError:
        return None
    for item in list_source_templates()["items"]:
        if item["id"] in _SECTIONS and any(_endpoint_key(url) == key for url in item["known_api_urls"]):
            return item
    return None


class _DirectoryPage(HTMLParser):
    """A bounded inert tree for the site's explicitly labelled HTML controls."""

    def __init__(self):
        super().__init__()
        self.root = Element("document")
        self.stack = [self.root]
        self.nodes = 0

    def handle_starttag(self, tag, attrs):
        self.nodes += 1
        if self.nodes > 8000 or len(self.stack) > 80:
            raise ValueError("page_layout")
        node = SubElement(self.stack[-1], tag, {k: v or "" for k, v in attrs if k in {"class", "id", "data-text"}})
        if tag not in _VOID:
            self.stack.append(node)
        elif tag == "br":
            node.text = "\n"

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        node = self.stack[-1]
        if len(node):
            node[-1].tail = (node[-1].tail or "") + data
        else:
            node.text = (node.text or "") + data


def _one(items):
    if len(items) != 1:
        raise ValueError("page_layout")
    return items[0]


def _clean_base(value, operator):
    url = validate_api_base_url(value)
    parts = urlsplit(url)
    if (parts.path.removesuffix("/") != "/api.php/provide/vod"
            or parts.port not in (None, 443 if parts.scheme == "https" else 80)
            or "." not in parts.hostname or parts.hostname.endswith((".internal", ".lan", ".home.arpa"))
            or operator != "feifan" and parts.scheme != "https"):
        raise ValueError("page_layout")
    return url


def _group_candidates(tab, operator):
    def children(node, tag, style):
        return [child for child in node.findall(tag) if style in child.get("class", "").split()]

    groups = []
    for group in tab.iter("div"):
        titles = children(group, "div", "api-group-title")
        if ("api-group" in group.get("class", "").split() and len(titles) == 1
                and "".join((titles[0].text or "").split()).upper() == "JSON采集接口"):
            groups.append(group)
    candidates = {}
    for row in children(_one(groups), "div", "api-row"):
        label = _one(children(row, "span", "api-label")).text
        role = {"主接口": "primary", "备用接口": "backup"}.get((label or "").strip())
        box = _one(children(row, "span", "api-box"))
        value = (box.text or "").strip()
        copied = _one(children(row, "button", "copy-btn")).get("data-text")
        if not role or role in candidates or len(box) or copied != value:
            raise ValueError("page_layout")
        candidates[role] = _clean_base(value, operator)
    if "primary" not in candidates:
        raise ValueError("page_layout")
    return [{"url": candidates[role], "role": role} for role in ("primary", "backup") if role in candidates]


def _feifan_candidates(tab):
    title = _one(list(tab.iter("h1")))
    if "".join((title.text or "").lower().split()) != "appjson接口":
        raise ValueError("page_layout")
    examples = _one([p for p in tab.iter("p") if "jiaocheng" in p.get("class", "").split()])
    if any(child.tag != "br" for child in examples):
        raise ValueError("page_layout")
    lines = [line.strip() for line in "".join(examples.itertext()).splitlines() if line.strip()]
    bases = []
    for label, query in (("1,视频列表地址", "?ac=list"), ("2,视频详情地址", "?ac=detail")):
        line = _one([line for line in lines if line.startswith(label)])
        address = line.removeprefix(label).strip()
        if not address.endswith(query):
            raise ValueError("page_layout")
        bases.append(_clean_base(address.removesuffix(query), "feifan"))
    if bases[0] != bases[1]:
        raise ValueError("page_layout")
    return [{"url": bases[0], "role": "primary"}]


def parse_candidates(operator: str, body: bytes) -> list[dict]:
    if len(body) > _MAX_BYTES:
        raise ValueError("page_size")
    page = _DirectoryPage()
    page.feed(body.decode("utf-8-sig"))
    page.close()
    for parent in page.root.iter():
        for node in list(parent):
            if node.tag in {"script", "style", "template", "noscript", "textarea", "iframe", "svg"}:
                parent.remove(node)
    scope = "APPjson" if operator == "feifan" else "tab-home"
    tab = _one([node for node in page.root.iter("div") if node.get("id") == scope])
    return _feifan_candidates(tab) if operator == "feifan" else _group_candidates(tab, operator)


class _HelpPageTarget(TargetPolicy):
    """Limit every hop to the reviewed page, including its HTTPS origin and path."""

    def __init__(self, snapshot, page):
        self.page = page
        super().__init__(snapshot.source_id, [urlsplit(page).hostname],
                         dns_timeout_seconds=snapshot.connect_timeout_seconds)

    def _canonical_url(self, value):
        target = super()._canonical_url(value)
        if target[0] != self.page:
            raise TargetPolicyError("unapproved_help_page")
        return target


def _read_page(snapshot, page, deadline):
    with (child_operation(current_operation(), min(deadline, time.monotonic() + 6)) as operation,
          create_client(snapshot, targets=_HelpPageTarget(snapshot, page)) as client):
        with client.stream("GET", page, headers={"Accept": "text/html"}) as response:
            if (response.status_code != 200 or response.headers.get("content-type", "").split(";")[0].lower().strip() != "text/html"
                    or response.headers.get("content-encoding", "identity").lower() != "identity"):
                raise ValueError("page_unavailable")
            length = response.headers.get("content-length")
            if length is not None and (not length.isdigit() or int(length) > _MAX_BYTES):
                raise ValueError("page_size")
            body = bytearray()
            for chunk in response.iter_raw(chunk_size=16384):
                operation.check()
                if len(body) + len(chunk) > _MAX_BYTES:
                    raise ValueError("page_size")
                body.extend(chunk)
            operation.check()
            return bytes(body)


def lookup_candidates(template: dict, api_snapshot: NetworkSnapshot, *, deadline: float) -> dict:
    """Return labelled candidates for independent catalogue validation over the same API route."""
    operator = template["id"]
    trusted = next(item for item in list_source_templates()["items"] if item["id"] == operator)
    page = trusted["homepage"]
    evidence = {"page_url": page, "section": _SECTIONS[operator], "checked_at": int(time.time())}
    try:
        body = _read_page(api_snapshot, page, deadline)
    except (ValueError, NetworkRequestError, NetworkOperationError):
        check_operation()
        reason = "page_unavailable"
    else:
        check_operation()
        try:
            candidates = parse_candidates(operator, body)
            return {**evidence, "origin": "operator_page", "candidates": candidates, "reason": "published"}
        except ValueError:
            reason = "page_layout_changed"
    reviewed = dict.fromkeys([trusted["api_base_url"], *trusted["known_api_urls"]])
    candidates = [{"url": url, "role": role} for url, role in zip(reviewed, ("primary", "backup"))]
    return {**evidence, "origin": "release_template", "reason": reason, "candidates": candidates}
