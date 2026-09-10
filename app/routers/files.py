"""文件整理：默认dry_run预览，确认后执行。模板：同级目录下 电影名 (年份)/电影名 (年份).ext"""
import os

from fastapi import APIRouter

from .. import store
from ..config import settings
from ..nfo import write_movie_nfo

router = APIRouter(prefix="/api/files")


def _plan_one(m: dict) -> dict | None:
    if not m.get("title") or not m.get("year"):
        return None
    ext = os.path.splitext(m["file_path"])[1]
    base = f"{m['title']} ({m['year']})"
    parent = os.path.dirname(m["file_path"])
    # 若已在同名目录且同名文件则跳过
    new_rel = os.path.join(parent, base, base + ext)
    if os.path.normpath(new_rel) == os.path.normpath(m["file_path"]):
        return None
    return {"id": m["id"], "from": m["file_path"], "to": new_rel}


@router.get("/preview")
def preview():
    plans = [p for m in store.list_movies() if (p := _plan_one(m))]
    return {"plans": plans}


@router.post("/rename")
def rename(body: dict | None = None):
    body = body or {}
    dry_run = body.get("dry_run", True)
    only = set(body.get("ids", []) or [])
    plans = [p for m in store.list_movies()
             if (not only or m["id"] in only) and (p := _plan_one(m))]
    if dry_run:
        return {"dry_run": True, "plans": plans}
    done = []
    for p in plans:
        src = os.path.join(settings.media_root, p["from"])
        dst = os.path.join(settings.media_root, p["to"])
        if not os.path.exists(src):
            done.append({**p, "status": "skipped_missing_src"})
            continue
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            os.rename(src, dst)
            # 本地写：只改 file_path，不碰 TMDB 镜像列
            store.update_movie_local(p["id"], file_path=p["to"])
            movie = store.get_movie(p["id"])
            write_movie_nfo(movie, os.path.join(os.path.dirname(dst), "movie.nfo"))
            # 旧目录无视频文件时清掉残留movie.nfo
            old_dir = os.path.dirname(src)
            if not any(os.path.splitext(f)[1].lower() in
                       {".mkv", ".mp4", ".avi", ".ts", ".m2ts", ".mov", ".wmv", ".flv", ".webm"}
                       for f in os.listdir(old_dir)):
                old_nfo = os.path.join(old_dir, "movie.nfo")
                if os.path.exists(old_nfo):
                    os.remove(old_nfo)
            done.append({**p, "status": "moved"})
        except Exception as e:
            done.append({**p, "status": f"error: {e}"})
    return {"dry_run": False, "results": done}
