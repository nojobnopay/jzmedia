# 配置参考

[本目录首页](README.md) · [部署教程](deployment.md)

默认安装无需 `.env`。Docker 的端口、卷和运行用户直接在 Compose 文件或 `docker run` 参数中设置；常用资料服务、智能辅助与访问令牌可在网页设置中填写。完整启动步骤见[安装教程](deployment.md)。

修改启动环境变量后需重新创建容器或重启宿主进程，通常不必重新构建镜像。Compose 可执行 `docker compose -f docker-compose.yml up -d --no-build --pull never --force-recreate`；使用自定义覆盖文件时保留原来的全部 `-f` 参数。`docker run` 实例需保留原端口、卷、用户及设备参数，停止并删除旧容器后重新创建；`docker restart` 不会更新容器环境变量。

按配置类型查找：[路径与连接](#基础与连接) · [播放与缓存](#播放与缓存) · [智能辅助](#智能辅助) · [元数据落盘](#元数据与落盘)。

## 配置优先级

TMDB 凭据、代理、语言、图片源及访问令牌支持设置页修改：**数据库非空配置 → 环境变量 → 默认值**。保存立即生效；“恢复跟随 .env”清除数据库覆盖，并不一定清空最终有效值。设置页显示来源及脱敏凭据。智能辅助使用独立配置接口，也遵循数据库优先，Key 留空保留、明确清除后回退环境值。

其他播放/存储调优变量通常从环境读取，不能假定都有设置页开关。

## 可选的环境变量文件

应用读取进程环境变量，不会自己打开 `.env`。Docker 的 `env_file` / `--env-file` 在宿主读取文件后将变量传入容器，不会将文件复制进去；镜像也不打包该文件。因此，容器内没有 `.env` 不影响正常运行。网页保存的设置在数据目录的数据库中，需保留原数据卷。

| 启动方式 | 无 `.env` 时 | 需要自定义环境变量时 |
|---|---|---|
| Compose | 使用 Compose 字段和应用默认值 | 在服务的 `environment` 中填写；也会读取宿主已有的 `.env` |
| `docker run` | 直接使用启动命令 | 添加 `-e 名称=值`，或在文件确实存在时添加 `--env-file .env` |
| `./start.sh` | 使用脚本和应用默认值 | 传入进程环境变量；脚本也会读取项目根已有的 `.env` |

需要集中保存变量时，可自行创建 `.env`，只填写要覆盖的项；仓库 `.env.example` 是可选参考，不要求整份复制。已有 `.env` 应继续保留，不会自动生成或覆盖。例如只调整转码缓存时，文件可以只有一行：

```dotenv
TRANSCODE_CACHE_GB=8
```

仓库 Compose 用 `env_file` 的 `required: false` 允许文件缺失，需要 Compose 2.24.0 或更新版本，见 [Docker 可选环境文件说明](https://docs.docker.com/compose/how-tos/environment-variables/set-environment-variables/#additional-information-1)。这个选项不影响显式传入的 `docker compose --env-file 文件`：该命令行参数指定的文件仍须存在。

Compose 的 `${变量:-默认值}` 用于替换 YAML 中的值，`env_file` 则将变量传入容器。保留仓库变量写法时，可用 `.env` 设置镜像标签、端口、目录和用户；若已把对应字段改为固定值，修改同名变量不会改变该字段。宿主终端中的应用变量也不会自动进入 Compose 容器，需在 `environment` 中声明或通过 `env_file` 提供。

## 基础与连接

### 运行路径与端口

| 变量 | 默认/用途 | 注意事项 |
|---|---|---|
| `APP_PORT` | `8080`，访问端口 | Compose 变量写法和 start.sh 使用；也可直接改 `ports` 或 `docker run -p` |
| `APP_VERSION` | `latest`，Compose `image` 中的标签变量 | 也可直接改 `image`；加载镜像后使用实际标签，不表示从公共仓库下载 |
| `MEDIA_HOST_PATH` / `DATA_HOST_PATH` | `./media` / `./data` | Compose 卷的变量写法使用；也可直接改 `volumes` 或 `docker run -v` |
| `MEDIA_ROOT` / `DATA_DIR` | 宿主默认 `./media` / `./data` | Docker 示例设置为 `/app/media` / `/app/data`，须与容器内挂载目标一致 |
| `TZ` | Docker 镜像与主 Compose 默认 `Asia/Shanghai` | 容器本地时区，用于闲时时段和本地日志时间；可覆盖为其他时区。宿主直接运行沿用宿主时区 |
| `UID` / `GID` | 主 Compose 缺省为 `0` / `0`；示例文件为 `1000` / `1000` | 按实际用户改 `user` 或这两个变量；`docker run` 用 `--user` |
| `LOG_LEVEL` | `INFO` | 应用日志；排障可短期用 DEBUG |

`MEDIA_ROOT` 不会改写已登记的库路径；已有 `/media` 部署按[旧路径兼容说明](deployment.md#legacy-media-path)保留映射。

`docker run --env-file .env` 只向容器传入环境变量，不会根据 `APP_PORT`、`APP_VERSION`、宿主路径或 UID/GID 自动生成启动参数。使用该方式时，镜像标签、端口、挂载与容器用户须在命令中明确填写。

### 资料服务、代理与访问保护

| 变量 | 默认/用途 | 注意事项 |
|---|---|---|
| `TMDB_READ_TOKEN` / `TMDB_API_KEY` | 空 | Read Token 优先；支持设置页覆盖 |
| `TMDB_PROXY` | 空，直连 | 运行时 API 和图片下载代理 |
| `TMDB_LANGUAGE` | `zh-CN` | 元数据语言 |
| `TMDB_IMAGE_BASE` | `https://image.tmdb.org` | 图片源；设置页可改 |
| `BUILD_HTTP_PROXY` | 空 | 仅镜像构建中的 pip/npm 下载代理 |
| `JZMEDIA_TOKEN` | 空，不校验写令牌 | 使用至少 8 位英文字母、数字或半角符号；保护常规 API 写请求，观看会话端点例外见下文 |

`BUILD_HTTP_PROXY` 不影响运行时请求或 Docker 拉取基础镜像的代理；三者的区别见[部署排障](troubleshooting.md#wsl--docker-desktop-代理故障)。

`JZMEDIA_TOKEN` 也保护网页播放决策、会话建立和进度保存；模式切换、管理解锁、锁定和活动上报四个观看会话端点不要求写令牌，仍执行会话及密码检查。GET 不受写令牌校验，但列表、海报与媒体读取仍受观看模式限制，管理读取还可能需要保护密码解锁。各类凭据及失败响应见[认证和安全边界](../developer/api.md#认证和安全边界)。

### 远程存储

| 变量 | 默认/用途 | 注意事项 |
|---|---|---|
| `SMB_DRIVER` | `auto` | 挂载可用用挂载，否则 SMB 直读；`direct` 强制直读，`mount` 强制挂载 |
| `ALLOW_SMB_MOUNT` | `1` | `0` 禁止应用内挂载；SMB 直读无需挂载权限 |
| `SMB_CONNECT_TIMEOUT` | `15` 秒 | SMB 连接超时 |
| `MEDIA_PROXY_HOST` | `127.0.0.1` | FFmpeg 访问 SMB 的内部 Range 代理监听地址，保持回环 |
| `SMB_META_TTL` | `3` 秒 | 元数据缓存，0 关闭 |
| `SMB_HANDLE_POOL` / `SMB_HANDLE_TTL` | `8` / `30` 秒 | 读句柄复用上限/空闲时间；池大小 0 关闭 |
| `SMB_LOG_LEVEL` | `WARNING` | SMB 库日志等级 |

应用内 SMB/NFS 挂载需要操作系统 mount 能力及容器相应权限；优先使用现有本地挂载或 SMB 直读。NFS 当前没有独立的用户态直读实现。

## 播放与缓存

| 变量 | 默认/用途 |
|---|---|
| `TRANSCODER` | `auto`；可设 `sw`、`vaapi`、`qsv`、`nvenc`，兼容旧 `HW_ACCEL` |
| `DOVI_P5_WEB_ENABLED` | `false`；P5 网页转换试验开关，显式设为 `true` 后启用 |
| `DOVI_AUTO_HEIGHT` | `720`；P5 网页转换的自动画质上限，仅支持 `720` / `1080` |
| `HLS_SEGMENT_TYPE` | `fmp4`；`ts` 为旧 MPEG-TS 回退路径 |
| `AUDIO_COPY_SAFE` | 非原生 HLS 默认只允许 AAC/MP3 copy；不建议未测试就放开其他编码 |
| `MIN_SEGS_COPY` / `MIN_SEGS_TRANSCODE` | `1` / `2`，起播所需视频分片数 |
| `MAX_TRANSCODES` | `2`，范围 1–8；并发生成 HLS 产物的上限 |
| `TRANSCODE_LOOKAHEAD_SECONDS` | `120` 秒；HLS 按客户端需求控制提前生成量，`0` 关闭，有限正值限制在 10～600 秒，低水位为一半 |
| `TRANSCODE_READRATE` | `0`，不额外手动限速；设为 ≥1 的有限数值限制 HLS 视频重编码读取速率，单位为每秒处理的媒体秒数 |
| `FFMPEG_PROBESIZE` | `2097152` 字节，远程转码与预览输入探测量 |
| `FFMPEG_ANALYZEDURATION` | `1000000` 微秒，远程转码与预览输入探测时长 |
| `FFMPEG_RW_TIMEOUT_US` | `30000000` 微秒，远程输入读写超时 |
| `TRANSCODE_CACHE_GB` | `10` GiB，播放缓存的软淘汰目标；旧值 0 只按 TTL 清理；网页保存容量设置后优先使用已保存值 |
| `PREVIEW_CACHE_GB` | `10` GiB，最低 0.1 GiB；0 不表示禁用；网页保存容量设置后优先使用已保存值 |
| `PREVIEW_INTERVAL` | 未设置时本地 10 秒、远程 20 秒；有效范围 5～60 秒 |
| `PREVIEW_MODE` | `auto` 按片源选择批量或逐点抽帧；`accurate` 强制逐点精确抽帧，具体规则见下文 |
| `PREVIEW_THREADS` | `2`，解码及 P5 软件 Vulkan 工作线程各限制为 1～4；FFmpeg 滤镜与拼图仍为单线程 |
| `PREVIEW_AUTO_MODE` | `all`；`local` 只处理本地视频库，`off` 关闭自动预览处理及已有影片入队 |
| `PREVIEW_AUTO_START_HOUR` / `PREVIEW_AUTO_END_HOUR` | 服务进程本地时间 `3` / `6`；Docker 默认按 `Asia/Shanghai`，相同小时表示全天 |

### 并发与输入调优

`MAX_TRANSCODES` 按输出产物计数，相同产物可由多设备共享；初始化、运行和按需窗口暂停期间均占额度，新产物超限返回 429。`TRANSCODE_LOOKAHEAD_SECONDS` 控制提前生成量，`TRANSCODE_READRATE` 限制视频重编码的输入读取速度，两者可独立关闭；预缓存明确要求生成整片。水位、共享和断链重连机制见[按需生成窗口](../developer/playback.md#按需生成窗口)。

三个 `FFMPEG_*` 探测和超时变量可设为 `0` 关闭限制，适用于远程转码、预览输入及预览专用时长探测；常规播放决策所需的完整轨道和附件探测保持原有范围。持续断网或片源不支持恢复读取时仍可能失败，修复网络后重新加载影片；本地路径不依赖 HTTP 重连。

### 预览生成的条件

普通预览硬件解码跟随 `TRANSCODER`，`sw` 禁用硬件尝试；使用实际片源验证，失败后当前影片改用软件。Intel 容器仍需[设备映射与权限](#intel-硬件设备)，NVIDIA 见[对应配置](#nvidia-硬件转码)，仅设置变量不会授予设备访问权。

P5／兼容标识 0 的预览固定使用软件解码和 Mesa llvmpipe 软件 Vulkan，无需映射 GPU。运行环境须有支持 `apply_dolbyvision` 的 FFmpeg `libplacebo` 滤镜及软件 Vulkan 驱动；项目 Docker 镜像安装 `mesa-vulkan-drivers`，宿主运行或自定义镜像需满足相同条件。`LP_NUM_THREADS` 由预览子进程按 `PREVIEW_THREADS` 自动限制为 1～4，无需另设。缺少滤镜、驱动或有效逐帧 RPU 时，补齐环境或修复片源后从失败清单重试，不会忽略元数据继续生成偏色图片。带兼容基底的 Dolby Vision 仍按已验证的基底生成，详细判断见[缩略图处理规则](../developer/playback.md#倍速缩略图与预缓存)。

修改 `PREVIEW_INTERVAL` 会改变缓存标识，旧间隔成品仍占空间，当前请求和任务使用新间隔。新预览单格固定为 480×270 像素，无画质开关；采样间隔仅改变节点密度，不改变单张清晰度。已有完整成品的复用、画质升级和失败重试见[批量生成与队列管理](../user-guide/playback-previews.md#批量生成与队列管理)，抽帧及有效视频时长的实现说明由[播放设计](../developer/playback.md#倍速缩略图与预缓存)维护。

闲时队列按 `PREVIEW_AUTO_MODE` 和服务进程本地时段运行；扫描新入库项可自动入队，已有本地视频需手动加入。自动任务让位于播放，缩略图占用达到自身额度 90% 或应用总占用达到总预算时暂停。预览全局只运行一个任务，立即批量生成遇实际运行的在线转码也会等待；操作及恢复见[预览任务教程](../user-guide/playback-previews.md#批量生成与队列管理)。

### 缓存额度与生效

播放缓存按 24 小时 TTL 和容量维护回收，有足够可淘汰内容时超限回收到约 90%；缩略图按自身额度回收，不使用该 TTL。活动、窗口暂停和新建保护期内的内容受保护，额度均为软目标；预读窗口也不会删除已播放的历史分片，不能用作硬磁盘配额。应用数据总预算默认 20 GB，超限不会自动删除数据库或图片。活动内容保护、会话失活与缓存回收见[缓存占用与回收](../developer/playback.md#缓存占用与回收)。

在“设置 → 系统维护 → 应用数据与缓存”调整总预算与两项缓存额度时，保存结果立即生效、优先于环境变量且重启保留。两项正额度加基础数据预留须不超过总预算；旧超配组合、旧播放额度 0 与环境值可继续读取，下次网页保存需符合分配条件。分配、清理和保存验证见[调整应用数据与缓存](../user-guide/settings.md#调整应用数据与缓存)。

调整启动变量后，按页首说明重新创建容器或重启宿主进程。先用一部测试影片核对播放器“播放信息”的实际后端和输出，再到视频库资料维护查看缩略图状态及失败原因；普通后端查询和健康检查只读取能力快照，不能代替实际试播。并发满额按[429 排障](troubleshooting.md#播放提示服务器繁忙或-429)处理。

### Dolby Vision P5 网页播放

P5 网页转换默认关闭。需要试用时设置 `DOVI_P5_WEB_ENABLED=true` 并重新创建容器；该开关不影响电视原视频播放、普通硬件转码及缩略图。启用仅允许逐片验证，不代表已经通过目标服务器的实时性能验收。关闭时不因网页播放触发 P5 硬件探测，界面提供电视原视频、外部播放器及兼容版本入口。

P5／兼容标识 0 的网页播放需要完整的 Intel 硬件链：HEVC Main10 解码、Intel Vulkan/`libplacebo` 转色及 VAAPI H.264 编码，设备与权限按下方[配置](#intel-硬件设备)准备。仅支持 fMP4 HLS；TS、普通 MP4 兼容模式和图片字幕烧录会被阻止，文字和客户端 PGS 字幕保持原流程。无法确认逐帧元数据或转换失败时，改用兼容版本、电视原视频或外部播放器。管线与审计机制见[P5 网页色彩转换与发布](../developer/playback.md#p5-网页色彩转换与发布)。

`DOVI_AUTO_HEIGHT` 默认 `720`。只有真实片源在目标服务器完成颜色、处理速度、CPU 及连续播放验收后，才考虑设置为 `1080`；这个变量只是自动画质上限，不代表硬件已经通过验收。自动档 1080p 持续处理不足且缓冲偏低时，可降至 720p 一次；720p 仍无法实时转换会停止。显式选择原画取消尺寸上限，但不能保证实时处理。

管理检查可访问 `/api/stream/backends?refresh=1`，在 `dovi_p5` 中分别核对 `hevc_main10_decode`、`h264_encode`、`intel_vulkan` 和 `combined`，每项提供 `ok` 和安全原因码。普通后端查询及健康检查只读能力快照；尚未探测时显示 `not_tested`，首次 P5 播放或显式刷新才执行合成探测。`enabled` 表示网页试验开关，`experimental: true` 标记试验状态；`available: true` 仅表示合成硬件检查通过，`validated_real_p5: false` 明确表示没有由此完成真实影片验收。播放诊断另提供实际管线、速度及失败原因。修复驱动或权限后重新创建服务再试，不要通过全局 `VK_DRIVER_FILES` 改变缩略图的软件 Vulkan 环境。

P5 新转换规则使用独立播放缓存标识，旧的偏色产物不会复用；普通影片、已生成缩略图及持久预览队列不因此重建。准备升级时保留旧镜像、Compose 和一致数据备份，在无播放时切换；失败可恢复旧镜像及原配置，不必删除缓存或重置数据库。回退的旧版本仍可能无法正确转换 P5，应改用兼容版本或外部播放器。

### 同一电脑边转码边播放

服务器与模拟器等播放器共用一台电脑时，全速转码可能挤占播放所需资源。若客户端缓存已有数十秒仍卡顿，且服务器转码结束后恢复流畅，可设置 `TRANSCODE_READRATE=2`，保留原启动参数重新创建容器或重启宿主进程，再重新打开影片验证。值 `2` 表示按约每秒 2 秒源媒体的速率读取；删除该变量或设为 `0` 恢复默认生成策略，预读窗口继续生效。

此项仅影响新启动的 HLS 视频重编码任务（含预转码），复制流、音频重编码及 MP4 兼容短片不受影响。首批分片和 seek 前的解码预卷允许短时突发，完整缓存仍可复用。限速会延长完整转码的完成时间及并发额度占用；以高倍速播放时，限速值应高于播放倍速并留有缓冲余量。它不改变画质，也不能提高本身低于实时速度的转码性能。

手动读取限速需要 FFmpeg 支持 [`-readrate` / `-readrate_initial_burst`](https://ffmpeg.org/ffmpeg.html#Advanced-options)，可通过 `ffmpeg -h full` 核对。不支持时保持默认 `0`；独立 NAS 无同机播放争用时通常无需额外设置读取速率。该设置与 NVIDIA 覆盖文件相互独立。

### Intel 硬件设备

在 Compose 服务 `mymedia` 下按文件中的注释启用：

```yaml
devices:
  - /dev/dri/renderD128:/dev/dri/renderD128
group_add:
  - "${RENDER_GID}"
```

在宿主用 `stat -c '%g' /dev/dri/renderD128` 核对设备组号，将 `group_add` 中的变量替换为实际组号；使用可选 `.env` 时也可在其中填写 `RENDER_GID`。保留原来的运行用户及数据目录权限，不需特权容器或修改宿主设备权限。`TRANSCODER` 默认就是 `auto`，启动后在 `/api/stream/backends` 检查实际后端。普通播放硬件失败可回落软件，P5 网页播放则要求上方专用能力全部通过。

项目 amd64 Docker 镜像安装完整的 `intel-media-va-driver-non-free` iHD 驱动；其他架构不会安装该 Intel 驱动。镜像内 `/usr/share/jzmedia/intel-driver-packages.txt` 与 `intel-driver-sha256.txt` 分别记录新增包的版本和下载哈希。镜像同时固定已验证的 FFmpeg、libplacebo、Mesa、VAAPI 和 Vulkan 运行库版本，升级这些组件前需重新验证转色与预览。宿主运行或自定义镜像需满足同样的驱动、FFmpeg/libplacebo 和 Intel ANV Vulkan 条件。

使用 `docker run` 时，在创建容器的命令中添加 `--device /dev/dri/renderD128:/dev/dri/renderD128`，并用 `--group-add` 传入上述实际组号；仅修改 `.env` 不会添加设备或组权限。配置需要重新创建容器，先结束正在进行的播放。

### NVIDIA 硬件转码

`compose.nvidia.yml` 是可选覆盖文件，默认不加载；通过 `-f compose.nvidia.yml` 或 `COMPOSE_FILE` 显式加入配置列表时才生效。无 NVIDIA GPU 的设备继续使用原启动命令，文件留在仓库不会产生影响；若强行加载，Docker 可能因无法分配 GPU 而拒绝启动，此时应用尚未运行，不能依靠应用的软件转码回退。

需要支持 NVENC 的 NVIDIA 显卡及可用的容器 GPU 环境：Windows 使用 [Docker Desktop 的 WSL2 后端](https://docs.docker.com/desktop/features/gpu/)和 Windows NVIDIA 驱动；Linux Docker Engine 按 [NVIDIA Container Toolkit 安装说明](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)配置运行时。显卡在宿主可见不代表现有容器已获得 GPU。

在项目根目录叠加 `compose.nvidia.yml`，先检查配置，再在停止观影后重新创建服务：

```bash
docker compose -f docker-compose.yml -f compose.nvidia.yml config --quiet
docker compose -f docker-compose.yml -f compose.nvidia.yml up -d --no-build --pull never --force-recreate mymedia
```

已有自定义覆盖文件的实例需保留全部原 `-f` 参数，再追加 `-f compose.nvidia.yml`；WSL 开发实例也需显式保留 `-f docker-compose.override.yml`。重新创建会中断正在播放的会话。

**预期结果：**浏览器打开服务器的 `/api/stream/backends`，返回 `name: "nvenc"`、`hw: true`。`TRANSCODER` 默认的 `auto` 会执行真实编码探测；已有配置为 `sw` 时，需改为 `auto` 或 `nvenc` 后重新创建容器。烧录字幕等软件专用路径仍可能使用 CPU，实际会话后端以播放调试结果为准。

覆盖文件申请 NVIDIA GPU，并启用 `compute,video,utility` 驱动能力；其中 `video` 提供 NVENC/NVDEC 所需的库，见 [NVIDIA 驱动能力说明](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/docker-specialized.html#driver-capabilities)。使用 `docker run` 时，在原创建命令中添加 `--gpus all -e NVIDIA_DRIVER_CAPABILITIES=compute,video,utility`，其余端口、卷及用户参数保持原配置。

若报告 `Cannot load libcuda.so.1` 或 `libnvidia-encode.so.1`，检查容器的 GPU 申请及上述驱动能力；若 Docker 报无法选择 GPU 设备驱动，先修复宿主 GPU 运行时。`TRANSCODER=nvenc` 本身不会授予容器 GPU。需要恢复时，移除 `-f compose.nvidia.yml`，保留原来的配置参数重新创建服务；若曾强制 `TRANSCODER=nvenc`，同时恢复为 `auto`。

## 智能辅助

以下变量也可在“设置 → 智能辅助”配置；普通扫描和搜索不会自动调用模型。完整请求与密钥保留规则见[智能辅助 API](../developer/api.md#智能辅助)。

| 变量 | 默认/用途 |
|---|---|
| `AI_ENABLED` | `false`，显式启用后才允许业务建议 |
| `AI_PROVIDER` | `deepseek`；可选 `opencode_go`、`compatible` |
| `AI_BASE_URL` | `https://api.deepseek.com`，Chat Completions 基础地址；请求追加 `/chat/completions` |
| `AI_MODEL` | `deepseek-flash`，须与服务商实际可用模型对应 |
| `AI_API_KEY` | 空；仅服务端使用，设置读取只回显掩码；compatible 允许空 Key |
| `AI_TIMEOUT_SECONDS` | `12`，范围 2–60 秒 |
| `AI_DAILY_LIMIT` | `100`，范围 1–10000 次；按 UTC 日计数 |

失败请求与连接测试计入每日限额，本地缓存命中不计。

切换服务商要同步地址、模型和对应 Key；环境变量不会按服务商自动补齐预设。OpenCode Go 的应用配置固定使用 `https://opencode.ai/zen/go/v1`，界面示例为 `glm-5.3-flash`，项目尚未验证真实影视用途与模型效果。自定义服务地址在容器中必须可达，容器的 `localhost` 指容器自身。AI 客户端不继承 TMDB 或系统代理，数据库中的 Key 不以脱敏回显代表加密存储。

## 元数据与落盘

| 变量/库级选项 | 用途 |
|---|---|
| `metadata_providers` | 视频库来源链；默认 `local → tmdb → wikidata`，可启用 TVmaze/Bangumi 等 |
| `DOUBAN_ENABLED` | 默认关闭；显式开启且加入来源链后才尝试候选提示，不是完整豆瓣刮削服务 |
| `IMDB_DATASET_PATH` | 服务器可读的 IMDb 数据集路径，导入任务使用；容器中须挂载该文件 |
| `BANGUMI_UA` | Bangumi 请求 User-Agent，可填写应用标识与联系方式 |
| `TV_EPISODE_NFO` | 控制逐集 NFO；缺省本地库启用、远程库不默认批量写逐集文件 |
| `TV_PLEXMATCH` | 可选剧集 `.plexmatch` 输出，默认不写 |
| `PER_VERSION_META` | `1` 可回退旧的每版本元数据输出；默认按目录和命名档决定 |

命名档、只读与落盘策略在建库/编辑入口管理。完整影响见 [媒体库手册](../user-guide/libraries.md) 和 [元数据手册](../user-guide/metadata.md)。
