#!/usr/bin/env python3
"""Fixed Apple HLS diagnostic relay; no decoding, remuxing, or media files.

Configure P0_HLS_RELAY_CONFIG with token (32+ URL-safe characters), port,
entry_url, ttl_seconds (default 2700), and max_bytes_total (default 512 MiB).
Only the public bipbop_adv_example_hevc or bipbop_4x3 HTTPS directory is accepted.
The entry selects one directory; child resources cannot cross between them. The
token health endpoint reports entry_path, relative to the token directory.
This is an isolated diagnostic, not a general-purpose production gateway.

The only local dependency is public_relay.Relay, reused for process metrics.
For remote stdin execution its module can be injected in sys.modules first.
"""

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import re
import secrets
import signal
import socket
import threading
import time
from urllib.parse import urljoin, urlsplit

import httpx

from scripts.online_p0.public_relay import Relay as ProcessMetricsRelay


ORIGIN = "https://devstreaming-cdn.apple.com"
PREFIX = "/videos/streaming/examples/bipbop_adv_example_hevc/"
SOURCE_PREFIXES = (PREFIX, "/videos/streaming/examples/bipbop_4x3/")
CHUNK_BYTES = 64 * 1024
MANIFEST_BYTES = 256 * 1024
SEGMENT_BYTES = 8 * 1024 * 1024
RESOURCE_LIMIT = 8192
MAX_ACTIVE = 4
MAX_QUEUED = 8
QUEUE_WAIT_SECONDS = 2
MAX_HANDLERS = 16
URI_ATTRIBUTE = re.compile(r'(?<![\w-])([\w-]*URI)="([^"\r\n]*)"')
CONTENT_RANGE = re.compile(r"bytes (\d+)-(\d+)/(\d+)")
BYTE_RANGE = re.compile(r"bytes=(\d*)-(\d*)")


class RelayError(ValueError):
    """The fixed diagnostic resource contract was not satisfied."""


class BudgetError(RelayError):
    """No further upstream body bytes are allowed."""


class StoppingError(RelayError):
    """A diagnostic shutdown cancelled an admitted media request."""


def checked_url(value, base=None, *, prefix=PREFIX):
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise RelayError("invalid resource URL")
    if any(ord(character) < 32 for character in value) or "\\" in value:
        raise RelayError("invalid URL characters")
    target = urljoin(base or ORIGIN + prefix, value)
    parsed = urlsplit(target)
    if (parsed.scheme != "https" or parsed.netloc != "devstreaming-cdn.apple.com"
            or not parsed.path.startswith(prefix) or "%" in parsed.path
            or parsed.fragment or "/../" in parsed.path or "/./" in parsed.path):
        raise RelayError("resource outside the fixed public HLS directory")
    return target


def validate_config(config):
    if not isinstance(config, dict):
        raise ValueError("configuration must be an object")
    token = config.get("token", "")
    if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", token):
        raise ValueError("token must contain 32-128 URL-safe characters")
    port = config.get("port", 0)
    if not isinstance(port, int) or isinstance(port, bool) or not 0 <= port <= 65535:
        raise ValueError("invalid port")
    ttl = config.get("ttl_seconds", 2700)
    if not isinstance(ttl, (int, float)) or isinstance(ttl, bool) or not 1 <= ttl <= 3600:
        raise ValueError("ttl_seconds must be between 1 and 3600")
    maximum = config.get("max_bytes_total", 512 * 1024 * 1024)
    if not isinstance(maximum, int) or isinstance(maximum, bool) or not 1 <= maximum <= 1024 ** 3:
        raise ValueError("invalid total byte budget")
    entry = config.get("entry_url", ORIGIN + PREFIX + "master.m3u8")
    if not isinstance(entry, str):
        raise ValueError("entry URL must be a string")
    prefix = next((value for value in SOURCE_PREFIXES if urlsplit(entry).path.startswith(value)), None)
    if prefix is None:
        raise ValueError("entry is outside the fixed public directories")
    entry = checked_url(entry, prefix=prefix)
    if not urlsplit(entry).path.lower().endswith(".m3u8"):
        raise ValueError("entry must be an HLS playlist")
    return {"token": token, "port": port, "ttl_seconds": float(ttl), "entry_url": entry, "prefix": prefix,
            "max_bytes_total": maximum, "max_range_bytes": SEGMENT_BYTES}


class HlsRelay(ProcessMetricsRelay):
    """Reuse only our existing counters/metrics/close helper, not its URL policy."""

    def __init__(self, config, *, transport=None):
        self.config = validate_config(config)
        self.started = time.monotonic()
        self.lock = threading.Lock()
        self.drain_condition = threading.Condition(self.lock)
        self.slots = threading.BoundedSemaphore(MAX_ACTIVE)
        self.queue_slots = threading.BoundedSemaphore(MAX_QUEUED)
        self.stop = threading.Event()
        self.counts = dict(requests=0, active=0, peak_active=0, completed=0,
                           bytes_sent=0, upstream_bytes=0, upstream_errors=0,
                           client_disconnects=0, retries=0, rejected_busy=0,
                           budget_charged_bytes=0, rejected_budget=0,
                           queued=0, peak_queued=0, queue_wait_seconds=0,
                           rejected_handlers=0, cancelled_requests=0)
        self.resources = {}
        self.resource_ids = {}
        self.entry_id = self.register(self.config["entry_url"])
        self.client = httpx.Client(
            trust_env=False, follow_redirects=False, transport=transport,
            timeout=httpx.Timeout(connect=10, read=15, write=10, pool=1),
            limits=httpx.Limits(max_connections=MAX_ACTIVE, max_keepalive_connections=MAX_ACTIVE),
            headers={"Accept-Encoding": "identity", "User-Agent": "jzmedia-P0-HLS/1"},
        )

    def metrics(self):
        result = super().metrics()
        with self.lock:
            result["registered_resources"] = len(self.resources)
        result.update(entry_path=f"media/{self.entry_id}", max_media_requests=MAX_ACTIVE,
                      manifest_limit_bytes=MANIFEST_BYTES, segment_limit_bytes=SEGMENT_BYTES,
                      resource_limit=RESOURCE_LIMIT, max_queued_requests=MAX_QUEUED,
                      queue_wait_limit_seconds=QUEUE_WAIT_SECONDS, max_http_handlers=MAX_HANDLERS,
                      stopping=self.stop.is_set())
        return result

    def acquire_slot(self):
        if self.stop.is_set():
            self.add("cancelled_requests")
            return False
        if self.slots.acquire(blocking=False):
            if self.stop.is_set():
                self.slots.release()
                self.add("cancelled_requests")
                return False
            return True
        if not self.queue_slots.acquire(blocking=False):
            self.add("rejected_busy")
            return False
        started = time.monotonic()
        with self.lock:
            self.counts["queued"] += 1
            self.counts["peak_queued"] = max(self.counts["peak_queued"], self.counts["queued"])
        acquired = False
        try:
            deadline = started + QUEUE_WAIT_SECONDS
            while not self.stop.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                if self.slots.acquire(timeout=min(0.05, remaining)):
                    acquired = True
                    break
        finally:
            with self.drain_condition:
                self.counts["queued"] -= 1
                self.counts["queue_wait_seconds"] += time.monotonic() - started
                self.drain_condition.notify_all()
            self.queue_slots.release()
        if acquired and self.stop.is_set():
            self.slots.release()
            acquired = False
        if not acquired:
            self.add("cancelled_requests" if self.stop.is_set() else "rejected_busy")
        return acquired

    def drain(self, timeout=20):
        """Stop admission and observe media cleanup without waiting indefinitely."""
        if not 0 <= timeout <= 20:
            raise ValueError("drain timeout must be between 0 and 20 seconds")
        self.stop.set()
        started = time.monotonic()
        deadline = started + timeout
        with self.drain_condition:
            while self.counts["active"] or self.counts["queued"]:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self.drain_condition.wait(remaining)
            active, queued = self.counts["active"], self.counts["queued"]
        return {"drained": active == queued == 0,
                "drain_wait_seconds": round(time.monotonic() - started, 3),
                "drain_timeout_seconds": timeout,
                "drain_active_remaining": active, "drain_queued_remaining": queued}

    def register(self, url, base=None):
        url = checked_url(url, base, prefix=self.config["prefix"])
        with self.lock:
            if url in self.resource_ids:
                return self.resource_ids[url]
            if len(self.resources) >= RESOURCE_LIMIT:
                raise RelayError("resource registry limit reached")
            resource_id = secrets.token_urlsafe(18)
            kind = "manifest" if urlsplit(url).path.lower().endswith(".m3u8") else "media"
            self.resources[resource_id] = (url, kind)
            self.resource_ids[url] = resource_id
            return resource_id

    def rewrite(self, body, base):
        if len(body) > MANIFEST_BYTES:
            raise RelayError("manifest exceeds byte limit")
        text = body.decode("utf-8-sig")
        if not text.startswith("#EXTM3U"):
            raise RelayError("not an HLS manifest")

        def resource_path(uri):
            resource_id = self.register(uri, base)
            return f"/{self.config['token']}/media/{resource_id}"

        lines = []
        for raw in text.splitlines():
            line = raw.strip()
            if line and not line.startswith("#"):
                lines.append(resource_path(line))
            else:
                lines.append(URI_ATTRIBUTE.sub(lambda match: f'{match[1]}="{resource_path(match[2])}"', raw))
        rewritten = ("\n".join(lines) + "\n").encode()
        if len(rewritten) > MANIFEST_BYTES * 4:
            raise RelayError("rewritten manifest exceeds limit")
        return rewritten

    @contextmanager
    def upstream(self, method, url, headers=None):
        if self.stop.is_set():
            raise StoppingError("diagnostic stopping")
        checked_url(url, prefix=self.config["prefix"])
        with self.lock:
            if self.counts["budget_charged_bytes"] >= self.config["max_bytes_total"]:
                self.counts["rejected_budget"] += 1
                raise BudgetError("diagnostic byte budget exhausted")
        request = self.client.build_request(method, url, headers=headers)
        if self.stop.is_set():
            raise StoppingError("diagnostic stopping")
        response = self.client.send(request, stream=True)
        try:
            if self.stop.is_set():
                raise StoppingError("diagnostic stopping")
            yield response
        finally:
            response.close()

    @contextmanager
    def body_budget(self, upper_bound):
        # Reserve before reading, so parallel requests cannot exceed the budget.
        with self.lock:
            available = self.config["max_bytes_total"] - self.counts["budget_charged_bytes"]
            if upper_bound > available:
                self.counts["rejected_budget"] += 1
                raise BudgetError("diagnostic byte budget exhausted")
            self.counts["budget_charged_bytes"] += upper_bound
        consumed = [0]
        try:
            yield consumed
        finally:
            with self.lock:
                self.counts["budget_charged_bytes"] -= upper_bound - consumed[0]

    def read_chunks(self, response, limit, consumed):
        size = 0
        if self.stop.is_set():
            raise StoppingError("diagnostic stopping")
        for chunk in response.iter_raw(chunk_size=CHUNK_BYTES):
            size += len(chunk)
            self.add("upstream_bytes", len(chunk))
            consumed[0] += len(chunk)
            if size > limit:
                raise RelayError("response exceeds byte limit")
            if self.stop.is_set():
                raise StoppingError("diagnostic stopping")
            yield chunk
            if size == limit:
                # Unknown-length streams reaching the cap cannot prove EOF without
                # another read. Fail closed instead of reading beyond the budget.
                if response.headers.get("content-length") is None:
                    raise RelayError("unknown-length response reached byte limit")
                return


def checked_length(response, cap):
    if response.headers.get("content-encoding", "identity").lower() != "identity":
        raise RelayError("encoded response rejected")
    raw = response.headers.get("content-length")
    if raw is None:
        return None
    if not raw.isdigit() or not 0 <= int(raw) <= cap:
        raise RelayError("invalid response length")
    return int(raw)


def checked_range(header):
    match = BYTE_RANGE.fullmatch(header or "")
    if not match or not any(match.groups()):
        raise ValueError("one byte range required")
    start, end = match.groups()
    if not start:
        if not 0 < int(end) <= SEGMENT_BYTES:
            raise ValueError("invalid suffix range")
    elif end and (int(end) < int(start) or int(end) - int(start) + 1 > SEGMENT_BYTES):
        raise ValueError("range exceeds segment limit")
    return match.groups()


def validate_response_range(response, requested):
    match = CONTENT_RANGE.fullmatch(response.headers.get("content-range", ""))
    if response.status_code != 206 or not match:
        raise RelayError("upstream ignored range")
    start, end, total = map(int, match.groups())
    first, last = requested
    expected_start = int(first) if first else max(0, total - int(last))
    expected_end = min(int(last), total - 1) if first and last else total - 1
    if start != expected_start or end != expected_end or not 0 <= start <= end < total:
        raise RelayError("inconsistent range response")
    if end - start + 1 > SEGMENT_BYTES:
        raise RelayError("range response exceeds limit")
    length = checked_length(response, SEGMENT_BYTES)
    if length != end - start + 1:
        raise RelayError("inconsistent range length")
    return length


def make_server(relay):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def setup(self):
            super().setup()
            self.connection.settimeout(15)

        def log_message(self, *_args):
            pass

        def headers_out(self, status, headers):
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, str(value))
            self.send_header("Connection", "close")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Expose-Headers", "Content-Length, Content-Range, Accept-Ranges")
            self.end_headers()
            self.close_connection = True

        def json_out(self, status, value):
            body = json.dumps(value).encode()
            self.headers_out(status, {"Content-Type": "application/json", "Content-Length": len(body)})
            if self.command != "HEAD":
                self.wfile.write(body)

        def do_HEAD(self):
            self.do_GET()

        def do_POST(self):
            if not secrets.compare_digest(self.path, f"/{relay.config['token']}/shutdown"):
                self.json_out(404, {"error": "not found"})
                return
            self.json_out(200, {"stopping": True})
            relay.stop.set()

        def do_GET(self):
            parts = self.path.split("/")
            if len(parts) < 3 or not secrets.compare_digest(parts[1], relay.config["token"]):
                self.json_out(404, {"error": "not found"})
                return
            if parts[2:] in (["health"], ["metrics"]):
                self.json_out(200, {"ok": True, **relay.metrics()})
                return
            if len(parts) != 4 or parts[2] != "media":
                self.json_out(404, {"error": "not found"})
                return
            with relay.lock:
                resource = relay.resources.get(parts[3])
            if resource is None:
                self.json_out(404, {"error": "not found"})
                return
            if not relay.acquire_slot():
                if relay.stop.is_set():
                    self.json_out(503, {"error": "diagnostic stopping"})
                else:
                    self.json_out(429, {"error": "bounded upstream queue unavailable"})
                return
            with relay.lock:
                stopping = relay.stop.is_set()
                if stopping:
                    relay.counts["cancelled_requests"] += 1
                else:
                    relay.counts["requests"] += 1
                    relay.counts["active"] += 1
                    relay.counts["peak_active"] = max(relay.counts["peak_active"], relay.counts["active"])
            if stopping:
                relay.slots.release()
                self.json_out(503, {"error": "diagnostic stopping"})
                return
            self.sent_upstream_headers = False
            try:
                self.resource(*resource)
            except StoppingError:
                relay.add("cancelled_requests")
                if not self.sent_upstream_headers:
                    self.json_out(503, {"error": "diagnostic stopping"})
            except (BrokenPipeError, ConnectionResetError, TimeoutError, socket.timeout):
                relay.add("client_disconnects")
            except (httpx.HTTPError, RelayError, UnicodeError):
                relay.add("cancelled_requests" if relay.stop.is_set() else "upstream_errors")
                if not self.sent_upstream_headers:
                    if relay.stop.is_set():
                        self.json_out(503, {"error": "diagnostic stopping"})
                    else:
                        self.json_out(502, {"error": "upstream HLS resource unavailable"})
            finally:
                self.close_connection = True
                with relay.drain_condition:
                    relay.counts["active"] -= 1
                    relay.drain_condition.notify_all()
                relay.slots.release()

        def resource(self, url, kind):
            raw_range = self.headers.get("Range")
            if kind == "manifest":
                if raw_range:
                    self.json_out(400, {"error": "manifest byte ranges are unsupported"})
                    return
                return self.manifest(url)
            requested = None
            if raw_range:
                try:
                    requested = checked_range(raw_range)
                except ValueError:
                    self.json_out(400, {"error": "one bounded byte range required"})
                    return
            headers = {"Range": raw_range} if raw_range else None
            with relay.upstream(self.command, url, headers) as response:
                if response.status_code == 416 and requested:
                    content_range = response.headers.get("content-range", "")
                    if not re.fullmatch(r"bytes \*/\d+", content_range):
                        raise RelayError("invalid upstream 416")
                    self.headers_out(416, {"Content-Range": content_range, "Content-Length": 0})
                    return
                if response.status_code in {401, 403, 404, 410, 429}:
                    relay.add("upstream_errors")
                    self.json_out(response.status_code, {"error": "upstream rejected resource"})
                    return
                if response.status_code != (206 if requested else 200):
                    raise RelayError("unexpected upstream status or redirect")
                # HEAD describes a whole object without consuming its body; a
                # large HLS byte-range backing file is valid metadata.
                body_cap = (2 ** 63 - 1) if self.command == "HEAD" else SEGMENT_BYTES
                length = validate_response_range(response, requested) if requested else checked_length(response, body_cap)
                content_type = response.headers.get("content-type", "application/octet-stream")
                if content_type.lower().startswith(("text/html", "application/json")):
                    raise RelayError("not a media resource")
                output = {"Content-Type": content_type, "Accept-Ranges": "bytes"}
                if requested:
                    output["Content-Range"] = response.headers["content-range"]
                if length is not None:
                    output["Content-Length"] = length
                elif self.command != "HEAD":
                    output["Transfer-Encoding"] = "chunked"
                if self.command == "HEAD":
                    self.headers_out(response.status_code, output)
                    return
                with relay.body_budget(length if length is not None else SEGMENT_BYTES) as consumed:
                    self.headers_out(response.status_code, output)
                    self.sent_upstream_headers = True
                    for chunk in relay.read_chunks(response, length if length is not None else SEGMENT_BYTES, consumed):
                        wire = chunk if length is not None else f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n"
                        self.wfile.write(wire)
                        relay.add("bytes_sent", len(chunk))
                    if length is not None and consumed[0] != length:
                        raise RelayError("truncated media response")
                    if length is None:
                        self.wfile.write(b"0\r\n\r\n")
                    relay.add("completed")

        def manifest(self, url):
            with relay.upstream("GET", url) as response:
                if response.status_code != 200:
                    raise RelayError("manifest request failed or redirected")
                length = checked_length(response, MANIFEST_BYTES)
                with relay.body_budget(length if length is not None else MANIFEST_BYTES) as consumed:
                    body = b"".join(relay.read_chunks(response, length if length is not None else MANIFEST_BYTES, consumed))
                if length is not None and len(body) != length:
                    raise RelayError("truncated manifest")
            rewritten = relay.rewrite(body, url)
            self.headers_out(200, {"Content-Type": "application/vnd.apple.mpegurl",
                                   "Content-Length": len(rewritten)})
            self.sent_upstream_headers = True
            if self.command != "HEAD":
                self.wfile.write(rewritten)
                relay.add("bytes_sent", len(rewritten))
            relay.add("completed")

    class BoundedHTTPServer(ThreadingHTTPServer):
        def __init__(self, address, handler):
            self.handler_slots = threading.BoundedSemaphore(MAX_HANDLERS)
            super().__init__(address, handler)

        def process_request(self, request, client_address):
            # Bound even connections that have not supplied an HTTP request yet.
            if not self.handler_slots.acquire(blocking=False):
                relay.add("rejected_handlers")
                self.shutdown_request(request)
                return
            try:
                super().process_request(request, client_address)
            except BaseException:
                self.handler_slots.release()
                raise

        def process_request_thread(self, request, client_address):
            try:
                super().process_request_thread(request, client_address)
            finally:
                self.handler_slots.release()

    server = BoundedHTTPServer(("127.0.0.1", relay.config["port"]), Handler)
    server.daemon_threads = True
    return server


def main():
    try:
        relay = HlsRelay(json.loads(os.environ.get("P0_HLS_RELAY_CONFIG", "{}")))
    except (TypeError, ValueError):
        print(json.dumps({"event": "config_error"}), flush=True)
        return 2
    server = make_server(relay)
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_args: relay.stop.set())
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
    thread.start()
    print(json.dumps({"event": "ready", "port": server.server_port, "pid": os.getpid(),
                      "entry_path": f"media/{relay.entry_id}", "ttl_seconds": relay.config["ttl_seconds"]}), flush=True)
    try:
        relay.stop.wait(relay.config["ttl_seconds"])
    finally:
        relay.stop.set()
        server.shutdown()
        server.server_close()
        drained = relay.drain()
        relay.close()
        thread.join(timeout=5)
        print(json.dumps({"event": "stopped", **relay.metrics(), **drained}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
