# R06 合集/系列推荐/top-up — status: done

## Scope
- `app/routers/collections.py`（247 行逐行）
- `frontend/src/views/Collections.vue`（271 行）、`frontend/src/views/CollectionDetail.vue`（92 行）
- 交叉：`store` 合集族函数（R02 已审）、`scanner.refresh_tmdb_id_fast`（R03）

## 目标（用户三问）
1. 功能设计是否合理？更好的方案？
2. 代码是否符合要求？废弃代码/漏洞（带 file:line）？
3. 质量差距（P0/P1/P2 分级，带证据）？

## 进度 Log（倒序追加）
- 2026-09-16 done：collections 路由 + 两个前端视图精读；核对 job 生命周期/幂等/成员粒度。
- 2026-09-16 doing：开始。

## 结论

### 1. 功能设计

**整体判断：海报粒度成员 + 系列推荐“纯本地只读、接受才写” + backfill 协作式取消 + top-up 服务端重算差集，是清晰且正确的产品设计。** 问题：

- **D1（P2）backfill 任务是全局单例 + 内存态 + 无生命周期管理。** `_JOBS`（`collections.py:14-15`）只允许一个 running job，`POST /suggest/backfill` 命中 running 时直接返回该 job（忽略请求的 limit/force，`collections.py:69-72`）；完成的 job 永久驻留内存（含 failed 列表）；进程重启丢失（注释已声明“重启丢失可重跑”）。自用可接受，但建议给 `_JOBS` 加上限/TTL（保留最近 N 个）并在响应里说明“已复用运行中任务”。
- **D2（P2）补全进度轮询每 2s 触发一次重负载 `/api/collections/suggest`。** `pollStatus` 里 `await loadSuggest()`（`Collections.vue:156`），而 `suggest_series_collections` 是全表 Python 聚合 + 每系列一次查询（R02 D6/D5）。backfill 期间 2 秒一次全库扫描，会与后台刷新抢锁。建议轮询只拉 status，`done` 变化时才刷新推荐。
- **D3（P2）待排查列表 LIMIT 无 ORDER BY（选择不确定）。** `tmdb_ids_missing_collection`（`store.py:1537-1563`）`LIMIT ?` 无排序，SQLite 返回顺序取决于查询计划；失败项下次可能不被优先重试、成功项也可能因计划变化重复入选（有盖戳兜底，不会死循环）。建议 `ORDER BY m.id`。
- **D4（P2）前端一次只补 50 条且不自动续跑。** `Collections.vue:110` 固定 `limit:50`（后端允许 200）；用户看到“补全完成”后需再点，虽然 coverage 行会显示剩余待排查数，但“完成”文案易误解为“全部完成”。建议 done 且仍有 unchecked 时自动续跑或改文案。
- **D5（P2）`from-tmdb-series` 服务端不校验“该系列已收录”。** `already_collected` 只在前端提示（`Detail.vue:62`）+ hint 返回（`store.py:1374-1379`）；直接调 API 可重复建同名系列合集（不同名可绕过 UNIQUE name）。后续 `suggest` 会按 `tmdb_collection_id` 或同名过滤，影响有限。建议服务端在 `from_tmdb_series` 内查重返回 409。
- **D6（P2）合集删除用原生 `confirm()`、无统一二次确认组件**（`CollectionDetail.vue:75`），与 Library 的自定义弹层风格不一致；且删除合集仅删合集不删影片（语义正确，提示已说明）。

### 2. 代码符合度（废弃代码/漏洞）

- **B1（P2）重复 import / 函数内局部 import 风格混乱。** 模块顶部已 `import threading`（`collections.py:7`），`suggest_backfill` 内又 `import threading as _threading` + `import time as _time` + `import uuid as _uuid`（`collections.py:59-61`）；同一文件 `import scanner` 也在函数内（`collections.py:68`，防循环可接受，但 threading/time/uuid 无此必要）。建议统一顶部导入。
- **B2（P2）`_JOBS` 无界增长**（见 D1）。另外 `_JOBS[jid]["failed"]` 在 200 条上限内最多 200 项/ job，长期运行也仅是数量累积。P2。
- **B3（P2）整数入参缺上界，超出 SQLite 64 位会 500。** `_ids_from`/`create` 的 `int(x)` 不限制范围（`collections.py:31,191`），`movie_ids: [10**30]` 会在 SQLite 绑定时抛 `OverflowError` → 未捕获 500；`add_collection_members` 的 `IN (...)` 也无长度上限（>32766 个占位符会 OperationalError）。建议加 `0 < x < 2**63` 与列表长度上限（如 2000）。
- **B4（P2）`add_collection_members` 吞异常逐条 continue**（`store.py:1313-1323`）与 R02 B1 同源：库故障时返回 `added:0` 且无任何日志，接口看似成功。
- **B5（P2）`patch_one` 放行任意 `poster_path`**（`collections.py:165`）：虽 `posterUrl()` 前端只取最后一段拼 `/posters/`（`api.js:25`）不会造成注入，但 DB 里会存无意义值。建议仅允许 `/posters/...` 或干脆从 allowlist 移除（当前无 UI 使用）。
- **B6（P2）`suggest_backfill_status` 的“无 job_id 返回最近一个”**（`collections.py:119-123`）在多标签页场景可能拿到别人的任务状态；单用户可接受，建议文档标注。
- **B7（P2）`refresh_tmdb_id_fast` 返回的 jobs（海报/头像/NFO）被丢弃**（`collections.py:92`）是“轻量补全”的刻意选择，与注释一致；但意味着 backfill 后同系列影片的海报不会更新——若用户预期“补全=完整刷新”会失望。建议 UI 文案明确“仅补系列信息，不碰海报”（当前 `Collections.vue:25` 已有该提示，合格）。

### 3. 代码质量差距（不符合量产要求）

- **Q1（P2）`Collections.vue` 单文件混合“手工合集 CRUD / 系列推荐 / 补全进度 / top-up”四条流**（271 行），状态分散（`backfilling/bfProgress/jobId/pollTimer`）；可抽 `useBackfillJob()` composable（轮询逻辑与 Detail 的 prewarm 轮询、PlayerModal 的 TTL 轮询高度相似，全前端至少 3 处轮询各写一遍，建议统一 `usePolling`）。
- **Q2（P2）`CollectionDetail.vue` 无 loading/404 态**：`onMounted(load)` 无 catch（`CollectionDetail.vue:86`），合集不存在时页面空白 + console 报错。建议最小 error 分支。
- **Q3（P2）`sort_order` 字段写入恒 0、无排序 UI**（`store.py:1319`）——设计为未来预留，但 `get_collection` 里“全 0 则按年份兜底”的逻辑（`store.py:1239-1240`）在有人手工改库后会突然改变排序。属隐性行为，建议注释明确或移除未用字段。
- **Q4（P2）job 状态机缺测试**：取消/重跑/并发启动三个分支只靠人工验，建议至少补一个后端脚本级冒烟（项目无测试框架，属长期项，记 99 汇总）。

### 交叉引用
- `suggest_series_collections` 的纯本地只读承诺成立（无网络调用），并发正确性依赖 `store._lock` 串行，OK。
- `top_up_collection` 不信任客户端 id（服务端重算差集），设计正确（`store.py:1510-1527`）。
- dismissals 存 localStorage 与 AGENTS 约定一致，无服务端残留。
- 前端推荐卡片封面 `posterUrl(s.cover)`：`cover` 来自 `movies.poster_path`（形如 `posters/xx.jpg`），`posterUrl` 再取 basename 拼 `/posters/`，双前缀风险已由 basename 化解。
