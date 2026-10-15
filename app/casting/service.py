"""Independent receiver sessions and progress tracking after the phone closes."""
from __future__ import annotations

import mimetypes
import os
import secrets
import socket
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from fastapi import HTTPException

from .. import library_paths, storage, store
from ..log import get_logger
from ..routers.stream.common import _media_cached_or_probe, _version_source
from . import dlna

logger = get_logger("casting")


def base_url(value: str) -> str:
    try:
        url = urlsplit(value.strip().rstrip("/"))
        # An explicit LAN address avoids leaking emulator/localhost addresses to TVs.
        if (url.scheme not in ("http", "https") or not dlna.lan_ip(url.hostname or "")
                or url.hostname == "10.0.2.2" or url.username or url.password
                or url.query or url.fragment or not 1 <= (80 if url.port is None else url.port) <= 65535
                or any(part in (".", "..") for part in url.path.split("/"))):
            raise ValueError
    except ValueError as exc:
        raise HTTPException(422, "请填写电视可访问的服务器局域网地址，例如 http://192.168.1.10:8080") from exc
    return url.geturl()


def suggested_url(request_url: str, device: dlna.Device | None = None) -> str:
    configured = os.getenv("CAST_BASE_URL", "").strip()
    if configured:
        return base_url(configured)
    try:
        return base_url(request_url)
    except HTTPException:
        if device is None:
            return ""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect((device.address, 1900))
            address = sock.getsockname()[0]
        url = urlsplit(request_url)
        port = f":{url.port}" if url.port else ""
        return base_url(f"{url.scheme}://{address}{port}{url.path}")
    except (OSError, ValueError, HTTPException):
        return ""


def media_type(path: str) -> str:
    return {".mkv": "video/x-matroska", ".ts": "video/mp2t", ".m2ts": "video/mp2t",
            ".avi": "video/x-msvideo"}.get(os.path.splitext(path)[1].lower(),
                                         mimetypes.guess_type(path)[0] or "video/mp4")


@dataclass
class CastSession:
    id: str
    device: dlna.Device
    version_id: int
    kind: str
    row: dict
    uri: str
    mime: str
    duration: float
    title: str
    created: float = field(default_factory=time.monotonic)
    state: str = "TRANSITIONING"
    position: float = 0
    position_known: bool = False
    last_position: float | None = None
    volume: int | None = None
    warning: str = ""
    error: str = ""
    failures: int = 0
    active: bool = True
    reported_playing: bool = False
    lock: threading.RLock = field(default_factory=threading.RLock)
    viewing_owner: str = ""
    viewing_generation: int = 0

    def public(self) -> dict:
        return {"id": self.id, "device": self.device.public(), "version_id": self.version_id,
                "kind": self.kind, "title": self.title, "state": self.state,
                "position": self.position, "position_known": self.position_known,
                "last_position": self.last_position,
                "duration": self.duration, "volume": self.volume, "active": self.active,
                "warning": self.warning, "error": self.error}


class CastManager:
    def __init__(self):
        self.lock = threading.RLock()
        self.search_lock = threading.Lock()
        self.devices: dict[str, dlna.Device] = {}
        self.manual_devices: dict[str, dlna.Device] = {}
        self.device_locks: dict[str, threading.RLock] = {}
        self.sessions: dict[str, CastSession] = {}
        self.searched_at = 0.0
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None

    def discover(self) -> list[dict]:
        with self.search_lock:
            if time.monotonic() - self.searched_at > 5:
                devices = dlna.discover()
                with self.lock:
                    self.devices = {**{d.id: d for d in devices}, **self.manual_devices}
                    self.searched_at = time.monotonic()
            return self.device_list()

    def add_device(self, location: str) -> dict:
        device = dlna.describe_location(location)
        with self.lock:
            if device.id not in self.manual_devices and len(self.manual_devices) >= dlna.MAX_DEVICES:
                raise dlna.CastError("手动添加的电视过多")
            self.manual_devices[device.id] = device
            self.devices[device.id] = device
            self.searched_at = time.monotonic()
        return device.public()

    def device_list(self) -> list[dict]:
        with self.lock:
            if time.monotonic() - self.searched_at > 120:
                return []
            return [device.public() for device in self.devices.values()]

    def get(self, sid: str) -> CastSession:
        with self.lock:
            session = self.sessions.get(sid)
        if session is None or time.monotonic() - session.created > 24 * 3600:
            raise HTTPException(404, "投屏会话已结束，请重新搜索设备并投屏")
        from ..viewing import current, check_lease, visible_item
        access = current.get()
        if session.viewing_owner and not check_lease(session.viewing_owner, session.viewing_generation):
            raise HTTPException(404, "投屏授权已结束")
        if access is not None:
            visible_item(session.kind, session.version_id)
            if session.viewing_owner and access.session_hash != session.viewing_owner:
                raise HTTPException(404, "投屏会话不存在")
        return session

    def current(self, version_id: int, kind: str) -> dict | None:
        with self.lock:
            matches = [s for s in self.sessions.values() if s.active
                       and s.version_id == version_id and s.kind == kind]
        for session in reversed(matches):
            try:
                return self.get(session.id).public()
            except HTTPException:
                continue
        return None

    def play(self, device_id: str, version_id: int, kind: str, position: float,
             server_url: str, request_url: str) -> dict:
        with self.lock:
            device = self.devices.get(device_id)
            if device is None or time.monotonic() - self.searched_at > 120:
                raise HTTPException(404, "设备列表已过期，请重新搜索电视")
            device_lock = self.device_locks.setdefault(device_id, threading.RLock())
        address = base_url(server_url) if server_url else suggested_url(request_url, device)
        if not address:
            raise HTTPException(422, "无法确定电视可访问的服务器地址，请填写局域网地址")
        row, source = _version_source(version_id, kind)
        info = _media_cached_or_probe(row, source)
        if not info.get("playable"):
            raise HTTPException(422, "片源无法读取，请先检查文件或视频库连接")
        duration = float(info.get("duration") or 0)
        mime = media_type(row["file_path"])
        sid = secrets.token_urlsafe(24)
        uri = f"{address}/api/cast/media/{sid}"
        title = row.get("title") or os.path.basename(row["file_path"])
        session = CastSession(sid, device, version_id, kind, row, uri, mime, duration, title)
        session.lock = device_lock
        with device_lock:
            if device.connection:
                try:
                    sink = dlna.action(device.connection, "GetProtocolInfo", {}).get("Sink", "")
                except dlna.CastError:
                    sink = ""  # Some receivers advertise the service but reject this query.
                protocols = [value.strip().split(":", 3) for value in sink.split(",") if value.strip()]
                aliases = {mime}
                if mime == "video/x-matroska":
                    aliases.update(("video/x-mkv", "video/mkv"))
                if protocols and not any(len(p) == 4 and p[0] in ("http-get", "*")
                                         and p[2].lower() in aliases | {"*", "video/*"} for p in protocols):
                    raise HTTPException(422, "电视未声明支持此文件格式，请换用兼容的影片版本")
                # Match a receiver's alternate Matroska MIME in metadata and HTTP headers.
                for protocol in protocols:
                    if len(protocol) == 4 and protocol[0] == "http-get" and protocol[2].lower() in aliases:
                        mime = session.mime = protocol[2].lower()
                        break
            with self.lock:
                self._prune()
                if len(self.sessions) >= 32:
                    stopped = sorted((s for s in self.sessions.values() if not s.active), key=lambda s: s.created)
                    for old in stopped:
                        self.sessions.pop(old.id, None)
                        if len(self.sessions) < 32:
                            break
                if len(self.sessions) >= 32:
                    raise HTTPException(429, "投屏会话过多，请先停止其他投屏")
                # The receiver may fetch immediately during SetAVTransportURI.
                from ..viewing import current
                access = current.get()
                if access is not None:
                    session.viewing_owner = access.session_hash
                    session.viewing_generation = access.generation
                self.sessions[sid] = session
            uri_set = False
            try:
                dlna.action(device.transport, "SetAVTransportURI", {
                    "InstanceID": 0, "CurrentURI": uri,
                    "CurrentURIMetaData": dlna.metadata(title, uri, mime, duration, source.size)})
                uri_set = True
                with self.lock:
                    for old in self.sessions.values():
                        if old.id != sid and old.device.id == device.id:
                            old.active = False
                            old.state = "REPLACED"
                dlna.action(device.transport, "Play", {"InstanceID": 0, "Speed": 1})
            except dlna.CastError:
                if uri_set:
                    try:
                        dlna.action(device.transport, "Stop", {"InstanceID": 0})
                    except dlna.CastError:
                        pass
                with self.lock:
                    self.sessions.pop(sid, None)
                raise
            if position > 0:
                try:
                    dlna.action(device.transport, "Seek", {
                        "InstanceID": 0, "Unit": "REL_TIME",
                        "Target": dlna.time_text(min(position, max(0, duration - 1)) if duration else position)})
                except dlna.CastError:
                    session.warning = "电视未接受续播位置，可能从头播放；请查看电视画面"
            self._ensure_worker()
            return session.public()

    def control(self, sid: str, command: str, value: float | None = None) -> dict:
        session = self.get(sid)
        with session.lock:
            if not session.active:
                raise HTTPException(409, "此投屏已结束或已被其他播放替换")
            self._poll(session)
            if not session.active:
                raise HTTPException(409, "此投屏已结束或已被其他播放替换")
            args = {"InstanceID": 0}
            service = session.device.transport
            name = {"play": "Play", "pause": "Pause", "stop": "Stop", "seek": "Seek",
                    "volume": "SetVolume"}[command]
            if command == "play":
                args["Speed"] = 1
            elif command == "seek":
                if value is None or value < 0 or (session.duration and value > session.duration):
                    raise HTTPException(422, "跳转位置超出视频范围")
                args.update(Unit="REL_TIME", Target=dlna.time_text(value))
            elif command == "volume":
                if not session.device.rendering:
                    raise HTTPException(422, "电视未提供 DLNA 音量控制，请使用电视遥控器")
                if value is None or not 0 <= value <= 100:
                    raise HTTPException(422, "音量必须在 0 到 100 之间")
                service = session.device.rendering
                args.update(Channel="Master", DesiredVolume=int(value))
            dlna.action(service, name, args)
            if command == "stop":
                session.state, session.active = "STOPPED", False
            elif command == "volume":
                session.volume = int(value)
            else:
                # Commands are requests, not proof of a reported playback state.
                session.state = "TRANSITIONING"
                if command == "seek":
                    session.position_known = False
            session.error = ""
            return session.public()

    def _poll(self, session: CastSession):
        with session.lock:
            if not session.active:
                return
            if not store.get_playable(session.kind, session.version_id):
                session.state, session.active = "MEDIA_REMOVED", False
                return
            try:
                transport = dlna.action(session.device.transport, "GetTransportInfo", {"InstanceID": 0})
                try:
                    position = dlna.action(session.device.transport, "GetPositionInfo", {"InstanceID": 0})
                except dlna.CastError:
                    position = {}  # Position reporting is optional on some receivers.
                track_uri = position.get("TrackURI", "")
                if track_uri and track_uri != session.uri:
                    session.state, session.active = "REPLACED", False
                    return
                from ..viewing import check_lease, _connection, IDLE_SECONDS
                if session.viewing_owner and not check_lease(session.viewing_owner, session.viewing_generation):
                    # Do not stop a different device's/newer cast. A matching
                    # receiver URI proves this is still our protected playback.
                    if track_uri == session.uri:
                        dlna.action(session.device.transport, "Stop", {"InstanceID": 0})
                    session.state, session.active = "PROTECTION_LOCKED", False
                    return
                state = transport.get("CurrentTransportState", "UNKNOWN")
                if state == "PLAYING" and track_uri == session.uri and session.viewing_owner:
                    # Real receiver playback exempts private viewing from idle
                    # expiry without extending the independent management grant.
                    with _connection() as c:
                        c.execute("UPDATE viewing_sessions SET last_activity=?,playback_until=?"
                                  " WHERE token_hash=? AND mode='private' AND generation=?"
                                  " AND MAX(last_activity+?,playback_until)>?",
                                  (time.time(), time.time() + 90, session.viewing_owner,
                                   session.viewing_generation, IDLE_SECONDS, time.time()))
                if state in ("PLAYING", "PAUSED_PLAYBACK"):
                    session.reported_playing = True
                pos = dlna.seconds(position.get("RelTime", ""))
                session.position_known = bool(track_uri == session.uri and pos is not None)
                if (state in ("STOPPED", "NO_MEDIA_PRESENT") and not session.reported_playing
                        and time.monotonic() - session.created <= 30):
                    state = "TRANSITIONING"
                if state in ("STOPPED", "NO_MEDIA_PRESENT") and pos == 0 and session.last_position:
                    session.position_known = False
                if session.position_known:
                    session.position = pos
                    reported = dlna.seconds(position.get("TrackDuration", ""))
                    if reported and not session.duration:
                        session.duration = reported
                    # No guessed positions, unrelated tracks or reset-to-zero stopped progress.
                    if state in ("PLAYING", "PAUSED_PLAYBACK") or state == "STOPPED" and pos > 0:
                        session.last_position = pos
                        store.save_progress(session.version_id, pos, session.duration, session.kind)
                session.state = state
                session.error = ("电视报告播放失败，请检查片源格式和服务器地址"
                                 if transport.get("CurrentTransportStatus") == "ERROR_OCCURRED" else "")
                session.failures = 0
                if (state in ("STOPPED", "NO_MEDIA_PRESENT")
                        and (session.reported_playing or time.monotonic() - session.created > 30)):
                    session.active = False
            except dlna.CastError as exc:
                session.error = str(exc)
                session.position_known = False
                session.failures += 1
                if session.failures >= 6:
                    session.state, session.active = "DISCONNECTED", False

    def _prune(self):
        now = time.monotonic()
        self.sessions = {sid: s for sid, s in self.sessions.items()
                         if now - s.created < 24 * 3600 and (s.active or now - s.created < 3600)}

    def _ensure_worker(self):
        with self.lock:
            if self.worker and self.worker.is_alive():
                return
            self.stop_event.clear()
            self.worker = threading.Thread(target=self._monitor, name="dlna-progress", daemon=True)
            self.worker.start()

    def _monitor(self):
        while not self.stop_event.wait(5):
            with self.lock:
                self._prune()
                sessions = [s for s in self.sessions.values() if s.active]
            for session in sessions:
                if self.stop_event.is_set():
                    return
                try:
                    self._poll(session)
                except Exception:
                    logger.exception("投屏状态检查失败")

    def shutdown(self):
        self.stop_event.set()
        worker = self.worker
        if worker:
            worker.join(timeout=2)
        # Playback belongs to the TV; shutdown does not send Stop to devices.

    def source(self, sid: str):
        session = self.get(sid)
        row = store.get_playable(session.kind, session.version_id)
        if not row:
            raise HTTPException(404, "影片已从媒体库移除")
        backend = storage.backend_for(row.get("library_id") or library_paths.DEFAULT_LIBRARY_ID)
        return session, backend, row["file_path"]


manager = CastManager()
