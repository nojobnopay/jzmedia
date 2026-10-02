---
version: 0.19.0
reviewed: 2026-10-02
---

# 安装与首次使用

[本目录首页](README.md) · [配置参考](configuration.md)

以下命令均从项目根目录执行。默认端口为 `8080`；浏览器访问远程服务器时，把 `localhost` 换成服务器地址。

## Docker Compose

镜像由仓库里的 Dockerfile 本地构建，前端构建产物也会打进镜像，无需在 NAS 上另装 Node.js。

1. 将项目源码放到部署目录。首次复制配置；已有配置时不要覆盖：

   ```bash
   cp .env.example .env
   mkdir -p media data
   id -u
   id -g
   ```

2. 编辑 `.env`。下面是示例文件内容；将 UID/GID 改为上一步输出，并确保该用户可写数据目录、可读取媒体目录：

   ```dotenv
   APP_PORT=8080
   APP_VERSION=latest
   MEDIA_HOST_PATH=./media
   DATA_HOST_PATH=./data
   UID=1000
   GID=1000
   TRANSCODER=auto
   ```

   `MEDIA_HOST_PATH` 是宿主目录，容器里对应 `/media`；`DATA_HOST_PATH` 对应 `/app/data`。应用设置里要填容器看得到的路径。需要写 NFO、整理或上传时，还需媒体目录写权限。

3. 启动并检查日志：

   ```bash
   docker compose -f docker-compose.yml up --build -d
   docker compose -f docker-compose.yml ps
   docker compose -f docker-compose.yml logs --tail=100 mymedia
   ```

4. 打开 `http://localhost:8080`，检查 `http://localhost:8080/api/health`。健康 JSON 中查看 `status`、`db`、`media`、`ffmpeg`、`ffprobe`、`transcoder`；容器显示 healthy 不能代替检查这些具体字段。

5. 按本页末尾的首次使用步骤建库。

生产部署始终显式指定 `-f docker-compose.yml`，不加载本机开发用的 override 文件。应用使用内存播放会话和任务状态，不能额外设置多个 uvicorn worker。

## NAS 部署

以具有 Docker/Container Manager 的 NAS 为例：

1. 放置源码，例如 `/volume1/docker/jzmedia`；不带本机 `.env`、缓存、真实数据库或开发 override 文件。
2. 使用 NAS 实际目录配置 `.env`，如媒体 `/volume1/video`、数据 `/volume1/docker/jzmedia/data`。
3. 确认配置的 UID/GID 对这些目录有相应权限；若报 Permission denied，修正目标目录的属主/ACL，不要直接递归修改整块媒体盘的权限。
4. 从 NAS 项目管理界面启动此 Compose 项目，或使用上节命令。
5. 访问 `http://NAS地址:8080`。媒体库类型选「本地路径」，根路径填 `/media`；同一台 NAS 的挂载目录不必再绕行 SMB。

Intel VAAPI/QSV 硬件转码可按 [配置参考](configuration.md) 映射设备。NVENC 在应用中有探测路径，但仓库没有完整 NVIDIA 容器设备配置，需在目标机器另行验证。

## 宿主直接运行

适合 Linux/WSL 开发或不使用 Docker 的环境。仓库 Docker 构建基线为 Python 3.12、Node.js 25；使用其他版本前自行运行项目验证命令。

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
npm ci --prefix frontend
cp .env.example .env
./start.sh
```

已有 `.env` 时跳过复制。系统 FFmpeg/ffprobe 优先；开发依赖提供静态二进制兜底，首次使用可能需要下载。离线设备应提前准备可用二进制。

`start.sh` 会在需要时构建前端，以单进程启动后端；宿主默认使用 `./data`、`./media`，不会直接采用 `.env` 中的容器数据路径。可这样明确覆盖：

```bash
DATA_DIR=./data MEDIA_ROOT=./media APP_PORT=8080 ./start.sh
```

脚本按 `KEY=VALUE` 读取 `.env`，不是完整的 shell 配置解析器。不要通过 `source .env` 导入容器配置。

## 第一次入库与播放

打开空库首页，点击“开始配置”，跟随[首次入库图解](../user-guide/onboarding.md)完成资料来源、目标视频库、扫描或上传，再核对结果并播放。已有内容的安装可从“设置 → 新手配置”进入。

扫描与整理是不同操作。首次试播不要求重命名或移动文件；整理需单独预览与确认。
