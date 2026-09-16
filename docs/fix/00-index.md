# jzmedia 修复计划 — 总纲（唯一真相源）

> 本文件是修复工作的进度真相源。任何中断（含上下文压缩）后：**先读本文件 → 看 §4 当前指针 → 继续**。
> 基线：commit `7a1b6bc`（v0.7.0）；分支策略：`fix/b1-scan` 等批次分支 → 每 fix 一提交 → 整批验证后合回 `main`。
> 来源：`docs/review/99-final-report.md`（P0=0 / P1=11 / P2=238）。

## 1. 工作协议（每步必守）

1. **一批一分支**：`fix/b1-scan`、`fix/b2-consistency` …；基线始终从最新 `main` 开。
2. **一个 fix 一个提交**：commit message 固定 `fix(<review-id>): <一句话>`，正文附验证命令与结果（如 `Reviewed: R03-D2`）。
3. **DoD（完成定义）**：① 先有能复现问题的用例（红）→ 修 → 用例绿；② L0 门禁通过（后端 `compileall`+`import app.main`、前端 `npm run build`）；③ L1 全量单测绿；④ 涉及 API/扫描的跑 L2 smoke；⑤ 验证证据写入本文件对应行。
4. **验证不了的不许标 done**：播放链（R11/R12/R13）宿主无 ffmpeg，必须 docker 实测或明确标 `verified: code-only`。
5. **收工/中断前必须更新 §4 指针**（当前批、当前 fix、断点一句话），不许只在脑子里记。
6. **不合回不删除分支**；整批 L2 通过后 `git checkout main && git merge --no-ff fix/<batch>`。

## 2. 验证体系（三层网）

| 层 | 命令 | 覆盖 | 频率 |
|---|---|---|---|
| L0 | `.venv/bin/python -m compileall -q app && .venv/bin/python -c "import app.main"`；`cd frontend && npm run build` | 语法/导入/打包 | 每 fix |
| L1 | `.venv/bin/python -m pytest tests/ -q`；`cd frontend && npm test` | 纯函数 + 扫描规则 + 决策矩阵 | 每 fix |
| L2 | `.venv/bin/python scripts/smoke_api.py` | 起临时实例跑关键 API 场景 | 每批合回前 |

## 3. 修复清单（P1 全量 + 批次）

| Fix ID | 来源 | 批次 | 问题 | 状态 | 验证 |
|---|---|---|---|---|---|
| P1-02 | R03-D2 | B1 | `_SAMPLE_RE` 误杀含 Sample 正片 | **done** | tests/test_scan_rules.py::test_is_sample_*（13 passed） |
| P1-03 | R03-D1 | B1 | 剧集跳过实际入库污染海报墙 | **done** | tests/test_scan_rules.py::test_episode_not_inserted / test_clean_episodes_* |
| P1-04 | R03-D3 | B1 | 扫描不剪枝 #recycle/@eaDir/隐藏目录 | **done** | tests/test_scan_rules.py::test_scan_all_prunes_recycle_and_hidden |
| P1-05 | R08-D1 | B1 | clean-sidecars 误删正片库行 | **done** | tests/test_scan_rules.py::test_clean_sidecars_protects_tmdb_rows |
| P1-06 | R09-D1 | B2 | 移动目标与 missing 行撞车致“文件已移/DB 未改” | **done** | tests/test_files_organize.py（4 用例） |
| P1-07 | R12-D1 | B2 | prewarm 与在线播互踩会话目录 | **done** | tests/test_stream_sessions.py（4 用例）+ docker e2e（见 log） |
| P1-08 | R13-D1 | B2 | HDR+烧录丢弃 tonemap 且无提示 | **done** | tests/test_playback_plan.py::test_burn_hdr_flags_no_tonemap_even_with_hw |
| P1-09 | R01-B1 | B3 | UID/GID 文档与 compose 不一致 | pending | — |
| P1-10 | R01-B7 | B3 | 零日志 + 76 处静默吞异常 | **done** | tests/test_logging.py（5 用例） |
| P1-11 | R04-D1 | B3 | 库页无分页 >500 截断 | **done** | tests/test_pagination.py（4 用例）+ smoke 3 项 |
| P1-01 | R01-D1 | B4 | 无认证 + 破坏性 API 全开放 | **done** | tests/test_auth.py（8 用例）+ smoke 鉴权相位 4 项 |

批次定义：**B1 扫描正确性**（P1-02→03→04→05，有依赖顺序）；**B2 一致性/并发**；**B3 部署/可运维/可用性**；**B4 安全基线（可选 token，需先与用户确认交互形态）**。

## 4. 当前指针（中断恢复点）

```
批次：B4（fix/b4-auth）— P1-01 done，待合回 main
方案（用户确认）：env+设置页双通道（DB 优先）；只护写操作；401 前端弹一次存 localStorage；
未配置=完全开放；GET/直链免鉴权。
步骤：pytest 83 passed + smoke 30/30（含鉴权相位）+ npm build/test ok；待 merge → tag
断点：main.py `_auth_write` 中间件；设置页「访问控制」；api.js token+401 事件；App.vue 弹层
下一步：merge --no-ff fix/b4-auth → tag p1-b4-auth → 汇报（11/11 P1 全清）
```

## 5. 进度 Log（倒序）

- 2026-09-16：**P1-01 done**：写操作可选鉴权全链路——`config.SETTING_MAP` 增 `jzmedia_token`（DB 优先/env 兜底，通用化命名保留 TMDB_SETTING_MAP 兼容）；`main._auth_write` 中间件（只护 `/api` 写方法，`X-Api-Token`/Bearer，`hmac.compare_digest`，未配置全放行）；`GET/PUT /api/settings` 回脱敏状态并可写入；前端 `api.js` 自动带令牌 + 401 派发事件、`apiUpload` 带头、App.vue 令牌弹层（存 localStorage 后重载）、Settings「访问控制」区（保存即记住）；`.env.example`/README/AGENTS 同步；tests/test_auth.py 8 用例 + smoke 鉴权相位 4 项。
- 2026-09-16：**B3 批次合回 main**（--no-ff `d163835`，tag `p1-b3-ops`）。验证：L0（compileall/import/npm build）+ L1（pytest 75、node --test 7）+ L2 smoke 26/26。
- 2026-09-16：**P1-11 done**：store `list_movies/search_fts/_search_like` 支持 `offset`；`/api/movies`/`/api/search` 统一 limit 钳制（1–2000）+ offset + `has_more`（多取 1 条判定）；Library.vue 每页 60 + IntersectionObserver 无限滚动 + 「加载更多」兜底 + 请求序列防竞态；tests/test_pagination.py 4 用例；smoke 新增 3 项分页断言；pytest 75 passed；README 分页文档同步。
- 2026-09-16：**P1-10 done**：新增 `app/log.py`（setup_logging/get_logger，`LOG_LEVEL`，第三方降噪）+ main 启动/停止日志；关键静默点补痕：files 移动/恢复失败与回滚、scanner NFO 写入失败（单/多/回退三路）与扫描异常、stream master 写入/转码失败/prewarm 失败/字体 dump、store 序列化与 probe 瞬态、tmdb 海报下载、config 读库失败、media static 下载失败、transcode smoke；tests/test_logging.py 5 用例（caplog 断言）；README/AGENTS 同步 `LOG_LEVEL` 约定。
- 2026-09-16：**P1-09 done**：`docker-compose.yml` 增加 `user: "${UID:-0}:${GID:-0}"`（缺省 root 保持旧行为）；README NAS 步骤补 chown/组权限说明；AGENTS deploy 同步；`.env.example` 注释更新；`docker compose config` 验证三态（.env=1002→1002:1002 / 空→0:0 / 显式→4321:4321）；tests/test_deploy_config.py 3 用例。
- 2026-09-16：**B2 批次合回 main**（--no-ff `4ac85e5`，tag `p1-b2-consistency`）。整批验证：L0（compileall/import/npm build）+ L1（pytest 63、node --test 7）+ L2 smoke 22/22 + docker e2e 全绿（明细见下）。
- 2026-09-16：**P1-07 done + docker e2e 全绿**（jzmedia:v0.4.0 + 当前代码 + 两个 ffmpeg 合成测试片）：
  - A) prewarm(remux) 完成 → 点播命中静态成品（`backend=static/complete/finished`，master 可取）；
  - B1) 先播后 prewarm：`attached=True`、sid 相同、分片 `6/3 → 58/29` **不回退**（修复前 `26/13 → 22/11`）；关播后附着任务如实失败「在线会话中断」；
  - B2) 先 prewarm 后播：**复用同一会话**（reuse=True，分片 `2→44`）；关播返回 `detached:True` **不杀进程**，prewarm 继续运行（segments=31）。
  - 附带 P2 观察（未修，记 backlog）：remux(copy) 档 prewarm 进度 `expected=31` 按 4s 估算，实际受源关键帧影响（12 片）→ 进度条偏差；后续可对 vcopy 用「时长/实测片均长」估算。
- 2026-09-16：**P1-08 done**：`playback.plan` 在 burn 时不启用硬件 tonemap 并追加 `hdr_no_tonemap`（前端有人话文案）；`transcode.video_args` 补注释防静默忽略；pytest 59 passed。
- 2026-09-16：**P1-07 已复现（红）**：docker（jzmedia:v0.4.0 + 当前 app + 真实片源《大桥下面》只读挂载）——在线会话分片 `26/13` → 启动 prewarm 后 `22/11`（目录被清、双进程同写）。
- 2026-09-16：**P1-06 done**：`_collect_plans` 增加库内占用检查（`conflict_db_occupied`/kind db）；`_move_one`/`_restore_one` 目标被他人行占用时提前拒绝，并在库写失败时回滚 rename；Settings 增加「库内占用」计数/徽标与恢复状态文案；pytest 58 passed，前端 build/test ok。
- 2026-09-16：**B2 开工**：建分支 `fix/b2-consistency`。环境确认：用户 8080 服务在跑（勿动）；真实媒体在 `sample_media/`（332 个 mkv，仅 3 个非 0 字节，最小 2.96GB《大桥下面 1984》）；`jzmedia:v0.4.0` 镜像含 ffmpeg 7.1.5 可复用。
- 2026-09-16：**B1 批次合回 main**（--no-ff `d791def`，tag `p1-b1-scan`）；main 上 pytest 54 passed。整体验证：L0（compileall/import/npm build）+ L1（pytest 54、node --test 7）+ L2 smoke 22/22。
- 2026-09-16：**P1-05 done**：clean-sidecars 过滤 `tmdb_id` 非空行（已匹配行永不清理）；pytest 54 passed，全绿；L2 smoke 22/22 PASS；B1 批次验证完成。
- 2026-09-16：**P1-04 done**：`scan_all` walk 剪枝（隐藏目录 + `_SKIP_DIR_NAMES`，`SCAN_SKIP_DIRS` 可追加），文件级隐藏名也跳过；pytest 53 passed / 1 failed；README 环境变量表同步。
- 2026-09-16：**P1-03 done**：`scan_one` 剧集不再建行；新增 `POST /api/files/clean-episodes`（dry_run 默认，保护已匹配行）+ Settings 两步确认按钮 + README/AGENTS 同步；pytest 52 passed / 2 failed。
- 2026-09-16：**P1-02 done**：`_SAMPLE_RE` 收紧（禁止空格/任意词边界，whitelist 发布词 ≤3 个）；13 个样片/侧车用例绿；全量 49 passed / 5 failed（剩余为 P1-03/04/05 预期红）。
- 2026-09-16：验证基建提交（L0-L2 + B1 复现用例）；基线：回归网 pytest 35 / node 7 全绿，B1 复现 11 红。
- 2026-09-16：创建分支 `fix/b1-scan`；安装 pytest；建 `docs/fix/` 真相源；提交评审文档。
