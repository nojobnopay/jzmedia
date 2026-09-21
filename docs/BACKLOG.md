# jzmedia 未完成任务清单（BACKLOG）

> 来源：2026-09-21 对 `docs/fix/`、`docs/plans/` 完成度审计后固化。原规划/台账文档已删除（见下方
> 「历史文档索引」），本文件是这些文档中**尚未落地**事项的唯一真相源。
> 状态：`pending` / `doing` / `done`；完成后在本文件标注并保留一行结论。
> 已决策不做的事项列在文末「已弃项」，附理由，不再重开。

## P1 — 小而明确，建议尽快

| # | 任务 | 现状与位置 |
|---|---|---|
| 1 | 前端路由懒加载 + 404 catch-all | `frontend/src/router.js` 仍 8 个静态 `import`，无 `/:pathMatch(.*)*`；`PlayerModal`/hls 已懒加载，但各 View/caps/subStyle 仍在主包。原评审 R14-D5 曾"留给 B9 拆分顺带"，B9 收口时漏做 |
| 2 | 抽 `useSubtitles()` composable（~500–600 行） | `frontend/src/components/PlayerModal.vue` 现 1957 行，VTT/ASS/PGS 三渲染器 + 偏移 + 样式 + 降级链仍在单文件；原 R13-Q1/R14-Q1，当时因"刚经用户验证、回归风险高"明确延期 → 需专门会话 + 即时 H-UI 点检 |

## P2 — 有价值，排期不紧

| # | 任务 | 现状与位置 |
|---|---|---|
| 3 | Collections/CollectionDetail/Person 样式单源 | B9-COLLECTIONS/PERSONS 台账标"部分"：`usePolling` 已抽，样式去重未收口 |
| 4 | 人物页作品列表按当前媒体库过滤 | 规划 §10.1/§12 的 `/p/:tmdb_id?lib=` 未实现：`app/routers/persons.py` 与 `frontend/src/views/Person.vue` 均无库参数（TV 页已有 `media_library` 过滤，可参照） |
| 5 | Provider 失败冷却 + 设置页链路状态 | 规划 §9.1 的 `provider_state`（fail_count/cooldown_until）未实现；`app/metadata/chain.py` 逐次直接调用，仅 `app/mounts.py:39` 有库级退避。设置页无 provider 链路/最近错误展示 |

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
