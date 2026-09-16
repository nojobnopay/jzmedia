# NAS Web 视频播放器开发方案

## 1. 目标

实现一个运行在 NAS 上的 Web 视频播放器，支持：

- 浏览器播放 NAS 本地视频
- 播放 / 暂停 / 拖动进度
- 多音轨切换
- 字幕切换
- 尽可能支持 MKV、MP4、H.264、HEVC、DTS、ASS、PGS 等常见格式
- 在 NAS 性能较弱的前提下，尽量避免视频转码
- 必要时使用 FFmpeg + Intel QSV / VAAPI 硬件转码

核心原则：

> 浏览器能直接播放就直接播放；只需要换封装就只换封装；只需要转音频就不要转视频；只有视频编码确实不兼容时才进行视频转码。

---

## 2. 总体架构

```text
Browser
│
├─ Vue 3
├─ 播放器 UI（可选 Vidstack）
├─ hls.js
├─ HTMLVideoElement
└─ JASSUB（ASS/SSA 字幕）
        │
        ▼
FastAPI
│
├─ MediaInfo
│   └─ ffprobe + SQLite 缓存
│
├─ ClientCapabilities
│   └─ 浏览器实际支持的编码能力
│
├─ PlaybackPlanner
│   └─ 计算播放方案
│
├─ StreamSession
│   └─ 管理 FFmpeg / HLS 会话
│
└─ FFmpeg
    ├─ remux
    ├─ audio transcode
    └─ video transcode(QSV/VAAPI)
```

---

## 3. 媒体扫描

使用 `ffprobe` 获取并缓存：

```text
container
duration
resolution

video:
  codec
  profile
  bit_depth
  hdr / dolby_vision

audio[]:
  codec
  channels
  language
  title

subtitle[]:
  codec
  language
  title
```

结果保存到 SQLite，避免播放时重复扫描大型视频文件。

建议数据模型：

```text
MediaInfo
 ├─ VideoTrack[]
 ├─ AudioTrack[]
 └─ SubtitleTrack[]
```

---

## 4. 客户端能力检测

不要简单认为：

```text
HEVC = 浏览器不支持
```

前端启动时检测浏览器实际能力：

```javascript
video.canPlayType(...)
MediaSource.isTypeSupported(...)
navigator.mediaCapabilities.decodingInfo(...)
```

生成：

```text
ClientCapabilities
```

例如：

```json
{
  "h264": true,
  "hevc": true,
  "aac": true,
  "ac3": false,
  "eac3": false,
  "mse": true
}
```

服务端根据：

```text
MediaInfo
+
ClientCapabilities
+
用户选择的音轨/字幕/画质
```

决定最终播放方式。

---

## 5. 播放决策

建议最终只保留 4 种 PlaybackPlan。

### DIRECT

浏览器可以直接播放原文件：

```text
video: copy
audio: copy
container: original
```

通过 HTTP Range 返回原文件。

NAS 开销最低。

---

### REMUX

编码兼容，但容器不兼容。

例如：

```text
MKV
H264
AAC
```

处理：

```text
MKV
↓
HLS + fMP4
```

FFmpeg：

```text
-c:v copy
-c:a copy
```

不重新编码。

---

### AUDIO_TRANSCODE

视频浏览器支持，但音频不支持。

例如：

```text
HEVC + DTS
```

客户端支持 HEVC：

```text
video:
HEVC → copy

audio:
DTS → AAC
```

不要因为 DTS 就重新编码整个视频。

---

### VIDEO_TRANSCODE

只有浏览器确实无法播放视频编码时才使用。

例如：

```text
HEVC
↓
H264
```

NAS 上优先：

```text
Intel QSV
或
VAAPI
```

不要默认使用 CPU `libx264`。

---

## 6. HLS 输出格式

建议优先：

```text
HLS + fragmented MP4
```

而不是只使用：

```text
HLS + MPEG-TS
```

推荐输出：

```text
master.m3u8

video/
 ├─ init.mp4
 ├─ seg0001.m4s
 └─ seg0002.m4s

audio_zh/
 ├─ init.mp4
 └─ ...

audio_en/
 ├─ init.mp4
 └─ ...

subtitle_zh.vtt
```

分片建议：

```text
3~4 秒
```

第一片生成后即可开始播放，不必等待 3 个分片。

---

## 7. 多音轨

视频和音频应独立处理。

例如：

```text
Video:
HEVC → copy

Audio #1:
DTS 中文 → AAC

Audio #2:
DTS 英文 → AAC
```

使用 HLS Alternate Audio。

切换：

```text
中文
↕
英文
```

时只切换 Audio Rendition。

不要重新启动视频转码。

---

## 8. 字幕策略

> 已落地（P3/P3.5）：文本/ASS/PGS 全部**客户端渲染**，切换字幕不重开会话、不触发视频转码；
> 仅 VobSub（及客户端解码失败的降级）走烧录。字幕延迟（±10s）对 ASS/PGS 生效。

### SRT / WebVTT

```text
SRT → WebVTT（服务端抽取/转换，缓存）
```

浏览器 `<track>` 直接显示。

---

### ASS / SSA

不要默认：

```text
ASS → WebVTT
```

否则会丢失字体、位置、动画、颜色等样式。

推荐：

```text
ASS
↓
JASSUB
↓
浏览器 WASM/libass 渲染
```

NAS 基本无额外负担。内嵌轨 `-c:s ass` 抽取缓存；**外挂 `.ass/.ssa` 直接服务原文件**。
字体来源：MKV 附件（首次请求懒抽取）+ `data/fonts/` 内置投放。

---

### PGS

```text
PGS
↓
ffmpeg -c:s copy 抽 .sup（纯流拷贝，缓存；外挂 .sup 直服）
↓
libpgs（浏览器端解码渲染，客户端画布叠加）
```

与 ASS 同理：切换/关闭字幕即时，不重编码、不重开会话，NAS 视频 CPU 归零。
`timeOffset` 支持字幕延迟。

---

### VobSub（.idx/.sub）

ffmpeg 无 vobsub muxer、解码器独立，暂不客户端渲染：

```text
字幕 burn-in
↓
FFmpeg（第二输入 overlay）
↓
重新编码 video
```

选择 / 关闭这类字幕时重新建立播放会话（这是唯一需要重编的字幕路径）。
客户端 PGS 解码失败时也自动降级到 burn-in（`force_burn`）。

---

## 9. Seek / 拖动进度

### Direct Play

使用 HTTP Range + `<video>` 自身 seek。

### HLS Remux / Transcode

用户拖到：

```text
3600s
```

关闭旧 Session，新建：

```text
baseTime = 3600
```

FFmpeg：

```bash
ffmpeg -ss 3600 -i input ...
```

前端真实 video 时间可能是：

```text
5s
```

UI 显示：

```text
baseTime + currentTime
= 3605s
```

即：

```text
01:00:05
```

不建议固定使用：

```text
target - 15 秒
```

再精确裁剪的逻辑，除非实际测试证明某些片源必须这样处理。

---

## 10. Stream Session

播放 HLS 时建立一个 StreamSession：

```text
session_id
media_id
start_time
audio_track
subtitle_track
quality
ffmpeg_process
last_heartbeat
```

流程：

```text
POST /stream/session
      ↓
创建 FFmpeg
      ↓
生成 init.mp4
      ↓
生成第一个 segment
      ↓
返回 master.m3u8
```

播放器定期发送 heartbeat。

停止播放或超时：

```text
kill ffmpeg
清理临时分片
```

不同用户 / 不同播放参数可以建立不同 Session。

---

## 11. DS425+ 优化

DS425+ 的 CPU 性能有限，所以核心原则是：

```text
尽量避免 Video Transcode
```

优先级：

```text
DIRECT
  ↓
REMUX
  ↓
AUDIO_TRANSCODE
  ↓
VIDEO_TRANSCODE
```

部署到 NAS 后检测：

```bash
/dev/dri/renderD128

ffmpeg -hwaccels

ffmpeg -encoders | grep -E 'qsv|vaapi'
```

转码 Backend 建议抽象：

```text
TranscoderBackend

├─ SoftwareBackend
│  └─ libx264
│
├─ QsvBackend
│  └─ h264_qsv
│
└─ VaapiBackend
   └─ h264_vaapi
```

WSL 开发阶段可以先使用 SoftwareBackend。

NAS 环境优先 QSV / VAAPI。

---

## 12. HDR / Dolby Vision

不要简单写成：

```text
Dolby Vision → 强制 SDR
```

应区分：

```text
HDR10
Dolby Vision + HDR10 compatible
Dolby Vision only
```

DS425+ 不应把：

```text
4K HDR / Dolby Vision 实时 Tone Mapping
```

作为主要能力。

对于复杂片源，可以提供：

```text
播放原文件
复制直链
外部播放器打开
```

例如 VLC / Kodi。

---

## 13. 预转码

保留“预转码”功能。

例如夜间生成：

```text
Original
    4K HEVC

Optimized
    1080p H264
    720p H264
```

后续播放直接读取静态 HLS / MP4，不再实时转码。

视频、音频、字幕尽量独立保存，避免组合爆炸。

---

## 14. 推荐技术栈

后端：

```text
FastAPI
SQLite
ffprobe
ffmpeg
```

前端：

```text
Vue 3
hls.js
```

可选播放器 UI：

```text
Vidstack
```

ASS 字幕：

```text
JASSUB
```

传输：

```text
HTTP Range
HLS
fMP4
WebVTT
```

NAS 转码：

```text
Intel QSV
VAAPI
```

---

## 15. 第一阶段开发重点

优先实现以下链路：

```text
ffprobe
    ↓
MediaInfo
    ↓
ClientCapabilities
    ↓
PlaybackPlanner
    ↓
PlaybackPlan
    ↓
FFmpegCommand
    ↓
StreamSession
```

核心数据结构建议：

```text
MediaInfo

ClientCapabilities

PlaybackRequest

PlaybackPlan

StreamSession
```

其中最重要的是 `PlaybackPlanner`。

输入：

```text
MediaInfo
ClientCapabilities
用户选择
```

输出：

```text
DIRECT
REMUX
AUDIO_TRANSCODE
VIDEO_TRANSCODE
```

以及：

```text
videoCodec
audioCodec
subtitleMode
container
hardwareAcceleration
```

字幕模式：

```text
NONE
WEBVTT
ASS_CLIENT
BURN_IN
```

---

## 16. 最终设计原则

整个项目最重要的一条原则：

> 不要思考“FFmpeg 怎么把所有视频转成浏览器能播的格式”，而应该优先判断“哪些数据根本不需要转”。

典型结果：

```text
H264 MKV
→ REMUX
→ 几乎零 CPU

HEVC MKV + AAC
→ 客户端支持 HEVC
→ REMUX / video copy

HEVC + DTS
→ video copy
→ DTS → AAC

HEVC + PGS
→ 只有开启 PGS 时进行 video transcode

客户端不支持 HEVC
→ QSV / VAAPI
→ HEVC → H264
```

这也是整个系统在 DS425+ 这类低功耗 NAS 上能够正常运行的关键。

---

## 17. 落地实施方案

本文目标与原则的分阶段落地方案（现状差距、文件级改动、验收与回滚）见
`docs/plans/PLAYER_IMPL_PHASES.md`：P1 能力检测 + 四档决策器 → P2 fMP4 + 多音轨 rendition →
P3 ASS/JASSUB + 字体 → P4 TranscoderBackend + HDR/DV → P5 预转码完整化与收尾。
