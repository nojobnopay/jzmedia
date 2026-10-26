"""Independent receiver sessions and progress tracking after the phone closes."""
from __future__ import annotations

import mimetypes
import os
import secrets
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urlsplit

from fastapi import HTTPException

from .. import library_paths, storage, store
from ..log import get_logger
from ..routers.stream.common import _media_cached_or_probe, _version_source
from . import audio as audio_output, dlna

logger = get_logger("casting")

RESUME_TIMEOUT = 30
RESUME_RETRY_SECONDS = 5
RESUME_MAX_ATTEMPTS = 3
RESUME_TOLERANCE = 3
RESUME_WARNING = "电视未确认续播位置，可能从头播放；请尝试手动跳转或返回手机播放"


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
    volume_checked: bool = False
    warning: str = ""
    error: str = ""
    failures: int = 0
    active: bool = True
    reported_playing: bool = False
    lock: threading.RLock = field(default_factory=threading.RLock)
    viewing_owner: str = ""
    viewing_generation: int = 0
    resume_target: float | None = None
    resume_deadline: float = 0
    resume_attempts: int = 0
    resume_sent_at: float = 0
    audio_index: int | None = None
    audio_lease: str | None = None
    prepared_path: str | None = None
    preparing: bool = False
    prepare_progress: float | None = None
    prepare_message: str = ""
    play_started: float | None = None
    audio_validator: Callable[[], bool] | None = None

    def public(self) -> dict:
        # State reads must remain available while the device lock is held across
        # slow SOAP calls. In particular, restoring an active cast must not time
        # out and accidentally start a second player on the phone.
        snapshot = vars(self).copy()
        keys = ("id", "version_id", "kind", "title", "state", "position", "position_known",
                "last_position", "duration", "volume", "active", "resume_target", "warning", "error",
                "audio_index", "prepare_progress", "prepare_message")
        return {**{key: snapshot[key] for key in keys}, "device": snapshot["device"].public(),
                "resume_pending": snapshot["resume_target"] is not None,
                "receiver_type": "dlna", "created_at": snapshot["created"]}


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
        self.wake_event = threading.Event()
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
             server_url: str, request_url: str, audio: int | None = None,
             sub: int | None = None) -> dict:
        if sub is not None and sub >= 0:
            raise HTTPException(422, "内置投屏不支持所选字幕，请打开电视上的 jzmedia-tv 播放")
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
        tracks = info.get("audio") or []
        if audio is not None and (type(audio) is not int or not 0 <= audio < len(tracks)):
            raise HTTPException(422, "所选音轨不存在，请重新选择音轨")
        prepare_audio = audio is not None and len(tracks) > 1
        if prepare_audio:
            audio_output.validate_selection(info, audio)
        duration = float(info.get("duration") or 0)
        mime = "video/mp4" if prepare_audio else media_type(row["file_path"])
        sid = "dlna_" + secrets.token_urlsafe(24)
        uri = f"{address}/api/cast/media/{sid}"
        title = row.get("title") or os.path.basename(row["file_path"])
        session = CastSession(sid, device, version_id, kind, row, uri, mime, duration, title)
        session.audio_index = audio
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
                    raise HTTPException(422, "电视未声明支持此文件格式，请打开电视上的 jzmedia-tv 播放，或换用兼容影片版本")
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
            if prepare_audio:
                session.preparing = True
                session.state = "PREPARING"
                session.resume_target = min(position, max(0, duration - 1)) if duration else position
                session.prepare_message = "正在准备所选音轨的完整兼容文件，首次需要等待；视频保持原样，音频为 AAC 双声道"
                expected = (row.get("library_id"), row["file_path"], source.size, source.mtime)
                def source_valid():
                    current = store.get_playable(kind, version_id)
                    if not current or (current.get("library_id"), current["file_path"]) != expected[:2]:
                        return False
                    backend = storage.backend_for(current.get("library_id") or library_paths.DEFAULT_LIBRARY_ID)
                    stat = backend.stat(current["file_path"])
                    return not stat.is_dir and (stat.size, stat.mtime) == expected[2:]
                def valid():
                    from ..viewing import check_lease
                    return (not session.viewing_owner or check_lease(session.viewing_owner, session.viewing_generation)) and source_valid()
                try:
                    session.audio_validator = source_valid
                    session.audio_lease = audio_output.outputs.prepare(row, source, info, audio, valid)
                except Exception:
                    with self.lock:
                        self.sessions.pop(sid, None)
                    raise
                # The newest accepted intent cancels older pending starts, even
                # when the files finish in the opposite order. A playing cast
                # remains untouched until this complete file can replace it.
                with self.lock:
                    for old in self.sessions.values():
                        if old.id != sid and old.device.id == device.id and old.preparing:
                            old.preparing, old.active, old.state = False, False, "REPLACED"
                            self._cancel_resume(old)
                            self._release_audio(old)
                self._ensure_worker()
                return session.public()
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
                            self._cancel_resume(old)
                            self._release_audio(old)
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
                session.resume_target = min(position, max(0, duration - 1)) if duration else position
                session.resume_deadline = time.monotonic() + RESUME_TIMEOUT
                # A single state snapshot can start a ready receiver immediately.
                # Waiting/retrying belongs to the independent monitor, never a
                # sleep loop inside this request or the device lock.
                self._poll(session, read_volume=False)
            self._ensure_worker()
            return session.public()

    def control(self, sid: str, command: str, value: float | None = None) -> dict:
        session = self.get(sid)
        with session.lock:
            if not session.active:
                raise HTTPException(409, "此投屏已结束或已被其他播放替换")
            if session.preparing:
                if command != "stop":
                    raise HTTPException(409, "正在准备所选音轨，请等待完成或停止投屏")
                self._release_audio(session)
                self._cancel_resume(session)
                session.preparing, session.active, session.state = False, False, "STOPPED"
                session.prepare_message = "音轨准备已取消"
                return session.public()
            if command == "seek" and (value is None or value < 0 or (session.duration and value > session.duration)):
                raise HTTPException(422, "跳转位置超出视频范围")
            manual_position = command in ("seek", "stop")
            self._poll(session, allow_resume=not manual_position, read_volume=False)
            if not session.active:
                raise HTTPException(409, "此投屏已结束或已被其他播放替换")
            if manual_position:
                self._cancel_resume(session)
            args = {"InstanceID": 0}
            service = session.device.transport
            name = {"play": "Play", "pause": "Pause", "stop": "Stop", "seek": "Seek",
                    "volume": "SetVolume"}[command]
            if command == "play":
                args["Speed"] = 1
            elif command == "seek":
                args.update(Unit="REL_TIME", Target=dlna.time_text(value))
            elif command == "volume":
                if not session.device.rendering:
                    raise HTTPException(422, "电视未提供 DLNA 音量控制，请使用电视遥控器")
                if value is None or not 0 <= value <= 100:
                    raise HTTPException(422, "音量必须在 0 到 100 之间")
                service = session.device.rendering
                args.update(Channel="Master", DesiredVolume=int(value))
            dlna.action(service, name, args)
            if manual_position and session.warning == RESUME_WARNING:
                session.warning = ""
            if command == "stop":
                session.state, session.active = "STOPPED", False
                self._release_audio(session)
            elif command == "volume":
                session.volume = int(value)
            else:
                # Commands are requests, not proof of a reported playback state.
                session.state = "TRANSITIONING"
                if command == "seek":
                    session.position_known = False
            session.error = ""
            return session.public()

    @staticmethod
    def _cancel_resume(session: CastSession):
        session.resume_target = None
        session.resume_deadline = 0

    @staticmethod
    def _release_audio(session: CastSession):
        if session.audio_lease:
            audio_output.outputs.release(session.audio_lease)
            session.audio_lease = None

    def _poll_preparation(self, session: CastSession):
        from ..viewing import check_lease
        if session.viewing_owner and not check_lease(session.viewing_owner, session.viewing_generation):
            self._release_audio(session)
            self._cancel_resume(session)
            session.preparing, session.active, session.state = False, False, "PROTECTION_LOCKED"
            return
        if not self._audio_source_valid(session):
            return
        prepared = audio_output.outputs.snapshot(session.audio_lease or "")
        session.prepare_progress = prepared.get("progress")
        if prepared["state"] in ("queued", "preparing"):
            if prepared["state"] == "queued":
                session.prepare_message = "等待播放处理资源，所选音轨将在完整文件准备好后投屏"
            return
        if prepared["state"] != "ready":
            self._release_audio(session)
            self._cancel_resume(session)
            session.preparing, session.active, session.state = False, False, "ERROR"
            session.error = prepared.get("error") or "音轨准备已取消或源文件发生变化，请重新尝试"
            return
        session.prepared_path = prepared["path"]
        session.duration = prepared["duration"]
        target = session.resume_target or 0
        # A receiver may fetch synchronously during SetURI or Play, before its
        # SOAP response returns. Publish the complete, leased file first; the
        # device lock still serializes controls and start-failure cleanup.
        session.preparing = False
        session.state, session.prepare_message = "TRANSITIONING", "所选音轨文件已准备完成"
        try:
            dlna.action(session.device.transport, "SetAVTransportURI", {"InstanceID": 0,
                "CurrentURI": session.uri, "CurrentURIMetaData": dlna.metadata(
                    session.title, session.uri, session.mime, session.duration, prepared["size"])})
            with self.lock:
                previous = [old for old in self.sessions.values()
                            if old.id != session.id and old.device.id == session.device.id]
                for old in previous:
                    old.active, old.state = False, "REPLACED"
                    self._cancel_resume(old)
                    self._release_audio(old)
            dlna.action(session.device.transport, "Play", {"InstanceID": 0, "Speed": 1})
        except dlna.CastError as error:
            # Only our own URI may be stopped after a partially accepted start.
            try:
                position = dlna.action(session.device.transport, "GetPositionInfo", {"InstanceID": 0})
                if position.get("TrackURI") == session.uri:
                    dlna.action(session.device.transport, "Stop", {"InstanceID": 0})
            except dlna.CastError:
                pass
            self._release_audio(session)
            self._cancel_resume(session)
            session.preparing, session.active, session.state = False, False, "ERROR"
            session.error = str(error)
            return
        session.play_started = time.monotonic()
        if target > 0:
            session.resume_target = min(target, max(0, session.duration - 1))
            session.resume_deadline = time.monotonic() + RESUME_TIMEOUT
        else:
            self._cancel_resume(session)

    def _resume(self, session: CastSession, state: str, track_uri: str, position: float | None):
        target = session.resume_target
        if target is None:
            return
        now = time.monotonic()
        matching = track_uri == session.uri
        if (matching and position is not None and session.resume_attempts
                and state in ("PLAYING", "PAUSED_PLAYBACK")):
            elapsed = max(0, now - session.resume_sent_at) if state == "PLAYING" else 0
            if target - RESUME_TOLERANCE <= position <= target + elapsed + RESUME_TOLERANCE:
                self._cancel_resume(session)
                return
        if (now >= session.resume_deadline or session.resume_attempts >= RESUME_MAX_ATTEMPTS
                and now - session.resume_sent_at >= RESUME_RETRY_SECONDS):
            self._cancel_resume(session)
            session.warning = RESUME_WARNING
            return
        if (not matching or state != "PLAYING" or session.resume_attempts >= RESUME_MAX_ATTEMPTS
                or session.resume_attempts and now - session.resume_sent_at < RESUME_RETRY_SECONDS):
            return
        session.resume_attempts += 1
        session.resume_sent_at = now
        try:
            dlna.action(session.device.transport, "Seek", {
                "InstanceID": 0, "Unit": "REL_TIME", "Target": dlna.time_text(target)})
        except dlna.CastError:
            # Rejecting a seek while loading is common; bounded retries require
            # another matching receiver report rather than trusting SOAP success.
            pass

    def _audio_source_valid(self, session: CastSession) -> bool:
        if session.audio_validator is None:
            return True
        try:
            valid = session.audio_validator()
        except Exception:
            valid = False
        if not valid:
            self._release_audio(session)
            self._cancel_resume(session)
            session.preparing, session.active, session.state = False, False, "SOURCE_CHANGED"
            session.error = "片源或播放权限已变化，请重新发起投屏"
        return valid

    def _poll(self, session: CastSession, *, allow_resume: bool = True, read_volume: bool = True):
        with session.lock:
            if not session.active:
                return
            if not store.get_playable(session.kind, session.version_id):
                self._cancel_resume(session)
                self._release_audio(session)
                session.state, session.active = "MEDIA_REMOVED", False
                return
            if session.preparing:
                self._poll_preparation(session)
                if session.preparing or not session.active:
                    return
                # The complete file has just been sent to the receiver. Read
                # its actual status in this same monitor pass, rather than
                # adding another idle interval before the phone sees playback.
            if not self._audio_source_valid(session):
                return
            try:
                transport = dlna.action(session.device.transport, "GetTransportInfo", {"InstanceID": 0})
                try:
                    position = dlna.action(session.device.transport, "GetPositionInfo", {"InstanceID": 0})
                except dlna.CastError:
                    position = {}  # Position reporting is optional on some receivers.
                track_uri = position.get("TrackURI", "")
                if track_uri and track_uri != session.uri:
                    self._cancel_resume(session)
                    self._release_audio(session)
                    session.state, session.active = "REPLACED", False
                    return
                from ..viewing import check_lease, _connection, IDLE_SECONDS
                if session.viewing_owner and not check_lease(session.viewing_owner, session.viewing_generation):
                    # Do not stop a different device's/newer cast. A matching
                    # receiver URI proves this is still our protected playback.
                    if track_uri == session.uri:
                        dlna.action(session.device.transport, "Stop", {"InstanceID": 0})
                    self._cancel_resume(session)
                    self._release_audio(session)
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
                transport_error = transport.get("CurrentTransportStatus") == "ERROR_OCCURRED"
                if transport_error:
                    self._cancel_resume(session)
                elif allow_resume:
                    self._resume(session, state, track_uri, pos)
                session.position_known = bool(track_uri == session.uri and pos is not None
                                              and session.resume_target is None and not transport_error)
                if (state in ("STOPPED", "NO_MEDIA_PRESENT") and not session.reported_playing
                        and time.monotonic() - (session.play_started or session.created) <= 30):
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
                                 if transport_error else "")
                if (not session.reported_playing and time.monotonic() - (session.play_started or session.created) > RESUME_TIMEOUT
                        and state in ("TRANSITIONING", "STOPPED", "NO_MEDIA_PRESENT") and not session.error):
                    session.error = "电视尚未确认视频起播，请检查片源兼容性和服务器地址，或返回手机播放"
                session.failures = 0
                if (state in ("STOPPED", "NO_MEDIA_PRESENT")
                        and (session.reported_playing or time.monotonic() - (session.play_started or session.created) > 30)):
                    self._cancel_resume(session)
                    self._release_audio(session)
                    session.active = False
                if read_volume and track_uri == session.uri and not session.volume_checked:
                    session.volume_checked = True
                    if session.device.rendering:
                        try:
                            value = dlna.action(session.device.rendering, "GetVolume", {
                                "InstanceID": 0, "Channel": "Master"}).get("CurrentVolume", "")
                            if (isinstance(value, str) and value.isascii() and value.isdecimal()
                                    and len(value) <= 3 and 0 <= int(value) <= 100):
                                session.volume = int(value)
                        except dlna.CastError:
                            pass  # An optional volume query must not break playback monitoring.
            except dlna.CastError as exc:
                session.error = str(exc)
                session.position_known = False
                session.failures += 1
                if session.failures >= 6:
                    self._cancel_resume(session)
                    self._release_audio(session)
                    session.state, session.active = "DISCONNECTED", False

    def _prune(self):
        now = time.monotonic()
        for session in list(self.sessions.values()):
            if now - session.created >= 24 * 3600 or not session.active and now - session.created >= 3600:
                self._release_audio(session)
        self.sessions = {sid: s for sid, s in self.sessions.items()
                         if now - s.created < 24 * 3600 and (s.active or now - s.created < 3600)}

    def _ensure_worker(self):
        with self.lock:
            if self.worker and self.worker.is_alive():
                self.wake_event.set()
                return
            self.stop_event.clear()
            self.worker = threading.Thread(target=self._monitor, name="dlna-progress", daemon=True)
            self.worker.start()

    def _monitor(self):
        while not self.stop_event.is_set():
            with self.lock:
                resuming = any(s.active and s.resume_target is not None for s in self.sessions.values())
            self.wake_event.wait(1 if resuming else 5)
            self.wake_event.clear()
            if self.stop_event.is_set():
                return
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
        self.wake_event.set()
        worker = self.worker
        if worker:
            worker.join(timeout=2)
        for session in list(self.sessions.values()):
            self._release_audio(session)
        audio_output.outputs.shutdown()
        # Playback belongs to the TV; shutdown does not send Stop to devices.

    def source(self, sid: str):
        session = self.get(sid)
        row = store.get_playable(session.kind, session.version_id)
        if not row:
            raise HTTPException(404, "影片已从媒体库移除")
        if session.preparing:
            raise HTTPException(409, "所选音轨仍在准备，尚未提供取流文件")
        if session.audio_validator is not None and (not session.active or not session.prepared_path
                                                   or not session.audio_lease):
            raise HTTPException(410, "所选音轨投屏已结束，请重新发起投屏")
        if not self._audio_source_valid(session):
            raise HTTPException(410, "片源或播放权限已变化，请重新发起投屏")
        if session.prepared_path:
            from ..storage.local import LocalStorageBackend
            path = os.path.basename(session.prepared_path)
            backend = LocalStorageBackend({"path": os.path.dirname(session.prepared_path), "name": "cast-audio"})
            return session, backend, path
        backend = storage.backend_for(row.get("library_id") or library_paths.DEFAULT_LIBRARY_ID)
        return session, backend, row["file_path"]


manager = CastManager()
