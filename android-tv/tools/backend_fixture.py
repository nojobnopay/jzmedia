#!/usr/bin/env python3
"""Run the real FastAPI backend against disposable synthetic TV playback media.

Run from the repository with its Python virtual environment::

    .venv/bin/python android-tv/tools/backend_fixture.py --port 18889

The printed /tmp directory contains fixture-info.json, requests.jsonl,
playback-responses.jsonl and server.pid. Playback response snapshots preserve the
first dynamic playlist and session offsets for investigating native HLS startup.
Use adb reverse tcp:18889 tcp:18889, then connect the APK to http://127.0.0.1:18889.
Stop only this process with SIGTERM; FastAPI then releases its FFmpeg children.
Artifacts remain in /tmp for review. This is an emulator fixture, not Redmi evidence.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time


REPOSITORY = Path(__file__).resolve().parents[2]


def binary(name: str, configured: str | None = None) -> str:
    if configured:
        path = Path(configured).resolve()
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
        raise SystemExit(f"Executable not found: {path}")
    found = shutil.which(name)
    if found:
        return found
    # Reuse a cached dependency without triggering static-ffmpeg downloads.
    import static_ffmpeg
    paths = sorted((Path(static_ffmpeg.__file__).parent / "bin").glob(f"*/{name}"))
    if paths:
        return str(paths[0])
    raise SystemExit(f"Install {name} or provide --{name}; this fixture never downloads binaries")


def create_media(root: Path, ffmpeg: str, seconds: int) -> list[dict]:
    media_root = root / "media"
    direct = media_root / "direct" / "direct.mp4"
    hls = media_root / "multi-audio" / "hls.mkv"
    direct.parent.mkdir(parents=True)
    hls.parent.mkdir(parents=True)
    subprocess.run([
        ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
        "-f", "lavfi", "-i", "smptebars=size=640x360:rate=24",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
        "-t", str(seconds), "-map", "0:v", "-map", "1:a",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "32",
        "-pix_fmt", "yuv420p", "-g", "48", "-threads", "2",
        "-c:a", "aac", "-b:a", "64k", "-metadata:s:a:0", "language=chi",
        "-movflags", "+faststart", str(direct),
    ], check=True, timeout=180)
    # MP2 is deliberately absent from the native client's decoder capability map.
    # Both audio tracks must become independent AAC renditions in real server HLS.
    subprocess.run([
        ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(direct),
        "-f", "lavfi", "-i", "sine=frequency=330:sample_rate=48000",
        "-f", "lavfi", "-i", "sine=frequency=660:sample_rate=48000",
        "-t", str(seconds), "-map", "0:v", "-map", "1:a", "-map", "2:a",
        "-c:v", "copy", "-c:a", "mp2", "-b:a", "96k", "-ac", "2",
        "-metadata:s:a:0", "language=chi", "-metadata:s:a:0", "title=低音测试 330 Hz",
        "-metadata:s:a:1", "language=eng", "-metadata:s:a:1", "title=高音测试 660 Hz",
        "-disposition:a:0", "default", "-disposition:a:1", "0", str(hls),
    ], check=True, timeout=180)

    def stamp(value: int) -> str:
        return f"{value // 3600:02d}:{value // 60 % 60:02d}:{value % 60:02d},000"

    subtitle = "\n\n".join(
        f"{i + 1}\n{stamp(start)} --> {stamp(min(start + 10, seconds))}\n"
        f"真实后端 · 合成字幕 · 源时间 {start} 秒\n音轨一 330 Hz / 音轨二 660 Hz"
        for i, start in enumerate(range(0, seconds, 10))
    ) + "\n"
    hls.with_name("hls.chs.srt").write_text(subtitle, encoding="utf-8")
    direct.with_name("direct.chs.srt").write_text(subtitle, encoding="utf-8")
    return [
        {"path": direct, "title": "01 真实后端：直接播放", "expected": "direct", "poster": 0},
        {"path": hls, "title": "02 真实后端：HLS 双音轨字幕", "expected": "audio_transcode", "poster": 1},
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18889)
    parser.add_argument("--duration", type=int, default=660)
    parser.add_argument("--ffmpeg")
    parser.add_argument("--ffprobe")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535 or not 30 <= args.duration <= 900:
        parser.error("port must be 1024–65535 and duration must be 30–900 seconds")
    ffmpeg, ffprobe = binary("ffmpeg", args.ffmpeg), binary("ffprobe", args.ffprobe)
    root = Path(tempfile.mkdtemp(prefix="jzmedia-tv-backend-", dir="/tmp"))
    print(f"Synthetic backend artifacts: {root}", flush=True)
    # All settings must be assigned before importing ANY app module. The process
    # receives no inherited credentials, proxy configuration or deployment paths.
    inherited_path = os.environ.get("PATH", os.defpath)
    os.environ.clear()
    os.environ.update({
        "PATH": inherited_path, "LANG": "C.UTF-8", "PYTHONUNBUFFERED": "1",
        "DATA_DIR": str(root / "data"), "MEDIA_ROOT": str(root / "media"),
        "TMDB_API_KEY": "", "TMDB_READ_TOKEN": "", "TMDB_PROXY": "",
        "JZMEDIA_TOKEN": "", "AI_ENABLED": "false", "AI_API_KEY": "",
        "ALLOW_SMB_MOUNT": "0", "TRANSCODER": "sw", "MAX_TRANSCODES": "2",
        "HLS_SEGMENT_TYPE": "fmp4", "TRANSCODE_CACHE_GB": "1", "LOG_LEVEL": "INFO",
    })
    items = create_media(root, ffmpeg, args.duration)
    sys.path.insert(0, str(REPOSITORY))
    from app import media, store
    from app.db import ensure_dirs
    from smoke_server import poster_bytes

    media._BIN_CACHE.update({"ffmpeg": ffmpeg, "ffprobe": ffprobe})
    ensure_dirs()
    store.init_db()
    rows = []
    for item in items:
        relative = item["path"].relative_to(root / "media").as_posix()
        mid = store.upsert_movie_by_path(relative)
        poster = f"posters/native_fixture_{mid}.png"
        (root / "data" / poster).write_bytes(poster_bytes(item["poster"]))
        # update_movie_meta performs the required per-movie FTS resynchronization.
        store.update_movie_meta(mid, title=item["title"], year=2026, poster_path=poster,
                                overview="全部媒体由 FFmpeg 合成；真实 FastAPI 接口、播放决策与转码。",
                                genres=["合成演示"], tags=["隔离测试"], needs_review=0,
                                origin_country="CN", region="华语", match_source="manual")
        info = store.upsert_media_info(mid, media.probe(str(item["path"])))
        if not info.get("playable"):
            raise RuntimeError(f"Synthetic media probe failed: {info.get('probe_error')}")
        rows.append({"id": mid, "title": item["title"], "expected_method": item["expected"],
                     "file_path": relative, "duration": info["duration"],
                     "audio": [{"codec": a["codec"], "channels": a["channels"]} for a in info["audio"]]})

    from app.main import app
    import uvicorn

    trace = root / "requests.jsonl"
    playback_trace = root / "playback-responses.jsonl"

    @app.middleware("http")
    async def fixture_trace(request, call_next):
        response = await call_next(request)
        path = request.url.path
        with trace.open("a", encoding="utf-8") as output:
            output.write(json.dumps({"time": time.time(), "method": request.method,
                                     "path": path, "status": response.status_code}) + "\n")
        if path.startswith("/api/stream/") and (
            path.endswith(".m3u8") or (request.method == "POST" and path.endswith("/sessions"))
        ):
            original = response.body_iterator
            recorded_at = time.time()

            async def observe_body():
                captured = bytearray()
                async for chunk in original:
                    encoded = chunk.encode("utf-8") if isinstance(chunk, str) else chunk
                    captured.extend(encoded[:max(0, 65536 - len(captured))])
                    yield chunk
                text = captured.decode("utf-8", errors="replace")
                try:
                    body = json.loads(text)
                except ValueError:
                    body = text
                with playback_trace.open("a", encoding="utf-8") as output:
                    output.write(json.dumps({"time": recorded_at, "path": path,
                                             "status": response.status_code,
                                             "body": body}, ensure_ascii=False) + "\n")

            response.body_iterator = observe_body()
        return response

    manifest = {"pid": os.getpid(), "root": str(root), "base_url": f"http://127.0.0.1:{args.port}",
                "media": rows, "credentials": "none", "tmdb": "disabled", "ai": "disabled",
                "cleanup": f"kill -TERM {os.getpid()} (keeps /tmp artifacts for review)"}
    (root / "fixture-info.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    pid_file = root / "server.pid"
    pid_file.write_text(str(os.getpid()) + "\n")
    print(json.dumps(manifest, ensure_ascii=False), flush=True)

    def exit_after_shutdown(signum, _frame):
        # Uvicorn first handles graceful shutdown, then re-raises the received
        # signal through the previous handler. Preserve finally cleanup here.
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, exit_after_shutdown)
    try:
        uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")
    finally:
        pid_file.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
