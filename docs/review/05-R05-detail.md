# R05 详情/编辑/评分/批量/手动匹配 — status: done

## Scope
- `app/routers/movies.py`：`GET/PATCH /api/movies/{id}`（70-128）、`POST /api/movies/batch`（131-225）、`/files`（245-373）、`blob`（376-460）、上传（463-622）、单文件删除（625-648）、`_movie_delete_scope`（709-814）、`batch-delete`（817-939）、`poster-orig`（942-972）
- `frontend/src/views/Detail.vue`（1014 行逐行）、`frontend/src/ratings.js`、`frontend/src/components/ScoreBadge.vue`
- 交叉：`store.update_movie_local`（R02）、`fs._exec_delete_one`（R09）、`scanner.scan_one/attribute_extra`（R03）

## 目标（用户三问）
1. 功能设计是否合理？更好的方案？
2. 代码是否符合要求？废弃代码/漏洞（带 file:line）？
3. 质量差距（P0/P1/P2 分级，带证据）？

## 进度 Log（倒序追加）
- 2026-09-16 done：movies.py 相关段 + Detail.vue 全文 + ratings.js/ScoreBadge.vue 精读；核对 allowlist/路径约束/删除安全/上传落盘。
- 2026-09-16 doing：开始。

## 结论

### 1. 功能设计

**整体判断：PATCH allowlist 严格（无 mass assignment）、评分/标签服务端二次校验、上传 `.part` 原子改名、`_movie_blob_rel` 双重路径约束、batch-delete 强制 dry-run+confirm、`watched_at` 服务端派生，都是达标设计。** 问题：

- **D1（P2）PATCH 无 `title` 长度/空串校验、`overview_override` 无长度上限。** `movies.py:82-84` allowlist 收下 `title,overview_override` 后直接落库（仅 tags/ratings/watched/edition/spec 有校验）。巨型字符串（MB 级）会进 DB/FTS（+NFO），空标题会破坏列表与 NFO；建议 title ≤ 200、overview_override ≤ 20k 并拒绝空 title。
- **D2（P2）上传端点无扩展名/大小约束，且 `os.rename` 存在同名覆盖竞态。** `movie_upload`/`library_upload`（`movies.py:498-510,574-586`）：check→写 `.part`→`rename` 非原子（POSIX rename 覆盖已存在目标），两个并发同名上传可互相覆盖，与“409 绝不覆盖”承诺相悖（测试方法：并发两请求同名文件）。修复：`os.open(dst, O_CREAT|O_EXCL)` 占位或 rename 前再检查 + `link/renameat2(RENAME_NOREPLACE)`。另外任意类型文件都允许写入媒体库（含 `.html`），`blob` 虽带 `filename=` 强制 attachment，仍建议加 `X-Content-Type-Options: nosniff`。
- **D3（P2）详情页 PDF/文本预览依赖 `blob` 的 `Content-Disposition: attachment`，PDF 预览不可用。** `movie_blob` 用 `FileResponse(abs_p, filename=...)`（`movies.py:460`）必带 attachment；`<iframe>` 遇上 attachment 会触发下载而非渲染（`Detail.vue:211`）。文本预览走 `mode=text`（PlainTextResponse）不受影响。修复：`mode=pdf` 或对 `inline` 白名单（jpg/png/pdf）返回不带 filename 的 FileResponse。
- **D4（P2）“删除”按钮与“播放/预览”并排且无二次确认（非正片）。** 字幕/花絮/周边单击即删（`Detail.vue:111` + `movies.py:637-647`）。产品上可接受（文件可恢复自备份），但误触成本高；建议非正片也给 3s 撤销窗口或在删除前用 `delArm` 同样两步。
- **D5（P2）`onPlayEnded` 逐版本串行 PATCH**（`Detail.vue:458-469`）：N 个版本 = N 次请求 + N 次整片读取；应直接用 `POST /api/movies/batch`（一次）。属实现选择，非 bug。
- **D6（P2）国家名映射在前端硬编码 10 国**（`Detail.vue:263`），与 AGENTS“region 唯一来源 app/regions.py”约定相悖；后端 facets 已返回 `country_name`。建议改为详情响应带 `origin_country_name` 或在 regions 暴露只读接口。

### 2. 代码符合度（废弃代码/漏洞）

- **B1（P2）重复 FTS 重建（性能浪费，跨模块）。** `store.update_movie_meta` 末尾已 `resync_fts`（`store.py:600`），但多处再显式调用：`movies.py:127`（PATCH）、`scanner.py:770,793,868`、`jobs.py:62` 等。每次 = 1 次全行读 + person_names 聚合 + 2 条 FTS 写。建议：只在“未走 update_movie_meta”的路径（`link_person`/`copy_person_links`/直接 SQL）保留显式调用。
- **B2（P2）`batch_update` 每版本调用重型 `store.get_movie`**（`movies.py:196`：内含 persons/versions/collections 三条查询），500 片 × 多版本时 N+1 明显；只需 `tags` 与 `tmdb` 字段，建议 `get_by_path` 或轻量列查询。
- **B3（P2）`batch_delete_movies` 先删文件后删库行、无事务**（`movies.py:907-931`）：中途 OSError 会留下“文件已删/库行仍在”的不一致（重扫时靠 missing 清理自愈，但重扫前列表挂着死行）。建议先库后文件或收集失败统一报告（当前 `except OSError: continue` 静默）。
- **B4（P2）`_movie_delete_scope` 与 `movie_files` 的“同茎兄弟”判定第三次重复实现**（`movies.py:731-735,264-269`；另两处在 `scanner.is_extra`、`files._sibling_followers`，R03 Q2 已记）。行为已出现细微差异（删除范围不识别 `extras/` 目录内的散落花絮，靠 extra_set 补）。建议单源化为 `scanner.same_stem()`。
- **B5（P2）废弃/可疑代码：**
  - `movies.py:336-341` 用 `new Array(n).fill({})` 伪造 `audio/subs` 数组只为让模板判断长度，不如直接传 `audio_count/sub_count`（`Detail.vue:303-307` 也只读长度）——数据形状失真。
  - `Detail.vue:641,674-715` `delArm` 简单对象 + 手动 `delete delArm.value[name]`：Vue3 中对 `ref({})` 的属性删除可触发（Proxy），但写法规避响应式更干净的方式；`reloadFiles` 后 delArm 残留不过期。
  - `Detail.vue:253` `flashTimer`、`Detail.vue:547-553` `upScanTimer/upHintTimer`、`posterObjUrl` 在 `onUnmounted`（`Detail.vue:901-904`）未清理：上传中途离开页面，interval 与 XHR 继续跑、object URL 泄漏；`flashTimer` 可能 setState-after-unmount。修复：`onUnmounted` 调 `stopUpScanTicker()`、`upAbort && upAbort()`、revoke object URL、clear flashTimer。
  - `Detail.vue:819-828` `tmdbSearch` 无 `catch`：`finally` 复位后异常成为 unhandled rejection（`api()` 会 throw），页面无提示。
- **B6（P2）`PATCH` 的 `watched` 兼容 bool 与 int，但 boolean 在 JSON 里先被 isinstance 命中，OK；`edition/spec` 为 `None` 时静默改 `""`**（`movies.py:109-124`）——若客户端只想改 rating 不发 edition 字段则不受影响（allowlist 过滤），语义正确。
- **B7（P2）`movie_blob` 的 `mode=text` 用 `chunk.decode("gbk", errors="replace")`**（`movies.py:456-458`）：UTF-8 解码失败才 GBK，符合国情；但 64KB 截断可能切碎多字节字符（replace 兜底）。可接受。
- **B8（P2）`poster-orig` GET 带缓存副作用且无并发保护**（`movies.py:965-971`）：两个并发请求会同时下载同一文件写同一路径（裸 `open(dest,"wb")`，R03 B6 同源问题）。建议 `.part` + `os.replace`。

### 3. 代码质量差距（不符合量产要求）

- **Q1（P2）`movies.py` 972 行职责过载**：列表/搜索/facets + 详情/编辑 + 批量 + 文件清单/blob/上传/删除 + 删除范围 + 海报原图，至少应拆为 `movies.py`（CRUD）/`files_blob.py`（文件与上传）/`batch.py`。
- **Q2（P2）`_movie_delete_scope` 100 行内嵌 `_same_stem` + `foreign` 判定 + 独占/共享双分支 + rels 组装**（`movies.py:709-814`），`movie_files` 同构复制一遍（`movies.py:245-373`）；两者对“foreign/own_paths/stem”的实现已各自演化，属高耦合重复。抽公共 `scope.py`。
- **Q3（P2）详情页无 `route.params.id` 变更监听**（`Detail.vue:897-904`）：当前 UI 不存在 `/m/1 → /m/2` 同组件跳转，但一旦新增“相关影片”链接就静默显示旧数据。建议 `watch(() => route.params.id, load)`。
- **Q4（P2）`Detail.vue` 1300 行级组件（1014）混合播放决策展示/文件管理/上传/编辑/匹配/合集**，弹层与状态可抽出（`FileManager`、`TmdbMatch`）。与 R04/R14 同源，记 99 汇总。
- **Q5（P2）`ratings.js` 良好**（纯函数、无 `-` 占位符合约定）；`ScoreBadge` 简洁。无问题。
- **Q6（P2）错误提示直达 UI 但无分类**：`msg.value = 'xxx失败：' + e.message` 模式在 Detail 出现 ~10 次，`e.message` 内含 HTTP body 全文（`api.js:12-13` 拼 `${status} ${t}`），超长会撑破布局（长 TMDB 错误体）。建议 `api()` 截断 body（`Library.vue` 上传有 `.slice(0,300)`，其他没有）。

### 交叉引用
- `_exec_delete_one`/`_impact_for_delete` 归 R09（本单元只确认调用契约与 confirm 语义正确）。
- `PATCH tags → normalize_tags` 单源实现归 R10（regions.py）。
- `batch-delete` 对 extras 行的显式清理（`movies.py:924-931`）与 R02 B2 的 `delete_movie` 悬挂问题互补：此路径无残留，`fs.delete` 路径有。建议把 extras 清理下沉到 `store.delete_movie`，两条路径统一。
- `mode=text` 的 GBK 兜底与字幕抽取编码判断无关（R13）。
