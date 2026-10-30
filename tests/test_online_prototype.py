"""The historical prototype uses generated media and actual loopback HTTP; no production app or public network."""
import asyncio
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import re
import threading
from urllib.parse import urlsplit

from fastapi.testclient import TestClient
import httpx
import pytest
from starlette.requests import ClientDisconnect

from scripts.online_prototype.core import NetworkPolicy, SourceError
from scripts.online_prototype.fixtures import FixtureOrigin
from scripts.online_fixture.media import generate_media
from scripts.online_prototype.server import create_app


@pytest.fixture(scope="module")
def media_root(tmp_path_factory):
    root = tmp_path_factory.mktemp("jzmedia-online-prototype-")
    generate_media(root)
    return root


@pytest.fixture
def origin(media_root):
    with FixtureOrigin(media_root) as source:
        yield source


@pytest.fixture
def client(origin):
    with TestClient(create_app(origin)) as browser:
        yield browser


def start(client, transport="mp4", episode="ep-1"):
    response = client.get(f"/api/prototype/play/prototype-series/{episode}", params={"transport": transport})
    assert response.status_code == 200, response.text
    return response.json()


@contextmanager
def loopback_proxy(origin):
    """Observable explicit HTTP proxy that can only forward to this test's origin."""
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            self.respond()

        def do_HEAD(self):
            self.respond()

        def respond(self):
            if urlsplit(self.path).netloc != urlsplit(origin.base_url).netloc:
                self.send_error(403)
                return
            calls.append({"path": urlsplit(self.path).path, "method": self.command})
            headers = {"Range": self.headers["Range"]} if "Range" in self.headers else {}
            with httpx.Client(trust_env=False, follow_redirects=False) as client:
                response = client.request(self.command, self.path, headers=headers)
            self.send_response(response.status_code)
            for key, value in response.headers.items():
                if key in {"content-type", "content-length", "content-range", "accept-ranges"}:
                    self.send_header(key, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(response.content)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_search_detail_and_episode_identity(client):
    result = client.get("/api/prototype/search", params={"q": "在线"}).json()
    assert [item["id"] for item in result["items"]] == ["prototype-series"]
    assert client.get("/api/prototype/search", params={"q": "does-not-exist"}).json()["items"] == []
    detail = client.get("/api/prototype/items/prototype-series").json()
    assert [item["id"] for item in detail["episodes"]] == ["ep-1", "ep-2"]
    assert detail["transports"] == ["mp4", "hls"]
    assert client.get("/api/prototype/items/no-item").status_code == 404
    assert client.get("/api/prototype/play/prototype-series/no-episode").status_code == 404
    assert client.get("/api/prototype/play/prototype-series/ep-1?transport=rtsp").status_code == 422


def test_mp4_range_head_and_unsatisfiable_requests(client, media_root):
    play = start(client)
    content = (media_root / "ep-1/movie.mp4").read_bytes()
    assert len(content) > 10000
    assert play["offset"] == 0 and play["duration"] == 24
    response = client.get(play["media_url"], headers={"Range": "bytes=0-31"})
    assert response.status_code == 206
    assert response.content == content[:32]
    assert response.headers["content-range"] == f"bytes 0-31/{len(content)}"
    suffix = client.get(play["media_url"], headers={"Range": "bytes=-17"})
    assert suffix.content == content[-17:]
    head = client.head(play["media_url"], headers={"Range": "bytes=10-29"})
    assert head.status_code == 206 and head.content == b""
    assert head.headers["content-length"] == "20"
    for value in ("bytes=999999999-", "bytes=1-0", "bytes=0-1,5-6", "bytes=-0"):
        response = client.get(play["media_url"], headers={"Range": value})
        assert response.status_code == 416
        assert response.headers["content-range"] == f"bytes */{len(content)}"


def test_hls_nested_playlist_and_init_segment_are_same_origin(client, origin):
    play = start(client, "hls")
    master = client.get(play["media_url"])
    assert master.status_code == 200
    assert origin.base_url not in master.text
    variant = next(line for line in master.text.splitlines() if line and not line.startswith("#"))
    assert variant.startswith("/api/prototype/media/")
    playlist = client.get(variant)
    assert playlist.status_code == 200
    assert "#EXT-X-ENDLIST" in playlist.text
    assert "../init.mp4" not in playlist.text
    init = re.search(r'#EXT-X-MAP:URI="([^"]+)"', playlist.text)[1]
    assert client.get(init, headers={"Range": "bytes=0-15"}).status_code == 206
    chunks = [line for line in playlist.text.splitlines() if line and not line.startswith("#")]
    assert len(chunks) == 6
    for segment in chunks:
        response = client.get(segment)
        assert response.status_code == 200 and len(response.content) > 100
    assert client.head(variant).headers["content-length"] == str(len(playlist.content))


def test_sessions_are_independent_and_close_invalidates_media(client):
    first = start(client)
    second = start(client, episode="ep-2")
    assert first["session_id"] != second["session_id"]
    assert client.delete(f'/api/prototype/sessions/{first["session_id"]}').status_code == 200
    assert client.get(first["media_url"]).status_code == 404
    assert client.get(second["media_url"], headers={"Range": "bytes=0-31"}).status_code == 206
    assert client.get(second["media_url"] + "-unknown").status_code == 404


def test_expired_session_and_session_limit(client):
    play = start(client)
    gateway = client.app.state.gateway
    gateway.sessions[play["session_id"]].created_at -= gateway.ttl + 1
    assert client.get(play["media_url"]).status_code == 404
    for _ in range(32):
        start(client)
    assert client.get("/api/prototype/play/prototype-series/ep-1").status_code == 502


def test_direct_ignores_environment_proxy(origin, monkeypatch):
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setenv("no_proxy", "")
    with TestClient(create_app(origin)) as client:
        play = start(client)
        assert client.get(play["media_url"], headers={"Range": "bytes=0-15"}).status_code == 206
        assert client.get("/api/prototype/health").json()["media_network"] == "direct"


@pytest.mark.parametrize("purpose", ["api", "media"])
def test_api_and_media_have_independent_explicit_routes(origin, purpose):
    with loopback_proxy(origin) as (url, calls):
        kwargs = {f"{purpose}_policy": NetworkPolicy("proxy", url)}
        with TestClient(create_app(origin, **kwargs)) as client:
            play = start(client, "hls")
            master = client.get(play["media_url"]).text
            variant = next(line for line in master.splitlines() if line and not line.startswith("#"))
            playlist = client.get(variant)
            assert playlist.status_code == 200
            init = re.search(r'#EXT-X-MAP:URI="([^"]+)"', playlist.text)[1]
            segment = next(line for line in playlist.text.splitlines() if line and not line.startswith("#"))
            assert client.get(init).status_code == 200
            assert client.get(segment).status_code == 200
        assert calls
        if purpose == "api":
            assert all(call["path"] == "/api.php/provide/vod" for call in calls)
        else:
            assert all(call["path"].startswith("/files/") for call in calls)
            assert any(call["path"].endswith("init.mp4") for call in calls)
            assert any(call["path"].endswith(".m4s") for call in calls)


def test_forced_proxy_failure_does_not_fall_back_to_direct(origin):
    with TestClient(create_app(origin, api_policy=NetworkPolicy("proxy", "http://127.0.0.1:1"))) as client:
        assert client.get("/api/prototype/search").status_code == 502
    assert origin.calls == []


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:1/files/ep-1/movie.mp4", "http://localhost/files/ep-1/movie.mp4",
    "http://192.168.1.1/admin", "https://example.com/movie.mp4", "file:///etc/passwd",
])
def test_unregistered_origins_are_rejected(client, url):
    with pytest.raises(SourceError):
        client.app.state.gateway.validate_media_url(url)


@pytest.mark.parametrize("suffix", ["/files/../secret", "/files/%2e%2e/secret",
                                   "/files/ep-1/movie.mp4?token=abc", "/files/unknown.mp4"])
def test_unregistered_assets_are_rejected(client, origin, suffix):
    with pytest.raises(SourceError):
        client.app.state.gateway.validate_media_url(origin.base_url + suffix)


def test_upstream_redirect_is_not_followed(client, origin):
    play = start(client)
    origin.redirects["/files/ep-1/movie.mp4"] = "http://127.0.0.1:1/secret"
    assert client.get(play["media_url"]).status_code == 502
    origin.redirects["/api.php/provide/vod"] = "http://127.0.0.1:1/secret"
    assert client.get("/api/prototype/search").status_code == 502


def test_malformed_api_does_not_become_valid_catalog(client, origin):
    origin.override_json = {"code": 1, "list": "not-a-list"}
    assert client.get("/api/prototype/search").status_code == 502


@pytest.mark.parametrize("playlist", [
    '#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="key"\n',
    '#EXTM3U\nhttp://127.0.0.1:1/admin\n',
    '#EXTM3U\n#EXT-X-MAP:URI="../../unknown.mp4"\n',
    '#EXTM3U\n#EXT-X-MAP:URI=init.mp4\n',
    'not-a-playlist',
])
def test_unsupported_hls_is_rejected_before_exposing_urls(client, monkeypatch, playlist):
    play = start(client, "hls")
    monkeypatch.setattr(client.app.state.gateway, "read_bounded", lambda *_args, **_kwargs: playlist.encode())
    assert client.get(play["media_url"]).status_code == 502


def test_network_policy_rejects_ambiguous_or_untrusted_proxy():
    for policy in (NetworkPolicy("inherit"), NetworkPolicy("direct", "http://127.0.0.1:1"),
                   NetworkPolicy("proxy"), NetworkPolicy("proxy", "http://example.com:80")):
        with pytest.raises(ValueError):
            policy.client()


class ObservedMediaStream(httpx.SyncByteStream):
    """A bounded slow upstream whose close wakes any in-flight read."""

    def __init__(self, slow=False):
        self.started = threading.Event()
        self.waiting = threading.Event()
        self.closed = threading.Event()
        self.slow = slow

    def __iter__(self):
        self.started.set()
        yield b"x" * 65536
        if self.slow:
            self.waiting.set()
            self.closed.wait(timeout=5)
        if not self.closed.is_set():
            yield b"y" * 65536

    def close(self):
        self.closed.set()


def media_scope(path):
    return {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
            "http_version": "1.1", "method": "GET", "scheme": "http",
            "path": path, "raw_path": path.encode(), "query_string": b"",
            "root_path": "", "headers": [], "client": ("127.0.0.1", 1),
            "server": ("127.0.0.1", 2)}


async def idle_receive():
    await asyncio.sleep(10)
    return {"type": "http.disconnect"}


def test_disconnect_before_response_start_closes_unstarted_upstream(client, monkeypatch):
    play = start(client)
    stream = ObservedMediaStream()
    upstream = httpx.Response(200, stream=stream, request=httpx.Request("GET", "http://fixture/media"))
    monkeypatch.setattr(client.app.state.gateway.media, "send", lambda *_args, **_kwargs: upstream)

    async def run():
        async def send(_message):
            raise OSError("browser disconnected before response headers")

        with pytest.raises(ClientDisconnect):
            await client.app(media_scope(play["media_url"]), idle_receive, send)

    asyncio.run(run())
    assert not stream.started.is_set()
    assert upstream.is_closed and stream.closed.is_set()


def test_cancel_during_slow_upstream_read_closes_connection(client, monkeypatch):
    play = start(client)
    stream = ObservedMediaStream(slow=True)
    upstream = httpx.Response(200, stream=stream, request=httpx.Request("GET", "http://fixture/media"))
    monkeypatch.setattr(client.app.state.gateway.media, "send", lambda *_args, **_kwargs: upstream)

    async def run():
        async def send(_message):
            return None

        task = asyncio.create_task(client.app(media_scope(play["media_url"]), idle_receive, send))
        assert await asyncio.to_thread(stream.waiting.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=2)
        assert upstream.is_closed and stream.closed.is_set()

    asyncio.run(run())


def test_revoking_session_during_stream_stops_and_closes_only_that_upstream(client, monkeypatch):
    play = start(client)
    other = start(client, episode="ep-2")
    stream = ObservedMediaStream()
    upstream = httpx.Response(200, stream=stream, request=httpx.Request("GET", "http://fixture/media"))
    gateway = client.app.state.gateway
    monkeypatch.setattr(gateway.media, "send", lambda *_args, **_kwargs: upstream)
    received = []

    async def run():
        async def send(message):
            if message["type"] == "http.response.body" and message.get("body"):
                received.append(message["body"])
                with gateway.lock:
                    gateway.sessions.pop(play["session_id"], None)

        await client.app(media_scope(play["media_url"]), idle_receive, send)

    asyncio.run(run())
    assert received == [b"x" * 65536]
    assert upstream.is_closed and stream.closed.is_set()
    assert gateway.session(other["session_id"]).episode_id == "ep-2"
