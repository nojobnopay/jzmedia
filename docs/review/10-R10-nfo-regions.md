# R10 NFO/产地映射 — status: done

## Scope
- `app/nfo.py`（45 行逐行）
- `app/regions.py`（116 行逐行）
- 交叉：`scanner.sync_nfos_for` 的三种落盘规则（R03 已审，本单元复核生成正确性）、`files._write_nfos`（R09）、`movies._compute_meta` 调用点

## 目标（用户三问）
1. 功能设计是否合理？更好的方案？
2. 代码是否符合要求？废弃代码/漏洞（带 file:line）？
3. 质量差距（P0/P1/P2 分级，带证据）？

## 进度 Log（倒序追加）
- 2026-09-16 done：两文件精读 + 全库 grep 验证“无硬编码大区”与 `CHINESE_SUB` 未使用。
- 2026-09-16 doing：开始。

## 结论

### 1. 功能设计

**整体判断：regions.py 的“存事实、派生视图、单源映射”设计完全达标**——`origin_country/origin_countries/original_language` 存原始 ISO/语言，`region` 仅派生；`REGION_ORDER` 驱动排序；`resolve()` 的 production_countries[0] → 语言回退策略清晰（`regions.py:83-92`）；全库 grep 证实除注释外无第二处硬编码大区词。NFO 侧字段编排符合 Kodi movie.nfo 主流格式（title/originaltitle/year/plot/rating/genre/country/tag/uniqueid/director/actor），ET 自动转义防止 XML 注入。问题：

- **D1（P2）NFO 未做 XML 非法控制字符清洗。** XML 1.0 不允许 `\x00-\x08\x0b\x0c\x0e-\x1f` 等字符，而 `ET.ElementTree.write` 原样输出。来源：`overview`（TMDB 偶发）、`title`（文件名解析）、`tags`（用户输入）。Kodi/Jellyfin 解析失败会导致该片元数据丢失。建议入库或写 NFO 前统一 `re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", s)`。
- **D2（P2）NFO 判定“评分 0 是否写出”不一致。** `tmdb_rating` 用真值判断（0 不写，`nfo.py:16`），`custom/douban` 用 `is not None`（0 写出，`18-21`）。语义上 0 分即无评分，建议统一为 `> 0`。
- **D3（P2）`origin_countries` 为空但 `origin_country` 有值时 NFO 无 `<country>`。** `nfo.py:24-25` 只遍历 `origin_countries`；老种子行可能只有主产地 ISO。建议回退到 `[movie.get("origin_country")]`（与 facets 的 primary 口径一致，R02）。
- **D4（P2）`resolve` 以 `production_countries[0]` 为主产地，合拍片顺序由 TMDB 决定。** 影响“大区”派生（如中美合拍首个是 US → 欧美）。业务上可接受（AGENTS 已声明该口径），但建议在 `resolve` 注释里明确“顺序非权威”，或引入“华语优先”启发式。属产品决策，记录不改。
- **D5（P2）`CHINESE_SUB` 常量未被任何代码使用**（`regions.py:42`，全库 grep 仅定义处）——死代码；华语细分展示实际由前端 `originName` 硬编码 8 国名完成（R05 D6），进一步说明前端重复实现。
- **D6（P2）`country_name` 未收录的国家回退 ISO 码，前端 Detail 又维护了一份 10 国映射（`Detail.vue:263`）**，两处不一致（如 `SE`：后端“瑞典”、前端显示 `SE`）。建议详情响应下发 `origin_country_name`，前端删除本地映射（同 R05 D6）。

### 2. 代码符合度（废弃代码/漏洞）

- **B1（P2）NFO 非原子写入。** `tree.write(nfo_path, ...)`（`nfo.py:45`）直接覆盖目标；进程中断/磁盘满会留下半截 XML。NFO 会被 Jellyfin/Kodi 常驻读取，建议 `.tmp` + `os.replace`（与 R03 B6/R05 B8 同一类问题，可统一封装 `atomic_write`）。
- **B2（P2）`write_movie_nfo` 对 `None` 文本依赖 ElementTree 的宽容行为。** `movie.get("title","")` 在列值为 SQL NULL 时返回 `None`（`_row_to_dict` 不补默认值，`store.py:1052-1060`），`ET` 会写成空元素而不报错——不会崩，但会静默产生 `<title />`；建议 `str(x or "")` 显式化。
- **B3（P2）无输入校验的 `country_name(code)`：`COUNTRY_NAMES.get(code.upper())`；code 非 str（如 int）会 `AttributeError`**（`regions.py:95-98`）。调用方均传 str，风险低；建议 `str(code or "")`。
- **B4（P2）`REGION_ORDER`/`REGION_UNKNOWN` 被 `files._REGION_SET`、`store._structured_where`、`get_facets` 引用**，耦合面合理，无违反单源约定的重复定义（已 grep 验证）。
- **B5（P2）`LANG_FALLBACK` 语言覆盖有限**（`regions.py:59-63`，10 种）；无 production_countries 且语言不在表内 → `primary=""` → `region=未知`。TMDB 基本都会给 production_countries，实际影响小；可在注释说明。
- **B6（P2）`normalize_tags`：先截断 20 再 dedup**（`regions.py:110-112`）——两个不同的长标签截断后可能重名（如 `AAAAAAAAAAAAAAAAAAAB` 与 `AAAAAAAAAAAAAAAAAAAC` → 都变 `AAAAAAAAAAAAAAAAAAAA`），先截断后 `seen` 判断会去重第二个（合理）但第一个保留的是截断值。行为可接受；建议注释。

### 3. 代码质量差距（不符合量产要求）

- **Q1（P2）NFO 无字段注释与格式版本说明**：45 行函数逐个 `SubElement`，新增字段时无“Kodi 字段对照”注释（`customrating`/`douban_rating` 是自定义扩展，Kodi 会忽略——已在 README 说明，但代码里没有）。建议加一行表头注释（字段→Kodi 用途）。
- **Q2（P2）`sync_nfos_for`（R03）与 `write_movie_nfo` 的职责边界模糊**：“写哪些文件”在 scanner，“怎么写”在 nfo；本单元确认职责划分正确，但 `sync_nfos_for` 的 150 行仍应拆（R03 Q1）。
- **Q3（P2）无 NFO 生成测试**：字段缺失/None/控制字符/多版本写法均无自动化验证。建议把 `write_movie_nfo` 做成纯函数（输入 dict → 输出 bytes），即可用 tmp 目录做极简测试。
- **Q4（P2）`ET.indent` 输出两空格缩进**：NFO 会被部分工具做 diff/比对，缩进变化会产生无意义漂移；可接受（无外部 diff 工作流），记录不改。

### 交叉引用
- NFO 落盘三种规则的正确性（独占/多版本/共享）已在 R03 复核：`scanner.sync_nfos_for` 判定 `foreign` 与 `related` 逻辑正确；本单元确认生成内容正确（除上述 D1-D3 小项）。
- `country_name` 的输出同时被 facets（`store.get_facets`）与 NFO 使用，口径一致；前端 `Detail.vue` 的重复实现是唯一的偏离点（R05 D6）。
- `normalize_tags` 是 tags 写入的唯一入口（PATCH/batch/scan 均调用），R05 已确认调用完备。
