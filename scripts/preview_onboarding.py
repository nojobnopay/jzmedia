#!/usr/bin/env python3
"""启动独立的新手向导演示，每次运行都是新数据库，不读取项目 .env。

运行：.venv/bin/python scripts/preview_onboarding.py [--port 18080]
Ctrl+C 停止；临时文件保留在打印的 /tmp/jzmedia-preview-* 目录。
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]


def installed_binary(name):
    """仅使用已安装的二进制，不触发 static-ffmpeg 下载。"""
    found = shutil.which(name)
    if found:
        return Path(found)
    spec = importlib.util.find_spec("static_ffmpeg")
    if spec and spec.origin:
        for candidate in (Path(spec.origin).parent / "bin").glob(f"*/{name}"):
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return candidate
    raise SystemExit(f"缺少已安装的 {name}；请使用项目 .venv，并先准备 FFmpeg。")


def prepare_help_preview(runtime):
    """复制已有静态帮助产物；缺产物时保留可读的旧入口，不启动构建。"""
    gallery = ROOT / "docs/assets/previews/onboarding"
    preview_entry = runtime / "frontend/dist/preview"
    if gallery.is_dir():
        shutil.copytree(gallery, preview_entry)
    else:
        preview_entry.mkdir(parents=True, exist_ok=True)
    help_dist = ROOT / "docs/.vitepress/dist"
    required = ("index.html", "user-guide/onboarding.html", "csp-hashes.json")
    if all((help_dist / name).is_file() for name in required):
        shutil.copytree(help_dist, runtime / "docs/.vitepress/dist",
                        ignore=lambda directory, names: [
                            name for name in names if name.startswith(".")
                            or (Path(directory) / name).is_symlink()])
        return True
    # 只替换临时副本；仓库旧链接仍指向正式帮助站。
    (preview_entry / "index.html").write_text(
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>新手图解尚未构建 · JZMedia</title></head><body>'
        '<h1>新手图解尚未构建</h1>'
        '<p>本次隔离演示可以正常使用；图解教程需要先生成帮助站。</p>'
        '<p>在项目根目录运行：</p>'
        '<pre><code>npm --prefix docs ci\n'
        'npm --prefix docs run build</code></pre>'
        '<p>构建完成后重新启动此演示脚本，即可在这里打开完整教程。</p>'
        '<p><a href="/setup">继续体验四步配置</a> · '
        '<a href="/">返回演示欢迎页</a></p></body></html>\n', encoding="utf-8")
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18080)
    parser.add_argument("--docs-demo", action="store_true",
                        help="拍摄用：180 秒合成视频、双语外挂字幕和虚构离线匹配缓存")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("端口必须在 1024–65535 之间")
    # 不终止或替换任何现有服务；端口占用时直接退出。
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", args.port))
    vite = ROOT / "frontend/node_modules/vite/bin/vite.js"
    node = shutil.which("node")
    if not vite.is_file() or not node:
        raise SystemExit("请先安装前端依赖（frontend/npm install）。")
    ffmpeg = installed_binary("ffmpeg")
    ffprobe = installed_binary("ffprobe")
    preview = Path(tempfile.mkdtemp(prefix="jzmedia-preview-", dir="/tmp"))
    runtime = preview / "runtime"
    media = preview / "media"
    data = preview / "data"
    movie = media / ("待归档演示" if args.docs_demo else "星海漫游 (2026)")
    movie.mkdir(parents=True)
    data.mkdir()
    # 复制源码与独立构建产物，主实例的 frontend/dist 也不被改写。
    shutil.copytree(ROOT / "app", runtime / "app",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    # 仅此临时副本跳过扫描时的远程搜索；UI、目录检查、NFO 扫描与入库仍走真实代码。
    (runtime / "preview_app.py").write_text(
        "from app import tmdb\n"
        "tmdb.search_movie = lambda *args, **kwargs: []\n"
        "tmdb.search_tv = lambda *args, **kwargs: []\n"
        "tmdb.movie_similar = lambda *args, **kwargs: []\n"
        "from app.main import app\n", encoding="utf-8")
    env = {key: os.environ[key] for key in (
        "PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "SYSTEMROOT") if key in os.environ}
    env.update(DATA_DIR=str(data), MEDIA_ROOT=str(media),
               ALLOW_SMB_MOUNT="0", TRANSCODER="sw", ENV="preview",
               PYTHONDONTWRITEBYTECODE="1", PYTHONUNBUFFERED="1")
    env["PATH"] = os.pathsep.join((str(ffmpeg.parent), str(ffprobe.parent),
                                  env.get("PATH", os.defpath)))
    (preview / "preview.json").write_text(json.dumps({
        "url": f"http://localhost:{args.port}", "data": str(data),
        "media": str(media), "runtime": str(runtime),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"演示目录：{preview}\n正在构建独立前端…", flush=True)
    with (preview / "build.log").open("w") as log:
        result = subprocess.run([node, str(vite), "build", "--mode", "preview",
                                 "--outDir", str(runtime / "frontend/dist")],
                                cwd=ROOT / "frontend", env=env, stdout=log,
                                stderr=subprocess.STDOUT)
    if result.returncode:
        raise SystemExit(f"构建失败，详见 {preview / 'build.log'}")
    help_ready = prepare_help_preview(runtime)
    subprocess.run([
        str(ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin",
        "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=24",
        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", "180" if args.docs_demo else "6",
        "-c:v", "libx264", "-preset", "ultrafast", "-threads", "1",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart",
        str(movie / ("Star.Voyage.2026.mp4" if args.docs_demo else "星海漫游 (2026).mp4")),
    ], env=env, check=True)
    (movie / "movie.nfo").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<movie><title>星海漫游（演示短片）</title><year>2026</year>'
        + ('<uniqueid type="tmdb" default="true">999900001</uniqueid>' if args.docs_demo else '')
        + '<plot>这是一段自动生成的测试图案，用于体验扫描、离线 NFO 入库和播放。'
        '影片资料为虚构演示内容。</plot><genre>演示</genre></movie>\n',
        encoding="utf-8")
    if args.docs_demo:
        for lang, line in (("chs", "星海漫游 · 中文外挂字幕演示"), ("eng", "Star Voyage · English subtitle demo")):
            (movie / f"Star.Voyage.2026.{lang}.srt").write_text(
                f"1\n00:00:00,000 --> 00:03:00,000\n{line}\n", encoding="utf-8")
        (preview / "本地临时字幕.srt").write_text(
            "1\n00:00:00,000 --> 00:03:00,000\n本地字幕已加载 · 仅本次播放有效\n", encoding="utf-8")
    # 只预设临时存储位置，不登记电影、不推进引导；用户可从欢迎页真实走完流程。
    subprocess.run([sys.executable, "-c", """
from app import store
from app.db import ensure_dirs
ensure_dirs()
store.init_db()
lib = store.default_library()
store.update_media_library(lib['media_library_id'], name='隔离演示 · 临时数据')
store.update_library(lib['id'], name='演示电影', metadata_providers='["local"]')
""" + ("""
store.upsert_tmdb_cache(999900001, {
    'title': '星海漫游（演示短片）', 'original_title': 'Star Voyage', 'year': 2026,
    'overview': '虚构演示短片，由 FFmpeg 生成。用于练习扫描、字幕和整理；无真实影视素材。',
    'genres': ['演示'], 'genre_ids': [], 'origin_countries': [],
})
""" if args.docs_demo else "") + """
"""], cwd=runtime, env=env, check=True)
    help_message = (f"帮助站：http://localhost:{args.port}/help/\n" if help_ready else
                    "帮助站尚未构建；截图导览入口显示构建方法，四步配置仍可体验。\n")
    print(f"\n欢迎页：http://localhost:{args.port}/\n"
          f"新手向导：http://localhost:{args.port}/setup\n"
          f"截图导览：http://localhost:{args.port}/preview/index.html\n"
          f"{help_message}"
          "体验顺序：开始配置 → 稍后配置 TMDB → 检查并使用此视频库 → 扫描 → 查看结果。\n"
          "演示使用本地 NFO，临时副本的 TMDB 电影/剧集搜索已停用。\n"
          f"数据库：{data / 'jzmedia.db'}\n"
          f"示例视频：{movie}\n"
          "请在演示库内体验；若手动添加真实目录，该目录仍可能被扫描或写入。\n"
          "Ctrl+C 停止。再次运行会创建全新的演示；原测试数据库和 .env 未读取。\n",
          flush=True)
    # exec 使 Ctrl+C 由 uvicorn 正常回收后台任务，无额外常驻启动器。
    os.chdir(runtime)
    os.execve(sys.executable, [sys.executable, "-m", "uvicorn", "preview_app:app",
                              "--host", "127.0.0.1", "--port", str(args.port)], env)


if __name__ == "__main__":
    main()
