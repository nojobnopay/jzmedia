#!/usr/bin/env bash
# 手动启动入口（宿主直跑）：先保证前端 dist 是最新构建，再起后端同源托管。
# 用法：./start.sh  （端口默认 8080，可 APP_PORT=8080 ./start.sh）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

# 1) 前端：dist 缺失，或 frontend/src 有更新，就重构建
if [ ! -f frontend/dist/index.html ] || [ -n "$(find frontend/src -newer frontend/dist/index.html -print -quit 2>/dev/null)" ]; then
  echo "[start] frontend changed, rebuilding..."
  (cd frontend && { [ -d node_modules ] || npm install; } && npm run build)
else
  echo "[start] frontend dist is fresh, skip build."
fi

# 2) 后端路径：宿主直跑必须用宿主路径（.env 里是容器内路径 /media、/app/data，不能直接用）；
#    TMDB_* 从 .env 取（已导出的环境变量优先）。
export DATA_DIR="${DATA_DIR:-./data}"
export MEDIA_ROOT="${MEDIA_ROOT:-./sample_media}"
if [ -f .env ]; then
  for k in TMDB_API_KEY TMDB_READ_TOKEN TMDB_PROXY TMDB_LANGUAGE APP_PORT; do
    if [ -z "${!k:-}" ]; then
      v="$(grep -E "^${k}=" .env | cut -d= -f2-)"
      [ -n "$v" ] && export "$k=$v"
    fi
  done
fi
mkdir -p "$MEDIA_ROOT" "$DATA_DIR"

# 2.5) 预检：.venv 与 ffmpeg/ffprobe（在线播放依赖）
if [ ! -x .venv/bin/python ]; then
  echo "[start] 缺少 .venv：先跑 python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi
FFSTAT="$(".venv/bin/python" -c "from app.media import bin_status; s=bin_status(); print(('ffmpeg' if s['ffmpeg'] else 'no-ffmpeg') + '/' + ('ffprobe' if s['ffprobe'] else 'no-ffprobe') + ('(static待下载)' if (s['static_pkg_installed'] and not (s['static_ffmpeg_present'] or s['system_ffmpeg'])) else ''))" 2>/dev/null || echo "check-failed")"
echo "[start] 转码依赖: $FFSTAT"
case "$FFSTAT" in
  no-ffmpeg*|no-ffprobe*|check-failed)
    echo "[start] 提示：缺 ffmpeg 将导致在线播放不可用（浏览/电视直链不受影响）。修复：.venv/bin/pip install -r requirements.txt（静态版首次播放自动下载，无需 sudo）" >&2
    ;;
esac

# 3) 启动（前台运行，Ctrl+C 停止）
echo "[start] DATA_DIR=$DATA_DIR MEDIA_ROOT=$MEDIA_ROOT PORT=${APP_PORT:-8080}"
exec .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port "${APP_PORT:-8080}"
