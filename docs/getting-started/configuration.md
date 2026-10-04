---
version: 0.20.0
reviewed: 2026-10-04
---

# 配置参考

[本目录首页](README.md) · [部署教程](deployment.md)

环境变量模板是仓库的 `.env.example`。首次复制为 `.env` 后，Docker Compose 和宿主启动脚本会读取配置；使用 `docker run` 时须显式传入 `--env-file .env`。三者的启动步骤见[安装教程](deployment.md)。

修改 `.env` 后需重新创建容器或重启宿主进程，通常不必重新构建镜像。Compose 可执行 `docker compose -f docker-compose.yml up -d --no-build --pull never --force-recreate`；使用自定义覆盖文件时保留原来的全部 `-f` 参数。`docker run` 实例需保留原端口、卷、用户及设备参数，停止并删除旧容器后重新创建；`docker restart` 不会读取修改后的 `.env`。

按配置类型查找：[路径与连接](#基础与连接) · [播放与缓存](#播放与缓存) · [智能辅助](#智能辅助) · [元数据落盘](#元数据与落盘)。

## 配置优先级

TMDB 凭据、代理、语言、图片源及访问令牌支持设置页修改：**数据库非空配置 → 环境变量 → 默认值**。保存立即生效；“恢复跟随 .env”清除数据库覆盖，并不一定清空最终有效值。设置页显示来源及脱敏凭据。智能辅助使用独立配置接口，也遵循数据库优先，Key 留空保留、明确清除后回退环境值。

其他播放/存储调优变量通常从环境读取，不能假定都有设置页开关。

## 基础与连接

### 运行路径与端口

| 变量 | 默认/用途 | 注意事项 |
|---|---|---|
| `APP_PORT` | `8080`，访问端口 | Compose 和 start.sh 使用；`docker run` 用 `-p` 设置，容器服务仍监听 8080 |
| `APP_VERSION` | 模板为 `latest`，Compose 的本地镜像标签 | 加载镜像后填实际标签，如 `v0.20.0`；不表示从公共仓库下载 |
| `MEDIA_HOST_PATH` / `DATA_HOST_PATH` | `./media` / `./data` | Compose 的宿主卷映射；`docker run` 用 `-v` 设置 |
| `MEDIA_ROOT` / `DATA_DIR` | 宿主默认 `./media` / `./data` | Docker 示例设置为 `/app/media` / `/app/data`，须与容器内挂载目标一致 |
| `UID` / `GID` | 模板为 `1000` / `1000` | Compose 用于设置容器用户，缺省回退 0；`docker run` 用 `--user` 设置 |
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
| `HLS_SEGMENT_TYPE` | `fmp4`；`ts` 为旧 MPEG-TS 回退路径 |
| `AUDIO_COPY_SAFE` | 非原生 HLS 默认只允许 AAC/MP3 copy；不建议未测试就放开其他编码 |
| `MIN_SEGS_COPY` / `MIN_SEGS_TRANSCODE` | `1` / `2`，起播所需视频分片数 |
| `MAX_TRANSCODES` | `2`，范围 1–8；并发生成 HLS 产物的上限 |
| `FFMPEG_PROBESIZE` | `2097152` 字节，远程转码输入探测量 |
| `FFMPEG_ANALYZEDURATION` | `1000000` 微秒，远程转码输入探测时长 |
| `FFMPEG_RW_TIMEOUT_US` | `30000000` 微秒，远程输入读写超时 |
| `TRANSCODE_CACHE_GB` | `10` GB；0 只按 TTL 清理 |
| `PREVIEW_CACHE_GB` | `2` GB，最低 0.1 GB；0 不表示禁用 |
| `PREVIEW_INTERVAL` | 未设置时本地 10 秒、远程 20 秒；有效范围 5～60 秒 |

`MAX_TRANSCODES` 按产物计数：从初始化到整个 FFmpeg 生命周期都占额，相同产物共享额度。达到上限后，新产物请求返回 429。

三个 `FFMPEG_*` 探测/超时变量可用 0 关闭限制，仅用于远程转码输入，不限制 ffprobe 元数据探测。

转码缓存超限时回收到约 90%，活跃目录受保护。缩略图使用独立缓存限额，不按转码的 24 小时 TTL 清理；缓存限额有活跃保护和扫描节流，可能暂时超过配置值。

### Intel 硬件设备

在 Compose 服务 `mymedia` 下按文件中的注释启用：

```yaml
devices:
  - /dev/dri:/dev/dri
group_add:
  - "${VIDEO_GID}"
  - "${RENDER_GID}"
```

在宿主用 `stat -c '%g' /dev/dri/renderD128` 等命令核对设备组号，在 `.env` 填相应的 `RENDER_GID`、`VIDEO_GID`，不要照抄另一台机器的数值。保持 `TRANSCODER=auto`，启动后在 `/api/stream/backends` 检查实际后端。识别不到时系统回落软件，指定后端也不能保证目标硬件编码一定成功。

使用 `docker run` 时，在创建容器的命令中添加 `--device /dev/dri:/dev/dri`，并分别用 `--group-add` 传入上述实际组号；仅修改 `.env` 不会添加设备或组权限。

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
