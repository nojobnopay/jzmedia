---
version: 0.22.3
reviewed: 2026-10-07
---

# 安装与首次使用

[本目录首页](README.md) · [配置参考](configuration.md)

先选择运行方案：[Docker 部署](#docker-部署)或 [Linux / WSL 直接运行源码](#宿主直接运行)。默认端口为 `8080`；浏览器访问远程服务器时，把 `localhost` 换成服务器地址。服务启动后再进行[第一次入库](#第一次入库与播放)。

| 运行方案 | 准备方式 | 启动方式 |
|---|---|---|
| Docker 容器 | 在线拉取、加载镜像文件或从源码构建 | 使用 Compose 配置文件，或执行 `docker run` |
| Linux / WSL 直接运行 | 准备源码及宿主 Python、Node.js、FFmpeg 环境 | 执行 `./start.sh` |

Docker 部署分为两个步骤：先把镜像准备到 Docker 中，再用镜像创建并启动容器。在线拉取、加载镜像文件和源码构建都属于镜像获取方式，都能配合 Compose 或 `docker run` 使用。Compose 将启动参数写在 YAML 文件里；本仓库使用的文件名是 `docker-compose.yml`。

NAS 是部署设备，使用 Docker/Container Manager 时仍按 Docker 流程操作，另见 [NAS 路径与权限说明](#nas-部署)。Linux/WSL 也可以运行 Docker；下文的“直接运行源码”特指在宿主上运行应用、不使用容器的独立方案。

## 安装前准备

- Docker 方案需要可用的 Docker Engine/Desktop 或 NAS 容器环境；使用仓库 Compose 配置需 Docker Compose 2.24.0 或更新版本，可用 `docker compose version` 查看。直接运行源码则需 Linux/WSL、Python、Node.js 与 FFmpeg。
- 一个可读取的媒体目录和一个可写的数据目录。仅浏览和播放时可将媒体库设为只读。
- 能访问服务器的浏览器。转码能力取决于片源、CPU/GPU 和网络。
- 可先使用已有 NFO 与本地资料，无需为启动服务提前申请 TMDB 凭据；资料来源在[首次配置](../user-guide/onboarding.md)中选择。

## Docker 部署

镜像包含服务端、网页、帮助站和 FFmpeg，运行容器无需在宿主另装 Python、Node.js 或 FFmpeg。以下命令在部署目录执行；选择源码构建时，该目录就是项目根目录。

源码归档、预构建的 Linux amd64 镜像归档与对应配置可从 [GitHub Releases](https://github.com/nojobnopay/jzmedia/releases) 下载。Docker 镜像也可从 `ghcr.io/nojobnopay/jzmedia` 在线拉取；Release 镜像归档仍可用于离线导入。目前 jzmedia 尚未发布到 DockerHub。

### Step 1：准备部署配置与目录

源码构建可下载 Release 的 `Source code` 归档并解压为完整项目；加载镜像文件则下载 `jzmedia-vX.Y.Z-linux-amd64.tar.gz`；在线拉取无需下载镜像归档。拉取或加载镜像后使用 Compose 时，下载同版本的 `docker-compose.yml`。`X.Y.Z` 替换为实际版本号；预构建镜像适用于 Linux amd64，其他架构需自行构建。将所需文件放到部署目录，在终端进入该目录。默认安装无需创建或复制 `.env`：

```bash
mkdir -p media data
id -u
id -g
```

**预期结果：**部署目录内有 `media/` 和 `data/`；最后两条命令分别打印当前用户的 UID、GID。

<span id="docker-paths"></span>

使用 Compose 时，在 `docker-compose.yml` 的 `services.mymedia` 下修改 `user`、`ports`、`volumes` 三个字段，保留其余字段。下面是这三个字段的示例，将 `1000:1000` 改为刚才的 UID/GID；需要其他端口或目录时，修改映射左侧的宿主值：

```yaml
user: "1000:1000"
ports:
  - "8080:8080"
volumes:
  - "./media:/app/media:rw"
  - "./data:/app/data"
```

仓库主 Compose 的 `user` 未配置时回退到 `0:0`（root）；应按宿主实际用户设置，并确保该用户可写数据目录、可读取媒体目录。宿主 `./media` 映射到容器 `/app/media`，`./data` 映射到 `/app/data`。改用已有目录时，填写实际路径并确认目录存在。应用设置里要填容器看得到的路径。需要写 NFO、整理或上传时，还需媒体目录写权限。已有部署若将影片登记在 `/media` 下，启动前先按[保留旧媒体路径](#legacy-media-path)配置。

选择 `docker run` 时无需准备 Compose 文件，直接在[启动命令](#docker-run)中填写端口、路径与 UID/GID。资料来源、智能辅助和访问令牌可在启动后的网页设置中配置。

`.env` 是可选的宿主配置文件，已有文件会继续读取；它不会被复制或挂载到容器里，容器内看不到该文件是正常现象。需要集中管理环境变量时，按[可选的环境变量文件](configuration.md#可选的环境变量文件)配置。

### Step 2：准备 Docker 镜像

下面三种方式任选一种。完成后，镜像应位于准备启动容器的同一个 Docker Engine 中。

#### 从 GHCR 拉取镜像

在 Linux amd64 设备上执行：

```bash
docker pull ghcr.io/nojobnopay/jzmedia:v0.22.3
docker image ls ghcr.io/nojobnopay/jzmedia
```

**预期结果：**本地镜像列表包含 `ghcr.io/nojobnopay/jzmedia:v0.22.3`。若拉取失败，检查网络和标签，或使用下方的 Release 镜像归档。

Compose 启动前将 `services.mymedia.image` 改为 `ghcr.io/nojobnopay/jzmedia:v0.22.3`；`docker run` 同样使用这个完整镜像名，再按 Step 3 启动。只修改 `APP_VERSION` 不会把原有 `jzmedia` 仓库名改为 GHCR 地址。

`ghcr.io/nojobnopay/jzmedia:latest` 跟随最新发布镜像；固定部署建议选择明确版本标签。在线镜像与对应 Release 归档使用同一发行镜像，均为 `linux/amd64`。

#### 从源码构建

在完整项目根目录执行，按 Compose 的 `image` 字段生成本地镜像，默认标签为 `jzmedia:latest`：

```bash
docker compose -f docker-compose.yml build mymedia
docker image ls jzmedia
```

**预期结果：**构建成功，镜像列表包含 `jzmedia:latest`（使用示例配置时）。前端和帮助站在镜像构建中编译；首次构建需要获取 Dockerfile 中的基础镜像及依赖。构建失败按[构建网络排障](troubleshooting.md#docker-构建或拉取超时)检查后重试。

如果只使用 Docker CLI、未安装 Compose，也可在项目根目录执行 `docker build -t jzmedia:latest .`。它不会自动读取 `.env` 中的构建代理；需要代理时通过 Docker 的 `--build-arg` 传入 `HTTP_PROXY`、`HTTPS_PROXY` 等构建参数。

以上操作只准备镜像。即使用 Compose 构建，下一步也可以选择 `docker run` 启动。需要同时交付镜像和 Android TV APK 时，使用[统一发布流程](../developer/releasing.md)。

#### 加载镜像文件

取得适合目标设备 CPU 架构的 jzmedia 镜像归档，将下面的 `X.Y.Z` 替换为下载的版本号。`docker load` 可直接读取 gzip 归档，无需先解压：

```bash
docker load -i jzmedia-vX.Y.Z-linux-amd64.tar.gz
docker image ls
```

**预期结果：**`docker load` 打印载入的镜像标签，镜像出现在本地列表中；此时尚未启动容器。归档应由 `docker save` 导出，交付方式见[镜像文件交付说明](../developer/releasing.md#docker-image-export)。目标设备不需要源码或构建依赖。

记下 `docker load` 实际载入的完整镜像名。云端发行归档使用 `ghcr.io/nojobnopay/jzmedia:vX.Y.Z`，随附 Compose 已固定为相同镜像；早期归档（如 `v0.20.1`）使用 `jzmedia:vX.Y.Z`。启动前核对 Compose 的 `image` 或 `docker run` 最后的镜像名与实际载入值一致，`X.Y.Z` 替换为实际版本号。仅修改 `APP_VERSION` 不能切换仓库名，也不要在本地缺少 `latest` 时沿用该标签。

### Step 3：选择一种方式启动容器

镜像准备好后，下面两种方式任选一种；它们都启动 jzmedia 的 Docker 容器，同一个实例只需启动一次。

<span id="docker-compose"></span>

#### 使用 Compose 配置文件

使用已调整端口、路径和用户的 `docker-compose.yml`；没有 `.env` 时，Compose 会跳过该可选文件：

```bash
docker compose -f docker-compose.yml up -d --no-build --pull never
docker compose -f docker-compose.yml ps
docker compose -f docker-compose.yml logs --tail=100 mymedia
```

**预期结果：**`mymedia` 服务的容器处于运行状态，日志没有持续重启或权限错误。`--no-build --pull never` 让启动使用上一步已准备的本地镜像；若报镜像不存在，核对拉取/载入/构建的标签与 Compose 的 `image`，再重试。参数含义见 [Docker Compose 命令参考](https://docs.docker.com/reference/cli/docker/compose/up/)。

生产部署始终显式指定 `-f docker-compose.yml`，不加载本机开发用的 override 文件。源码构建需要完整项目，拉取或加载镜像后的启动只需配置文件和挂载目录。

<span id="docker-run"></span>

#### 使用 docker run

下面示例使用部署目录下的 `media/`、`data/`，容器名为 `jzmedia`。执行前，将 `1000:1000` 改为实际 UID/GID，将最后的 `jzmedia:latest` 改为上一步准备的完整镜像标签。使用其他宿主目录或端口时，同时修改 `-v` 左侧路径和 `-p` 左侧端口：

```bash
docker run -d --name jzmedia \
  --pull=never --init --restart unless-stopped \
  --user 1000:1000 \
  -e MEDIA_ROOT=/app/media \
  -e DATA_DIR=/app/data \
  -p 8080:8080 \
  -v "$(pwd)/media:/app/media:rw" \
  -v "$(pwd)/data:/app/data" \
  jzmedia:latest
docker ps -a --filter name=jzmedia
docker logs --tail=100 jzmedia
```

**预期结果：**`jzmedia` 容器处于运行状态，日志没有持续重启或权限错误。`--pull=never` 只使用本地镜像；镜像标签不存在时会直接报错，见 [Docker run 命令参考](https://docs.docker.com/reference/cli/docker/container/run/#set-the-pull-policy---pull)。启动失败按[部署排障](troubleshooting.md)检查；若同名容器已存在，先检查该容器，已有容器的启停见[停止与重启](operations.md#停止与重启)。

上面的命令无需 `.env`。需设置高级环境变量时添加 `-e 名称=值`；已有自定义文件时也可添加 `--env-file .env`，具体区别见[配置参考](configuration.md#可选的环境变量文件)。需要硬件转码时还需显式映射设备与补充设备组，见[配置参考](configuration.md#intel-硬件设备)。

两种启动方式都应保留单进程服务：应用使用内存播放会话和任务状态，不能额外设置多个 uvicorn worker。

<span id="check-service"></span>

### Step 4：打开网页并检查服务

打开 `http://localhost:8080`，再查看 `http://localhost:8080/api/health`。健康 JSON 中检查 `status`、`db`、`media`、`ffmpeg`、`ffprobe`、`transcoder`；Compose 配置的容器健康检查显示 healthy，也不能代替检查这些具体字段。

**预期结果：**能看到应用首页，数据库与媒体检查正常，播放所需工具可用。随后按[第一次入库与播放](#第一次入库与播放)建库。

若设置 `JZMEDIA_TOKEN`，网页需填写令牌才能提交播放决策、创建播放会话、保存进度以及执行管理操作；页面浏览和媒体直链等 GET 仍开放。令牌保护不能代替完整的读取访问控制。

<span id="legacy-media-path"></span>

### 已有部署保留旧媒体路径

修改 `MEDIA_ROOT` 不会改写数据库中已登记的媒体库和视频库路径；已有影片或剧集记录的媒体库也不能在设置页直接改根路径。若原库使用 `/media`，升级时先[备份数据](operations.md#一次一致的备份)，保留同一个宿主媒体目录与 `/media` 的映射，无需重新建库或扫描。

使用 Compose 时，在部署目录创建 `compose.media-legacy.yml`。将示例卷映射左侧改为原来的宿主目录；如果已有 `.env` 设置了 `MEDIA_HOST_PATH`、`DATA_HOST_PATH`，可保留下面的变量写法和原文件：

```yaml
services:
  mymedia:
    environment:
      MEDIA_ROOT: /media
    volumes: !override
      - "${MEDIA_HOST_PATH:-./media}:/media:rw"
      - "${DATA_HOST_PATH:-./data}:/app/data"
```

`!override` 完整替换卷列表，避免新的 `/app/media` 映射一并合入；已有额外映射需一并列入。此写法需要 Docker Compose 2.24.4 或更新版本，见 [Docker 合并规则](https://docs.docker.com/reference/compose-file/merge/#replace-value)。先按上文准备镜像并对齐完整镜像名，再执行：

```bash
docker compose -f docker-compose.yml -f compose.media-legacy.yml config --quiet
docker compose -f docker-compose.yml -f compose.media-legacy.yml up -d --no-build --pull never
```

**预期结果：**原媒体仍在容器 `/media` 下可见，已有库的连接检查与播放正常。后续管理该实例继续使用相同的两个 `-f` 参数；若配置检查提示不支持 `!override`，先升级 Compose，不要直接删掉该标记。

使用 `docker run` 时，同样保留原宿主媒体目录，将示例的媒体卷目标改为 `/media`，并设置 `-e MEDIA_ROOT=/media`；数据卷继续指向原数据目录。

## NAS 部署

具有 Docker/Container Manager 的 NAS 使用上面的 Docker 部署流程。以下补充 NAS 的目录、权限与操作入口：

1. 建立部署目录，例如 `/volume1/docker/jzmedia`。选择源码构建时放完整源码；选择预构建镜像时按上文拉取或导入适合 NAS 架构的镜像，并准备对应的配置模板。首次安装不要把开发机的 `.env`、数据库、缓存或开发 override 文件一并复制；已有实例迁移按[备份与迁移](operations.md)操作。
2. 在 Compose 文件、`docker run` 参数或 NAS 界面中填写实际宿主目录，如媒体 `/volume1/video`、数据 `/volume1/docker/jzmedia/data`，无需另建 `.env`。
3. 确认容器运行用户的 UID/GID 对这些目录有相应权限；若报 Permission denied，修正目标目录的属主/ACL，不要直接递归修改整块媒体盘的权限。
4. 使用 NAS 的 Compose 项目管理界面或上面的 `docker compose` / `docker run` 命令启动。界面中也需选择已准备的本地镜像，并核对端口、目录、用户与设备映射；当前不要选择从 DockerHub 下载 jzmedia。
5. 访问 `http://NAS地址:8080`。媒体库类型选「本地路径」，新部署默认根路径填 `/app/media`；同一台 NAS 的挂载目录不必再绕行 SMB。

Intel VAAPI/QSV 硬件转码可按 [配置参考](configuration.md) 映射设备。NVENC 在应用中有探测路径，但仓库没有完整 NVIDIA 容器设备配置，需在目标机器另行验证。

## 宿主直接运行

这是独立于 Docker 的方案：直接在 Linux/WSL 宿主安装依赖并运行应用，既不构建 jzmedia 镜像，也不创建容器。将完整源码放到宿主，在项目根目录执行以下命令。仓库 Docker 构建基线为 Python 3.12、Node.js 25；使用其他版本前自行运行项目验证命令。

### Step 1：安装开发依赖

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
npm ci --prefix frontend
```

系统 FFmpeg/ffprobe 优先；开发依赖提供静态二进制兜底，首次使用可能需要下载。离线设备应提前准备可用二进制。

### Step 2：启动服务

依赖安装完成后直接启动，无需复制 `.env.example`：

```bash
./start.sh
```

**预期结果：**脚本完成所需构建并打印服务地址；保持终端运行，按 `Ctrl+C` 停止。打开首页与 `/api/health`，按上面 [Docker 的服务检查步骤](#check-service)核对结果，再进行首次入库。

### 启动脚本与路径说明

`start.sh` 通过 `scripts/build_frontend.py`、`scripts/build_docs.py` 检查源码、静态资源、配置和锁文件的内容摘要，包含新增与删除；输入变化才构建，依赖未准备或锁文件变化时执行相应 `npm ci`。前端品牌素材和帮助站正文/主题/素材都在检查范围内，私有文档、计划与历史报告不触发帮助站构建。随后以单进程启动后端；宿主默认使用 `./data`、`./media`，不会直接采用 `.env` 中的容器数据路径。可这样明确覆盖：

```bash
DATA_DIR=./data MEDIA_ROOT=./media APP_PORT=8080 ./start.sh
```

脚本只在宿主存在 `.env` 时读取它，不会自动生成文件。需要使用该可选文件时，按 `KEY=VALUE` 填写；脚本不是完整的 shell 配置解析器，使用模板中的无引号单行值，不写 shell 展开或行尾注释。已导出的非空环境变量优先；空值可能被 `.env` 中的非空值补回。不要通过 `source .env` 导入容器配置。

## Android TV 客户端

服务器启动后可供网页与独立 Android TV 客户端共用。镜像与 APK 通过[统一发布](../developer/releasing.md)从同一提交同步构建；普通 Docker Compose 或 `start.sh` 部署不会另外生成 APK。客户端安装、开发构建与产物位置见仓库 `android-tv/README.md`。电视连接服务器地址，通过协议 1 握手，并在实例启用访问令牌时单独验证写权限。电视真机的解码、HDR、音轨及遥控器行为仍需按实际设备验收。

## 第一次入库与播放

打开空库首页，点击“开始配置”，跟随[首次入库图解](../user-guide/onboarding.md)完成资料来源、目标视频库、扫描或上传，再核对结果并播放。已有内容的安装可从“设置 → 新手配置”进入。

扫描与整理是不同操作。首次试播不要求重命名或移动文件；整理需单独预览与确认。
