# R12 HLS 会话/预转码/断点续播 — status: done

## Scope
- `app/routers/stream.py` 会话/进度/预转码段：`media`/`decide`/`versions`/`probe-missing`/`sessions`/`master`/`seg`/`{name}`/`debug`/`ping`/`close`/`progress`/`prewarm`/`backends`/`fonts`（字体与字幕接口的正确性归 R13，本单元只确认契约）
- 前端消费：`Detail.vue` 的 prewarm/进度轮询、`PlayerModal.vue` 的会话调用（细节归 R14）
- 交叉：`playback.plan/build_cmd`（R11）、`transcode`（R11）、`main.shutdown_sessions`（R01）

## 目标（用户三问）
1. 功能设计是否合理？更好的方案？
2. 代码是否符合要求？废弃代码/漏洞（带 file:line）？
3. 质量差距（P0/P1/P2 分级，带证据）？

## 进度 Log（倒序追加）
- 2026-09-16 done：stream.py 会话段逐行 + 生命周期/并发/清理路径人工推演；字幕/字体接口仅确认与 R11 索引契约一致。
- 2026-09-16 doing：开始。

## 结论

### 1. 功能设计

**整体判断：会话模型（增长型播放列表 + 前 N 分片即回 + TTL/心跳/清道夫 + 同 plan 复用 + 完工静态 VOD + HW 失败软编重试）是成熟 Plex 式设计**，`complete.json + plan.json` 双标记防“被杀会话冒充静态成品”（`stream.py:840-861`）尤其严谨。`_plan_marker` 去掉档位字符串/非烧录字幕/所选音轨，使预转码与在线播同 plan 命中静态成品（`447-469`），设计正确。问题：

- **D1（P1，并发正确性）预转码与在线播/预转码之间会互踩同一会话目录。** `_prewarm_worker` 不在 `_sessions` 注册（`stream.py:899` 独立字典），启动时无条件清空目录内所有文件（`938-942`）：
  - 场景 A：正在播放（会话 sdir=`<vid>/fh720_s0`）时点「开始预转码」→ prewarm 清空该目录 → 在线会话分片被删（ffmpeg 继续写新片，hls.js 拉不到已删片）；
  - 场景 B：预转码进行中重新点播 → `_spawn_session` 的复用循环只查 `_sessions`（`664-674`），看不到 prewarm → 走 `681-685` 清目录并发起第二个 ffmpeg，两个进程写同一 `<name>_segNNNNN.m4s`；
  - 场景 C：重复点「开始预转码」（前端 `preJob` 只在当前页面生效，刷新后可再点）→ 两个 job 同目录。
  修复：把 prewarm 注册为 `_sessions` 成员（带 `prewarm=True`）并在两处复用/拒绝逻辑中可见，或给 prewarm 单独的子目录前缀并在 `_spawn_session` 复用时优先查静态成品。
- **D2（P2）预转码用 `default_caps()`，与真实客户端 plan 可能不一致导致成品白转。** `_prewarm_worker` 固定保守 caps（`916`），而 `_plan_marker` 含 `acopy`/`tonemap` 等字段。典型：Chrome + HEVC 片（需视频重编）+ EAC3 音轨（客户端可 copy）→ 在线 plan `acopy=True`，预转码 plan `acopy=False` → 键不同，预转码成品不命中，在线仍真转。建议 `/prewarm` 接受 `caps`（前端有实测结果，直接带上），与 decide 同源。
- **D3（P2）同目录不同 plan 会清掉已完工静态成品。** 目录键 `_quality_key` 不含 `sub`/`tonemap`（`426-434`），例如同一 `fh720_s0` 目录先做出 sub=none 的静态成品，之后用户要求烧录（plan 不同）→ `_session_complete` 不匹配 → 走 `681-685` 清空目录重转，原静态成品丢失，再切回 non-burn 需重转。属空间/复用权衡；建议按 plan 拆目录或保留 `complete.json` 多份。
- **D4（P2）`_hls_sem` 并发上限硬编码 2、不可配置。** `stream.py:40`。NAS CPU 差异大，建议 `MAX_TRANSCODES` env（默认 2）。
- **D5（P2）首屏等待把 semaphore 槽位占用最坏 300s**（`_spawn_session` `738-744`）：两路慢转码会把第三路直接 429；符合“最多 2 路”的设计，但 429 文案未告知等待时长。产品层面可接受，记录。
- **D6（P2）`probe_missing` 是同步全库 N+1**（`318-331` 每行一次 `get_media_info`，再看 `rows[:limit]`）：1 万行库每次调用约 1 万次 DB 往返，页面会卡。建议 SQL 一次筛选（`LEFT JOIN media_info ... WHERE mi.movie_id IS NULL OR probe_ver<? OR ...`）。
- **D7（P2）`_versions_payload` 串行探测所有版本**（`246-285`）：无缓存的多版本片（4 版本 × ffprobe）单请求最坏 2 分钟，超过前端 120s 超时。建议并发有界（ThreadPool 2-4）或懒探测（只探最优候选）。
- **D8（P2）`_SESS_IDLE=600` 与前端心跳间隔（~10s）不匹配风险**：hls.js 在暂停时可能停止请求列表 → 无心跳 → 10 分钟杀会话；恢复播放时 hls.js 会因 404 重建会话（前端需处理）。设计可接受，但建议前端在 `pause`/`seeking` 时也 ping（R14 复核是否有 ping 逻辑）。

### 2. 代码符合度（废弃代码/漏洞）

- **B1（P2）`_decide_payload` 的 method 白名单含不存在的 `"transcode"`**（`stream.py:158-159`），P1 遗留命名；`hls_url` 判断可简化为 `method != "direct"`。
- **B2（P2）`_prewarm_jobs` 无锁且无上限**（`899`）：worker 线程与状态接口并发读写 dict（GIL 层面安全但快照可能看到中间态），job 永不清理 → 长期运行内存增长（每 job 含 sdir/error）。建议加锁 + 保留最近 N 条/TTL。
- **B3（P2）`hls_segment`（legacy）按 mtime 选最新会话目录**（`1267-1284`）：同版本多个会话并存时可能取错目录的分片（新播放后旧 hls.js 还在拉旧会话分片 → 拿到新会话的同名分片，时间轴错位）。legacy 口已标记“新播放器请用 sessions 口”，属可接受遗留；若前端已无调用建议删除（与 R09 旧口同类）。
- **B4（P2）`_write_master` 失败静默**（`576-580` `except OSError: pass`）：master 写不出时客户端 404，但会话仍跑（浪费）；debug 口也看不出。建议记录/上报失败。
- **B5（P2）`_spawn_session` 复用判定把 `caps_hash` 排除在 `plan_key` 外是刻意设计**（注释 `642-643`）——正确，但 `caps_hash` 只用于 debug 观测；`hls_session_create` 响应也没带它，前端无法把“同一会话被不同 caps 客户端复用”告知用户。信息性，可接受。
- **B6（P2）`_media_start_for` 缓存上限 64 时整体 `clear()`**（`497-500`）：抖动清空但无正确性影响。
- **B7（P2）`_kill_proc` 后立刻 `_drop_session`**：`proc.wait(timeout=5)` 失败再 `kill()` 不等待；OS 层面进程可能残留短暂时间，行为可接受。
- **B8（P2）安全面（未发现可利用漏洞）**：所有文件路由（`{name}`/`seg`/font）都做了白名单正则 + `basename` + `dirname==base` 双重校验（`1103-1111,1189-1195,1544-1566`）；`sid` 只作字典键；`version_id` int 校验；命令均为 list 形式无 shell。唯一残余：无鉴权（R01 D1/D2），任何能访问端口者可开转码会话消耗 CPU（429 限流缓解）。
- **B9（P2）`hls_session_debug` 把 `plan`（含内部路径 plan.json 结构）与 ffmpeg 尾日志返回**：自用调试口，可接受；若未来加鉴权再评估。

### 3. 代码质量差距（不符合量产要求）

- **Q1（P2）1571 行单文件混合“会话编排/文件服务/进度/预转码/字幕抽取/字体/旧口兼容”**，其中会话段约 800 行。建议拆 `stream_session.py`（spawn/sweeper/reuse）、`stream_media.py`（media/probe/versions）、`subtitles.py`（R13 范围）、`fonts.py`；旧直连口建议删除（前端已用 sessions 口，R14 确认后）。
- **Q2（P2）核心并发逻辑无测试**：复用/静态命中/被杀会话判 ENDLIST/软编重试/HW 判定——都是纯文件+进程状态机，可抽出 `_session_complete`/`_plan_marker`/`_quality_key` 做纯函数测试（零依赖），目前为零。
- **Q3（P2）状态与文件的双真相**：会话状态在内存，产物状态在磁盘（`plan.json/complete.json`），二者需人工推演保持一致（本次评审即做了此推演）。长期建议把会话元数据也写进 `plan.json`（pid/backend/attempt/started_at），重启后清道夫可自愈清理孤儿进程产物（当前重启后 `_sessions` 空、磁盘残留靠 TTL）。
- **Q4（P2）`_spawn_session` 的 300s 等待 + 每 0.5s 轮询文件系统**（`738-744`，`_wait_file` 同理）：请求线程被占用（FastAPI 线程池），两路会话 + 多个分片请求同时等待会耗尽默认线程池（40）。当前 2 路限制下安全，建议改为事件/更短轮询或 async。
- **Q5（P2）`video_segments`/`_seg_count` 用 `os.listdir` 全目录扫描**（`356-363`）：单目录数千分片时每次心跳/进度扫描成本上升（4K 电影 ~1000+ 片）。建议用计数器或 `glob` 前缀（当前实现已是前缀过滤，可接受）。

### 交叉引用
- **字幕索引契约已验证一致**（R11 B1 关闭）：`_sub_list` 合并后重排 `index`，`sub_idx` 即合并序号；内嵌用 probe 的 `ff_index` 做 `-map 0:{ff_index}`，外挂走 `sub_sidecar` 第二输入；`_spawn_session` 烧录分支的取轨与 `playback._subtitle_mode` 同源。
- `media_start` 语义（copy=前一关键帧 / 转码=start）由 `playback.actual_media_start` 提供，会话响应返回给前端做字幕平移；R13 复核客户端平移公式。
- 静态成品复用与 `PLAN_VERSION` 联动（R11 Q4 的版本号）正确：`_plan_marker` 带 `v` 字段（`468`）。
- `main.py` lifespan 调 `shutdown_sessions`（R01）已确认；会话 TTL 与 `transcode/` 清理同源（`_purge_old`，R13 字体/字幕缓存同目录共用 TTL）。
