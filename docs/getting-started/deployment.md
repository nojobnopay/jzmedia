# 安装与首次使用

[本目录首页](README.md) · [配置参考](configuration.md) · [部署排障](troubleshooting.md)

首次安装建议在 Linux amd64（x86_64）设备上，按下面的 [Docker 部署](#docker-部署)启动网页，再跟随[首次入库](../user-guide/onboarding.md)扫描并试播。默认端口为 `8080`；浏览器访问远程服务器时，把 `localhost` 换成服务器地址。

需要其他方式时，可选择 [Compose 配置文件](#docker-compose)、[离线镜像](#加载镜像文件)、[源码构建](#从源码构建)或 [Linux / WSL 直接运行源码](#宿主直接运行)。NAS 使用 Docker/Container Manager 时同样按 Docker 流程操作，另见 [NAS 路径与权限](#nas-部署)。在原生 Linux 或支持宿主网络的 NAS 上需要 DLNA 电视自动发现时，建议按 [DLNA 投屏网络](#dlna-投屏网络)使用 host 模式。

## 安装前准备

- 可用的 Docker Engine/Desktop 或 NAS 容器环境。预构建镜像适用于 Linux amd64，其他架构需自行构建并验证；直接运行源码的条件见[对应流程](#宿主直接运行)。
- 一个可读取的媒体目录和一个可写的数据目录。下面的最短流程在当前部署目录创建 `media/` 与 `data/`，使用当前用户的 UID/GID 运行容器；该用户需有 Docker 操作权限。
- 能访问服务器的浏览器。转码能力取决于片源、CPU/GPU 和网络，先用小目录试播；硬件选配见[硬件与配置建议](#硬件与配置建议)。
- 可先使用已有 NFO 与本地资料，无需为启动服务提前申请 TMDB 凭据；资料来源在[首次配置](../user-guide/onboarding.md)中选择。

## Docker 部署

镜像包含服务端、网页、帮助站和 FFmpeg，宿主无需另装 Python、Node.js 或 FFmpeg。以下为新安装的最短在线路径，不需要下载源码、Compose 文件或创建 `.env`。

### Step 1：准备部署配置与目录

在选定的部署目录打开终端并执行：

```bash
mkdir -p media data
```

**预期结果：**当前目录内有 `media/` 和 `data/`；当前用户能读取媒体目录并写入数据目录。可先在 `media/` 放少量测试影片；`data/` 持久保存数据库、图片与缓存，重建容器时继续使用同一目录。

<span id="docker-paths"></span>

宿主 `./media` 映射到容器 `/app/media`，`./data` 映射到 `/app/data`。应用设置里要填容器看得到的路径；新部署的媒体库根路径填 `/app/media`。使用已有目录时，修改后面 `-v` 左侧的宿主路径，并确保目录存在且当前用户有相应权限。需要写 NFO、整理或上传时，还需媒体目录写权限；仅浏览和播放可将媒体卷改为 `:ro`。

已有部署若将影片登记在 `/media` 下，启动前先按[保留旧媒体路径](#legacy-media-path)配置，不要把原数据目录换成新空目录。

### Step 2：准备 Docker 镜像

快速体验使用 `ghcr.io/nojobnopay/jzmedia:latest`，下一步的命令会从 GHCR 自动拉取镜像。`latest` 跟随最新发布镜像；固定部署应先在 [GitHub Releases](https://github.com/nojobnopay/jzmedia/releases) 选择真实发行版本，再将镜像标签替换为对应的 `vX.Y.Z`，不要根据仓库中的版本号猜测已发布标签。

需要预先拉取、Compose 或离线安装时，见后面的[其他 Docker 安装方式](#其他-docker-安装方式)。在线拉取、加载镜像文件和源码构建都是镜像获取方式，均可搭配 Compose 或 `docker run`；目前 jzmedia 尚未发布到 DockerHub。

### Step 3：选择一种方式启动容器

下面使用 `docker run`，在当前部署目录执行；希望将参数保存在 YAML 文件中时，可改用 [Compose 配置文件](#docker-compose)。同一个实例只需启动一次。

默认命令使用 bridge 网络。原生 Linux／NAS 需要 DLNA 电视自动发现时，推荐将命令中的 `-p 8080:8080` 替换为 `--network host`；host 模式下直接使用宿主 `8080`，其余用户、卷和环境参数保留。端口与代理要求见 [DLNA 投屏网络](#dlna-投屏网络)。

<span id="docker-run"></span>

#### 使用 docker run

```bash
docker run -d --name jzmedia \
  --pull=always --init --restart unless-stopped \
  --user "$(id -u):$(id -g)" \
  -e MEDIA_ROOT=/app/media \
  -e DATA_DIR=/app/data \
  -p 8080:8080 \
  -v "$(pwd)/media:/app/media:rw" \
  -v "$(pwd)/data:/app/data:rw" \
  ghcr.io/nojobnopay/jzmedia:latest
```

**预期结果：**Docker 拉取镜像并打印容器 ID。`--pull=always` 每次执行创建命令时检查远端镜像，不会自动升级已经运行的容器。bridge 模式使用其他端口时修改 `-p` 左侧端口；host 模式不使用端口映射。离线或本地构建镜像按[本地镜像启动说明](#本地镜像的-docker-run-启动参数)改用 `--pull=never` 和实际镜像名。

拉取失败时检查网络与发行标签，或使用[离线镜像](#加载镜像文件)。若提示同名容器已存在，先检查该容器；已有实例的启停见[停止与重启](operations.md#停止与重启)，升级见[升级](operations.md#升级)。

<span id="check-service"></span>

### Step 4：打开网页并检查服务

打开 `http://localhost:8080`；部署在另一台设备时，打开 `http://服务器地址:8080`。能看到应用首页后，继续[第一次入库与播放](#第一次入库与播放)。

再打开同一地址下的 `/api/health`，检查 JSON 中的 `status`、`db`、`media`、`ffmpeg`、`ffprobe`、`transcoder`。**预期结果：**数据库与媒体检查正常，播放所需工具可用；Compose 显示 healthy 也不能代替核对这些字段。

网页打不开或服务反复退出时，检查容器状态与日志：

```bash
docker ps -a --filter name=jzmedia
docker logs --tail=100 jzmedia
```

日志有 `Permission denied` 时先核对运行用户和目录权限；端口被占用时修改宿主端口再创建容器。其他问题按[部署排障](troubleshooting.md)处理，不要通过删除数据目录来重试。

若设置 `JZMEDIA_TOKEN`，网页需填写令牌才能提交播放决策、创建播放会话、保存进度以及执行管理操作。令牌本身不限制读取；另行设置的[儿童与隐私保护](../user-guide/settings.md#儿童模式与隐私模式)按当前设备模式限制列表、详情、图片和媒体访问，两种保护独立生效。

## 第一次入库与播放

打开空库首页，点击“开始配置”，跟随[首次入库图解](../user-guide/onboarding.md)完成资料来源、目标视频库、扫描或上传，再核对结果并播放。已有内容的安装可从“设置 → 新手配置”进入。

扫描与整理是不同操作。首次试播不要求重命名或移动文件；整理需单独预览与确认。

## Android TV 客户端

服务器启动后，可按 [Android TV 安装与连接（GitHub）](https://github.com/nojobnopay/jzmedia/blob/main/android-tv/README.md#已有-apk安装并连接)安装 APK 并连接服务器。网页与电视共用同一套媒体库；电视真机的解码、HDR、音轨及遥控器行为需按实际设备验收。

镜像与 APK 通过[统一发布](../developer/releasing.md)从同一提交同步构建；普通 Docker Compose 或 `start.sh` 部署不会另外生成 APK。电视通过协议 1 握手，并在实例启用访问令牌时单独验证写权限；开发构建与产物位置见仓库 `android-tv/README.md`。

## 其他 Docker 安装方式

下面的方式复用前面的目录与路径约定。镜像准备好后，选择 Compose 或 `docker run` 启动；完成后均按[服务检查](#check-service)打开网页并建库。

### 从 GHCR 拉取镜像

需要提前准备镜像或使用 Compose 时，在 Linux amd64 设备上执行：

```bash
docker pull ghcr.io/nojobnopay/jzmedia:latest
docker image ls ghcr.io/nojobnopay/jzmedia
```

**预期结果：**本地镜像列表包含 `ghcr.io/nojobnopay/jzmedia:latest`。固定部署时，将命令中的 `latest` 换成 [GitHub Releases](https://github.com/nojobnopay/jzmedia/releases) 已发布的 `vX.Y.Z`；拉取失败时检查网络与标签，或选择 Release 镜像归档。

Compose 的 `services.mymedia.image` 和 `docker run` 最后的镜像名必须与准备的完整镜像名一致。只修改 `APP_VERSION` 不会把模板中的 `jzmedia` 仓库名改成 GHCR 地址。在线镜像与对应 Release 归档使用同一发行镜像，均为 `linux/amd64`。

<span id="docker-compose"></span>

### 使用 Compose 配置文件

需要 Docker Compose 2.24.0 或更新版本，可用 `docker compose version` 查看。拉取或加载 Release 镜像时，下载同一版本的 `docker-compose.yml` 放到部署目录；源码构建使用项目根目录中的文件。默认安装无需创建或复制 `.env`。

先按前面的方式准备 `media/`、`data/`，并用 `id -u`、`id -g` 查看宿主用户。在 `docker-compose.yml` 的 `services.mymedia` 下修改 `image`、`user`、`ports`、`volumes`，保留其余字段。下面是在线体验的字段示例，将 `1000:1000` 换为实际 UID/GID；固定部署将 `latest` 换为已经拉取的真实发行标签：

```yaml
image: ghcr.io/nojobnopay/jzmedia:latest
user: "1000:1000"
ports:
  - "8080:8080"
volumes:
  - "./media:/app/media:rw"
  - "./data:/app/data"
```

仓库主 Compose 的 `user` 未配置时回退到 `0:0`（root）；应按宿主实际用户设置，并确保该用户可写数据目录、可读取媒体目录。使用源码构建或离线镜像时，`image` 填对应的本地完整镜像名。准备好镜像后执行：

```bash
docker compose -f docker-compose.yml up -d --no-build --pull never
docker compose -f docker-compose.yml ps
docker compose -f docker-compose.yml logs --tail=100 mymedia
```

**预期结果：**`mymedia` 服务的容器处于运行状态，日志没有持续重启或权限错误。`--no-build --pull never` 使用已经准备的本地镜像；若报镜像不存在，核对拉取、载入或构建的标签与 Compose 的 `image`，再重试。参数含义见 [Docker Compose 命令参考](https://docs.docker.com/reference/cli/docker/compose/up/)。

生产部署始终显式指定 `-f docker-compose.yml`，不加载本机开发用的 override 文件。原生 Linux／NAS 需要 DLNA 自动发现时，按 [host 配置](#host-network)在最后追加 `-f compose.host.yml`，后续启停、升级和重建继续保留该参数。源码构建需要完整项目，拉取或加载镜像后的启动只需配置文件和挂载目录。

`.env` 是可选的宿主配置文件，已有文件会继续读取；它不会被复制或挂载到容器里，容器内看不到该文件是正常现象。需要集中管理环境变量时，按[可选的环境变量文件](configuration.md#可选的环境变量文件)配置。

### 从源码构建

从 [GitHub Releases](https://github.com/nojobnopay/jzmedia/releases) 下载 `Source code` 归档并解压，或使用已有完整源码。在项目根目录执行，按仓库 Compose 的 `image` 字段生成本地镜像，未改写该字段时默认标签为 `jzmedia:latest`：

```bash
docker compose -f docker-compose.yml build mymedia
docker image ls jzmedia
```

**预期结果：**构建成功，镜像列表包含 `jzmedia:latest`（未改写仓库配置时）；已自定义 `image` 的部署按该字段核对实际标签。前端和帮助站在镜像构建中编译；首次构建需要获取 Dockerfile 中的基础镜像及依赖。构建失败按[构建网络排障](troubleshooting.md#docker-构建或拉取超时)检查后重试。

如果只使用 Docker CLI、未安装 Compose，也可在项目根目录执行 `docker build -t jzmedia:latest .`。它不会自动读取 `.env` 中的构建代理；需要代理时通过 Docker 的 `--build-arg` 传入 `HTTP_PROXY`、`HTTPS_PROXY` 等构建参数。

以上操作只准备镜像。即使用 Compose 构建，也可以按下文的本地镜像启动说明选择 `docker run` 启动。需要同时交付镜像和 Android TV APK 时，使用[统一发布流程](../developer/releasing.md)。

### 加载镜像文件

从 [GitHub Releases](https://github.com/nojobnopay/jzmedia/releases) 下载适合目标设备 CPU 架构的 jzmedia 镜像归档，将下面的 `X.Y.Z` 替换为实际下载的版本号。`docker load` 可直接读取 gzip 归档，无需先解压：

```bash
docker load -i jzmedia-vX.Y.Z-linux-amd64.tar.gz
docker image ls
```

**预期结果：**`docker load` 打印载入的镜像标签，镜像出现在本地列表中；此时尚未启动容器。归档应由 `docker save` 导出，交付方式见[镜像文件交付说明](../developer/releasing.md#docker-image-export)。目标设备不需要源码或构建依赖。

记下 `docker load` 实际载入的完整镜像名。云端发行归档使用 `ghcr.io/nojobnopay/jzmedia:vX.Y.Z`，随附 Compose 已固定为相同镜像；早期归档（如 `v0.20.1`）使用 `jzmedia:vX.Y.Z`。启动前核对 Compose 的 `image` 或 `docker run` 最后的镜像名与实际载入值一致，`X.Y.Z` 替换为实际版本号。仅修改 `APP_VERSION` 不能切换仓库名，也不要在本地缺少 `latest` 时沿用该标签。

### 本地镜像的 docker run 启动参数

源码构建、离线导入或预先拉取完成后，复用上面的 [docker run 命令](#docker-run)：将 `--pull=always` 改为 `--pull=never`，最后一行改为本地实际存在的完整镜像名，例如源码构建的 `jzmedia:latest`，或 `docker load` 打印的 `ghcr.io/nojobnopay/jzmedia:vX.Y.Z`。这样启动不会访问远端仓库；镜像不存在时会直接报错，见 [Docker run 命令参考](https://docs.docker.com/reference/cli/docker/container/run/#set-the-pull-policy---pull)。

该命令无需 `.env`；需设置高级环境变量时添加 `-e 名称=值`，已有自定义文件时也可添加 `--env-file .env`，具体区别见[配置参考](configuration.md#可选的环境变量文件)。需要硬件转码时还需显式映射设备与补充设备组，见[配置参考](configuration.md#intel-硬件设备)。

容器默认时区为 `Asia/Shanghai`，缩略图闲时默认对应北京时间 03:00–06:00。使用其他时区时配置 `TZ`，见[配置参考](configuration.md#运行路径与端口)；已有容器需更新启动配置并重新创建后才生效。

Compose 与 `docker run` 都应保留单进程服务：应用使用内存播放会话和任务状态，不能额外设置多个 uvicorn worker。

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

具有 Docker/Container Manager 的 NAS 使用上面的 Docker 部署流程。需要 DLNA 电视自动发现时，推荐整个 jzmedia 服务使用 [host 网络](#host-network)；无需安装额外的手机或电视接收应用。不使用 DLNA 自动发现时可继续使用默认 bridge 配置。以下补充 NAS 的目录、权限与操作入口：

1. 建立部署目录，例如 `/volume1/docker/jzmedia`。选择源码构建时放完整源码；选择预构建镜像时按上文拉取或导入适合 NAS 架构的镜像，并准备对应的配置模板。首次安装不要把开发机的 `.env`、数据库、缓存或开发 override 文件一并复制；已有实例迁移按[备份与迁移](operations.md)操作。
2. 在 Compose 文件、`docker run` 参数或 NAS 界面中填写实际宿主目录，如媒体 `/volume1/video`、数据 `/volume1/docker/jzmedia/data`，无需另建 `.env`。
3. 确认容器运行用户的 UID/GID 对这些目录有相应权限；若报 Permission denied，修正目标目录的属主/ACL，不要直接递归修改整块媒体盘的权限。
4. 使用 NAS 的 Compose 项目管理界面或上面的 `docker compose` / `docker run` 命令启动。界面中也需选择已准备的本地镜像，并核对端口、目录、用户与设备映射；当前不要选择从 DockerHub 下载 jzmedia。
5. 访问 `http://NAS地址:8080`。媒体库类型选「本地路径」，新部署默认根路径填 `/app/media`；同一台 NAS 的挂载目录不必再绕行 SMB。

Intel VAAPI/QSV 硬件转码可按 [配置参考](configuration.md#intel-硬件设备)映射设备；配有 NVIDIA 显卡及容器 GPU 运行时的机器可按 [NVIDIA 硬件转码](configuration.md#nvidia-硬件转码)叠加可选配置，并核对实际后端。

## 硬件与配置建议

以下针对服务端。预构建 Docker 镜像适用于 Linux amd64（x86_64），其他架构需自行构建并验证。项目尚未通过硬件基准确定统一的最低 CPU 核数与内存；已有电脑或 NAS 可先用小目录完成扫描、浏览与试播，再判断是否需要升级。

原画直放时，服务器主要读取和传输文件，视频解码由客户端完成，无需强制配备 GPU。客户端不兼容、降低分辨率或需要烧录字幕时，可能由服务器重新编码视频，对 CPU/GPU 的要求会提高；选择“原画”也可能因格式不兼容而转码，详见[画质与实际输出](../user-guide/playback-controls.md#选择倍速与画质)。

| 项目 | 家庭使用的选配起点 |
|---|---|
| CPU / GPU | 优先考虑兼容的 Intel 核显平台；启用普通视频硬件转码前，需核对片源编码的硬件解码能力和 H.264 硬件编码能力，常见 HEVC 10-bit 片源需支持 Main10 解码 |
| 内存 | 建议 8 GB 整机内存，并为操作系统、其他容器和核显共享内存留出余量 |
| 数据与缓存 | 建议将数据库、海报和播放缓存放在 SSD；媒体文件可继续放在已有硬盘或 NAS 共享中 |
| 网络 | 建议服务器使用千兆有线网络；有效带宽需覆盖片源码率峰值与同时播放的总需求 |

上述为选配建议，尚非 jzmedia 的硬件实测结论；8 GB 指宿主整机内存，无需为容器单独预留 8 GB。选配思路可参考 [Jellyfin 官方硬件指南](https://jellyfin.org/docs/general/administration/hardware-selection/)，具体支持范围以 jzmedia 的配置说明与实际播放结果为准。

有核显或独立显卡，还需驱动、容器设备访问权限和实际片源编解码能力配合。Intel 与已有 NVIDIA 设备的配置分别见 [Intel 硬件设备](configuration.md#intel-硬件设备)和 [NVIDIA 硬件转码](configuration.md#nvidia-硬件转码)。

4K HDR 的视频转换、字幕烧录和多路并发需按实际片源单独验证，不能仅凭内存大小或显卡型号保证实时播放；兼容客户端能直接播放原视频时，仍可减少服务器负担。Dolby Vision P5 网页转换是默认关闭的试验功能，需要专用 Intel 硬件链路并逐片验收，见 [P5 网页播放条件](configuration.md#dolby-vision-p5-网页播放)。并发任务上限只是软件额度，可同时流畅转码的路数仍取决于硬件与片源。

磁盘空间需分别考虑镜像、数据库、图片、预览与播放缓存，媒体文件另计；缓存额度和应用数据总预算均为软目标，活动任务可能使占用暂时超出设置值，因此需额外保留空闲空间。额度含义与调整方法见[播放与缓存](configuration.md#播放与缓存)。

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

## DLNA 投屏网络

需要手机投屏时，先确认电视已开启 DLNA 视频接收，服务器与电视处于可互通的局域网。手机网页只访问 jzmedia，电视直接从服务器获取视频，无需手机或电视另装接收应用。操作见[投屏到电视](../user-guide/playback-controls.md#投屏到电视)。不要给电视发送 `localhost`、`127.0.0.1`、模拟器专用的 `10.0.2.2` 或容器内部地址。

发现使用服务器发出的 SSDP UDP 组播（`239.255.255.250:1900`），响应返回服务器临时 UDP 端口；控制通过电视声明的 HTTP 端口，视频由电视从 jzmedia 读取。只映射容器的 TCP `8080` 或 UDP `1900`，不能保证组播发现可用。手机能打开网站，也不代表服务器能搜索电视；访客 Wi-Fi、跨 VLAN、WSL NAT 和 Docker 桥接都需分别检查互通。

<span id="host-network"></span>

### Linux／NAS：推荐 host 部署

原生 Linux 或支持宿主网络的 NAS 上，需要 DLNA 电视自动发现时建议整个 jzmedia 服务使用 host 模式，不需要另设发现助手。host 让服务直接使用宿主网络；它不保证跨 VLAN、访客 Wi-Fi 或已被防火墙阻断的设备可达，也不会改变电视的解码、音轨或字幕能力。Docker Desktop／WSL 的 host 实现与原生 Linux 不同，仍需在实际环境验证，不能以 NAS 的验证结果代替。

使用 `docker run` 时，将[启动命令](#docker-run)中的 `-p 8080:8080` 替换为 `--network host`，不要同时保留 `-p`。使用 Compose 时，将仓库或同一发行包中的 `compose.host.yml` 与 `docker-compose.yml` 放在部署目录；准备好镜像、用户和卷配置后执行：

```bash
docker compose -f docker-compose.yml -f compose.host.yml config --quiet
docker compose -f docker-compose.yml -f compose.host.yml up -d --no-build --pull never --force-recreate mymedia
docker compose -f docker-compose.yml -f compose.host.yml ps
```

`compose.host.yml` 清除服务的 `ports` 与 `networks`，设置 `network_mode: host`；需要 Docker Compose 2.24.0 或更新版本。原命令已有其他覆盖文件时，保留全部原 `-f` 参数，并将 `-f compose.host.yml` 放在最后。若旧版发行包不含此文件，或 NAS 的旧 Compose 不支持 `!reset`，可在自己的服务配置中直接删除 `ports`、`networks`，增加 `network_mode: host`；不要仅删除 `!reset` 标记，否则列表可能仍被合并。

切换前检查以下配置，停止观影并[保存一致备份](operations.md#一次一致的备份)后再重新创建容器：

- **端口：**默认镜像和主 Compose 直接监听宿主 `8080`；原 `APP_PORT` 端口映射不再生效。确保该端口未被其他服务占用；若需改用其他监听端口，应明确修改容器启动命令，不能只改 `ports` 或 `APP_PORT`。
- **代理：**原先通过 Docker 自定义网络访问的代理容器名称，切换后可能无法解析。将 `TMDB_PROXY` 改为 host 服务可访问的代理地址；代理确实监听宿主回环时可使用 `http://127.0.0.1:实际端口`。核对 API 与图片下载，不把未认证请求的响应当作 TMDB 配置已验证。
- **数据与权限：**保持原媒体和数据挂载、`MEDIA_ROOT`／`DATA_DIR`、UID/GID、硬件设备和附加组；保留原 `.env`、`secret.key` 和观看保护设置，不换成空数据目录，也不重新建库。

**预期结果：**首页与 `/api/health` 正常，原媒体库、代理下载与权限保持可用；投屏面板能列出开启接收的电视。再用隔离视频观察电视画面和声音，并验证暂停、跳转与停止。升级与日常管理继续使用同一组 `-f` 参数。需要回退时恢复原配置与代理地址，移除 host 覆盖并重新创建原服务；仅切换网络时不删除数据目录。

### 保留 bridge 或手动添加电视

bridge 模式可能阻断 SSDP 自动发现，导致搜索不到电视；这不等于所有投屏能力都不可用。若服务器仍能通过 HTTP 连接电视，可在投屏面板手动填写完整的 DLNA 描述地址来绕过搜索，但电视也必须能访问服务器的视频地址。手动条目只保留在当前服务进程中，端口或地址变化后需要重新添加，具体操作见[投屏到电视](../user-guide/playback-controls.md#投屏到电视)。

需要固定投屏媒体地址时，可通过进程环境或 Compose 的可选 `.env` 配置 `CAST_BASE_URL=http://192.168.1.10:8080`，替换为电视可访问的真实地址，重启服务生效。该值只决定电视取流地址，不会修复组播网络；默认根据网页访问地址填写，投屏面板也允许本次手动调整。支持地址带代理路径前缀，代理必须提供对应 `/api/cast/media/` 的 GET、HEAD 与 Range 访问。

## 重设保护密码

忘记儿童与隐私模式的保护密码时，由有服务器终端权限的维护者在本机重设。命令交互式输入新密码和确认密码，要求 6–128 个字符；不要把密码放进命令行或环境变量。

使用本仓库 Compose 配置时，在部署目录执行：

```bash
docker compose exec mymedia python -m app.reset_protection_password
```

使用上文 `docker run` 的容器名时执行 `docker exec -it jzmedia python -m app.reset_protection_password`；自定义容器名称需替换 `jzmedia`。宿主直接运行源码时，在项目根目录使用已有虚拟环境，并把示例路径替换为正在运行实例的实际数据目录：

```bash
DATA_DIR=/path/to/jzmedia-data .venv/bin/python -m app.reset_protection_password
```

必须指向原实例的 `DATA_DIR`，不能创建一个新空目录代替。看到“保护密码已重设”后，用新密码重新进入隐私或管理页面；隐私标记、儿童适宜性选择与各设备儿童状态保留，原有解锁授权全部撤销。该命令不修改写操作访问令牌，也不移动或删除媒体文件。
