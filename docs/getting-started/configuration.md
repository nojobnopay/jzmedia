---
version: 0.19.0
reviewed: 2026-10-02
---

# 配置参考

[本目录首页](README.md) · [部署教程](deployment.md)

环境变量模板是仓库的 `.env.example`（仓库根目录）。首次复制为 `.env` 后，Docker Compose 和宿主启动脚本才会读取配置。修改 `.env` 后需重建容器或重启宿主进程；仅修改环境变量通常不必重新构建镜像，可用 `docker compose -f docker-compose.yml up -d --force-recreate`。

## 配置优先级

TMDB 凭据、代理、语言、图片源及访问令牌支持设置页修改：**数据库非空配置 → 环境变量 → 默认值**。保存立即生效；「恢复跟随 .env」清除数据库覆盖，并不一定清空最终有效值。设置页显示来源及脱敏凭据。

其他播放/存储调优变量通常从环境读取，不能假定都有设置页开关。

## 基础与连接

| 变量 | 默认/用途 | 注意事项 |
|---|---|---|
| `APP_PORT` | `8080`，访问端口 | Compose 和 start.sh 使用；容器服务仍监听 8080 |
| `APP_VERSION` | 模板为 `latest`，本地镜像标签 | 不表示从公共镜像仓库下载 |
| `MEDIA_HOST_PATH` / `DATA_HOST_PATH` | `./media` / `./data` | 仅 Compose 宿主卷映射 |
| `MEDIA_ROOT` / `DATA_DIR` | 宿主默认 `./media` / `./data` | Compose 固定覆盖为 `/media` / `/app/data` |
| `UID` / `GID` | 模板为 `1000` / `1000` | 按宿主实际用户填写；Compose 缺省回退 0 |
| `TMDB_READ_TOKEN` / `TMDB_API_KEY` | 空 | Read Token 优先；支持设置页覆盖 |
| `TMDB_PROXY` | 空，直连 | 运行时 API 和图片下载代理 |
| `TMDB_LANGUAGE` | `zh-CN` | 元数据语言 |
| `TMDB_IMAGE_BASE` | `https://image.tmdb.org` | 图片源；设置页可改 |
| `BUILD_HTTP_PROXY` | 空 | 仅镜像构建中的 pip/npm 代理；不等同于运行时代理或 Docker 拉取镜像代理 |
| `JZMEDIA_TOKEN` | 空，写接口开放 | 非空时 API 写操作需令牌；GET 和直链仍开放 |
| `LOG_LEVEL` | `INFO` | 应用日志；排障可短期用 DEBUG |
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
| `FFMPEG_PROBESIZE` | `2097152` 字节，远程转码输入探测量 |
| `FFMPEG_ANALYZEDURATION` | `1000000` 微秒，远程转码输入探测时长 |
| `FFMPEG_RW_TIMEOUT_US` | `30000000` 微秒，远程输入读写超时 |
| `TRANSCODE_CACHE_GB` | `10` GB；超限回收到约 90%，活跃目录受保护；0 只按 TTL 清理 |
| `PREVIEW_CACHE_GB` | `2` GB；独立缩略图缓存限额，不按转码 24h TTL 清理 |
| `PREVIEW_INTERVAL` | 未设置时本地 10 秒、远程 20 秒；有效范围 5～60 秒 |

三个 `FFMPEG_*` 探测/超时变量可用 0 关闭限制，仅用于远程转码输入，不限制 ffprobe 元数据探测。缓存限额有活跃保护和扫描节流，可能暂时超过配置值。

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
