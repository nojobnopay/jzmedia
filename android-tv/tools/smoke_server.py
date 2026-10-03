#!/usr/bin/env python3
"""Isolated TV fixture server. No app imports, live credentials, database or NAS access.

Run: python android-tv/tools/smoke_server.py --port 18888 [--hls] [--browse-stress]
Then adb reverse tcp:18888 tcp:18888, and enter http://127.0.0.1:18888 on TV.
Use --require-token to exercise the fixed fixture token ``demo-tv-token``.
Use --browse-stress for 120 episode numbers, merged episodes and duplicate versions.
All media and state are synthetic; request traces omit credentials and request bodies.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile
import threading
import time
import unicodedata
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
import zlib


def poster_bytes(seed):
    width, height = 300, 450
    palette = [(20, 50, 80), (58, 20, 80), (75, 42, 15), (17, 70, 58)]
    base = palette[seed % len(palette)]
    rows = bytearray()
    for y in range(height):
        rows.append(0)
        for x in range(width):
            highlight = 95 if ((x - 150) ** 2 + (y - 180) ** 2 < 8500 or (x + y + seed * 20) % 200 < 18) else 0
            rows.extend(min(255, c + highlight + y // 15) for c in base)
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(bytes(rows))) + chunk(b"IEND", b"")


def make_fixtures(directory, hls, binary=None):
    ffmpeg = str(Path(binary).resolve()) if binary else shutil.which("ffmpeg")
    if not ffmpeg:
        raise SystemExit("ffmpeg is required to generate isolated test media")
    video = directory / "demo.mp4"
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "smptebars=size=640x360:rate=24",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "660", "-c:v", "libx264", "-preset", "ultrafast",
                    "-crf", "32", "-pix_fmt", "yuv420p", "-g", "48", "-c:a", "aac", "-b:a", "64k", "-movflags", "+faststart", str(video)], check=True, timeout=180)
    if hls:
        subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(video), "-c", "copy", "-f", "hls", "-hls_time", "4",
                        "-hls_playlist_type", "vod", "-hls_segment_type", "fmp4", "-hls_segment_filename", str(directory / "segment%04d.m4s"),
                        str(directory / "master.m3u8")], check=True, timeout=120, cwd=directory)
    for index in range(4):
        (directory / f"poster{index}.png").write_bytes(poster_bytes(index))
    (directory / "demo.vtt").write_text("WEBVTT\n\n00:00:00.000 --> 00:00:08.000\njzmedia 电视客户端 · 合成演示媒体\n\n00:00:08.000 --> 00:00:20.000\n遥控器方向键、暂停、音轨与字幕测试\n\n00:00:40.000 --> 00:01:00.000\n续播时间轴：40–60 秒\n", encoding="utf-8")


def _search_text(value):
    return "".join(c for c in unicodedata.normalize("NFKC", str(value)).casefold() if c.isalnum())


def _actor_catalog():
    """Invented people and local work relations; TMDB-shaped keys never leave this server."""
    rows = [
        {"key": "tmdb:9001", "name": "周星河", "avatar_path": "posters/persons/poster0.png",
         "phonetic": ("zxh", "zhouxinghe"), "works": [("movie", i) for i in range(1, 31)] + [("movie", 45), ("movie", 46), ("show", 100)]},
        {"key": "tmdb:9002", "name": "周晓河", "avatar_path": "persons/poster1.png",
         "phonetic": ("zxh", "zhouxiaohe"), "works": [("movie", 2), ("movie", 4), ("movie", 46), ("show", 100)]},
        {"key": "name:林雾汀", "name": "林雾汀", "avatar_path": "",
         "phonetic": ("lwt", "linwuting"), "works": [("show", 100)]},
        {"key": "tmdb:9004", "name": "顾远舟", "avatar_path": "",
         "phonetic": ("gyz", "guyuanzhou"), "works": [("movie", 45)]},
    ]
    rows.extend({"key": f"tmdb:{9100 + i}", "name": f"合成演员{i:02d}", "avatar_path": "",
                 "phonetic": (f"hcyy{i:02d}", f"hechengyanyuan{i:02d}"), "works": [("movie", i)]}
                for i in range(1, 31))
    # A shared spelling is not a shared identity. Exercise both numeric and name keys.
    rows.extend({"key": key, "name": "合成同名演员", "avatar_path": "",
                 "phonetic": ("hctmyy", "hechengtongmingyanyuan"), "works": [("movie", i)]}
                for i, key in enumerate(("tmdb:9201", "tmdb:9202", "name:合成同名演员"), 1))
    return rows


class Fixtures:
    def __init__(self, directory, hls=False, require_token=False, browse_stress=False):
        self.directory, self.hls, self.require_token = directory, hls, require_token
        self.browse_stress = browse_stress
        self.lock = threading.Lock()
        self.progress = {("movie", 1): {"position": 45, "duration": 660}, ("episode", 201): {"position": 46, "duration": 660}}
        self.watched = set()
        if browse_stress:
            self.watched.update(("episode", eid) for eid in (203, 204, 207))
            self.progress[("episode", 208)] = {"position": 135, "duration": 660}
        self.sessions = 0
        self.trace = directory / "requests.jsonl"

    def movie(self, mid):
        return {"id": mid, "title": {2: "山海之间", 46: "沙丘回声"}.get(mid, f"星际航程 {mid:02d}"), "year": 2020 + mid % 6,
                "poster_path": f"posters/poster{mid % 4}.png", "file_path": f"合成电影/film{mid}.mp4", "library_id": 3 if mid == 45 else 1,
                "media_library_id": 2 if mid == 45 else 1, "library_name": "演示电影", "genres": ["科幻" if mid % 2 else "剧情"],
                "origin_country_name": "中国", "vote_average": 7 + mid % 3 / 10,
                "watched": int(("movie", mid) in self.watched), "version_count": 2 if mid == 1 else 1,
                "updated_at": 1000 + mid, "added_at": 1000 + mid,
                "overview": "原创合成影片，用于验证电视界面的浏览、中文、遥控器和播放。" * 8,
                "persons": self.cast("movie", mid),
                "versions": [{"id": mid, "file_path": f"film{mid}.mp4", "spec": "H.264 · AAC · 360p", "edition": "演示版"}] +
                            ([{"id": 101, "file_path": "film1-alt.mp4", "spec": "H.264 · AAC", "edition": "另一版本"}] if mid == 1 else []),
                "collections": [{"id": 1, "name": "科幻时光"}], "extras": [{"id": 301, "title": "幕后片段", "kind": "trailer"}]}

    def episode_ids(self):
        if not self.browse_stress:
            return [201, 202, 203]
        # E05 covers E06; E10 has an additional physical version. There are 120 files.
        return [eid for number in range(1, 121) if number != 6
                for eid in ([200 + number, 1210] if number == 10 else [200 + number])]

    def episode(self, eid):
        number, version = (10, 2) if self.browse_stress and eid == 1210 else (eid - 200, 1)
        merged = self.browse_stress and number == 5
        offline = self.browse_stress and number == 12
        peers = [i for i in self.episode_ids() if (i == 1210) == (version == 2)]
        index = peers.index(eid) if eid in peers else -1
        previous_id = peers[index - 1] if index > 0 else None
        next_id = peers[index + 1] if 0 <= index < len(peers) - 1 else None
        title = ["启程", "新的信号", "回家"][max(0, number - 1) % 3]
        if self.browse_stress and number == 7:
            title = "当遥远灯塔的信号再次亮起，我们终于听见了来自星海尽头的漫长回声"
        return {"id": eid, "title": title, "show_id": 100, "library_id": 2, "media_library_id": 1,
                "show_title": "遥远的灯塔", "season": 1, "episode": number, "episode_end": 6 if merged else None,
                "show_poster": "tv/poster2.png", "poster_path": "tv/poster2.png", "exists": not offline, "missing": int(offline),
                "file_path": f"演示剧集/S01E{number:02d}{'-E06' if merged else ''}{'.v2' if version == 2 else ''}.mp4", "version": version,
                "watched": int(("episode", eid) in self.watched), "progress": self.progress.get(("episode", eid), {}),
                "overview": "旅程继续，信号终于从远方传来。", "cast": self.cast("show", 100),
                "previous_episode": {"id": previous_id} if previous_id is not None else None,
                "next_episode": {"id": next_id, "season": 1, "episode": next_id - 200, "title": "下一集"} if next_id is not None else None}

    def show(self):
        # Match the scanner's POSTER_DIR-relative TV paths, alongside DATA_DIR-relative movies.
        count = len(self.episode_ids())
        watched = sum(("episode", i) in self.watched for i in self.episode_ids())
        return {"id": 100, "title": "遥远的灯塔", "year": 2026, "poster_path": "tv/poster2.png", "episode_count": count,
                "library_id": 2, "media_library_id": 1, "watched_count": watched,
                "overview": "一百二十集的合成长季，用于验证选集与分页。" if self.browse_stress else "三个短篇故事，寻找夜色中的灯塔。",
                "cast": self.cast("show", 100), "next_episode": self.episode(201),
                "seasons": [{"season": 1, "name": "第一季", "total": count, "watched_count": watched}],
                "extras": [{"id": 301, "title": "幕后片段", "label": "花絮", "kind": "trailer", "exists": True}]}

    def search(self, query):
        """Deterministic phonetic fixtures; deliberately independent of production search."""
        q = lambda name, default="": query.get(name, [default])[0]
        term, kind, scope = _search_text(q("q")), q("kind", "all"), q("media_library")
        limit, offset = int(q("limit", "24")), int(q("offset", "0"))
        if len(q("q")) > 80 or kind not in ("all", "movie", "show", "collection") or not 1 <= limit <= 60 or offset < 0:
            raise ValueError("Invalid fixture search query")
        candidates = []
        for mid in range(1, 47):
            row = self.movie(mid)
            phonetic = {2: ("shzj", "shanhaizhijian"), 46: ("sqhs", "shaqiuhuisheng")}.get(
                mid, (f"xjhc{mid:02d}", f"xingjihangcheng{mid:02d}"))
            result = {key: row[key] for key in ("id", "title", "year", "poster_path", "media_library_id")}
            candidates.append(({**result, "kind": "movie", "version_count": row["version_count"]}, phonetic))
        candidates.append(({"id": 100, "kind": "show", "title": "遥远的灯塔", "year": 2026,
                            "poster_path": "tv/poster2.png", "media_library_id": 1}, ("yyddt", "yaoyuandedengta")))
        candidates.append(({"id": 1, "kind": "collection", "title": "科幻时光", "name": "科幻时光",
                            "poster_path": "posters/poster1.png", "media_library_id": 1, "member_count": 8}, ("khsg", "kehuanshiguang")))
        rows = [row for row, phonetic in candidates if term and (kind == "all" or row["kind"] == kind)
                and (not scope or str(row["media_library_id"]) == scope)
                and any(term in _search_text(value) for value in (row["title"], *phonetic))]
        return {"items": rows[offset:offset + limit], "total": len(rows), "has_more": offset + limit < len(rows),
                "limit": limit, "offset": offset}

    def cast(self, kind, item_id):
        return [{"name": row["name"], "tmdb_id": int(row["key"].split(":", 1)[1]) if row["key"].startswith("tmdb:") else None,
                 "avatar": row["avatar_path"], "character": "合成角色", "role": "actor"}
                for row in _actor_catalog() if (kind, item_id) in row["works"]]

    def actors(self, query, works=False):
        q = lambda name, default="": query.get(name, [default])[0]
        raw_query, scope, kind = q("q"), q("media_library"), q("kind", "all")
        limit, offset = int(q("limit", "24")), int(q("offset", "0"))
        if len(raw_query) > 80 or not 1 <= limit <= 60 or offset < 0 or (works and kind not in ("all", "movie", "show")):
            raise ValueError("Invalid fixture actor query")
        candidates = []
        for row in _actor_catalog():
            scoped_works = [(work_kind, item_id) for work_kind, item_id in row["works"]
                            if not scope or scope == ("2" if work_kind == "movie" and item_id == 45 else "1")]
            if not scoped_works:
                continue
            movie_count = sum(work_kind == "movie" for work_kind, _ in scoped_works)
            profile = {key: row[key] for key in ("key", "name", "avatar_path")}
            profile.update(movie_count=movie_count, show_count=len(scoped_works) - movie_count, work_count=len(scoped_works))
            candidates.append((profile, row["phonetic"], scoped_works))
        actor = None
        if works:
            chosen = next((row for row in candidates if row[0]["key"] == q("actor")), None)
            if chosen is None:
                raise KeyError("Unknown fixture actor")
            actor, _, links = chosen
            rows = []
            for work_kind, item_id in links:
                if kind != "all" and kind != work_kind:
                    continue
                item = self.movie(item_id) if work_kind == "movie" else self.show()
                rows.append({**item, "kind": work_kind})
        else:
            term = _search_text(raw_query)
            rows = [profile for profile, phonetic, _ in candidates
                    if not raw_query.strip() or (term and any(term in _search_text(value) for value in (profile["name"], *phonetic)))]
            rows.sort(key=lambda row: (-row["work_count"], row["name"], row["key"]))
        result = {"items": rows[offset:offset + limit], "total": len(rows), "has_more": offset + limit < len(rows),
                  "limit": limit, "offset": offset}
        if actor is not None:
            result["actor"] = actor
        return result

    def media(self, mid):
        return {"version_id": mid, "title": "合成演示", "duration": 660, "width": 640, "height": 360, "vcodec": "h264", "fps": 24,
                "container": "mp4", "playable": True, "vcaps": ['video/mp4; codecs="avc1.42C01E"'],
                "audio": [{"index": 0, "ff_index": 1, "codec": "aac", "lang": "zho", "title": "合成测试音", "channels": 1, "sample_rate": 48000, "default": 1, "caps": ['audio/mp4; codecs="mp4a.40.2"']}],
                "subs": [{"index": 0, "ff_index": None, "codec": "webvtt", "lang": "zho", "title": "演示字幕", "default": 1, "image": 0}]}

    def handle(self, method, path, query, body):
        q = lambda name, default="": query.get(name, [default])[0]
        if path in ("/api/stream/client-info", "/api/stream/client-check"):
            return {"protocol_version": 1, "auth_required": self.require_token,
                    "features": ["android_tv", "independent_sessions", "tv_search", "tv_actor_search"]}
        if path == "/api/media-libraries":
            return {"items": [{"id": 1, "name": "家庭演示库", "enabled": True},
                              {"id": 2, "name": "备用演示库", "enabled": True}], "default_id": 1}
        if path == "/api/tv-client/search":
            return self.search(query)
        if path in ("/api/tv-client/actors", "/api/tv-client/actor-works"):
            return self.actors(query, works=path.endswith("/actor-works"))
        if path in ("/api/movies", "/api/search"):
            rows = [self.movie(i) for i in range(1, 47)]
            rows = [r for r in rows if q("q").lower() in r["title"].lower() and (not q("genre") or q("genre") in r["genres"])
                    and (not q("media_library") or str(r["media_library_id"]) == q("media_library"))
                    and (not q("year") or str(r["year"]) == q("year")) and (not q("watched") or str(r["watched"]) == q("watched"))]
            rows.sort(key=lambda r: r.get(q("sort", "added_at"), r["id"]), reverse=q("order", "desc") == "desc")
            offset, limit = int(q("offset", "0")), int(q("limit", "36"))
            return {"items": rows[offset:offset + limit], "has_more": len(rows) > offset + limit, "offset": offset, "limit": limit}
        if path == "/api/movies/recent-played":
            progress = self.progress.get(("movie", 1))
            return {"items": [{**self.movie(1), "progress": {**progress, "version_id": 1, "last_played_at": 2000}}] if progress and ("movie", 1) not in self.watched else []}
        if path == "/api/tv/recent-played":
            progress = self.progress.get(("episode", 201))
            return {"items": [{**self.show(), "kind": "episode", "subtitle": "S01E01 · 启程", "progress": {**progress, "version_id": 201, "last_played_at": 1990}}] if progress and ("episode", 201) not in self.watched else []}
        if path in ("/api/facets", "/api/tv/facets"):
            return {"genres": [{"value": "科幻", "count": 23}, {"value": "剧情", "count": 22}], "regions": [{"value": "华语", "count": 45}], "years": [{"value": y, "count": 7} for y in range(2025, 2019, -1)]}
        if path == "/api/tv/shows":
            rows = [self.show()] if q("q") in self.show()["title"] and q("media_library", "1") == "1" else []
            return {"items": rows, "total": len(rows), "has_more": False}
        if path == "/api/tv/shows/100":
            return self.show()
        if path == "/api/tv/shows/100/seasons/1":
            rows = [self.episode(i) for i in self.episode_ids()]
            versions = [{"version": version, "count": sum(row["version"] == version for row in rows)}
                        for version in sorted({row["version"] for row in rows})]
            rows = [row for row in rows if not q("version") or str(row["version"]) == q("version")]
            offset, limit = max(0, int(q("offset", "0"))), max(1, min(500, int(q("limit", "100"))))
            return {**self.show(), "show_title": "遥远的灯塔", "show_poster": "tv/poster2.png", "name": "第一季", "season": 1,
                    "total": len(rows), "offset": offset, "limit": limit, "distinct_count": 120 if self.browse_stress else 3,
                    "has_more": offset + limit < len(rows), "episodes": rows[offset:offset + limit], "versions": versions,
                    "next_episode": next((row for row in rows if not row["watched"] and row["exists"]), None)}
        if path == "/api/collections":
            return {"items": [{"id": 1, "name": "科幻时光", "member_count": 8, "cover": "posters/poster1.png", "updated_at": 1000}] if q("q") in "科幻时光" else []}
        if path == "/api/collections/1":
            return {"id": 1, "name": "科幻时光", "member_count": 8, "overview": "原创模拟合集", "members": [self.movie(i) for i in range(1, 9)]}
        if path.endswith("/similar"):
            return {"items": [self.movie(i) for i in range(2, 5)] if "/movies/" in path else []}
        match = re.fullmatch(r"/api/movies/(\d+)", path)
        if match:
            return self.movie(int(match[1]))
        match = re.fullmatch(r"/api/tv/episodes/(\d+)(/next)?", path)
        if match:
            row = self.episode(int(match[1]))
            return {"next": row["next_episode"]} if match[2] else row
        if path == "/api/movies/batch":
            for mid in body.get("ids", []):
                self.set_watched("movie", mid, body.get("ops", {}).get("watched"))
            return {"ok": True}
        match = re.fullmatch(r"/api/tv/(episodes|shows)/(\d+)(?:/seasons/1)?/watched", path)
        if match:
            for eid in ([int(match[2])] if match[1] == "episodes" else self.episode_ids()):
                self.set_watched("episode", eid, body.get("watched", True))
            return {"ok": True}
        if path == "/api/stream/progress":
            key = (q("kind", "movie"), int(q("version_id")))
            if method == "POST":
                self.progress[key] = {"position": body["position"], "duration": body["duration"]}
            elif method == "DELETE":
                self.progress.pop(key, None)
            return {"version_id": key[1], **self.progress.get(key, {"position": 0, "duration": 660})}
        match = re.fullmatch(r"/api/stream/(\d+)/(media|decide|sessions)", path)
        if match:
            mid, action = int(match[1]), match[2]
            if action == "media":
                return self.media(mid)
            plan = {"vcopy": True, "width": 640, "height": 360, "acodec": "aac"}
            if action == "decide":
                return {"method": "remux" if self.hls else "direct", "direct_url": "/fixture/demo.mp4", "plan": plan,
                        "subtitle_mode": "webvtt" if body.get("sub") is not None else "none", "reasons": [], "media": self.media(mid)}
            self.sessions += 1
            return {"session_id": f"demo-{self.sessions}", "playlist_url": f"/api/stream/sessions/demo-{self.sessions}/master.m3u8",
                    "media_start": 0, "initial_time": body.get("start", 0), "complete": True, "plan": plan}
        if re.fullmatch(r"/api/stream/sessions/demo-\d+(?:/ping)?", path):
            return {"ok": True}
        raise KeyError(path)

    def set_watched(self, kind, mid, value):
        if value:
            self.watched.add((kind, mid))
            self.progress.pop((kind, mid), None)
        else:
            self.watched.discard((kind, mid))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        self.serve("GET")

    def do_POST(self):
        self.serve("POST")

    def do_DELETE(self):
        self.serve("DELETE")

    def serve(self, method):
        parsed = urlparse(self.path)
        fixtures = self.server.fixtures
        with fixtures.lock, fixtures.trace.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"at": time.time(), "method": method, "path": parsed.path, "query": parsed.query}) + "\n")
        if method != "GET" and fixtures.require_token and self.headers.get("X-Api-Token") != "demo-tv-token":
            return self.send_json(401, {"detail": "Unauthorized"})
        if method == "GET":
            file = None
            content_type = "application/octet-stream"
            if parsed.path == "/fixture/demo.mp4":
                file, content_type = fixtures.directory / "demo.mp4", "video/mp4"
            elif re.fullmatch(r"/posters/(?:(?:tv|persons)/)?poster[0-3]\.png", parsed.path):
                file, content_type = fixtures.directory / parsed.path.rsplit("/", 1)[1], "image/png"
            elif re.fullmatch(r"/api/stream/\d+/sub/0\.vtt", parsed.path):
                file, content_type = fixtures.directory / "demo.vtt", "text/vtt; charset=utf-8"
            elif re.fullmatch(r"/api/stream/sessions/demo-\d+/(master\.m3u8|init\.mp4|segment\d+\.m4s)", parsed.path):
                file = fixtures.directory / parsed.path.rsplit("/", 1)[1]
                content_type = "application/vnd.apple.mpegurl" if file.suffix == ".m3u8" else "video/mp4"
            if file:
                return self.send_file(file, content_type)
        try:
            size = min(int(self.headers.get("Content-Length", 0)), 65536)
            body = json.loads(self.rfile.read(size)) if size else {}
            with fixtures.lock:
                result = fixtures.handle(method, parsed.path, parse_qs(parsed.query), body)
            self.send_json(200, result)
        except KeyError:
            self.send_json(404, {"detail": "Unknown fixture route"})
        except ValueError:
            self.send_json(422, {"detail": "Invalid fixture query or body"})
        except (BrokenPipeError, ConnectionResetError):
            pass

    def send_json(self, status, body):
        data = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_file(self, file, content_type):
        if not file.is_file():
            return self.send_json(404, {"detail": "Fixture not found"})
        size = file.stat().st_size
        begin, end, partial = 0, size - 1, False
        match = re.fullmatch(r"bytes=(\d+)-(\d*)", self.headers.get("Range", ""))
        if match:
            begin, end, partial = int(match[1]), min(size - 1, int(match[2]) if match[2] else size - 1), True
        if begin > end:
            self.send_response(416); self.send_header("Content-Range", f"bytes */{size}"); self.end_headers(); return
        self.send_response(206 if partial else 200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(end - begin + 1))
        self.send_header("Accept-Ranges", "bytes")
        if partial:
            self.send_header("Content-Range", f"bytes {begin}-{end}/{size}")
        self.end_headers()
        try:
            with file.open("rb") as source:
                source.seek(begin)
                remaining = end - begin + 1
                while remaining > 0:
                    data = source.read(min(65536, remaining))
                    if not data:
                        break
                    self.wfile.write(data)
                    remaining -= len(data)
        except (BrokenPipeError, ConnectionResetError):
            pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18888)
    parser.add_argument("--hls", action="store_true")
    parser.add_argument("--require-token", action="store_true")
    parser.add_argument("--browse-stress", action="store_true", help="Synthetic 120-episode season with merged ranges and duplicate versions")
    parser.add_argument("--ffmpeg", help="Explicit FFmpeg binary; no automatic downloads")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="jzmedia-tv-fixture-") as temp:
        directory = Path(temp)
        make_fixtures(directory, args.hls, args.ffmpeg)
        server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
        server.daemon_threads = True
        server.fixtures = Fixtures(directory, args.hls, args.require_token, args.browse_stress)
        print(json.dumps({"url": f"http://127.0.0.1:{server.server_port}", "fixtures": str(directory), "synthetic": True,
                          "hls": args.hls, "browse_stress": args.browse_stress}), flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()


if __name__ == "__main__":
    main()
