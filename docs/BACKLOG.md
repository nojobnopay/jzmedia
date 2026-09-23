# jzmedia 未完成任务清单（BACKLOG）

> 来源：2026-09-21 对 `docs/fix/`、`docs/plans/` 完成度审计后固化。原规划/台账文档已删除（见下方
> 「历史文档索引」），本文件是这些文档中**尚未落地**事项的唯一真相源。
> 状态：`pending` / `doing` / `done`；完成后在本文件标注并保留一行结论。
> 已决策不做的事项列在文末「已弃项」，附理由，不再重开。

## P1 — 小而明确，建议尽快

| # | 任务 | 状态 | 结论 |
|---|---|---|---|
| 1 | 前端路由懒加载 + 404 catch-all | **done** 2026-09-21 (`764e53f`) | `router.js` 全 View 动态 import + `/:pathMatch(.*)*` 回首页；构建已拆出独立 chunk |
| 2 | 抽 `useSubtitles()` composable（~500–600 行） | **done** 2026-09-21 | 字幕子系统移至 `frontend/src/useSubtitles.js`（选轨/默认轨、本地临时字幕、VTT 自绘、ASS/PGS、延迟/外观/兼容降级）；PlayerModal 1957→1380 行，对外标识符同名、设置契约不变；lint/test/build 通过，**待 H-UI 按验收清单点检（2026-09-23 仍未确认，保持 pending）** |

## P2 — 有价值，排期不紧

| # | 任务 | 状态 | 结论 |
|---|---|---|---|
| 3 | Collections/CollectionDetail/Person 样式单源 | **done** 2026-09-21 (`0996a9e`) | 共享块（card-block/hero-*/meta-line/overview/empty/cast-*）上移 `App.vue` 全局，Person/Detail 去重；页面特化保留 scoped 覆盖 |
| 4 | 人物页作品列表按当前媒体库过滤 | **done** 2026-09-21 | `GET/POST /api/persons/{tmdb_id}` 支持 `media_library`/`library`（`_library_scope`，未知媒体库→空），Person.vue 随切换器重载；`tests/test_person_scope.py` |
| 5 | Provider 失败冷却 + 设置页链路状态 | **done** 2026-09-21 | `app/metadata/state.py` 状态落 `app_settings`：连续失败 3 次冷却 10 分钟、冷却期链跳过、成功清零；`GET/POST /api/metadata/providers` + 设置页 TMDB 区展示/重置；`tests/test_metadata_chain.py` |

## 后续版本（需要单独立项）

| # | 任务 | 说明 |
|---|---|---|
| 6 | TVmaze provider | 规划 §9.3：无 key 剧集源，仅框架；`app/metadata/` 现无 `tvmaze.py`，`KNOWN_PROVIDERS = local/tmdb/wikidata/douban/nfo` |
| 7 | 完整 TV 支持 | **done** 2026-09-22（T1+T2+T3）：T1 扫描器 v2 + v21（`tv_parse.py` 规则阶梯/子剧拆分/绝对集号/多集区间/增量+失效 GC；`tmdb_cache` 复合主键；4,150 视频 → 3,877 集 / 0 未知）。T2 刮削与前端（`tv_match.py`+`tv_persist.py`、`/api/jobs/tv-scrape`、TV API/海报墙/详情/继续观看/连播、stream episode 版本与 prewarm；60/60 剧匹配、99.6% 集有元数据、12 部待确认）。T3 落盘与维护（v22 所有权哈希；`tv_nfo_link` 写 `tvshow.nfo`+季 `season.nfo`；`artwork.write_for_show` 写海报/季海报；`/api/jobs/rebuild-tv-nfo`；`PATCH /api/tv/shows/{id}` 手工标题保护；fs 浏览器剧集跳转；`.plexmatch` 可选）。**不做**：剧集改名（NAS 有硬链接/做种，用户已确认只写元数据）；逐集 NFO 远程库默认关（SMB ~1.6s/文件；本次已按用户确认跑全量，`TV_EPISODE_NFO=1` 或 job `episodes:true` 可开）。未匹配剧列表仍走海报墙「待确认」角标 + 详情页手动匹配 |
| 11 | TV 结构与花絮收口（用户 2026-09-22 确认的 T4） | **done** 2026-09-23 | T4.1/T4.2 v2/T4.3（见 #7 同段）；用户逐项审核后全库执行：56 剧 4455 操作 0 失败（`org-*` 8 批）+ 5 部剧编号口径修正（`fix-20260922`）+ 漂移修复（`repair-*`）+ 一休尾盘对齐/绝对号剧执行/AoT S04 重编号 + 跨季回退守卫（`test_cross_season_fallback_guard`）+ S00 内容级审计收口；全部审计在 `organize_moves` 表可撤销。详见 `docs/tv-fix-and-pending-20260922.md` §5–§10。剧根改名断种风险已按用户确认逐剧执行完毕；蜡笔小新冻结待换资源（见该文档 §9–§10）。 |
| 8 | 播放实验项 | ① MKV 容器直通（Chrome 可直解 mkv，需 caps 实验）；② 无缝切画质（不重开会话）；③ 内置 CJK 字体子集是否随仓库分发（现为 `data/fonts/` 运行时可投放） |
| 9 | NFS 纯网页配置 | 技术指导 §28 的 libnfs sidecar 方向；现状 NFS 仅 host mount 兼容模式（`app/storage/factory.py:6`），未做应用内协议访问 |
| 10 | `logo.png` 落盘 | 规划 §8.3「后续可选」；`tmdb_cache.logo_tmdb_path` 字段已留，无落盘/展示实现 |

## review 迁移收录（2026-09-23 `docs/review/` 删除前，逐条验代码）

`docs/review/`（v0.7.0 快照评审，P0=0/P1=11/P2=238）已收官删除。删除前核查结论：P1×11 全部在代码中确认已修；B1–B7 路线图已被 v0.8.0/v0.10.0+/远程性能批消费；§2.4 四组单源化（国家名/清洗与茎判定/轮询 `usePolling.js`）均已落地。仅以下 3 个微项未实施，转入此处排期（均为低优先级）：

| # | 事项 | 出处 | 状态 |
|---|---|---|---|
| 12 | 设置页表单未保存提示（TMDB 区等，`isDirty` 守卫） | 评审 B7 残留 | pending |
| 13 | `/api/health` 扩展磁盘余量（DB 可写/媒体根可读/转码后端已有，缺磁盘） | 评审 §3.5 建议 | pending |
| 14 | 上传扩展名/大小硬白名单 | 评审 §3.6 建议 | **不做**：现状文件名清洗 + subdir 白名单 + 原子落盘，局域网可信场景够用；仅当暴露公网时重议 |

评审 §7 行动#4（贴 Issues 清单）作废：本仓库本地 git、无远端 Issues。
代码中 56 处 `评审 Rxx` 注释 ID 改走 git 历史回溯：`git show 078bf33:docs/review/01-R01-infra.md`（以此类推）。

## 已弃项（附理由，不重开）

- **rclone sidecar（技术指导 Phase 2/3）**：`SMB_DIRECT_SPIKE` 在真实 NAS 实测后判定改用
  `smbprotocol` 进程内直读（内置 Range 代理），rclone 暂缓；仅当重连/VFS 缓存出现痛点时再议。
  已按此实现并全链路落地（`app/storage/`）。
- **B9-COLLECTIONS/PERSONS 之外的旧评审条目**：249 条已全部修复/等价覆盖/判定无需改，无欠账。

## 历史文档索引（已删除，git 历史可查）

代码注释中仍有 `目标文档 §x` / `指导 §x` / `MULTI_LIBRARY_PLAN` 等引用，按此表回溯：

| 原文件 | 最后提交 | 内容 |
|---|---|---|
| `docs/review/`（16 文件：00-index/R01–R14/99-final-report） | `078bf33` | v0.7.0 快照评审（P0=0/P1=11/P2=238 + B1–B7 路线图）；2026-09-23 按用户要求删除，未实施微项已收录上文，代码注释 `评审 Rxx` 按此提交回溯 |
| `docs/fix/00-index.md` | `c8f73f3` | v0.8.0 修复总纲（P1 全清，已收口） |
| `docs/fix/10-p2-backlog.md` | `c8f73f3` | P2 全量台账（B5a–B11，已完成） |
| `docs/plans/PLAYER_IMPL_PHASES.md` | `800207a` | 播放 P1–P5 实施日志（代码注释 `目标文档 §x` 指其配套 NAS 方案） |
| `docs/plans/NAS_Web_Video_Player_Development_Plan.md` | `67696f3` | 播放器目标与原则（代码注释 `目标文档 §x`） |
| `docs/plans/SMB_DIRECT_SPIKE.md` | `4bc2552` | SMB 直读 spike 实测与 GO 决策 |
| `docs/plans/MULTI_LIBRARY_PLAN.md` | `e65dd96` | 多库/远程库/离线刮削定稿（A–G；schema 章节为 v12/v13，已被 v17/v18 取代） |
| `docs/plans/jzmedia_remote_media_library_technical_guidance.md` | `4bc2552` | 远程库技术指导（代码注释 `指导 §x`；rclone 主路径已弃） |

检索示例：`git show 4bc2552:docs/plans/SMB_DIRECT_SPIKE.md`；`docs/review/` 已按用户要求删除（见上表回溯方式），代码中
`评审 Rxx-xx` ID 按 `078bf33` 提交查到详情。
