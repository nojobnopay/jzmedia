"""Real loopback HTTP/proxy/TLS paths, without public DNS or media decoding."""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import base64
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
from pathlib import Path
import select
import shutil
import socket
import ssl
import subprocess
import threading
import time
from urllib.parse import urlsplit

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
import pytest

from app import library_paths, secrets, store
from app.network.client import NetworkRequestError, create_client as guarded_client
from app.network.headers import HeaderGrant, HeaderPolicy
from app.network.guard import NetworkOperation
from app.network.policy import NetworkPolicyError, resolve_online_policy
from app.network.targets import TargetPolicy
from app.store import online, online_network


class LoopbackPolicy(TargetPolicy):
    """Test-only socket fixture seam; production policies always reject loopback."""

    def _validate_address(self, value):
        if value == "127.0.0.1":
            return value
        return super()._validate_address(value)


def create_client(snapshot, *, targets=None, header_policy=None):
    return guarded_client(snapshot, targets=targets or LoopbackPolicy(snapshot.source_id, ["127.0.0.1"]),
                          header_policy=header_policy)


def logical_url(url, host):
    parts = urlsplit(url)
    return parts._replace(netloc=f"{host}:{parts.port}").geturl()


@pytest.fixture
def source(tmp_path, monkeypatch):
    monkeypatch.setattr(store._base, "DB_PATH", str(tmp_path / "transport.db"))
    monkeypatch.setattr(secrets.settings, "data_dir", str(tmp_path / "data"))
    for key in ("SSL_CERT_FILE", "SSL_CERT_DIR"):
        monkeypatch.delenv(key, raising=False)
    secrets.reset_cache()
    library_paths.invalidate_cache()
    store.init_db()
    result = online.create_online_source({"name": "loopback fixture only", "provider": "maccms",
                                         "api_base_url": "https://example.invalid/api"})
    try:
        yield result["id"]
    finally:
        secrets.reset_cache()
        library_paths.invalidate_cache()


@contextmanager
def running_server(handler, tls_context=None):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    if tls_context:
        server.socket = tls_context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .05}, daemon=True)
    thread.start()
    scheme = "https" if tls_context else "http"
    try:
        yield f"{scheme}://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.fixture
def origin():
    records = []
    release = threading.Event()
    routes = {}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def setup(self):
            super().setup()
            self.connection.settimeout(4)

        def log_message(self, *_args):
            pass

        def do_GET(self):
            records.append({"path": self.path, "headers": dict(self.headers)})
            route = routes.get(self.path) or routes.get(urlsplit(self.path).path)
            if route:
                status, headers = route
                self.send_response(status)
                for name, value in headers:
                    self.send_header(name, value)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "/followed")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            body = b"0123456789" if self.path == "/stream" else b"isolated upstream"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.path == "/stream":
                self.wfile.write(body[:1])
                self.wfile.flush()
                release.wait(timeout=5)
                try:
                    self.wfile.write(body[1:])
                except (BrokenPipeError, ConnectionResetError):
                    pass
            else:
                self.wfile.write(body)

    with running_server(Handler) as url:
        try:
            yield {"url": url, "records": records, "release": release, "handler": Handler, "routes": routes}
        finally:
            release.set()


@contextmanager
def proxy_server(target_url, username="", password="", tls_context=None):
    records = []
    target = urlsplit(target_url)
    expected = "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def setup(self):
            super().setup()
            self.connection.settimeout(4)

        def log_message(self, *_args):
            pass

        def authorized(self):
            records.append({"method": self.command, "path": self.path,
                            "proxy_authorization": self.headers.get("Proxy-Authorization")})
            if username or password:
                if self.headers.get("Proxy-Authorization") != expected:
                    self.send_error(407)
                    return False
            return True

        def do_GET(self):
            if not self.authorized():
                return
            parsed = urlsplit(self.path)
            # This fixture cannot forward to any external or unregistered host.
            if parsed.scheme != "http" or (parsed.hostname, parsed.port) != (target.hostname, target.port):
                self.send_error(502)
                return
            forwarded = {key: value for key, value in self.headers.items()
                         if key.lower() not in {"proxy-authorization", "proxy-connection", "connection"}}
            connection = http.client.HTTPConnection(target.hostname, target.port, timeout=3)
            try:
                path = parsed.path or "/"
                if parsed.query:
                    path += "?" + parsed.query
                connection.request("GET", path, headers=forwarded)
                response = connection.getresponse()
                body = response.read(65537)
                if len(body) > 65536:
                    self.send_error(502)
                    return
                self.send_response(response.status)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("X-Isolated-Proxy", "yes")
                self.end_headers()
                self.wfile.write(body)
            finally:
                connection.close()

        def do_CONNECT(self):
            if not self.authorized():
                return
            if self.path != f"{target.hostname}:{target.port}":
                self.send_error(502)
                return
            with socket.create_connection((target.hostname, target.port), timeout=3) as upstream:
                self.send_response(200)
                self.end_headers()
                self.wfile.flush()
                deadline = time.monotonic() + 5
                peers = (self.connection, upstream)
                while time.monotonic() < deadline:
                    readable, _, _ = select.select(peers, [], [], .1)
                    for peer in readable:
                        try:
                            body = peer.recv(16384)
                        except (OSError, ssl.SSLError):
                            return
                        if not body:
                            return
                        (upstream if peer is self.connection else self.connection).sendall(body)

    with running_server(Handler, tls_context) as url:
        yield {"url": url, "records": records}


@pytest.fixture
def certificate(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "isolated test CA")])
    now = datetime.now(timezone.utc)
    certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                   .public_key(key.public_key()).serial_number(x509.random_serial_number())
                   .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=1))
                   .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
                   .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost"),
                                    x509.DNSName("fixture.test"), x509.DNSName("next.test"),
                                    x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
                   .sign(key, hashes.SHA256()))
    cert_path = tmp_path / "ca.pem"
    key_path = tmp_path / "key.pem"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    return {"path": str(cert_path), "context": context}


def profile(proxy=None, **values):
    return online_network.create_network_profile({"name": "isolated connection", "mode": "proxy" if proxy else "direct",
                                                   **({"proxy_url": proxy} if proxy else {}), **values})


def bind(sid, purpose, record):
    online_network.update_source_network_policy(sid, {purpose: {"mode": record["mode"], "profile_id": record["id"]}})


def test_direct_ignores_environment_proxies(source, origin, monkeypatch):
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.setenv(key, "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setenv("no_proxy", "")
    with create_client(resolve_online_policy(source, "media")) as client:
        assert client.request("GET", origin["url"] + "/direct").status_code == 200
    assert [row["path"] for row in origin["records"]] == ["/direct"]


def test_api_proxy_and_media_direct_use_independent_routes(source, origin, monkeypatch):
    with proxy_server(origin["url"], "test-user", "test-password") as proxy:
        configured = profile(proxy["url"], username="test-user", password="test-password")
        bind(source, "api", configured)
        monkeypatch.setenv("NO_PROXY", "*")
        monkeypatch.setenv("no_proxy", "*")
        with create_client(resolve_online_policy(source, "api")) as api:
            assert api.request("GET", origin["url"] + "/api").status_code == 200
        with create_client(resolve_online_policy(source, "media")) as media:
            assert "X-Isolated-Proxy" not in media.request("GET", origin["url"] + "/media").headers
        assert len(proxy["records"]) == 1
        assert proxy["records"][0]["method"] == "CONNECT"
        assert proxy["records"][0]["path"] == urlsplit(origin["url"]).netloc
        assert proxy["records"][0]["proxy_authorization"].startswith("Basic ")
    assert [row["path"] for row in origin["records"]] == ["/api", "/media"]
    assert all("Proxy-Authorization" not in row["headers"] for row in origin["records"])


def test_media_subresources_stay_on_captured_policy_after_config_change(source, origin):
    with proxy_server(origin["url"]) as old_proxy, proxy_server(origin["url"]) as new_proxy:
        configured = profile(old_proxy["url"])
        bind(source, "media", configured)
        snapshot = resolve_online_policy(source, "media")
        online_network.update_network_profile(configured["id"], {"proxy_url": new_proxy["url"]})
        newer = resolve_online_policy(source, "media")
        assert snapshot.fingerprint != newer.fingerprint
        with create_client(snapshot) as media:
            for path in ("/master.m3u8", "/child.m3u8", "/init.mp4", "/segment.ts"):
                assert media.request("GET", origin["url"] + path).status_code == 200
        with create_client(newer) as media:
            assert media.request("GET", origin["url"] + "/new-session").status_code == 200
        assert len(old_proxy["records"]) == 1
        assert len(new_proxy["records"]) == 1


def test_proxy_failure_never_falls_back_to_direct(source, origin):
    with socket.socket() as closed:
        closed.bind(("127.0.0.1", 0))
        unavailable_port = closed.getsockname()[1]
    configured = profile(f"http://127.0.0.1:{unavailable_port}", connect_timeout_seconds=1)
    bind(source, "media", configured)
    with create_client(resolve_online_policy(source, "media")) as client:
        with pytest.raises(NetworkRequestError):
            client.request("GET", origin["url"] + "/must-not-bypass")
    assert origin["records"] == []


def test_each_redirect_is_validated_before_following(source, origin):
    with create_client(resolve_online_policy(source, "api")) as client:
        assert client.request("GET", origin["url"] + "/redirect").status_code == 200
    assert [row["path"] for row in origin["records"]] == ["/redirect", "/followed"]


def test_purpose_pools_are_isolated_and_each_pool_is_bounded(source, origin):
    configured = profile(max_connections=1, connect_timeout_seconds=1, read_timeout_seconds=2)
    bind(source, "media", configured)
    bind(source, "api", configured)
    try:
        with create_client(resolve_online_policy(source, "media")) as media:
            with media.stream("GET", origin["url"] + "/stream") as stream:
                assert stream.status_code == 200
                with create_client(resolve_online_policy(source, "api")) as api:
                    assert api.request("GET", origin["url"] + "/independent-api").status_code == 200
                with pytest.raises(NetworkRequestError):
                    media.request("GET", origin["url"] + "/pool-must-not-grow")
    finally:
        origin["release"].set()
    assert [row["path"] for row in origin["records"]] == ["/stream", "/independent-api"]


def test_guarded_pool_wait_obeys_the_shorter_operation_deadline(source, origin):
    configured = profile(max_connections=1, connect_timeout_seconds=5, read_timeout_seconds=2)
    bind(source, "media", configured)
    try:
        with create_client(resolve_online_policy(source, "media")) as media:
            with media.stream("GET", origin["url"] + "/stream"):
                started = time.monotonic()
                operation = NetworkOperation(started + .15)
                with pytest.raises(NetworkRequestError, match="^operation_timeout$"):
                    with media.guarded_stream("GET", origin["url"] + "/never-dial", operation=operation):
                        pytest.fail("the occupied pool must not provide another connection")
                assert time.monotonic() - started < .75
    finally:
        origin["release"].set()
    assert [row["path"] for row in origin["records"]] == ["/stream"]


def test_ssl_cert_file_is_preserved_and_frozen_despite_trust_env_false(source, origin, certificate, monkeypatch):
    with running_server(origin["handler"], certificate["context"]) as url:
        monkeypatch.setenv("SSL_CERT_FILE", certificate["path"])
        snapshot = resolve_online_policy(source, "api")
        monkeypatch.setenv("SSL_CERT_FILE", str(Path(certificate["path"]).with_name("not-present.pem")))
        with create_client(snapshot) as client:
            assert client.request("GET", url + "/trusted").status_code == 200
        with pytest.raises(NetworkPolicyError) as failure:
            resolve_online_policy(source, "api")
        assert "not-present.pem" not in str(failure.value)


def test_ssl_cert_dir_is_explicitly_preserved(source, origin, certificate, monkeypatch, tmp_path):
    openssl = shutil.which("openssl")
    if openssl is None:
        pytest.skip("OpenSSL command required to construct its hashed CA directory")
    result = subprocess.run([openssl, "x509", "-hash", "-noout", "-in", certificate["path"]],
                            check=True, capture_output=True, text=True, timeout=5)
    cert_dir = tmp_path / "hashed-ca"
    cert_dir.mkdir()
    shutil.copyfile(certificate["path"], cert_dir / (result.stdout.strip() + ".0"))
    monkeypatch.setenv("SSL_CERT_DIR", str(cert_dir))
    with running_server(origin["handler"], certificate["context"]) as url:
        with create_client(resolve_online_policy(source, "api")) as client:
            assert client.request("GET", url + "/directory-ca").status_code == 200


@pytest.mark.parametrize("proxy_tls", [False, True])
def test_connect_checks_target_and_https_proxy_certificates(source, origin, certificate, proxy_tls):
    with running_server(origin["handler"], certificate["context"]) as secure_origin:
        with proxy_server(secure_origin, "tunnel-user", "tunnel-password",
                          certificate["context"] if proxy_tls else None) as proxy:
            configured = profile(proxy["url"], username="tunnel-user", password="tunnel-password",
                                 ca_file=certificate["path"])
            bind(source, "media", configured)
            with create_client(resolve_online_policy(source, "media")) as client:
                assert client.request("GET", secure_origin + "/secure").status_code == 200
            assert proxy["records"][0]["method"] == "CONNECT"
            assert proxy["records"][0]["proxy_authorization"].startswith("Basic ")
    assert "Proxy-Authorization" not in origin["records"][0]["headers"]


@pytest.mark.parametrize("route", ["direct", "http_proxy", "https_proxy"])
def test_numeric_connections_preserve_logical_host_tls_and_pool_identity(source, origin, certificate, route):
    names = []
    certificate["context"].set_servername_callback(lambda _socket, name, _context: names.append(name))
    snapshot = None
    with running_server(origin["handler"], certificate["context"]) as secure:
        with proxy_server(secure, "tunnel", "private-password",
                          certificate["context"] if route == "https_proxy" else None) as proxy:
            proxy_url = logical_url(proxy["url"], "localhost") if route != "direct" else None
            configured = profile(proxy_url, ca_file=certificate["path"],
                                 **({"username": "tunnel", "password": "private-password"} if proxy_url else {}))
            bind(source, "media", configured)
            snapshot = resolve_online_policy(source, "media")
            resolutions = []

            def resolve(host, port):
                resolutions.append((host, port))
                return ("127.0.0.1",)

            targets = LoopbackPolicy(source, ["fixture.test", "next.test"], resolver=resolve)
            with create_client(snapshot, targets=targets) as client:
                for host in ("fixture.test", "next.test", "fixture.test"):
                    assert client.request("GET", logical_url(secure, host) + "/host").status_code == 200
            assert [host for host, _port in resolutions] == ["fixture.test", "next.test"]
            assert [row["headers"]["Host"] for row in origin["records"]] == [
                f"{host}:{urlsplit(secure).port}" for host in ("fixture.test", "next.test", "fixture.test")]
            # Two domains sharing an IP must not share the same logical TLS connection.
            assert names.count("fixture.test") == names.count("next.test") == 1
            if proxy_url:
                assert len(proxy["records"]) == 2
                assert all(row["method"] == "CONNECT" and row["path"] == urlsplit(secure).netloc
                           for row in proxy["records"])
                assert names.count("localhost") == (2 if route == "https_proxy" else 0)
            else:
                assert proxy["records"] == []
    assert all("Proxy-Authorization" not in row["headers"] for row in origin["records"])


def test_dns_answers_are_pinned_across_requests_without_rebinding(source, origin):
    calls = []

    def rebinding(host, port):
        calls.append((host, port))
        return ("127.0.0.1",) if len(calls) == 1 else ("169.254.169.254",)

    targets = LoopbackPolicy(source, ["fixture.test"], resolver=rebinding)
    with create_client(resolve_online_policy(source, "api"), targets=targets) as client:
        for path in ("/first", "/second"):
            assert client.request("GET", logical_url(origin["url"], "fixture.test") + path).status_code == 200
    assert len(calls) == 1
    assert [row["path"] for row in origin["records"]] == ["/first", "/second"]


def test_cross_origin_redirect_reselects_grants_and_never_replays_cookies_or_params(source, origin):
    first = logical_url(origin["url"], "fixture.test")
    second = logical_url(origin["url"], "next.test")
    origin["routes"]["/start"] = (302, [("Location", second + "/final"),
                                             ("Set-Cookie", "upstream_cookie=response-secret; Path=/")])
    targets = LoopbackPolicy(source, ["fixture.test", "next.test"], resolver=lambda _h, _p: ("127.0.0.1",))
    headers = HeaderPolicy(source, [HeaderGrant(first, {"Authorization": "Bearer origin-a-secret",
                                                        "Cookie": "upstream_cookie=grant-secret",
                                                        "Referer": first + "/private-page", "Origin": first})])
    with create_client(resolve_online_policy(source, "api"), targets=targets, header_policy=headers) as client:
        assert client.request("GET", first + "/start", params={"first_only": "query-secret"},
                              headers={"Range": "bytes=0-9"}).status_code == 200
        assert client.request("GET", second + "/another").status_code == 200
        assert len(client._client.cookies.jar) == 0
    first_request, *following = origin["records"]
    assert first_request["path"] == "/start?first_only=query-secret"
    assert first_request["headers"]["Authorization"] == "Bearer origin-a-secret"
    assert first_request["headers"]["Cookie"] == "upstream_cookie=grant-secret"
    assert following[0]["path"] == "/final"
    assert following[0]["headers"]["Range"] == "bytes=0-9"
    for row in following:
        assert not {"Authorization", "Cookie", "Referer", "Origin"} & row["headers"].keys()


@pytest.mark.parametrize("allowed", [False, True])
def test_redirect_cannot_reach_unapproved_or_private_target(source, origin, allowed):
    first = logical_url(origin["url"], "fixture.test")
    second = logical_url(origin["url"], "next.test")
    origin["routes"]["/unsafe"] = (302, [("Location", second + "/private")])
    calls = []

    def resolve(host, _port):
        calls.append(host)
        return ("127.0.0.1",) if host == "fixture.test" else ("169.254.169.254",)

    targets = LoopbackPolicy(source, ["fixture.test", "next.test"] if allowed else ["fixture.test"], resolver=resolve)
    with create_client(resolve_online_policy(source, "api"), targets=targets) as client:
        with pytest.raises(NetworkRequestError, match="non_public_target_address" if allowed else "target_host_not_allowed"):
            client.request("GET", first + "/unsafe")
    assert calls == (["fixture.test", "next.test"] if allowed else ["fixture.test"])
    assert [row["path"] for row in origin["records"]] == ["/unsafe"]


@pytest.mark.parametrize("cycle", [False, True])
def test_redirect_limit_and_cycle_close_before_another_request(source, origin, cycle):
    for index in range(7):
        origin["routes"][f"/hop/{index}"] = (302, [("Location", "/hop/0" if cycle else f"/hop/{index + 1}")])
    with create_client(resolve_online_policy(source, "api")) as client:
        with pytest.raises(NetworkRequestError, match="redirect_cycle" if cycle else "redirect_limit"):
            client.request("GET", origin["url"] + "/hop/0")
    assert len(origin["records"]) == (1 if cycle else 6)


def test_https_redirect_cannot_downgrade_to_http(source, origin, certificate):
    configured = profile(ca_file=certificate["path"])
    bind(source, "api", configured)
    origin["routes"]["/downgrade"] = (302, [("Location", origin["url"] + "/insecure")])
    with running_server(origin["handler"], certificate["context"]) as secure:
        with create_client(resolve_online_policy(source, "api")) as client:
            with pytest.raises(NetworkRequestError, match="redirect_downgrade"):
                client.request("GET", secure + "/downgrade")
    assert [row["path"] for row in origin["records"]] == ["/downgrade"]
