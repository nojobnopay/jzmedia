---
version: 0.29.2
reviewed: 2026-10-18
---

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
| `JZMEDIA_TOKEN` | 空，写接口开放 | 非空时全部 API 写请求需令牌 |

`BUILD_HTTP_PROXY` 不影响运行时请求或 Docker 拉取基础镜像的代理；三者的区别见[部署排障](troubleshooting.md#wsl--docker-desktop-代理故障)。

`JZMEDIA_TOKEN` 也保护网页播放决策、会话建立和进度保存。浏览、海报、媒体直链等 GET 仍开放，因此它不是完整的读取访问控制。

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
| `PREVIEW_MODE` | `auto`：普通本地片源批量抽取关键帧并补缺失位置，不兼容时逐点恢复，普通远程片源保持逐点精确抽帧；P5 先逐点精确读取，确认需要预读后暂时用有界短窗口提取相邻采样点；`accurate` 强制逐点精确抽帧 |
| `PREVIEW_THREADS` | `2`，解码及 P5 软件 Vulkan 工作线程各限制为 1～4；FFmpeg 滤镜与拼图仍为单线程 |
| `PREVIEW_AUTO_MODE` | `all`；`local` 只处理本地视频库，`off` 关闭自动预览处理及已有影片入队 |
| `PREVIEW_AUTO_START_HOUR` / `PREVIEW_AUTO_END_HOUR` | 服务进程本地时间 `3` / `6`；Docker 默认按 `Asia/Shanghai`，相同小时表示全天 |

`MAX_TRANSCODES` 按产物计数：从初始化到整个 FFmpeg 生命周期都占额，预读窗口暂停也不提前释放额度，相同产物共享额度。达到上限后，新产物请求返回 429。

普通 HLS 播放默认以提前生成约 120 秒为目标，到达高水位暂停 FFmpeg，消费到剩余约 60 秒时恢复；GOP、巡检间隔和在途分片可能使实际超前量超过目标。多客户端共享产物时合并各自需求，明确启动的整片预缓存继续生成。此项适用于 fMP4 / TS，不影响有限 MP4 兼容片段、画质或完整缓存复用；平台不支持进程暂停和恢复信号时保留原行为，诊断标记 `window_supported=false`。它控制提前量，`TRANSCODE_READRATE` 则限制读取速度，两者用途不同。

`TRANSCODE_READRATE=0` 不额外手动限速，按需窗口仍然生效。将 `TRANSCODE_LOOKAHEAD_SECONDS` 设为 `0` 可关闭水位控制，已有的手动读取速率设置仍保留。

HTTP 输入按 FFmpeg 支持情况启用有限断链重连；缺少所需重连参数时，保留原生成行为，诊断标记 `window_supported=false`。网络持续中断或片源不支持恢复读取时仍可能失败，需要重新加载影片；本地文件和本地挂载路径不依赖此重连能力。

三个 `FFMPEG_*` 探测/超时变量可用 0 关闭限制，用于远程转码、预览抽帧输入及预览专用的视频时长探测；供播放决策读取完整轨道和附件的常规元数据探测保持原有范围。

预览始终只运行一个任务。本地快速路径每页生成 25 个采样格，画面与目标时间的偏差最多为一个采样间隔；先校验并复用批量抽取的候选画面，仅对未覆盖位置从索引附近补抽，仍不满足时间范围或没有输出时再精确抽帧。无法核对批量画面与时间戳时，安全回退逐点读取。P5 之外的远程片源保持按时间点读取，避免为了预览连续读取整片。采样间隔不因混合抽帧而改变。修改 `PREVIEW_INTERVAL` 会改变缓存标识，旧间隔成品仍占用空间，但当前请求和任务只使用新间隔的缓存；需要保留原成品时不要直接更改间隔。

预览单独确定有效视频时长：优先读取视频流的时长或可信结束时间标签，缺失时只探测末尾最多 300 秒的视频包。容器尾部只有音频时，采样总数止于有效画面；播放器的片长与跳转时间不变。探测限时并响应取消和播放让行，失败时保留原时长，让抽帧报告具体失败；不会通过读取整片来补足时长信息。

除下述 Dolby Vision P5 专用路径外，预览硬件解码跟随 `TRANSCODER`，直接验证片源的解码能力，不以播放的编码探测结果代替。`auto` 在可见 Intel/NVIDIA 设备上尝试解码，失败后当前影片改用软件；`sw` 禁用硬件尝试。Intel 容器设备与权限见下方[硬件设备配置](#intel-硬件设备)，仅设置环境变量不会授予设备访问权。自动任务仍让位于播放；立即批量生成遇实际运行的在线转码时终止当前未提交页，待空闲后重新生成该页，预读窗口暂停的转码不阻塞手动生成。

Dolby Vision 带兼容基底的片源沿原路径转换；P7／兼容标识 6 在 HEVC、PQ 传递特性和 BT.2020 色域均确认时使用 HDR10 基底。P5／兼容标识 0 没有普通 HDR10 基底，需要逐帧有效 RPU，由 FFmpeg 的 `libplacebo` 滤镜转换成 SDR 预览；无可用 RPU 时仍会失败，不通过忽略 Dolby Vision 元数据生成偏色图片。

P5 在 `auto` 模式下确认某点需要预读恢复后，暂时用短窗口共用相邻采样点的解码和色彩元数据，减少重复读取；每次最多提取两张、输入跨度最多 42 秒，窗口无法核对时按点恢复，采样间隔和画质保持原值。P5 预览路径固定使用软件解码和 Mesa llvmpipe 软件 Vulkan，无需 `/dev/dri` 或额外显卡配置。运行环境必须同时具备支持 `apply_dolbyvision` 的 FFmpeg `libplacebo` 滤镜与软件 Vulkan 驱动；项目 Docker 镜像安装 `mesa-vulkan-drivers`，宿主运行或自定义镜像需自行满足相同条件。软件 Vulkan 工作线程通过预览子进程的 `LP_NUM_THREADS` 自动取 `PREVIEW_THREADS` 的 1～4 范围值，无需另设开关。缺少滤镜、驱动或无法初始化时，补齐环境后从失败清单重试；仅通过合成能力检查不代表实际片源的 RPU 和颜色已验收。

新生成的预览单格固定为 480×270 像素，中间帧使用无损 PNG，最终拼图只进行一次 JPEG 编码。所有客户端共用这套图片，画质参数固定，无需额外设置环境变量；调整采样间隔只改变预览节点的密度，不会提高单张图片的清晰度。旧版 160×90 像素完整缓存可继续显示，已有完整的 480×270 像素成品也继续复用；抽帧规则使用独立缓存标识，升级不会自动全库重建。更新画质与重试失败项的方法见[生成进度缩略图](../user-guide/playback-previews.md#生成进度缩略图)。

会话维护随应用启动运行，不需再播放影片才触发回收。正常关闭释放该客户端持有关系；关闭请求未送达时，未认领会话超过 90 秒、已认领会话无取流或心跳超过 600 秒，在下一轮维护时回收。其他客户端或明确启动的预缓存仍持有同一产物时，任务继续。

播放缓存按 24 小时 TTL 和容量维护回收，有足够可淘汰目录时超限回收到约 90%；活跃、窗口暂停及新建保护期内的目录受保护，因此 `TRANSCODE_CACHE_GB` 是软目标。预读窗口不会删除已播放的历史分片，不能作为硬磁盘配额。

缩略图按自身额度回收，不按转码的 24 小时 TTL 清理；其活跃保护也可能使占用暂时超过配置值。自动预览为扫描新入库项排队；设置页也可把已有本地视频加入闲时队列，默认夜间且无人播放时处理。缩略图占用达到额度的 90% 会暂停，避免生成后立即淘汰。应用数据总占用达到总预算（默认 20 GB）时网页提醒并暂停自动预览，不会自动删除数据库或图片。

在“设置 → 系统维护 → 应用数据与缓存”通过滑条或数值输入调整总预算与两项缓存额度。保存时需让两项正额度加基础数据自动预留不超过总预算；已存在的超配组合、旧播放额度 0 及环境变量值仍可读取，不会自动改写，但下次网页保存必须符合分配条件。保存到数据库后立即生效并优先于上述环境变量，重启后保留。自动预留算法、分配色条和清理步骤见[调整应用数据与缓存](../user-guide/settings.md#调整应用数据与缓存)。

### Dolby Vision P5 网页播放

P5 网页转换默认关闭。需要试用时设置 `DOVI_P5_WEB_ENABLED=true` 并重新创建容器；该开关不影响电视原视频播放、普通硬件转码及缩略图。启用仅允许逐片验证，不代表已经通过目标服务器的实时性能验收。关闭时不因网页播放触发 P5 硬件探测，界面提供电视原视频、外部播放器及兼容版本入口。

P5／兼容标识 0 的网页播放需要完整的 Intel 硬件转换链：HEVC Main10 硬件解码保留原始帧及 RPU，`libplacebo` 完成杜比映射、缩放与 BT.709 SDR 输出，再由 VAAPI 编为 H.264。仅支持 fMP4 HLS；TS、普通 MP4 兼容模式及图片字幕烧录会明确提示无法播放，文字字幕和客户端 PGS 字幕继续使用原流程。无法确认逐帧色彩元数据或硬件转换失败时停止，错误区可复制直链交给支持该片源的外部播放器，或选择已有的兼容版本。

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

项目 amd64 Docker 镜像安装完整的 `intel-media-va-driver-non-free` iHD 驱动；其他架构不会安装该 Intel 驱动。镜像内 `/usr/share/jzmedia/intel-driver-packages.txt` 与 `intel-driver-sha256.txt` 分别记录新增包的版本和下载哈希。宿主运行或自定义镜像需满足同样的驱动、FFmpeg/libplacebo 和 Intel ANV Vulkan 条件。

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
