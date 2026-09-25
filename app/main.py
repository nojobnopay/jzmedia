import hmac
import os
import re
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
from .routers import (collections, extras, files, fs, health, jobs, libraries,
                      media_libraries, metadata, movies, persons, stream, tv)

setup_logging()
_logger = get_logger("main")


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    # 启动初始化（评审 R01-Q1）：此前在模块导入期执行，测试无法隔离/导入即写盘
    ensure_dirs()
    store.init_db()
    # posters 子目录迁移（根部旧文件 → 功能子目录 + DB 旧值改写；幂等可重入）
    try:
        from . import posters as _posters
        _posters.migrate_posters()
    except Exception as e:
        _logger.warning("poster migrate at startup failed: %s", e)
    # 后台预热转码后端探测（冒烟编码最多几秒，不阻塞启动；首次 decide/health 即命中缓存）
    from . import transcode as _tr
    try:
        killed = stream._reap_orphans()   # 上次崩溃/SIGKILL 留下的孤儿 ffmpeg（评审 R12-Q3）
        if killed:
            _logger.warning("启动清理孤儿转码进程 %s 个", killed)
    except Exception as e:
        _logger.warning("reap orphans failed: %s", e)
    threading.Thread(target=_tr.detect, daemon=True).start()
    # 远程库挂载看门狗（C 阶段）：启动重挂 + 60s 巡检（ALLOW_SMB_MOUNT=0 可禁用）
    try:
        from . import mounts as _mounts
        _mounts.start_watchdog()
    except Exception as e:
        _logger.warning("start mount watchdog failed: %s", e)
    try:
        from . import library_paths
        library_paths.invalidate_cache()
        medias = store.list_media_libraries()
        parts = []
        for m in medias:
            vids = store.video_libraries_of(m["id"])
            vdesc = "/".join(f"{v['name']}:{v['kind']}" for v in vids) or "无视频库"
            parts.append(f"{m['id']}:{m['name']}({m['source']})[{vdesc}]")
        lib_desc = ", ".join(parts) or "无媒体库（请在设置页建库）"
    except Exception as e:
        _logger.warning("list libraries at startup failed: %s", e)
        lib_desc = "unavailable"
    _logger.info("jzmedia %s 启动：media=[%s] DATA_DIR=%s ENV=%s",
                 app.version, lib_desc, settings.data_dir, settings.env)
    yield
    # 优雅退出：杀掉全部转码进程（防重启/停服后孤儿 ffmpeg 继续烧 CPU 写分片）
    stream.shutdown_sessions()
    try:
        from . import mounts as _mounts
        _mounts.shutdown_mounts()
    except Exception as e:
        _logger.warning("shutdown mounts failed: %s", e)
    _logger.info("jzmedia 已停止")


app = FastAPI(title="jzmedia", version="0.18.0", lifespan=_lifespan)

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

app.include_router(health.router)
app.include_router(media_libraries.router)
app.include_router(libraries.router)
app.include_router(movies.router)
app.include_router(metadata.router)
app.include_router(collections.router)
app.include_router(files.router)
app.include_router(fs.router)
app.include_router(extras.router)
app.include_router(jobs.router)
app.include_router(persons.router)
app.include_router(stream.router)
app.include_router(tv.router)
# check_dir=False：目录由 lifespan ensure_dirs 创建，导入期不再有副作用（评审 R01-Q1）
app.mount("/posters", StaticFiles(directory=POSTER_DIR, check_dir=False), name="posters")

DIST = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "frontend", "dist"))
ASSETS = os.path.join(DIST, "assets")
if os.path.isdir(ASSETS):
    app.mount("/assets", StaticFiles(directory=ASSETS), name="assets")


# 安全响应头（评审 B6/R01-B8+R14-B5）：CSP 需放行自绘字幕/worker/wasm 场景；
# 出问题时可用 JZMEDIA_CSP=off 应急关闭（其余头保留）
_CSP = ("default-src 'self'; "
        "script-src 'self' 'wasm-unsafe-eval' blob:; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; "
        "media-src 'self' blob:; "
        "connect-src 'self' blob:; "
        "font-src 'self' data: blob:; "
        "worker-src 'self' blob:; "
        "frame-src 'self' blob:; "
        "object-src 'none'; base-uri 'self'; form-action 'self'")


# 可变海报文件（换海报会原地覆盖，URL 不变）：
# StaticFiles 默认不发 Cache-Control，浏览器启发式缓存会一直用旧图 → 强制重新验证
# （ETag/Last-Modified 未变则 304，变了则 200）。cand/（hash 命名）与头像/tv/背景不受影响。
_POSTER_MUTABLE_RE = re.compile(r"^/posters/(?:movies|orig)/\d+\.(?:jpg|jpeg|png)$")


@app.middleware("http")
async def _security_headers(request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    if _POSTER_MUTABLE_RE.match(request.url.path):
        resp.headers.setdefault("Cache-Control", "no-cache")
    if os.getenv("JZMEDIA_CSP", "").strip().lower() not in ("off", "0", "no"):
        resp.headers.setdefault("Content-Security-Policy", _CSP)
    return resp


def _spa_file(dist: str, full_path: str) -> str | None:
    """SPA 静态文件定位（纯函数，便于测试）：只在 dist 目录内取文件。
    评审 B6/R01-B2：此前用 startswith(dist) 前缀判断，`../dist-x/a` 归一后仍能绕过
    （同级且以 dist 开头的目录可被读到）→ 改为路径边界判断。"""
    if not full_path:
        return None
    try:
        candidate = os.path.normpath(os.path.join(dist, full_path))
    except (OSError, ValueError):
        return None
    if candidate != dist and not candidate.startswith(dist + os.sep):
        return None
    return candidate if os.path.isfile(candidate) else None


@app.get("/{full_path:path}", response_class=HTMLResponse)
def spa(full_path: str):
    # 未知 /api 路径返回 JSON 404（评审 B6/R01-D3）：否则会被 SPA catch-all 当成页面
    # 返回 200 HTML，API 客户端（含调试脚本）分不清“没有这个接口”和“接口出错”。
    if (full_path or "").startswith("api/"):
        return JSONResponse({"detail": "not found"}, status_code=404)
    # vite public/* 落到 dist 根目录（favicon / 图标）：存在即直出，否则回退 SPA
    hit = _spa_file(DIST, full_path)
    if hit:
        return FileResponse(hit)
    index = os.path.join(DIST, "index.html")
    if os.path.exists(index):
        # 入口永不缓存：带哈希的 /assets 天然防旧，index.html 必须每次最新，
        # 否则浏览器攥着旧入口引用不存在的旧包（发版后“修了像没修”）。
        return FileResponse(index, headers={"Cache-Control": "no-store"})
    return ("<h3>jzmedia api ok</h3><p>前端未构建：进frontend跑 npm run build。"
            "</p><p><a href='/docs'>/docs</a></p>")
