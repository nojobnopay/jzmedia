# Android TV 客户端协议 1

本页供修改电视客户端或服务端接口时查阅，记录必须保持的兼容约定。构建和连接步骤见 [README](../README.md)，验证步骤见[测试指南](testing.md)。核对日期：2026-10-04。

APK 与服务器通过[统一发布](../../docs/developer/releasing.md)从同一提交构建，版本以根 [version.properties](../../version.properties) 为准。设备仍可分别安装升级，因此通过协议握手确认兼容性；首次正式签名发布的最低服务端发行版本尚待确定，见[待验收事项](../../docs/roadmap/android-tv.md)。媒体探测字段基线为 schema 30 / probe v4，旧媒体探测缓存可自动重探。

## 连接、认证与能力

### 握手与令牌

`GET /api/stream/client-info` 为只读握手，返回：

```json
{
  "protocol_version": 1,
  "features": ["android_tv", "independent_sessions", "tv_search", "tv_actor_search"],
  "auth_required": false
}
```

`POST /api/stream/client-check` 返回相同内容，不修改配置或媒体。它走已有写操作认证中间件，用于验证当前连接的令牌：

- 未配置令牌可直接通过。
- 已配置时使用 `X-Api-Token` 或 `Authorization: Bearer`，错误令牌返回 401。
- GET 握手成功不等于写操作令牌通过。响应绝不包含服务器令牌。

APK 当前使用 `X-Api-Token`，仅发往保存的服务器 origin 和路径范围，拒绝携带凭据跨 origin 重定向。API 连接使用可取消请求；连接改变、退出和过期响应不得重新进入旧页面或启动旧播放器。

### 可选搜索能力

`tv_search`（片名）与 `tv_actor_search`（演员）是独立的可选能力，不提高播放协议版本。缺少接口时仅对应搜索功能提示升级，浏览和播放继续可用。参数、演员身份和资料范围见[搜索接口](search-design.md#服务端与兼容)。

### 播放能力声明

以下 POST 请求显式传 `client: "android_tv"` 和 `caps`：

- `/api/stream/versions`：根据候选版本及设备能力排序。
- `/api/stream/{id}/decide`：选择 `direct|remux|audio_transcode|video_transcode` 或 `blocked`。
- `/api/stream/{id}/sessions`：创建 HLS 客户端会话。
- `/api/stream/prewarm`：可选预缓存接口，使用同一能力和产物规则。

旧客户端不传 `client` 时仍按 `web` 处理。不能用浏览器 `mse` 或 `native_hls` 冒充 Android 原生播放器能力。

| `caps` 字段 | 语义 |
| --- | --- |
| `video` / `audio` | 设备实际解码器能力，按已有编码名称记录严格布尔值 |
| `probes` | 服务端 `vcaps` 与各音轨 `caps` 的具体候选格式检测结果 |
| `containers` | 原生媒体解析器支持的容器，如 `mp4`、`mkv`、`webm` |
| `hls` | 原生播放器能否处理 HLS |
| `hls_audio` | HLS 封装允许复制的音频编码；必须同时有设备解码能力 |
| `audio_track_selection` | 原文件播放中能否切换音轨 |
| `hdr_formats` | 明确支持的 HDR 格式列表，不能由品牌、`native_hls` 或普通 HEVC 能力推断 |
| `subtitles.webvtt` | 首版兼容文本字幕能力 |

APK 通过 Media3/MediaCodec 查询解码器，再按片源的宽、高、帧率、profile/level、声道数和采样率补充检测。通用 H.264/AAC 能力代表可接受的服务器输出；高规格源格式不支持时，不能连带关闭较低规格的输出能力。

当前 APK 的 HLS 音频复制保守仅声明 AAC/MP3；源文件能解 DTS 或 Dolby 不意味着它们可以安全复制进 HLS。HDR10/HLG 结合解码器与显示能力声明；未验证的 Dolby Vision 不主动放行。真实电视仍可能与检测结果不同，播放器提供重试和兼容播放入口。

fMP4 master 的 `CODECS` 必须包含整个音频组的实际输出编码并集，不能只写默认音轨；Media3 会据此解析其他 rendition。无法复制的音轨以输出 AAC 描述。

## 播放请求与时间轴

### 媒体类型与资源地址

播放、进度等接口均需带真实 `kind: movie|episode|extra`；三种 ID 空间相互独立，不能只根据整数 ID 判断归属。源文件/海报/字幕地址使用服务器返回值并按当前连接解析，不能将 NAS 路径当成电视本地路径。

海报字段 `poster_path`、`show_poster`、`cover`、`season_poster` 可能带 `posters/` 前缀或只有相对路径。统一去掉开头 `/` 和已有的 `posters/`，再拼接 `/posters/`；`tv/123.jpg` 与 `posters/tv/123.jpg` 均请求 `/posters/tv/123.jpg`。保留子目录、旧文件名和连接的部署前缀，不推断新文件名；空路径显示占位。

### 创建会话

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

请求字段：

- `caps` 必须来自当前设备检测，示例空值仅用于说明字段，不代表具备可播放能力。
- `start` 是源视频秒数，必须有限且非负。
- `quality` 为 `auto|source|1080p|720p`。
- `audio` / `sub` 是媒体接口返回列表中的索引。

`ff_index` 是 FFmpeg 流序号，不能与 Media3 的 `Format.id` 直接比较（例如 MKV 使用容器 TrackNumber）。APK 对直连按音轨顺序与数量对应；不确定或无法解码时改走 HLS。fMP4 优先按服务端 `NAME=audioN` 选 rendition，TS 单轨模式切换时新建会话。

`decide` 返回 `direct_url` 时，原文件由 Media3 播放且源时间从 0 开始。HLS 返回 `session_id`、`playlist_url`、`method`、`plan`、`subtitle_mode`、`media_start`、`initial_time`、`complete` 和 `caps_hash`。

### 起播与源时间

| 时间字段 | 单位与用途 |
| --- | --- |
| `start` | 源视频秒数，请求的续播/跳转位置 |
| `media_start` | HLS 片内 0 对应的源秒数；copy 可落在更早的关键帧 |
| `initial_time` | HLS 片内起播秒数，传给 Media3 时乘以 1000 |
| 源播放位置 | `media_start + player.currentPosition / 1000`，用于进度、字幕和界面 |

例如从 450 秒续播：复用完整缓存时为 `media_start=0, initial_time=450`；产物从 445 秒开始时可为 `media_start=445, initial_time=5`。

### HLS 跳转与定位

完整缓存可直接在片内定位。未覆盖目标的进行中 HLS 跳转须创建新会话；不能把目标源时间直接写入局部 HLS 进度。播放器关闭旧的本机会话，再创建新请求，不影响其他设备。

进行中的 HLS 清单持续增长，但内容仍是点播。Media3 的占位时间轴可能把初始 0 替换为直播默认位置；APK 先暂停准备，真实时间轴到达后，仅对当前请求执行一次 `seekTo(initial_time × 1000)` 再按用户意图播放。后续清单更新不能重复归零。回归必须包含真实服务端尚未转完的清单，静态 VOD 无法覆盖此问题。

## 会话、预缓存与资源限制

### 独立会话与共享任务

每次成功的 `POST sessions` 都返回新的客户端 `session_id`。服务器内部按 `(kind, id, 完整产物键)` 原子共享 FFmpeg 任务：产物键包括源路径/size/mtime、封装、实际音频策略、视频输出、烧录字幕及精确起点；输出目录含该键 SHA-256。改变只影响客户端显示的文本字幕或同一 fMP4 音轨选择，不应重新生成相同产物。

### 保活与释放

`POST /api/stream/sessions/{sid}/ping` 保活；读取 playlist / 分片也认领并刷新会话。APK 播放期间定期保活。

`DELETE /api/stream/sessions/{sid}` 仅释放该 sid；只有最后持有者离开才终止仍在运行的 FFmpeg。预缓存是独立持有者，关闭电视不会取消仍在进行的预缓存任务。

### 并发与清理

`MAX_TRANSCODES` 限制完整进程生命周期，默认 2；同产物加入已有任务不再占一个名额。创建新产物满额返回 429，不抢占别的设备。预缓存任务在异步作业状态中报告资源不足。客户端显示服务器繁忙并允许重试/返回，不无限重试或自动关闭其他会话。

尚未取流的会话由服务器短期回收兜底；已有 sid 的过期响应由客户端主动 DELETE。启动中、活动任务及被使用的缓存目录不被 TTL/手动缓存清理删除，停服回收 FFmpeg。

## 字幕、进度与生命周期

### 字幕时间轴

媒体接口合并内嵌与外挂字幕。文本及 ASS/SSA 使用 `/api/stream/{id}/sub/{idx}.vtt?kind=...`，APK 解析 WebVTT 并在源时间上显示，支持字号和延迟；ASS 的字体、特效和复杂排版不保真。图片字幕采用 `subtitle_mode=burn`，切换需要重新准备播放。

内嵌图片字幕的 `sub_ff_index` 是源文件绝对流号，服务端 overlay 使用 `[0:<ff_index>]`，不能当成字幕类型内序号 `[0:s:<ff_index>]`。外挂图片字幕仍使用第二输入的 `[1:s:0]`。

当前内部字幕计算为 `cueSourceTime = sourcePosition + subtitleDelay`；界面显示的“延迟”符号与内部偏移相反。延迟不能再次叠加到 `media_start` 上，否则续播后会错位。无字幕和烧录字幕不重复绘制文本层。

### 进度与完成

`GET/POST/DELETE /api/stream/progress?version_id=...&kind=...` 复用网页进度；POST 的 `position`、`duration` 是源秒数。全家共享且最后保存为准。

客户端区分“标为已看”的进度阈值和 Media3 的真正结束事件；下一集只由真正结束触发，并复用服务端 `/next` 的选集结果。

完成阈值为观看达到 95%，或观看达到 80% 且剩余不超过 300 秒；短片不能仅因总时长不足 300 秒而算看完。服务端 `playback_completion.py`、网页 `progress.js` 与 APK `PlaybackModels.kt` 必须保持一致。旧服务器仍可连接，但旧的继续观看/下一集判定需升级服务端才会修复，无需重扫。

### 连续播放与用户意图

APK 将用户的播放 / 暂停意图与播放器缓冲状态分开，跳转、换画质及初始 HLS 定位后保留意图。

连续播放只在本次观看会话传递倍速、音轨与字幕偏好；按语言、名称及轨道属性匹配下一集，不复用文件内序号，无匹配时提示回退。已关闭字幕保持关闭。打开设置暂缓结束倒计时，单次取消不修改长期自动连播开关。

`/api/tv/episodes/{id}/next` 可额外返回 `exists` 与 `missing`，仅反映服务端已知状态，不新增远程探盘；旧客户端可忽略。APK 遇到明确离线的下一集不自动开流，提示返回选集。字段缺失时兼容旧响应，由实际播放请求处理失败。

### 请求超时与退出清理

| 请求类型 | 读取超时 | 总超时 |
| --- | --- | --- |
| 普通 API、图片 | 15 秒 | 20 秒 |
| 媒体探测、decide/sessions、字幕提取 | 150 秒 | 180 秒 |

所有请求继续遵循页面或播放请求取消，不将普通浏览超时套用到首次转码准备。

退出、Home、待机或 Activity 停止时，客户端停止播放、释放 Media3/媒体会话、取消过期读取、保存最终进度并释放本机 HLS sid。更新画质/切片源以请求代际判定晚响应；不能在用户退出后继续起播。进度写入失败会记录，但不阻止本机停止声音。

## 修改时核对的位置

Android 文件位于 `app/src/main/java/org/jzmedia/tv/` 下；Python 路径相对仓库根目录。

### 握手、地址和认证

- 源码：`JzApi.kt`、`app/routers/stream/media.py`。
- 回归：`JzApiTest`、`test_android_tv_playback.py`。

### 原生能力与播放方案

- 源码：`NativeCapabilities.kt`、`app/caps.py`、`app/playback/`。
- 回归：`test_android_tv_playback.py`。

### 时间轴、轨道与退出

- 源码：`PlaybackController.kt`、`PlaybackModels.kt`。
- 回归：Android playback 单测、`test_stream_cache_seek.py`。

### 多端会话与共享任务

- 源码：`app/routers/stream/`。
- 回归：`test_stream_multiclient.py`、`test_stream_sessions.py`。

执行方法与真实后端验证边界见[测试指南](testing.md)。
