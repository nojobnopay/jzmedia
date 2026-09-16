# R07 人物页/头像 — status: done

## Scope
- `app/routers/persons.py`（41 行逐行）
- `frontend/src/views/Person.vue`（137 行逐行）
- 交叉：`store.get_person/update_person_bio/upsert_person/persons_missing_avatar`（R02）、`scanner._sync_jobs/save_person_avatar`（R03）

## 目标（用户三问）
1. 功能设计是否合理？更好的方案？
2. 代码是否符合要求？废弃代码/漏洞（带 file:line）？
3. 质量差距（P0/P1/P2 分级，带证据）？

## 进度 Log（倒序追加）
- 2026-09-16 done：两文件精读；核对纯本地承诺、负缓存、并发触发。
- 2026-09-16 doing：开始。

## 结论

### 1. 功能设计

**整体判断：设计正确且克制。** `GET /api/persons/{id}` 纯本地瞬时返回（`persons.py:21-29`，`store.get_person` 无网络调用）；bio 渐进填充只在 `biography` 为空且 `bio_fetched_at==0` 时由前端后台触发一次（`Person.vue:90-100`）；空结果也盖戳形成负缓存（`store.update_person_bio`，`store.py:643-653`）；头像 `'-'` 语义（确认无照片）与 `profile_tmdb_path` 变化感知都有完整闭环。无阻断性问题，以下为改进项：

- **D1（P2）bio 语言硬编码 `zh-CN`，忽略设置页的 `TMDB_LANGUAGE`。** `_fetch_and_cache_bio`（`persons.py:11-14`）固定先 `zh-CN` 再 `en-US`，不走 `config.effective_tmdb_language()`。若用户把刮削语言改成 `ja-JP`，人物简介不会跟随。建议：主语言用 effective 值，若结果为空再用 `en-US` 兜底（当前语义保留）。
- **D2（P2）bio 自动补齐无并发保护，多标签页/快速刷新可重复打 TMDB。** 无“请求中”去重（前端 `bioLoading` 仅是本地 UI 状态，`Person.vue:90-99`；后端 `POST /refresh` 无节流）。同一人物在两个标签页同时打开会发两次请求。成本低、影响小；可接受，若要收紧可加 `bio_fetching_at` 短锁或用 `bio_fetched_at` 先占位。
- **D3（P2）`POST /{id}/refresh` 无频率限制**（`persons.py:32-40`）：设置页/人物页按钮可连点，每次都打两次 TMDB（zh 空时再 en）。建议前端按钮 debounce（当前 `refreshBio` 无 disabled 态，`Person.vue:105-114`）。

### 2. 代码符合度（废弃代码/漏洞）

- **B1（P2）`refresh_person` 的 404 判断与 `GET` 的 404 语义不一致。** `GET` 用 `store.get_person` 判空（`persons.py:26-28`），`POST refresh` 用 `store.get_person_raw`（`persons.py:35-36`）——对只有 persons 行、无作品的孤立人物，两者都放行/拦截一致，无实际差异；但两个函数对“存在”的定义不同（`get_person` 会返回无作品列表的 dict，只要有行就非 None），建议统一用一个 `person_exists()`。
- **B2（P2）无输入校验：`tmdb_id` 由 FastAPI 保证 int，但其值域不校验（负值/超大）**，会直接落到 SQLite 查询（负值返回 404，超大 int 在 SQLite 绑定抛 `OverflowError` → 500）。与 R06 B3 同源，建议路由层统一 `if not 0 < tmdb_id < 2**31: 404`。
- **B3（P2）`_fetch_and_cache_bio` 对“第二次英文请求也失败”无区分**：异常直接抛给路由转 502，`bio_fetched_at` 不写 → 下次访问重试（合理）；但若 zh 请求成功且 bio 非空、`birthday/place_of_birth` 为空，不会尝试英文（`birthday` 是语言无关字段，无需重试）。逻辑正确。
- **B4（P2）`Person.vue:96` 使用 `console.warn`**——全前端唯一的 console 调用（其余静默 `catch {}`）。生产代码应统一日志策略；此外 `refreshBio` 的 `setTimeout` 未在 `onUnmounted` 清理（`Person.vue:113`），快速切页会有 setState-after-unmount。P2。

### 3. 代码质量差距（不符合量产要求）

- **Q1（P2）`Person.vue` 的 `load()` 内联了“首屏本地 + 后台补齐 + 路由竞态防护”三段逻辑（约 25 行）**，可读性尚可，但与 Detail/Collections 的轮询/刷新模式重复（三处各写一遍），建议 composable 化（同 R06 Q1）。
- **Q2（P2）`err` 提示包含原始 HTTP 错误体**（`Person.vue:102`：`'人物不存在：' + e.message`），而 `api.js:12-13` 把响应正文拼进 message，长正文会撑破页面（同 R05 Q6）。
- **Q3（P2）样式与 Detail 大量重复**（`.hero-inner/.card-block/.work-wall/.poster-wrap` 等），未抽公共样式/组件；当前两页可维护，三页以上会明显重复（记 99 汇总的“前端样式单源”项）。
- **Q4（P2）无标题/无障碍细节**：`<img>` 无 alt（Person/Detail 均如此），属全前端共性。

### 交叉引用
- 头像文件缺失/`'-'`/`profile_tmdb_path` 变化的下载与清除逻辑归 R03（已审，发现“下载失败清空旧头像”问题，与本单元负缓存设计互补）。
- `persons_missing_avatar` 驱动的后台补头像（`jobs.backfill-meta`、`refresh_movie`）归 R03/R12。
- 人物页作品列表排序/去重（同 tmdb 去重、年份倒序）由 `store.get_person` 保证，R02 已确认正确。
