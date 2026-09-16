# R04 媒体库浏览/搜索/过滤（列表 API + Library 前端） — status: done

## Scope
- `app/routers/movies.py`：`GET /api/search`（15-33）、`GET /api/search/suggest`（36-40）、`GET /api/movies`（43-61）、`GET /api/facets`（64-67）
- `frontend/src/views/Library.vue`（1050 行逐行）、`frontend/src/api.js`、`frontend/src/prefs.js`
- 交叉：`store.search_fts/list_movies/get_facets/suggest_*`（R02 已审）

## 目标（用户三问）
1. 功能设计是否合理？更好的方案？
2. 代码是否符合要求？废弃代码/漏洞（带 file:line）？
3. 质量差距（P0/P1/P2 分级，带证据）？

## 进度 Log（倒序追加）
- 2026-09-16 done：Library.vue 全文 + api.js/prefs.js + 列表 API 段精读；核对 URL 同步/联想竞态/分页。
- 2026-09-16 doing：开始。

## 结论

### 1. 功能设计

**整体判断：前端结构清晰、多选交互（Plex 式）与上传二步流程设计好**；URL 同步 + 分享、facet 内 OR/跨维 AND、国家选后大区让位（`Library.vue:377`）等细节考虑周到。设计问题：

- **D1（P1）库页无分页/无加载更多，超过 500 部直接截断。** `load()` 请求 `/api/search?<qs>` 不传 `limit`（`Library.vue:390-394`），后端默认 500（`movies.py:16`），列表全量渲染且无滚动加载/翻页控件（全文无 scroll/limit 相关逻辑）。影片总数 >500 时第 501 部起不可见、不可搜索到（除关键字命中）。对 NAS 媒体库（常见数千部）这是硬伤。方案：游标分页（`limit/offset` 或 `updated_at+id` 复合游标）+ 无限滚动；后端 `/api/search` 同时补 `limit` 上限。
- **D2（P2）facets 是“全库计数”而非“当前筛选结果计数”。** 前端 `loadFacets()` 无参数（`Library.vue:492-495`），后端 `/api/facets` 无过滤参数（`movies.py:64-67`）。选中“日本”后类型/年代 chip 的计数仍是全库数字，用户会误读为筛选后数量。产品上可接受（Plex/Emby 也有全库计数），但建议 UI 加“全库计数”提示或在筛选激活时改走带过滤的轻量计数端点。
- **D3（P2）每次筛选触发两次相同请求（URL watcher + 显式 load）。** `applyAndLoad()` 先 `syncUrl()`（`router.replace`）再 `await load()`（`Library.vue:395-398`），而 `watch(() => route.query, ...)` 又执行一次 `readUrl(); load()`（`Library.vue:977`）。实测逻辑：chip 一次点击 = 2 个 `/api/search` + 2 次渲染。建议 watcher 内加来源标记或只保留 watcher 一条路径。
- **D4（P2）`load()` 无错误处理、无请求序列保护。** `const d = await api(...); items.value = d.items`（`Library.vue:390-394`）：失败会 unhandled rejection 且旧列表静默保留；快速连点筛选时旧响应可能后到覆盖新响应（`fetchSuggest` 有 `suggestSeq` 防竞态，`load` 没有，对比 `Library.vue:420-423`）。建议统一 seq 守卫 + 错误条。
- **D5（P2）搜索联想把演员名当关键词搜索影片**（`pickRow` 对 person 用 `row.v.name` 作 q，`Library.vue:449-458`），依赖 FTS 里 `person_names` 命中。演员重名/改名情况下可能漏；但 FTS 未命中会走 LIKE 兜底（R02），可接受。更好的方案：按 `tmdb_id` 查演员作品（库里已有 person 页接口），点击演员直接跳 `/p/{tmdb_id}`（信息更准）。
- **D6（P2）上传与扫描的 120s 前端超时与后端同步阻塞不匹配。** `api()` 默认 120s abort（`api.js:2`），`POST /api/scan` 大库会超时（R03 D4），用户看到“请求超时，后台可能仍在处理”（`api.js:18`）但 `scanning=false` 已复位、可再次点击触发并发扫描（`Library.vue:665-679`）。与 R03 D4 同一根因，建议后端任务化。

### 2. 代码符合度（废弃代码/漏洞）

- **B1（P2）`GET /api/search` 不钳制 `limit`**（`movies.py:16` 直接传给 `store.search_fts`），而 `/api/movies` 钳到 `max(1,min(limit,2000))`（`movies.py:59`）。`?limit=10^9` 会把全表 JSON 化（本地 DoS/内存峰值）；`limit` 为负时 SQLite `LIMIT -1`= 无限制。修复：与 `/movies` 同款钳制。
- **B2（P2）`api()` 对 GET/DELETE 也发 `Content-Type: application/json`**（`api.js:7`）——无害但与 fetch 默认不符；`opts.timeout` 与 `fetchOpts` 的展开顺序正确（timeout 被消费不会传给 fetch）。`api()` 未处理非 JSON 成功响应（`r.json()` 对 204/空体抛错），当前所有端点都返 JSON，可接受。建议 `r.text()` 后按需 parse 或检查 204。
- **B3（P2）`apiUpload` 在 `xhr.onload` 的 JSON 解析失败时 `resolve({})`**（`api.js:62-63`）：调用方把 `r.status` 缺失当作 `stored`（`Library.vue:861`），可能把服务端异常静默标成“完成”。建议解析失败即 reject 或带 `_raw` 标记。
- **B4（P2）废弃/可疑代码：**
  - `Library.vue:626` `}async function joinCollection(cid) {` 花括号与函数声明同行，格式化残留（同文件其他函数均换行）。
  - `Library.vue:1005` `.selbar { display: none; }` 样式类在模板中无任何元素使用（`grep selbar` 仅此一处）—— 死样式。
  - `Library.vue:272` `let composing = false` 为普通变量（非 ref），与模板 `@compositionstart="composing = true"` 配合可用（模板内赋值的是 setup 作用域变量？——注意：Vue `<script setup>` 模板中赋值 `composing` 会编译为对 setup 变量的写入，可行），但混用 ref/普通变量易误改，建议统一。
  - `Library.vue:103` `.poster-wrap`、`.card` 无 scoped 定义（在全局样式），依赖 App.vue；跨文件隐式耦合（R14 复核全局样式归属）。
- **B5（P2）数据正确性：`selectAllVisible` 后 `batch-delete` 的 `ids` 是“当前可见 items”**（`Library.vue:506-510`）——语义正确；但 `items` 只有 500 条（D1），用户以为“全选”=全库时实际只选前 500。D1 的连带风险，值得在 UI 明示。

### 3. 代码质量差距（不符合量产要求）

- **Q1（P2）1050 行单文件承载：搜索联想 / facets 交互 / 多选批量 / 整片删除 / 上传 / 归档整理 6 个交互流。** 其中“上传 + 归档”占约 300 行（`Library.vue:681-960`），可抽成 `<UploadDialog>` 组件；多选浮动条与三个弹层可各自组件化。当前状态可维护但会随功能继续膨胀。
- **Q2（P2）`sel` 重置字面量重复 4 次**（`Library.vue:277,361-371,483,488`），新增过滤维度时易漏改。建议抽 `defaultSel()` 工厂 + `readUrl/buildParams` 的键表驱动（`buildParams` 已是表驱动，`readUrl`/重置不是）。
- **Q3（P2）上传流程状态机以零散 ref + 队列对象字段（`t.state/t.phase/t._hintTimer/t.scanStartedAt`）实现**，`_hintTimer` 挂在数据对象上、模板渲染依赖会 mutate 的对象（`upQueue.value` 内对象未用 reactive 包装，靠 `upQueue.value = staged` 触发渲染；循环中 mutate `t.state` 能不能触发更新依赖 Vue 对数组内对象的深度响应——`ref([])` 是深响应，OK，但依赖隐式行为）。建议用显式状态机/`reactive` 数组 + `computed`，并补单元可测性（该项目无测试框架，属长期项）。
- **Q4（P2）中英混杂的提示与 `upScanHint` 硬编码“10–30 秒”预期**（`Library.vue:738`）在慢网络/大批量下会误导（虽有动态平均，但文案固定）。建议改为纯动态估算。
- **Q5（P2）`readUrl` 与 `sel` 的类型不一致**：years/decades 存字符串（URL 与模板 select 需要），facet 是数字（后端 `_split_ints` 承接）。前端比较 `sel.decades.includes(String(d.value))`（`Library.vue:62`）靠显式转换维系；易错，建议统一字符串或数字之一。
- **Q6（P2）无前端测试/无 eslint/prettier 配置**（`package.json` scripts 仅 dev/build）。这是全前端共同问题，记 99 报告；R04 侧建议至少加 `vue-tsc`/eslint 基础检查。

### 交叉引用
- 搜索/FTS/LIKE 语义正确性归 R02（已确认参数顺序与转义正确）；本单元只确认调用方式。
- `POST /api/movies/batch`（批量已看/标签）语义与校验归 R05；`batch-delete` 影响面归 R05（`_movie_delete_scope`）。
- 上传端点 `/api/uploads`（`movies.py:542` 起，目标目录、同名跳过、文件大小限制、路径清洗）超出本单元 scope，归 R09/R05 复核时补（**行动项：R09 执行时把 `movies.py` 上传段纳入复核**）。
- `facets` 全库聚合性能归 R02（D4）。
