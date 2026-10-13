"""Internal isolated backend for smoke_casting.mjs; no real library or LAN discovery."""
import argparse
from contextlib import asynccontextmanager
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    if not str(root).startswith("/tmp/jzmedia-cast-ui-"):
        parser.error("This fixture requires its own /tmp/jzmedia-cast-ui-* directory")
    os.environ.update(DATA_DIR=str(root / "data"), MEDIA_ROOT=str(root / "media"),
                      TMDB_READ_TOKEN="", TMDB_API_KEY="", JZMEDIA_TOKEN="", TRANSCODER="sw")
    os.environ.pop("CAST_BASE_URL", None)
    from scripts.preview_onboarding import installed_binary
    ffmpeg, ffprobe = installed_binary("ffmpeg"), installed_binary("ffprobe")
    os.environ["PATH"] = os.pathsep.join((str(ffmpeg.parent), str(ffprobe.parent), os.environ.get("PATH", "")))
    media = root / "media"
    media.mkdir(exist_ok=True)
    filename = "合成电影 空格%&#.mp4"
    subprocess.run([str(ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                    "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=10", "-f", "lavfi", "-i",
                    "sine=frequency=440:sample_rate=44100", "-f", "lavfi", "-i",
                    "sine=frequency=660:sample_rate=44100", "-map", "0:v", "-map", "1:a", "-map", "2:a",
                    "-t", "90", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-metadata:s:a:0", "language=chi", "-metadata:s:a:1", "language=eng",
                    "-disposition:a:0", "default", "-disposition:a:1", "0",
                    "-movflags", "+faststart", str(media / filename)], check=True)
    (media / f"{Path(filename).stem}.chs.srt").write_text(
        "1\n00:00:00,000 --> 00:01:30,000\n仅用于播放器快捷面板验证的合成字幕\n", encoding="utf-8")
    from fastapi import FastAPI
    from fastapi.staticfiles import StaticFiles
    from app import library_paths, store
    from app.db import ensure_dirs
    from app.casting import dlna
    from app.casting.service import manager
    from app.routers import cast, movies, stream, tv
    from scripts.fixtures.dlna_receiver import DlnaReceiver
    ensure_dirs()
    store.init_db()
    library = library_paths.default_library()["id"]
    movie_id = store.upsert_movie_by_path(filename, library_id=library)
    store.update_movie_meta(movie_id, title="合成电影：中文路径 & 投屏验证")
    show = store.upsert_show(library, "合成剧集")
    episode_id = store.upsert_episode(show, library, filename, 1, 1, title="合成第一集")
    receiver = DlnaReceiver(fetch_media=True)
    receiver.__enter__()
    # Only this isolated process permits loopback receivers/media addresses.
    original = dlna.lan_ip
    dlna.lan_ip = lambda value: value == "127.0.0.1" or original(value)
    mode = {"discovery": "normal"}

    def discover():
        if mode["discovery"] == "empty":
            return []
        if mode["discovery"] == "error":
            raise dlna.CastError("模拟组播网络不可用，请重试")
        return [dlna.describe(receiver.location, "127.0.0.1")]

    dlna.discover = discover

    @asynccontextmanager
    async def lifespan(_app):
        yield
        manager.shutdown()
        stream.shutdown_sessions()
        receiver.__exit__()

    app = FastAPI(lifespan=lifespan)
    for router in (cast.router, movies.router, stream.router, tv.router):
        app.include_router(router)

    @app.get("/__fixture/info")
    def info():
        return {"movie_id": movie_id, "episode_id": episode_id, "calls": receiver.calls,
                "fetched": len(receiver.fetched), "errors": receiver.fetch_errors, "state": receiver.state}

    @app.post("/__fixture/mode")
    def set_mode(body: dict):
        mode.update(body)
        if "reject" in body:
            receiver.reject = set(body["reject"])
        if "position" in body:
            receiver.position = body["position"]
        manager.searched_at = 0
        return {"ok": True}

    app.mount("/", StaticFiles(directory=root / "dist", html=True))
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
