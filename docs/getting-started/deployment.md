---
version: 0.20.0
reviewed: 2026-10-04
---

# 安装与首次使用

[本目录首页](README.md) · [配置参考](configuration.md)

以下命令均从项目根目录执行。默认端口为 `8080`；浏览器访问远程服务器时，把 `localhost` 换成服务器地址。

选择一种安装方式：[Docker Compose](#docker-compose) · [NAS](#nas-部署) · [Linux / WSL 直接运行](#宿主直接运行)。服务启动后再进行[第一次入库](#第一次入库与播放)。

## 安装前准备

- 能运行 Docker Compose 的家用电脑或 NAS；直接运行源码则需 Linux/WSL、Python、Node.js 与 FFmpeg。
- 一个可读取的媒体目录和一个可写的数据目录。仅浏览和播放时可将媒体库设为只读。
- 能访问服务器的浏览器。转码能力取决于片源、CPU/GPU 和网络。
- 可先使用已有 NFO 与本地资料，无需为启动服务提前申请 TMDB 凭据；资料来源在[首次配置](../user-guide/onboarding.md)中选择。

## Docker Compose

镜像由仓库里的 Dockerfile 本地构建，前端与帮助站的静态产物都会打进镜像，无需在 NAS 上另装 Node.js。

### Step 1：准备源码与配置文件

将项目源码放到部署目录，在终端进入这个目录。首次复制配置；已有 `.env` 时跳过复制，不要覆盖：

```bash
cp .env.example .env
mkdir -p media data
id -u
id -g
```

**预期结果：**项目目录内有 `.env`、`media/` 和 `data/`；最后两条命令分别打印当前用户的 UID、GID，下一步会用到。

<span id="docker-paths"></span>

### Step 2：填写媒体路径与目录权限

编辑 `.env` 中对应的项目。下面是示例，将 UID/GID 改为上一步输出，并确保该用户可写数据目录、可读取媒体目录：

```dotenv
APP_PORT=8080
APP_VERSION=latest
MEDIA_HOST_PATH=./media
DATA_HOST_PATH=./data
UID=1000
GID=1000
TRANSCODER=auto
```

`MEDIA_HOST_PATH` 是宿主目录，容器里对应 `/app/media`；`DATA_HOST_PATH` 对应 `/app/data`。应用设置里要填容器看得到的路径。需要写 NFO、整理或上传时，还需媒体目录写权限。已有部署若将影片登记在 `/media` 下，启动前先按[保留旧媒体路径](#legacy-media-path)配置。

### Step 3：构建并启动服务

```bash
docker compose -f docker-compose.yml up --build -d
docker compose -f docker-compose.yml ps
docker compose -f docker-compose.yml logs --tail=100 mymedia
```

**预期结果：**`mymedia` 容器处于运行状态，日志没有持续重启或权限错误。构建或启动失败时，按[部署排障](troubleshooting.md)检查后重试本步。

生产部署始终显式指定 `-f docker-compose.yml`，不加载本机开发用的 override 文件。应用使用内存播放会话和任务状态，不能额外设置多个 uvicorn worker。

<span id="check-service"></span>

### Step 4：打开网页并检查服务

打开 `http://localhost:8080`，再查看 `http://localhost:8080/api/health`。健康 JSON 中检查 `status`、`db`、`media`、`ffmpeg`、`ffprobe`、`transcoder`；容器显示 healthy 不能代替检查这些具体字段。

**预期结果：**能看到应用首页，数据库与媒体检查正常，播放所需工具可用。随后按[第一次入库与播放](#第一次入库与播放)建库。

若设置 `JZMEDIA_TOKEN`，网页需填写令牌才能提交播放决策、创建播放会话、保存进度以及执行管理操作；页面浏览和媒体直链等 GET 仍开放。令牌保护不能代替完整的读取访问控制。

<span id="legacy-media-path"></span>

### 已有部署保留旧媒体路径

修改 `MEDIA_ROOT` 不会改写数据库中已登记的媒体库和视频库路径；已有影片或剧集记录的媒体库也不能在设置页直接改根路径。若原库使用 `/media`，升级时先[备份数据](operations.md#一次一致的备份)，保留同一个宿主媒体目录与 `/media` 的映射，无需重新建库或扫描。

在项目根创建 `compose.media-legacy.yml`，保留 `.env` 中原来的 `MEDIA_HOST_PATH`、`DATA_HOST_PATH`：

```yaml
services:
  mymedia:
    environment:
      MEDIA_ROOT: /media
    volumes: !override
      - "${MEDIA_HOST_PATH:-./media}:/media:rw"
      - "${DATA_HOST_PATH:-./data}:/app/data"
```

`!override` 完整替换卷列表，避免新的 `/app/media` 映射一并合入；已有额外映射需一并列入。此写法需要 Docker Compose 2.24.4 或更新版本，见 [Docker 合并规则](https://docs.docker.com/reference/compose-file/merge/#replace-value)。随后执行：

```bash
docker compose -f docker-compose.yml -f compose.media-legacy.yml config --quiet
docker compose -f docker-compose.yml -f compose.media-legacy.yml up --build -d
```

**预期结果：**原媒体仍在容器 `/media` 下可见，已有库的连接检查与播放正常。后续管理该实例继续使用相同的两个 `-f` 参数；若配置检查提示不支持 `!override`，先升级 Compose，不要直接删掉该标记。

## NAS 部署

以具有 Docker/Container Manager 的 NAS 为例：

1. 放置源码，例如 `/volume1/docker/jzmedia`；不带本机 `.env`、缓存、真实数据库或开发 override 文件。
2. 使用 NAS 实际目录配置 `.env`，如媒体 `/volume1/video`、数据 `/volume1/docker/jzmedia/data`。
3. 确认配置的 UID/GID 对这些目录有相应权限；若报 Permission denied，修正目标目录的属主/ACL，不要直接递归修改整块媒体盘的权限。
4. 从 NAS 项目管理界面启动此 Compose 项目，或使用上节命令。
5. 访问 `http://NAS地址:8080`。媒体库类型选「本地路径」，新部署默认根路径填 `/app/media`；同一台 NAS 的挂载目录不必再绕行 SMB。

Intel VAAPI/QSV 硬件转码可按 [配置参考](configuration.md) 映射设备。NVENC 在应用中有探测路径，但仓库没有完整 NVIDIA 容器设备配置，需在目标机器另行验证。

## 宿主直接运行

适合 Linux/WSL 开发或不使用 Docker 的环境。仓库 Docker 构建基线为 Python 3.12、Node.js 25；使用其他版本前自行运行项目验证命令。

### Step 1：安装开发依赖

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
npm ci --prefix frontend
```

系统 FFmpeg/ffprobe 优先；开发依赖提供静态二进制兜底，首次使用可能需要下载。离线设备应提前准备可用二进制。

### Step 2：准备配置并启动

首次运行复制配置；已有 `.env` 时跳过第一条命令：

```bash
cp .env.example .env
./start.sh
```

**预期结果：**脚本完成所需构建并打印服务地址；保持终端运行，按 `Ctrl+C` 停止。打开首页与 `/api/health`，按上面 [Docker 的服务检查步骤](#check-service)核对结果，再进行首次入库。

### 启动脚本与路径说明

`start.sh` 通过 `scripts/build_frontend.py`、`scripts/build_docs.py` 检查源码、静态资源、配置和锁文件的内容摘要，包含新增与删除；输入变化才构建，依赖未准备或锁文件变化时执行相应 `npm ci`。前端品牌素材和帮助站正文/主题/素材都在检查范围内，私有文档、计划与历史报告不触发帮助站构建。随后以单进程启动后端；宿主默认使用 `./data`、`./media`，不会直接采用 `.env` 中的容器数据路径。可这样明确覆盖：

```bash
DATA_DIR=./data MEDIA_ROOT=./media APP_PORT=8080 ./start.sh
```

脚本按 `KEY=VALUE` 读取 `.env`，不是完整的 shell 配置解析器；使用模板中的无引号单行值，不写 shell 展开或行尾注释。已导出的非空环境变量优先；空值可能被 `.env` 中的非空值补回。不要通过 `source .env` 导入容器配置。

## Android TV 客户端

服务器启动后可供网页与独立 Android TV 客户端共用。镜像与 APK 通过[统一发布](../developer/releasing.md)从同一提交同步构建；普通 Docker Compose 或 `start.sh` 部署不会另外生成 APK。客户端安装、开发构建与产物位置见仓库 `android-tv/README.md`。电视连接服务器地址，通过协议 1 握手，并在实例启用访问令牌时单独验证写权限。电视真机的解码、HDR、音轨及遥控器行为仍需按实际设备验收。

## 第一次入库与播放

打开空库首页，点击“开始配置”，跟随[首次入库图解](../user-guide/onboarding.md)完成资料来源、目标视频库、扫描或上传，再核对结果并播放。已有内容的安装可从“设置 → 新手配置”进入。

扫描与整理是不同操作。首次试播不要求重命名或移动文件；整理需单独预览与确认。
