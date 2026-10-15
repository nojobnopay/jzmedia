"""HTTP boundary for protected catalogue and artwork access."""
import json
import hashlib
import re
from pathlib import PurePosixPath

from fastapi import HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from . import viewing


def artwork_allowed(path):
    from . import posters
    from .store._base import _conn
    raw = path.removeprefix("/posters/")
    if ".." in PurePosixPath(raw).parts:
        return False
    name = posters.legacy_rel(raw) if "/" not in raw else raw
    name = name or raw
    if name.startswith("tvcast/"):
        return any(name in {f"tvcast/h632_{hashlib.sha1(p.encode()).hexdigest()[:12]}.jpg",
                            f"tvcast/{hashlib.sha1(p.encode()).hexdigest()[:12]}.jpg"}
                   for p in visible_profile_paths())
    with _conn() as c:
        match = re.fullmatch(r"(?:movies|orig)/(\d+)\.\w+|backdrops/movie_(\d+)\.\w+", name)
        if match:
            return c.execute("SELECT 1 FROM movies WHERE tmdb_id=? LIMIT 1", (next(v for v in match.groups() if v),)).fetchone() is not None
        match = re.fullmatch(r"tv/(\d+)(?:_s\d+)?\.\w+|backdrops/tv_(\d+)\.\w+", name)
        if match:
            return c.execute("SELECT 1 FROM tv_shows WHERE tmdb_id=? LIMIT 1", (next(v for v in match.groups() if v),)).fetchone() is not None
        match = re.fullmatch(r"stills/(\d+)\.\w+", name)
        if match:
            return c.execute("SELECT 1 FROM tv_episodes WHERE id=?", (match[1],)).fetchone() is not None
        match = re.fullmatch(r"persons/(\d+)\.\w+", name)
        if match:
            return c.execute("SELECT 1 FROM persons WHERE tmdb_id=?", (match[1],)).fetchone() is not None
        # External posters and user selected images must have a visible owner.
        for table, column in (("movies", "poster_path"), ("tv_shows", "poster_path"),
                              ("tv_shows", "backdrop_path"), ("tv_seasons", "poster_path"),
                              ("tv_episodes", "still_path"), ("persons", "avatar")):
            if c.execute(f"SELECT 1 FROM {table} WHERE {column} IN (?,?,?) LIMIT 1",
                         ("posters/" + name, name, raw)).fetchone():
                return True
    return False


def visible_profile_paths():
    """Profiles can belong to movie cast, whole-show cast, seasons or guests."""
    from .store._base import _conn
    paths = set()
    with _conn() as c:
        paths.update(r[0] for r in c.execute("SELECT profile_tmdb_path FROM persons") if r[0])
        blobs = [r[0] for r in c.execute("SELECT credits FROM tmdb_cache WHERE media_type='tv'"
                                        " AND tmdb_id IN (SELECT tmdb_id FROM tv_shows)")]
        blobs.extend(r[0] for r in c.execute("SELECT cast FROM tv_seasons"))
        blobs.extend(r[0] for r in c.execute("SELECT episode_credits FROM tv_episodes"))
    def collect(value):
        if isinstance(value, dict):
            path = value.get("profile_path") or value.get("profile_tmdb_path")
            if path:
                paths.add(path)
            for item in value.values():
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)
    for blob in blobs:
        try:
            collect(json.loads(blob or "{}"))
        except (TypeError, ValueError):
            pass
    return paths


def _error_response(exc):
    if isinstance(exc.detail, dict) and "code" in exc.detail:
        return JSONResponse({"detail": exc.detail["message"], "code": exc.detail["code"]},
                            status_code=exc.status_code, headers={"Cache-Control": "no-store"})
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


async def middleware(request, call_next):
    path = request.url.path
    if not (path.startswith("/api/") or path.startswith("/posters/")):
        return await call_next(request)
    context_token = None
    try:
        active = await run_in_threadpool(viewing.enabled)
        if active:
            supplied = request.headers.get(viewing.HEADER) or request.cookies.get(viewing.COOKIE, "")
            session, _, expired = await run_in_threadpool(viewing.resolve, supplied)
            if supplied and session is None:
                viewing.error("viewing_locked", "设备会话已失效，请重新连接服务", 401)
            ctx = viewing.context_for(session or {})
            if expired and not path.startswith("/api/viewing/"):
                viewing.error("viewing_locked", "隐私模式已锁定，请重新验证保护密码", 401)
            context_token = viewing.current.set(ctx)
            request.state.viewing_context = ctx
            management = viewing.management_path(path, request.method)
            if request.method == "PATCH" and re.fullmatch(r"/api/movies/\d+", path):
                try:
                    payload = await request.json()
                    if isinstance(payload, dict) and set(payload) == {"watched"}:
                        management = False
                except (ValueError, TypeError):
                    pass
            if request.method == "POST" and path == "/api/movies/batch":
                try:
                    payload = await request.json()
                    if (isinstance(payload, dict) and set(payload) == {"ids", "ops"}
                            and isinstance(payload["ops"], dict) and set(payload["ops"]) == {"watched"}
                            and isinstance(payload["ids"], list) and len(payload["ids"]) <= 1000):
                        for item_id in payload["ids"]:
                            await run_in_threadpool(viewing.visible_item, "movie", int(item_id))
                        management = False
                except (ValueError, TypeError):
                    pass
            if management:
                await run_in_threadpool(viewing.require_management, session)
                viewing.current.set(None)
            if path.startswith("/posters/") and not await run_in_threadpool(artwork_allowed, path):
                raise HTTPException(404, "图片不存在")
        response = await call_next(request)
        if active:
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Viewing-Generation"] = str(ctx.generation)
            response.headers["X-Viewing-Mode"] = ctx.mode
        return response
    except HTTPException as exc:
        return _error_response(exc)
    finally:
        if context_token is not None:
            viewing.current.reset(context_token)


def browse_library_payload(value):
    """The same navigation endpoints have a narrow projection while locked."""
    ctx = viewing.current.get()
    if not ctx or ctx.management:
        return value
    keys = {"id", "name", "kind", "media_library_id", "media_name", "enabled", "effective_enabled",
            "read_only", "sort_order", "movie_count", "episode_count", "show_count", "video_libraries"}
    output = {key: val for key, val in value.items() if key in keys}
    if "video_libraries" in output:
        output["video_libraries"] = [browse_library_payload(item) for item in output["video_libraries"]]
    return output
