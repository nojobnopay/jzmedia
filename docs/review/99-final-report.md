# 99 汇总报告 — status: done

> 评审范围 pin：commit `7a1b6bc`（v0.7.0），评审时工作树干净。数据来源：`01`–`14` 单元文件。
> 定级纪律：所有 P1 均有代码路径/实测证据；未做运行时复现的注明“代码路径确认”。**未发现 P0。**

## 0. 结论摘要

| 指标 | 数值 |
|---|---|
| 评审代码行数 | ~14.9k（后端 ~7.5k + 前端 ~7.4k，含 14 单元全量精读） |
| P0（正确性/安全/数据丢失/资源泄漏） | **0** |
| P1（应修） | **11** |
| P2（酌情修） | 238（含设计改进/质量项，逐条带 `file:line`） |
| 实测复现的问题 | 4（剧集入库、is_sample 误杀、手动标题被覆盖、批量删除预览路径） |
| 已确认安全漏洞（可利用） | 0（无鉴权为产品取舍，见 P1-01） |
| 已确认废弃代码 | 14 处（旧 API 2 组、死函数/字段/常量 8 处、死样式/重复定义 4 处） |

**总体判断**：架构选型与关键链路（播放决策、镜像缓存、NFO 收敛、字幕渲染、会话生命周期）的工程质量明显高于一般自用项目，设计文档（AGENTS/README/注释）与实际实现的一致度约 95%+。主要短板集中在 **①部署文档与实现不一致 ②边界输入（剧集/样片/回收站）③并发与原子性 ④巨型模块与零测试/零日志**。多数 P1 修复成本在“一行到十几行”量级。

---

## 1. P1 问题清单（按建议修复顺序）

| # | 单元 | 问题 | 证据 | 修复要点 |
|---|---|---|---|---|
| 01 | R01 D1 | 无任何认证/授权；`/api/fs/delete`、`PUT /api/settings` 等破坏性/写凭证 API 全开放 | `main.py:29-37`、`fs.py:323-383`、`health.py:108-119` | 可选 `JZMEDIA_TOKEN`（未配置=现状）；README 显著标注禁公网暴露 |
| 02 | R03 D2 | `is_sample` 正则误杀片名含 Sample 的正片 → 永不入库（**实测**） | `scanner.py:27,938-941` | 禁止空格边界；要求 release 分隔符/行尾语境 |
| 03 | R03 D1 | 剧集“跳过”实际入库为电影行，污染海报墙/统计/搜索（**实测**，README 承诺不符） | `scanner.py:947-950` | 不建行（skipped_paths 轻表）或 `media_type='episode'` 全局过滤 |
| 04 | R03 D3 | 扫描不剪枝 `#recycle`/`@eaDir`/隐藏目录（NAS 已删视频会重新入库） | `scanner.py:1000` | `dirs[:]` 剪枝 + 可配置 ignore |
| 05 | R08 D1 | `clean-sidecars` 与 02 号问题叠加会删真实正片库行（文件保留但重扫仍被样例规则跳过） | `files.py:559`、`Settings.vue:607-612` | 先修 02；再对 `tmdb_id` 非空行加保护 |
| 06 | R09 D1 | 整理/恢复目标与“库内 missing 行”撞车 → `UNIQUE(file_path)` 冲突，**文件已移动但 DB 未更新** | `files.py:392,454-455,669-671`；`store.py:17` | 执行前查 `store.get_by_path(to)`；或 IntegrityError 后 rename 回滚 |
| 07 | R12 D1 | 预转码未注册会话 → 与在线播/其他 prewarm 互踩同一会话目录（清片/双写） | `stream.py:899,938-942` vs `664-685` | prewarm 注册进 `_sessions` 或独立目录 + 复用检查 |
| 08 | R13 D1 | HDR + 烧录时 tonemap 被丢弃且无 `hdr_no_tonemap` 提示（有色偏但无告警） | `playback.py:315-320`、`transcode.py:54-59` | burn+tonemap 时补 reason（或软件 tonemap 链） |
| 09 | R01 B1 | UID/GID 文档要求 NAS 配置，但仅在 WSL override 生效 → NAS 容器 root，宿主文件属主错乱 | `docker-compose.yml:1-31`、`override:5`、`README:128` | 主 compose 增加 `user:` |
| 10 | R01 B7 | 全应用零日志 + 76 处静默 `except: pass`（跨模块） | 后端无 `import logging`（grep 证） | `logging.basicConfig` + 关键路径补日志；长期替换静默吞异常 |
| 11 | R04 D1 | 库页无分页，>500 部静默截断（`/api/search` 默认 limit 500，UI 不传 limit） | `Library.vue:390-394`、`movies.py:16` | 游标分页/无限滚动 + 后端 limit 钳制 |

**建议批次**：02→03→04→05 一批（扫描正确性）；06→07→08 一批（一致性/并发）；09→10→11 一批（部署/可运维/可用性）；01 属安全基线，可独立并行。

---

## 2. 跨单元重复问题（合并去重）

### 2.1 文件写入的原子性与并发护栏（出现 5 次）
- `save_person_avatar`/`download_poster` 裸写目标（R03 B6）；`poster-orig` 同（R05 B8）；`_convert_sidecar`/`_extract_embedded` 并发写同一缓存（R13 D4）；NFO 直写（R10 B1）；上传/移动的 rename 覆盖竞态（R05 D2、R09 D1）。
- **统一方案**：加 `atomic_write(dest, bytes)`（tmp+`os.replace`）与按目标路径的进程内锁；上传类用 `O_EXCL` 占位。一次封装消除 5 处。

### 2.2 全表加载 / N+1（出现 9 次）
`get_facets` 全表 Python 聚合（R02 D4）、`_collection_cover`/`list_collections` N+1（R02 D6）、`find_movie_for_extra` 全表 × sidecar（R03）、`batch_update` 每版本 `get_movie`（R05 B2）、`add_collection_members` 重载（R06）、`_collect_plans` 两次全表（R09 D4）、`probe_missing` 每行 `get_media_info`（R12 D6）、`_versions_payload` 串行探测（R12 D7）、Settings 首屏 6 个重查询（R14 D1）。
**统一方案**：SQL 侧聚合/`LEFT JOIN` 预筛 + 有界线程池并发 + 轻量列表接口。

### 2.3 巨型模块（出现 6 次）
`store.py 1931` / `stream.py 1571` / `scanner.py 1020` / `movies.py 972` / `files.py 712`；前端 `PlayerModal 1853` / `Settings 1060` / `Library 1050` / `Detail 1014`。关键函数超长：`sync_nfos_for` 150 行、`_collect_plans` 108 行、`plan` 100 行、`_movie_delete_scope` 100 行。
**统一方案**：见 §4 路线图批次 4。

### 2.4 单源化缺失（出现 4 组）
- 国家中文名：`regions.country_name` vs `Detail.vue:263` 硬编码 10 国（R05 D6/R10 D6）。
- 文件名清洗：`files._safe_component` vs `editions.sanitize_tag` 两份（R09 B3）。
- “同茎兄弟”判定：`files._sibling_followers` / `movies._movie_delete_scope` / `scanner.is_extra` 三份（R03 Q2、R05 B4）。
- 轮询模式：Collections backfill / Detail prewarm / PlayerModal ping 各写一遍（R06 Q1、R07 Q1）。
建议按上述顺序单源化（国家名后端下发/清洗与茎判定收敛到 scanner/editions，轮询抽 `usePolling`）。

### 2.5 废弃代码与旧兼容口
- API 旧口：`POST /api/files/rename|relocate`（前端已无调用，R09 Q4）、旧 HLS 直连 `/{id}/master.m3u8` + `/{id}/seg/*`（R12 B3）。
- 死函数/字段：`store.get_all_settings`、`settings.env/app_port`、`caps.mse`（R02/R01/R11）、`CHINESE_SUB`（R10 D5）、`_KIND_WORDS` 重复行（R03 B3）、`sanitize_edition/sanitize_spec` 别名（R03 B3）。
- 死样式/重复：`Library.vue .selbar`、`App.vue .card img` 双份（R04 B4、R14 B3）。
- 死字符串：`"transcode"` 方法名（R12 B1）、`"phase":"phase2"`（R01 B4）。
**统一方案**：批次 4 一次清理（删前 grep 确认无引用，已在本报告完成引证）。

### 2.6 错误处理与可观测性
零日志 + 76 处静默吞异常（R01 B7）、`api.js` 把 HTTP body 拼进 message 直出 UI（R05 Q6/R14 Q4）、前端 3 处 `console.warn` 无策略（R14 B4）、`_JOBS`/`_prewarm_jobs` 无界增长无锁（R06 B2/R12 B2）。

### 2.7 测试与工程化基建缺失
后端 0 测试（R03 Q5、R09 Q2、R11 Q2、R12 Q2、R13 Q4）、前端 0 测试/0 lint/0 类型（R04 Q6、R14 Q3）、依赖 `>=` 无锁（R01 Q2）、无 CI。
**建议起点**（成本低、收益高）：`tests/` 用 stdlib `unittest` 覆盖 6 个纯函数域——`editions`（后缀消解/冲突）、`regions.normalize_tags`、`nfo` 生成、`playback.plan` 决策矩阵（media/caps dict → method）、`stream._plan_marker/_quality_key/_session_complete`（tmp 目录）、前端 `subStyle.js`。这些均无外部依赖。

---

## 3. 架构级建议

1. **长任务形态统一**：当前 4 种（同步 HTTP / FastAPI BackgroundTasks / 自建线程+内存 job / 自建线程+文件产物）。建议统一为“job 注册表 + 状态查询 + 取消”的最小框架（`collections.py` 的实现可作模板），扫描/刷新/补全/预转码/补探测全部接入；顺带解决 R03 D4、R04 D6、R12 D2/D6。
2. **数据一致性收口**：把 “删影片时同步清 extras 引用”（R02 B2）、“移动前查 DB 占用”（R09 D1）、“会话产物键互斥”（R12 D1）三处一致性规则下沉到 store/stream 的单一入口，避免多路径各自为政。
3. **写路径原子化基建**：见 §2.1；同时把“先磁盘后 DB”的顺序改为“先校验/占位 → 执行 → DB → 失败回滚”，或至少保证失败可自愈（R05 B3、R09 D1）。
4. **配置单一来源**：`regions`/`editions`/`caps` 契约已是单源；把“国家名/清洗/茎判定”补齐（§2.4），并把前端可见的枚举（reason 文案、字幕后缀词表、caps 键）与后端契约写进 AGENTS 或生成。
5. **可观测性**：结构化日志（含 movie_id/路径/会话 id）+ `/api/health` 扩展（DB 可写、媒体根可读、磁盘余量、转码后端）+ 现有 debug 口保留。
6. **安全基线**：可选 token、安全响应头（`X-Content-Type-Options`/CSP）、`README` 增加“网络边界”章节；上传端点扩展名/大小白名单（R05 D2）。

---

## 4. 重构路线图（可落地分批）

| 批次 | 主题 | 内容 | 预估 |
|---|---|---|---|
| B1 | 扫描/清理正确性（P1-02…05） | `_SAMPLE_RE` 收紧；episode 不入库或过滤；walk 剪枝；clean-sidecars 保护；补 4 个纯函数测试 | 0.5–1 天 |
| B2 | 一致性与并发（P1-06…08 + §2.1） | `atomic_write` 基建（5 处替换）；移动前 DB 占用检查；prewarm 会话注册；burn+tonemap reason | 1–2 天 |
| B3 | 部署/运维/可用性（P1-09…11 + §2.6） | compose `user:`；logging 基础；关键静默点补日志；库页分页 + limit 钳制；Settings 首屏并行 | 1–2 天 |
| B4 | 性能（§2.2） | facets/SQL 预筛；probe_missing SQL；versions 并发探测；collections N+1；organize 单次全表 | 2–3 天 |
| B5 | 结构（§2.3/2.4/2.5） | 拆 `store/stream/scanner` 与前端巨组件；抽 composable；删旧 API/死代码；单源化四组；前端路由懒加载/404 | 1–2 周 |
| B6 | 测试与 CI | pytest 纯函数域；前端 vitest/node:test；eslint；requirements 锁版本 | 1 周 |
| B7 | 安全与体验 | 可选 token；安全响应头；a11y（alt/焦点陷阱）；TMDB 表单未保存提示 | 1 周 |

> B1/B2 全部为“小改动高收益”，建议优先；B5 前先把 B1/B2 落地，避免边拆边改引入回归。

---

## 5. 值得保留的设计亮点（重构时勿丢）

- `tmdb_cache` 镜像 + `LOCAL_FIELDS` 白名单隔离（`store.py:71-95,179-189,577-583`）：本地改动永不污染远端数据，是多版本/刷新体系的地基。
- FTS 显式 `resync_fts` + 启动自愈触发残留清理（`store.py:162-169,534-561`）。
- 四档播放决策 + 逐片码串实测（`caps.probes` 三态）+ reasons 人话映射（`playback.py:243-346`、`PlayerModal.vue:362-380`）。
- `complete.json + plan.json` 双标记防“被杀会话冒充静态成品”（`stream.py:832-861`）——这是很罕见的严谨处理。
- 字幕五档模式与三条降级链（ASS 无字体→VTT、PGS 失败→烧录、解析失败→原生 track）：`PlayerModal.vue:736-1154`。
- 播放器三级恢复 + 冻结帧 + `reloadGen` 防并发 reload（`PlayerModal.vue:1675-1720`）。
- `editions.py` 的“最小后缀消解 + 编号兜底 + 疑似错配不动手”策略与集中注释。
- `regions.py` 的“存事实派生视图”单源设计。
- NFO 三种落盘规则（独占/多版本/共享）与 `sync_nfos_for` 唯一入口。

---

## 6. 方法与局限

- **方法**：逐单元逐行精读（14 单元，含 1931/1853 行级文件），每条结论带 `file:line`；跨单元契约（FTS 调用、字幕索引、caps 字段、plan marker、original_file_path）做了两两核对；对 4 个高危疑点做了运行时实验（隔离在 `/tmp/opencode/rev3*`，未触碰仓库/真实库）。
- **局限**：宿主无 ffmpeg/ffprobe（未下载 static 包），所有 ffmpeg 命令行为（fMP4 ENDLIST、tonemap_vaapi/vpp_qsv 实机效果、字幕抽取）为代码路径/文档确认而非实测；浏览器侧行为（PDF 预览 attachment、原生 HLS 音轨、libpgs/JASSUB）未实机验证；未做性能压测（全表/N+1 的严重度基于复杂度分析）。
- **快照**：结论基于 `7a1b6bc`；若后续提交改动上述文件，需按 `docs/review/*.md` 中 `file:line` 重新定位。

## 7. 后续行动（可直接执行）

1. 从 B1 批次建 issue/任务：4 个 P1 + 对应测试（纯函数，零依赖）。
2. 全库 grep 确认后删除 §2.5 旧 API/死代码（一次提交一个主题）。
3. 在 AGENTS.md 增补：单 worker 约束、可选 token 约定、日志级别 env、`atomic_write` 约定、国家名/清洗/茎判定单源位置。
4. 把本报告 §1 表格粘贴为仓库 Issues 清单（11 条），逐条关单。
