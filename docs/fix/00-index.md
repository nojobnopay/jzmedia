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
| P1-06 | R09-D1 | B2 | 移动目标与 missing 行撞车致“文件已移/DB 未改” | pending | — |
| P1-07 | R12-D1 | B2 | prewarm 与在线播互踩会话目录 | pending | — |
| P1-08 | R13-D1 | B2 | HDR+烧录丢弃 tonemap 且无提示 | pending | — |
| P1-09 | R01-B1 | B3 | UID/GID 文档与 compose 不一致 | pending | — |
| P1-10 | R01-B7 | B3 | 零日志 + 76 处静默吞异常 | pending | — |
| P1-11 | R04-D1 | B3 | 库页无分页 >500 截断 | pending | — |
| P1-01 | R01-D1 | B4 | 无认证 + 破坏性 API 全开放 | pending | — |

批次定义：**B1 扫描正确性**（P1-02→03→04→05，有依赖顺序）；**B2 一致性/并发**；**B3 部署/可运维/可用性**；**B4 安全基线（可选 token，需先与用户确认交互形态）**。

## 4. 当前指针（中断恢复点）

```
批次：B1（fix/b1-scan）已合回 main（merge d791def）并打批标签 p1-b1-scan
步骤：B1 收尾完成。下一批待定：B2（P1-06/07/08，播放链需 docker+ffmpeg 实测）或 B3（P1-09/10/11，宿主可验）
断点：等用户指令开下一批；开批时从最新 main 拉新分支（fix/b2-consistency / fix/b3-ops）
下一步：用户确认批次后：git checkout -b fix/<batch>
```

## 5. 进度 Log（倒序）

- 2026-09-16：**B1 批次合回 main**（--no-ff `d791def`，tag `p1-b1-scan`）；main 上 pytest 54 passed。整体验证：L0（compileall/import/npm build）+ L1（pytest 54、node --test 7）+ L2 smoke 22/22。
- 2026-09-16：**P1-05 done**：clean-sidecars 过滤 `tmdb_id` 非空行（已匹配行永不清理）；pytest 54 passed，全绿；L2 smoke 22/22 PASS；B1 批次验证完成。
- 2026-09-16：**P1-04 done**：`scan_all` walk 剪枝（隐藏目录 + `_SKIP_DIR_NAMES`，`SCAN_SKIP_DIRS` 可追加），文件级隐藏名也跳过；pytest 53 passed / 1 failed；README 环境变量表同步。
- 2026-09-16：**P1-03 done**：`scan_one` 剧集不再建行；新增 `POST /api/files/clean-episodes`（dry_run 默认，保护已匹配行）+ Settings 两步确认按钮 + README/AGENTS 同步；pytest 52 passed / 2 failed。
- 2026-09-16：**P1-02 done**：`_SAMPLE_RE` 收紧（禁止空格/任意词边界，whitelist 发布词 ≤3 个）；13 个样片/侧车用例绿；全量 49 passed / 5 failed（剩余为 P1-03/04/05 预期红）。
- 2026-09-16：验证基建提交（L0-L2 + B1 复现用例）；基线：回归网 pytest 35 / node 7 全绿，B1 复现 11 红。
- 2026-09-16：创建分支 `fix/b1-scan`；安装 pytest；建 `docs/fix/` 真相源；提交评审文档。
