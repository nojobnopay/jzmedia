#!/usr/bin/env python3
"""Fixed-public-sample, restricted-bind MP4 diagnostic relay. No decoding or media files.

Run with ONLINE_MP4_RELAY_CONFIG JSON, e.g. {"port": 18763, "token": "<32+ chars>",
"sources": {"lecture": "https://video.blender.org/object-storage/web_videos/
8e364d32-8918-403e-a8c0-915634b9f405-360.mp4"}, "ttl_seconds": 2700}.
The same file can be sent to ``python -u -``; no repository imports are needed.
Only GET/HEAD /<token>/media/<id>, /<token>/health and /<token>/metrics exist.
GET media requires a single byte Range, capped at 4 MiB by default. An explicit
max_range_bytes (64 KiB multiples, 64 KiB to 4 MiB) selects a diagnostic cap.
No redirects, proxy fallback, full-file prefetch, on-disk cache, or in-stream
retry is performed.
This is a diagnostic for fixed public samples, not a production URL gateway.
"""

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import os
import re
import resource
import secrets
import signal
import socket
import threading
import time
from urllib.parse import urlsplit

import httpx


CHUNK_BYTES = 64 * 1024
RANGE_BYTES = 4 * 1024 * 1024
SOURCE_PATH = re.compile(r"/object-storage/web_videos/[a-f0-9-]{36}-360\.mp4")
CONTENT_RANGE = re.compile(r"bytes (\d+)-(\d+)/(\d+)")
RANGE = re.compile(r"bytes=(\d*)-(\d*)")


class RelayError(ValueError):
    """Upstream does not satisfy this deliberately narrow relay contract."""


def child_observation(proc_root="/proc", pid=None):
    """Observe direct children; never turn unreadable process data into zero."""
    pid = os.getpid() if pid is None else pid
    try:
        task_root = os.path.join(proc_root, str(pid), "task")
        tasks = os.listdir(task_root)
        if str(pid) not in tasks:
            raise ValueError("main task not visible")
        children = []
        for task in tasks:
            task_path = os.path.join(task_root, task)
            try:
                with open(os.path.join(task_path, "children"), encoding="ascii") as file:
                    children.extend(map(int, file.read().split()))
            except FileNotFoundError:
                if os.path.isdir(task_path) or task == str(pid):
                    raise
        return {"child_pids": sorted(set(children)), "child_measurement": "task_children"}
    except (OSError, ValueError):
        pass
    # DSM kernels may omit task/<tid>/children. Process stat contains PPID,
    # and scanning the small container PID namespace does not spawn commands.
    try:
        processes = [entry for entry in os.listdir(proc_root) if entry.isdigit()]
        if str(pid) not in processes:
            raise ValueError("own process not visible")
        children = []
        for process in processes:
            process_path = os.path.join(proc_root, process)
            try:
                with open(os.path.join(process_path, "stat"), encoding="ascii") as file:
                    raw = file.read()
            except FileNotFoundError:
                if os.path.isdir(process_path) or process == str(pid):
                    raise
                continue  # A process exited after directory enumeration.
            prefix, separator, fields = raw.rpartition(")")
            # comm may contain spaces and ')' characters; use the last ')'.
            if not separator or int(prefix.split("(", 1)[0].strip()) != int(process):
                raise ValueError("invalid process stat")
            parts = fields.split()
            if len(parts) < 2 or len(parts[0]) != 1 or not parts[1].isdigit():
                raise ValueError("invalid process stat parent")
            if int(parts[1]) == pid:
                children.append(int(process))
        return {"child_pids": sorted(children), "child_measurement": "proc_stat"}
    except (OSError, ValueError):
        return {"child_measurement": "unavailable"}


def validate_config(config):
    """No caller-supplied host, URL path, or redirect is accepted at request time."""
    if not isinstance(config, dict):
        raise ValueError("configuration must be an object")
    token = config.get("token", "")
    if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", token):
        raise ValueError("token must contain 32-128 URL-safe characters")
    port = config.get("port", 0)
    if not isinstance(port, int) or isinstance(port, bool) or not 0 <= port <= 65535:
        raise ValueError("invalid port")
    bind_host = config.get("bind_host", "127.0.0.1")
    try:
        address = ipaddress.IPv4Address(bind_host)
    except (ipaddress.AddressValueError, TypeError) as error:
        raise ValueError("bind_host must be an explicit IPv4 address") from error
    if not (address.is_loopback or address in ipaddress.IPv4Network("100.64.0.0/10")):
        raise ValueError("bind_host must be loopback or the verified NAS Tailscale IPv4")
    ttl = config.get("ttl_seconds", 2700)
    if not isinstance(ttl, (int, float)) or isinstance(ttl, bool) or not 1 <= ttl <= 3600:
        raise ValueError("ttl_seconds must be between 1 and 3600")
    sources = config.get("sources")
    if not isinstance(sources, dict) or not 1 <= len(sources) <= 2:
        raise ValueError("one or two fixed public sources are required")
    for source_id, url in sources.items():
        if not isinstance(source_id, str) or not re.fullmatch(r"[a-z0-9_-]{1,32}", source_id):
            raise ValueError("invalid source id")
        if not isinstance(url, str):
            raise ValueError("invalid source URL")
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or parsed.netloc != "video.blender.org"
                or not SOURCE_PATH.fullmatch(parsed.path) or parsed.query or parsed.fragment):
            raise ValueError("only fixed Blender 360p public MP4 URLs are accepted")
    max_bytes = config.get("max_bytes_total", 256 * 1024 * 1024)
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or not 1 <= max_bytes <= 1024 ** 3:
        raise ValueError("max_bytes_total must be between 1 and 1073741824")
    max_range = config.get("max_range_bytes", RANGE_BYTES)
    if (not isinstance(max_range, int) or isinstance(max_range, bool)
            or not CHUNK_BYTES <= max_range <= RANGE_BYTES or max_range % CHUNK_BYTES):
        raise ValueError("max_range_bytes must be a 64 KiB multiple between 64 KiB and 4 MiB")
    return dict(config, token=token, port=port, ttl_seconds=float(ttl), sources=dict(sources),
                max_bytes_total=max_bytes, bind_host=str(address), max_range_bytes=max_range)


class Relay:
    def __init__(self, config, *, transport=None):
        self.config = validate_config(config)
        self.started = time.monotonic()
        self.lock = threading.Lock()
        self.slots = threading.BoundedSemaphore(2)
        self.stop = threading.Event()
        self.counts = dict(requests=0, active=0, peak_active=0, completed=0,
                           bytes_sent=0, upstream_bytes=0, upstream_errors=0,
                           client_disconnects=0, retries=0, rejected_busy=0,
                           budget_charged_bytes=0, rejected_budget=0)
        self.totals = {}
        self.client = httpx.Client(
            trust_env=False, follow_redirects=False, transport=transport,
            timeout=httpx.Timeout(connect=10, read=15, write=10, pool=1),
            limits=httpx.Limits(max_connections=2, max_keepalive_connections=2),
            headers={"Accept-Encoding": "identity", "User-Agent": "jzmedia-MP4-diagnostic-relay/1"},
        )

    def add(self, key, amount=1):
        with self.lock:
            self.counts[key] += amount

    def metrics(self):
        with self.lock:
            result = dict(self.counts)
        usage = resource.getrusage(resource.RUSAGE_SELF)
        result.update(uptime_seconds=round(time.monotonic() - self.started, 3),
                      cpu_seconds=round(usage.ru_utime + usage.ru_stime, 6),
                      max_rss_kib=usage.ru_maxrss, pid=os.getpid(),
                      media_buffer_limit_bytes=CHUNK_BYTES,
                      response_range_limit_bytes=self.config["max_range_bytes"], max_media_requests=2,
                      max_bytes_total=self.config["max_bytes_total"],
                      media_disk_writes_by_design=False, decode_processes_by_design=0)
        try:
            with open("/proc/self/status", encoding="ascii") as file:
                for line in file:
                    if line.startswith("VmRSS:"):
                        result["rss_kib"] = int(line.split()[1])
        except (OSError, ValueError):
            pass
        try:
            with open("/proc/self/io", encoding="ascii") as file:
                values = dict(line.strip().split(": ", 1) for line in file)
            result["process_read_bytes"] = int(values["read_bytes"])
            result["process_write_bytes"] = int(values["write_bytes"])
            result["process_rchar"] = int(values["rchar"])
            result["process_wchar"] = int(values["wchar"])
        except (OSError, ValueError, KeyError):
            pass
        try:
            with open("/proc/stat", encoding="ascii") as file:
                result["host_cpu_ticks"] = list(map(int, file.readline().split()[1:]))
        except (OSError, ValueError):
            pass
        try:
            with open("/proc/meminfo", encoding="ascii") as file:
                for line in file:
                    if line.startswith("MemAvailable:"):
                        result["host_mem_available_kib"] = int(line.split()[1])
        except (OSError, ValueError):
            pass
        result.update(child_observation())
        return result

    @contextmanager
    def response(self, method, source_id, headers=None):
        """Retry one failed connection/response before downstream headers only."""
        response = None
        for attempt in range(2):
            try:
                request = self.client.build_request(method, self.config["sources"][source_id],
                                                    headers=headers)
                response = self.client.send(request, stream=True)
                if response.status_code in {502, 503, 504} and attempt == 0:
                    response.close()
                    self.add("retries")
                    time.sleep(0.2)
                    continue
                break
            except httpx.TransportError:
                if attempt:
                    raise
                self.add("retries")
                time.sleep(0.2)
        try:
            yield response
        finally:
            if response is not None:
                response.close()

    def total(self, source_id):
        if source_id not in self.totals:
            with self.response("HEAD", source_id) as response:
                if (response.status_code != 200 or not response.headers.get("content-length", "").isdigit()
                        or response.headers.get("content-encoding", "identity") != "identity"):
                    raise RelayError("invalid upstream HEAD")
                total = int(response.headers["content-length"])
                if total <= 0:
                    raise RelayError("empty upstream media")
                self.totals[source_id] = total
        return self.totals[source_id]

    def span(self, header, source_id):
        match = RANGE.fullmatch(header or "")
        if not match or not any(match.groups()):
            raise ValueError("single byte Range required")
        start, end = match.groups()
        max_range = self.config["max_range_bytes"]
        if not start:
            suffix = int(end)
            if suffix <= 0:
                raise ValueError("invalid suffix")
            total = self.total(source_id)
            start = max(0, total - min(suffix, max_range))
            return start, total - 1
        start = int(start)
        requested_end = int(end) if end else start + max_range - 1
        if requested_end < start:
            raise ValueError("invalid byte range")
        return start, min(requested_end, start + max_range - 1)

    def close(self):
        self.client.close()


def make_server(relay):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def setup(self):
            super().setup()
            self.connection.settimeout(15)

        def log_message(self, *_args):
            pass  # Never log the secret path or public signed URLs.

        def headers_out(self, status, headers):
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, str(value))
            self.send_header("Connection", "close")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.close_connection = True

        def json_out(self, status, value, extra=None):
            body = json.dumps(value).encode()
            self.headers_out(status, {"Content-Type": "application/json", "Content-Length": len(body),
                                      **(extra or {})})
            if self.command != "HEAD":
                self.wfile.write(body)

        def do_HEAD(self):
            self.do_GET()

        def do_POST(self):
            expected = f"/{relay.config['token']}/shutdown"
            if not secrets.compare_digest(self.path, expected):
                self.json_out(404, {"error": "not found"})
                return
            self.json_out(200, {"stopping": True})
            relay.stop.set()

        def do_GET(self):
            # Queries are intentionally rejected; requests cannot change targets/policy.
            parts = self.path.split("/")
            if len(parts) < 3 or not secrets.compare_digest(parts[1], relay.config["token"]):
                self.json_out(404, {"error": "not found"})
                return
            if parts[2:] in (["health"], ["metrics"]):
                self.json_out(200, {"ok": True, **relay.metrics()})
                return
            if len(parts) != 4 or parts[2] != "media" or parts[3] not in relay.config["sources"]:
                self.json_out(404, {"error": "not found"})
                return
            if not relay.slots.acquire(blocking=False):
                relay.add("rejected_busy")
                self.json_out(429, {"error": "two media requests already active"}, {"Retry-After": "1"})
                return
            with relay.lock:
                relay.counts["requests"] += 1
                relay.counts["active"] += 1
                relay.counts["peak_active"] = max(relay.counts["peak_active"], relay.counts["active"])
            try:
                self.media(parts[3])
            except (BrokenPipeError, ConnectionResetError, TimeoutError, socket.timeout):
                relay.add("client_disconnects")
                self.close_connection = True
            finally:
                relay.add("active", -1)
                relay.slots.release()

        def media(self, source_id):
            sent_headers = False
            try:
                range_header = self.headers.get("Range")
                if self.command == "HEAD" and not range_header:
                    total = relay.total(source_id)
                    self.headers_out(200, {"Content-Type": "video/mp4", "Content-Length": total,
                                           "Accept-Ranges": "bytes"})
                    return
                try:
                    start, end = relay.span(range_header, source_id)
                except ValueError:
                    self.json_out(400, {"error": "one valid byte Range is required"})
                    return
                method = "HEAD" if self.command == "HEAD" else "GET"
                if method == "GET":
                    with relay.lock:
                        remaining = relay.config["max_bytes_total"] - relay.counts["budget_charged_bytes"]
                        if remaining > 0:
                            end = min(end, start + remaining - 1)
                            # Charge the requested range even if the browser cancels it.
                            relay.counts["budget_charged_bytes"] += end - start + 1
                    if remaining <= 0:
                        relay.add("rejected_budget")
                        self.json_out(503, {"error": "diagnostic byte budget exhausted"})
                        return
                with relay.response(method, source_id, {"Range": f"bytes={start}-{end}"}) as response:
                    if response.status_code == 416:
                        content_range = response.headers.get("content-range", "")
                        if not re.fullmatch(r"bytes \*/\d+", content_range):
                            raise RelayError("invalid upstream 416")
                        self.headers_out(416, {"Content-Range": content_range, "Content-Length": 0})
                        return
                    match = CONTENT_RANGE.fullmatch(response.headers.get("content-range", ""))
                    if response.status_code != 206 or not match:
                        raise RelayError("upstream ignored range or redirected")
                    actual_start, actual_end, total = map(int, match.groups())
                    length = actual_end - actual_start + 1
                    if (actual_start != start or actual_end != min(end, total - 1) or length <= 0
                            or length > relay.config["max_range_bytes"] or total <= actual_end
                            or response.headers.get("content-length") != str(length)
                            or response.headers.get("content-encoding", "identity") != "identity"):
                        raise RelayError("inconsistent upstream range")
                    relay.totals[source_id] = total
                    self.headers_out(206, {"Content-Type": "video/mp4", "Content-Length": length,
                                           "Content-Range": f"bytes {start}-{actual_end}/{total}",
                                           "Accept-Ranges": "bytes"})
                    sent_headers = True
                    if self.command == "HEAD":
                        return
                    copied = 0
                    for chunk in response.iter_raw(chunk_size=CHUNK_BYTES):
                        relay.add("upstream_bytes", len(chunk))
                        if copied + len(chunk) > length:
                            raise RelayError("upstream body exceeded declared range")
                        self.wfile.write(chunk)
                        copied += len(chunk)
                        relay.add("bytes_sent", len(chunk))
                    if copied != length:
                        raise RelayError("truncated upstream range")
                    relay.add("completed")
            except (httpx.HTTPError, RelayError):
                relay.add("upstream_errors")
                if not sent_headers:
                    self.json_out(502, {"error": "upstream range unavailable"})
                self.close_connection = True  # Content-Length exposes any interrupted body.

    server = ThreadingHTTPServer((relay.config["bind_host"], relay.config["port"]), Handler)
    server.daemon_threads = True
    return server


def main():
    try:
        config = json.loads(os.environ.get("ONLINE_MP4_RELAY_CONFIG", "{}"))
        relay = Relay(config)
    except (ValueError, TypeError):
        print(json.dumps({"event": "config_error"}), flush=True)
        return 2
    server = make_server(relay)
    def shutdown(*_args):
        relay.stop.set()

    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, shutdown)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
    thread.start()
    print(json.dumps({"event": "ready", "port": server.server_port, "pid": os.getpid(),
                      "ttl_seconds": relay.config["ttl_seconds"]}), flush=True)
    try:
        relay.stop.wait(relay.config["ttl_seconds"])
    finally:
        server.shutdown()
        server.server_close()
        relay.close()
        thread.join(timeout=5)
        print(json.dumps({"event": "stopped", **relay.metrics()}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
