"""Explicit outbound routes and fixed-source MacCMS/HLS experiment, not production code."""
from dataclasses import dataclass, field
import json
import re
import secrets
import threading
import time
from urllib.parse import quote, unquote, urljoin, urlsplit

import httpx

from scripts.online_p0.fixtures import DURATION, EPISODES, FixtureOrigin


class SourceError(ValueError):
    """An upstream result is unsupported by the deliberately narrow P0 contract."""


@dataclass(frozen=True)
class NetworkPolicy:
    mode: str = "direct"
    proxy_url: str | None = None

    def client(self) -> httpx.Client:
        if self.mode not in {"direct", "proxy"}:
            raise ValueError("P0 network mode must be direct or proxy")
        if self.mode == "direct" and self.proxy_url:
            raise ValueError("direct cannot have a proxy")
        if self.mode == "proxy":
            parsed = urlsplit(self.proxy_url or "")
            if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.port
                    or parsed.username or parsed.password or parsed.path not in {"", "/"}
                    or parsed.query or parsed.fragment):
                raise ValueError("P0 only accepts an explicit loopback HTTP test proxy")
        return httpx.Client(proxy=self.proxy_url if self.mode == "proxy" else None,
                            trust_env=False, follow_redirects=False, timeout=10.0,
                            limits=httpx.Limits(max_connections=8, max_keepalive_connections=4))


@dataclass
class Session:
    created_at: float
    item_id: str
    episode_id: str
    resources: dict[str, str] = field(default_factory=dict)
    resource_ids: dict[str, str] = field(default_factory=dict)


class Gateway:
    """Only the generated fixture origin is trusted; no arbitrary user URL input."""

    def __init__(self, origin: FixtureOrigin, api_policy: NetworkPolicy | None = None,
                 media_policy: NetworkPolicy | None = None):
        self.origin = origin
        self.api_policy = api_policy or NetworkPolicy()
        self.media_policy = media_policy or NetworkPolicy()
        self.api = self.api_policy.client()
        self.media = self.media_policy.client()
        self.sessions: dict[str, Session] = {}
        self.lock = threading.RLock()
        self.ttl = 3600.0

    def close(self):
        self.api.close()
        self.media.close()
        with self.lock:
            self.sessions.clear()

    @staticmethod
    def read_bounded(client: httpx.Client, url: str, limit: int, **kwargs) -> bytes:
        with client.stream("GET", url, **kwargs) as response:
            if response.status_code != 200:
                raise SourceError(f"upstream status {response.status_code}")
            result = bytearray()
            for chunk in response.iter_bytes():
                result.extend(chunk)
                if len(result) > limit:
                    raise SourceError("upstream response too large")
            return bytes(result)

    def catalog(self, **params) -> list[dict]:
        raw = self.read_bounded(self.api, f"{self.origin.base_url}/api.php/provide/vod",
                                262144, params={"ac": "detail", **params})
        try:
            data = json.loads(raw)
        except (ValueError, UnicodeError) as error:
            raise SourceError("invalid API JSON") from error
        if not isinstance(data, dict) or data.get("code") != 1 or not isinstance(data.get("list"), list):
            raise SourceError("invalid MacCMS response")
        rows = data["list"]
        if len(rows) > 50 or any(not isinstance(row, dict) for row in rows):
            raise SourceError("invalid MacCMS items")
        return rows

    @staticmethod
    def summary(row: dict) -> dict:
        if str(row.get("vod_id")) != "1":
            raise SourceError("unregistered fixture item")
        return {"id": "p0-series", "title": str(row.get("vod_name") or "在线点播验证"),
                "type": "series", "source": "fixture-maccms", "episode_count": len(EPISODES)}

    def search(self, query: str) -> dict:
        return {"items": [self.summary(row) for row in self.catalog(wd=query[:100])],
                "source": "fixture-maccms"}

    def detail(self, item_id: str) -> tuple[dict, dict[str, dict[str, str]]]:
        if item_id != "p0-series":
            raise KeyError(item_id)
        rows = self.catalog(ids="1")
        if len(rows) != 1:
            raise SourceError("fixture item unavailable")
        row = rows[0]
        data = self.summary(row)
        lines = str(row.get("vod_play_from") or "").split("$$$")
        values = str(row.get("vod_play_url") or "").split("$$$")
        if len(lines) != len(values) or set(lines) != {"mp4", "hls"} or len(lines) != 2:
            raise SourceError("unsupported fixture lines")
        streams = {}
        for transport, value in zip(lines, values):
            episodes = value.split("#")
            if len(episodes) != len(EPISODES):
                raise SourceError("fixture episode list changed")
            streams[transport] = {}
            for (episode_id, expected_title), raw in zip(EPISODES, episodes):
                title, separator, url = raw.partition("$")
                if not separator or title != expected_title:
                    raise SourceError("fixture episode identity changed")
                self.validate_media_url(url)
                streams[transport][episode_id] = url
        data.update(episodes=[{"id": episode, "title": title, "number": index}
                              for index, (episode, title) in enumerate(EPISODES, 1)],
                    transports=["mp4", "hls"], description=str(row.get("vod_content") or ""))
        return data, streams

    def validate_media_url(self, url: str) -> str:
        target = urlsplit(url)
        expected = urlsplit(self.origin.base_url)
        if (target.scheme != expected.scheme or target.netloc != expected.netloc
                or target.username or target.password or target.query or target.fragment
                or not target.path.startswith("/files/")):
            raise SourceError("media URL outside registered fixture")
        asset = unquote(target.path[len("/files/"):])
        if asset not in self.origin.assets:
            raise SourceError("unregistered fixture resource")
        return url

    def prune(self):
        now = time.monotonic()
        for sid in list(self.sessions):
            if now - self.sessions[sid].created_at > self.ttl:
                self.sessions.pop(sid, None)

    def session(self, sid: str) -> Session:
        with self.lock:
            self.prune()
            if sid not in self.sessions:
                raise KeyError(sid)
            return self.sessions[sid]

    def resource(self, sid: str, url: str) -> str:
        self.validate_media_url(url)
        with self.lock:
            session = self.session(sid)
            if url not in session.resource_ids:
                if len(session.resources) >= 128:
                    raise SourceError("session resource limit")
                rid = secrets.token_urlsafe(12)
                session.resources[rid] = url
                session.resource_ids[url] = rid
            rid = session.resource_ids[url]
            return f"/api/p0/media/{quote(sid)}/{quote(rid)}"

    def resolve(self, item_id: str, episode_id: str, transport: str) -> dict:
        if transport not in {"mp4", "hls"}:
            raise SourceError("unsupported transport")
        _detail, streams = self.detail(item_id)
        if episode_id not in streams[transport]:
            raise KeyError(episode_id)
        with self.lock:
            self.prune()
            if len(self.sessions) >= 32:
                raise SourceError("too many sessions")
            sid = secrets.token_urlsafe(24)
            self.sessions[sid] = Session(time.monotonic(), item_id, episode_id)
        media_url = self.resource(sid, streams[transport][episode_id])
        return {"kind": "online", "item_id": item_id, "episode_id": episode_id,
                "transport": transport, "media_url": media_url, "duration": DURATION,
                "offset": 0, "source": "fixture-maccms", "session_id": sid}

    def media_url(self, sid: str, rid: str) -> str:
        session = self.session(sid)
        if rid not in session.resources:
            raise KeyError(rid)
        return session.resources[rid]

    def rewrite_hls(self, sid: str, url: str) -> bytes:
        data = self.read_bounded(self.media, url, 262144).decode("utf-8-sig")
        if not data.startswith("#EXTM3U"):
            raise SourceError("invalid HLS playlist")
        output = []
        for raw in data.splitlines():
            line = raw.strip()
            if line.startswith(("#EXT-X-KEY:", "#EXT-X-SESSION-KEY:")):
                raise SourceError("encrypted HLS not supported in P0")
            if line and not line.startswith("#"):
                line = self.resource(sid, urljoin(url, line))
            elif 'URI=' in line:
                if not line.startswith(("#EXT-X-MAP:", "#EXT-X-MEDIA:", "#EXT-X-I-FRAME-STREAM-INF:")):
                    raise SourceError("unsupported HLS URI attribute")
                line, count = re.subn(r'\bURI="([^"]+)"',
                                     lambda match: 'URI="' + self.resource(sid, urljoin(url, match[1])) + '"',
                                     line)
                if count != 1:
                    raise SourceError("invalid HLS URI attribute")
            output.append(line)
        return ("\n".join(output) + "\n").encode()
