"""Synthetic, loopback-only MacCMS origin used by the P0 experiment."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import subprocess
import threading
from urllib.parse import parse_qs, unquote, urlsplit

from scripts.preview_onboarding import installed_binary


DURATION = 24
EPISODES = (("ep-1", "第 1 集 · 蓝色测试片"), ("ep-2", "第 2 集 · 绿色测试片"))


def generate_media(root: Path) -> None:
    """Generate only under a new P0 directory; no database, .env, or NAS access."""
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    marker = root / ".online-p0-fixture"
    if marker.exists():
        return
    if any(root.iterdir()):
        raise ValueError("fixture root must be empty or an existing P0 fixture")
    ffmpeg = str(installed_binary("ffmpeg"))
    font = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    for number, (episode, _title) in enumerate(EPISODES, 1):
        folder = root / episode
        variants = folder / "hls" / "variants"
        variants.mkdir(parents=True)
        color = "blue" if number == 1 else "green"
        filter_text = f"drawbox=x=0:y=0:w=iw:h=44:color={color}:t=fill"
        if font.exists():
            filter_text += (f",drawtext=fontfile={font}:text='P0 EPISODE {number}'"
                            ":x=12:y=10:fontsize=22:fontcolor=white")
        subprocess.run([
            ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=480x270:rate=12",
            "-f", "lavfi", "-i", f"sine=frequency={330 + number * 110}:sample_rate=48000",
            "-t", str(DURATION), "-vf", filter_text, "-c:v", "libx264", "-threads", "1",
            "-preset", "ultrafast", "-crf", "28", "-pix_fmt", "yuv420p", "-g", "24",
            "-keyint_min", "24", "-sc_threshold", "0", "-c:a", "aac", "-b:a", "64k",
            "-movflags", "+faststart", str(folder / "movie.mp4"),
        ], check=True, timeout=60)
        subprocess.run([
            ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-i", str(folder / "movie.mp4"), "-c", "copy", "-f", "hls", "-hls_time", "4",
            "-hls_playlist_type", "vod", "-hls_list_size", "0", "-hls_segment_type", "fmp4",
            "-hls_fmp4_init_filename", "../init.mp4", "-hls_segment_filename", "part-%03d.m4s",
            "low.m3u8",
        ], cwd=variants, check=True, timeout=30)
        (folder / "hls" / "master.m3u8").write_text(
            '#EXTM3U\n#EXT-X-VERSION:7\n#EXT-X-STREAM-INF:BANDWIDTH=900000,RESOLUTION=480x270,'
            'CODECS="avc1.42c015,mp4a.40.2"\nvariants/low.m3u8\n', encoding="utf-8")
    marker.write_text("synthetic P0 fixture v1\n", encoding="utf-8")


def byte_range(value: str | None, size: int) -> tuple[int, int, int]:
    """Return inclusive bounds and status; reject unsupported multipart/invalid ranges."""
    if value is None:
        return 0, size - 1, 200
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", value)
    if not match or not any(match.groups()) or size <= 0:
        raise ValueError("invalid range")
    first, last = match.groups()
    if not first:
        count = int(last)
        if count <= 0:
            raise ValueError("empty suffix")
        return max(0, size - count), size - 1, 206
    start = int(first)
    end = min(int(last), size - 1) if last else size - 1
    if start >= size or start > end:
        raise ValueError("range outside file")
    return start, end, 206


class FixtureOrigin:
    """Fixed local source, intentionally not a generic file server or proxy."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.assets = {path.relative_to(self.root).as_posix()
                       for path in self.root.rglob("*") if path.is_file() and not path.is_symlink()
                       and path.suffix in {".mp4", ".m3u8", ".m4s", ".vtt"}}
        self.calls: list[dict] = []
        self.redirects: dict[str, str] = {}
        self.override_json: dict | None = None
        origin = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                self.respond(False)

            def do_HEAD(self):
                self.respond(True)

            def respond(self, head: bool):
                parsed = urlsplit(self.path)
                origin.calls.append({"method": self.command, "path": parsed.path,
                                     "range": self.headers.get("Range")})
                del origin.calls[:-2000]
                if parsed.path in origin.redirects:
                    self.send_response(302)
                    self.send_header("Location", origin.redirects[parsed.path])
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if parsed.path == "/api.php/provide/vod":
                    query = parse_qs(parsed.query)
                    payload = origin.override_json if origin.override_json is not None else origin.catalog(query)
                    data = json.dumps(payload, ensure_ascii=False).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    if not head:
                        self.wfile.write(data)
                    return
                path = unquote(parsed.path.removeprefix("/files/"))
                if not parsed.path.startswith("/files/") or path not in origin.assets:
                    self.send_error(404)
                    return
                file = origin.root / path
                if file.is_symlink() or not file.resolve().is_relative_to(origin.root):
                    self.send_error(404)
                    return
                size = file.stat().st_size
                try:
                    first, last, status = byte_range(self.headers.get("Range"), size)
                except ValueError:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                content_types = {".mp4": "video/mp4", ".m4s": "video/iso.segment",
                                 ".m3u8": "application/vnd.apple.mpegurl", ".vtt": "text/vtt"}
                self.send_response(status)
                self.send_header("Content-Type", content_types[file.suffix])
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(last - first + 1))
                if status == 206:
                    self.send_header("Content-Range", f"bytes {first}-{last}/{size}")
                self.end_headers()
                if head:
                    return
                try:
                    with file.open("rb") as handle:
                        handle.seek(first)
                        left = last - first + 1
                        while left:
                            chunk = handle.read(min(left, 65536))
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                            left -= len(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def catalog(self, query: dict) -> dict:
        rows = []
        keyword = query.get("wd", [""])[0].casefold()
        matches = not keyword or keyword in "在线点播验证 p0 合成 剧集".casefold()
        if matches and query.get("ids", ["1"])[0] == "1":
            urls = []
            for filename in ("movie.mp4", "hls/master.m3u8"):
                urls.append("#".join(f"{title}${self.base_url}/files/{episode}/{filename}"
                                     for episode, title in EPISODES))
            rows.append({"vod_id": 1, "vod_name": "在线点播验证", "type_name": "合成剧集",
                         "vod_content": "仅用于验证在线接口的本地合成片源。",
                         "vod_play_from": "mp4$$$hls", "vod_play_url": "$$$".join(urls)})
        return {"code": 1, "page": 1, "pagecount": 1, "total": len(rows), "list": rows}

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
