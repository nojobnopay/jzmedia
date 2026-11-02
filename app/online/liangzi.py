"""Bounded, server-only static extraction for the official Liangzi cloud line.

Only a literal ``var main`` is read. Source scripts are parsed, never run, and
the extracted address must stay on the final page's authorized origin.
"""

from contextlib import contextmanager
from dataclasses import dataclass, field
from html.parser import HTMLParser
import re
import time
from urllib.parse import unquote, urljoin, urlsplit

from tree_sitter import Language, Parser
import tree_sitter_javascript

from ..network.client import create_online_client, NetworkRequestError
from ..network.guard import NetworkOperation, NetworkOperationError, current_operation, operation_context
from ..network.policy import NetworkPolicyError
from ..network.targets import TargetPolicy, TargetPolicyError
from .providers import ProviderError


MAX_PAGE_BYTES = 64 * 1024
MAX_PAGE_SECONDS = 10.0
_API_HOSTS = frozenset({"cj.lziapi.com", "cj.lzcaiji.com"})
_LANGUAGE = Language(tree_sitter_javascript.language())
_HEX = re.compile(r"[0-9a-fA-F]+\Z")
_UNSAFE = re.compile(r"[\s\x00-\x1f\x7f\\]")


@dataclass(frozen=True)
class PageResolution:
    url: str = field(repr=False)
    media_policy_fingerprint: str = field(repr=False)


def supports_source(source):
    """Recognize only the published official JSON API origins and path."""
    if not isinstance(source, dict) or source.get("provider") != "maccms":
        return False
    value = source.get("api_base_url")
    if not isinstance(value, str) or not value or _UNSAFE.search(value):
        return False
    try:
        parts = urlsplit(value)
        return bool(parts.scheme == "https" and parts.hostname in _API_HOSTS
                    and parts.port in {None, 443} and parts.username is None
                    and parts.password is None and not parts.query and not parts.fragment
                    and "%" not in parts.netloc
                    and not parts.netloc.endswith(":")
                    and parts.path in {"/api.php/provide/vod", "/api.php/provide/vod/"})
    except (ValueError, UnicodeError):
        return False


def supports_page(source, line_name, url):
    """Classify a source-owned page without DNS, HTTP or implicit host trust."""
    if (not supports_source(source) or line_name != "liangzi" or not isinstance(url, str)
            or not url or len(url) > 8192 or _UNSAFE.search(url)):
        return False
    try:
        parts = urlsplit(url)
        return bool(parts.scheme == "https" and parts.hostname
                    and parts.port in {None, 443} and parts.username is None
                    and parts.password is None and not parts.fragment and "%" not in parts.netloc
                    and not parts.netloc.endswith(":")
                    and not unquote(parts.path).lower().endswith((".mp4", ".m3u8")))
    except (ValueError, UnicodeError):
        return False


class _Scripts(HTMLParser):
    _INERT = frozenset({"template", "noscript", "textarea", "title", "xmp", "iframe", "noembed",
                        "noframes", "plaintext", "svg", "math"})

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.scripts = []
        self.current = None
        self.accept = False
        self.broken = False
        self.inert = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in self._INERT:
            self.inert.append(tag)
        if tag == "plaintext":
            self.broken = True
        if tag == "base" and any(key.lower() == "href" for key, _ in attrs):
            self.broken = True
        if tag != "script" or self.inert:
            return
        if self.current is not None:
            self.broken = True
        names = [key.lower() for key, _ in attrs]
        if len(names) != len(set(names)):
            self.broken = True
        values = {key.lower(): value for key, value in attrs}
        script_type = (values.get("type") or "").strip().lower()
        self.accept = "src" not in values and script_type in {"", "text/javascript", "application/javascript"}
        self.current = []

    def handle_startendtag(self, tag, attrs):
        if tag.lower() in self._INERT or tag.lower() == "script":
            # HTML non-void self-closing tags do not reliably end the element;
            # do not let HTMLParser expose browser-inert text as active code.
            self.broken = True
            return
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in self._INERT and tag in self.inert:
            if self.inert[-1] != tag:
                self.broken = True
            else:
                self.inert.pop()
        if tag == "script" and not self.inert and self.current is not None:
            if self.accept:
                self.scripts.append("".join(self.current))
            self.current = None
            self.accept = False

    def handle_data(self, value):
        if self.current is not None:
            self.current.append(value)


def _nodes(root):
    operation = current_operation()
    pending = [root]
    visited = 0
    while pending:
        if operation is not None and visited % 128 == 0:
            operation.check()
        node = pending.pop()
        visited += 1
        yield node
        pending.extend(reversed(node.named_children))


def _string(value):
    """Decode a JS string literal's data without evaluating any expression."""
    if len(value) < 2 or value[0] not in {"'", '"'} or value[-1] != value[0]:
        raise ValueError
    value, index, result = value[1:-1], 0, []
    basic = {"b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v",
             "'": "'", '"': '"', "\\": "\\", "/": "/"}
    while index < len(value):
        char = value[index]
        index += 1
        if ord(char) < 32 or char in {"\u2028", "\u2029"}:
            raise ValueError
        if char != "\\":
            result.append(char)
            continue
        if index >= len(value):
            raise ValueError
        escape = value[index]
        index += 1
        if escape in basic:
            result.append(basic[escape])
            continue
        if escape == "0" and (index == len(value) or not value[index].isdigit()):
            result.append("\0")
            continue
        if escape == "u" and value[index:index + 1] == "{":
            end = value.find("}", index + 1)
            digits = value[index + 1:end] if end != -1 else ""
            if not 1 <= len(digits) <= 6 or not _HEX.fullmatch(digits):
                raise ValueError
            code = int(digits, 16)
            if code > 0x10FFFF or 0xD800 <= code <= 0xDFFF:
                raise ValueError
            result.append(chr(code))
            index = end + 1
            continue
        count = 2 if escape == "x" else 4 if escape == "u" else 0
        if not count or index + count > len(value):
            raise ValueError
        digits = value[index:index + count]
        if not _HEX.fullmatch(digits):
            raise ValueError
        code = int(digits, 16)
        index += count
        if 0xD800 <= code <= 0xDBFF:
            if value[index:index + 2] != "\\u" or index + 6 > len(value):
                raise ValueError
            low = value[index + 2:index + 6]
            if not _HEX.fullmatch(low) or not 0xDC00 <= int(low, 16) <= 0xDFFF:
                raise ValueError
            code = 0x10000 + (code - 0xD800) * 0x400 + int(low, 16) - 0xDC00
            index += 6
        elif 0xDC00 <= code <= 0xDFFF:
            raise ValueError
        result.append(chr(code))
    return "".join(result)


def _writes_main(node):
    """Conservatively reject writes/destructuring of the selected global."""
    for selected in _nodes(node):
        if selected.type in {"identifier", "property_identifier", "shorthand_property_identifier_pattern"}:
            # JavaScript identifiers may encode letters with Unicode escapes.
            # A write/binding containing any such spelling is outside the
            # supported static shape; never compare only its raw name bytes.
            if selected.text == b"main" or b"\\" in selected.text:
                return True
        if selected.type == "subscript_expression":
            index = selected.child_by_field_name("index")
            # A computed key may evaluate to "main". Only literal keys can be
            # proved different without evaluating source code or expressions.
            if index is None or index.type not in {"string", "number", "true", "false", "null"}:
                return True
        if selected.type == "string":
            try:
                if _string(selected.text.decode("utf-8")) == "main":
                    return True
            except (ValueError, UnicodeError):
                return True
    return False


def extract_media_url(body, page_url, targets):
    """Extract and authorize syntax only; networking remains in resolve()."""
    if not isinstance(body, bytes) or not 0 < len(body) <= MAX_PAGE_BYTES or not isinstance(targets, TargetPolicy):
        raise ProviderError("provider_page_invalid")
    try:
        document = _Scripts()
        document.feed(body.decode("utf-8-sig"))
        document.close()
        if document.broken or document.current is not None or document.inert:
            raise ProviderError("provider_page_invalid")
        candidates = []
        for script in document.scripts:
            tree = Parser(_LANGUAGE).parse(script.encode("utf-8"))
            if tree.root_node.has_error:
                raise ProviderError("provider_page_unsupported")
            for node in _nodes(tree.root_node):
                if node.type == "variable_declarator":
                    name = node.child_by_field_name("name")
                    if name is not None and _writes_main(name):
                        parent = node.parent
                        value = node.child_by_field_name("value")
                        if (name.type != "identifier" or name.text != b"main"
                                or parent is None or parent.type != "variable_declaration"
                                or parent.parent is None or parent.parent.type != "program"
                                or value is None or value.type != "string"):
                            raise ProviderError("provider_page_unsupported")
                        candidates.append(_string(value.text.decode("utf-8")))
                elif node.type in {"function_declaration", "generator_function_declaration", "class_declaration"}:
                    name = node.child_by_field_name("name")
                    if name is not None and _writes_main(name):
                        raise ProviderError("provider_page_unsupported")
                elif node.type in {"assignment_expression", "augmented_assignment_expression", "update_expression"}:
                    left = node.child_by_field_name("left") or node.child_by_field_name("argument")
                    if left is not None and _writes_main(left):
                        raise ProviderError("provider_page_unsupported")
                elif node.type in {"for_in_statement"}:
                    left = node.child_by_field_name("left")
                    if left is not None and _writes_main(left):
                        raise ProviderError("provider_page_unsupported")
        if not candidates:
            raise ProviderError("provider_page_unsupported")
        if len(candidates) != 1:
            raise ProviderError("provider_page_invalid")
        if not candidates[0] or len(candidates[0]) > 8192 or _UNSAFE.search(candidates[0]) or "#" in candidates[0]:
            raise ProviderError("provider_page_invalid")
        page = targets._canonical_url(page_url)
        result = targets._canonical_url(urljoin(page[0], candidates[0]))
        if page[4] != result[4] or not unquote(urlsplit(result[0]).path).lower().endswith(".m3u8"):
            raise ProviderError("provider_page_invalid")
        return result[0]
    except ProviderError:
        raise
    except (ValueError, TypeError, UnicodeError, TargetPolicyError):
        raise ProviderError("provider_page_invalid") from None


class _ChildCancellation:
    def __init__(self, operation):
        self.operation = operation

    def shutdown(self, _how):
        self.operation.abort()


@contextmanager
def _page_operation(parent):
    if parent is not None:
        parent.check()
    deadline = time.monotonic() + MAX_PAGE_SECONDS
    child = NetworkOperation(min(parent.deadline, deadline) if parent is not None else deadline)
    cancellation = _ChildCancellation(child)
    try:
        if parent is not None:
            parent.bind_socket(cancellation)
        with operation_context(child):
            try:
                yield child
            except NetworkRequestError:
                # Socket shutdown may surface as a generic request failure.
                # Check before close() so a healthy child is not mistaken for
                # cancellation, and the independent ten-second timeout wins.
                if parent is not None:
                    parent.check()
                child.check()
                raise
    finally:
        if parent is not None:
            parent.release_socket(cancellation)
        child.close()


def _html_response(response):
    if response.status_code == 429:
        raise ProviderError("provider_rate_limited")
    if response.status_code in {401, 403}:
        raise ProviderError("provider_auth_failed")
    if response.status_code != 200:
        raise ProviderError("provider_upstream_error")
    content_types = response.headers.get_list("content-type")
    if len(content_types) != 1:
        raise ProviderError("provider_page_invalid")
    parts = content_types[0].split(";")
    if parts[0].strip().lower() != "text/html":
        raise ProviderError("provider_page_invalid")
    charsets = []
    for value in parts[1:]:
        name, separator, selected = value.partition("=")
        if name.strip().lower() == "charset":
            if not separator:
                raise ProviderError("provider_page_unsupported")
            selected = selected.strip()
            if selected.startswith('"') or selected.endswith('"'):
                if len(selected) < 2 or selected[0] != '"' or selected[-1] != '"' or '"' in selected[1:-1]:
                    raise ProviderError("provider_page_unsupported")
                selected = selected[1:-1]
            charsets.append(selected.lower())
    if len(charsets) > 1 or charsets and charsets[0] not in {"utf-8", "utf8", "us-ascii"}:
        raise ProviderError("provider_page_unsupported")
    encodings = response.headers.get_list("content-encoding")
    if len(encodings) > 1 or encodings and encodings[0].strip().lower() != "identity":
        raise ProviderError("provider_page_invalid")
    lengths = response.headers.get_list("content-length")
    if len(lengths) > 1 or lengths and (not lengths[0].isascii() or not lengths[0].isdigit()
                                       or len(lengths[0]) > 8 or not 0 < int(lengths[0]) <= MAX_PAGE_BYTES):
        raise ProviderError("provider_page_invalid")
    return charsets[0] if charsets else "utf-8"


def resolve(source, page_url, *, operation=None):
    """Fetch one selected page using the source's bounded media network path."""
    if not supports_page(source, "liangzi", page_url):
        raise ProviderError("provider_page_unsupported")
    parent = operation or current_operation()
    child = None
    try:
        with _page_operation(parent) as child:
            with create_online_client(source["id"], "media") as client:
                child.check()
                with client.stream("GET", page_url, headers={"Accept": "text/html"}) as response:
                    child.check()
                    charset = _html_response(response)
                    body, size = [], 0
                    for chunk in response.iter_raw(chunk_size=4096):
                        child.check()
                        size += len(chunk)
                        if size > MAX_PAGE_BYTES:
                            raise ProviderError("provider_page_invalid")
                        body.append(chunk)
                    child.check()
                    length = response.headers.get("content-length")
                    if length is not None and int(length) != size:
                        raise ProviderError("provider_page_invalid")
                    content = b"".join(body)
                    if charset == "us-ascii" and not content.isascii():
                        raise ProviderError("provider_page_invalid")
                    result = extract_media_url(content, str(response.url), client.targets)
                    child.check()
                    # Reuse the already-pinned final page origin, retaining the
                    # ordinary public-address/peer policy before registration.
                    client.targets.resolve(result)
                    fingerprint = client.snapshot.fingerprint
                child.check()
            if parent is not None:
                parent.check()
            return PageResolution(result, fingerprint)
    except NetworkOperationError as error:
        if parent is not None:
            try:
                parent.check()
            except NetworkOperationError as stopped:
                error = stopped
        raise ProviderError("provider_timeout" if str(error) == "operation_timeout" else "provider_cancelled") from None
    except (NetworkRequestError, NetworkPolicyError, TargetPolicyError) as error:
        if parent is not None:
            try:
                parent.check()
            except NetworkOperationError as stopped:
                raise ProviderError("provider_timeout" if str(stopped) == "operation_timeout"
                                    else "provider_cancelled") from None
        category = getattr(error, "category", str(error))
        if category in {"network_timeout", "operation_timeout", "target_dns_timeout"}:
            raise ProviderError("provider_timeout") from None
        if category == "operation_cancelled":
            raise ProviderError("provider_cancelled") from None
        raise ProviderError("provider_network_error") from None
