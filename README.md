# jzmedia

**把硬盘里的电影和剧集，变成好找、好看、好管理的私人影视库。**

jzmedia 是面向家庭本地收藏的开源影视库。连接电脑或 NAS 上已有的媒体，在浏览器里逛海报墙、找片和续看，也能用 Android TV 客户端在电视上继续看。影片由使用者自行提供，jzmedia 不提供下载源。

[开始安装](docs/getting-started/deployment.md) · [下载发行版](https://github.com/nojobnopay/jzmedia/releases) · [在线图文帮助](https://nojobnopay.github.io/jzmedia/) · [Android TV](android-tv/README.md#已有-apk安装并连接) · [版本说明](CHANGELOG.md)

<span id="怎样工作"></span>

## 快速开始

<span id="运行条件与推荐配置"></span>

适用于已安装 Docker 的 **Linux amd64（x86_64）** 电脑或 NAS，在家庭或受信任网络中使用。原生 Linux／NAS 需要 DLNA 电视自动发现时，推荐将下面命令的 `-p 8080:8080` 替换为 `--network host`；先核对 [host 部署的端口与代理要求](docs/getting-started/deployment.md#host-network)。默认 bridge 安装仍可用于普通网页与 Android TV 播放。在准备用来部署的目录打开终端，执行：

```bash
mkdir -p media data
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

1. **打开网页**：访问 `http://localhost:8080`；安装在其他电脑或 NAS 上时，把 `localhost` 换成服务器地址。能看到首页后，按[服务检查](docs/getting-started/deployment.md#check-service)确认数据库、媒体目录与播放工具正常。
2. **添加媒体**：把一部影片放进当前目录的 `media/`，在空库首页点“开始配置”，按[首次入库图解](docs/user-guide/onboarding.md)扫描并试播。容器内的目录填 `/app/media`；已有媒体目录可替换 `-v` 左侧的宿主路径。
3. **保存数据**：`data/` 保存数据库、海报与缓存，重建容器时保留此目录和原挂载。命令使用当前用户的 UID/GID，媒体目录需可读取，数据目录需可写。

`latest` 跟随最新发行镜像；固定版本部署请从 [Releases](https://github.com/nojobnopay/jzmedia/releases) 选择实际发布的 `vX.Y.Z` 标签。启动失败或已有同名容器时，先看[部署排障](docs/getting-started/troubleshooting.md)，不要重复创建实例。

使用 Compose、离线镜像或 Linux/WSL 源码运行，见[完整安装教程](docs/getting-started/deployment.md)；硬件选配与转码设备设置见[硬件与配置建议](docs/getting-started/deployment.md#硬件与配置建议)。

<span id="能做什么"></span>
<span id="值得试试的特色"></span>
<span id="日常看片也有这些方便之处"></span>

## 主要功能

<span id="追剧时看清播出进度和自己的收藏"></span>
<span id="多版本收藏各自记住看到哪里"></span>

### 电影剧集与多版本

电影和剧集按版本选择文件、独立记住进度，追剧可沿选定版本连续观看。剧集还能对照播出目录查看已收藏与缺少的分集，播出资料依赖 TMDB 配置和网络。[电影版本](docs/user-guide/movie-versions.md) · [追剧与连播](docs/user-guide/watch-tv.md)

<span id="在网页里也能保留字幕的样式"></span>

### 字幕与播放

网页支持 ASS/SSA 样式字幕、PGS 图片字幕、音轨切换和进度预览，并按片源与设备能力选择直放或兼容处理；实际播放与转码能力取决于设备、服务器和网络。[音轨与字幕](docs/user-guide/subtitles.md) · [播放控件与画质](docs/user-guide/playback-controls.md) · [预览与预缓存](docs/user-guide/playback-previews.md)

<span id="一句话从自己的片库里找片"></span>

### 搜索与发现

按类型、地区、评分和观看状态筛选，也可沿演员、合集找片。自然语言搜索和 AI 匹配建议需自行配置模型服务与 API Key，普通搜索与手动匹配可独立使用。[找片与筛选](docs/user-guide/find-movies.md) · [合集与人物](docs/user-guide/collections.md) · [辅助核对匹配](docs/user-guide/metadata.md#辅助核对匹配)

<span id="整理收藏前先看清每一处路径变化"></span>

### 整理与撤销

扫描入库后即可观看；需要整理时，先预览路径变化和冲突，再确认移动，支持 Kodi/Plex 命名档及关联文件处理。电影可预览还原首次位置，剧集可按历史批次预览撤销。[整理电影](docs/user-guide/organizing.md) · [整理剧集](docs/user-guide/tv-organizing.md) · [文件管理与上传](docs/user-guide/files.md)

<span id="沿用现有片库资料也能留在文件旁"></span>

### 本地/NAS 与 NFO

连接本地目录、SMB 或 NFS，沿用已有 NFO 与海报离线入库，也可按策略将资料写回文件旁。只读库仍可扫描、浏览和播放。[连接片库](docs/user-guide/libraries.md) · [资料来源与 NFO](docs/user-guide/metadata.md#来源链与离线资料)

### 网页与电视

电脑、手机网页与 Android TV 共享观看进度，各设备可独立选择儿童或隐私模式；也可向支持 DLNA 视频接收的电视投屏，兼容性取决于接收设备。[安装电视端](android-tv/README.md#已有-apk安装并连接) · [观看模式](docs/user-guide/settings.md#儿童模式与隐私模式) · [DLNA 投屏](docs/user-guide/playback-controls.md#投屏到电视)

## 界面预览

![电影海报墙与继续观看](docs/assets/screenshots/movie-library.webp)

*界面演示使用虚构影片资料与原创海报。*

<span id="文档"></span>

## 文档与帮助

[在线图文帮助](https://nojobnopay.github.io/jzmedia/)无需启动应用，跟随最新正式版本；已安装实例的“帮助”（`/help/`）对应安装版本，可离线使用。仓库 Markdown 可直接阅读，完整图文布局与交互请在帮助站查看。

| 想做什么 | 从这里开始 |
|---|---|
| 连接片库，播放第一部影片 | [首次入库图解](docs/user-guide/onboarding.md) |
| 找片、追剧、调字幕或整理文件 | [用户任务目录](docs/user-guide/README.md) |
| 在电视上安装并连接服务 | [Android TV 安装与连接](android-tv/README.md#已有-apk安装并连接) |
| 安装、配置、升级与备份 | [部署与维护](docs/getting-started/README.md) |
| 影片不见了、匹配不对或播放失败 | [按现象排查](docs/user-guide/troubleshooting.md) |

<span id="运行边界与参与方式"></span>

## 适用范围

适合家庭或受信任网络中的单实例部署。儿童与隐私模式按当前设备限制浏览和媒体访问；可选访问令牌独立保护写操作，包括网页播放的启动和进度保存。当前没有多用户登录与完整权限系统；清除客户端数据可重置该设备的儿童模式，隐私内容仍需密码解锁。

## 参与开发

项目使用 FastAPI、SQLite 和 Vue 3，Android TV 为独立 Gradle 工程。欢迎通过 [GitHub Issue](https://github.com/nojobnopay/jzmedia/issues) 反馈问题，或提交改进；API 调试文档位于运行实例的 `/docs`。

<span id="仓库目录"></span>

[开发者参考与仓库约定](docs/developer/README.md#仓库目录) · [统一发布](docs/developer/releasing.md) · [文档制作与发布](docs/developer/documentation.md) · [开发计划](docs/roadmap/README.md)

## 许可证

本项目采用 [BSD 3-Clause License](LICENSE)，版权归 joey.zhou 所有。第三方组件和素材保留各自的许可证与来源说明，设计素材见 [design/README.md](design/README.md)。
