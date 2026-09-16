# NAS Web 播放器分阶段实施方案

> 配套文档：`docs/plans/NAS_Web_Video_Player_Development_Plan.md`（目标与原则，下称“目标文档”）
> 本文是当前实现的差距分析与落地方案，对应目标文档 §15 的链路，并覆盖 §3–§13。
> 状态：已确认，按 P1 → P5 执行。代码版本统一从 `v0.6.0` 升至 `v0.7.0`。

---

## 0. 现状 vs 目标差距

当前实现（截至 `v0.6.0`）：`app/media.py`（ffprobe 探测 + 三档 decision + TS HLS 命令）+
`app/routers/stream.py`（渐进式会话/心跳/TTL/双路并发/秒开复用/seek 重开+baseTime/PGS 烧录/VTT 抽取/prewarm）+
`frontend/src/components/PlayerModal.vue`（自绘控件/watchdog 自救/冻结帧/全屏显隐）。

现有资产保留：会话与心跳模型、TTL/清道夫、双路并发、完工静态 VOD 复用、seek 对齐策略、
watchdog 恢复、自绘控件层（不迁 Vidstack）。

| 目标文档 | 当前实现 | 差距 |
|---|---|---|
| §4 ClientCapabilities 前端实测能力 | 无；服务端硬编码 H264+AAC+MP4 profile | **结构性缺失**，HEVC/AC3 一律转码 |
| §5 四档 PlaybackPlan | 三档 `direct/remux/transcode`，音频单转混在 transcode | 缺 `AUDIO_TRANSCODE` 档；决策不看客户端 |
| §6 fMP4 + 3~4s + `audio_xx/` 布局 | MPEG-TS 6s、单 variant、`master.m3u8` 实为 media playlist | 输出结构需重构 |
| §7 音轨独立 rendition | 切音轨 = 关旧会话开新（视频一起重转） | 需 `-var_stream_map` + `EXT-X-MEDIA` |
| §8 ASS → JASSUB | ASS 转 VTT（丢样式） | 需 `.ass` 抽取 + JASSUB + 字体 |
| §11 TranscoderBackend + 环境检测 | `HW_ACCEL` env 字符串切换，QSV 路径不完整，无检测 | 后端抽象 + 冒烟测试 |
| §12 HDR/DV 分类 + 外部播放器兜底 | 仅 `dv_profile`，一律强制重编，无 tonemap/兜底入口 | HDR/DV 判定矩阵 |
| §3 丰富 MediaInfo | 缺 `bit_depth/profile/level/color/HDR/DV兼容/轨道 default` | 探测字段扩充 + 缓存失效 |
| §13 预转码独立保存 | prewarm 单音轨、整片转完 | 复用新管线，补多音轨 rendition |

## 0.1 决策记录（已确认）

1. **排期**：按目标文档分阶段全做。
2. **HLS 输出**：fMP4 + `var_stream_map` 单进程（video + 全部音轨 rendition），保留 `HLS_SEGMENT_TYPE=ts` 回滚开关。
3. **ASS 字幕**：抽取 MKV 内嵌字体 + 支持运行时可投放内置字体 + 保留“兼容字幕(VTT)”开关，采用 JASSUB 客户端渲染。
4. **硬件转码**：自动检测 + 冒烟测试，VAAPI 优先、QSV 兜底、软件兜底；HDR/DV 先做“客户端支持则直通，否则外部播放器/降级”。
5. **播放器 UI**：保留自绘控件 + JASSUB，不迁 Vidstack。

## 0.2 进度

| 阶段 | 状态 | 落地摘要 |
|---|---|---|
| P1 能力检测 + 四档决策器 | ✅ 已落地 | 新增 `app/caps.py`、`app/playback.py`；`media.probe` 扩字段 + `probe_ver` 自愈重探；POST `decide/versions/sessions` 带 caps；前端 `caps.js` + 详情/播放器接入；HLS 输出仍 TS（P2 换 fMP4） |
| P2 fMP4 + 多音轨 rendition | ✅ 已落地并实测 | fMP4 扁平产物 + `-var_stream_map` 单进程多音轨；自产 master（含 CODECS）；`GET /sessions/{sid}/{name}`；前端 `hls.audioTrack` 免重开切轨；`HLS_SEGMENT_TYPE=ts` 回滚 |
| P3 ASS/JASSUB + 字体 | ✅ 已落地并实测 | `.ass` 抽取 + 附件字体懒 dump + `data/fonts` 内置；前端 JASSUB 懒加载（RPC worker/wasm）、「兼容」VTT 降级、烧录不受影响 |
| P3.5 图片字幕客户端化 + 外挂字幕 | ✅ 已落地并实测 | PGS→libpgs 客户端（切换不重开、零转码）+ 解码失败自动降级烧录；外挂 `.srt/.ass/.ssa/.sup`（含自动选中文）与 `.idx/.sub` 烧录；字幕延迟（ASS/PGS）；`scripts/find_subs.py` |
| P4 TranscoderBackend + HDR/DV | ⬜ 待开始 | |
| P5 预转码完整化 + 收尾 | ⬜ 待开始 | |

### P1 落地记录

- 模块：`app/playback.py`（四档 `plan()`/选版 `score()`/`build_cmd()`，含 `hw_backend()`
  P1 占位判定）、`app/caps.py`（`normalize_caps/default_caps/caps_hash/probe_state`）。
- 探测：`media.probe` 新增 `video_profile/video_level/bit_depth/pix_fmt/color_transfer/
  color_primaries/hdr/dv_bl_compat/hdr10plus/attachments` 与音频/字幕 `default/forced`；
  `media.decorate` 生成客户端实测候选码串（`vcaps` + 每音轨 `caps`，不落库）。
  缓存自愈：`media_info.probe_ver` 低于 `media.PROBE_VERSION` 播放时自动重探。
- 接口：`POST /api/stream/{id}/decide`、`POST /api/stream/versions`、`POST
  /api/stream/{id}/sessions` 均接受 `caps`；GET 口保留（走 `default_caps()` 保守默认，
  等价改造前 profile）。decide 返回 `subtitle_mode` 与 `caps_hash`（观测用）。
- 会话复用键只放 plan 输出（copy/height/音轨/字幕/start）：不同 caps 落到同一 plan
  即可安全复用静态 VOD；caps 不是复用键的一部分。
- 前端：`frontend/src/caps.js` 通用矩阵 + `probeStrings` 逐片候选码串实测（decide 前
  复判一次，每版本一次）；`quality=auto` 由服务端定封顶（HW 1080p / 软件 720p），
  移除播放器内 `auto_downscale_720p` 硬编码；`audio_transcode` 计入“浏览器友好”。
- 实测（sample_media）：HEVC 4K MKV + AAC → 支持 HEVC 客户端 `remux`、否则
  `video_transcode` 720p；H264+EAC3 MKV → `audio_transcode`（视频 copy）；DV P8 →
  P1 保守 `video_transcode`（P4 细化直通矩阵）；H264 4K MP4 → `direct`。
- 实测修复（上线后回归）：
  - **EAC3/AC3 copy 无声**：浏览器 `isTypeSupported('audio/mp4;codecs="ec-3"')` 报 true，
    但 TS→hls.js 管线 copy EAC3 无声（古董局中局复现）。引入 copy 安全集：
    `hls.js = {aac, mp3}`，Safari 原生 HLS 才放行 `{ac3, eac3}`；其余一律
    `audio_transcode` 转 AAC（视频仍 copy）。P2 fMP4 后需重测决定是否放宽。
  - **seek 后从更后位置起播**（选 13:20 实际从 30:50）：增长型 live 列表默认从
    “直播边缘”起（hls.js `startPosition=-1` + `liveSyncPosition`），copy 档 ffmpeg
    抢跑越远偏得越多。新会话统一 `startPosition: 0`（片内 0 = 会话起点，绝对位置由
    `startOffset` 记录）；原生 HLS 在 loadedmetadata 后回 0；自救仍默认回直播边缘。
  - 顺带修：`resumePos` 用后未清导致切档跳回上次 seek 点；`从头开始` 被“保持当前位置”
    捕获而失效（显式 `startFromZero` 跳过）。
  - **画质语义修正**：P1 把自动降档挪到服务端后，前端仍显示“原画”而实际 720p。现改为
    下拉 `自动（默认）/ 原画(source，不封顶，重编时提示 source_transcode 耗 CPU）/
    1080p / 720p`，方法行旁显示“实际输出 720p（自动封顶）/ 原分辨率×直通/重编”；
    `original` 字符串保留为 auto 兼容旧前端。
  - **静态复用键归一**：会话目录键改由产物决定（`_quality_key`：copy / h720 / h1080 /
    src），复用键只放产物字段（`_plan_marker`：去掉档位字符串，非烧录字幕不参与，
    烧录保留）。效果：预转码 720p 与在线播 `自动→720p` 命中同一成品；换文本/ASS 字幕
    不再作废整片成品。预转码接口默认 `auto`，详情页按钮加目标下拉
    （自动/1080p/720p/原画）。旧命名成品不命中新键，会重转一次（24h TTL 自清）。

---

## P1 能力检测 + 四档决策器

最高价值项：让支持 HEVC/AC3 的客户端拿到 remux/audio_transcode 零 CPU 路径。

### 后端

- **新增 `app/caps.py`**
  - `normalize_caps(raw) -> dict`：白名单校验，防止超大 payload。结构：
    `{video: {...}, audio: {...}, hdr: bool, mse: bool, native_hls: bool}`。
  - `caps_hash(caps) -> str`：短摘要，用于会话 `plan_key`，防跨浏览器复用错档。
- **新增 `app/playback.py`**（从 `app/media.py` 迁出并升级；`media.py` 只留 ffprobe/二进制解析）
  - `plan(media, caps, request) -> {method, reasons, plan, subtitle_mode}`
    - `method: direct | remux | audio_transcode | video_transcode | blocked`
    - `subtitle_mode: none | webvtt | ass_client | burn`（对齐目标文档 §15）
    - 判定顺序：烧录/降档/视频不兼容 → `video_transcode`；视频兼容 + 音频不兼容 →
      `audio_transcode`（`vcopy=True`）；容器不兼容 → `remux`；全兼容 → `direct`。
    - 补充规则：所选音轨非 default 且 direct 且浏览器无 `audioTracks` API → 强制 `remux`
      （否则浏览器只播默认轨）；ASS/SSA → `ass_client` 不强制转码；PGS/VobSub → `burn`
      归入 `video_transcode`。
  - `score(media, caps, quality)`（原 `score_for_client`，`app/media.py:423`）；
    `audio_transcode` 计入“浏览器友好”档。
  - `build_cmd()` 暂保持 TS 路径（P2 重写）。
  - reasons 词表补 `video_bit_depth_not_supported / hdr_not_supported / quality_forced`。
- **`app/media.py:134` probe 扩充**
  - video：`profile / level / bit_depth / pix_fmt / color_transfer / color_primaries`、
    `hdr`（pq|hlg）、`dv_bl_compat`（`dv_bl_signal_compatibility_id`）、`hdr10plus`。
  - audio/sub：`default` / `forced` disposition。
  - attachments 清单（P3 字体判定用）。
  - 服务端按片源生成候选码串（`hvc1/hev1`、`avc1.XXYYZZ`、`av01…`、`ec-3` 等），供前端精确实测。
- **`app/store.py` media_info 扩充**
  - 新增列：`probe_ver`、`video_profile`、`video_level`、`bit_depth`、`pix_fmt`、
    `color_transfer`、`hdr`、`dv_bl_compat`、`attachments_json`；
    用 `init_db` 自愈 ALTER（先例：`dv_profile`，`app/store.py:294`）。
  - `upsert_media_info` 同步写入；`probe_ver` 低于当前版本的行视为过期，播放时自动重探
    （等价于现有 `is_retryable_error` 自愈思路，避免手动全量 backfill）。
- **接口**
  - `POST /api/stream/decide`、`POST /api/stream/versions`（body 带 caps，改造
    `app/routers/stream.py:93,162`）；GET 保留为保守 profile（curl 调试用）。
  - 默认画质改 `auto`：direct/remux 原画；转码有 HW 用 1080p、否则 720p。
    替换前端 `auto_downscale_720p` 硬编码（`PlayerModal.vue:387`）。
  - 会话 `plan_key` 加入 `caps_hash`（`stream.py:396`）。

### 前端

- **新增 `frontend/src/caps.js`**（模块级缓存 Promise）
  - 同步基底：`MediaSource`、`navigator.mediaCapabilities`、
    `matchMedia('(dynamic-range: high)')`、`ManagedMediaSource`（原生 HLS）。
  - 编解码矩阵：`decodingInfo()` 优先（带 width/height/framerate），
    `MediaSource.isTypeSupported` 兜底；键：
    `h264/h264_hi10p/hevc/hevc10/av1/vp9/mpeg2/vc1` ×
    `aac/mp3/ac3/eac3/dts/truehd/flac/opus/vorbis/pcm` + `hdr`。
  - 逐片精确码串实测回填（避免“支持 H264 ≠ 支持 Hi10P”）。
  - `sessionStorage` 短 TTL 缓存（UA + dynamic-range 变化失效）。
- `Detail.vue:311` 改用 POST 并带 caps；`PlayerModal.vue:162` `isHls` 覆盖
  `audio_transcode`；`REASON_TEXT`（`PlayerModal.vue:183`）补新词。

### 验收

- HEVC+AC3：支持 HEVC 的 Chrome/Safari → `remux`；不支持 → `video_transcode`。
- HEVC+AAC → `remux`；H264+DTS → `audio_transcode`；H264+AAC+MKV → `remux`。
- `/api/stream/versions` 最优版打分随 caps 变化；旧 TS 播放链路无回归。

### 回滚

纯增量：不传 caps 时走保守 profile，行为等同现状。

---

## P2 fMP4 + 3~4s + 多音轨 rendition

唯一高风险阶段。TS → fMP4 live 增长列表 + 多 rendition，必须带开关与 golden 命令验证。

### 后端

- **`build_cmd` 重写（单进程 `-var_stream_map`）**
  - 全部音轨入流：`-map 0:v:0` + 每轨 `-map 0:a:{i}`，`-c:a:{i} copy|aac`；
    `-var_stream_map "v:0,agroup:aud,name:video a:0,agroup:aud,name:audio0,language:zh,default:yes a:1,…"`。
  - 输出：
    `-f hls -hls_time 4 -hls_segment_type fmp4
     -hls_fmp4_init_filename "%v/init.mp4" -hls_segment_filename "%v/seg%05d.m4s"
     -master_pl_name master.m3u8`，预建 `video/ audio0/ …` 目录。
  - `-hls_flags temp_file+independent_segments`（`temp_file` 顺带修掉“半写分片被拉走”的隐性问题）。
  - 转码路径加 `-force_key_frames "expr:gte(t,n_forced*4)"` 保证分段对齐；
    copy HEVC 加 `-tag:v hvc1`（Apple/MSE 兼容）；burn-in 走 `filter_complex → [vout]` 再进 var_stream_map。
  - 旧 TS 路径原样保留为独立函数；`HLS_SEGMENT_TYPE=ts` env 一键回滚；
    `plan_key` 含段类型，新旧目录互不命中。
  - 实现前先跑一次 golden 命令，确认目标 ffmpeg（WSL 静态 n8.0.1 / 镜像 Debian 版）的
    `%v` 展开、master 结构、init 命名。
- **播放入口泛化**
  - 新增 `GET /sessions/{sid}/{relpath:path}`：白名单
    `master.m3u8 | <name>.m3u8 | <name>/(init.mp4|seg\d+\.(m4s|ts))`；
    沿用双 `normpath` 防越界（`stream.py:744` 先例）+ 25s 等分片；m3u8 仍 `no-store`。
  - `_playlist_text/_seg_count/_playlist_endlist/_SEG_RE`、FileResponse media type、
    prewarm `expected`（按 4s 估）随之调整。
  - `complete.json` 完工判定改为“全部 variant 列表带 ENDLIST”。
  - `_MIN_SEGS` 在 4s 分片下取 3（约 12s 缓冲）。
- 旧 `seg/{name}` 与 `{version_id}/master.m3u8` 直连口保留（旧 dist/旧 TS 目录兜底）。

### 前端（保留自绘控件）

- 切音轨改用 `hls.audioTracks / hls.audioTrack`（`MANIFEST_PARSED` 后按 name/language
  重建下拉），**不重开会话**；原生 Safari HLS 走 `video.audioTracks`，不支持时回落旧重开逻辑。
- 切画质仍走重开会话（目标文档未要求无缝切档）。
- watchdog 重挂后按存下的 rendition name 复位音轨；`isHls` 覆盖新方法名。

### 验收

- MKV H264+AAC → remux fMP4 正常播放、seek/续播/watchdog 无回归。
- 切音轨时视频 ffmpeg 进程 PID 不变、画面无冻结。
- `HLS_SEGMENT_TYPE=ts` 下同流程可用（回滚有效）。
- **重测 AC3/EAC3 copy**：fMP4 下若有声则放宽 `AUDIO_COPY_SAFE`（Windows Chrome 有
  Dolby 解码器）；无声则维持只 copy AAC/MP3。

### P2 落地记录

- **产物扁平命名**（不建 `video/ audio_/` 子目录）：`out_<name>.m3u8` +
  `<name>_init.mp4` + `<name>_segNNNNN.m4s` 全部落会话根目录，由统一路由
  `GET /sessions/{sid}/{name}` 服务；`%v` 取 `var_stream_map` 的 `name`。
- **必须 `cwd=会话目录` 执行 ffmpeg**：`-hls_segment_filename` 相对路径按进程 CWD
  落盘，而播放列表 URI 按列表位置解析；只有裸文件名 + cwd 才能两者一致
  （踩坑：分片曾落到服务进程 CWD，会话目录永远等不到分片）。`build_cmd` 内会把
  输入路径绝对化。
- **master.m3u8 自产**（`_write_master`）：ffmpeg 的 `-master_pl_name` 对 HEVC copy
  不产 `CODECS`（Safari 原生 HLS 需要），改为自写 EXT-X-MEDIA（DEFAULT/CHANNELS/
  LANGUAGE/URI）+ STREAM-INF（BANDWIDTH 估算、RESOLUTION、CODECS、AUDIO="aud"）。
- **默认音轨取源 disposition**（首条 default，无则第一条），与用户选择解耦；
  前端在 `MANIFEST_PARSED` 后用 `hls.audioTrack = audioIdx` 应用选择。
  因此 fMP4 的复用键/目录键都不含所选音轨（`audio_idx` 归一为 null、`a` 归一为 0），
  换音轨/预转码可秒开命中同一成品。
- **分段与等待**：转码路径 `-force_key_frames expr:gte(t,n_forced*4)` 保证 4s；
  copy 路径受源码 GOP 限制（TARGETDURATION 可能 6~10s）。首屏等待只数**视频**分片
  （`_seg_count(prefix)`），音频 rendition 转码更快，混数会在视频就绪前放行。
- **完工判定**：`_variant_playlists` 取全部 `out_*.m3u8`，要求每个都有 ENDLIST
  （fMP4 的 master 是自产索引、无 ENDLIST，不参与判定）。
- **回滚**：`HLS_SEGMENT_TYPE=ts` 走 `_build_cmd_ts`（P1 原命令/命名 `copy_a0_s*`），
  master 路由按 `plan.seg` 决定是否重写 `seg/` 前缀；旧直连口的 `seg/` 路由仅 TS 命名。
- 实测（临时库 + 真实片源）：405（H264+EAC3，1 音轨）audio_transcode 全程 copy；
  403（DV 10bit HEVC + 2 音轨）720p 重编产 2 条 AAC rendition；
  401（HEVC+AAC）remux 的 master `CODECS="hvc1.1.6.L150.B0,mp4a.40.2"`、init tag=hvc1；
  预转码完工后换音轨/档位字符串仍命中静态成品（0.1s，无 ffmpeg 进程）；
  TS 开关下分片 `video/MP2T` + `seg/` 重写正常。
- **实测修复：切音轨无声变化（headless Chromium 复现）**。根因两条：
  1. hls.js 的 `MANIFEST_PARSED` 触发时 `audioTracks` 仍为空，真正就绪在
     `AUDIO_TRACKS_UPDATED`；起播等待期（"正在转码…"）就切轨的选择会丢。
     修复：两个事件都尝试 `applyAudioTrack()`；解析前不重开会话（等事件应用）。
  2. 解析前切轨走旧回退 `reload()` 会与进行中的 reload 竞争出两个 hls 实例
     （旧实例仍在播、`hls` 变量指向新实例）→ 表现为"切了没反应"。
     修复：`reloadGen` 代际守卫（并发 reload 只允许最后一轮挂载）+
     `manifestReady` 标志；原生 Safari 用 `video.audioTracks`（loadedmetadata 后应用）。
  验证：早切（hls 未挂载）→ 解析后 `hls:audioTrack 1` + 只拉 `out_audio1_seg*`；
  播中切/seek 后切同样生效；切画质位置保持（3747→3748）无回归。
- **停服杀转码进程**：`app/main.py` lifespan 退出时 `stream.shutdown_sessions()`
  （uvicorn 优雅关闭/docker stop 不再留孤儿 ffmpeg；SIGKILL 场景仍靠 TTL）。

---

## P3 ASS/JASSUB + 字体 + 字幕模式

### 后端

- `GET /api/stream/{vid}/sub/{idx}.ass`：ffmpeg 抽取缓存（同 VTT 逻辑，`stream.py:884`）。
- `GET /api/stream/{vid}/fonts/{name}`：首次 `ffmpeg -dump_attachment:t "" -i` 抽 MKV 字体到
  `transcode/{vid}/fonts/`；仅 ttf/otf/ttc/woff2，白名单 + 防穿越。
- 内置兜底字体走运行时可投放目录 `data/fonts/*.woff2`（不往仓库塞大字体），
  经 `/api/stream/fonts/builtin/{name}` 提供；无附件时前端提示可自行投放。

### 前端

- 加 `jassub` 依赖（懒加载，同 hls.js 模式）；worker/wasm 经 Vite `?url` 引入。
- ASS/SSA 轨 → JASSUB canvas 覆盖 video（`subUrl`、`fonts`、`fallbackFont`）；
  seek/换会话时重建；全屏 z-index 与 `pv-wrap` 对齐。
- 字幕下拉加“兼容字幕(VTT)”项强制走旧 `<track>` 路径；图片字幕仍烧录（reload）。

### 验收

- 带样式 ASS 中文字幕（含内嵌字体片源）样式/位置/动画正确；无字体时不崩、可切 VTT。

### P3 落地记录

- 后端：`GET /{id}/sub/{idx}.ass`（`-c:s ass` 统一转 ASS，SSA 也归一，缓存同 VTT）；
  `GET /{id}/fonts` 汇总（attachment + builtin）；`GET /{id}/fonts/{name}` 首次请求触发
  `ffmpeg -dump_attachment:t ""`（在独立 `.dump` 子目录执行，只回收 basename+字体扩展名，
  防路径逃逸；成功才写 `.dumped` 标记）；`GET /fonts/builtin/{name}` 服务
  `data/fonts/*`（`ensure_dirs` 建目录，`.gitignore` 排除）。
- 前端：`src/jassubLoader.js` 懒加载（`workerUrl` 必须是 RPC worker
  `jassub/dist/worker/worker.js?worker&url`；误指 emscripten glue 会导致 `ready` 永挂、
  不拉 `.ass`/字体——实测踩坑）；`vite.config` 需 `worker.format='es'`（jassub 内含
  `new Worker(new URL(...))`，iife 无法代码分割）。`applySubs()` 统一管理
  `<track>` / JASSUB / 烧录；归属键含 videoKey，元素级自救后重建；seek 由 JASSUB 逐帧
  同步（实测 seek 后仍有字幕）；关闭/切轨销毁 worker。
- 「兼容字幕(VTT)」勾选出现于选中 ASS 轨时，降级 VTT 并跨会话记住
  （`localStorage jzmedia.subCompat`）；无字体时提示可切兼容或投放字体。
- 实测（Playwright + 合成 MKV：黑底视频 + 红色 ASS 样式行 + CJK 行 + 内嵌 DejaVu/Noto CJK
  + 内置字体）：ASS 模式红字 7236 px（样式保留）+ CJK 白字 1835 px，字体三源全部拉取；
  兼容模式红字 0、`<track>` 生效；切回 ASS canvas 重建；seek 后红字仍在；关闭播放器
  worker 销毁、canvas 移除。目录穿越请求只落到 SPA 兜底（无文件泄露），非本片字体 404。

---

## P3.5 图片字幕客户端化 + 外挂字幕（字幕独立于视频）

目标（用户第一性原则 + 目标文档 §8 预留的 bitmap renderer）：字幕渲染与视频彻底解耦——
切换字幕/调时间不重开会话、不转码；NAS 视频 CPU 为零；烧录只剩 VobSub 与降级兜底。

### 落地记录

- **PGS → libpgs 客户端渲染**：`GET /{id}/sub/{idx}.sup`（内嵌 `-c:s copy` 抽取缓存；
  外挂 `.sup` 直服；VobSub 415）。前端 `src/pgsLoader.js` 懒加载 `libpgs`
  （`workerUrl` 必须显式传，默认值是相对路径会 404），`PgsRenderer` canvas 叠加，
  `timeOffset` 支持延迟；**解码失败/超时（20s）自动降级烧录**（`force_burn` 进
  decide/sessions → `subtitle_mode=burn`，`reasons=subtitle_burn_forced`）。
- **字幕延迟**：ASS(JASSUB)/PGS(libpgs) 的 `timeOffset`，±0.5s 步进（±10s 上限），
  按版本存 `localStorage jzmedia.subDelay.<vid>`；VTT 原生 `<track>` 不支持（已确认边界）。
- **外挂字幕**：`scanner.sidecar_subtitles()` 同目录（含 `subs/Subs/字幕` 一层）stem 匹配；
  `_sub_list()` 合并内嵌+外挂（`source/sidecar` 标记、文件名后缀推断 lang/title、图片 codec
  归一 pgs/vobsub）；`.srt→VTT/ASS` 转换缓存（按源 mtime 失效），`.ass/.ssa` 直服；
  `.idx/.sub` 成对去重（优先 `.sub`）走烧录第二输入 `[1:s:0]`（输入侧 `-ss` 与主输入同
  提前量对齐时间轴）。**无内嵌字幕时自动选中第一条中文文本外挂**（图片不自动选）。
- **决策/复用**：`subtitle_mode` 扩为 `none|webvtt|ass_client|pgs_client|burn`；
  `_plan_marker` 只对 `burn` 保留字幕字段 → 客户端渲染的字幕选择不再影响静态成品复用。
- **工具**：`scripts/find_subs.py`（只读 SQLite + 磁盘）列出可测片源：内嵌 ASS/PGS/VobSub、
  字体附件、外挂字幕、未探测行数；`--limit/--json`。
- 实测（Playwright + 真实片源片段）：内嵌 PGS 选中/切换轨道 `sessionPosts` 恒为 1
  （**不重开会话不转码**），合成画面 diff 28643 px（可见）；切外挂 ass/srt/sup 同样不重开；
  SideTest 无内嵌字幕时自动选中中文外挂 ASS（JASSUB 激活）；+0.5s 延迟生效；
  阻断 libpgs worker → 超时兜底：`force_burn=true` + 新会话（body 带 `force_burn`）。
- 已知边界：`.sup` 整片抽取/下载（局域网一次性、缓存即时）；VobSub 仍烧录；
  VTT 无延迟；`.idx` 多语言只取第一流。

### 反馈修复（《刑房》外挂 ASS 等三项）

1. **《刑房》外挂 ASS 不显示**：两个叠加原因——(a) 外挂后缀 `简中` 未在语言词表内 →
   不满足"自动选中中文外挂"规则；(b) **原文件直发（direct）时自绘控件条整体不渲染**
   （`v-if="isHls"`），用户无法手动选字幕。修复：词表补 `简中/繁中/中日/中…`（有序匹配）；
   控件条改为 direct 也启用（去掉原生 `controls`，`--pvb` 统一预留 46px，seek/时长/音量
   对 direct 走同一套 `absPos`/`doSeek`）。另：ASS 无可用字体且含 CJK 时 **自动降级 VTT**
   （`assNeedsCjk` 前 40KB 探测 + `autoVttSub` 按轨，提示投放 `data/fonts/`），
   有 CJK 字体时 libass 字形回退正常（实测墨迹 40835px vs VTT 33406px，均可见）。
2. **延迟控件出现又消失 / 控件条拥挤**：按用户建议把**画质/音轨/字幕/兼容/延迟**收进
   头部「⚙ 设置」弹层（日常只留按钮，空白处/Esc 关闭）；控件条只保留播放/时间/进度/
   音量/全屏。弹层对 direct 同样可用。
3. **窗口模式控件遮挡画面/字幕**：核因是 (a) direct 用原生控件（悬浮在画面底部）、
   (b) libpgs 自建 canvas 用 `inset:0` 覆盖整个 wrap（含控件条区域），字幕坐标落进被
   遮挡区。修复：统一自绘控件条并预留 46px；PGS 改为传入自建 canvas，内联样式
   `top:0; width:100%; height:calc(100% - var(--pvb))`（动态元素吃不到 scoped 样式，
   必须内联）。实测：`video.bottom == .pv-ctl.top`（overlap 0）、PGS canvas rect 与
   video rect 完全一致。
4. 附带修复：所选音轨非默认且客户端无原生 HLS 时 direct → remux（
   `audio_track_selection`），否则设置里切音轨在原片直发下会静默无效。

---

## P4 TranscoderBackend + HDR/DV

### 后端

- **新增 `app/transcode.py`**：`Backend`（`video_encoder / scale_filter / tonemap_filter /
  hwaccel_args`）+ `detect()`：
  - env `TRANSCODER=auto|sw|vaapi|qsv|nvenc`；auto 下 `/dev/dri/renderD128` 存在 →
    0.2s lavfi 冒烟编码验 VAAPI → QSV → 软件；结果缓存。
  - VAAPI：`-vaapi_device /dev/dri/renderD128 -hwaccel vaapi -hwaccel_output_format vaapi`
    + `scale_vaapi` + `h264_vaapi`。
  - QSV：`-init_hw_device qsv=hw -filter_hw_device hw` + `scale_qsv` + `h264_qsv`。
- 暴露 `GET /api/health` 后端字段 + 新 `GET /api/stream/backends`。
- **HDR/DV 判定矩阵（`playback.plan`）**
  - `caps.hdr && HEVC 支持` → direct/remux 直通。
  - DV profile5（无 HDR10 基底）→ 不直通；DV8.x compat=1 → 按 HDR10 处理。
  - 否则：HW 后端存在 → tonemap 转 SDR（vaapi 优先；软件 zscale 链仅显式选择）；
    都不可行 → 播放器头部常驻“复制直链/外部播放器”入口（复用 blob，
    `app/routers/movies.py:432`；详情页已有 Kodi 直链区）。
- compose/README：NAS 侧加 `devices: [/dev/dri:/dev/dri]` + `group_add`（render 组）说明；
  WSL override 不动。

### 验收

- `/api/health` 正确报告后端；不支持 HEVC 的客户端 4K HEVC 转码走 VAAPI 且 CPU 明显低于 libx264；
- HDR 片在支持端直通、不支持端给出外部播放建议。

---

## P5 预转码完整化 + 收尾

- `prewarm` 复用新 `build_cmd`：一次产出 video + 全部音轨 rendition（quality 可选，
  默认按后端 auto）；状态含各 variant 分片数；完工静态 VOD 判定覆盖全部 playlist。
  （预转码目标下拉与复用键归一已在 P1 收尾完成。）
- player 头部加“复制直链”；
  `/sessions/{sid}/debug` 补 backend/variants/caps hash/各列表 ENDLIST。
- 文档与版本：更新 `AGENTS.md` 播放章节（caps/四档/fMP4 回滚开关/字体目录/`/dev/dri`）、
  README 播放说明（现仍写“不做播放”）；代码版本统一升 `0.7.0`；
  按仓库惯例一功能一提交、发版打 tag。

---

## 风险与护栏

- **P2 高风险**：`HLS_SEGMENT_TYPE=ts` 即时回滚 + 旧 TS 目录继续可播；
  `temp_file`/`independent_segments`/init 命名先在目标 ffmpeg 实测。
- **P1 与 P2 解耦**：只上 P1 也能立刻拿到零 CPU 收益，且是后续所有阶段的前置。
- 探测缓存升级靠 `probe_ver` 自愈，不需要一次性全量重探测。
- 无测试框架（仓库现状）：每阶段用 sample_media + `/api/stream/*` 手工验收，
  关键命令与 `/sessions/{sid}/debug` 输出留档。

## 开放项（后续再看）

- MKV 容器直通（Chrome 可直解 mkv）：等 caps 就绪后做实验项，暂按目标文档 remux。
- 无缝切画质（不重开会话）：成本高，目标文档未要求，暂不做。
- 内置 CJK 字体是否随仓库分发：优先运行时可投放，实际体验不足再评估内置子集。
