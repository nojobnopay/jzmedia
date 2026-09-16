# P2 全量清零台账（唯一真相源）

> 目的：docs/review 认定 238 条 P2 全部落地（修复或决议关闭）。按**组**管理，不逐条建账；
> 每组给成员 ID、批次、状态、证据。中断恢复：读 §1 指针 → 查 §3 组状态。
> 状态：`pending` / `doing` / `done` / `closed`（决议不做，附理由）。
> 规则：组内全部处理完才标 `done`；评审原文只读，新发现记在各组备注。

## 1. 当前指针

```
批次：B9 结构重构批（fix/b9-<module>）— 设计说明已出，待用户确认后逐模块开工
B8 已合回（merge e51bcff，tag p2-b8-ux，用户 H-UI 通过）
进度：B5a+B6+B7+B8 已清 P2 约 150/238；剩余 = B9 结构（~12 组）+ B10 工程化（~5 组）
设计要点（见 §6 B9 设计说明）：门面重导出保持调用面零改动；逐模块一分支一合回
断点：R2/R3 已合 main（过程偏差：store 拆分提交直接落在 main，无独立分支；已打 b9-store 标）
下一步：files 拆 planner/executor → movies 拆 crud/blob/batch+scope 单源 → stream 拆
        session/media/subtitles/fonts → playback 拆 plan/cmd → R02-D1 迁移框架（H-DATA）
        → R04-D6 扫描任务化 → 前端视图拆分；每模块先建分支再改
```

## 6. B9 设计说明（送审）

1. **拆分原则：门面重导出（facade）**。形如 `app/store.py` 变 `app/store/__init__.py` 重导出各子模块符号，
   调用方（scanner/routers/…）**零改动**；子模块内部自由 import 对方。好处：每步可回滚、测试不需要改。
   备选（直改 import 全库）不推荐：改动面 200+ 处、与后续维护冲突。
2. **顺序（风险递增，逐模块一分支一合回）**：
   R1 nfo/regions 注释收尾（无拆分）→ R2 collections/persons 小文件整理 +
   `usePolling` composable → R3 store 拆分（schema/movies/persons/extras/collections/search/facets/app_settings）
   → R4 scanner 拆分（classify/parse/match/persist/nfo-link）→ R5 files 拆 planner/executor
   → R6 movies 拆 crud/blob/batch + scope 单源 → R7 stream 拆 session/media/subtitles/fonts
   → R8 playback 拆 plan/cmd → R9 前端：PlayerModal 拆 composable、Library/Detail/Settings 拆组件。
3. **DB 迁移框架（R02-D1，H-DATA）**：引入 `PRAGMA user_version` + 有序迁移表；
   以“现有自愈语句等价迁移”为 v1 基线，测试用旧库夹具（无新列）验证幂等与升级；
   升级前提示用户备份 `data/jzmedia.db`（一次性）。
4. **扫描任务化（R04-D6，H-DESIGN）**：`POST /api/scan` 改后台 job（复用 collections 的 job 模式：
   立即返回 job_id → `GET /api/jobs/scan/{id}` 进度/摘要 → 可选取消）；前端按钮改轮询。
5. **会话元数据落盘（R12-Q3）**：`plan.json` 增写 pid/backend/attempt/started_at；
   启动时清道夫据此清理孤儿产物（当前仅 TTL）。
6. **前端拆分（R14-Q1/Q5、R13-Q1/Q2、R04-Q1、R14-Q2）**：按“纯逻辑抽 composable、视图抽子组件”推进，
   每步 `npm build` + 手工点检（H-UI）合回；样式约定（JS 动态元素用非 scoped 块）写进 AGENTS。

## 4. 进度 Log（倒序）


## 2. 已完成批次（B1–B4，P1 全清，见 docs/fix/00-index.md）

- B1 扫描正确性 `p1-b1-scan`；B2 一致性/并发 `p1-b2-consistency`；B3 部署/可运维/可用性 `p1-b3-ops`；B4 安全基线 `p1-b4-auth`。

## 3. P2 分组台账

### B5a 硬化批（fix/b5a-harden）
| 组 | 成员 | 状态 | 证据 |
|---|---|---|---|
| B5a-1 | R03-B1 重扫覆盖手动标题 | done | 见 commit |
| B5a-2 | R03-D6 年份兜底采信未标 needs_review | done | 见 commit |
| B5a-3 | R10-D6 / R05-D6 国家名前端硬编码 | done | 见 commit |
| B5a-4 | R09-D4 clean 缺 dry_run | done | 见 commit |
| B5a-5 | R05-D2 上传 rename 覆盖竞态 + blob nosniff | done | 见 commit |
| B5a-6 | R03-B6 / R05-B8 / R10-B1 / R13-D4 / R13-D5 原子写 | done | 见 commit |
| B5a-7 | R12-D2 prewarm 用 default_caps 与真实 plan 不一致 | done | 见 commit |
| B5a-8 | R09-Q4 / R12-B3 旧口删除（rename/relocate/legacy HLS，用户确认删） | done | 见 commit |
| B5a-9 | R04-D3 筛选双请求 | done | 见 commit |
| B5a-10 | R06-D4 backfill 不自动续跑 | done | 见 commit |

### B6 安全加固批
| 组 | 成员 | 状态 | 备注 |
|---|---|---|---|
| B6-REQ | R01-B2（SPA 前缀越权）、R01-B8+R14-B5（安全头/CSP）、R05-D3（PDF inline）、R01-D3（未知 /api 返 200 HTML）、R01-D4（health 语义） | done（tests/test_web_security.py 6 用例；curl 证据见 log） | H-SEC 人审 |
| B6-INPUT | R09-B1（symlink realpath）、R09-B4（restore 越界）、R09-B2（only 强转 int）、R06-B3（int 上界）、R11-B5（caps 严格 bool）、R07-B2（tmdb_id 值域）、R02-B1 残余（rowcount/LIKE 转义）、R13-B3（VTT 对 VobSub 应 415） | done（tests/test_input_guard.py 12 用例，含 R13-B3 闭环） | |
| B6-CRYPTO | R11-B6（sha1→blake2）、R02-B4（SQLite 特性自检） | done（tests/test_crypto_selfcheck.py 2 用例） | |
| B6-BEHAVIOR | R08-D5（attach 覆盖需 force+回 previous）、R05-D4（非正片删除两步确认） | done（attach force 用例 + Detail 两步确认，build ok） | H-UI 点检 |
| B6-DECISION | R12-B9（debug 口保留：LAN+可选鉴权已覆盖，记录关闭） | closed（用户确认保留 debug 口：LAN+可选鉴权已覆盖，排障优先） | H-DECISION |

### B7 数据一致性与正确性批
| 组 | 成员 | 状态 | 备注 |
|---|---|---|---|
| B7-DELETE | R02-B2 / R08-B5 / R09-B5（delete_movie 悬挂 extras） | done（delete_movie 置空 extras；test_delete_movie_orphans_extras） | |
| B7-FAIL | R05-B3（删文件顺序+失败报告）、R05-B1（重复 FTS 重建）、R13-D6（ff_index 缺失 422）、R06-D5（已收录 409）、R06-B4（吞异常改日志）、R02-B5（assert 控制流） | done（先库后盘+失败上报/去重 FTS/ff_index 422/系列 409/日志/去 assert；4 用例） | |
| B7-NFO | R10-D1（控制字符）、R10-D2（0 分）、R10-D3（country 回退）、R10-B2（None 文本）、R10-B3（country_name 守卫）、R10-B5（语言表注释）、R10-B6（截断注释） | done（控制字符/评分 0/country 回退/None 文本 + 注释；4 新用例） | |
| B7-SCAN | R03-B2（头像失败保留旧值）、R03-B5（scan 响应摘要）、R03-B7（find_subs 日志）、R03-Q4（注释）、R03-Q5（import 上移）、R03-B3（重复规则删除）、R03-B4（异常审计） | done（头像保留旧图/scan 摘要/find_subs 日志/异常审计/import 上移/删重复规则） | |
| B7-PLAYBACK | R11-D1（封顶钳制）、R11-D5/D7（reason）、R11-B2/B3/B4/B9（死路径/归一/封面/vbitrate）、R11-B7（master CODECS docker 验证）、R11-B8、R11-Q2（决策矩阵表测）、R11-Q3、R11-D4（smoke 超时）、R12-B1（"transcode"）、R12-B5/B6/B7（caps_hash/缓存/等待） | done（不放大/DV reason/media 清理/caps_hash/FIFO/kill 等待 + 5 矩阵用例；R11-B7 经 R12 复核关闭） | 部分需 DOCKER |
| B7-EXTRAS | R08-Q1/Q2（归属纯函数单测+拆分）、R08-Q3（状态常量）、R08-B4（rowcount）、R06-Q4（job 状态机单测）、R09-Q2（planner fixture 单测） | done（归属纯函数/planner/job 状态机 13 用例 + ST_* 常量） | |
| B7-DECISION1 | R13-B4（sup 死路径）、R13-B7（parseVtt 格式）、R05-B6/B7、R07-B3、R10-B4、R02-Q6、R05-Q5、R08-D6、R11-B1/B10（无 bug 结论） | closed（R13-B4 sup 死路径不可达、R13-B7 parseVtt 有原生回退、R05-B6/B7 语义正确、R07-B3 无需区分、R10-B4 注释已明、R02-Q6 无未用 import、R05-Q5 良好、R08-D6 定位如此、R11-B1 契约已验、R11-B8 字段注释明确口径、R11-B9/B10/无注入面、R11-Q3 注记重探） | H-DECISION |

### B8 体验/性能/小重构批
| 组 | 成员 | 状态 | 备注 |
|---|---|---|---|
| B8-LIB | R04-D2（facets 口径文案）、R04-B5（演员直跳/全选文案）、R04-Q2/Q5（类型统一）、R04-B4（死样式）、R04-B2/B3（api 小修+单测） | done（facets 全库口径提示/演员直跳人物页/全选已加载/defaultSel/删死样式/api 头与上传解析；R04-Q5 关闭：字符串约定一致，改动收益低） | H-UI |
| B8-DETAIL | R05-D1（长度校验）、R05-D5（批量 PATCH）、R05-B5（定时器/delArm/catch）、R05-Q3（路由监听）、R05-Q6（错误截断） | done（PATCH 长度校验/播完 batch/离开页清理/路由 watch/错误截断） | H-UI |
| B8-PLAYER | R13-D2（内嵌默认字幕自动选）、R13-B8（控件显隐）、R13-Q3（撤销降级）、R13-B6（字体 MIME）、R12-D8（暂停 ping）、R12-D5（429 文案）、R14-D3（进度 keepalive）、R14-D4（调试口命名空间）、R14-B1（监听对称）、R14-B2（已注释，关闭） | done（内嵌默认字幕自动选/原生兜底隐藏外观/撤销降级/暂停心跳/429 文案/keepalive/调试命名空间/监听对称；R14-B2 关闭） | H-UI |
| B8-SETTINGS | R14-D1（首屏并行+懒加载）、R14-D2（fs 改名/移动预览）、R14-B3（样式去重）、R14-B4/R07-B4（console）、R06-D6（confirm 二选一） | done（首屏并行+懒加载/fs 预览确认/样式去重/console 清理/自定义删除弹层） | H-UI |
| B8-COLLECTIONS | R06-D1/B2（JOBS TTL/上限）、R06-D2（轮询降载）、R06-D3（ORDER BY）、R06-B6/B7（文案）、R06-Q3（sort_order 注释） | done（Job 修剪/轮询降载/ORDER BY/resumed 文案/sort_order 注释） | |
| B8-PERSONS | R07-D1（bio 语言）、R07-D2/D3（并发/限频）、R07-B1（person_exists）、R07-Q2（错误截断）、R07-Q4（a11y 随全局） | done（bio 语言跟随/10s 限频/person_exists/错误截断/图片 alt） | H-UI |
| B8-PERF | R02-D4（facets SQL）、R02-D6（封面批量）、R05-B2（批量轻查询）、R08-D2/B1（collect/classify）、R12-D6（probe-missing SQL）、R12-D7（versions 并发）、suggest N+1 | done（facets 轻列/封面窗口函数/成员批读/series 批读/collect 预取/classify 映射/探测预筛/并行探测/轻量 tags） | H-PERF 数字 |
| B8-SCAN-PERF | R03-D5（TMDB 重试退避）、R09-D3（EXDEV 兜底）、R14-D2（同 SETTINGS）、R01-Q6（fs_list 分页）、R09-Q5（preview 懒加载） | done（TMDB 重试退避/EXDEV 兜底/fs 分页/重负载 GET 懒加载；smoke 超时 8s） | |
| B8-CLEANUP | R02-Q5、R10-Q1/Q2/D4、R11-Q4、R13-Q5（subs 目录 env）、R06-Q3、R09-B3（清洗单源注释）、R13-B9、R03-Q4、R12-D4（MAX_TRANSCODES env） | done（store/playback/files/sort_order 注释；MAX_TRANSCODES、SIDECAR_SUB_DIRS、字体 MIME；console 清理） | |
| B8-DECISION2 | R04-Q3（URL/状态机大改）、R10-Q4（缩进）、R12-Q4/Q5（微优化）、R14-D5（路由懒加载→B9） | closed（R04-Q3 大改不做（收益/风险比低）、R10-Q4 缩进保持、R12-Q4/Q5 微优化保持、R14-D5 路由懒加载留 B9 拆分时顺带） | H-DECISION |

### B9 结构重构批（逐模块合回）
| 组 | 成员 | 状态 |
|---|---|---|
| B9-STORE | R02-Q1/Q3（拆分+DB_PATH 注入）、R02-D1（user_version 迁移，H-DATA） | 进行中（拆分完成：app/store/ 11 子模块 + 门面；R02-Q1 关闭（测试以 env 注入 DATA_DIR）；R02-D1 迁移框架待做，H-DATA 前置） |
| B9-STREAM | R12-Q1/Q3（会话/媒体/字幕/字体拆分+元数据落盘） | pending |
| B9-SCANNER | R03-Q1/Q3（拆分+增量扫描，H-DESIGN） | 拆分 done（app/scanner/ 6 阶段模块+门面，pytest/smoke 绿）；R03-Q3 增量扫描待设计 |
| B9-PLAYBACK | R11-Q1（决策/命令拆分） | pending |
| B9-MOVIES | R05-Q1/Q2/B4（CRUD/blob/batch + scope 单源） | pending |
| B9-FILES | R09-Q1（planner/executor 拆分） | pending |
| B9-COLLECTIONS/PERSONS | R06-Q1、R07-Q1/Q3（composable/样式单源） | 进行中（usePolling 抽取并接入 Collections/Detail；R07-Q3 样式单源并入前端模块） |
| B9-PLAYER-UI | R14-Q1/Q5、R13-Q1/Q2（PlayerModal 拆分+样式约定） | pending |
| B9-VIEWS | R14-Q2、R04-Q1（Settings/Library 拆分）、R04-D6（scan 后台任务，复用 job 框架） | pending |

### B10 工程化收尾批
| 组 | 成员 | 状态 |
|---|---|---|
| B10-DEPS | R01-Q2（锁版本）、R01-Q5（static-ffmpeg extras） | pending |
| B10-DOCKER | R01-Q3（healthcheck/init）、Dockerfile npm ci | pending |
| B10-TESTS | R14-Q3/R04-Q6（eslint+前端测试框架）、R03-Q5 等已随批补测 | pending |
| B10-IMPORT | R01-Q1（导入期副作用迁 lifespan） | pending |
| B10-CI | 无远端仓库，CI 记 closed（有远端再加） | pending |

## 4. 进度 Log（倒序）

- 2026-09-16：**B9-R4 完成**：scanner 拆为 classify/parse/match/persist/nfo_link/scan 六模块 + 门面；
  228→…（158 tests + smoke 29/29 全绿，pyflakes 无未定义/未用 import）。
- 2026-09-16：**B9-R2/R3 完成**：usePolling composable（Collections/Detail 接入）；
  store 1935 行拆成 app/store/ 11 子模块 + 门面重导出（调用方零改动），pytest 158 + smoke 29/29。
- 2026-09-16：**B8 合回 main**（tag p2-b8-ux，用户 H-UI 通过）；B9 设计说明送审。
- 2026-09-16：**B8 代码完成**：4 提交；pytest 158、smoke 29/29、npm build/test；待 H-UI。
- 2026-09-16：**B7 合回 main**（tag p2-b7-consistency）；B5a+B6+B7 累计清 ~75 项 P2。
- 2026-09-16：**B7 全部组完成**：DELETE/FAIL/NFO/SCAN/PLAYBACK/EXTRAS + DECISION1 closed；
  pytest 147 passed、smoke 30/30、npm build/test 绿。
- 2026-09-16：**B6 合回 main**（tag p2-b6-security，用户确认）；B7 开工。
- 2026-09-16：**B6 四组完成**：REQ（安全头+CSP/SPA 边界/未知 api 404/health/blob inline）、
  INPUT（symlink/ids/tmdb_id/caps/LIKE/VobSub 415）、CRYPTO（blake2/特性自检）、
  BEHAVIOR（attach force/删除两步确认）。H-SEC 证据：curl 头四件套齐全、
  /api/nope → 404 JSON、`..%2Fdist-x%2Fsecret.txt` 返回 SPA 页而非同级文件。
- 2026-09-16：**B5a 10/10 done**：R03-B1、R03-D6、R10-D6、R09-D4、R05-D2、原子写（R03-B6/R05-B8/R10-B1/R13-D4）、R12-D2、旧口删除（R09-Q4/R12-B3）、R04-D3、R06-D4；pytest 99 passed，smoke 30/30，前端 build/test 绿。
- 2026-09-16：台账建立；B5a 开工（分支 fix/b5a-harden）。
