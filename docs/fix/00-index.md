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
| P1-02 | R03-D2 | B1 | `_SAMPLE_RE` 误杀含 Sample 正片 | pending | tests/test_scan_rules.py::test_is_sample_* |
| P1-03 | R03-D1 | B1 | 剧集跳过实际入库污染海报墙 | pending | tests/test_scan_rules.py::test_episode_not_inserted |
| P1-04 | R03-D3 | B1 | 扫描不剪枝 #recycle/@eaDir/隐藏目录 | pending | tests/test_scan_rules.py::test_walk_prunes |
| P1-05 | R08-D1 | B1 | clean-sidecars 误删正片库行 | pending | tests/test_scan_rules.py::test_clean_sidecars_protects_* |
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
批次：B1（fix/b1-scan）
步骤：验证基建完成并提交；开始按顺序修 P1-02
断点：基线已跑（回归网 35+7 绿；B1 复现用例 11 红，见下）
下一步：P1-02 收紧 _SAMPLE_RE → 跑 tests/test_scan_rules.py → 提交
```

## 5. 进度 Log（倒序）

- 2026-09-16：验证基建完成（`requirements-dev.txt`、`tests/` 5 文件、`scripts/smoke_api.py`+`_smoke_app.py`、`frontend/tests/` + `npm test`）。
  - 基线：回归网全绿（pytest 35 passed；node --test 7 passed）；
  - B1 红：`test_is_sample_true[Samples.720p.mkv]`、`test_is_sample_false[4 例]`、`test_is_sidecar_keeps_sample_titled_movies`、`test_episode_not_inserted`、`test_scan_all_prunes_recycle_and_hidden`、`test_clean_sidecars_protects_tmdb_rows`、`test_clean_episodes_*`（2）。
- 2026-09-16：创建分支 `fix/b1-scan`；安装 pytest 9.1.1；建 `docs/fix/` 真相源；提交评审文档（commit 见 git log）。
- 2026-09-16：评审完成（`docs/review/`，P1=11）。
