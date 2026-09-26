# 播放核心设计

[开发者文档](README.md)

## 从能力到播放计划

`app/media.py` 用 ffprobe 得到容器、编码、音字幕轨、HDR/DV 等，结果在 `media_info` 按 `(kind,item_id)` 缓存，探测版本落后时在播放前重新探测。前端 `caps.js` 检测浏览器格式能力，`POST /api/stream/{id}/decide` 上传 caps；不带 caps 的 GET 兼容口使用保守默认。`app/caps.py` 规范/哈希能力，`app/playback/plan.py` 决定：

| 方式 | 视频 | 音频 | 典型原因 |
|---|---|---|---|
| `direct` | 原文件直发 | 原文件默认轨 | 容器、编码及轨道均可浏览器直放 |
| `remux` | copy | copy | 内容可解，但容器/选轨需改为 HLS |
| `audio_transcode` | copy | 转 AAC | 视频可解，音频不在安全 copy 集合 |
| `video_transcode` | 重编 | 按需 copy/重编 | 视频编码、尺寸或 HDR 路径不适合直通 |

画质 `auto/source/1080p/720p` 会影响输出尺寸和会话复用；“原画”只取消自动封顶，不保证不会重编。hls.js 音频 copy 默认仅 AAC/MP3；Safari 原生 HLS 可支持更多 Dolby 格式。HDR10/HLG、带 HDR10 基底的 DV P8.1 在客户端可解 PQ 时可直通；DV P5 无兼容基底阻止直通。硬件后端可 tone map，软件退化路径会给原因提示。

## HLS 与 FFmpeg

`playback/cmd.py` 组命令，`transcode.py` 检测 VAAPI → QSV → NVENC → 软件，首轮硬件不出片时软件重试。默认 fMP4 HLS，每片目标 4 秒，`HLS_SEGMENT_TYPE=ts` 回退旧 TS。fMP4 单 FFmpeg 进程产生视频和最多 8 条音轨 rendition；服务端写 `master.m3u8`，ffmpeg 继续增长的 `out_*.m3u8` 应按内容快照返回，不能直接用按旧长度计算 Content-Length 的 `FileResponse`。FFmpeg 工作目录必须是 session 目录，因为分片输出文件名是相对路径。

音轨切换在 hls.js `MANIFEST_PARSED` 和 `AUDIO_TRACKS_UPDATED` 后应用，前者触发时列表可能尚空。源轨 default 与用户 UI 选择分离，非烧录字幕切换不应让音视频重新编码。会话复用以实际 plan 标记和质量目录键判断；烧录另有键防冲突。copy 首屏等一个视频分片，视频重编/烧录等两个，不能把音频片计入视频可播放判断。

## 会话时间轴与清理

`POST /sessions` 返回源时间轴起点 `media_start` 和会话内起播位置 `initial_time`。copy 会话起点可落在目标前关键帧；客户端进度和字幕必须把片内时间换回原片时间。先在缓冲或 seekable 范围复用；范围外重开会话；完整的 start=0 成品可被续播命中。

会话在服务端有心跳、未认领 TTL 和总缓存 TTL；前端用 `disposed`、`reloadGen`、AbortController 守护异步请求，关闭窗口前显式 pause、断开 src 并 load，迟到会话立即 DELETE。退出 lifespan 调 `shutdown_sessions` 清转码子进程。修改任何 await 路径后要审查晚到响应和双 HLS 实例风险。

## 字幕、预览与预缓存

`useSubtitles.js` 自绘文本 VTT，JASSUB 渲染 ASS/SSA，libpgs 渲染 PGS；VobSub 或必要降级烧录。字幕源时间相对 HLS 会话应叠加 `media_start + subDelay`。外挂轨从 `scanner.classify` 归属，临时本地字幕仅浏览器持有，关闭失效。

六档倍速由浏览器 `playbackRate` 实现，保持音调；换元素/会话需恢复。`usePlaybackPreviews.js` 与 `stream/previews.py` 管理逐页拼图，GET 只查状态，POST 才生成；缓存按源标识、大小、mtime 和规则失效，与 24 小时转码缓存分开。远程适用版本通过 prewarm 预缓存 start=0 的 HLS 成品，质量计划一致才复用。字幕、预览、seek 的所有 UI 时间都以原片时间表示。
