import hmac
import os
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config, store
from .config import settings
from .db import POSTER_DIR, ensure_dirs
from .log import get_logger, setup_logging
from .routers import collections, extras, files, fs, health, jobs, movies, persons, stream

setup_logging()
_logger = get_logger("main")


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    # 后台预热转码后端探测（冒烟编码最多几秒，不阻塞启动；首次 decide/health 即命中缓存）
    from . import transcode as _tr
    threading.Thread(target=_tr.detect, daemon=True).start()
    _logger.info("jzmedia %s 启动：MEDIA_ROOT=%s DATA_DIR=%s ENV=%s",
                 app.version, settings.media_root, settings.data_dir, settings.env)
    yield
    # 优雅退出：杀掉全部转码进程（防重启/停服后孤儿 ffmpeg 继续烧 CPU 写分片）
    stream.shutdown_sessions()
    _logger.info("jzmedia 已停止")


app = FastAPI(title="jzmedia", version="0.7.0", lifespan=_lifespan)

# 写操作访问令牌（评审 P1-01）：仅当 JZMEDIA_TOKEN/设置页配置了令牌才生效。
# 只护 /api 的写方法（POST/PUT/PATCH/DELETE）；GET 全放行（Kodi/电视直链、海报、
# HLS 分片读取都免鉴权）。令牌来自 config 的 DB 优先/env 兜底机制，改后免重启。
_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _request_token(request) -> str:
    got = (request.headers.get("x-api-token") or "").strip()
    if got:
        return got
    auth = (request.headers.get("authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


@app.middleware("http")
async def _auth_write(request, call_next):
    if request.method in _WRITE_METHODS and request.url.path.startswith("/api"):
        token = await run_in_threadpool(config.effective_jzmedia_token)
        if token and not hmac.compare_digest(_request_token(request), token):
            return JSONResponse(
                {"detail": "unauthorized: 需要访问令牌（设置页 → 访问控制）"},
                status_code=401)
    return await call_next(request)

ensure_dirs()
store.init_db()
app.include_router(health.router)
app.include_router(movies.router)
app.include_router(collections.router)
app.include_router(files.router)
app.include_router(fs.router)
app.include_router(extras.router)
app.include_router(jobs.router)
app.include_router(persons.router)
app.include_router(stream.router)
app.mount("/posters", StaticFiles(directory=POSTER_DIR), name="posters")

DIST = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "frontend", "dist"))
ASSETS = os.path.join(DIST, "assets")
if os.path.isdir(ASSETS):
    app.mount("/assets", StaticFiles(directory=ASSETS), name="assets")


@app.get("/{full_path:path}", response_class=HTMLResponse)
def spa(full_path: str):
    # vite public/* 落到 dist 根目录（favicon / 图标）：存在即直出，否则回退 SPA
    if full_path:
        candidate = os.path.normpath(os.path.join(DIST, full_path))
        if candidate.startswith(DIST) and os.path.isfile(candidate):
            return FileResponse(candidate)
    index = os.path.join(DIST, "index.html")
    if os.path.exists(index):
        # 入口永不缓存：带哈希的 /assets 天然防旧，index.html 必须每次最新，
        # 否则浏览器攥着旧入口引用不存在的旧包（发版后“修了像没修”）。
        return FileResponse(index, headers={"Cache-Control": "no-store"})
    return ("<h3>jzmedia api ok</h3><p>前端未构建：进frontend跑 npm run build。"
            "</p><p><a href='/docs'>/docs</a></p>")
