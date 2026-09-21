# jzmedia 未完成任务清单（BACKLOG）

> 来源：2026-09-21 对 `docs/fix/`、`docs/plans/` 完成度审计后固化。原规划/台账文档已删除（见下方
> 「历史文档索引」），本文件是这些文档中**尚未落地**事项的唯一真相源。
> 状态：`pending` / `doing` / `done`；完成后在本文件标注并保留一行结论。
> 已决策不做的事项列在文末「已弃项」，附理由，不再重开。

## P1 — 小而明确，建议尽快

| # | 任务 | 状态 | 结论 |
|---|---|---|---|
| 1 | 前端路由懒加载 + 404 catch-all | **done** 2026-09-21 (`764e53f`) | `router.js` 全 View 动态 import + `/:pathMatch(.*)*` 回首页；构建已拆出独立 chunk |
| 2 | 抽 `useSubtitles()` composable（~500–600 行） | **done** 2026-09-21 | 字幕子系统移至 `frontend/src/useSubtitles.js`（选轨/默认轨、本地临时字幕、VTT 自绘、ASS/PGS、延迟/外观/兼容降级）；PlayerModal 1957→1380 行，对外标识符同名、设置契约不变；lint/test/build 通过，**待 H-UI 按验收清单点检** |

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
| 7 | 完整 TV 支持 | 规划 §1.2/§13 明确的后续独立版本：TMDB TV 刮削、tvshow/episode NFO、Plex 剧集命名与 `.plexmatch`、剧集改名整理。当前 TV 为只读清单（`scan_tv_one` + `/api/tv/*`） |
| 8 | 播放实验项 | ① MKV 容器直通（Chrome 可直解 mkv，需 caps 实验）；② 无缝切画质（不重开会话）；③ 内置 CJK 字体子集是否随仓库分发（现为 `data/fonts/` 运行时可投放） |
| 9 | NFS 纯网页配置 | 技术指导 §28 的 libnfs sidecar 方向；现状 NFS 仅 host mount 兼容模式（`app/storage/factory.py:6`），未做应用内协议访问 |
| 10 | `logo.png` 落盘 | 规划 §8.3「后续可选」；`tmdb_cache.logo_tmdb_path` 字段已留，无落盘/展示实现 |

## 已弃项（附理由，不重开）

- **rclone sidecar（技术指导 Phase 2/3）**：`SMB_DIRECT_SPIKE` 在真实 NAS 实测后判定改用
  `smbprotocol` 进程内直读（内置 Range 代理），rclone 暂缓；仅当重连/VFS 缓存出现痛点时再议。
  已按此实现并全链路落地（`app/storage/`）。
- **B9-COLLECTIONS/PERSONS 之外的旧评审条目**：249 条已全部修复/等价覆盖/判定无需改，无欠账。

## 历史文档索引（已删除，git 历史可查）

代码注释中仍有 `目标文档 §x` / `指导 §x` / `MULTI_LIBRARY_PLAN` 等引用，按此表回溯：

| 原文件 | 最后提交 | 内容 |
|---|---|---|
| `docs/fix/00-index.md` | `c8f73f3` | v0.8.0 修复总纲（P1 全清，已收口） |
| `docs/fix/10-p2-backlog.md` | `c8f73f3` | P2 全量台账（B5a–B11，已完成） |
| `docs/plans/PLAYER_IMPL_PHASES.md` | `800207a` | 播放 P1–P5 实施日志（代码注释 `目标文档 §x` 指其配套 NAS 方案） |
| `docs/plans/NAS_Web_Video_Player_Development_Plan.md` | `67696f3` | 播放器目标与原则（代码注释 `目标文档 §x`） |
| `docs/plans/SMB_DIRECT_SPIKE.md` | `4bc2552` | SMB 直读 spike 实测与 GO 决策 |
| `docs/plans/MULTI_LIBRARY_PLAN.md` | `e65dd96` | 多库/远程库/离线刮削定稿（A–G；schema 章节为 v12/v13，已被 v17/v18 取代） |
| `docs/plans/jzmedia_remote_media_library_technical_guidance.md` | `4bc2552` | 远程库技术指导（代码注释 `指导 §x`；rclone 主路径已弃） |

检索示例：`git show 4bc2552:docs/plans/SMB_DIRECT_SPIKE.md`；`docs/review/` 保留未删，代码中
`评审 Rxx-xx` ID 仍可在该目录查到详情。
