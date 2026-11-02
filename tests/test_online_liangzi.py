"""Static cloud-page extraction stays bounded and never runs source code."""

from contextlib import contextmanager
import socket
import threading
import time
from types import SimpleNamespace

import httpx
import pytest

from app.network.client import NetworkRequestError
from app.network.guard import NetworkOperation, NetworkOperationError, current_operation, operation_context
from app.network.targets import TargetPolicy
from app.online import liangzi
from app.online.providers import ProviderError


SOURCE = {"id": "src_" + "1" * 32, "provider": "maccms",
          "api_base_url": "https://cj.lziapi.com/api.php/provide/vod/",
          "allowed_hosts": ["cdn.example", "other.example"]}
PAGE = "https://cdn.example/share/episode?source=example"
FINGERPRINT = "a" * 64


def html(script):
    return ("<!doctype html><html><script>" + script + "</script></html>").encode()


def target():
    return TargetPolicy(SOURCE["id"], SOURCE["allowed_hosts"], resolver=lambda *_: ["8.8.8.8"])


def category(function, expected):
    with pytest.raises(ProviderError) as caught:
        function()
    assert caught.value.category == expected
    assert "example" not in str(caught.value)


@pytest.mark.parametrize("host", ["cj.lziapi.com", "cj.lzcaiji.com"])
@pytest.mark.parametrize("slash", ["", "/"])
def test_official_source_recognition_is_local_and_exact(monkeypatch, host, slash):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_: pytest.fail("recognition must not use DNS"))
    source = {**SOURCE, "api_base_url": f"https://{host}/api.php/provide/vod{slash}"}
    assert liangzi.supports_source(source)
    assert liangzi.supports_page(source, "liangzi", PAGE)
    assert not liangzi.supports_page(source, "lzm3u8", PAGE)
    assert not liangzi.supports_page(source, "liangzi", "https://cdn.example/a.m3u8")
    assert not liangzi.supports_page(source, "liangzi", "https://cdn.example/a.MP4?x=1")


@pytest.mark.parametrize("api", [
    "http://cj.lziapi.com/api.php/provide/vod/", "https://cj.lziapi.com:444/api.php/provide/vod/",
    "https://cj.lziapi.com.evil.example/api.php/provide/vod/", "https://cj.lziapi.com/other/",
    "https://cj.lziapi.com/api.php/provide/vod/?token=private", "https://cj.lziapi.com/api.php/provide/vod/#x",
    "https://user:private@cj.lziapi.com/api.php/provide/vod/", "https://cj.lziapi.com:/api.php/provide/vod/",
    "https://cj.lziapi.com/api.php/provide/vod//", "https://cj.lziapi.com\\/api.php/provide/vod/",
])
def test_unrecognized_or_ambiguous_sources_are_not_enabled(api):
    source = {**SOURCE, "api_base_url": api}
    assert not liangzi.supports_source(source)
    assert not liangzi.supports_page(source, "liangzi", PAGE)
    assert not liangzi.supports_source({**SOURCE, "provider": "open_catalog"})


@pytest.mark.parametrize("page", ["http://cdn.example/share/1", "https://cdn.example:444/share/1",
                                      "https://cdn.example:/share/1", PAGE + "#x",
                                      "https://user:private@cdn.example/share/1", " https://cdn.example/share/1"])
def test_page_recognition_rejects_unsupported_shapes(page):
    assert not liangzi.supports_page(SOURCE, "liangzi", page)


@pytest.mark.parametrize("literal,expected", [
    ('"/media/index.m3u8?signature=synthetic-value&part=1"',
     "https://cdn.example/media/index.m3u8?signature=synthetic-value&part=1"),
    ("'../media/index.m3u8?part=1'", "https://cdn.example/media/index.m3u8?part=1"),
    ('"https://cdn.example/media/index.m3u8?part=1"', "https://cdn.example/media/index.m3u8?part=1"),
    (r'"\/media\/index.m3u8?part=1\u0026name=\x61"', "https://cdn.example/media/index.m3u8?part=1&name=a"),
    (r"'/media/index.m3u8?name=it\'s'", "https://cdn.example/media/index.m3u8?name=it's"),
    (r'"/media/index.m3u8?emoji=\uD83D\uDE00"', "https://cdn.example/media/index.m3u8?emoji=%F0%9F%98%80"),
    (r'"/media/index.m3u8?emoji=\u{1F600}"', "https://cdn.example/media/index.m3u8?emoji=%F0%9F%98%80"),
])
def test_literal_addresses_are_decoded_relative_to_the_final_page(literal, expected):
    assert liangzi.extract_media_url(html(f"var main={literal}; new DPlayer({{video:{{url:main}}}});"), PAGE, target()) == expected


def test_comments_strings_and_external_scripts_cannot_supply_a_candidate():
    document = (b'<script src="/ignored.js">var main="/ignored.m3u8";</script>'
                b'<script type="application/json">{"main":"/ignored.m3u8"}</script>'
                b'<script>/* var main="/ignored.m3u8"; */'
                b'var text=\'var main="/ignored.m3u8";\'; // var main="/ignored.m3u8";\n'
                b'var main="/real/index.m3u8?signature=synthetic-value";</script>')
    assert liangzi.extract_media_url(document, PAGE, target()) == "https://cdn.example/real/index.m3u8?signature=synthetic-value"


@pytest.mark.parametrize("tag", ["template", "noscript", "textarea", "title", "xmp", "iframe", "noembed",
                                     "noframes", "svg", "math"])
def test_browser_inert_script_lookalikes_are_not_candidates(tag):
    fake = f"<{tag}><script>var main='/fake.m3u8';</script></{tag}>".encode()
    category(lambda: liangzi.extract_media_url(fake, PAGE, target()), "provider_page_unsupported")
    assert liangzi.extract_media_url(fake + html("var main='/real.m3u8';"), PAGE, target()) == "https://cdn.example/real.m3u8"


@pytest.mark.parametrize("tag", ["template", "noscript", "svg", "math"])
def test_nested_same_name_inert_elements_do_not_expose_the_outer_content(tag):
    fake = (f"<{tag}><{tag}></{tag}><script>var main='/fake.m3u8';</script></{tag}>").encode()
    category(lambda: liangzi.extract_media_url(fake, PAGE, target()), "provider_page_unsupported")
    assert liangzi.extract_media_url(fake + html("var main='/real.m3u8';"), PAGE, target()) == "https://cdn.example/real.m3u8"


def test_unclosed_inert_context_makes_the_document_ambiguous():
    body = html("var main='/real.m3u8';") + b"<template><script>var main='/fake.m3u8';</script>"
    category(lambda: liangzi.extract_media_url(body, PAGE, target()), "provider_page_invalid")


@pytest.mark.parametrize("script", [
    "var main=decodeURIComponent(location.search);", "var main='/a'+'.m3u8';", "var main=" + chr(96) + "/a.m3u8" + chr(96) + ";",
    "var main;", "let main='/a.m3u8';", "const main='/a.m3u8';",
    "function f(){ var main='/a.m3u8'; }", "var {main}=source;",
    "var main='/a.m3u8'; main='/b.m3u8';", "var main='/a.m3u8'; main+='/b';",
    "var main='/a.m3u8'; ++main;", "var main='/a.m3u8'; window.main='/b.m3u8';",
    "var main='/a.m3u8'; window['main']='/b.m3u8';", "var main='/a.m3u8'; function main(){}",
    "var main='/a.m3u8'; class main{}", "var main='/a.m3u8'; for(main of values){}",
    "var main='/a.m3u8'; for({main} of values){}", "var main = ;",
    r"var main='/a.m3u8'; \u006dain='/b.m3u8';",
    r"var main='/a.m3u8'; window.ma\u0069n='/b.m3u8';",
    r"var main='/a.m3u8'; function \u006dain(){}",
    r"var main='/a.m3u8'; var \u006dain='/b.m3u8';",
    r"var main='/a.m3u8'; \u{6d}ain='/b.m3u8';",
    "var main='/a.m3u8'; window['ma'+'in']='/b.m3u8';",
    "var main='/a.m3u8'; window[name]='/b.m3u8';",
    "var main='/a.m3u8'; globalThis[key]='/b.m3u8';",
    "var main='/a.m3u8'; window[name]++;",
])
def test_dynamic_or_reassigned_main_is_not_interpreted(script):
    category(lambda: liangzi.extract_media_url(html(script), PAGE, target()), "provider_page_unsupported")


def test_escape_lookalikes_in_comments_strings_and_read_positions_do_not_become_writes():
    body = html(r'''// \u006dain='/fake.m3u8';
var text="window.ma\\u0069n='/fake.m3u8';";
var main='/real.m3u8'; var player=new DPlayer({video:{url:\u006dain}});
window['other']='value'; window[1]='value';''')
    assert liangzi.extract_media_url(body, PAGE, target()) == "https://cdn.example/real.m3u8"


@pytest.mark.parametrize("body", [
    html("var main='/a.m3u8'; var main='/b.m3u8';"),
    b"<script>var main='/a.m3u8';</script><script>var main='/a.m3u8';</script>",
    b"<script>var main='/a.m3u8';", b"<script type='text/javascript' type='module'>var main='/a.m3u8';</script>",
    b"\xff<script>var main='/a.m3u8';</script>", b"", b"x" * (liangzi.MAX_PAGE_BYTES + 1),
    b"<plaintext><script>var main='/a.m3u8';</script>",
    b"<template/><script>var main='/a.m3u8';</script>",
    b"<base href='https://other.example/'><script>var main='/a.m3u8';</script>",
])
def test_ambiguous_or_malformed_documents_are_invalid(body):
    category(lambda: liangzi.extract_media_url(body, PAGE, target()), "provider_page_invalid")


@pytest.mark.parametrize("literal", [
    '"https://other.example/a.m3u8"', '"//other.example/a.m3u8"', '"http://cdn.example/a.m3u8"',
    '"https://user:private@cdn.example/a.m3u8"', '"https://127.0.0.1/a.m3u8"',
    '"/a.mp4"', '"/a.m3u8#fragment"', r'"\n/a.m3u8"', '" /a.m3u8"',
    r'"/a.m3u8?x=\u0000"', '"/a%0a.m3u8"', r'"/a.m3u8?x=\q"', r'"/a.m3u8?x=\uD800"',
])
def test_extracted_target_must_be_same_origin_hls_without_unsafe_data(literal):
    category(lambda: liangzi.extract_media_url(html("var main=" + literal + ";"), PAGE, target()), "provider_page_invalid")


@pytest.fixture
def reader(monkeypatch):
    state = SimpleNamespace(status=200, headers={"Content-Type": "text/html; charset=utf-8"},
                            chunks=[html("var main='/media/index.m3u8?signature=synthetic-value';")],
                            urls=[], readers=[], closed_responses=0, iterated=0, before_chunk=None,
                            before_factory=None, final_url=PAGE, failure=None, operation=None)

    class Body(httpx.SyncByteStream):
        def __iter__(self):
            state.iterated += 1
            if state.before_chunk:
                state.before_chunk()
            if state.failure:
                raise state.failure
            yield from state.chunks

        def close(self):
            state.closed_responses += 1

    class Reader:
        def __init__(self, source_id, purpose):
            assert source_id == SOURCE["id"] and purpose == "media"
            state.operation = current_operation()
            if state.before_factory:
                state.before_factory()
            self.snapshot = SimpleNamespace(fingerprint=FINGERPRINT)
            self.targets = target()
            self.closed = False
            state.readers.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *_):
            self.closed = True

        @contextmanager
        def stream(self, method, url, **options):
            self.targets.resolve(url)
            state.urls.append((method, url, options))
            response = httpx.Response(state.status, headers=state.headers, stream=Body(),
                                      request=httpx.Request(method, state.final_url))
            try:
                yield response
            finally:
                response.close()

    monkeypatch.setattr(liangzi, "create_online_client", Reader)
    return state


def test_selected_page_is_read_once_using_media_policy_and_closed(reader):
    started = time.monotonic()
    result = liangzi.resolve(SOURCE, PAGE)
    assert result.url == "https://cdn.example/media/index.m3u8?signature=synthetic-value"
    assert result.media_policy_fingerprint == FINGERPRINT
    assert "synthetic-value" not in repr(result) and FINGERPRINT not in repr(result)
    assert len(reader.urls) == reader.iterated == reader.closed_responses == 1
    assert reader.urls[0][2] == {"headers": {"Accept": "text/html"}}
    assert all(item.closed for item in reader.readers)
    assert reader.operation.deadline <= started + liangzi.MAX_PAGE_SECONDS + .01


@pytest.mark.parametrize("header,value,expected", [
    ("Content-Type", "application/json", "provider_page_invalid"),
    ("Content-Type", "text/html; charset=gbk", "provider_page_unsupported"),
    ("Content-Type", "text/html; charset = gbk", "provider_page_unsupported"),
    ("Content-Type", "text/html; charset", "provider_page_unsupported"),
    ("Content-Type", "text/html; charset=utf-8; charset=utf-8", "provider_page_unsupported"),
    ("Content-Type", 'text/html; charset="utf-8""', "provider_page_unsupported"),
    ("Content-Encoding", "gzip", "provider_page_invalid"),
    ("Content-Length", str(liangzi.MAX_PAGE_BYTES + 1), "provider_page_invalid"),
    ("Content-Length", "-1", "provider_page_invalid"),
    ("Content-Length", "9" * 5000, "provider_page_invalid"),
])
def test_response_headers_are_checked_before_body_read(reader, header, value, expected):
    reader.headers[header] = value
    category(lambda: liangzi.resolve(SOURCE, PAGE), expected)
    assert reader.iterated == 0 and reader.closed_responses == 1
    assert all(item.closed for item in reader.readers)


@pytest.mark.parametrize("status,expected", [(401, "provider_auth_failed"), (403, "provider_auth_failed"),
                                          (429, "provider_rate_limited"), (404, "provider_upstream_error"),
                                          (206, "provider_upstream_error")])
def test_page_status_errors_keep_fixed_categories(reader, status, expected):
    reader.status = status
    category(lambda: liangzi.resolve(SOURCE, PAGE), expected)
    assert reader.iterated == 0 and all(item.closed for item in reader.readers)


def test_unknown_length_stream_overflow_is_rejected_and_closed(reader):
    reader.chunks = [b" " * liangzi.MAX_PAGE_BYTES, b"extra"]
    category(lambda: liangzi.resolve(SOURCE, PAGE), "provider_page_invalid")
    assert reader.closed_responses == 1 and all(item.closed for item in reader.readers)


def test_us_ascii_header_does_not_accept_non_ascii_body(reader):
    reader.headers["Content-Type"] = "text/html; charset=us-ascii"
    reader.chunks = [html("var main='/media/index.m3u8?name=中文';")]
    category(lambda: liangzi.resolve(SOURCE, PAGE), "provider_page_invalid")


def test_parent_cancellation_stops_body_and_retains_cancelled_category(reader):
    parent = NetworkOperation(time.monotonic() + 30)
    reader.before_chunk = parent.abort
    category(lambda: liangzi.resolve(SOURCE, PAGE, operation=parent), "provider_cancelled")
    assert reader.operation.cancelled.is_set()
    assert reader.closed_responses == 1 and all(item.closed for item in reader.readers)
    assert not parent._sockets


def test_short_parent_deadline_is_not_extended(reader):
    parent = NetworkOperation(time.monotonic() + 2)
    liangzi.resolve(SOURCE, PAGE, operation=parent)
    assert reader.operation.deadline == parent.deadline
    assert not parent._sockets
    parent.check()


def test_cancelled_factory_cannot_start_an_http_request(reader):
    parent = NetworkOperation(time.monotonic() + 30)
    reader.before_factory = parent.abort
    category(lambda: liangzi.resolve(SOURCE, PAGE, operation=parent), "provider_cancelled")
    assert reader.urls == [] and all(item.closed for item in reader.readers)


def test_declared_length_mismatch_is_invalid(reader):
    reader.headers["Content-Length"] = "1"
    category(lambda: liangzi.resolve(SOURCE, PAGE), "provider_page_invalid")
    assert reader.closed_responses == 1 and all(item.closed for item in reader.readers)


def test_relative_media_uses_the_final_redirected_page(reader):
    reader.final_url = "https://cdn.example/redirected/share/episode"
    reader.chunks = [html("var main='../media/index.m3u8?signature=synthetic-value';")]
    assert liangzi.resolve(SOURCE, PAGE).url == "https://cdn.example/redirected/media/index.m3u8?signature=synthetic-value"


def test_exact_byte_limit_is_accepted_without_extra_page_reads(reader):
    body = html("var main='/media/index.m3u8';")
    reader.chunks = [body + b" " * (liangzi.MAX_PAGE_BYTES - len(body))]
    reader.headers["Content-Length"] = str(liangzi.MAX_PAGE_BYTES)
    assert liangzi.resolve(SOURCE, PAGE).url == "https://cdn.example/media/index.m3u8"
    assert len(reader.urls) == 1


def test_expired_child_preserves_timeout_after_an_apparent_network_error(reader):
    parent = NetworkOperation(time.monotonic() + 30)

    def expire():
        reader.operation.deadline = time.monotonic() - 1
    reader.before_chunk = expire
    reader.failure = NetworkRequestError("network_request_failed")
    category(lambda: liangzi.resolve(SOURCE, PAGE, operation=parent), "provider_timeout")
    assert all(item.closed for item in reader.readers)
    parent.check()


@pytest.mark.parametrize("failure,expected", [("network_timeout", "provider_timeout"),
                                            ("operation_cancelled", "provider_cancelled"),
                                            ("network_request_failed", "provider_network_error")])
def test_network_failure_closes_the_actual_reader(reader, failure, expected):
    reader.failure = NetworkRequestError(failure)
    category(lambda: liangzi.resolve(SOURCE, PAGE), expected)
    assert reader.closed_responses == 1 and all(item.closed for item in reader.readers)


def test_unapproved_page_host_is_rejected_before_http(reader):
    category(lambda: liangzi.resolve(SOURCE, "https://unapproved.example/share/1"), "provider_network_error")
    assert reader.urls == [] and all(item.closed for item in reader.readers)


def test_unrecognized_source_cannot_start_a_reader(reader):
    category(lambda: liangzi.resolve({**SOURCE, "api_base_url": "https://other.example/api.php/provide/vod/"}, PAGE),
             "provider_page_unsupported")
    assert reader.readers == []


def test_parent_abort_shuts_down_registered_child_io():
    parent = NetworkOperation(time.monotonic() + 30)
    stopped = threading.Event()

    class Socket:
        def shutdown(self, _how):
            stopped.set()

    with liangzi._page_operation(parent) as child:
        sock = Socket()
        child.bind_socket(sock)
        parent.abort()
        assert stopped.is_set()
    assert not parent._sockets


def test_static_tree_walk_obeys_the_active_operation_deadline():
    operation = NetworkOperation(time.monotonic() + 30)
    with operation_context(operation):
        operation.deadline = time.monotonic() - 1
        with pytest.raises(NetworkOperationError, match="operation_timeout"):
            liangzi.extract_media_url(html("var main='/media/index.m3u8';"), PAGE, target())
    operation.close()
