"""Synthetic MacCMS Provide JSON and immutable media on one fixed loopback origin."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
from pathlib import Path
import subprocess
import threading
import time
from urllib.parse import parse_qs, unquote, urlsplit

from scripts.online_p0.fixtures import byte_range, generate_media
from scripts.preview_onboarding import installed_binary


MARKER = ".online-p2-fixture"
MARKER_TEXT = "jzmedia online P2 isolated fixture v1\n"
DURATION = 24.0
RANGE_DURATION = 90.0
RANGE_MARKER = ".online-p2-range-fixture.json"
MODES = {"normal", "malformed", "xml", "status_error", "duplicate_labels", "mismatched_lines"}
TAXONOMY_CATEGORIES = (
    (20, 0, "电影片"), (21, 20, "动作片"), (22, 20, "喜剧片"), (23, 20, "恐怖片"),
    (24, 20, "科幻片"), (25, 20, "爱情片"), (26, 20, "剧情片"), (27, 20, "战争片"),
    (28, 20, "纪录片"), (29, 20, "理论片"), (30, 0, "连续剧"), (31, 30, "国产剧"),
    (32, 30, "欧美剧"), (33, 30, "香港剧"), (34, 30, "韩国剧"), (35, 30, "台湾剧"),
    (36, 30, "日本剧"), (37, 30, "海外剧"), (38, 30, "泰国剧"), (39, 0, "动漫片"),
    (40, 39, "国产动漫"), (41, 39, "日韩动漫"), (42, 39, "欧美动漫"), (43, 39, "港台动漫"),
    (44, 39, "海外动漫"), (45, 0, "综艺片"), (46, 45, "大陆综艺"), (47, 45, "港台综艺"),
    (48, 45, "日韩综艺"), (49, 45, "欧美综艺"), (50, 20, "动画片"), (51, 0, "电影解说"),
    (52, 0, "预告片"), (53, 0, "体育赛事"), (54, 53, "足球"), (55, 53, "篮球"),
    (56, 53, "网球"), (57, 53, "斯诺克"), (58, 0, "短剧大全"), (65, 58, "重生民国"),
    (66, 58, "穿越年代"), (67, 58, "现代言情"), (68, 58, "反转爽文"), (69, 58, "女恋总裁"),
    (70, 58, "闪婚离婚"), (71, 58, "都市脑洞"), (72, 58, "古装仙侠"), (73, 0, "福利"),
    (74, 58, "AI漫剧"),
)


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
    poster = root / "media" / "poster.png"
    if poster.is_symlink():
        raise ValueError("synthetic poster must not be a symlink")
    if not poster.is_file():
        # Generation is a development-only prepare step. The read-only origin
        # and application never invoke a decoder while serving the fixture.
        subprocess.run([
            str(installed_binary("ffmpeg")), "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-threads", "1", "-ss", "1", "-i", str(root / "media" / "ep-1" / "movie.mp4"),
            "-vf", "scale=240:135", "-frames:v", "1", "-c:v", "png", "-threads", "1",
            "-update", "1", str(poster),
        ], check=True, timeout=15)
    marker.write_text(MARKER_TEXT)
    return root


def prepare_range_media(value):
    """Development-only long MP4 generation; finish before starting the server."""
    root = prepare_media(value)
    target = root / "media" / "ep-1" / "movie.mp4"
    subprocess.run([
        str(installed_binary("ffmpeg")), "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=480x270:rate=24",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
        "-t", str(RANGE_DURATION), "-c:v", "libx264", "-threads", "1", "-preset", "ultrafast",
        "-b:v", "880k", "-maxrate", "900k", "-bufsize", "900k", "-pix_fmt", "yuv420p",
        "-g", "48", "-keyint_min", "48", "-sc_threshold", "0",
        "-c:a", "aac", "-b:a", "64k", "-movflags", "+faststart", str(target),
    ], check=True, timeout=60)
    size = target.stat().st_size
    if not 9 * 1024**2 < size < 14 * 1024**2:
        raise ValueError("range fixture must contain real encoded media larger than 9 MiB and smaller than 14 MiB")
    probe = subprocess.run([
        str(installed_binary("ffprobe")), "-v", "error", "-select_streams", "v:0",
        "-show_entries", "packet=pts_time,pos", "-of", "json", str(target),
    ], capture_output=True, text=True, check=True, timeout=15)
    packets = json.loads(probe.stdout)["packets"]
    boundary_time = next(float(packet["pts_time"]) for packet in packets if int(packet["pos"]) >= 8 * 1024**2)
    natural_target = max(82., boundary_time + 2.)
    if natural_target >= RANGE_DURATION - 2:
        raise ValueError("range fixture needs enough decoded media after the old 8 MiB boundary")
    info = {"file": "ep-1/movie.mp4", "bytes": size, "duration_seconds": RANGE_DURATION,
            "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            "average_bits_per_second": round(size * 8 / RANGE_DURATION),
            "old_range_boundary_bytes": 8 * 1024**2, "boundary_video_time_seconds": boundary_time,
            "natural_target_seconds": natural_target}
    (root / RANGE_MARKER).write_text(json.dumps(info, indent=2) + "\n")
    return root


class FixtureOrigin:
    """Read-only media snapshot and fixed generated catalogue, with bounded scenarios."""

    def __init__(self, root, *, throttle_bytes_per_second=0):
        if type(throttle_bytes_per_second) is not int or not 0 <= throttle_bytes_per_second <= 2 * 1024**2:
            raise ValueError("invalid fixture throughput limit")
        self.root = Path(root).resolve()
        self.throttle_bytes_per_second = throttle_bytes_per_second
        self.stop = threading.Event()
        self._pace_lock = threading.Lock()
        self._next_byte_at = time.monotonic()
        self.assets = {path.relative_to(self.root).as_posix() for path in self.root.rglob("*")
                       if path.is_file() and not path.is_symlink()
                       and path.suffix in {".mp4", ".m3u8", ".m4s", ".vtt", ".ts", ".png"}}
        self.calls, self.lock = [], threading.Lock()
        self.next_plan = (0, "normal")
        self.pending_requests = 0
        self.revision = 1
        self.reverse_lines = False
        self.media_alias = False
        self.gallery = False
        self.taxonomy = False
        self.related = False
        self.related_delay_ms = 0
        self.related_failure = False
        self.image_delay_ms = 0
        self.image_active = 0
        self.image_peak = 0
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
                    call = {"method": self.command, "path": parsed.path,
                            "host": self.headers.get("Host", "").split(":", 1)[0],
                            "kind": "api" if api else "image" if parsed.path.endswith(".png") else "media",
                            "action": query.get("ac", [""])[0],
                            "category_id": query.get("t", [""])[0] if api else "",
                            "year": query.get("year", [""])[0] if api else "",
                            "page": query.get("pg", [""])[0] if api else "",
                            "external_id": query.get("ids", [""])[0] if api else "",
                            "keyword": query.get("wd", [""])[0] if api else "",
                            "range": self.headers.get("Range"),
                            "app_credentials": bool(any(name in self.headers for name in (
                                "X-Api-Token", "X-Viewing-Session", "X-Receiver-Token"))
                                or "jz_viewing=" in cookie)}
                    origin.calls.append(call)
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
                         ".m3u8": "application/vnd.apple.mpegurl", ".vtt": "text/vtt", ".png": "image/png"}
                with origin.lock:
                    call.update({"start": start, "end": end, "total": size, "bytes_sent": 0,
                                 "complete": False, "status": status})
                    if not head:
                        origin.pending_requests += 1
                        if asset.suffix == ".png":
                            origin.image_active += 1
                            origin.image_peak = max(origin.image_peak, origin.image_active)
                    image_delay_ms = origin.image_delay_ms if asset.suffix == ".png" else 0
                digest = hashlib.sha256()
                try:
                    if image_delay_ms and origin.stop.wait(image_delay_ms / 1000):
                        return
                    self.send_response(status)
                    self.send_header("Content-Type", types[asset.suffix])
                    self.send_header("Content-Length", str(end - start + 1))
                    self.send_header("Accept-Ranges", "bytes")
                    self.send_header("ETag", f'"fixture-{size}"')
                    if status == 206:
                        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                    self.end_headers()
                    if not head:
                        with asset.open("rb") as media:
                            media.seek(start)
                            remaining = end - start + 1
                            while remaining:
                                chunk = media.read(min(remaining, 32768))
                                if not chunk:
                                    break
                                if not origin.wait_for_bytes(len(chunk)):
                                    return
                                self.wfile.write(chunk)
                                self.wfile.flush()
                                digest.update(chunk)
                                remaining -= len(chunk)
                                with origin.lock:
                                    call["bytes_sent"] += len(chunk)
                            with origin.lock:
                                call["complete"] = remaining == 0
                except OSError:
                    self.close_connection = True
                finally:
                    with origin.lock:
                        call["sha256"] = digest.hexdigest()
                        if not head:
                            origin.pending_requests -= 1
                            if asset.suffix == ".png":
                                origin.image_active -= 1

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
                    # Only synthetic recommendation root searches are slow.
                    # The selected item detail and playback resolver remain free
                    # to prove that foreground work can preempt a live socket.
                    root_search = (origin.related and query.get("wd", [""])[0] in
                                   {"青山书院", "重启之路", "回声"} and "ids" not in query)
                    if root_search:
                        delay_ms = max(delay_ms, origin.related_delay_ms)
                    if origin.related_failure and origin.related and "ids" not in query and (
                            root_search or query.get("t", [""])[0]):
                        mode = "status_error"
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
                    category = query.get("t", [""])[0]
                    if category:
                        rows = [row for row in rows if str(row["type_id"]) == category]
                    with origin.lock:
                        taxonomy = origin.taxonomy or origin.related
                    year = query.get("year", [""])[0]
                    if taxonomy and year:
                        rows = [row for row in rows if row.get("vod_year") == year]
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
                    with origin.lock:
                        paginated = (origin.gallery or taxonomy) and wanted is None
                    selected = rows[(page - 1) * 20:page * 20] if paginated else rows if page == 1 else []
                    payload = {"code": 1, "msg": "隔离数据", "page": page,
                               "pagecount": max(1, (len(rows) + 19) // 20) if paginated else 1,
                               "limit": "20", "total": len(rows), "list": selected,
                               "class": ([{"type_id": identity, "type_pid": parent, "type_name": name}
                                          for identity, parent, name in TAXONOMY_CATEGORIES] if taxonomy else
                                         [{"type_id": 1, "type_name": "电影"}, {"type_id": 2, "type_name": "剧集"}])}
                    self.send_body(200, json.dumps(payload, ensure_ascii=False).encode(), "application/json; charset=utf-8", head)
                finally:
                    with origin.lock:
                        origin.pending_requests -= 1

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_port
        self.base_url = f"http://fixture.invalid:{self.port}"
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .05}, daemon=True)

    def wait_for_bytes(self, count):
        """Aggregate source-side pacing, including concurrent browser probes."""
        if not self.throttle_bytes_per_second:
            return not self.stop.is_set()
        with self._pace_lock:
            self._next_byte_at = max(time.monotonic(), self._next_byte_at) + count / self.throttle_bytes_per_second
            delay = max(0., self._next_byte_at - time.monotonic())
        return not self.stop.wait(delay)

    def records(self, mode="normal"):
        with self.lock:
            revision, reverse, alias, gallery, taxonomy, related = (self.revision, self.reverse_lines, self.media_alias,
                                                                   self.gallery, self.taxonomy, self.related)
        def media(episode, transport):
            asset = "movie.mp4" if transport == "mp4" else "hls/master.m3u8"
            media_base = f"http://media.fixture.invalid:{self.port}" if alias else self.base_url
            return f"{media_base}/files/ep-{episode}/{asset}?fixture_signature=server-only-{revision}"
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
        if related:
            def recommendation_record(identity, title, category, year, *, actors="合成演员甲,合成演员乙"):
                parent, category_name = next((parent, name) for selected, parent, name in TAXONOMY_CATEGORIES
                                             if selected == category)
                movie = "电影" in category_name or category_name.endswith("片")
                return {"vod_id": identity, "vod_name": title, "type_id": str(category), "type_id_1": parent,
                        "type_name": category_name, "vod_year": year,
                        "vod_pic": f"{self.base_url}/files/poster.png?related={identity}",
                        "vod_remarks": "正片" if movie else "全2集", "vod_class": "剧情,校园", "vod_area": "大陆", "vod_lang": "国语",
                        "vod_actor": actors, "vod_director": "合成导演", "vod_total": 1 if movie else 2,
                        "vod_content": "纯合成系列与同类推荐隔离用例，不对应真实影视作品。",
                        "vod_play_from": movie_from if movie else show_from,
                        "vod_play_url": movie_url if movie else show_url}

            # Twenty-four fillers place the selected second season on page 2
            # for the exact category/year filter; seasons span different years.
            return [recommendation_record(f"peer-{number}", f"同类合成剧 {number:02d}", 31, "2024")
                    for number in range(1, 25)] + [
                recommendation_record("academy-s4", "青山书院第四季", 31, "2026"),
                recommendation_record("academy-s1", "青山书院第一季", 31, "2023"),
                recommendation_record("academy-s3", "青山书院第三季", 31, "2025", actors="合成演员丙"),
                recommendation_record("academy-s2", "青山书院第二季", 31, "2024"),
                recommendation_record("academy-unseasoned", "青山书院", 31, "2024"),
                recommendation_record("academy-s2-movie", "青山书院第二季", 22, "2024"),
                recommendation_record("academy-film", "青山书院外传", 22, "2020"),
                recommendation_record("restart-s2", "重启之路第二季", 31, "2026"),
                recommendation_record("echo-s1", "回声第一季", 31, "2025", actors="合成演员戊"),
                recommendation_record("echo-s2", "回声第二季", 31, "2026", actors="合成演员丁"),
                recommendation_record("lonely-trailer", "独行预告", 52, "2026"),
            ]
        if taxonomy:
            def record(identity, title, category, year):
                parent, category_name = next((parent, name) for selected, parent, name in TAXONOMY_CATEGORIES
                                             if selected == category)
                return {"vod_id": identity, "vod_name": title, "type_id": str(category), "type_id_1": parent,
                        "type_name": category_name, "vod_year": year,
                        "vod_pic": f"{self.base_url}/files/poster.png?taxonomy={identity}",
                        "vod_remarks": "更新至第2集", "vod_class": "都市,喜剧", "vod_area": "大陆",
                        "vod_lang": "国语", "vod_actor": "合成演员甲,合成演员乙", "vod_director": "合成导演",
                        "vod_sub": "合成分类别名", "vod_total": 2, "vod_isend": 0,
                        "vod_content": "分类、年份与元资料的隔离用例。",
                        "vod_play_from": show_from, "vod_play_url": show_url}

            return [record(f"urban-{year}-{number}", f"都市合成剧 {year} · {number:02d}", 71, year)
                    for year in ("2026", "2025") for number in range(1, 25)] + [
                record("urban-1988-1", "都市合成剧 1988 · 01", 71, "1988"),
                record("short-overview", "合成短剧综合条目", 58, "2026"),
                record("action-1", "合成动作片", 21, "2026"),
                record("drama-1", "合成国产剧", 31, "2026"),
                record("ai-1", "合成AI漫剧", 74, "2026"),
            ]
        if gallery:
            return [{"vod_id": f"gallery-{number}", "vod_name": f"合成海报 {number:02d}",
                     "type_id": "1", "type_name": "电影", "vod_year": "2026",
                     "vod_pic": f"{self.base_url}/files/poster.png?gallery={number}",
                     "vod_content": "有界加载与返回位置隔离用例。",
                     "vod_play_from": movie_from, "vod_play_url": movie_url} for number in range(1, 41)]
        return [
            {"vod_id": "movie-1", "vod_name": "合成电影", "type_id": "1", "type_name": "电影", "vod_year": "2026",
             "vod_pic": f"{self.base_url}/files/poster.png",
             "vod_content": '<p>开发机生成的24秒样片。</p><img src=x onerror="window.__fixtureInjected=true">',
             "vod_play_from": movie_from, "vod_play_url": movie_url},
            {"vod_id": "show-1", "vod_name": "合成剧集", "type_id": "2", "type_name": "国产剧", "vod_year": "2026",
             "vod_pic": f"{self.base_url}/files/poster.png",
             "vod_content": "两集、两条线路，各集有独立的合成画面。", "vod_total": 2,
             "vod_play_from": show_from, "vod_play_url": show_url},
            {"vod_id": "unsupported-1", "vod_name": "合成网页来源", "type_id": "1", "type_name": "电影", "vod_year": "2026",
             "vod_content": "只提供网页，不可作为媒体播放。", "vod_play_from": "网页线路",
             "vod_play_url": f"正片${self.base_url}/watch/movie"},
        ]

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_args):
        self.stop.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
