"""Synthetic MacCMS Provide JSON and immutable media on one fixed loopback origin."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time
from urllib.parse import parse_qs, unquote, urlsplit

from scripts.online_p0.fixtures import byte_range, generate_media


MARKER = ".online-p2-fixture"
MARKER_TEXT = "jzmedia online P2 isolated fixture v1\n"
DURATION = 24.0
MODES = {"normal", "malformed", "xml", "status_error", "duplicate_labels", "mismatched_lines"}


def fixture_root(value, *, require_media=False):
    root = Path(value).resolve()
    if not root.is_relative_to(Path("/tmp")) or not root.name.startswith("jzmedia-online-p2-"):
        raise ValueError("root must be /tmp/jzmedia-online-p2-*")
    if require_media:
        marker = root / MARKER
        if not marker.is_file() or marker.is_symlink() or marker.read_text() != MARKER_TEXT:
            raise ValueError("generate the marked P2 fixture before starting its server")
        for episode in ("ep-1", "ep-2"):
            for relative in ("movie.mp4", "hls/master.m3u8"):
                asset = root / "media" / episode / relative
                if not asset.is_file() or asset.is_symlink() or not asset.resolve().is_relative_to(root / "media"):
                    raise ValueError("synthetic fixture media is incomplete")
    return root


def prepare_media(value):
    root = fixture_root(value)
    root.mkdir(parents=True, exist_ok=True)
    marker = root / MARKER
    if marker.exists() and (marker.is_symlink() or marker.read_text() != MARKER_TEXT):
        raise ValueError("unrecognized fixture marker")
    generate_media(root / "media")
    marker.write_text(MARKER_TEXT)
    return root


class FixtureOrigin:
    """Read-only media snapshot and fixed generated catalogue, with bounded scenarios."""

    def __init__(self, root):
        self.root = Path(root).resolve()
        self.assets = {path.relative_to(self.root).as_posix() for path in self.root.rglob("*")
                       if path.is_file() and not path.is_symlink()
                       and path.suffix in {".mp4", ".m3u8", ".m4s", ".vtt", ".ts"}}
        self.calls, self.lock = [], threading.Lock()
        self.next_plan = (0, "normal")
        self.pending_requests = 0
        self.revision = 1
        self.reverse_lines = False
        origin = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def setup(self):
                super().setup()
                self.connection.settimeout(2)

            def log_message(self, *_args):
                pass

            def handle_one_request(self):
                try:
                    super().handle_one_request()
                except OSError:
                    self.close_connection = True

            def do_GET(self):
                self.respond(False)

            def do_HEAD(self):
                self.respond(True)

            def respond(self, head):
                parsed = urlsplit(self.path)
                query = parse_qs(parsed.query)
                api = parsed.path == "/provide/vod"
                cookie = self.headers.get("Cookie", "")
                with origin.lock:
                    origin.calls.append({"method": self.command, "path": parsed.path,
                                         "kind": "api" if api else "media", "action": query.get("ac", [""])[0],
                                         "range": self.headers.get("Range"),
                                         "app_credentials": bool(any(name in self.headers for name in (
                                             "X-Api-Token", "X-Viewing-Session", "X-Receiver-Token"))
                                             or "jz_viewing=" in cookie)})
                    del origin.calls[:-2000]
                if api:
                    self.catalogue(query, head)
                    return
                signed = query.get("fixture_signature", [None])[0]
                with origin.lock:
                    revision = origin.revision
                if signed is not None and signed != f"server-only-{revision}":
                    self.send_body(403, b"expired isolated signature", "text/plain", head)
                    return
                relative = unquote(parsed.path.removeprefix("/files/"))
                asset = origin.root / relative
                if (not parsed.path.startswith("/files/") or relative not in origin.assets
                        or asset.is_symlink() or not asset.resolve().is_relative_to(origin.root)):
                    self.send_body(404, b"", "application/octet-stream", head)
                    return
                size = asset.stat().st_size
                try:
                    start, end, status = byte_range(self.headers.get("Range"), size)
                except ValueError:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                types = {".mp4": "video/mp4", ".m4s": "video/iso.segment", ".ts": "video/mp2t",
                         ".m3u8": "application/vnd.apple.mpegurl", ".vtt": "text/vtt"}
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", types[asset.suffix])
                    self.send_header("Content-Length", str(end - start + 1))
                    self.send_header("Accept-Ranges", "bytes")
                    if status == 206:
                        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                    self.end_headers()
                    if not head:
                        with asset.open("rb") as media:
                            media.seek(start)
                            remaining = end - start + 1
                            while remaining:
                                chunk = media.read(min(remaining, 65536))
                                if not chunk:
                                    break
                                self.wfile.write(chunk)
                                remaining -= len(chunk)
                except OSError:
                    self.close_connection = True

            def send_body(self, status, body, mime, head=False):
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", mime)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    if not head:
                        self.wfile.write(body)
                except OSError:
                    self.close_connection = True

            def catalogue(self, query, head):
                with origin.lock:
                    delay_ms, mode = origin.next_plan
                    origin.next_plan = (0, "normal")
                    origin.pending_requests += 1
                try:
                    if delay_ms:
                        time.sleep(delay_ms / 1000)
                    if mode == "malformed":
                        self.send_body(200, b'{"code":1,"list":[', "application/json", head)
                        return
                    if mode == "xml":
                        self.send_body(200, b"<rss><list/></rss>", "application/xml", head)
                        return
                    if mode == "status_error":
                        self.send_body(503, b"isolated upstream failure", "text/plain", head)
                        return
                    rows = origin.records(mode)
                    wanted = query.get("ids", [None])[0]
                    if wanted is not None:
                        rows = [row for row in rows if row["vod_id"] == wanted]
                    else:
                        keyword = query.get("wd", [""])[0].strip().casefold()
                        if keyword == "none":
                            rows = []
                        elif keyword not in {"", "all", "合成"}:
                            rows = [row for row in rows if keyword in row["vod_name"].casefold()]
                    page = int(query.get("pg", ["1"])[0])
                    payload = {"code": 1, "msg": "隔离数据", "page": page, "pagecount": 1,
                               "limit": "20", "total": len(rows), "list": rows if page == 1 else []}
                    self.send_body(200, json.dumps(payload, ensure_ascii=False).encode(), "application/json; charset=utf-8", head)
                finally:
                    with origin.lock:
                        origin.pending_requests -= 1

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_port
        self.base_url = f"http://fixture.invalid:{self.port}"
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .05}, daemon=True)

    def records(self, mode="normal"):
        with self.lock:
            revision, reverse = self.revision, self.reverse_lines
        def media(episode, transport):
            asset = "movie.mp4" if transport == "mp4" else "hls/master.m3u8"
            return f"{self.base_url}/files/ep-{episode}/{asset}?fixture_signature=server-only-{revision}"
        def lines(captions):
            groups = [("MP4直链", "#".join(f"{caption}${media(number, 'mp4')}" for number, caption in enumerate(captions, 1))),
                      ("HLS直链", "#".join(f"{caption}${media(number, 'hls')}" for number, caption in enumerate(captions, 1)))]
            if reverse:
                groups.reverse()
            return "$$$".join(row[0] for row in groups), "$$$".join(row[1] for row in groups)
        movie_from, movie_url = lines(["正片"])
        show_from, show_url = lines(["第1集", "第2集"])
        if mode == "duplicate_labels":
            show_url = show_url.replace("第2集$", "第1集$")
        if mode == "mismatched_lines":
            show_from += "$$$错位线路"
        return [
            {"vod_id": "movie-1", "vod_name": "合成电影", "type_name": "电影", "vod_year": "2026",
             "vod_content": '<p>开发机生成的24秒样片。</p><img src=x onerror="window.__fixtureInjected=true">',
             "vod_play_from": movie_from, "vod_play_url": movie_url},
            {"vod_id": "show-1", "vod_name": "合成剧集", "type_name": "国产剧", "vod_year": "2026",
             "vod_content": "两集、两条线路，各集有独立的合成画面。", "vod_total": 2,
             "vod_play_from": show_from, "vod_play_url": show_url},
            {"vod_id": "unsupported-1", "vod_name": "合成网页来源", "type_name": "电影", "vod_year": "2026",
             "vod_content": "只提供网页，不可作为媒体播放。", "vod_play_from": "网页线路",
             "vod_play_url": f"正片${self.base_url}/watch/movie"},
        ]

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
