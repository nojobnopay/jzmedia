"""Developer-generated sample media and a fixed, read-only loopback origin."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
from urllib.parse import unquote, urlsplit

from scripts.online_fixture.media import byte_range, generate_media, validate_fixture_root


MARKER = ".online-player-fixture"
MARKER_TEXT = "jzmedia online player isolated fixture v1\n"
DURATION = 24.0


ROOT_PREFIX = "jzmedia-online-player-"


def fixture_root(value, *, require_media=False):
    return validate_fixture_root(value, prefix=ROOT_PREFIX, marker=MARKER,
                                 marker_text=MARKER_TEXT, require_media=require_media)


def prepare_media(value):
    """Explicit development-only generation; serving never runs FFmpeg."""
    root = fixture_root(value)
    root.mkdir(parents=True, exist_ok=True)
    marker = root / MARKER
    if marker.exists() and (marker.is_symlink() or marker.read_text() != MARKER_TEXT):
        raise ValueError("unrecognized fixture marker")
    generate_media(root / "media")
    extras = root / "media" / "scenarios"
    extras.mkdir(exist_ok=True)
    (extras / "unsupported.mp4").write_bytes(b"isolated unsupported video payload\n")
    (extras / "encrypted.m3u8").write_text(
        '#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXT-X-KEY:METHOD=AES-128,URI="key.bin"\n'
        '#EXTINF:4,\nsecret-segment.ts\n#EXT-X-ENDLIST\n', encoding="utf-8")
    marker.write_text(MARKER_TEXT)
    return root


class FixtureOrigin:
    """Serve only an initial snapshot of generated assets, never arbitrary files."""

    def __init__(self, root):
        self.root = Path(root).resolve()
        self.assets = {path.relative_to(self.root).as_posix() for path in self.root.rglob("*")
                       if path.is_file() and not path.is_symlink()
                       and path.suffix in {".mp4", ".m3u8", ".m4s", ".vtt", ".ts"}}
        self.calls = []
        self.lock = threading.Lock()
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
                cookie = self.headers.get("Cookie", "")
                with origin.lock:
                    origin.calls.append({"method": self.command, "path": parsed.path,
                                         "range": self.headers.get("Range"),
                                         "app_credentials": bool(any(name in self.headers for name in (
                                             "X-Api-Token", "X-Viewing-Session", "X-Receiver-Token"))
                                             or "jz_viewing=" in cookie)})
                    del origin.calls[:-2000]
                relative = unquote(parsed.path.removeprefix("/files/"))
                asset = origin.root / relative
                if (not parsed.path.startswith("/files/") or relative not in origin.assets
                        or asset.is_symlink() or not asset.resolve().is_relative_to(origin.root)):
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
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

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_port
        self.base_url = f"http://fixture.invalid:{self.port}"
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .05}, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
