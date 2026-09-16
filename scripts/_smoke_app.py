"""L2 smoke 专用启动器：离线确定性（不打 TMDB / 不触 static-ffmpeg 下载）。

仅供 scripts/smoke_api.py 子进程使用，不参与生产运行。
"""
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> None:
    import uvicorn

    from app import media, tmdb

    # 把二进制解析钉死在 PATH 查找（失败快速返回），禁止触发 static-ffmpeg 下载
    media._BIN_CACHE["ffmpeg"] = "ffmpeg"
    media._BIN_CACHE["ffprobe"] = "ffprobe"

    # 离线 TMDB：扫描不触网
    tmdb.search_movie = lambda q, year=None: []
    tmdb.movie_detail = lambda tmdb_id: (_ for _ in ()).throw(RuntimeError("smoke offline"))

    from app.main import app

    port = int(os.environ.get("SMOKE_PORT", "18099"))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
