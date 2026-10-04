#!/usr/bin/env bash
# 手动启动入口（宿主直跑）：先保证前端 dist 是最新构建，再起后端同源托管。
# 用法：./start.sh  （端口默认 8080，可 APP_PORT=8080 ./start.sh）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

# 1) 前端：包括品牌资源、配置、锁文件及删除，按内容判断更新。
python3 scripts/build_frontend.py

# 文档独立构建：正文、主题、素材及锁文件变化（包括删除）都会触发。
python3 scripts/build_docs.py

# 2) 后端路径：宿主直跑必须用宿主路径（.env 里是容器内路径 /app/media、/app/data，不能直接用）；
#    TMDB_* 从 .env 取（已导出的环境变量优先）。
export DATA_DIR="${DATA_DIR:-./data}"
export MEDIA_ROOT="${MEDIA_ROOT:-./media}"
if [ -f .env ]; then
  # 全键回读（已导出的环境变量优先）；容器专用键在宿主直跑无意义，跳过（评审 R01-B5）
  while IFS='=' read -r k v; do
    case "$k" in ''|'#'*) continue ;; esac
    case "$k" in MEDIA_HOST_PATH|DATA_HOST_PATH|BUILD_HTTP_PROXY|APP_VERSION|GIT_SHA|UID|GID) continue ;; esac
    if [ -z "${!k:-}" ] && [ -n "$v" ]; then export "$k=$v"; fi
  done < .env
fi
mkdir -p "$MEDIA_ROOT" "$DATA_DIR"

# 2.5) 预检：.venv 与 ffmpeg/ffprobe（在线播放依赖）
if [ ! -x .venv/bin/python ]; then
  echo "[start] 缺少 .venv：先跑 python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt" >&2
  exit 1
fi
FFSTAT="$(".venv/bin/python" -c "from app.media import bin_status; s=bin_status(); print(('ffmpeg' if s['ffmpeg'] else 'no-ffmpeg') + '/' + ('ffprobe' if s['ffprobe'] else 'no-ffprobe') + ('(static待下载)' if (s['static_pkg_installed'] and not (s['static_ffmpeg_present'] or s['system_ffmpeg'])) else ''))" 2>/dev/null || echo "check-failed")"
echo "[start] 转码依赖: $FFSTAT"
case "$FFSTAT" in
  no-ffmpeg*|no-ffprobe*|check-failed)
    echo "[start] 提示：缺 ffmpeg 将导致在线播放不可用（浏览/电视直链不受影响）。修复：.venv/bin/pip install -r requirements.txt（静态版首次播放自动下载，无需 sudo）" >&2
    ;;
esac

# 2.6) 远程库挂载能力（SMB/NFS 应用内挂载；能力不足时改用宿主挂载后登记为本地路径）
MOUNTSTAT="$(".venv/bin/python" -c "from app.mounts import mount_supported; ok, why = mount_supported(); print(('ok' if ok else 'unavailable') + ('' if ok else ': ' + why))" 2>/dev/null || echo "check-failed")"
echo "[start] 远程库挂载: $MOUNTSTAT"

# 3) 启动（前台运行，Ctrl+C 停止）
echo "[start] DATA_DIR=$DATA_DIR MEDIA_ROOT=$MEDIA_ROOT PORT=${APP_PORT:-8080}"
exec .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port "${APP_PORT:-8080}"
