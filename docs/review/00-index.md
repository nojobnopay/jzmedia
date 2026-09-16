# jzmedia 代码 Review — 总纲（唯一真相源）

> 本文件是整个 review 工作的进度真相源。任何中断（含上下文压缩）后，先读本文件恢复状态。
> 评审范围 pin：**commit `7a1b6bc`（v0.7.0）**，工作树干净。评审基于此快照，后续代码变动需在结论文件注明“快照后漂移”。
> 纪律：只评审不改代码；所有问题必须带 `file:line` 证据；不确定的写“未确认”，不臆断；一次只进行一个 R 单元。

## 用户三问（每个单元必须回答）

1. **功能设计**是否合理？是否有更好的方案？
2. **代码编写**是否符合要求？是否有废弃代码、软件漏洞？
3. **代码质量**是否有需要提升的地方？哪些实现不符合高质量量产代码要求？

严重度定义：

- **P0 严重**：正确性/安全/数据丢失/资源泄漏，必须修。
- **P1 重要**：可复现的体验/性能/维护性问题，应修。
- **P2 建议**：风格/一致/可读性/长期可维护，酌情修。

## 系统架构速览

```
浏览器 (Vue3 SPA, frontend/dist 由 FastAPI 托管)
   │ /api/*  (同源, /docs OpenAPI)
   ▼
FastAPI app/main.py  v0.7.0
   ├─ routers/health.py    健康检查(+transcoder 状态)
   ├─ routers/movies.py    列表/搜索/facets/详情/PATCH/batch/match/TMDB search
   ├─ routers/collections.py  手工合集 + TMDB 系列推荐/补全/top-up
   ├─ routers/files.py     整理预览/执行(rename/organize)/恢复原始位置/clean-sidecars
   ├─ routers/fs.py        目录浏览(登记媒体根/本地目录)
   ├─ routers/extras.py    花絮样片
   ├─ routers/jobs.py      scan/backfill-meta/rebuild-nfo/douban-fetch(501)
   ├─ routers/persons.py   人物页(纯本地 + 后台 bio 刷新)
   └─ routers/stream.py    decide/versions/sessions(HLS)/sub/fonts/progress/prewarm/backends
        │
        ├─ playback.py   4 档决策 direct|remux|audio_transcode|video_transcode + build_cmd
        ├─ transcode.py  VAAPI→QSV→NVENC 冒烟探测 + 软件兜底
        ├─ media.py      ffprobe 探测(缓存进 media_info)
        ├─ caps.py       ClientCapabilities 归一化/哈希
        ├─ scanner.py    扫描/匹配/入库/侧车(is_sidecar)/NFO 联动/同步人物
        ├─ tmdb.py       TMDB 客户端(Http 代理/多语言)
        ├─ nfo.py        Kodi NFO 生成
        ├─ editions.py   版本/规格/分卷命名解析与规划
        ├─ store.py      SQLite + FTS5 + facets + 过滤(1931 行, 核心数据层)
        ├─ regions.py    产地→大区映射(唯一来源)
        └─ config.py     DB 优先/env 兜底运行时配置 + 设置页可写键
```

数据落盘：`data/jzmedia.db`（SQLite WAL）+ `data/posters/` + `data/transcode/<vid>/`（TTL 24h）+ `data/fonts/`；NFO 写媒体目录（Kodi/Jellyfin 兼容）。

## 单元划分与状态

| ID | 功能 | 核心文件 | 状态 | 结论文件 |
|---|---|---|---|---|
| R01 | 基础架构/配置/健康/SPA 托管/部署 | main.py, config.py, db.py, routers/health.py, routers/fs.py, Dockerfile, docker-compose*.yml, start.sh, .env.example | done | 01-R01-infra.md |
| R02 | 数据层 Store/FTS/facets/过滤 | store.py, db.py | done | 02-R02-store.md |
| R03 | 扫描刮削入库 | scanner.py, tmdb.py, editions.py, scripts/find_subs.py | done | 03-R03-scanner.md |
| R04 | 媒体库浏览/搜索/过滤前端 | routers/movies.py(列表/搜索/facets段), views/Library.vue, api.js, prefs.js | done | 04-R04-library.md |
| R05 | 详情/编辑/评分/批量/手动匹配 | routers/movies.py(详情/PATCH/batch/match段), views/Detail.vue, ratings.js, ScoreBadge.vue | done | 05-R05-detail.md |
| R06 | 合集/系列推荐/top-up | routers/collections.py, views/Collections.vue, CollectionDetail.vue | done | 06-R06-collections.md |
| R07 | 人物页/头像 | routers/persons.py, views/Person.vue | done | 07-R07-persons.md |
| R08 | 花絮/样片 extras | routers/extras.py, scanner.py(is_sidecar 相关段) | done | 08-R08-extras.md |
| R09 | 文件整理/重命名/搬迁/恢复 | routers/files.py, views/Settings.vue(整理/恢复段) | done | 09-R09-files.md |
| R10 | NFO/产地映射 | nfo.py, regions.py | done | 10-R10-nfo-regions.md |
| R11 | 播放决策(probe/caps/plan/后端/HDR) | media.py, caps.py, playback.py, transcode.py, caps.js | done | 11-R11-playback-plan.md |
| R12 | HLS 会话/预转码/断点续播 | routers/stream.py(会话/进度/预转码段) | done | 12-R12-stream-session.md |
| R13 | 字幕系统(抽取/外挂/VTT/ASS/PGS/字体) | routers/stream.py(字幕段), jassubLoader.js, pgsLoader.js, subStyle.js | done | 13-R13-subtitles.md |
| R14 | 播放器 UI + 设置页 | PlayerModal.vue, views/Settings.vue, clipboard.js, router.js, App.vue, Spinner.vue | done | 14-R14-player-ui.md |

汇总已完成：`99-final-report.md`（P0=0 / P1=11 / P2=238；含重构路线图 B1–B7）。**14 个单元 + 汇总全部 done，本次评审结束。**

状态取值：`pending` → `doing` → `done`（`blocked` 需在该单元文件写明原因）。

## 跨单元交叉点（评审时互相引用，避免重复结论）

- FTS 规则：`movies`/`persons`/`movie_person` 写入后必须 `store.resync_fts(movie_id)`（R02 立规，R03/R05/R06 检查调用方）。
- `original_file_path` 永不改（R09 核心约束，R03 首写）。
- region 一律走 `regions.resolve`（R10，检查 R03/R04/R05）。
- caps 契约：前端 `caps.js` 产出 ↔ 后端 `caps.py` 归一化（R11，R12/R13 消费）。
- 会话产物键 `_quality_key`/`_plan_marker`（R12，R13 字幕抽取复用）。
- `update_movie_local` 只写本地列（R05 约束，R02 实现）。

## 恢复指引（中断后从这里继续）

1. 读本文件状态表，找第一个非 `done` 的单元。
2. 读该单元文件头 `status` 与进度 log，若 `doing` 则从 log 最后一条继续。
3. 完成后：先更新单元文件（结论 + status: done + log），再回本文件把状态改 `done`。
4. 全部 done 后写 `99-final-report.md`（跨单元去重、Top 问题排序、重构路线图）。

## 全局进度 Log

- 2026-09-16：评审启动，pin commit `7a1b6bc`，创建脚手架与 14 个单元模板。
- 2026-09-16：R01 开始（infra/config/health/fs/deploy）。
- 2026-09-16：R01 done（P1：UID/GID 文档与实现不一致、零日志+76 处异常吞噬；P2×13）。R02 开始（store）。
- 2026-09-16：R02 done（P2×15，含 delete_movie 遗留 extras 悬挂引用（重扫自愈）、无 WAL/busy_timeout）。R03 开始（scanner/tmdb/editions）。
- 2026-09-16：R03 done（P1×3 实测复现：剧集实际入库污染海报墙、is_sample 误杀正片、不跳过 #recycle/@eaDir；P2×11）。R04 开始（Library 前端+列表 API）。
- 2026-09-16：R04 done（P1：库页无分页 >500 部截断；P2×11）。R05 开始（详情/编辑/批量/匹配）。
- 2026-09-16：R05 done（P2×15，含 PDF 预览失效、上传改名竞态、定时器泄漏；定级校准：PATCH 长度校验按 P2）。R06 开始（collections）。
- 2026-09-16：R06 done（P2×12，无 P1：任务单例内存态、轮询重负载、LIMIT 无排序、整数无上界）。R07 开始（persons）。
- 2026-09-16：R07 done（P2×8，无 P1：bio 语言硬编码忽略 TMDB_LANGUAGE、无频率限制、console.warn 与 timer 泄漏）。R08 开始（extras）。
- 2026-09-16：R08 done（P1：clean-sidecars 与 is_sample 误报叠加会删正片库行（R03 D2 连带）；P2×9）。R09 开始（files 整理/搬迁/恢复）。
- 2026-09-16：R09 done（P1：目标路径与 missing 行撞车致“文件已移/DB 未改”（代码路径确认）；P2×15，含旧 API 死口、跨设备无兜底）。R10 开始（nfo/regions）。
- 2026-09-16：R10 done（P2×10，无 P1：XML 控制字符未清洗、NFO 非原子写、CHINESE_SUB 死常量、前端国家名重复映射）。R11 开始（播放决策链）。
- 2026-09-16：R11 done（P2×16，无 P1：显式档位上采样、caps.mse 死字段、>8 音轨选择不一致、avc1 探针契约待 R12/R13 验证）。R12 开始（stream 会话）。
- 2026-09-16：R12 done（P1：prewarm 未注册会话导致与在线播/自身互踩同目录；P2×14；R11 字幕索引契约已验证一致）。R13 开始（字幕系统）。
- 2026-09-16：R13 done（P1：HDR+烧录丢弃 tonemap 且无提示；P2×14；索引契约与时间轴公式验证通过）。R14 开始（播放器 UI + 设置页，最后一个单元）。
- 2026-09-16：R14 done（P2×11，无 P1：首屏串行重负载、fs 改名/移动无预览、无路由懒加载/404、可访问性欠账）。14 个单元全部完成，开始写 99 汇总报告。
- 2026-09-16：99 done。**评审收尾完成**：P0=0、P1=11、P2=238；4 处实测复现；重构路线图 B1–B7。定级校准：R02 B1/B2、R05 D1 由 P1 降为 P2（健壮性/UX 类）。
