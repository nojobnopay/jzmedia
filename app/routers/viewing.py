"""Viewing mode and local content choices. Passwords never enter catalogue settings."""
from typing import Literal
import time

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field

from .. import viewing

router = APIRouter(prefix="/api/viewing", tags=["viewing"])


def _session(request, response):
    supplied = request.headers.get(viewing.HEADER) or request.cookies.get(viewing.COOKIE, "")
    session, secret, _ = viewing.resolve(supplied, create=not supplied)
    if session is None:
        viewing.error("viewing_locked", "设备会话已失效，请重新连接服务", 401)
    if secret and request.headers.get("x-viewing-client") != "tv":
        response.set_cookie(viewing.COOKIE, secret, httponly=True, samesite="strict",
                            secure=request.url.scheme == "https", max_age=365 * 86400)
    response.headers["Cache-Control"] = "no-store"
    return session, secret or supplied


def _result(request, session, secret):
    result = viewing.public_session(viewing.session_by_hash(session["token_hash"]))
    if request.headers.get("x-viewing-client") == "tv":
        result["session_token"] = secret
    return result


def _subject(request):
    # IP limit cannot be reset by obtaining another anonymous session.
    return request.client.host if request.client else "local"


@router.get("/session")
def get_session(request: Request, response: Response):
    session, secret = _session(request, response)
    return _result(request, session, secret)


class ModeBody(BaseModel):
    mode: Literal["normal", "children", "private"]
    password: str | None = Field(default=None, max_length=128)


@router.post("/mode")
def change_mode(body: ModeBody, request: Request, response: Response):
    session, secret = _session(request, response)
    if body.mode != "normal" and not viewing.enabled():
        viewing.error("protection_setup_required", "请先设置保护密码")
    needs_password = body.mode == "private" or (session["mode"] == "children" and body.mode != "children")
    revision = None
    if needs_password:
        if not body.password:
            viewing.error("protection_password_required", "请输入保护密码")
        revision = viewing.verify_password(body.password, _subject(request))
    now = time.time()
    with viewing._connection() as c:
        updated = c.execute("UPDATE viewing_sessions SET mode=?,generation=generation+1,last_activity=?,"
                  " management_until=?,playback_until=0 WHERE token_hash=? AND generation=?"
                  " AND (? IS NULL OR EXISTS(SELECT 1 FROM protection_settings WHERE id=1 AND revision=?))",
                  (body.mode, now, now + viewing.IDLE_SECONDS if needs_password else 0, session["token_hash"], session["generation"],
                   revision, revision))
        if not updated.rowcount:
            viewing.error("protection_password_invalid", "保护设置已更改，请重新验证密码", 401)
    return _result(request, session, secret)


class PasswordBody(BaseModel):
    password: str = Field(max_length=128)


@router.post("/unlock")
def unlock(body: PasswordBody, request: Request, response: Response):
    session, secret = _session(request, response)
    if session["mode"] == "children":
        viewing.error("management_required", "请先退出儿童模式")
    revision = viewing.verify_password(body.password, _subject(request))
    with viewing._connection() as c:
        updated = c.execute("UPDATE viewing_sessions SET management_until=? WHERE token_hash=? AND generation=?"
                  " AND EXISTS(SELECT 1 FROM protection_settings WHERE id=1 AND revision=?)",
                  (time.time() + viewing.IDLE_SECONDS, session["token_hash"], session["generation"], revision))
        if not updated.rowcount:
            viewing.error("protection_password_invalid", "保护设置已更改，请重新验证密码", 401)
    return _result(request, session, secret)


@router.post("/lock")
def lock(request: Request, response: Response):
    session, secret = _session(request, response)
    with viewing._connection() as c:
        c.execute("UPDATE viewing_sessions SET mode=CASE WHEN mode='children' THEN 'children' ELSE 'normal' END,"
                  " management_until=0,playback_until=0,generation=generation+1 WHERE token_hash=?",
                  (session["token_hash"],))
    return _result(request, session, secret)


class ActivityBody(BaseModel):
    user_activity: bool = False
    playing: bool = False


@router.post("/activity")
def activity(body: ActivityBody, request: Request, response: Response):
    session, secret = _session(request, response)
    now = time.time()
    with viewing._connection() as c:
        c.execute("UPDATE viewing_sessions SET last_activity=?,management_until=?,playback_until=? WHERE token_hash=? AND generation=?",
                  (now if body.user_activity or body.playing else session["last_activity"],
                   now + viewing.IDLE_SECONDS if body.user_activity and session["management_until"] > now
                   else session["management_until"],
                   now + 90 if body.playing and session["mode"] == "private" else 0, session["token_hash"], session["generation"]))
    return _result(request, session, secret)


class ChangePasswordBody(BaseModel):
    current_password: str | None = Field(default=None, max_length=128)
    new_password: str = Field(min_length=6, max_length=128)


@router.post("/password")
def change_password(body: ChangePasswordBody, request: Request, response: Response):
    session, secret = _session(request, response)
    from ..store._base import _lock
    with _lock:
        if viewing.enabled():
            revision = viewing.verify_password(body.current_password, _subject(request))
        else:
            with viewing._connection() as c:
                row = c.execute("SELECT password_hash,revision FROM protection_settings WHERE id=1").fetchone()
            if row["password_hash"]:
                viewing.error("protection_password_required", "请验证当前保护密码")
            revision = row["revision"]
        viewing.set_password(body.new_password, expected_revision=revision)
    return _result(request, session, secret)


def _policy(row):
    return {"is_private": bool(row["is_private"]), "children_override": row["children_override"],
            "children_auto_status": row["children_auto_status"], "children_reason": row["children_reason"]}


@router.get("/policy")
def get_policy(request: Request, kind: Literal["movie", "show"], id: int):
    from ..store._base import _conn
    table = "movies" if kind == "movie" else "tv_shows"
    with _conn() as c:
        row = c.execute(f"SELECT * FROM {table} WHERE id=?", (id,)).fetchone()
    if not row:
        viewing.error("not_found", "内容不存在", 404)
    return _policy(row)


class PolicyBody(BaseModel):
    kind: Literal["movie", "show"]
    ids: list[int] = Field(min_length=1, max_length=1000)
    is_private: bool | None = None
    children_override: Literal["auto", "allow", "block"] | None = None


@router.patch("/policy")
def update_policy(body: PolicyBody, request: Request, response: Response):
    session, _ = _session(request, response)
    if not viewing.enabled():
        viewing.error("protection_setup_required", "请先设置保护密码")
    viewing.require_management(session)
    from .. import store
    with viewing.unrestricted():
        ids = set(body.ids)
        if body.kind == "movie":
            ids = {value for values in store.expand_ids_to_versions(body.ids).values() for value in values}
        fields = {}
        if body.is_private is not None:
            fields["is_private"] = int(body.is_private)
        if body.children_override is not None:
            fields["children_override"] = body.children_override
        if fields and ids:
            table = "movies" if body.kind == "movie" else "tv_shows"
            with viewing._connection() as c:
                c.execute(f"UPDATE {table} SET " + ",".join(f"{key}=?" for key in fields)
                          + " WHERE id IN (" + ",".join("?" for _ in ids) + ")", (*fields.values(), *ids))
                c.execute("UPDATE viewing_sessions SET generation=generation+1")
                c.execute("UPDATE protection_settings SET revision=revision+1 WHERE id=1")
            if body.kind == "movie":
                for item_id in ids:
                    store.resync_fts(item_id)
    return {"updated": len(ids)}
