# Android TV 客户端协议 1

核对日期：2026-10-03。本页仅为仓库内开发文档，不进入公开帮助站。APK 与服务器独立发布，协议通过握手确认；首次正式发布时再记录对应的最低服务端发行版本。

## 连接、认证与能力

`GET /api/stream/client-info` 为只读握手，返回：

```json
{
  "protocol_version": 1,
  "features": ["android_tv", "independent_sessions"],
  "auth_required": false
}
```

`POST /api/stream/client-check` 返回相同内容，不修改配置或媒体。它走已有写操作认证中间件，用于验证当前连接的令牌：未配置令牌可直接通过；已配置时使用 `X-Api-Token` 或 `Authorization: Bearer`，错误令牌返回 401。GET 握手成功不等于写操作令牌通过。响应绝不包含服务器令牌。

APK 当前使用 `X-Api-Token`，仅发往保存的服务器 origin 和路径范围，拒绝携带凭据跨 origin 重定向。API 连接使用可取消请求；连接改变、退出和过期响应不得重新进入旧页面或启动旧播放器。

以下 POST 请求显式传 `client: "android_tv"` 和 `caps`：

- `/api/stream/versions`：根据候选版本及设备能力排序。
- `/api/stream/{id}/decide`：选择 `direct|remux|audio_transcode|video_transcode` 或 `blocked`。
- `/api/stream/{id}/sessions`：创建 HLS 客户端会话。
- `/api/stream/prewarm`：可选预缓存接口，使用同一能力和产物规则。

旧客户端不传 `client` 时仍按 `web` 处理。不能用浏览器 `mse` 或 `native_hls` 冒充 Android 原生播放器能力。

| `caps` 字段 | 语义 |
|---|---|
| `video` / `audio` | 设备实际解码器能力，按已有编码名称记录严格布尔值 |
| `probes` | 服务端 `vcaps` 与各音轨 `caps` 的具体候选格式检测结果 |
| `containers` | 原生媒体解析器支持的容器，如 `mp4`、`mkv`、`webm` |
| `hls` | 原生播放器能否处理 HLS |
| `hls_audio` | HLS 封装允许复制的音频编码；必须同时有设备解码能力 |
| `audio_track_selection` | 原文件播放中能否切换音轨 |
| `hdr_formats` | 明确支持的 HDR 格式列表，不能由品牌、`native_hls` 或普通 HEVC 能力推断 |
| `subtitles.webvtt` | 首版兼容文本字幕能力 |

APK 通过 Media3/MediaCodec 查询解码器，并按每个片源的宽、高、帧率、profile/level、音轨声道数和采样率补充具体检测。通用 H.264/AAC 能力代表可接受的服务器输出；某个高规格源格式不支持，不能连带错误关闭较低规格的服务器输出能力。服务器媒体探测/存储新增帧率字段（probe v4、schema v30），旧缓存可重探。

当前 APK 的 HLS 音频复制保守仅声明 AAC/MP3；源文件能解 DTS 或 Dolby 不意味着它们可以安全复制进 HLS。HDR10/HLG 结合解码器与显示能力声明；未验证的 Dolby Vision 不主动放行。真实电视仍可能与检测结果不同，播放器提供重试和兼容播放入口。

fMP4 master 的 `CODECS` 必须包含整个音频组的实际输出编码并集，不能只写默认音轨；Media3 会据此解析其他 rendition。无法复制的音轨以输出 AAC 描述。

## 播放请求与时间轴

播放、进度等接口均需带真实 `kind: movie|episode|extra`；三种 ID 空间相互独立，不能只根据整数 ID 判断归属。源文件/海报/字幕地址使用服务器返回值并按当前连接解析，不能将 NAS 路径当成电视本地路径。

HLS 创建请求示意（`caps` 必须来自当前设备检测）：

```json
{
  "client": "android_tv",
  "kind": "episode",
  "quality": "auto",
  "audio": 0,
  "sub": null,
  "start": 450,
  "caps": {}
}
```

空 `caps` 仅用于说明字段，不代表具备可播放能力。`start` 是源视频秒数，必须有限且非负。`quality` 为 `auto|source|1080p|720p`；`audio`/`sub` 是媒体接口返回列表中的索引。`ff_index` 是 FFmpeg 流序号，不能与 Media3 的 `Format.id` 直接比较（例如 MKV 使用容器 TrackNumber）。APK 对直连按音轨顺序与数量对应；不确定或无法解码时改走 HLS。fMP4 优先按服务端 `NAME=audioN` 选 rendition，TS 单轨模式切换时新建会话。

`decide` 返回 `direct_url` 时，原文件由 Media3 播放且源时间从 0 开始。HLS 返回 `session_id`、`playlist_url`、`method`、`plan`、`subtitle_mode`、`media_start`、`initial_time`、`complete` 和 `caps_hash`。

时间口径必须分开：

- `media_start`：HLS 的片内时间 0 对应源视频的时间。copy seek 可能落在请求点之前的关键帧，转码通常等于请求的 `start`。
- `initial_time`：此次请求在返回 HLS 时间轴上的起播位置；传给 Media3 的起播毫秒为 `initial_time × 1000`。
- 源播放位置：`media_start + player.currentPosition / 1000`。进度保存、字幕、跳转和界面总时长按源视频口径处理。
- 若 450 秒续播复用从 0 开始的完整缓存，则 `media_start=0`、`initial_time=450`；若产物起于 445 秒，则可为 `media_start=445`、`initial_time=5`。

完整缓存可直接在片内定位。未覆盖目标的进行中 HLS 跳转须创建新会话；不能把目标源时间直接写入局部 HLS 进度。播放器关闭旧的本机会话，再创建新请求，不影响其他设备。

进行中的 HLS 清单会持续增长，但播放内容仍是点播。Media3 1.10.1 的 [MaskingMediaSource](https://github.com/androidx/media/blob/1.10.1/libraries/exoplayer/src/main/java/androidx/media3/exoplayer/source/MaskingMediaSource.java) 会把占位时间轴上的初始 0 替换为直播默认位置。因此 APK 先暂停准备 HLS，等真实时间轴到达后，仅对当前请求执行一次 `seekTo(initial_time × 1000)` 再开始播放；后续清单更新不能重复归零。模拟静态 VOD 不能覆盖这个行为，须使用新的临时库及尚未转完的真实服务端清单复验。

## 会话、预缓存与资源限制

每次成功的 `POST sessions` 都返回新的客户端 `session_id`。服务器内部按 `(kind, id, 完整产物键)` 原子共享 FFmpeg 任务：产物键包括源路径/size/mtime、封装、实际音频策略、视频输出、烧录字幕及精确起点；输出目录含该键 SHA-256。改变只影响客户端显示的文本字幕或同一 fMP4 音轨选择，不应重新生成相同产物。

`POST /api/stream/sessions/{sid}/ping` 保活；读取 playlist/分片也认领并刷新会话。APK 播放期间定期保活。`DELETE /api/stream/sessions/{sid}` 仅释放该 sid；只有最后持有者离开才终止仍在运行的 FFmpeg。预缓存是独立持有者，关闭电视不会取消仍在进行的预缓存任务。

`MAX_TRANSCODES` 限制完整进程生命周期，默认 2；同产物加入已有任务不再占一个名额。创建新产物满额返回 429，不抢占别的设备。预缓存任务在异步作业状态中报告资源不足。客户端显示服务器繁忙并允许重试/返回，不无限重试或自动关闭其他会话。

尚未取流的会话有短期回收宽限；客户端请求中途取消且拿不到 sid 时由服务器兜底回收。已有 sid 的过期响应由客户端主动 DELETE。启动中、活动任务及被使用的缓存目录不会被 TTL/手动缓存清理删除，停服会回收 FFmpeg。旧的非哈希目录不迁移到新布局，由原缓存回收规则清理。

## 字幕、进度与生命周期

媒体接口合并内嵌与外挂字幕。文本及 ASS/SSA 使用 `/api/stream/{id}/sub/{idx}.vtt?kind=...`，APK 解析 WebVTT 并在源时间上显示，支持字号和延迟；ASS 的字体、特效和复杂排版不保真。图片字幕采用 `subtitle_mode=burn`，切换需要重新准备播放。

内嵌图片字幕的 `sub_ff_index` 是源文件绝对流号，服务端 overlay 使用 `[0:<ff_index>]`，不能当成字幕类型内序号 `[0:s:<ff_index>]`。外挂图片字幕仍使用第二输入的 `[1:s:0]`。

当前内部字幕计算为 `cueSourceTime = sourcePosition + subtitleDelay`；界面显示的“延迟”符号与内部偏移相反。延迟不能再次叠加到 `media_start` 上，否则续播后会错位。无字幕和烧录字幕不重复绘制文本层。

`GET/POST/DELETE /api/stream/progress?version_id=...&kind=...` 复用网页进度；POST 的 `position`、`duration` 是源秒数。全家共享且最后保存为准。客户端区分“标为已看”的进度阈值和 Media3 的真正结束事件；下一集只由真正结束触发，并复用服务端 `/next` 的选集结果。

退出、Home、待机或 Activity 停止时，客户端停止播放、释放 Media3/媒体会话、取消过期读取、保存最终进度并释放本机 HLS sid。更新画质/切片源以请求代际判定晚响应；不能在用户退出后继续起播。进度写入失败会记录，但不阻止本机停止声音。

## 回归与验收证据

- 服务端：`test_android_tv_playback.py`、`test_stream_multiclient.py`、`test_stream_cache_seek.py`、`test_stream_sessions.py`、既有播放/远程源/转码参数回归。
- 多设备用例覆盖并发原子创建、独立 sid、最后引用退出、不同输出隔离、满额、完成后释放名额、预缓存持有者、源替换、kind、TTL、失败传播、停服竞态，以及合成媒体的真实 FFmpeg 产物。
- Android 单元测试覆盖服务器地址/认证边界、错误与取消、WebVTT 和源时间计算；Compose 焦点、真实中文键盘、Home/待机、解码和多电视实播须另外验收。
- 构建与模拟器通过不能代替红米真机，也不能证明所有 4K/HDR/Dolby 格式可用。真实片源验收不应触发媒体扫描、整理或删除。

操作与发布说明见 [工程 README](../README.md)，里程碑见 [开发计划](../../docs/roadmap/android-tv.md)。
