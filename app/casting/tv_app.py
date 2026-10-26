"""Foreground jzmedia-tv receivers and scoped phone-to-App playback commands."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import hmac
import ipaddress
import secrets
import threading
import time

from fastapi import HTTPException

from .. import store, viewing

ONLINE_TTL = 15
COMMAND_TTL = 60
CONTROL_TIMEOUT = 8


def _lan_address(value):
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    if (not address.is_private or address.is_loopback or address.is_unspecified
            or address.is_multicast or address.is_link_local):
        return None
    return str(address)


@dataclass(frozen=True)
class Scope:
    key: str
    generation: int
    protected: bool

    def valid(self):
        return viewing.check_lease(self.key, self.generation)

    @contextmanager
    def install(self):
        if not self.valid():
            raise HTTPException(403, "观看授权已失效，请重新连接")
        context = (viewing.context_for(viewing.session_by_hash(self.key) if self.key else {})
                   if self.protected else None)
        token = viewing.current.set(context)
        try:
            yield
        finally:
            viewing.current.reset(token)


def current_scope():
    context = viewing.current.get()
    if context is None:
        return Scope("", viewing.context_for({}).generation, False)
    return Scope(context.session_hash, context.generation, True)


@dataclass
class Receiver:
    id: str
    secret: str
    installation: str
    name: str
    address: str
    scope: Scope
    seen: float
    active_session: str | None = None
    online: bool = True
    dlna_name: str = ""

    def public(self):
        return {"id": self.id, "name": self.dlna_name or self.name, "app_name": self.name, "address": self.address,
                "model": "jzmedia-tv", "receiver_type": "jzmedia_tv", "volume_control": False}


@dataclass
class Command:
    id: str
    session_id: str
    action: str
    payload: dict
    created: float
    delivered: bool = False
    accepted: bool | None = None
    done: threading.Event = field(default_factory=threading.Event)

    def public(self):
        return {"id": self.id, "session_id": self.session_id,
                "action": self.action, "payload": dict(self.payload),
                "valid_for_seconds": max(0, COMMAND_TTL - (time.monotonic() - self.created))}


@dataclass
class AppSession:
    id: str
    receiver: Receiver
    scope: Scope
    version_id: int
    kind: str
    title: str
    target: float
    created: float
    state: str = "WAITING_FOR_APP"
    active: bool = True
    accepted: bool = False
    position: float = 0
    position_known: bool = False
    last_position: float | None = None
    duration: float = 0
    error: str = ""
    warning: str = ""

    def public(self):
        snapshot = vars(self).copy()
        pending = snapshot["active"] and snapshot["state"] in ("WAITING_FOR_APP", "LOADING")
        return {key: snapshot[key] for key in ("id", "version_id", "kind", "title", "state",
                "active", "position", "position_known", "last_position", "duration", "error", "warning")} | {
                "device": snapshot["receiver"].public(), "receiver_type": "jzmedia_tv",
                "volume": None, "created_at": snapshot["created"],
                "resume_pending": pending, "resume_target": snapshot["target"] if pending else None}


class TvAppManager:
    def __init__(self):
        self.lock = threading.RLock()
        self.receivers: dict[str, Receiver] = {}
        self.sessions: dict[str, AppSession] = {}
        self.commands: dict[str, list[Command]] = {}

    def _refresh_receiver_names(self):
        # Read only the existing discovery cache, before taking the App registry
        # lock. Naming must not start network discovery or nest the two locks.
        from .service import manager as dlna_manager
        devices = dlna_manager.device_list()
        matches = {}
        for device in devices:
            address = _lan_address(device["address"])
            if address:
                matches.setdefault(address, {})[device["id"]] = device["name"].strip()
        now = time.monotonic()
        with self.lock:
            receivers = [receiver for receiver in self.receivers.values()
                         if receiver.online and now - receiver.seen <= ONLINE_TTL]
            addresses = {}
            for receiver in receivers:
                address = _lan_address(receiver.address)
                if address:
                    addresses.setdefault(address, []).append(receiver)
            for address, same_address in addresses.items():
                candidates = matches.get(address, {})
                if len(same_address) != 1 or len(candidates) > 1:
                    # Shared proxy/NAT addresses are not a device identity.
                    for receiver in same_address:
                        receiver.dlna_name = ""
                elif candidates:
                    same_address[0].dlna_name = next(iter(candidates.values()))
                # The discovery cache expires sooner than a foreground App.
                # Keep a confirmed name for this registration when it does;
                # reconnecting creates a new receiver and starts without it.

    def _finish(self, session, state, error=""):
        session.active = False
        session.state = state
        session.error = error
        session.position_known = False

    def _prune(self):
        now = time.monotonic()
        for receiver in self.receivers.values():
            if now - receiver.seen > ONLINE_TTL:
                receiver.online = False
        for session in self.sessions.values():
            if not session.active:
                continue
            if not session.receiver.online:
                self._finish(session, "DISCONNECTED", "电视 App 已离线，请在电视重新打开 jzmedia-tv")
            elif not session.accepted and now - session.created > COMMAND_TTL:
                self._finish(session, "EXPIRED", "电视 App 未及时接收播放，请重新投屏")
        for key, commands in self.commands.items():
            self.commands[key] = [command for command in commands
                                  if command.accepted is not None or now - command.created <= COMMAND_TTL][-16:]
        self.sessions = {key: session for key, session in self.sessions.items()
                         if session.active or now - session.created < 3600}
        stale = [key for key, receiver in self.receivers.items() if now - receiver.seen > 3600]
        for key in stale:
            self.receivers.pop(key, None)
            self.commands.pop(key, None)

    def register(self, installation, name, address):
        scope = current_scope()
        with self.lock:
            self._prune()
            for key, old in list(self.receivers.items()):
                if old.installation == installation:
                    old.online = False
                    if old.active_session in self.sessions:
                        self._finish(self.sessions[old.active_session], "DISCONNECTED", "电视 App 已重新连接")
                    for command in self.commands.pop(key, []):
                        if command.accepted is None:
                            command.accepted = False
                            command.done.set()
                    self.receivers.pop(key, None)
            if len(self.receivers) >= 64:
                raise HTTPException(429, "在线电视过多，请稍后重试")
            receiver = Receiver("app_" + secrets.token_urlsafe(18), secrets.token_urlsafe(32),
                                installation, name.strip() or "jzmedia-tv", address, scope, time.monotonic())
            self.receivers[receiver.id] = receiver
            self.commands[receiver.id] = []
            return {"receiver_id": receiver.id, "receiver_token": receiver.secret,
                    "heartbeat_seconds": 3, "ttl_seconds": ONLINE_TTL}

    def device_list(self):
        self._refresh_receiver_names()
        with self.lock:
            self._prune()
            receivers = [receiver for receiver in self.receivers.values() if receiver.online]
        return [receiver.public() for receiver in receivers if receiver.scope.valid()]

    def _receiver(self, receiver_id, secret):
        with self.lock:
            receiver = self.receivers.get(receiver_id)
        if receiver is None or not receiver.online or not secret or not hmac.compare_digest(receiver.secret, secret):
            raise HTTPException(404, "电视接收会话已失效，请重新连接")
        scope = current_scope()
        if scope != receiver.scope or not receiver.scope.valid():
            raise HTTPException(403, "电视观看模式已变化，请重新连接")
        return receiver

    def _enqueue(self, session, action, payload):
        command = Command(secrets.token_urlsafe(18), session.id, action, payload, time.monotonic())
        commands = self.commands[session.receiver.id]
        if len([item for item in commands if item.accepted is None]) >= 8:
            raise HTTPException(429, "电视操作尚未完成，请稍后重试")
        commands.append(command)
        return command

    def play(self, device_id, version_id, kind, position, audio=None, sub=None):
        self._refresh_receiver_names()
        scope = current_scope()
        row = viewing.visible_item(kind, version_id)
        with self.lock:
            self._prune()
            receiver = self.receivers.get(device_id)
            if receiver is None or not receiver.online:
                raise HTTPException(409, "请先在电视打开新版 jzmedia-tv 并连接此服务器，再选择 App 接收")
        try:
            with receiver.scope.install():
                viewing.visible_item(kind, version_id)
        except HTTPException as error:
            raise HTTPException(403, "电视当前观看模式不能播放此内容，请先在电视调整观看模式") from error
        media = store.get_media_info(version_id, kind)
        if media:
            if audio is not None and not 0 <= audio < len(media.get("audio") or []):
                raise HTTPException(422, "所选音轨已变化，请刷新播放器")
            if sub is not None and sub >= 0 and sub >= len(media.get("subs") or []):
                raise HTTPException(422, "所选字幕已变化，请刷新播放器")
        with self.lock:
            self._prune()
            if not receiver.online:
                raise HTTPException(409, "电视 App 已离线，请重新打开")
            if len(self.sessions) >= 128:
                raise HTTPException(429, "投屏会话过多，请稍后重试")
            if receiver.active_session in self.sessions:
                old = self.sessions[receiver.active_session]
                self._finish(old, "REPLACED")
                for command in self.commands[receiver.id]:
                    if command.session_id == old.id and command.accepted is None:
                        command.accepted = False
                        command.done.set()
            session = AppSession("tv_" + secrets.token_urlsafe(24), receiver, scope, version_id,
                                 kind, row.get("title") or "投屏影片", position, time.monotonic(),
                                 duration=float((media or {}).get("duration") or 0))
            self.sessions[session.id] = session
            receiver.active_session = session.id
            self._enqueue(session, "play", {"kind": kind, "version_id": version_id, "title": session.title,
                                           "position": position, "audio": audio, "sub": sub})
            return session.public()

    def get(self, sid):
        self._refresh_receiver_names()
        with self.lock:
            self._prune()
            session = self.sessions.get(sid)
        if session is None:
            raise HTTPException(404, "投屏会话不存在")
        scope = current_scope()
        if scope != session.scope or not session.scope.valid():
            raise HTTPException(404, "投屏会话不存在或观看授权已失效")
        viewing.visible_item(session.kind, session.version_id)
        return session

    def current(self, version_id, kind):
        with self.lock:
            self._prune()
            matches = [session for session in self.sessions.values()
                       if session.active and session.version_id == version_id and session.kind == kind]
        for session in sorted(matches, key=lambda value: value.created, reverse=True):
            try:
                return self.get(session.id).public()
            except HTTPException:
                continue
        return None

    def poll(self, receiver_id, secret, body):
        receiver = self._receiver(receiver_id, secret)
        with self.lock:
            self._prune()
            if not body.foreground:
                self._offline(receiver)
                return {"command": None}
            receiver.seen = time.monotonic()
            sid = receiver.active_session
            session = self.sessions.get(sid or "")
        if session and session.active:
            try:
                if not session.scope.valid():
                    raise HTTPException(403)
                with session.scope.install():
                    viewing.visible_item(session.kind, session.version_id)
                with receiver.scope.install():
                    viewing.visible_item(session.kind, session.version_id)
            except HTTPException:
                with self.lock:
                    # A newer phone can replace this session while permission
                    # checks run without the registry lock. Revoke only the
                    # captured owner; never discard the new phone's Play.
                    if receiver.active_session == session.id and session.active:
                        self._finish(session, "PROTECTION_LOCKED", "观看授权已结束")
                        for command in self.commands[receiver.id]:
                            if command.session_id == session.id and command.accepted is None:
                                command.accepted = False
                                command.done.set()
                        self._enqueue(session, "stop", {})
            else:
                with self.lock:
                    current_owner = receiver.active_session == session.id and session.active
                    if current_owner and body.session_id == session.id:
                        session.state = body.state
                        session.error = body.error[:200]
                        known = body.position is not None and body.state in ("PLAYING", "PAUSED_PLAYBACK", "STOPPED")
                        session.position_known = known
                        if known:
                            session.position = body.position
                            session.last_position = body.position
                        if body.duration and body.duration > 0:
                            session.duration = body.duration
                        if body.state in ("STOPPED", "ERROR"):
                            self._finish(session, body.state, body.error[:200])
                    elif current_owner and session.accepted and body.session_id is None and body.state == "IDLE":
                        self._finish(session, "STOPPED")
                if current_owner and body.session_id == session.id and body.state == "PLAYING" and session.scope.key:
                    with viewing._connection() as connection:
                        connection.execute("UPDATE viewing_sessions SET playback_until=? WHERE token_hash=? AND generation=?",
                                           (time.time() + 90, session.scope.key, session.scope.generation))
        with self.lock:
            candidates = [command for command in self.commands.get(receiver.id, []) if command.accepted is None]
            for command in candidates:
                owner = self.sessions.get(command.session_id)
                if owner and (owner.active or command.action == "stop"):
                    command.delivered = True
                    return {"command": command.public()}
            return {"command": None}

    def ack(self, receiver_id, secret, command_id, accepted, error=""):
        receiver = self._receiver(receiver_id, secret)
        with self.lock:
            command = next((value for value in self.commands.get(receiver.id, []) if value.id == command_id), None)
            if command is None:
                raise HTTPException(404, "电视命令已过期")
            if command.accepted is not None:
                if command.accepted != accepted:
                    raise HTTPException(409, "电视命令已取消或已被更新的操作替换")
                return {"ok": True}
            if time.monotonic() - command.created > COMMAND_TTL:
                raise HTTPException(409, "电视命令已过期，请重新投屏")
            session = self.sessions.get(command.session_id)
            if not session or not session.active and command.action != "stop":
                raise HTTPException(409, "电视播放已结束或被替换")
            if not command.delivered:
                raise HTTPException(409, "电视命令尚未领取")
            command.accepted = accepted
            if session and receiver.active_session == session.id:
                if not accepted:
                    self._finish(session, "ERROR", error[:200] or "电视 App 未接受播放操作")
                elif command.action == "stop":
                    self._finish(session, "STOPPED")
                else:
                    session.accepted = True
                    session.state = "LOADING" if command.payload.get("version_id") else "TRANSITIONING"
            command.done.set()
            return {"ok": True}

    def control(self, sid, command, value=None):
        session = self.get(sid)
        if command == "volume":
            raise HTTPException(422, "请使用电视遥控器调整系统音量")
        if command == "seek" and (value is None or value < 0 or session.duration and value > session.duration):
            raise HTTPException(422, "跳转位置超出视频范围")
        with self.lock:
            if not session.active:
                raise HTTPException(409, "此投屏已结束")
            pending = [item for item in self.commands[session.receiver.id]
                       if item.session_id == sid and item.accepted is None]
            if command == "stop" and not session.accepted and not any(item.delivered for item in pending):
                for item in pending:
                    item.accepted = False
                    item.done.set()
                self._finish(session, "CANCELLED")
                return session.public()
            if command == "stop":
                for item in pending:
                    item.accepted = False
                    item.done.set()
            elif not session.accepted:
                raise HTTPException(409, "电视 App 正在准备播放，请稍后重试")
            item = self._enqueue(session, command, {"position": value} if command == "seek" else {})
        if not item.done.wait(CONTROL_TIMEOUT):
            raise HTTPException(504, "尚未确认电视已执行操作，请核对电视状态后重试")
        if not item.accepted:
            raise HTTPException(502, "电视 App 未能执行操作，请核对电视状态")
        return session.public()

    def _offline(self, receiver):
        receiver.online = False
        session = self.sessions.get(receiver.active_session or "")
        if session and session.active:
            self._finish(session, "DISCONNECTED", "电视 App 已退出前台")

    def unregister(self, receiver_id, secret):
        receiver = self._receiver(receiver_id, secret)
        with self.lock:
            self._offline(receiver)
        return {"ok": True}

    def shutdown(self):
        with self.lock:
            for commands in self.commands.values():
                for command in commands:
                    if command.accepted is None:
                        command.accepted = False
                        command.done.set()
            self.receivers.clear()
            self.sessions.clear()
            self.commands.clear()


manager = TvAppManager()
