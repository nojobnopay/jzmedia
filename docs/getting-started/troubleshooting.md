# 部署排障

[本目录首页](README.md)

按现象定位：[启动与网页](#启动与网页) · [构建与更新](#构建与更新) · [存储与健康状态](#存储与健康状态) · [资料与令牌](#资料与令牌) · [播放与电视](#播放与电视)。命令从部署目录执行。

## 启动与网页

### 无法访问网页

先按容器启动方式检查状态和日志。Compose 实例：

```bash
docker compose -f docker-compose.yml ps
docker compose -f docker-compose.yml logs --tail=100 mymedia
```

`docker run` 实例（自定义容器名时替换 `jzmedia`）：

```bash
docker ps -a --filter name=jzmedia
docker logs --tail=100 jzmedia
```

容器已运行时，再检查服务器地址与宿主防火墙。bridge 模式检查端口映射；host 模式直接检查宿主监听端口，默认是 `8080`。从另一台设备访问要使用服务器地址，不能使用 `localhost`。

### 启动时报 Permission denied

核对 Compose 的 `user` 或 `docker run` 的 `--user`，以及数据目录属主/ACL 和媒体目录可读性；若 Compose 的 `user` 保留变量写法，同时检查可选 `.env` 中的 UID/GID。宿主路径与容器路径需分别填写，具体对应关系见[安装教程](deployment.md#docker-paths)。

### 端口被占

Linux/WSL 可用 `ss -ltnp` 查看占用。bridge 模式下，Compose 修改 `ports` 的宿主端口（保留变量写法时可修改 `.env` 的 `APP_PORT`），`docker run` 修改 `-p` 的宿主端口，再重新创建容器；宿主直接运行修改 `APP_PORT` 后重启。host 模式不使用端口映射，默认镜像直接监听 `8080`，需调整冲突服务或明确修改容器启动命令，不能只修改 `APP_PORT`。也可确认后结束占用端口的自有进程，再重新启动。

### host 配置仍提示 ports／networks 冲突，或不识别 !reset

`compose.host.yml` 必须放在最后一个 `-f` 参数中，防止后续覆盖文件重新合入端口或网络。先运行与启动相同参数的 `docker compose ... config --quiet`，核对 Compose 版本及覆盖顺序。旧版 NAS Compose 若不支持 `!reset`，可在自己的配置中直接删除应用服务的 `ports`、`networks` 并增加 `network_mode: host`；不要只删掉 `!reset` 标记。完整步骤见 [host 部署](deployment.md#host-network)。

### Compose 不支持 env_file 的 path 或 required 字段

本仓库通过 `env_file` 的可选文件写法支持无 `.env` 启动，需要 Docker Compose 2.24.0 或更新版本。用 `docker compose version` 核对版本，升级 Compose 或 NAS 的容器管理组件；暂不能升级时，可按安装教程使用 [docker run](deployment.md#docker-run)。已有 `.env` 继续保留，其中配置仍可能被现有实例使用。

## 构建与更新

### 页面还是旧界面

- **Docker**：确认已拉取目标版本的 GHCR 镜像、加载新版本镜像文件或从新源码构建镜像，并让启动配置指向对应的完整镜像名，再按原来的 Compose 或 `docker run` 方式重新创建容器。只重启旧容器不会应用新镜像，具体步骤见[升级](operations.md#升级)。
- **宿主直接运行**：重新运行 `./start.sh`，让脚本按源码与资源内容摘要检查构建；也可显式执行 `npm run build --prefix frontend`。

完成后刷新浏览器，并确认访问的是正确端口。

### 帮助站返回 503 或 404

`/help/` 返回 503 通常表示尚未构建帮助站，在源码环境执行：

```bash
npm --prefix docs ci
npm --prefix docs run build
```

Docker 的网页与帮助站随镜像交付。从 GHCR 拉取已修复版本、获取并加载已修复的镜像文件，或重新构建包含帮助站产物的镜像后，再重新创建容器，见[升级](operations.md#升级)。缺页 404 则核对公开页面清单及链接；帮助站 `/help/` 和 FastAPI `/docs` 是不同入口。

### 本地镜像不存在或提示 pull access denied

先按镜像来源核对本机完整镜像名：

```bash
docker image ls ghcr.io/nojobnopay/jzmedia
docker image ls jzmedia
```

GHCR 和当前 Release 归档使用 `ghcr.io/nojobnopay/jzmedia`，源码构建默认使用 `jzmedia`，早期归档以 `docker load` 打印的镜像名为准。Compose 的 `image` 和 `docker run` 命令末尾的完整镜像名须与实际镜像一致；如果模板通过 `APP_VERSION` 替换标签，修改该变量不会把仓库名 `jzmedia` 改为 GHCR 地址。

在线安装按[从 GHCR 拉取镜像](deployment.md#从-ghcr-拉取镜像)准备镜像，并从 [GitHub Releases](https://github.com/nojobnopay/jzmedia/releases) 核对所需标签是否已发布。离线导入和源码构建按安装教程[准备本地镜像](deployment.md#其他-docker-安装方式)，使用 `--pull=never`（`docker run`）或 `--pull never`（Compose）避免访问远端。已导入固定版本却仍指定本地不存在的 `latest` 时，也会启动失败。

当前 jzmedia 尚未发布到 Docker Hub，不能通过反复 `docker pull jzmedia` 获取成品。使用 Compose 时明确指定 `-f docker-compose.yml`，避免加载开发 override。

### Docker 构建或拉取超时

- **GHCR 成品镜像拉取失败**：检查是否能访问 `ghcr.io`、镜像全名和发行标签是否正确，以及 Docker daemon 的网络/代理。无法在线拉取时，可改用[Release 镜像文件](deployment.md#加载镜像文件)。
- **源码构建失败**：先分清是 Dockerfile 的 Node.js/Python 等基础镜像拉取失败，还是构建中的 pip/npm 下载失败。基础镜像由 Docker daemon 拉取；`BUILD_HTTP_PROXY` 只影响构建阶段的下载步骤，不能修复 daemon 拉取。
- **离线导入失败**：检查归档是否下载完整、是否适合目标 CPU 架构，以及 `docker load` 的具体错误。加载已有 jzmedia 镜像文件无需重新下载基础镜像或构建依赖。

Docker Desktop 的 daemon 代理检查见下方[代理故障](#wsl--docker-desktop-代理故障)。运行时 TMDB 请求单独使用 `TMDB_PROXY`，不能用来修复 GHCR 拉取或源码构建。升级后仍是旧页面时，按[升级流程](operations.md#升级)核对目标镜像及重建容器步骤。

## 存储与健康状态

### SMB 连不上

在媒体库“连接”或新建时执行测试，核对共享名、帐号、权限及连接地址。SMB direct 无需 mount 权限。

### NFS 或强制挂载失败

检查宿主挂载能力及容器权限；可先在宿主完成挂载，再以本地路径建库。

### health 为 degraded

查看 `/api/health` JSON 中的 `db`、`media` 子项，修复实际失败点。SMB 直读连接需另通过媒体库连接检查确认。

### 数据或转码空间不足

查看 `disks.filesystems` 中的 `available_bytes`；数据与转码同卷时不能把容量相加。`disks.ok` 为 `false` 时先处理目录或权限错误，顶层 `status` 为 `ok` 不代表容量已验证。

## 资料与令牌

### TMDB 401 或找不到候选

1. 在设置页检查凭据来源，确认是否仍有数据库覆盖。
2. 核对 Read Token/API Key，保存并测试连接。
3. 查看来源冷却状态；网络修复后按[在线资料服务](../user-guide/settings.md#在线资料服务)重试。

### 写操作提示鉴权失败

设置正确的访问令牌。GET 能打开网页，不表示写令牌有效。

## 播放与电视

### 视频不能播放

查看 `/api/health` 的 `ffmpeg`、`ffprobe`、播放器的播放信息及服务器日志。按具体现象继续查[播放排障](../user-guide/troubleshooting.md#播放与画质)。

### 硬件后端显示 software

检查设备映射、组权限、驱动及编码支持。`auto` 回落软件属于允许行为，不能据此认为硬件已经可用。

### 播放提示服务器繁忙或 429

新 HLS 产物已达 `MAX_TRANSCODES` 上限，默认 2。等待其他任务完成或关闭自身播放后重试；同输出可共享产物，勿终止其他设备会话。

### 电视能握手但不能保存或播放

GET 握手只报告 `auth_required`。先用客户端令牌验证检查 POST 权限，再核对原生能力、版本与实际片源。

<span id="wsl--docker-desktop-代理故障"></span>

## WSL / Docker Desktop 代理故障

若宿主 `curl` 正常，而 Docker 拉 GHCR 成品或基础镜像时报 HTTPS proxy 或 timeout，先区分 **Docker daemon 拉镜像** 与 **镜像构建中的 pip/npm 下载**。前者需要修复 Docker Desktop 的代理配置；`BUILD_HTTP_PROXY` 只传入构建阶段，不能修复 daemon 拉取。

已有 `crane` 且可通过可用代理访问镜像仓库时，也可以在宿主拉取所需基础镜像并导入 Docker 后重试构建，例如：

```bash
crane pull --platform linux/amd64 python:3.12-slim /tmp/jzmedia-python-3.12.tar
docker load -i /tmp/jzmedia-python-3.12.tar
```

这只是针对对应架构和镜像的应急方法；Dockerfile 中其他基础镜像仍需可获取。Compose 构建期下载超时，可通过进程环境或可选 `.env` 设置容器可访问的 `BUILD_HTTP_PROXY`。运行时 TMDB 请求单独使用 `TMDB_PROXY`，不要混用三种代理配置。

## 提交问题

提供应用版本、部署方式、脱敏错误信息和可复现步骤。播放问题可复制播放器诊断信息；分享前检查文件名、路径或网络信息。不要贴 `.env`、访问令牌、SMB 密码或数据库。

继续查阅 [播放器手册](../user-guide/player.md) 与 [功能排障](../user-guide/troubleshooting.md)。
