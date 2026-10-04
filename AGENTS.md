# AGENTS.md

现行约束核对：2026-10-04。下文功能条目的日期记录引入或修复背景；操作入口与行为按当前代码维护，末尾版本记录保留发布时的历史事实。

## Stack

### Backend

FastAPI + stdlib `sqlite3` (no ORM), Vue3 + Vite frontend. 验证：`pytest`（tests/）+ `scripts/check_python.py` + 前端 `npm test`/`npm run lint`。GitHub Actions 的 Release 工作流在版本标签推送时完成双端检查与发布；手动运行只验证，不发布。

### Entrypoints

- `app/main.py` (app + SPA hosting，发行版本以根 `version.properties` 为准；启动初始化 ensure_dirs/init_db 在 lifespan，导入期无副作用),
- `app/store/` (SQLite+FTS+facets/filters),
- `app/scanner/` (scan/match flow),
- `app/tmdb.py` (TMDB client),
- `app/regions.py` (country→region mapping, single source),
- `app/routers/` (`health|media_libraries|libraries|movies|collections|files|extras|fs|jobs|persons|stream`; TMDB search lives in `movies` router),
- `app/nfo.py` (Kodi NFO),
- `app/scanner/tv_parse.py` (TV 集号规则阶梯，纯函数).

### Playback

`app/media.py` (ffprobe probe + ffmpeg bin resolve) → `app/caps.py` (ClientCapabilities normalize/hash) → `app/playback/` (4-tier plan `direct|remux|audio_transcode|video_transcode` + `build_cmd`) → `app/routers/stream/` (sessions/heartbeat/TTL/HLS). `POST /api/stream/{id}/decide`, `POST /api/stream/versions`, `POST /api/stream/{id}/sessions` accept `caps`; GET variants use `caps.default_caps()` (conservative). `media_info.probe_ver < media.PROBE_VERSION` auto-reprobes on play.

Frontend capability detect: `frontend/src/caps.js`。`direct_url` 已由服务端编码，前端直接赋给视频 `src`，不得再次 `encodeURI`；中文/空格/%/&/# 路径的真实浏览器回归见 `scripts/smoke_settings_ui.mjs`。

#### 倍速/进度预览（2026-09）

`playbackControls.js` 统一六档倍速、片内 seek 范围与拼图映射；`PlayerModal` 在切档/换元素时恢复倍速，`PlayerSeekbar.vue` 鼠标悬停/触屏拖动预览，`usePlaybackPreviews.js` 请求代际防晚到响应。

HLS seek 优先 `buffered/seekable` 内复用；`sessions` 对非零 start 优先命中 start=0 完整缓存，响应 `media_start`（源时间偏移）+ `initial_time`（片内起播位置），前端必须分别使用。

`stream/previews.py` 单后台任务，逐帧输入侧 seek + 每 25 帧一页拼图，GET 不触发生成；POST 单片或 `{library_id}` 批量（`store.preview_items` 包括 movie/episode/extra），取消/停服回收子进程，部分页可续做。

独立 `DATA_DIR/previews` 缓存按源标识/size/mtime/生成规则失效；`PREVIEW_INTERVAL` 缺省本地 10s/远程 20s，`PREVIEW_CACHE_GB` 缺省 2GB（活跃/最近访问保护），不跟转码 24h TTL。

UI 设置中生成单片，电影/剧集维护面板 `PreviewMaintenance` 批量生成。回归 `test_playback_previews.py`（含临时视频真实 FFmpeg 抽帧）、`test_stream_cache_seek.py`、`playbackControls.test.js`、`playbackPreviews.test.js`。

#### quality

`auto`(默认；需视频重编且源>1080p 时封顶 无HW 720p/有HW 1080p) | `source`(原画不封顶) | `1080p` | `720p`；`original` 兼容为 auto。UI 显示“实际输出”。

#### 播放产物复用

产物目录键 `_artifact_key` = `_session_key` 前缀 + 完整 `_plan_marker` 的 SHA-256；`_quality_key(plan)` 仅决定 copy/h720/h1080/src（烧录加 `_burn`）前缀。

复用键包含源库/路径/size/mtime、精确 start 与实际产物规则；fMP4 不含所选音轨，非烧录字幕归一、不影响复用。预转码与在线播产物键一致时共享完整成品，每个客户端仍有独立 sid。

#### 音频 copy 安全集

hls.js 只信 `aac/mp3`（EAC3/AC3 copy 实测无声），Safari 原生 HLS 才放行 Dolby；其余 `audio_transcode` 转 AAC。可用 env `AUDIO_COPY_SAFE` 放开实测可用的编码。

#### HLS 输出

默认 fMP4（`HLS_SEGMENT_TYPE=ts` 回滚旧 MPEG-TS）。fMP4 单进程 `-var_stream_map` 产 video + 全部音轨 rendition，扁平命名 `out_<name>.m3u8`/`<name>_init.mp4`/`<name>_segNNNNN.m4s`，每片 4s；

`master.m3u8` 由 `stream._write_master` 自产（ffmpeg 对 HEVC copy 不产 CODECS，Safari 需要）；变体 `out_*.m3u8` 被 ffmpeg 持续改写，服务端按内容快照返回（`session._read_playlist_stable`，**不用 FileResponse**——stat 定长遇增长会抛 `Response content longer than Content-Length`）；

重编 variant 的 CODECS 按输出宽高取最小 level（`transcoded_video_codec(w,h)`，如 1282×720→`avc1.640020` 3.2）。**ffmpeg 必须 `cwd=会话目录` 执行**（分片相对名按 CWD 落盘，输入路径已在 `build_cmd` 绝对化）。

#### 音轨切换

fMP4 走 `hls.audioTrack`（`MANIFEST_PARSED` + `AUDIO_TRACKS_UPDATED` 后应用——前者触发时 `audioTracks` 可能还是空），不重开会话；解析前切轨只改选择不重开（`manifestReady`/`reloadGen` 防并发 reload 出双 hls 实例）；

原生 Safari 用 `video.audioTracks`。前端选择与服务端产物解耦（默认音轨=源 disposition 首条 default）。

#### 停服

`main.py` lifespan 退出调 `stream.shutdown_sessions()` 杀转码子进程（防孤儿 ffmpeg）。

#### 关窗清场（2026-09 用户实测「关播放器后海报墙传出片声」修复）

PlayerModal 用 `disposed` 标志 + `reloadGen` 代际 + 每轮 `AbortController`（`api()` 支持外部 `signal`，外部取消抛「已取消」）守卫，每个 await 后校验；`onBeforeUnmount`（videoEl 尚有效）显式 `pause/removeAttribute('src')/load()`，`onUnmounted` 置 disposed 并作废在飞 reload；

晚到的 session 立即 DELETE。服务端兜底：会话创建标 `unclaimed=True`，首次 playlist/分片/心跳请求（`_get_session`）认领，`_UNCLAIMED_TTL=90s` 内无人认领由 `_dead_sessions`/sweeper 快速收割（客户端 abort 拿不到 sid 的孤儿路径）；

硬件转码首轮只等 45s 不出片即回退软编（原 300s）。起播等待期视频区有 spinner 浮层（`booting`，防用户误以为卡死而关窗）。回归 `tests/test_stream_sessions.py`、`frontend/tests/playerLifecycle.test.js`。

#### 首屏等待

首屏等待只数视频分片（`_seg_count(prefix)`），片数按 plan 自适应（`_min_segs`：copy/remux=1，视频重编/烧录=2；env `MIN_SEGS_COPY/MIN_SEGS_TRANSCODE`）——copy 分片按源码 GOP 切（实测 2160p 片 9s/片），原固定等 3 片=27s 内容，慢链路上起播黑屏很久；

完工=全部 `out_*.m3u8` 带 ENDLIST；统一路由 `GET /sessions/{sid}/{name}`（白名单 `_SESS_FILE_RE`）。

#### 远程输入探测限流（`media.probe_limit_args`，仅 ffmpeg 转码输入用）

`-probesize`(默认 2M)/`-analyzeduration`(1s)/`-rw_timeout`(30s)（env `FFMPEG_PROBESIZE/FFMPEG_ANALYZEDURATION/FFMPEG_RW_TIMEOUT_US`，0=关）——ffmpeg 默认 5MB/5s 探测在慢链路上首帧前白读十几 MB。

**不要**用于 ffprobe 元数据探测（附件/字体可能排在 2MB 之后，截断会丢字体信息）。

#### 预缓存（慢链路/远程库）

详情页对 `remux/audio_transcode` 且视频库为 smb/nfs 的版本显示「预缓存到服务器」（`Detail.vue` + `libraries.isRemoteVideoLib`；direct 无需缓存，本地库读取快不显示），复用 `POST /api/stream/prewarm`（copy 档几乎不耗 CPU，只读盘）→ 完工走 `_session_complete` 静态 VOD 秒播、零 NAS 读取。

预缓存从 `start=0` 生成完整成品；续播/seek 创建独立 sid 时，可复用相同产物规则的完整缓存，通过 `initial_time` 定位。成品未完成、规则不符或缓存已淘汰时才重新读取源文件；成品受 24h TTL 与缓存限额约束。

#### 转码缓存限额

`_purge_old` = ① 24h TTL 删会话目录 ② 总量超 `TRANSCODE_CACHE_GB`（默认 10）按最旧淘汰到 90%（`_transcode_cache_scan/_evict_transcode_cache`，跳过进程在跑/10min 内取过流的会话目录与 5min 内新目录；限额扫描 5min 节流）；

`POST /api/stream/cache/clean {dry_run}` 手动清理（设置页「系统维护 → 播放缓存 → 检查可清理空间 → 确认清理缓存」两段确认，全局）。

#### 字幕（客户端渲染为主，`subtitle_mode: none|webvtt|ass_client|pgs_client|burn`）

字幕子系统在 `frontend/src/useSubtitles.js`（composable，PlayerModal 解构使用；含选轨/默认轨、本地临时字幕、VTT 自绘、ASS/PGS、延迟/外观/兼容降级；R13-Q1/R14-Q1 抽离）。**文本/VTT → 自绘 DOM 层**（`.sub-layer`，fetch+解析 cue，rVFC 渲染循环/100ms 轮询兜底，图层按 contain 内接矩形定位到画面区、字号随画面高缩放；解析失败回退原生 `<track>` 并有全局 `::cue` 去黑底兜底；PiP 不显示）；

ASS/SSA → `GET /{id}/sub/{idx}.ass` + JASSUB（`frontend/src/jassubLoader.js`，`workerUrl` 必须指 RPC worker `jassub/dist/worker/worker.js?worker&url`）；

PGS → `GET /{id}/sub/{idx}.sup`（`-c:s copy` 抽取）+ libpgs（`frontend/src/pgsLoader.js`，workerUrl 必传）；切字幕不重开会话不转码；libpgs 失败/超时自动降级烧录（`force_burn`）。

仅 VobSub 走烧录（外挂第二输入 `[1:s:0]`）。**时间轴对齐**：HLS 会话片内 0 = `media_start`（`POST sessions` 返回；copy 会话=目标前关键帧，转码=start），所有客户端字幕的偏移在渲染时叠加 = `media_start + subDelay`（自绘层逐帧判定 cue 源时间区间；ASS/PGS 走 `timeOffset`）；

直连/烧录不需平移。字幕延迟按版本存 localStorage `jzmedia.subDelay.<vid>`（VTT/ASS/PGS 均支持）；字幕外观/定位存 `jzmedia.subStyle`（背景 无/半透明/纯黑 + 字形四向 text-shadow 描边 无/细/粗 + 位置 auto黑边优先/画面内/下黑边 + 字号 小/中/大，仅自绘文本层）。

位置策略对齐 mpv `sub-use-margins`：auto 时下方黑边 ≥ 字号+4px 锚到视频元素底（字幕落入黑边，多行允许“一行画面内一行黑边”），否则锚画面区底部；纯计算在 `frontend/src/subStyle.js`（可 node 单测）。

#### 外挂字幕

`scanner.sidecar_subtitles` 同目录(含 `subs/字幕` 一层)匹配——严格 stem 同正片/以正片 stem 开头，或**标题宽松匹配**（字幕 stem 剥尾部语言提示 token 后 guessit 标题与正片相同，如 `大桥下面.srt ↔ 大桥下面 (1984).mkv`、`大桥下面.chs.srt`；空结果走 `_strip_lang_tail`/`_title_key`，`lru_cache`）；

`_sub_list` 合并内嵌+外挂（`source/sidecar`，无内嵌时优先第一条中文文本外挂、无中文再兜底第一条文本外挂为 default，均不自动选图片轨，后缀词表 `_SIDECAR_LANG_HINTS` 已移至 `scanner/classify.py`，`stream/subtitles` re-import）；

`data/fonts/*` 为内置字体投放目录（`ensure_dirs` 创建、gitignore）。

#### 音轨选择

原文件直发（direct）只能播默认音轨 → 选了非默认轨且客户端无原生 HLS 时自动改走 remux（HLS rendition 切换）；reason `audio_track_selection`。

#### 转码后端（`app/transcode.py`）

`detect()` 用 0.2s lavfi 冒烟编码按 VAAPI→QSV→NVENC 探测，失败回落软件并缓存；env `TRANSCODER`（兼容旧 `HW_ACCEL`）= auto|sw|vaapi|qsv|nvenc 可强制；参数由 `Backend.input_args/video_args/tonemap_filter` 生成（`-hwaccel` 必须在 `-i` 前；烧录走软件）。

编码统一 **High profile + 强制 IDR**：NVENC 默认 Main 且不加 `-forced-idr 1` 时 `-force_key_frames` 不生效（HLS 视频分片按源码 GOP → 10s+，音频 4s 先跑 → 画面卡住声音继续；2026-09 沙丘 720p 实测修复），QSV 用 `-forced_idr 1`，VAAPI 本构建无该选项（NAS 待实测）。

`GET /api/stream/backends`（`?refresh=1`）与 `/api/health.transcoder` 暴露结果。硬件路径不出片时 `_spawn_session` 自动软件重试一次（`force_sw`）。NAS 需 compose 映射 `/dev/dri`（见 compose 注释/.env `TRANSCODER`）。

#### HDR/DV

`_hdr_blocks_direct` 判定——DV compat=2 当 SDR、compat=1 当 HDR10、无兼容基底（P5 等）阻断；HDR10/HLG 与 DV P8.1 在客户端能解 PQ 时原画直通（`caps.hdr_decode`，与显示器是否 HDR 解耦；旧字段 `hdr`/Safari `native_hls` 仍认，SDR 屏由浏览器/系统 tone map，Plex 式），解不了才转码且**仅硬件后端做 tonemap**（`tonemap_vaapi`/`vpp_qsv`），无硬件时 reason `hdr_no_tonemap`（提示色彩偏灰 + 设置里「复制直链」外放）。

#### 播放器外观（2026-09）

`PlayerIcon.vue` 统一线性 SVG 图标；双行控件（`PlayerSeekbar` 缓冲/已播/缩略图 + 播放/±10s/音量/倍速/设置/全屏），单击画面暂停、双击全屏。`PlayerSettings` 向上展开，播放/字幕/更多选项分组，尺寸由容器 `ResizeObserver` 约束；

状态、错误重试与继续观看在视频容器内，全屏同样可用。移动端精简音量滑条；≤600px 时当前时间/总时长独立成行，六个主控保留 44px 触摸目标，倍速从设置可达；`useFocusTrap` 全屏时限定在视频容器，键盘聚焦保持控件可见。静态样式在组件 scoped、跨组件按钮在 `player.css` 的 `.player-dlg` 下；

视频/冻结帧显式使用 `height: calc(100% - var(--pvb))`，字幕图层仍以视频矩形定位。

#### 前端 UI

画质/音轨/字幕/外观/位置/延迟/兼容收进弹层（底部齿轮入口；弹层在底部控件栏**条件渲染窗口/全屏两个实例**（`v-if="!isFull"` / `v-else`，2026-09 弃用动态 Teleport 宿主——用户反馈全屏找不到设置入口），`settingsProps/settingsEvents` 共享绑定，打开时保持悬浮控件常显）；

字幕行「加载字幕文件」可临时加载本地文本字幕（srt/vtt/ass/ssa；浏览器端 `decodeSubtitleBytes` 解码 UTF-8/UTF-16/GB18030 兜底，ass 走 blob URL + JASSUB，VTT/SRT 自绘；`localSubs` 与服务端轨合并、按 `localId` 跟踪选中，切档/seek 后仍在，不入库、关播放器失效，`reload` 对服务端传 `sub=null`）；

控件条（自绘）对 direct 与原 HLS 统一启用（不再用原生 `<video controls>`，避免遮挡画面/字幕，`--pvb` 窗口态预留 84px，≤600px 时为 104px，全屏为 0）；PGS 画布用 `ensurePgsCanvas` 内联定位到画面区（动态元素吃不到 scoped 样式）。

ASS 无任何可用字体且含 CJK 时自动降级 VTT（`autoVttSub` + 提示投放字体）。掉帧看门狗 `frontend/src/dropGuard.js`（纯函数 + node 单测）：仅视频 copy 且源 >1080p 参与，30s 窗口丢 ≥5 帧 → 播放器居中浮层询问（窗口/全屏统一：切换/取消，10s 无操作=保持原画），手点降档、不自动切不记忆；

调试快照含 dropped/total/corrupted 帧。

#### 字体

MKV 附件首次请求 `/{id}/fonts/{name}` 时 `ffmpeg -dump_attachment` 到 `transcode/{id}/fonts/`；`GET /{id}/fonts` 汇总 attachment+builtin。前端「兼容」勾选强制走 VTT（localStorage `jzmedia.subCompat`）。

#### 找可测片源

`python scripts/find_subs.py`（只读，列内嵌 ASS/PGS/字体附件/外挂）。

## Run

### Android TV

`android-tv/` 为同仓独立 Gradle 工程，先读其 `AGENTS.md` / `README.md`；日常构建与测试可独立执行，发行必须通过根 `scripts/release.py` 从同一干净 Git 提交同步构建 Docker 镜像与 APK。`start.sh`、Dockerfile 与 Vue 构建本身不依赖 Android SDK。已接入连接/浏览/Media3 观影、`android_tv` 原生能力和独立 sid/共享转码任务；

协议 1 见 `android-tv/docs/protocol.md`，进度及真机待验收项见 `docs/roadmap/android-tv.md`。服务端、网页、帮助站与 APK 共用根 `version.properties` 的 `versionName`；Android `versionCode` 在该文件全局递增。统一构建不代表设备必须同时升级，连接时仍检查协议与能力。

### Backend (WSL dev, hot-reload via `docker-compose.override.yml`)

`mkdir -p media/电影 data && docker compose up --build`, check `http://localhost:8080/docs`。主 Compose 的 `.env` 可选（Compose ≥2.24.0），已有文件继续读取、不自动生成；运行用户、端口与卷可直接在 Compose 配置，或保留变量写法使用 `.env`。容器接收环境变量，不需要容器内存在该文件。

### Host-direct (no docker)

prefer `./start.sh` — no `.env` required; rebuilds `frontend/dist` only when stale, uses host data/media defaults even if an optional `.env` contains container paths (`/app/media`, `/app/data`), then runs uvicorn.

Manual equivalent uses `DATA_DIR=./data MEDIA_ROOT=./media`; optional `TMDB_*` can come from the process environment or web settings (see docs/getting-started/deployment.md).

### Frontend dev

`npm run lint`（eslint 最小集：未定义/未用变量/console 警告）、`npm test`（node --test；含 `tests/templateBindings.test.js` 模板标识符绑定检查，防拆分后残留父级引用；`tests/useSubtitles.test.js` composable 初始化冒烟，防 setup 期异常导致"点播放无反应"——lint/build 不执行 setup，这类回归必须靠它）in `frontend/`；

`npm run build` 产出 `frontend/dist`。`npm run dev` (5173, proxies `/api`,`/posters` → 8080). Prod build: `npm run build` → `frontend/dist`, served by FastAPI at `/` + `/assets`.

### 验证命令

`.venv/bin/python -m pytest -q`（含迁移/多库/媒体库聚合/离线匹配/TV/存储层；`pytest.ini` 默认 120s/用例熔断，hang 转失败+堆栈，`--timeout=N` 临时覆盖）；`.venv/bin/python scripts/check_python.py`（pyflakes 严格包装，仅固定白名单的导出门面及同名重导出例外）；

冒烟 `scripts/smoke_multi_library.py`、`scripts/smoke_metadata_offline.py`（临时目录、不触网）；线上自检 `/api/health` + docs/developer/api.md。

### 健康容量（2026-10-03）

`GET /api/health` 的 `disks` 检查 `settings.data_dir` 与 `db.TRANSCODE_DIR`，按 `st_dev` 关联并去重文件系统容量，`available_bytes` 取 `f_bavail`、`used_bytes` 不含保留空闲块。

同卷不重复累加；缺失目录不创建、不向父目录猜测，失败独立返回结构化错误且不回显路径。顶层 `status` 仍按 DB/媒体可读性，容量监控需另检查 `disks.ok`；不自动设告警阈值。回归 `tests/test_health_disk.py`；接口说明 `docs/developer/api.md`。

## Documentation

- 编写、维护或审核全仓文档（含 `android-tv/`）时使用 `.agents/skills/jzmedia-docs/SKILL.md`，详细规则以 `docs/developer/documentation.md` 为准。功能、命令与用户流程变更时检查文档影响；

  纯内部重构无影响时不启动全仓审核。教程和操作指南先完成任务、后展开原理；参考与解释按各自用途组织。同一事实只维护一处，行为变更替换原文。

- 仓库目录职责见根 `README.md`，工具入口见 `scripts/README.md`；开发计划与完成状态放 `docs/roadmap/`，私有操作记录放 `docs/private/`。阶段性实施和测试总结写入提交或 PR 说明，不新增独立报告目录；长期约束并入对应开发文档，素材来源维护在清单。

  已完成且仅适用于单次数据状态的修复脚本从工作树删除，Git 保留历史。`output/` 整体忽略，不提交本地验证输出；设计导出与教程素材依各自清单提交。

- HTML 帮助站使用独立 `docs/` npm 包（VitePress 1.6.4），正文单源，图文组件位于 `docs/.vitepress/theme/`；普通教程按任务组织，开发参考继续 Markdown。`/help/` 与 FastAPI `/docs` 分开，应用内帮助链接使用 `HelpLink` 新标签页打开。

- 构建/检查：首次 `npm --prefix docs ci`，完整验证 `npm --prefix docs run verify`（格式 lint → 源链接检查 → build → test → 产物 check）；快速格式检查 `npm --prefix docs run lint` 覆盖全仓源 Markdown（含 skill），局部检查可加 `-- android-tv` 等仓库相对路径。浏览器冒烟 `npm --prefix docs run test:browser`，可加 `-- --url http://127.0.0.1:8080/help/` 验证应用托管。

  独立站 `npm --prefix docs run preview`，发行包 `npm --prefix docs run package`。维护入口 `docs/developer/documentation.md`。

- 在线帮助共用正文，`JZMEDIA_DOCS_TARGET=pages`（`JZMEDIA_DOCS_BASE` 默认 `/jzmedia/`）构建到 `docs/.artifacts/pages/`；构建、检查和预览共用 `docs/scripts/site-config.mjs`。应用目标固定 `/help/`，宿主构建和离线包强制使用应用目标；根版本文件参与构建摘要。

  `.github/workflows/docs-pages.yml` 在正式 Release 的 `publish` 成功后部署同一发行提交，独立手动入口只重发最新正式版本。`scripts/github_pages.py` 核对发行清单、标签和源码，部署前再次检查版本，旧任务跳过；Pages 首次设置与恢复见 `docs/developer/releasing.md`。

- 公开页面与导航统一由 `.vitepress/public-pages.mjs` 管理；`docs/private/`、`docs/roadmap/` 不可进入站点或搜索索引，禁止整目录静态发布 docs。Mermaid 修改后运行 `npm --prefix docs run diagrams`，提交图表缓存；

  普通 build 不下载浏览器。

- `app/help_site.py` 只托管 `.vitepress/dist`，未构建返回 503，缺页返回 404；构建生成 `csp-hashes.json`，仅帮助 HTML 的 CSP 加入哈希。Docker 复制静态产物，`start.sh` 经 `scripts/build_docs.py` 按输入内容检测更新。

- 图解/录屏用隔离演示实例和合成媒体，脚本与素材元信息一起维护；不能为拍摄对真实媒体执行扫描、整理或删除。界面变更同步检查对应教程及素材，记录适用版本与核对日期。

## Env / paths (gotchas)

### 配置与路径映射

Config is `os.getenv` in `app/config.py`; compose sets `MEDIA_ROOT=/app/media`, `DATA_DIR=/app/data` inside container. In code always use `settings.media_root` / `settings.data_dir`, never host paths (`MEDIA_HOST_PATH` is compose-only volume mapping).

### 代理与凭据

`TMDB_PROXY` is runtime httpx proxy for API + poster/avatar download; `BUILD_HTTP_PROXY` is build-time only (pip/npm in Dockerfile/compose args). `TMDB_READ_TOKEN` (Bearer) takes precedence over `TMDB_API_KEY`.

### Data

`./data/jzmedia.db` + `./data/posters/`（按功能拆子目录，`app/posters.py` 单一来源：`movies/<tmdb>.jpg` 主海报、`orig/<tmdb>.jpg` 原图、`backdrops/movie_<id>/tv_<id>.jpg` 背景、 `persons/<id>.jpg` w185 头像、`tv/<id>[_sN].jpg` 剧/季海报、`stills/<episode_id>.jpg` 集剧照懒下载、`cand/` 候选缩略图、`tvcast/` 剧集演职头像；DB 存 `posters/...` DATA_DIR 相对路径，前端 `posterUrl` 保留子路径；根部旧文件 + DB 旧值由 `migrate_posters()` 启动一次性搬迁，读侧保留旧名回退）；

NFO rule: 专属影片目录默认只写 `movie.nfo`；同片多版本仅在 `naming_profile=plex` 且 edition 不同时补每版 `<stem>.nfo`（`PER_VERSION_META=1` 可回退旧策略）；混放不同影片的共享目录只写当前同名 NFO（见 `library_paths.per_version_meta` / `scanner.sync_nfos_for`）。

All gitignored — never commit.

## DB / FTS rule

- SQLite 以 WAL 运行（`_conn` 带 busy_timeout=5000，R02-D2）；FTS5 `movies_fts` has **no triggers** — after any `movies`/`persons`/`movie_person` write you must call `store.resync_fts(movie_id)` (`update_movie_meta` already does; manual SQL or `link_person` does not). `store.init_db()` + `rebuild_fts()` run at startup and self-heal old trigger schemas (`DROP TRIGGER IF EXISTS movies_ai/ad/au`).

## Conventions / constraints

### 剧集播出与收藏（2026-10-03，schema v31）

`app/tv_airing.py` + `store/tv_airing.py` 持久快照/季目录/事件/租约/冷却；lifespan 启停，首次检查启用库内已确认 TMDB 剧，同 TMDB 去重，非结束剧 7 天、完结/取消 90 天，季目录按需缓存。`tv_collection.py` 只读对照当前媒体库的有效收藏，官方编号与本地来源分离，SP/未来季不计缺季，无法确认的跨季/多集/绝对编号保守处理；

`collection_reason` 区分 `catalog_missing/numbering_unresolved/coverage_incomplete/match_review/show_unconfirmed`，缺官方目录只说明资料不全、保留已收藏数、不提示重新匹配，分集实际 `needs_review/binding_conflict` 才提示匹配核对并限定受影响范围，剧级待确认用 `show_unconfirmed`，其余受跨季覆盖不明影响的季用中性 `coverage_incomplete`，保守 `uncertain` 不参与缺集推荐、不增加周期请求；

不造本地分集、不改播放进度/编号、不写 NAS。新增 `/api/tv/shows/{id}/collection`、`seasons/{season}/catalog|poster`、`updates?media_library=`、`airing/status|check`；

普通详情/墙 GET 仅读缓存，旧 API 保持本地文件语义。网页详情显示缺季与“已收藏/全部分集”，墙每周推荐已播未收藏，浏览器曝光去重与服务端检查独立；关闭推荐不关服务器维护。回归 `test_tv_airing/test_tv_collection`、`tvUpdates/tvCollection`，隔离浏览器 `node scripts/smoke_tv_airing.mjs`；

说明 `docs/user-guide/watch-tv.md`。

### 设置草稿保护（2026-10-03）

`settingsDrafts.js` 由 `Settings.vue` 提供注册表，共享表单通过可选 `useSettingsDraft` 注册所属区块、dirty/busy 与丢弃动作；独立新手向导不受此注册表影响。覆盖 TMDB、AI、匹配规则多库、访问令牌、媒体库/视频库表单，区块/路由离开用 `SettingsLeaveDialog`，刷新/关页用 `beforeunload`。

默认继续编辑；忙碌操作不能强行放弃；确认丢弃仅在 `router.afterEach` 导航成功后执行，其他守卫拒绝离开时保留草稿。保存失败和同 ID 数据刷新保留编辑，成功保存后关闭等待弹窗并留在原页。显示偏好即时保存、后台维护任务和测试查询不作为配置草稿。回归 `settingsDrafts/settingsFormDrafts/settingsMatching`；

隔离浏览器 `node scripts/smoke_settings_ui.mjs --drafts-only`，教程 `docs/user-guide/settings.md`。

### 跨端设计资产（2026-10-03）

`design/` 管理SVG母版、品牌、主题语义令牌、资产定义和全量使用需求；`python3 scripts/build_design.py` 生成Web注册表/令牌、TV ImageVector/令牌及用途图鉴，`--check`验证引用与产物，品牌位图更新使用独立锁定依赖加`--render-brand`。

禁止直接改生成物或在业务组件手写第二份SVG/字形图标。`AppIcon/PlayerIcon`共源但保留旧语义别名（导航forward≠快进10秒）；普通按钮用`JzButton`（icon/iconOnly、el/focus），专门控件通过清单登记。`ArtworkPlaceholder`保留缺图标题/姓名。

`node scripts/smoke_design_system.mjs --demo`含可检索图鉴；需求记录区分CSS px/dp、矢量网格、触摸目标、位图分辨率和实际验证。手机播放器窄屏时间独立成行、倍速从设置可达、六个主控保留44px。`start.sh`通过`scripts/build_frontend.py`按内容摘要检测源码/public/入口/配置/锁文件及删除。

详见设计源README及界面规范。

### Web 筛选（2026-10-03）

电影/剧集墙共用 `BrowseFilters` 快捷面板和全部筛选抽屉，`BrowseFilterSummary` 显示可移除的已选条件；面板草稿在“应用筛选”时才生效，关闭/Escape 丢弃，重置只改草稿。手机全部筛选为全屏。`browseFilters.js` 统一规范化/计数/标签，国家选择清除大区，年份与年代取交集；

选项计数为本库总量，不是组合结果预估。媒体库/路由变化丢弃草稿，facets 请求代际防旧库回包；返回详情恢复列表位置，不恢复打开的面板。回归 `browseFilters.test.js` / `browseFilterPages.test.js`、隔离浏览器 `scripts/smoke_browse_filters.mjs`；

说明 `docs/user-guide/find-movies.md`。

### UI 设计系统（2026-10-02）

`styles/tokens.css` 定义语义变量，`styles/base.css` 承载原 App 全局基础样式；通用控件 `JzButton/JzField/JzDialog/AppIcon`，浏览页 `BrowseToolbar/BrowseResultsHeader/EmptyState`。

弹窗 Teleport 后主按钮仍独立生效，焦点陷阱按最上层处理；复杂播放器/文件预览保留专门生命周期。项目 skill `.agents/skills/jzmedia-ui/SKILL.md` 与 `docs/developer/design-system.md` 说明使用边界；

`node scripts/smoke_design_system.mjs [--demo|--capture]` 为隔离组件目录及浏览器检查，不接真实 API/媒体，输出 `output/playwright/` 不提交。

### UI 信息层级（2026-10-02）

设置用分区标题/范围与扁平表单，概览待办优先、媒体连接可展开、TMDB 凭据/可选配置/真实验证结果分组；手机分类入口紧凑。浏览页统一结果数量/排序，继续观看为横向紧凑卡片；手机详情小海报与标题并排、操作先于简介，未匹配电影/剧先点“匹配资料”展开原表单。手机文件名下显示元信息，保留五项排序与“更多”操作。

四步引导显示进度和明确导入目标；`useFocusTrap` 嵌套锁背景滚动、菜单打开弹窗前恢复可见触发器，上传操作常驻 footer。`node scripts/ui_visual_review.mjs` 用完整模拟 API/原创海报审查 1440/390/375px，`--source` 冻结改前源码，同 fixture 哈希才可比较；

`--capture-docs` 更新对应教程图，详见 `docs/developer/documentation.md`。

### 文件入口与预览（2026-10-02）

扫描与整理只保留页内工具标签，独立次要按钮“管理文件”通过 `fileNavigation.js` 保存 `sessionStorage jzmedia.files.origin.v1` 来源（视频库/工具标签/展开步骤/滚动/还原勾选），URL `files_from` 绑定来源，`files_return` 一次恢复；

刷新及文件页切库保留，侧栏直接进入/成功离开/转扫描清理，失败导航不丢来源。返回仍经原文件变更守卫，展示守卫前关闭预览。`FilePreviewHost/FilePreviewDialog` + `filePreview/useFilePreview` 供 FsBrowser 和 MovieFileManager 共用；

双击/Enter 原地预览，详情为独立动作。已入库视频按具体 episode/extra/feature movie ID 使用 `PlayerModal.preview=true`（默认 false），不读写/删除观看进度、不发已看/连播事件；未入库原文件预览，不临时造行。

`GET /api/fs/blob?library=&path=` 必须显式精确库和规范相对路径，支持只读/本地/SMB Range；inline 图片/PDF/视频白名单，文本前 64KiB 且 `X-Preview-Truncated/Limit`，纯文本显示。

关闭/切库取消请求及晚响应，卸载前停播断源；错误预览区分404/403/503。回归 `test_fs_preview.py`、`fileNavigation/libraryFileEntry/filePreview/fsBrowser`，`smoke_settings_ui.mjs` 使用临时 FFmpeg 合成媒体和真实 PlayerModal 验证预览与普通播放的进度隔离（不接真实 NAS）。

### 设置与文件管理改版（2026-10-02，schema v29）

侧栏分为媒体管理、资料与智能、系统设置；原资料来源拆为在线资料服务（`sec-tmdb`）、匹配规则（`sec-matching`）、离线资料（`sec-offline`）、智能辅助（`sec-ai`）。库级链设置草稿/排序/保存由 `useMatchingSettings` 管理，`GET /api/metadata/test-search?library=&q=` 按已保存链及 movie/tv 类型测试。

文件管理独立 `sec-files`，`FsBrowser/useFsBrowser/fsBrowser` 提供视频库目录树、全量分页列表、排序/筛选、多选、右键菜单与关联文件预览；目录改名及跨库复制/移动仍不支持。`store/fs_changes.py` 持久记录文件实际变更，`GET /api/fs/changes?library=` 返回待扫描摘要和仍在工作的复制任务；

成功完整扫描仅清扫描开始前的变更，失败/取消/离线/期间新变更保留，扫描快照过期跳过 GC。TV 文件操作保留分集 ID/进度，并同步字幕/NFO/花絮；TV 复制后统一重扫。`LibraryToolsPanel` 的退出守卫 + `FileReviewDialog/useFileChanges/fileReview` 按实际视频库检查，允许留在页面、稍后扫描或扫描后进入核对匹配；

异库刷新独立，请求新快照失败不能静默放行。复制取消后 `worker_finished=false` 仍视为活跃且不可淘汰。回归 `test_fs_changes.py/test_metadata_test_search.py/test_jobkit.py`、`fsBrowser/fileChanges/fileReview/settingsMatching`；

隔离浏览器 `node scripts/smoke_settings_ui.mjs`（`--capture-docs` 更新演示图，不接真实 API/媒体）。

### 智能辅助首版（2026-10-02）

`app/ai/{settings,client,search,match}.py` + `/api/ai/settings`（GET/PATCH）、`POST /api/ai/check|search|match`；默认关闭，DeepSeek/OpenCode Go/自定义兼容 Chat Completions，`AI_*` env 兜底、DB 优先，Key 仅服务端使用/脱敏回显（清除 DB 值可回退 env）。

模型只输出经校验的筛选/候选建议；搜索前端确认后复用当前库查询，匹配只核对来源链真实候选，确认后复用原绑定，绝不自动改手工匹配/目录归属/季集号。模型无效业务 JSON 用 `discard_cached` 清对应缓存；按 UTC 日记录调用次数/token，失败及连接测试计次、缓存命中不计，匹配至多两次模型调用。

`chain._tmdb` 必须按 movie/tv 分流；`chain.candidate_for` 从可信外源详情缓存恢复候选，或允许可回取详情的来源，纯索引候选不可绑定（`bindable:false`），详情失败不能套用当前标题假确认。前端 `AiSettingsPanel/AiSearchPanel/AiMatchSuggestions` 与 `useAiRequest` 处理取消/代际，普通搜索和扫描不调用模型。

回归 `test_ai_client.py/test_ai_assistance.py/test_external_bind_cache.py`、`aiAssistance.test.js`；`node scripts/smoke_ai_ui.mjs` 用真实页面+模拟API隔离冒烟。

真实模型中文效果须单独测量，模拟通过不能当真实效果证据。OpenCode Go=`opencode_go`，官方 base `https://opencode.ai/zen/go/v1`、UI 示例 `glm-5.3-flash`，仅用户切换服务商时填预设；单套 Key 留空沿用。

Go 专用真实 jzmedia UA + `operation_session()` / `x-opencode-session`，同次匹配两调用共享、不同操作隔离，不伪装编码任务；官方用途限制与真实影视用途未验证须显示。文档图片/演示可用 `scripts/smoke_ai_ui.mjs --capture-docs` / `--demo`，全 API 模拟且不载入真实配置，素材逐项标记模拟来源。

### 新手配置（2026-10-02）

`/setup` 独立四步向导（资料来源→视频库→扫描/上传→结果），空库墙欢迎卡片点击进入，设置侧栏保留入口；支持 TMDB 明确跳过、暂缓与恢复。`GET/PATCH /api/onboarding` 以 `app_settings.onboarding_state` 保存实例共享进度；

GET 仅本地 DB，完成必须目标启用、类型一致、配置对应连接检查成功且有 movie/episode 记录（文件传输数不算完成）。`POST /api/tmdb/check` 直连 TMDB `/3/authentication`，8s 超时、不重试／不走降级链；

上游 401 用结构化结果回 200，不能触发应用访问令牌弹窗，错误不回显凭据。共享 `TmdbSettingsPanel/MediaLibraryCreateForm/VideoLibraryForm`，`SetupLibraryStep` 负责确认目标；`UploadDialog.libraryId` 固定目标不跟全局切库，`result/busy` 事件供向导使用。

只读／检测不可写库仅扫描，整理仍需独立预览确认。同秒完成的连续扫描按创建顺序取最新任务（`jobkit.latest`）。回归 `test_onboarding.py`、`test_jobkit.py`、`onboarding.test.js`、`onboardingSetup.test.js`、`settingsSetup.test.js`；

用户说明 `docs/user-guide/onboarding.md`。

### 剧集归属与季号（2026-09-28，schema v28）

`tv_directory_bindings` 持久化目录→剧/季，`tv_binding_history` 保存事务预览与撤销；服务 `app/tv_bindings.py`、纯规则 `tv_binding_rules.py`、存储 `store/tv_bindings.py`，接口 `/api/tv/bindings/{directories,suggest,preview,apply,history,undo}`。

前端 `TvBindingsDialog`/`useTvBindings` 从剧集库工具和剧详情更多菜单进入。确认仅改应用 DB/图片缓存、不写 NAS，重扫和新集沿用规则；显式季号/手工绑定/重复集号需用户明确处理，分集 ID 和进度保留。已确认目录不走绝对号重映射/跨季匹配回退；

换绑/撤销清旧剧照缓存。简单独立季目录可经原整理预览归并至统一剧根/Season NN，规则路径随整理与恢复改写；复杂目录保留人工提示。多物理剧根 NFO/海报维护逐根写，普通规则剧刮削不自动落盘。回归 `test_tv_bindings.py`、`tvBindings.test.js`、`tvBindingsSetup.test.js`；

说明 `docs/user-guide/tv.md`。

### 体验审查实施（2026-09-27）

电影/剧集墙共用 `ScanAction` + `useLibraryScan`，扫描按明确视频库，`GET /api/jobs/scan` 共享状态；`UploadDialog(kind)` 按类型过滤启用可写库，TV 默认文件夹、散文件需剧/季，`upload_support` 后端复核并登记 TV，再由 `useTvUpload` 补资料，整理始终保留预览确认。

远程 `open_write(overwrite=False)` 提交不覆盖。`browseHistory` 按媒体库/筛选/排序保存列表与卡片锚点，恢复后刷新同样页数；`BackToTop` 全局避让弹窗和批量栏。播放三角形统一 `PlayerIcon`；所有 TV 连播入口共用 `episodePlayback` → 服务端 `/next`，详情邻集由 `episode_neighbors` 返回，季页有版本筛选与区间去重。

详情编辑/匹配/更新独立，直链在版本行，修复资料文件选择 NFO/海报（`rebuild-meta.nfo` 默认 true）。概览按具体视频库展示电影与剧集待办。回归：`test_ux_upload.py`、TV versions/pending、metadata maintenance，以及 browseHistory/episodePlayback/scanScope 前端测试。

### 单剧整理弹窗（2026-09-26）

`TvOrganizeDialog.vue` 独立对话框，变更前后路径/文件示例先展示，整理范围默认折叠；绝对编号提示单独显示，不再重复列入手动项目。未确认编号时仅执行后端允许的目录整理，保留正片名；勾选后自动重新预览。`useTvOrganizeDialog.js` 管理预览代际/取消、选项变化即时失效、两次确认、单剧作用域、串行轮询与结果保留；

执行中不可关闭，完成/失败后保留状态。`tvOrganizeDialog.test.js` 覆盖过期响应、空/失败预览、重复执行、显式编号许可与卸载清理；真实媒体整理仍必须由用户在 UI 确认。

### 浏览与详情 UI（2026-09-26）

电影/剧集墙默认收起筛选、保留已选摘要和独立排序；“管理媒体库”按当前页面类型定位视频库。详情共用 `MediaBackdrop` / `MediaOverview` / `ActionMenu` 与 `styles/mediaPages.css`；电影 `GET /api/movies/{id}/backdrop` 独立请求横版缓存（缺图按缓存 TMDB 路径懒下载，失败不影响详情、无海报放大兜底），季/集响应带 `show_backdrop_path`。

电影“影片资料 / 文件与版本”分栏，编辑/匹配/重写 NFO 收进更多操作；剧/季先选集后演员，手机演员横向滚动；简介按实际溢出折叠，菜单收起恢复焦点并限制在视口内。

### Auth (optional)

写操作（POST/PUT/PATCH/DELETE 且 path 以 `/api` 开头）在配置了 `JZMEDIA_TOKEN`（env/设置页，DB 优先）时需带 `X-Api-Token` 或 `Authorization: Bearer`；GET/直链放行。

网页播放决策、创建会话与保存进度使用 POST，同样需要令牌。实现是 `main.py` 的 `_auth_write` 中间件，设置页可写键 `jzmedia_token`（`config.SETTING_MAP` / `store.APP_SETTING_KEYS`）。

### Logging

统一走 `app/log.py`（`get_logger`/`setup_logging`，env `LOG_LEVEL`）；失败路径禁止 `except: pass` 静默（至少 `logger.warning/debug` + 关键上下文）。历史静默点按批次改造，新代码直接遵守。

### 文件浏览（设置页 sec-files，`app/routers/fs/` + `frontend/src/components/FsBrowser.vue`）

文件选择（2026-10-02）：单击行仅定位，双击/Enter 打开；复选框整块区域/空格明确勾选，Shift 连选、Ctrl+A 全选。当前行与勾选项独立，工具栏/F2/Delete/Ctrl+C·X 操作勾选项；右键未勾选行仅操作该行并保留原勾选，菜单标题明确对象。

Esc 仅清勾选并保留当前行，关闭预览保留勾选与焦点；表头支持半选态。Backspace 上级、F5 刷新、Ctrl+V 粘贴。改名/移动跟随 DB 与同茎兄弟；`POST /api/fs/copy` 是 jobkit 后台任务（进度/取消），支持目录递归与 symlink（直读远程无符号链接语义，按普通项处理），冲突自动「(副本)」**绝不覆盖**，≥2GB/≥200 项前端二次确认；

正片复制用 `scan_one(tmdb_hint=源行 tmdb_id)` 自动登记为同片新版本，目录复制不自动登记（完成后引导「扫描新文件」）；**目录改名不支持**（用归档整理）；删除物理删除（正片两段确认，非正片提示不可恢复）。**媒体根模式（2026-09 媒体库级重构）**：`GET /api/fs/list?media_library=` 列媒体库根（只读 `fs_writable:false`、`root_kind:"media"`，真实一级目录并回带视频库 subpath 命中的 `video_library_id/kind` 徽章）；

点进视频库目录后前端切换回 `library` 上下文（分类/改名/删除/正片跳转照旧）；库外目录只读、跨视频库移动/复制不做；`storage.backend_for_media(media_id)` 为媒体级只读后端（伪库 id `1e9+media_id`，SMB 直读可用）。

### 匹配后归档引导（2026-09 用户需求）

绑定 TMDB 成功后详情页调 `GET /api/movies/{id}/organize-hint`（按行内路径自动选就地/搬迁）弹归档确认框，确认直接执行单 id organize；设置页扫描完成/进入「入库整理」时自动预览并展开 ③ 目录整理，无需用户自己找折叠区。

### 设置页现行交互（2026-10-03 核对；独立分区始于 2026-09-26）

`Settings.vue` 侧栏按媒体管理、资料与智能、系统设置分组，11 个分区由 `settingsNavigation.js::SETTINGS_PAGES` 单一维护；桌面侧栏、手机分类下拉，不用长页滚动监听。`settingsNavigation.js` 解析旧 `sec/library/media/ids` 深链，明确分区优先于残留库参数。

库工具首次访问懒挂载、之后 `v-show` 保留任务与表单；`MovieLibraryTools`/`TvLibraryTools` 内分“入库整理/资料维护”（电影另有“还原位置”），文件管理独立 `sec-files`，通过“管理文件”保存来源并可返回原工具状态。

旧 `sec-meta/restore` 分别定位资料维护/还原位置，`sec-files` 直接打开文件页。全局缓存清理独立 `TranscodeCachePanel` 放“系统维护”（dry-run→确认执行）；IMDb 导入在“离线资料”。TMDB 保存与“测试 TMDB 连接”分离，连接测试直连认证接口；

“匹配规则”的测试资料搜索按已保存来源链运行，不能用于判断 TMDB 连通。空令牌不能保存，移除已保存令牌明确确认并按返回状态区分关闭保护/回退 env。`LibrariesPanel` 新建表单按需展开，移除库一次点击展示影响、二次确认；2026-09 曾修正 `form` 声明晚于 watcher 的 setup 异常。

回归 `settingsNavigation.test.js`、`settingsSetup.test.js`；完整入口见 `docs/user-guide/settings.md`。

### 视频库工具架构（2026-09 引入，2026-10-03 核对）

`sec-libtools` 的 `LibraryToolsPanel.vue` 以**视频库**为标签页（多媒体库时标签带媒体名前缀）；`?library=视频库id` 直选，`?media=媒体库id` 按区块类型偏好选择该媒体库的视频库。电影工具 `MovieLibraryTools.vue`：① 扫描 `{library_id}` / 失效清理 `missing?library=`，② 核对匹配 `unmatched?library=`（未匹配/待确认/疑似英文/未归属花絮），③ `OrganizePanel` 目录整理。

剧集工具 `TvLibraryTools.vue`：① 扫描（自动刮削、失效集 GC），② 核对匹配（`GET /api/tv/shows?library=&pending=1`，含集号待处理），③ `TvOrganizePanel` 目录整理。**单步聚焦手风琴**默认展开首个有待办的步骤，完成后推进，手动切换/收起后不抢回。

资料维护标签分别挂 `LibraryMaintenancePanel` / `TvMaintenancePanel`，电影还原位置标签挂 `RestorePanel`（`restore-candidates?library=`，支持 `preselectIds`）；

这些操作均传 `library_id`。文件管理由独立 `sec-files` 中的 `FsBrowser` 处理，全局转码缓存清理位于 `sec-index` 系统维护。面板按视频库 `v-show` 常驻，首次选中 `ensure` 拉重清单，子组件上报 `status` 形成待处理数徽章。

深链兼容：`sec-restore&ids=&library=` 定位还原，`sec-pipeline&media=` 定位对应电影库，`sec-tvorganize&library=` 定位剧集整理，`sec-meta` 定位资料维护；`toolsRef.focus()` 解析视频库与步骤/工具标签，锚点仅选中标签绑定以免重复。

Tab/深链/步骤纯函数在 `frontend/src/libraryToolsTabs.js`，历史分组兼容逻辑在 `libraryToolGroups.js`。

### 库工具接口的作用域过滤（2026-09 媒体库级补齐）

读接口 `GET /api/files/unmatched|missing|restore-candidates` 支持 `library=`（逗号多库）与 `media_library=`（媒体库聚合；未知媒体库→空结果哨兵 `[-1]`，绝不退化全库），missing/restore 返回项带 `library_id`（分表必需）；

`GET /api/tv/shows` 支持 `pending=1`（未匹配 / 剧级待确认 / 有未匹配集）并下发 `episode_review_count`（剧集 Tab ② 步骤数据源，回归 `tests/test_tv_pending.py`）；写接口 `POST /api/files/organize`/`restore-original`/`clean`、`POST /api/jobs/rebuild-nfo`/`backfill-meta`/`tmdb-refresh`/`rebuild-meta`/`clean-bdmv`/`clean-samples`/`rebuild-tv-nfo`/`tv-scrape`/`tv-organize`/`tv-organize-restore`、`POST /api/extras/collect` 支持 body `media_library_id`（单库 `library_id` 兼容；视频库 Tab 一律传 `library_id`）。

`clean-mount-artifacts` 按挂载点 `lib_<媒体库id>` 匹配（`library_id` 自动映射到所属媒体库）。`rebuild-fts` 仍全局（FTS 索引不分库）。缺省行为一律不变（全库）。

### TV 目录规范化（T4.2 v2，v0.17.0）

`app/scanner/tv_organize.py`（`plan_tv_organize` 纯预览 + `execute_tv_organize` 经 StorageBackend 执行 + `summarize_plan` 分组摘要）。7 个动作（`TV_ORGANIZE_ACTIONS`）：`root` 剧根改名（匹配剧 → `中文标题 (年份)`，Plex 规范；未匹配/`naming_profile=off`/目标占用跳过，**最后执行**）；

`seasondir` 季目录规范化（`season 1`/`S04`/`第3季` → `Season NN`，`Specials/特典` → `Season 00`）；`wrapper` 包装层拍平；`season` 剧根散集补 `Season NN/`，且**文件已在季目录但季号与库内不符 → 移入正确 `Season NN/`**（换绑改季拆分后归位，深度 >1 交 wrapper/untouched）；

`specials` S00 特典归位 `Season 00/`（同规则）；`extras` 花絮类型目录**整目录上移**（仅深度 ≤2；季级 `Season NN/<KindDir>` 视为已合规；更深/散文件只列 `untouched` 需手动，花絮文件永不改名）；

`rename` 正片统一命名（`剧名-S01E01[-E02]-集名.ext`，同集多版本从第 2 版起用**版本前缀**（`剧名-V2-S01E01-…`，文件列表 V1/V2 各自成组），S00 同式；同茎字幕/NFO 跟随；**整季单文件（`S01E01-E13` 一文件多集）按普通集正常改名/归位**（扫/刮/剧照/NFO 早已支持多集区间）；`needs_review`/未匹配/绝对集号占比 ≥50%（**且确有正片需改名**时才报——已全部规范名的剧不再打扰）→ 只列 `manual` 不猜；`local_only` 本地集 → `kept`（保持原名，不计需手动）；目标冲突不覆盖）。

**一次动一个文件**：移动与改名合并为单次 rename（from 按已执行目录映射链式投影，`_resolve_show_dir` 防 `[OVA]` 被当剧根）；执行顺序 typo → wrapper → seasondir → season → specials → extras → 空目录 → rename → **root 最后**；

守卫 `.torrent` 整剧阻断（`allow_torrent` 放行）、只读库跳过；同步 DB（`store.move_tv_paths`/`repath_*_prefix`，`scan_state` 跟随）、只删空目录、执行后补写季海报。任务 `POST /api/jobs/tv-organize {library_id|media_library_id|ids, actions[], dry_run, allow_torrent}`（dry-run 返回逐剧分组摘要：`groups`（每组带 `episodes/files` 分计正片与字幕/NFO 附属，避免"正片 3/文件 12"两口径）/`untouched/manual/kept/conflicts` + `absolute` 风险剧数 + `dir_totals`（**最终季目录分布，含无需移动的正片**——防"Season 01 只显示需移动的 1 项"误读））；

设置页「扫描与整理 → 剧集视频库 → 入库整理 → ③ 目录整理」面板（信息架构 2026-09 重构：顶部一句话用途；7 动作收进「整理项目（默认全部）」折叠网格，每项带一句说明+示例；预览结果先给结构化 chips（将执行 N 部/正片/附属文件/需手动/冲突/保持原名/深层花絮/做种跳过），再分两区——「将执行（N 部）」行卡片（剧名 + 动作 chips + 徽标，展开明细：分组标题「正片 X + 附属 Y」、basename→basename hover 全路径、执行后分布、四类提示块）与「仅提示 · 不会执行（N 部）」折叠区（一行一句 `noteText`，如「深层花絮 245 个未整理」「保持原名 1 项（本地集）」）；勾选/全选只作用于将执行区；两段确认/进度；绝对集号风险剧默认不勾选，需勾选**「我已核对无误，同意按 TMDB 编号改名」**——勾选后自动重新预览（带 `allow_absolute_shows`，计划即含正片改名）并把风险剧纳入默认勾选，未确认时执行按钮禁用 + 行内提示）。

**单剧入口（对标电影 organize-hint）**：`GET /api/tv/shows/{id}/organize-hint`（复用 `plan_tv_organize(ids=[show])` 同步返回，`needs`=有可执行移动/改名，风险剧 `needs=false` 仍带 `absolute/manual/conflicts` 解释；`params` 直接回传 `POST /api/jobs/tv-organize` 执行，审计/撤销链路不变）；

剧详情页匹配成功后自动弹窗（`TvShow.vue`：7 动作勾选 + 预览 + 绝对风险显式勾选 + 两段确认直接执行本剧 + 轮询 `load()/verifyExists()`，另有「更多操作 → 整理剧集目录」手动入口：先 `POST /api/tv/shows/{id}/discover` 发现新集，再获取整理预览；「去设置页整理」深链 `?sec=sec-tvorganize&library=`；弹窗与面板均显示「最终分布（正片）」行，纯函数 `frontend/src/tvOrganizePlans.js::dirTotalsText/riskShowIds/defaultChecked(allowAbs)`）。

体检脚本 `python scripts/tv_structure_report.py --library 3`（只读）。回归 `tests/test_tv_organize.py`、`tests/test_tv_organize_restore.py`、`frontend/tests/tvOrganizePlans.test.js`。

### TV 多版本（V1/V2）展示与连播（2026-09 用户需求）

`store.episode_version(file_path)` 从文件名解析版本（`剧名-V2-S01E01-…` 版本前缀 / `…-V2` 旧后缀，无标记=1）；`_episode_payload` 带 `version`；`TvShow.vue` 季卡片显示去重集数与版本数，`SeasonView.vue` 通过“播放版本”筛选分集，V2 及以上在集号旁显示 `Vn` 徽标。

自动连播和邻集导航共用 `store.episode_neighbors/episode_after`，**仅沿当前版本**并跳过已覆盖的多集区间，当前版本没有下一集即停止。剧/季详情的推荐播放项由 `_next_unwatched` 计算：优先最近未完播断点，再找同版本后续未看项，最后可回退其他未看项；

不能把这一推荐回退当作自动跨版本连播。回归 `tests/test_tv_versions.py`、`frontend/tests/episodeVersions.test.js`。

### TV 整理加固（2026-09-22 全库执行发现并修复）

① `_clean_name` 必须清洗 `/`——集名含 `/`（如 `OVA#6「走光风/变身」`）否则 rename 会建出**子目录**（已修 + `test_rename_sanitizes_slash_in_episode_title`）；事后修复出包王女 27 个文件（拍平回季目录，审计批次 `repair-*`）。

② 剧根解析 `_resolve_show_dir` 改为「所有集路径公共目录 → 去尾部季/特典目录 → 父目录只属本剧才上溯包装层」（`store.has_other_show_under_prefix`）——子剧套在父目录（`七龙珠/龙珠 (1986)/Season 01`）不再被误判到父目录，发布包装层仍正常上溯（`test_resolve_show_dir_nested_subshow_and_wrapper`）。

③ 绝对集号风险剧需**显式勾选**才改名（`allow_absolute_shows`，UI 勾选即同意；默认只列 manual）。NAS 实测：56 剧 4455 操作 6 批执行 0 失败，复检 0 待办、重扫 60 剧/3877 集不变。

### TV 编号口径修正与 Plex 拆分集（2026-09-22 用户逐项审核执行）

历史一次性修复脚本（已完成并从工作树删除，原 `scripts/fix_tv_bindings.py` 可通过 Git 历史查看）把 5 部剧与 TMDB 对齐——进击的巨人 S01（本地 13=总集篇→S00E01、14-26=官方 13-25，用户实看确认；BD 版 13/21 与 TV 有画面差异属未删节）、S04E85→S04E26（保留本地绝对编号）、S05E01/02→S00E36/E37、S00 八个 OAD 重绑 S00E07/E13-E19；

怪兽8号 S02E01《保科の休日》→S00E18、S02E02-E12→S01E13-E23；老友记双长集拆分文件按 Plex `-partN` 绑定同一 TMDB 集（S06 存在两处双长集，按只读时长探测整体重排 E16-E25）；神探夏洛克 S03E04（可恶的新娘，TVDB DVD 顺序）→S00E09；

黑镜 S00E01（白色圣诞节）→S02E04。全部文件操作逐条审计（`fix-*`/`repair-*` 批次可撤销），随后 force 重刮 + force 重写逐集 NFO/海报，重扫 60 剧/3877 集。配套加固：`store.move_tv_paths` 两阶段更新（链式/置换改名 `A→B、B→C` 不再撞 UNIQUE）；

`smb.rename` 自动建目标父目录（与本地语义对齐，跨季移动不再 404）；整理器识别 Plex 拆分集 `-partN`（幂等保留，不再转 `-V2-`，回归 `test_rename_keeps_part_files`）。教训：修正脚本跨进程重跑前必须先检测「已迁移」状态（本次 AoT 断点续跑触发 `.fixtmp` 链，已按「DB 预期路径 + scan_state 大小」唯一配对修复）。

### TV 整理审计/撤销（v24，2026-09 事故修复）

`organize_moves` 表逐条留痕（`library_id/show_id/batch_id/kind/obj(dir|file)/action/from_path/to_path/undone_at`，`store.record_organize_moves` 由 `execute_tv_organize` 在每个 rename 成功点写入，含 rmdir 与附属文件；`obj=dir` 的还原走**前缀重写** `repath_*_prefix` 而非逐文件）。

`plan_restore/execute_restore`（`app/scanner/tv_organize.py`）：按批次**反向时序**还原（后发生的先回退），校验源存在 + 原路径空闲（冲突只报告），还原后回写 DB、清扁平 `scan_state`、标记原移动 `undone_at`；

**不再自动写 NFO/海报**（目录内原有元数据随目录搬回，需要时到该库资料维护运行「重写剧集 NFO 与海报」）。接口：`GET /api/jobs/tv-organize/history`（批次列表）、`POST /api/jobs/tv-organize-restore {batch_id?,shows?,kinds?,dry_run:true}` + `GET/cancel`；

设置页「剧集目录整理」面板内「整理历史/撤销」区（批次下拉 + 类型勾选 + 预览/两段确认/进度）。**保守 extras 规则**：默认只把类型目录（`Deleted Scenes`/`Behind The Scenes`/…）**整目录上移**到剧根同名目录（同名冲突不合并），其余散文件仅列入 `untouched` 不动（文件级拍平已删除）。

v24 之前的历史移动由 `store.backfill_legacy_organize_moves()` 在 `init_db` 一次性从 `scan_state` 按 `(basename,size,mtime)` 配对回填 legacy 批次（事故快照仅本地留存 `docs/private/`，未入库；298 条全部唯一配对）。

回归 `tests/test_tv_organize_restore.py`。教训：**开发期禁止对真实媒体库执行 execute**（只允许 dry-run/测试目录），真实执行必须走 UI 两段确认。

### TV 花絮/剧场版/未匹配集（T4.1，v0.16.0）

数据模型 v23 —— `extras` 加 `show_id`（movie_id 语义不变）+ `tv_episodes.needs_review`。扫描分流（`scan_tv_file`）：样片 → `skipped_sample`；剧库内电影目录（Movies/剧场版/真人版）→ `kind='movie'`、其余花絮 → `extra_kind(rel)`，统一 `store.upsert_tv_extra` 挂到**剧根正片所属剧行**（`find_show_by_dir_prefix` 按集路径前缀解析，防 TMDB 改名后按目录名建重复行——真实事故 `Breaking Bad` vs `绝命毒师`；`reattach_tv_extras` 每次扫描自愈重挂 + 空行 prune）。

播放新增 `kind='extra'`：`normalize_kind`/`store.get_playable` 读 extras 行（`extra_kind` + 中文 label），会话/缓存目录前缀 `x<id>`（`_kind_prefix`，防与电影/剧集同号撞目录），`_decide_payload` 直链 `/api/tv/extras/{id}/blob`，断点键 `('extra',id)` 隔离。

API：`GET /api/tv/shows/{id}` 返回 `extras[]`（exists+label）与 `review_count`；`GET /api/tv/shows/{id}/tmdb-episodes?season=`（按需拉候选，不落库）+ `POST /api/tv/episodes/{id}/match-episode {tmdb_episode_id, season}`（本地季集号不变、只取 TMDB 元数据并清 needs_review、废剧照缓存）；

`apply_tv_detail` 对 TMDB 无对应集的行标 `needs_review=1`（已手工绑定/有 tmdb_episode_id 的不重复标）。前端：剧详情「剧场版」「花絮」区 + 集行「未匹配集号」徽章与候选弹层；PlayerModal 支持 `kind=extra`（`kindParam`/`kindSuffix`/字幕延迟键 `x.<id>`，连播只对 episode）。

**本地确认集（v25，2026-09-23）**：`tv_episodes.local_only` + `POST /api/tv/episodes/{id}/confirm-local {title?}`（TMDB 无对应集的本地集，如马达加斯加 E04《爱登堡和巨蛋》= TMDB 电影 123669，按 4 集口径留在剧内）；

`apply_tv_detail` 对 local_only 行不自动匹配/不重标 `needs_review`，`match-episode` 绑定时清该标记，organizer 不列 manual；详情页弹层「确认无对应集」按钮 + 「本地集」徽标。NAS 实测：271 个花絮/剧场版登记（绝命毒师 245、胜者即是正义 14、犬夜叉 5、进击的巨人 5、武林外传 2；2 个样片跳过），14 集标未匹配（老友记 6/进击 3/黑镜 2/怪兽8号 1/神探夏洛克 1/马达加斯加 1）。

回归 `tests/test_tv_extras.py`、`tests/test_tv_scrape.py::test_confirm_local_episode_survives_rescrape`。

### TV 落盘/维护（T3，v0.15.0）

`app/nfo.py` 增 `render_tvshow_nfo_bytes`/`render_season_nfo_bytes`/`render_episode_nfo_bytes`；`app/scanner/tv_nfo_link.py`（`tvshow.nfo` 剧根 + 季目录 `season.nfo` + 每集 `<视频同名>.nfo`；**所有权哈希保护** `tv_shows.nfo_hash`/`tv_episodes.nfo_hash`（v22 迁移，外部改过默认不覆盖，`force` 才重写）；`show_dir_of` 从集文件路径上跳季目录推导剧根；可选 `.plexmatch`（env `TV_PLEXMATCH=1`，写 title/year/tmdb/tvdb 提示））。

`app/artwork.py::write_for_show`：剧根 `poster.jpg`/`fanart.jpg` + 季目录 `seasonNN-poster.jpg`（与电影同门槛：`artwork_mode=nfo_art` 且库非只读；`thumbs=True` 才写每集 `<stem>-thumb.jpg`，千集级默认关）；

本地/远程统一走 StorageBackend，内容相同不重写。**逐集 NFO 默认仅本地库写**（`_episode_nfo_enabled`：env `TV_EPISODE_NFO` 显式设置优先，否则按 `backend.abs_path` 判定；NAS 实测 SMB 原子写 ~1.6s/文件，3,877 集 ≈ 1.7h，远程默认关；job 可传 `episodes:true`）。

SMB 写路径优化：`_ensure_parent` 已知目录免每次 `makedirs`（0.4s/次，list/mkdir/write 登记 + 删除时失效）。触发：`tv_persist.write_media_files`（刮削/手动匹配后；匹配接口在后台线程写，防大剧阻塞请求）。

任务：`POST /api/jobs/rebuild-tv-nfo {library_id|media_library_id|ids, dry_run, thumbs, episodes}`（离线重写 NFO/海报，jobkit 进度/取消；设置页「扫描与整理 → 剧集视频库 → 资料维护」有「重写剧集 NFO 与海报 / 预览写入清单」按钮）。

`PATCH /api/tv/shows/{id}`（title/overview_override/custom_rating/tags/watched；**手工标题保护**：`title_auto=1` 为扫描自动写入、可被 TMDB 覆盖，手工改名后 `title_auto=0` 刷新/重刮不覆盖）；

剧详情页「更多操作 → 修改剧名」。NAS 实测：60 剧 `tvshow.nfo` + 126 季 `season.nfo` + 3,877 集 `<视频同名>.nfo` + 60 poster + 60 fanart + 130 季海报落盘（0 失败；逐集并发 4 约 67 分钟）（逐集 NFO 远程默认关，见上）。

fs 浏览器剧库内正片带 `show_id`；双击/Enter 原地预览，进入详情为独立动作。回归 `tests/test_tv_nfo.py`。

### TV 刮削/前端（T2，v0.14.0）

`app/scanner/tv_match.py`（匹配：目录 hint → 本地已匹配 → TMDB 搜索；相似门同电影，**同档多候选按 popularity 取最热并标待确认**——防 `西部世界` 绑到 1980 版 Beyond Westworld；`short_candidates` 回退 + **别名表兜底**（Legal High/Hanzawa Naoki 英文目录名 vs 本地化标题）；`force` 重刮跳过本地索引与库内年份，强制真实搜索）+ `app/scanner/tv_persist.py`（detail+季详情 → `tmdb_cache(media_type='tv')` → `tv_shows` 镜像/`tv_seasons`/`tv_episodes`；**集匹配三档回退**：精确 (季,集) → TMDB 跨季连续编号（越狱兔 S2 编号 14–26）→ 本地季拼接/绝对号（咒术 S2E1→TMDB S1E25、AoT S4E60→S4E1）；海报/季海报并发下载到 `data/posters/tv/`，背景下载到 `data/posters/backdrops/`，集剧照懒下载 `GET /api/tv/episodes/{id}/still`（10min 失败冷却）；离线用 `payload_json` 重放标 `ok_offline`）。

`POST /api/jobs/tv-scrape`（jobkit：`media_library_id|library_id|ids` + `force`，设置页「扫描与整理 → 剧集视频库 → 资料维护」内「补全缺失剧集资料/重新获取全部剧集资料」按钮 + 进度轮询）。

API 补齐：`GET /api/tv/search`、`shows/{id}/match|refresh|confirm-match|watched`、`episodes/{id}/watched|next|still`、`GET /api/tv/recent-played`（剧集继续观看：断点粒度、海报粒度去重、`subtitle=SxxEyy`）；

`GET /api/tv/shows/{id}` 带季对象（name/poster）、每集 `progress`、`next_episode`、`watched_count`；`list_shows` 带 `watched_count`。前端：`Tv.vue` 海报墙（`ContinueWatchingRow kind="tv"` 继续观看行/已看角标/待确认角标/搜索）+ `TvShow.vue`（hero 背景+海报、季 chips 带海报、集列表剧照/播出日期/断点条/已看切换、「播放下一集」、手动匹配面板（TMDB 搜索→匹配）、确认匹配、刷新元数据、单剧整理弹窗见上）+ PlayerModal **`@ended` 连播**（`watched` 按共享完播规则触发（已播 ≥95%，或已播 ≥80% 且剩余 ≤300s），连播只认 `ended`；父级换 `:key` 重挂载）。

**匹配可见性**：`POST shows/{id}/match` 快回包自带剧快照 `show{tmdb_id/title/poster/backdrop/has_overview/fetched_at}` + 落盘状态 `media{artwork.reason}`（小剧 ≤120 集同步写 NFO/海报，大剧后台线程 `media.queued=true`；`TV_MATCH_SYNC_EPISODES` 可调；TMDB 不可用时按 `tmdb_cache` 离线绑定 `{offline:true}`，与电影同语义；`tmdb_id` 值域守卫 422；**显式换绑 `force_title=True`**（新条目标题必覆盖 `title_auto=0`，防换绑后标题停留旧条目，对齐电影 `manual_match`）；单季拉取失败在响应 `seasons_failed` + 日志告警（不静默丢一季集号回填））；

前端 `posterVer` 破同 URL 缓存 + `waitForMedia` 补齐轮询 + 落盘失败原因行内提示（`disabled/read_only/no_backend` 不再静默）。流：`/api/stream/versions` 支持 `kind=episode`（同剧同季同集多版本）、prewarm 支持 `kind=episode`（含 `_register_prewarm_session` 存 kind）、`_media_start_for` 缓存键含 kind（防电影/剧集同号串缓存）。

NAS 实测：60/60 剧匹配、3,863/3,877 集（99.6%）拿到 TMDB 元数据、60 海报/200 季海报、12 部标待确认；回归 `tests/test_tv_scrape.py`。

### TV（T1 扫描器 v2，v21）

`kind=tv` 库扫描走 `scanner.scan_tv_file`（**规则阶梯** `app/scanner/tv_parse.py`：`SxxEyy`+区间 `E01-E02/E73E74` → `NxNN` → `第X集` → `EPxx`/`Exx` → 日期 → 特典 `SP/OVA/OAD` → 裸数字绝对集号（含**括号包裹的尾部数字** `高清版 (240)`——与分辨率同值（240/360/720/1080…）也按集号，`Show.1080p` 不受影响）；季目录回退支持 `Season 01`/`S02 后缀`/`第X季`/`Specials`，`season==年份`/`>99` 守卫；guessit 仅兜底且**裸数字时不采信其季号**——`0001.flv` 不再落 S0）。

`scan_all` 预计算**子剧拆分**（剧根下直接含集文件且无季子目录/非花絮/非特典/非电影目录 → 独立 show，如 `七龙珠/{A,Z,GT}` → 3 部）与无编号特典顺序号；多集文件记 `episode_end`、绝对集号记 `absolute_number`；

**增量**（`scan_state` mtime/size 未变且 tv_ok → `skipped_unchanged`，`force` 强制）+ **失效 GC**（完整遍历后删磁盘已不存在的集行/空剧，级联断点/探测缓存；库离线绝不 GC）；剧库内 `Movies/剧场版/真人版` 目录按非正片跳过。

`VIDEO_EXTS` 已含 `.rmvb/.rm/.mpg/.mpeg`。数据模型 v21：`tv_shows` 元数据镜像列 + `tv_seasons` + `tv_episodes`（episode_end/absolute_number/still/air_date/watched/missing…）+ **`tmdb_cache` 复合主键 `(media_type, tmdb_id)`**（电影/剧集数值 id 空间独立；电影侧查询默认 movie，SQL join 已带 `t.media_type='movie'`）；

`store.upsert_episode` 重扫不覆盖已刮削元数据。`/api/tv/*` 只读浏览（季对象含 name/poster 等扩展字段）；播放统一 `store.get_playable(kind,id)` + stream 接口 `kind=episode`（media_info/播放断点/会话目录/字幕字体缓存按 `(kind,id)` 隔离，目录前缀 `m<id>`/`e<id>`）；

`/api/tv/episodes/{id}/blob` 为直链。体检脚本 `python scripts/tv_parse_report.py --library 3`（只读，与入库同口径）。NAS 实测：4,150 视频 → 3,877 集 / 0 未知 / 273 花絮，60 部剧（含七龙珠拆 3 部）。

回归 `tests/test_tv_parse.py`、`tests/test_tv_scan_v2.py`、`tests/test_tv_inventory.py`。

### New pages

don't add bare `GET /...` routes — the catch-all `GET /{full_path:path}` in `main.py` serves the SPA (routes: `/`, `/m/:id`, `/p/:tmdb_id`, `/collections`, `/c/:id`, `/settings`).

API routes must live under `/api` routers.

### 豆瓣接口边界

`POST /api/jobs/douban-fetch` is intentionally `501` (no Douban scraping by default) — keep the stub. Only remote bulk write is `POST /api/jobs/tmdb-refresh` (explicit ids only); `backfill-meta` is offline from `tmdb_cache`.

### 媒体库/视频库两层（2026-09 用户需求，v17；v18 切换/聚合/合集调整）

**媒体库** `media_libraries` = 存储连接/根目录（source/path/smb_*/nfs_*/凭据/read_only/auto_mount/enabled/健康状态/storage_identity，挂载点 `data_dir/mounts/lib_<media_id>`）；

**视频库** `libraries` = 媒体库下带类型的子树（`media_library_id + subpath + kind(movie|tv)`，`path` 为「媒体根 + subpath」派生生效根，迁移/建改时同步）。业务分区 `movies/tv_shows/tv_episodes/extras/scan_state` 的 `library_id` 一律指视频库 id（v18 起 `collections.library_id`→`media_library_id`）。

`store.list_libraries()/get_library()/default_library()` 返回**增强视图**（联表带出媒体连接字段 `source/read_only/smb_*/nfs_*/media_name/media_mount_point/media_enabled`，`smb_subpath`=媒体+视频拼接），storage/mounts/scanner/library_paths 零感知。

API：`GET /api/libraries` 仍是视频库扁平列表（新增 `media_library_id/media_name/subpath` 字段）；`/api/media-libraries` 负责媒体库 CRUD/检查/挂载/诊断/`GET {id}/subdirs`（建库 body 必带 `video_libraries:[{name,subpath,kind}]` 至少一条；`PATCH` 连接/路径/只读；`DELETE` 级联删其全部视频库记录+合集+取消任务+断会话+卸载）。

视频库 `POST /api/libraries` 带 `media_library_id`，旧扁平 body 仍兼容（自动建同名媒体库+根视频库）；`PATCH/DELETE /api/libraries/{id}` 只改名称/类型(仅空库)/子路径/命名档等，连接/路径/只读属于媒体库（422 提示）。

同媒体内视频库子路径禁止重叠/嵌套（含根，大小写不敏感），媒体库根之间也不允许重叠；存储身份与"已存在指向同一存储的媒体库"查重都在媒体库层。**v18 读聚合**：`/api/search|/api/movies|/api/facets|/api/search/suggest|/api/tv/shows|/api/tv/stats|/api/collections|/api/collections/suggest` 新增 `media_library=<id>`（展开为其全部视频库 id；未知/无视频库→空结果哨兵 `[-1]`，绝不退化成全库），`library=` 视频库参数保留兼容；

`POST /api/jobs/scan {media_library_id}` 扫媒体库全部启用视频库。迁移 `_m17`：每个旧库原 id 建同名媒体库（挂载点/影片归属 id 不变）；远程旧 `smb_subpath` 最后一段下沉为视频库 subpath；

本地库若全部记录在同一顶层子目录则自动拆出并去掉路径前缀（`_m17_guess_subdir/_m17_rebase`）。迁移 `_m18`：collections 回填 `media_library_id` + 同媒体库同名合并（成员并集）。前端：顶栏切换器只列**媒体库**（不再按类型过滤/分组），电影墙/剧集页按当前媒体库聚合（URL `?media=<媒体id>`，旧 `?lib=` 映射兼容），`libraries.js` 以媒体库为当前选择（`mediaParam/switchMedia/currentMediaVideoLibs/preferredVideoLibId`，localStorage `jzmedia.media`，旧 `jzmedia.lib` 作回退）；

上传弹窗在媒体库有多个电影类视频库时给目标选择器（`jzmedia.uploadLib.<mediaId>` 记忆）；组件 `LibrariesPanel.vue` 两层管理（媒体行 + 展开视频库行 + 建库时视频库清单 + 添加视频库/检测子目录）。

### 库路径

媒体库根 `local` 必填容器内路径（建库校验存在/不嵌套）、`smb|nfs` 不填路径（UI 单输入框粘贴完整地址，后端 `app/smburl.parse_smb_url` 解析成 host/share/subpath，兼容 `smb://`；也接受手动三段 `smb` dict），后端建行后自动派生 `data_dir/mounts/lib_<media_id>`（与 `mounts` 挂载同一公式 `db.mount_point`）。

视频库子目录相对媒体库根：本地创建时自动建目录，远程不建；**本地媒体库 0 记录时 `PATCH /api/media-libraries/{id} {"path":...}` 可改根路径**（有记录 422，防止记录与路径脱节）。设置页媒体库面板交互（2026-09 用户反馈二次重构）：媒体行主按钮本地=「检查」、远程=「连接」（挂载+检查一步，失败显示 `last_error` + `suggested_cmd` 可复制）；

「扫描全部」= `POST /api/jobs/scan {media_library_id}`；「⋯」收编辑连接/改路径/挂载/卸载/只读/停用/删除/添加视频库；展开行显示各视频库（名称/类型/子目录/计数）可单独扫描/编辑/删除；建库表单底部是视频库清单（名称/子目录/类型，至少一条）。

注意 `LibrariesPanel.vue` 编辑行必须包在 `<template v-for>` 内（曾因兄弟节点引用循环变量 `l` 导致整块渲染崩溃）。

### 存储抽象（2026-09，`app/storage/`）

`base.py` 定义 `StorageBackend`（list/stat/read/open_read/get_read_url/iter_tree/mkdir/write/rename/delete/health/test_*，错误带 code，只读写拦截）+ `MediaSource`（`input` 本地路径或内网 URL、`local_path` 远程为 None）+ `media_write_path()`（远程返回 ''，NFO/artwork 落盘据此跳过）；

`local.py` POSIX（realpath 边界、原子写、跨盘 move、scandir 树遍历）；`smb.py` 用户态 SMB2/3（smbprotocol，读句柄 `share_access="r"`（池化流式句柄用 `rwd`，绝不阻塞写删），按库缓存实例 + 失败 30s 冷却，凭据仅内存，见下方「远程库性能与缓存」）；

`httpproxy.py` 内网 HTTP Range 服务（仅 127.0.0.1，`MEDIA_PROXY_HOST` 可覆盖，1MB 块 206/416；`/v1/l<id>/` 按库、`/v1/d/<token>/` 诊断用临时注册、TTL 600s）；

`factory.py` 唯一分流点（业务层禁止 `if source=="smb"`）。env `SMB_DRIVER=auto|direct|mount`（默认 auto：挂载点可用走挂载，否则直读）；`GET /api/libraries` 返回 `smb_driver`。

**存储身份（v15，v17 起在媒体库层）**：`media_libraries.storage_identity`（local=realpath / smb=host/share/subpath / nfs=export，启动迁移回填）在 create/update 时查重（`store.media_libraries._check_identity`，重复报 422「已存在指向同一存储的库」，**不做自动合并**；改连接方式在原库上操作）；

`media_libraries.smb_connect_host`（Tailscale IP/内网地址，空=同 smb_host）供 SMB 后端/诊断/挂载指引实际连接，`smb_host` 仅展示；建库/编辑连接表单均可填。诊断：`POST /api/media-libraries/diag/smb`（预检不落库）与 `POST /api/media-libraries/{id}/diag`/`{id}/check`（写 last_status）为 11 阶段（ADDRESS→…→FFPROBE，结构化 `{stage,code,message,suggestions,warnings}`）；

**SMB 直读库的 `check` 走诊断管线**（不触碰挂载），只读账号/只读库的 WRITE 失败记 warning 不整体失败（扫描/播放仍可用）；`mounts` 看门狗在 SMB 直读生效时跳过 SMB 重挂（`_auto_remote_libs`）。

设置页媒体库面板 SMB「测试连接」（阶段芯片 + suggestions）与直读状态文案不变；`/api/libraries/{id}/mount|unmount|diag` 保留为解析到媒体的兼容别名。

### 直读库全链路（2026-09 Batch A+B+D，真实 NAS 验证）

**扫描** `scanner.scan_all` 走 `backend.iter_tree`（SMB 每目录一次 list，不逐文件 stat），`scan_file`/`scan_tv_file`/`attribute_extra_file` 以 `(backend, rel)` 入库；

库离线 → 结果 `library_offline`，跳过且**不 GC/不删行**（`ST_LIBRARY_OFFLINE`，Offline ≠ Deleted）；旧 `scan_one/scan_tv_one/attribute_extra(abs_path)` 保留为本地写路径（上传/复制）薄包装，`rescan_movie` 已改走 backend（直读库可重扫）。

**外挂字幕** `classify.sidecar_subtitles_fs(backend, rel)`（旧 `sidecar_subtitles(abs)` 为本地包装），`stream/subtitles` 外挂枚举/转换输入（`MediaSource`）/`ass|sup` 直发（`routers/blob.media_response`，本地 FileResponse / 远程 Range）全部去 POSIX；

VobSub 烧录第二输入 `plan["sub_sidecar_input"]` 由 `session` 用 backend URL 解析。**播放** `stream.common._version_source` 返回 MediaSource（缺失 410/离线 503），`media.probe(input,size=)`、`playback.build_cmd`（URL 不 abspath）、`routers/blob.py`（movie/tv blob 共用）。

**落盘（D）**：`nfo.render_movie_nfo_bytes` + `nfo_link` 的 `_LocalDirFS`/`_BackendDirFS` 适配器让同一套 NFO 收敛策略经 backend 落盘（`sync_nfos_for(mid, abs_path="", backend=None, rel="")`；远程绝不删非 NFO 文件、所有权哈希保护不变；扫描/刷新/rebuild-nfo 全走 backend），`artwork.write_for_movie(..., backend, rel)` 同理写 poster/fanart（内容相同不重写）。

**整理（D）**：`files/paths._exists/_is_file` 统一本地/远程存在性（存储错误保守为 True，`/files/missing`、`/files/clean`、restore 候选不再把直读库误判为全缺失）；`files/executor._move_one` 远程分支 `_move_one_remote`（改名+同茎字幕/花絮跟随+extras 归位+旧目录清理+NFO 重收敛），`_restore_one` 远程分支同构；

`extras.collect`、`jobs.rebuild-nfo`、`jobs.stats` 均已 backend 化。**文件浏览器（D3/W）**：`/api/fs/list` 直读库经 backend 可浏览（dirs 的 `children` 为 null，显示“目录”），`fs_writable` 按 `read_only` 返回；

`_resolve_dir` 远程经 `backend.is_dir`。**远程写路径（2026-09 Batch W）**：`StorageBackend.open_write` 流式原子写（local=`.part`+`os.replace`；smb=临时文件+`replace`，异常清理并失效元数据缓存）；

fs 写操作（`mkdir/rename/move/delete`、`/api/fs/copy` 后台任务）在直读库全部经 backend（copy 1MB 分块+进度+取消，正片复制后 `scan_file(backend, rel, tmdb_hint)` 登记）；

上传（`/api/movies/{id}/upload`、`/api/uploads`）经 `_stream_upload_backend` 落 NAS（不再写本地 `data/mounts/lib_*` 空目录），入库走 `scan_file/attribute_extra_file`。

边界：SMB 无 POSIX 符号链接语义（远程复制按普通项处理）；远程上传无 `O_EXCL`（先查存在 409，冲突自动副本命名绝不覆盖）；远程目录删除仅允许空目录（与本地一致）。**读路径补齐（2026-09）**：`movies/movie_files`（目录清单 size/mtime/花絮判定全走 backend，直读库不再空）、`_movie_blob_rel`、`batch-delete` 范围与执行（`movies/scope._movie_delete_scope` 远程分支 iter_tree + backend.delete + 远程目录清场）、`tv._episode_payload` exists、`fs._classify`（接受 `entry`+`backend`，直读库 size 不再为 0、通用目录花絮可判）、NFO 离线导入（`metadata/nfo_import.candidates_for_backend` 经 backend 读同名 NFO）。

### 远程库性能与缓存（2026-09 Batch P，NAS 验证目标=少往返/少刷屏）

**日志** `app/log.py` 把 `smbprotocol/smbclient/spnego` 默认压到 WARNING（此前每次线上操作 2 行 INFO 刷屏；env `SMB_LOG_LEVEL=DEBUG/INFO` 可单独放开）。**元数据缓存** `app/storage/cache.py`（TTLCache）+ `smb.py` 接入：`stat/list` 按 `(op, rel)` 缓存（env `SMB_META_TTL` 默认 3s，负缓存 2s，`0` 关闭），`mkdir/write/rename/delete` 精确失效自身+父目录；

`storage.clear_meta_cache(lib|None)` 只清缓存保留连接，供扫描/`/files/missing|clean`/restore/`/jobs/stats`/rescan 等**显式“重新看盘”**入口调用（外部删除必须立即反映）；

`smb.invalidate()` 同时清句柄池。**读句柄池** `_HandlePool`（env `SMB_HANDLE_POOL` 默认 8 / `SMB_HANDLE_TTL` 默认 30s，`0` 关闭）：`open_read`（httpproxy/blob 流式）复用 SMB 句柄省 Create/Close，句柄 `share_access="rwd"`，写/改名/删除 evict 受影响路径，出错句柄丢弃、空闲超时/超容量淘汰。

**扫描热路径**：`scan_file/scan_tv_file(entry=WalkEntry)` 用 `iter_tree` 顺带的 size/mtime（不再逐片 stat）；`scan_all` 用树快照做花絮 GC（`list_all_extras` 每批一次）；

`_BackendDirFS` 一次 list 建 `{name:is_dir}` 快照（不再逐目录项 stat）；`persist.apply_tmdb_detail` 复用 `finish_tmdb_media` 结果（NFO/海报不再双写）；`artwork._backend_put`/`_cleanup_stale_backend_posters` 先比 size 再读。

**播放热路径**：`_decide_payload` 只调一次 `_media_payload`（外挂字幕枚举减半）；`_versions_payload` 探测结果缓存复用（不再重复 stat/探测）。**批量端点**：`files/paths._exists_map` 按父目录分组一次 list 建集合（`/files/missing|clean|restore-candidates`、`/jobs/stats` 由 N 次 stat → D 次 list；`StorageNotFound→False`，离线/其它错误保守 True）。

**factory 修正**：直读不可用且挂载点未就绪时**上抛**（不再落到未挂载空目录，防离线误判为文件缺失）。调优 env 见 `.env.example`；回归 `tests/test_storage_cache.py`、`tests/test_remote_reads.py`、`test_scan_remote.py::test_scan_no_per_file_stat`、`test_remote_organize.py::test_missing_batches_list_per_dir`。

### 电影归档与恢复

`POST /api/files/organize`（统一口，`mode=inplace|relocate`，`library_id` 缺省=默认库；`media_library_id`=整个媒体库，目标根=各视频库根）默认 `dry_run:true`；总是先看预览（旧 `rename`/`relocate` 口已删除，2026-09 B5a）。

命名随库级 `naming_profile`：`kodi`=旧模板 `标题 (年份)[-版本][-规格][-分卷][-版本N].ext`（单 `-` 直连），`plex`=`标题 (年份) {edition-版本} - 规格.ext`（附 `plex_warnings` 兼容性检查），`off`=不改名。

冲突分 `suspect_mismatch`（等人工重匹配，不自动加后缀）与规格变体（最小后缀消解，兜底 `-版本N`）；执行时识别到的版本/编号落库（手工值优先）。**就地归档（2026-09 用户反馈重构）**：影片专属目录（目录名规范，或与文件名茎/标题/原名分词覆盖率 ≥60%，`planner._dir_owned_by_comp`，罗马数字/CJK 标点归一）→ **整目录改名**为 `标题 (年份)`（合集目录保留，如 `周星驰.Stephen Chow/望夫成龙…/` → `周星驰.Stephen Chow/望夫成龙 (1993)/`）；

**合集判定用子树独占性**：目录整棵子树出现 ≥2 个影片标识（`tmdb_id`，无则 `-id`）即视为合集，`_target_for` 上跳到此为止、不得整目录改名（`comp["blocked_dirs"]`）——防 `惊声尖笑.Scary.Movie.1-5.2000-2013/Scary.Movie.2000` 因年份/音轨 `DTS-HD.MA.5.1` 数字撞合集名越界到库根（同类：蜘蛛侠/黑衣人/魔戒六部曲等），也防影片目录里混放他片子目录被连坐改名；

目录内正片与同茎字幕/花絮跟随改名，`Sample/Behind The Scenes/封面截图`、`cover.jpg/poster.jpg` 等原名原位置跟随（不做花絮归位）；历史套娃空目录自动清理。非专属目录（合集平铺/暂存/根散文件）→ 保持父目录套一层 `父/标题 (年份)/`。

规划输出目录计划 `{kind:"dir", from, to, movie_ids, files[]}`（一组文件只出一个计划，`only` 子集执行整目录带走、同片多版本一起），执行 `executor._move_dir`：目录改名 → `store.repath_movies_prefix/repath_extras_prefix` 前缀改库（`original_file_path` 不动）→ 目录内正片改名 + NFO 收敛；

目标目录已存在报 `conflict_disk_exists` 不自动合并。`mode=relocate` 不再有「待整理」源头：整库（或 `only` 子集/媒体库作用域）收敛到**视频库根**，平铺为 `标题 (年份)/标题 (年份).ext`（plex 档带 `{edition-…}`）；

旧参数 `from_prefix`/`to_dir` 仅单库兼容（`to_dir` 空=库根，媒体库作用域传 `to_dir` 422）。`GET /api/movies/{id}/organize-hint` 仅当顶层目录是暂存词表（`待整理/未整理/下载/downloads/incoming/temp/tmp`）时推荐 relocate，其余一律就地（NAS 库根即 Movies）。

UI `OrganizePanel.vue`：一次媒体级预览，plans/conflicts 按视频库分表（每表全选/执行）；逐行复选框（默认全选）+ 动作下拉（跟随上方/强制搬到顶层/保持不动），行内展示目录计划「目录 · N 片」徽标与影片改名明细（`frontend/src/organizePlans.js` 纯函数：徽标/明细/basename，node 单测）。

逐行动作**即时投影预览**（`projectPlan`：强制顶层投影到视频库根、保持不动灰显+标签、全局已顶层不二次投影；仅显示，不改执行参数），执行按钮只计非「保持不动」的勾选项，执行后动作重置防二次投影。

### 花絮/样片

`scanner.is_sidecar` 判定 → `extras` 表归属（标题/原标题+年份±1，种类词前后缀剥掉再试一轮 fallback；历史 orphan 在正片后入库后重扫自动补归属，已有归属/手工认领的不碰；样片永不归属）；**目录名 token 化**（2026-09）：按 `,，、;；&+` 切分 + `._-`→空格归一后匹配，`Sample,Screens`/`Behind.The.Scenes`/`Extras & Trailers` 均命中；

`screens/screenshots/截图/封面截图/样片` → kind `sample`（不建行、不入 extras、不搬迁）；`_SAMPLE_RE` 允许空白分隔（`... 3Li Sample.mkv`）；**中文花絮关键词按片段/词尾边界匹配**（2026-09-23：`幕后黑手` 不再误判花絮、`散落花絮`/`功夫-花絮`/`预告-功夫` 仍命中，修半泽 S02E09 被重扫登记成 extras 的 bug）；

`is_extra/is_sidecar/is_feature_video` 支持可选 `library_id`/`backend`（scanner 传 backend、fs/movies/planner/executor 传 library_id），`scenes/other/shorts` 通用目录判定在 NAS 直读/非默认库不再失效；

整理时跟随进 `extras/` 子目录，散落的已归属花絮用 `POST /api/extras/collect` 归位；剧集不入库（`scan_one` 直接 skip）；历史误入库样片行用 `POST /api/jobs/clean-samples`（dry_run 预览，支持 `library_id`/`media_library_id`，只删 DB 行）清理，维护面板有按钮；

历史一次性清理口 `clean-sidecars`/`clean-episodes` 已删（2026-09 用户确认，旧扫描器脏行清完即无用）；`unmatched` 另有 `suspect_title_high/info`（英文标题两档）与 `orphan_extras`。

### `PATCH /api/movies/{id}` allowlist only

`title, overview_override, douban_rating, custom_rating, tags, edition, spec, watched` (ratings 0–10, tags must be list; `original_file_path` is server-side audit only, never patchable; `watched_at` is server-derived, never patchable).

Display: `overview_override` wins via `overview_display`. Local-only writes go through `store.update_movie_local` so TMDB mirror columns are never polluted. **待确认确认**（2026-09 用户反馈）：`POST /api/movies/{id}/confirm-match`（单条，仅清 `needs_review`，不重刮不触网；未匹配 422）与 `POST /api/movies/batch ops.confirm_review:true`（同 tmdb 全版本一起清）；

详情页「待确认」徽章内联「确认/重新匹配」按钮，设置页待确认列表有行内「确认」与「全部确认」。

### 库列表访问方式（2026-09 用户反馈）

`GET /api/libraries`（视频库）每项带 `driver`（local|smb|mount，只做展示判定不建连）；媒体库面板「地址」列对远程媒体库显示 `\\host\share\subpath`/NFS export + 「直读/挂载」徽章，tooltip 注明 `path` 字段仅是宿主挂载回退路径（直读模式不使用），避免看起来像本地路径。

### 原始路径审计

`movies.original_file_path` 只在 `upsert_movie_by_path` 首次入库（空值）时写入，搬迁仅改 `file_path` 永不碰它；存量行由 `init_db` 用当前路径种子（=最早已知）。错配搬错用 `GET /api/files/restore-candidates` 预览 + `POST /api/files/restore-original`（默认 `dry_run:true`，目标被占/源缺失跳过不上报覆盖）搬回；

详情页只展示 + 跳转 `settings?sec=sec-restore&ids=`。

### Filtering

`GET /api/movies` + `GET /api/search` accept repeatable `genre/region/country/year/decade/tag/collection` (comma or repeated keys; declare as `list[str]|None = Query`) plus single `min_rating` + `rating_source` (`tmdb|douban|custom`, whitelisted via `store.RATING_SOURCES`, defaults tmdb) plus single `watched` (`1|0`).

Semantics: OR within a facet, AND across facets — except `tag` multi-select is AND and `min_rating` is a single `>=` threshold. `country` matches any `origin_countries` entry (co-productions); `decade=2020` means 2020–2029. `GET /api/facets` returns live counts (only nonzero) incl. cumulative rating steps + `watched` + `collections`. 排序：`sort=added|updated|year|title|rating` + `order=asc|desc`（API 不传参数时默认 `updated/desc` 保持旧行为，网页显式传入默认 `added/desc`；`store.search.SORT_KEYS` 白名单 + `_order_clause`，grouped 用 `MAX(col)` + `id DESC` 兜底防翻页抖动；rating 跟随 `rating_source`）。

q 非空时搜索走相关度、忽略 sort。

### 入库时间 + 继续观看（2026-09，v20/v0.12.0）

`movies.added_at` 首次入库写入（`upsert_movie_by_path`），**任何重扫/刷新/backfill/标已看/搬迁/改名都不碰**（`update_movie_meta` allowed 集不含它；迁移 `_m20` 用 `updated_at` 近似回填存量）；

海报墙默认 `sort=added`（`frontend/src/wallSort.js`，localStorage `jzmedia.wallSort` + URL `?sort=&order=`，`BrowseResultsHeader` 的排序下拉与方向按钮，默认加入时间倒序、标题默认升序）。

`GET /api/movies/recent-played?limit=&include_finished=&library=&media_library=` 走 `store.list_recent_played`（join `playback_progress`，`position>=15s`；默认 `watched=0` 且排除已完播项（已播 ≥95%，或已播 ≥80% 且剩余 ≤300s；`app/playback_completion.py`）；海报粒度去重留最近播放版本，返回 `progress:{version_id,position,duration,percent,remaining_sec,last_played_at}`）→ 海报墙筛选栏下方「继续观看」横向行（`ContinueWatchingRow.vue`：底部红色进度条 + 剩余时间 + hover ▶ 本页 PlayerModal 续播，「全部最近播放」切换 localStorage `jzmedia.recentAll`；有搜索/筛选/多选时隐藏）。

**当前布局**：继续观看为横向紧凑卡片（桌面 290px/手机 246px，小海报与标题/剩余时间并排）及左右滚动按钮；电影墙使用 `BrowseResultsHeader` 统一结果数量、排序下拉与方向。2026-09 的红色渐变面板/160px 相框卡片和排序 chips 是历史版本样式，已由 2026-10 的统一浏览布局替代。

详情页「影片信息」显示入库日期、卡片 hover 提示入库日期。**悬浮播放键（2026-09 用户需求）**：继续观看与全部影片海报 hover 显形居中 ▶（全局 `.poster-play` 单源，按钮自身 hover 放大变红，触屏常显 44px）；点击直接播放——继续观看按断点续播（`progress.version_id`），墙单版本零等待播 `m.id`、多版本先 `POST /api/stream/versions`（带 caps）取 `best_version_id` 失败回落；

点卡片其余区域仍进详情（继续观看/海报墙一致），海报墙左上角仍是选择、多选态隐藏播放键。回归 `tests/test_added_at.py`、`tests/test_recent_played.py`、`frontend/tests/wallSort.test.js`、`recentPlayed.test.js`、`format.test.js`、`posterPlay.test.js`。

### Collections/members are poster-granularity

`collection_members(movie_tmdb_id|movie_id)` matching the `COALESCE(tmdb_id,-id)` grouping; `POST /api/movies/batch` expands representative ids via `store.expand_ids_to_versions`.

Member removal from frontend uses `POST /{id}/members/remove` (DELETE-with-body alias exists but some clients drop the body). **合集跟随媒体库（v18 用户反馈）**：`collections` 唯一键 = `(media_library_id, name)`（成员可跨同一媒体库内的视频库，同名合集可分媒体库共存）；

`media_library_id` 缺省解析到默认视频库所属媒体库；详情页 chips（`_collections_for_film`）、封面（`_collection_covers`）、相似「同合集」加分（`similar._manual_collections`）均限同媒体库；

`POST /api/collections` body 用 `media_library_id`（旧 `library_id` 自动映射到其媒体库；不存在 422）；已收录查重、系列推荐/topups 按媒体库。加入成员返回 `{added,total,skipped}`（跨媒体库 id 计入 skipped，前端提示）。

前端推荐区有「全部接受」批量建当前媒体库系列合集。

### 系列合集建议

Series suggest (`GET /api/collections/suggest`) is local-only clustering on `tmdb_cache.collection_tmdb_id` (≥2 films in library); never auto-creates — writes happen only on user accept via `from-tmdb-series`, accepted series filtered from future suggestions.

New films in collected series surface under `topups` (one-click fill via `POST /{id}/members/top-up`, server recomputes diff); scan/refresh/backfill never touch members.

Dismissals live in browser localStorage (`jzmedia.dismissedSeries`), not server-side. Backfill (`POST /suggest/backfill`) is a background job (immediate `{job_id,total}`, poll `./status`, `POST ./cancel`) on the light `refresh_tmdb_id_fast` path (no poster/avatar/NFO); duplicate starts resume the running job.

Every successful fetch stamps `tmdb_cache.collection_checked_at` (confirmed standalones never rechecked unless `force:true`).

### 库中类似（详情页 Plex 式推荐，2026-09）

`GET /api/movies/{id}/similar?limit=12`（≤30），纯本地不调网（`app/store/similar.py`）——同系列 +100、同手工合集 +30、同导演 +36/人、同主演按番位 6–26/人、类型 IDF 加权 Jaccard ×45、标签 +8/个、产地/语言/年份小幅加分；

门槛=至少命中一个内容信号（系列/合集/导演/主演/类型/标签）且总分 ≥18；海报粒度排除本片全部版本，候选分信号限量收集防大库爆量。前端 Detail.vue 横向海报行（箭头滚动/评分角标/推荐理由），请求失败静默不挡详情页。

### Poster-wall multiselect is Plex-style with no manual toggle

hover circle per poster (`.sel-circle`, always visible on touch via `@media (hover:none)`), first check auto-enters, empty/Esc auto-exits; actions live in a fixed bottom pill (`.floatbar`), visually distinct from filter chips.

### 海报选择器（2026-09，方案 A）

详情页海报大图弹窗「换海报」→ `GET /api/movies/{id}/posters`（TMDB `/movie/{id}/images`，按分辨率+评分排序，`current` 标记当前）；缩略图走 `GET /api/movies/{id}/poster-thumb?path=`（后端代理下载 w185 缓存 `data/posters/cand/<tmdb>_<sha1[:12]>.jpg`，浏览器无需可达 TMDB 图片域名；path 白名单 `^/[A-Za-z0-9]{6,}\.(jpg|jpeg|png)$`）；

选定 `POST /api/movies/{id}/poster {file_path}`（须在候选列表内）→ 下载 w500+original 覆盖 `data/posters/movies/<tmdb>.jpg` / `data/posters/orig/<tmdb>.jpg`（先删旧 orig）→ `store.set_poster_override` 写 `tmdb_cache.poster_override`（v19 列；`upsert_tmdb_cache` 见 override 即用它覆盖默认海报，**刷新/重刮不回退**）→ 同 tmdb 全版本 `movies.poster_path` 同步 → best-effort 重写媒体目录 `poster.jpg`（本地/远程 backend，`backdrops=false`）。

原图接口 `/movies/{id}/poster-orig` 优先读取 `posters/orig/<tmdb>.jpg`，旧根目录 `_orig.jpg` 仅作兼容回退；低分海报（如《黑衣人：全球追缉》默认海报 original 仅 540×755）改选 2000×3000 候选即可。

**缓存**：海报原地覆盖 URL 不变，`main.py::_security_headers` 对 `^/posters/(?:movies|orig)/\d+\.(?:jpg|jpeg|png)$` 与 `poster-orig` 响应加 `Cache-Control: no-cache`（强制重验证；`cand/`/头像不加）；

前端 `posterUrl(p, v)` 支持 `?v=`，详情页换海报后 `posterVer++` 使头部/弹窗即时刷新。回归 `tests/test_poster_picker.py`、`frontend/tests/posterUrl.test.js`。

### Frontend ratings

shared `src/ratings.js` (`hasScore` treats null/0 as missing — never render `-` placeholders) + `src/components/ScoreBadge.vue` (poster overlay; `♥` for custom, `★` otherwise).

Display rule: hide any source with no score. Cast `character_name` is contributor free-text, not localized — only show it when `original_language` starts with `en` (EN descriptions/romaji for CJK films read as noise).

### Origin data

`movies` has `origin_country` (primary ISO) + `origin_countries` (JSON) + `original_language` + `region` (derived via `regions.resolve`: production_countries[0], fallback `original_language`; 华语=CN/HK/TW/MO).

Never hardcode region lists elsewhere — edit `app/regions.py` only. Tags normalized server-side (`regions.normalize_tags`: trim/dedup/cap 20 chars × 20).

### Persons/avatars

`persons.avatar` holds local rel path (or `'-'` = confirmed no TMDB photo, so backfill won't retry it). `upsert_person` only overwrites avatar when arg is not None.

Person writes go through `scanner.sync_persons` (directors + top-10 cast, skips existing avatar files); `link_person` alone does not touch avatars or FTS.

### Person page

`GET /api/persons/{tmdb_id}` pure-local instant response (`store.get_person`: acting/directing split, dedup by tmdb, year desc; never blocks on TMDB)。

作品列表支持库范围（v18 读聚合）：`?media_library=<id>`/`?library=<视频库id[,id]>`，缺省全库；`Person.vue` 随顶栏媒体库切换重载（`mediaParam`/`onLibChange`），`POST /refresh` 同样接受该范围。

Bio progressively filled: frontend fires `POST /api/persons/{tmdb_id}/refresh` in background only when `biography` empty AND `bio_fetched_at==0`, then renders; cached via `update_person_bio` (zh empty → retry en-US; empty result still stamps `bio_fetched_at` as negative cache — no retry storm, no bulk bio backfill).

Frontend route `/p/:tmdb_id`; person clicks use `tmdb_id`, never name search (avoids 同名混淆).

### Backfill

`scan_one` skips cached rows, so new meta fields need `POST /api/jobs/backfill-meta {"limit":N,"force":bool}` (offline from cache; no poster/NFO rewrite; preserves manual `title` via `old_title=None`; always syncs persons/avatars for processed rows, skips completed ones unless `force`).

NFO also writes `<country>` per origin.

### 刮削失败可见性（2026-09 用户需求）

`scan_one` 解析后**先建行再刮削**，TMDB 异常 → `scan_failed`（行保留、不写 scan_state、下次扫描自动重试）；`POST /api/movies/{id}/rescan`（force）手动重试；`scan_one(force=, tmdb_hint=)`（hint 用于复制直绑）。

未匹配/失败片在**海报墙**以占位卡+「未匹配」角标可见，详情页对未匹配影片显示“匹配资料”按钮，点击后展开匹配面板；上传对话框对待处理行给「去详情匹配/重试刮削」。

### 标题来源（2026-09 修复）

`movies.title_auto` 1=扫描按文件名自动写入（可被 TMDB 标题覆盖），0=TMDB/手工（刷新/重扫不覆盖）。`copy_tmdb_to_movie` 据此决定是否跟随标题；迁移 v11 会把「标题==按路径反解」的存量行标 1 并用 tmdb_cache 离线愈合已匹配行。

### 离线/无 token 外源刮削（v0.19.0 Phase 1+2，数据模型 v27）

`external_meta(source, source_id, kind, title, …, tmdb_id/imdb_id/poster_url/payload_json)`（`store/external.py`，身份非 TMDB，写完同步 `match_index`、启动自愈播种）；

`match_index.alt_titles`（别名召回，`metadata/local.py::score_row` 别名精确 +36、需配年份过自动绑定阈）；`tmdb_cache.seasons_json`（TV 离线季集回放，`set/get_tmdb_cache_seasons`，`apply_tv_detail` 在线写入、`scrape_show` 离线读回）。

应用层 `metadata/external.py`：`apply_external_movie/show`（只补空字段、外源标题 `title_auto=1` 可被 TMDB 覆盖、`tmdb_id` 默认不写防“有 id 就跳过重扫”卡升级、远程图 `tmdb.download_url`/本地图经 backend 落 `posters/ext/`、NFO 收敛）。

NFO 全量回放：`metadata/nfo_import.py` 解析 `<movie>/<tvshow>/<episodedetails>` 完整字段（标题/简介/评分/类型/产地名→ISO/导演演员/图片）+ `scan_file` 离线首见片 `status=ok_external`/`match_source=nfo`（增量短路 `skipped_external`；`store/search.py::resync_fts` 无 person 关联时不覆盖 `person_names` 防清空外源人名）。

扫描/TV 刮削在 TMDB 不可用时按库链试外源：`chain.search(..., exclude=('local','tmdb'))` + `metadata/auto.py::pick_auto` 门控（本地/NFO score≥45，外源相似≥0.7+年份±1 自动、否则 `needs_review`）+ `chain.detail_for(cand)` 拉完整详情。

Provider：Wikidata 深化（P577/P57/P161/P136/P495→ISO/P18 Commons 海报）、新 `metadata/tvmaze.py`（无 key 剧集+分集 embed）、新 `metadata/bangumi.py`（无 key 中文/动漫，`BANGUMI_UA` 建议带联系方式；三家均进程内限速）。

API：`POST /api/movies/{id}/bind-external`、`POST /api/tv/shows/{id}/bind-external`（`{source, source_id}`，有 tmdb_id 且已缓存优先复用缓存）；`/api/tv/search` 支持降级链；

设置页“匹配规则”保存库级来源链，“离线资料”提供 IMDb 导入；`MovieEditPanel`/`TvShow` 外源候选来源徽章与「绑定外源」。回归 `tests/test_external_meta.py`、`scripts/smoke_metadata_offline.py`。

### 匹配质量（2026-09 用户反馈修复，`tests/test_match_quality.py`）

**本地优先**——`scan_file` 在 TMDB 搜索前用 `metadata.local.parse_path_variants`（文件名 guessit 标题 + 文件名/父目录中文段）+ `parse_path_year`（父目录年份优先）+ `library_index`（跨库已匹配 `(归一标题,年份)→tmdb_id`）零网络绑定，命中即 `match_source=library`（告白.Confessions.2010 ↔ 本地库 告白/54186，纠正此前 TMDB 错配 471040）；

**force 重扫同样先走本地**（纠正错配的路径，换绑走手动匹配）。**标题相似门**：`match.pick_match(results, year, query)` 三档——相似度≥0.7 且年份±1 直接采信；年份±1 但不像 / 像但年份不符 → 采信并 `needs_review=1`；

都不满足 → 不绑定（`no_match`，不再静默采信 `results[0]`，修复「龙珠Z剧场版07→世界大战」）；短查询命中一律待确认。**换绑标题纠正**：本地优先/手动匹配换绑时 `apply_cached_to_movie(force_title=True)` 用新缓存标题覆盖（含旧 tmdb 错配写入的标题），避免「id 改了标题没改」。

**原盘跳过**：`_SKIP_DIR_NAMES` 含 `BDMV/VIDEO_TS/AUDIO_TS/CERTIFICATE`（00000.m2ts 碎片不再入库）。**Provider 链冷却（2026-09）**：搜索链（`metadata/chain.py`，默认 local→tmdb→wikidata，库级 `metadata_providers` 可覆盖）任一 provider 连续失败 3 次 → 冷却 10 分钟，冷却期内链跳过它（状态落 `app_settings.metadata_provider_state`，重启仍生效；成功清零、空结果不算失败）；

`GET /api/metadata/providers` 快照 + `POST .../reset`（设置页“在线资料服务”展示来源状态/最近错误/重置冷却），`metadata/state.py`，回归 `tests/test_metadata_chain.py`。

### 多版本落盘策略（2026-09，`library_paths.per_version_meta` 单一来源）

默认只写 `movie.nfo` + `poster.jpg/fanart.jpg`（kodi 等把多版本合并为一个条目）；仅 `naming_profile=plex` **且版本间 edition 不同**（Plex 拆独立条目）才补 `<stem>.nfo` / `<stem>-poster.jpg`；

混放不同影片的共享目录仍只写当前同名。旧 per-version NFO 进 `deleted` 清理，旧 `<stem>-poster.jpg` 内容=我们写的海报时删除（`cleaned`）。env `PER_VERSION_META=1` 回退旧行为。

### 标题本地化（2026-09 用户反馈）

TMDB 的 zh-CN 记录可能缺本地化 `title`（回退英文原名）但中文名在 `alternative_titles`（实证 Top Gun: Maverick 361743 → CN/TW/SG 别名均为「壮志凌云2：独行侠」）。`tmdb.movie_detail` 现附加 `alternative_titles`；

`scanner.match.pick_display_title(detail, language)`：主标题已含 CJK 不动，否则按配置地区→CN→TW→HK→SG 取含 CJK 的别名（非 zh 语言不动、空/同名/无 CJK 跳过）；`meta_from_detail` 的 `original_title` 保留 TMDB 原名（空则回退主标题）。

存量愈合走「扫描与整理 → 电影视频库 → 资料维护 → 更新本库影片资料」（每次最多 5000 部，`old_title` 机制保证手工标题不被覆盖）或详情页「更多操作 → 更新资料」；已实测 Top Gun 两版本与 NAS `movie.nfo` 同步为中文。

### 元数据落盘维护（2026-09）

`POST /api/jobs/rebuild-meta {library_id?, media_library_id?, ids?, dry_run?, artwork?, backdrops?}`（jobkit 后台，离线不触网）按现有匹配从 tmdb_cache 重写 NFO+海报（远程经 backend 写 NAS，`backdrops=false` 跳过 fanart 下载）；

`ids`（≤500，优先于库筛选）用于单部修复——详情页「更多操作 → 修复资料文件」选择 NFO/海报后，以 `{ids:[id], nfo, artwork, backdrops:false}` 执行并轮询，已有全库任务在跑时按 ids 请求返回 409。

`POST /api/jobs/clean-bdmv`（删 BDMV/VIDEO_TS 碎片行，只删记录）；`POST /api/jobs/clean-mount-artifacts`（仅未真正挂载的 `data/mounts/lib_*` 下清 NFO/图片残留）。

手动匹配 `POST /api/movies/{id}/match` 现在经 `write_target` 写 NAS（此前误写挂载点），TMDB 401/断网时回落 `tmdb_cache` 离线绑定（`{offline:true}`，含强制标题）。`POST /api/jobs/scan` API 支持 `force:true`；

当前设置页常规扫描入口不提供强制重扫按钮。**换绑落盘加固（2026-09 用户反馈「修正匹配后 NAS 海报没更新」）**：`POST /api/movies/{id}/refresh` 默认 `force:true`（显式刷新即使 tmdb_cache 无变化也重写该片全版本 NFO/海报，`{force:false}` 退回旧行为；响应带 `forced`，MovieEditPanel 提示「已重写」）；

`finish_tmdb_media`/`finish_refresh_media` 对 `artwork.write_for_movie` 失败重试一次并 `logger.warning` 带 reason（此前只返回 `{ok:false}` 静默丢弃）；同片媒体落盘经 `persist._media_lock(mid)` 串行（双击匹配/匹配+刷新并发会互相覆盖出 NFO 缺演员/海报回退旧图）；

`smb.write` 临时名 `.tmp-{pid}-{monotonic_ns}`（此前固定 `.tmp{pid}` 同路径并发撞名）。回归 `tests/test_media_write_hardening.py`、`test_meta_maintenance.py::test_rebuild_meta_ids_only_target`、`test_refresh_force_rewrites_media`、`test_remote_writes.py::test_smb_write_tmp_name_unique`。

### `scanner.scan_one`

cached `tmdb_id` → `skipped_cached`; `guessit type==episode` → `skipped_episode_v1` (V1 movies only); sidecars route to `extras` via `is_sidecar`, never to movies; year match tolerance ±1; fallback-query hits set `needs_review=1`.

Manual fix flow: `GET /api/tmdb/search` → `POST /api/movies/{id}/match {"tmdb_id":…}`.

## Deploy

- `docker-compose.override.yml` is WSL-only (dev user + bind mounts + `--reload`). Delete / exclude it on NAS; configure the actual media/data paths and UID/GID in Compose directly, or keep its variable syntax and use an optional `.env`（主 compose 用 `user: "${UID:-0}:${GID:-0}"` 运行容器，缺省 root；NAS 需保证数据目录属主为该 UID，见 docs/getting-started/deployment.md）。

- WSL Docker Desktop proxy breakage is documented in docs/getting-started/troubleshooting.md (use `crane pull … && docker load`, or `BUILD_HTTP_PROXY=http://nas:7890`).

## Versioning

### Git 与发布历史

Git (`main` branch, `origin`: `git@github.com:nojobnopay/jzmedia.git`): commit per feature, annotated tag per release. Project license: BSD-3-Clause (root `LICENSE`).

- `v0.19.0` = 离线/无 token 外源刮削 Phase 1+2（v27）：`external_meta` + `match_index.alt_titles` + `tmdb_cache.seasons_json`；NFO 全量离线回放（movie/tvshow/分集 + 本地图片）；imdb_id→tmdb_id 离线桥；`metadata/auto.py` 门控 + 扫描/TV 刮削外源链（Wikidata 深化 / TVmaze / Bangumi，无 key，限速）；`POST /api/movies/{id}/bind-external`、`POST /api/tv/shows/{id}/bind-external`、`/api/tv/search` 降级链；设置页库级链勾选 + IMDb 导入按钮 + 候选来源徽章；回归 `tests/test_external_meta.py`）；

- `v0.18.0` = 设置页库工具视频库级重构：左侧快速访问不再列媒体库按钮（只留「媒体库工具」入口）+ `LibraryToolsPanel` 顶层改「每个视频库一个 Tab」（`frontend/src/libraryToolsTabs.js` 纯函数）+ 电影/剧集工具完全分开（新 `MovieLibraryTools.vue`/`TvLibraryTools.vue`/`TvMaintenancePanel.vue`，删除 `LibraryPipelinePanel.vue`；`LibraryMaintenancePanel` 仅电影）+ 单步聚焦手风琴（自动展开待办步骤）+ 「更多工具」折叠区（高级维护/恢复/剧集维护/文件浏览）+ 剧集 Tab ② 待匹配列表（`GET /api/tv/shows?pending=1` + `episode_review_count`，行内确认/去匹配）+ 全部子面板改视频库作用域（`library_id`）+ `FsBrowser.initialLibId` + 剧集海报墙「未匹配」角标；

- `v0.17.0` = TV 目录规范化工具：`tv_organize`（剧根改名/季目录规范化/包装层拍平/补 Season/特典归位/花絮目录上移/正片统一命名）+ 冲突/做种守卫 + `/api/jobs/tv-organize` + 设置页整理面板 + **整理审计/撤销**（v24 `organize_moves`、`/api/jobs/tv-organize-restore`、改造为反向时序/前缀重写的 `plan_restore/execute_restore`、legacy 回填）；

- `v0.16.0` = TV 花絮/剧场版登记与未匹配集：`extras.show_id`（v23）+ 扫描分流 + `kind='extra'` 播放链路 + 手动指定 TMDB 集 + 详情页花絮/剧场版区；NAS 实测 271 个登记/14 集待绑定）；

- `v0.15.0` = TV 落盘与维护：TV NFO 渲染 + `tv_nfo_link`（tvshow/季/集 NFO、所有权哈希 v22、`.plexmatch` 可选）+ `artwork.write_for_show`（海报/季海报/可选集剧照）+ `/api/jobs/rebuild-tv-nfo` + `PATCH /api/tv/shows/{id}` 手工标题保护；NAS 实测 60 剧/3,877 集 NFO 落盘）；

- `v0.14.0` = TV 刮削与前端：`tv_match`（相似门+热度消歧+别名兜底）/`tv_persist`（TMDB detail+季详情→剧季集镜像、集匹配三档回退、海报并发下载、集剧照懒加载、离线重放）、`/api/jobs/tv-scrape`、TV API（search/match/refresh/confirm/watched/next/still/recent-played）、`Tv.vue`/`TvShow.vue`/继续观看/连播、stream 版本与 prewarm 支持 episode；NAS 实测 60/60 剧匹配、99.6% 集有元数据）；

- `v0.13.0` = TV 扫描器 v2（`app/scanner/tv_parse.py` 规则阶梯 + 子剧拆分 + 绝对集号 + 多集区间 + 无编号特典顺序号 + `.rmvb` 等容器补齐 + 增量扫描/失效 GC + `scripts/tv_parse_report.py`）与数据模型 v21（`tv_shows/tv_seasons/tv_episodes` 元数据列 + `tmdb_cache` 复合主键 `(media_type, tmdb_id)`）；NAS 实测 4,150 视频 → 3,877 集 / 0 未知 / 273 花絮 / 60 部剧）；

- `v0.12.0` = 入库时间 `movies.added_at`（v20 迁移，不可变）+ 海报墙默认按入库排序（`sort/order` 参数与排序 chips）+ 「继续观看」栏（`/api/movies/recent-played`，进度条/剩余时间/本页续播/全部最近播放切换；红主题面板/轮播箭头/渐隐与「全部影片」墙标题双分区）+ 换绑/刷新落盘加固（refresh 默认 force、rebuild-meta ids + 详情页「重写元数据」、artwork 失败重试/告警、同片媒体串行锁、SMB 写临时名唯一化）；

- `v0.11.0` = 远程库性能缓存 + 远程写/上传 + 海报选择器（v0.10.0 后首提交）+ 前端收尾（路由懒加载/404、共享样式单源、人物页作品按媒体库过滤、provider 失败冷却与设置页链路状态）+ 字幕子系统抽 `useSubtitles` composable（含解构契约回归网）+ 播放链路修复（变体 m3u8 内容快照防 ASGI Content-Length、NVENC/QSV 强制 IDR 与 High profile、CODECS 按输出宽高算 level、全屏设置入口条件实例、播放状态同步、`bindVideo` 解构错位）；

- `v0.10.0` = 媒体库工具媒体库级重构：工具页顶层=媒体库/视频库分表+筛选，扫描/待匹配/归档/维护/恢复/文件浏览全部支持 `media_library_id`（列表分表+每表独立操作），归档去掉「待整理」源头（就地保留自建父目录 / 搬到顶层=视频库根平铺；上传直接落视频库根、详情页一键归档同语义），fs 媒体根只读浏览 + 进入视频库切换上下文；样片/花絮目录识别修复（复合目录名 token 化 + 空白 Sample + screens/截图 → sample 不登记 + backend 感知通用目录 + `clean-samples` 清理）；

- `v0.9.0` = 媒体库/视频库两层重构（v17 迁移：存储连接上移 media_libraries，libraries 变带类型子库；本地按数据自动拆分+路径改写，远程 subpath 下沉）+ `/api/media-libraries` 与两层设置页 + 扫描按媒体库 + v18 切换器媒体库级/电影剧集按媒体库聚合/合集改媒体库级；

- `v0.8.0` = 评审修复总收口：P1×11 全修 + P2 主体清理（B5a/B6/B7/B8）+ B9 结构拆分（store/scanner/files/movies/stream/playback/前端视图）+ 迁移框架/扫描任务化/增量扫描 + 未匹配可见性与归档引导 + 文件浏览 Windows 化 + B10 工程化；

- `v0.7.0` = 在线播放 P1–P5：客户端能力四档决策 + fMP4 多音轨 + ASS/PGS 客户端字幕与外挂 + 转码后端探测/软编兜底 + HDR/DV 矩阵 + 预转码与 0.7.0 文档；

- `v0.6.0` = settings rework + missing-cleanup + stats;

- `v0.5.0` = TMDB mirror cache + refresh + progressive person page;

- `v0.4.0` = filters/ratings/avatar-wall/person page

根 `version.properties` 是服务端、网页、帮助站及 Android TV 的唯一发行版本源。使用 `python3 scripts/release.py version X.Y.Z --android-code N` 同步版本，再提交全部发行源码；`check` 校验版本一致，`build` 从干净同一 Git 提交构建两端并生成追溯清单。推送与根版本一致的 `vX.Y.Z` 标签触发 `.github/workflows/release.yml`；云端不自动升版、不创建或移动 Git 标签。每次交付内容变化同时提高发行版本和 Android `versionCode`，本地重复开发验证使用 `build --validate-only`。完整规则见 `docs/developer/releasing.md`。

依赖版本锁定在 `requirements.txt`；宿主直跑用 `requirements-dev.txt`（含 static-ffmpeg 兜底，评审 R01-Q5）；compose 有 `init: true` + `/api/health` healthcheck（R01-Q3）。

### Images

Compose 的 `image: jzmedia:${APP_VERSION:-latest}` 保留本地部署构建能力。统一发行使用 `python3 scripts/release.py build`；两端检查和构建全部通过后，才更新 `jzmedia:vX.Y.Z`、`jzmedia:latest` 与 `output/releases/vX.Y.Z/`。默认生成 Debug 试装 APK；正式 APK 必须配置并验证正式签名，未签名包不能交付。

`manifest.json` 记录完整 Git 提交、统一版本、镜像 ID、APK SHA-256 与签名类别，OCI labels 记录版本和提交；本机发行成功创建或核对同提交的 annotated Git tag `vX.Y.Z`，同名异提交拒绝发行。本地 `build` 不导出镜像、推送远端或安装设备；`--validate-only` 不更新发行目录或 Git／镜像版本标签。

云端复用完整双端构建，以 `scripts/github_signing.py` 恢复并核对固定 Debug 签名；`scripts/github_release.py` 提供 `preflight/package/publish`。八个发行附件保存为保留 14 天的 Actions bundle，独立发布 job 复核后先保存完整草稿附件，再推送 GHCR 固定标签、匿名核验、公开 Release，最后更新 `latest`。发布失败重跑 failed jobs 复用原 bundle，不重建或覆盖同版本产物；已存在草稿时拒绝重新构建。产物、签名密钥与密码不提交。
