import os

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from . import store
from .config import settings
from .db import POSTER_DIR, ensure_dirs
from .routers import collections, extras, files, fs, health, jobs, movies, persons, stream

app = FastAPI(title="jzmedia", version="0.6.0")

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
        return FileResponse(index)
    return ("<h3>jzmedia api ok</h3><p>前端未构建：进frontend跑 npm run build。"
            "</p><p><a href='/docs'>/docs</a></p>")
