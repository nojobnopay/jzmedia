# R09 文件整理/重命名/搬迁/恢复 — status: done

## Scope
- `app/routers/files.py`（712 行逐行：`_components/_collect_plans/_move_one/_organize/rename/relocate/preview/unmatched/clean-sidecars/missing/clean/restore-*`）
- `app/editions.py` 命名模板/冲突消解（R03 已读，本单元侧重使用方式）
- `frontend/src/views/Settings.vue` 整理/恢复段（191-269、727-840）
- **补入（R04 行动项，已在 R05 完成复核）**：`movies.py` 上传段（`/uploads`、`/movies/{id}/upload`）——结论见 `05-R05-detail.md` D2/B5

## 目标（用户三问）
1. 功能设计是否合理？更好的方案？
2. 代码是否符合要求？废弃代码/漏洞（带 file:line）？
3. 质量差距（P0/P1/P2 分级，带证据）？

## 进度 Log（倒序追加）
- 2026-09-16 done：files.py 全文精读 + Settings 整理/恢复 UI 交互确认；核对 UNIQUE(file_path) 与预览/执行一致性。
- 2026-09-16 doing：开始。

## 结论

### 1. 功能设计

**整体判断：dry-run 默认、预览重算（执行时服务端重新规划不信任预览列表）、`original_file_path` 只读不改、恢复“目标被占/源缺失跳过绝不上报覆盖”、疑似错配不自动加后缀等，是成熟且安全的设计。** 问题：

- **D1（P1，数据一致性）目标路径与“库内存在但磁盘缺失”的行撞车时，文件已移动但 DB 更新失败。** `movies.file_path` 有 `UNIQUE` 约束（`store.py:17`）；`_move_one` 只检查磁盘 `os.path.exists(dst)`（`files.py:392`），不查 DB 占用。场景：A 片整理目标恰好等于“已删盘未清理”的 B 行路径（`/api/files/clean` 之前常见）→ `os.rename` 成功 → `store.update_movie_local` 抛 `sqlite3.IntegrityError` → 被 `except` 吞成 `status="error"`（`files.py:454-455`）→ **磁盘已移动、DB 仍指旧路径**，A 变成 missing 行、B 仍挂在目标路径。同样风险存在于 `_restore_one`（`files.py:669-671`）。修复：执行前统一查 `store.get_by_path(to)`（存在且非本行 → conflict），或捕获 IntegrityError 后反向 `os.rename(dst, src)` 回滚并如实上报。严重度依据：由代码路径确认（未做运行时复现），恢复需重扫/手工。
- **D2（P2）同批次“链式改名/换位”不保证顺序。** `_collect_plans` 的磁盘占用判断对“同为 current_paths 的目标”放行（`files.py:235-236`），期望其他计划先把它挪走；但 `_organize` 按分组排序顺序执行（`files.py:490`）、无依赖排序。若 A→B、B→C 两个计划且 A 先执行，A 会 `conflict_disk_exists`（`files.py:392`）。重跑一次即可收敛，属可恢复的体验问题；建议按“目标是否为他人源路径”拓扑排序或两轮执行。
- **D3（P2）跨文件系统搬迁无兜底。** `os.rename` 跨设备抛 `EXDEV`（`files.py:397,430,669`），被记为 error。NAS 单卷内无感，多卷/共享文件夹场景会失败；建议 `shutil.move` 兜底（整树移动注意先建目录）。
- **D4（P2）`POST /api/files/clean` 缺 dry_run，与全文件“危险操作必先预览”的约定不一致。** `files.py:590-610`：不传 ids 即删除全部 missing 行且无二次确认；邻居 `clean-sidecars` 有默认 dry_run（`files.py:556`）。建议统一 `dry_run:true` 默认 + UI 两步确认（UI 目前走 `checkedMissing` 显式 ids，安全，但 API 面是敞开的）。
- **D5（P2）执行失败后不回滚、不聚合报告（跨行）。** `_organize` 顺序执行、单行失败不阻断（正确），但 `moved/total` 之外没有“为什么失败”的聚合（`files.py:490-491` 把 results 全量返回，UI 只显示 `moved/total`，`Settings.vue:761-762`）。建议 UI 展示失败原因分组（磁盘占用/源缺失/错误）。
- **D6（P2）整理预览不标注“源文件缺失”。** `_collect_plans` 会为磁盘上不存在的行生成计划（`_move_one` 时才发现 `skipped_missing_src`），预览页看着“可整理”实际点执行只得到跳过。建议预览过滤或在计划行标 missing。

### 2. 代码符合度（废弃代码/漏洞）

- **B1（P2）`_check_inside_root` 不解析符号链接，也不防“先建链再操作”的 TOCTOU。** `files.py:244-249` 仅 `normpath` 字符串约束。本地自用风险低；但若 MEDIA_ROOT 内存在指向外部的 symlink 目录，`relocate`/`restore` 会经其写出根外（`os.rename` 目标解析跟随链接）。建议对 dst 做 `os.path.realpath` 前缀校验（成本低）。
- **B2（P2）`only` 集合为字符串时不匹配、静默做全量。** `organize`/`restore-original`/`extras.collect` 都是 `set(body.get("ids"))`（`files.py:504,701`、`extras.py:45`），JSON 字符串 id 会导致“以为只操作选中项，实际全量”。（`_organize` relocate 分支下 `only` 为真但集合不匹配 → `rows = []` → `scoped={-1}` → 空计划，不危险；但 `restore` 会全量恢复、`extras.collect` 会全量收集，语义放大了操作范围。）修复：`{int(x) for x in ...}` 且非法值 422。
- **B3（P2）废弃/重复代码：**
  - `files.py:27` `_ILLEGAL` 与 `editions.py:55` 的 `_ILLEGAL` 两份实现；`_safe_component`（`files.py:49-53`）与 `editions.sanitize_tag`（`editions.py:70-73`）行为几乎一致（前者去非法字符+压空白，后者加 strip/限长）。建议单源到 editions。
  - `files.py:30-38` 惰性编译的 `_MOVIE_DIR_RE` 用 `global` 变量实现——可行但反模式（并发下双编译无害）；用 `functools.lru_cache` 或模块级编译（正则无依赖）更直观。
  - `files.py:548-575 clean-sidecars`/`590-610 clean` 两段逻辑几乎相同（scan→delete_movie→results），可抽 `_delete_rows(cands)`。
- **B4（P2）`_restore_one` 未约束 `to` 在 MEDIA_ROOT 内（防御性缺失）。** `to` 来自 DB `original_file_path`；当前写入路径保证了它合法，但若库被外部工具改坏（或历史导入），恢复会执行根外 `os.rename`。建议入口补 `_check_inside_root`。
- **B5（P2）`clean`/`clean-sidecars` 的 `delete_movie` 不清理 extras 悬挂引用**：与 R02 B2/R08 B5 同一问题，整理/清理路径再次触发。
- **B6（P2）`_collect_plans` 磁盘冲突的 `kind` 一律为 `"spec"`**（`files.py:238`）：实际是“磁盘占用/库外文件”，UI 靠 else 分支显示“磁盘占用”侥幸正确；`kind` 语义错误，建议 `"disk"`。
- **B7（P2）`_under` 前缀匹配基于未归一字符串**（`files.py:478-481`）：`./电影/...`、`电影//...` 等（库内一般不会出现）会漏判；`os.path.normpath` 后再比更稳。

### 3. 代码质量差距（不符合量产要求）

- **Q1（P2）`_collect_plans` 108 行、`_move_one` 68 行**，承载“规划/冲突消解/执行/DB 持久化/NFO/跟随后缀/花絮归位/目录收尾”全部逻辑。建议拆 `planner.py`（纯函数，可测：`_collect_plans` 只需 store 只读）与 `executor.py`（`_move_one`），并把冲突决策表变成数据（便于加规则）。
- **Q2（P2）规划算法无测试**：最小后缀消解、编号兜底、疑似错配判定、`core_of` 噪声表——全是高回归风险纯逻辑；`editions.py` 设计上已声明“可 node/py 单测”的意图（sanitize/纯函数），但项目无测试。建议优先为 `_stem_of`/`_collect_plans` 建 fixture 测试（不需要 DB 全栈，把 `store.list_movies` 注入替换即可）。
- **Q3（P2）`files.py` 14 处 `except Exception`、9 处 `except ... pass`**，与 R01 B7 同源，但本文件是全项目最需审计的文件（破坏性操作），建议所有吞异常处至少记录日志（含 from/to/id）。
- **Q4（P2）响应形状不统一**：organize 返回 `{plans, conflicts}/{results, conflicts}`，restore 返回 `{plans}/{results}`，rename 旧口 pop `mode` 伪装旧形状（`files.py:613-622`），clean 无 dry_run。旧口属于废弃兼容层——grep 前端已无 `/api/files/rename|relocate` 调用（`Settings.vue` 只用 `/organize`），**建议删除旧口（废弃 API 没有使用方）**。已确认：`frontend/src` 无 `files/rename`/`files/relocate` 引用。
- **Q5（P2）`GET /preview` 与 `unmatched` 都是全库重负载 GET**（`files.py:508-510,513-547`），Settings 页进入即打；`unmatched` 每次全表 + 正则。建议合并/缓存或按需加载（R14 前端的进入时机复核）。

### 交叉引用
- 本次 R05 已复核上传端点：`.part` 原子改名、目标约束、409 跳过策略正确；缺口（无扩展名/大小限制、rename 覆盖竞态、`nosniff`）记在 `05-R05-detail.md` D2。
- `_sibling_followers`/`_cleanup_old_dir`/`_resync_old_dir` 的行为已随本次精读确认：依赖 `is_sidecar`（R03 D2 的 `is_sample` 误报会改变“谁是花絮”的判定，间接影响跟随与清理）。
- `original_file_path` 首写约定（R02/R03）与本文件“搬迁永不改它”一致，核实无违反点。
