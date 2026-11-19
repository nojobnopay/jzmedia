"""Short-lived immutable discovery turns. A follow-up can never overwrite another tab."""
from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import secrets
from threading import Lock
from time import monotonic

from .. import viewing
from ..store import _base
from .client import AiUnavailable

COOKIE = "jz_discovery_device"
_TTL = 20 * 60
_MAX = 192
_lock = Lock()
_turns = OrderedDict()


@dataclass(frozen=True)
class Owner:
    device: str
    database: str
    media_library_id: int | None
    library_id: int | None
    entry: str
    kinds: tuple
    access: object


def owner(request, response, body):
    device = request.cookies.get(COOKIE)
    if not device or len(device) != 43:
        device = secrets.token_urlsafe(32)
        response.set_cookie(COOKIE, device, httponly=True, samesite="strict",
                            secure=request.url.scheme == "https", path="/api/ai/search/results")
    return Owner(hashlib.sha256(device.encode()).hexdigest(), str(_base.DB_PATH),
                 body.media_library_id, body.library_id, body.kind,
                 tuple(sorted(set(body.kinds or []))), viewing.current.get())


def allowed():
    ctx = viewing.current.get()
    if ctx and ctx.mode == "children":
        viewing.error("children_restricted", "儿童模式暂不提供智能搜索。")


def verify_access():
    allowed()
    ctx = viewing.current.get()
    if ctx is None:
        valid = not viewing.enabled()
    else:
        valid = viewing.enabled() and viewing.check_lease(ctx.session_hash, ctx.generation)
    if not valid:
        viewing.error("viewing_locked", "观看范围已经改变，请重新搜索。", 403)


def _access_scope(owner):
    ctx = owner.access
    return (ctx.mode, ctx.session_hash, ctx.generation) if ctx else None


def _scope(owner):
    return (owner.device, owner.database, owner.media_library_id, owner.library_id, owner.entry,
            owner.kinds, _access_scope(owner))


def load(key, access):
    if not key:
        return None
    with _lock:
        entry = _turns.get(key)
        if not entry or entry[0] <= monotonic() or entry[1] != _scope(access):
            raise AiUnavailable("session_expired", "这次找片记录已过期或观看范围已改变，请重新搜索。")
        return entry[2]


def save(access, turn):
    verify_access()
    key = secrets.token_urlsafe(24)
    now = monotonic()
    with _lock:
        while _turns and (next(iter(_turns.values()))[0] <= now or len(_turns) >= _MAX):
            _turns.popitem(last=False)
        _turns[key] = (now + _TTL, _scope(access), turn)
    return key
