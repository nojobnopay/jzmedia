# R03 扫描刮削入库 — status: done

## Scope
- `app/scanner.py`（1020 行逐行）、`app/tmdb.py`（69）、`app/editions.py`（165）、`scripts/find_subs.py`（138）
- 交叉：`store`（R02）、`nfo`（R10）、`regions.resolve`（R10）、`jobs.py`/`movies.py` 调用面

## 目标（用户三问）
1. 功能设计是否合理？更好的方案？
2. 代码是否符合要求？废弃代码/漏洞（带 file:line）？
3. 质量差距（P0/P1/P2 分级，带证据）？

## 进度 Log（倒序追加）
- 2026-09-16 done：全文精读 + 3 个运行时实验（episode 入库 / is_sample 误判 / 手动标题被重扫覆盖），均在 `/tmp/opencode/rev3*` 隔离环境用 `.venv/python` 复现。
- 2026-09-16 doing：开始。

## 结论

### 1. 功能设计

**整体判断：刮削管线设计成熟**——tmdb_cache 零请求复用、`short_candidates` 多级 fallback 查询、`used_q != title → needs_review`、花絮/样片分类树（`is_extra`/`extra_kind`/`strip_kind_affix`）、NFO 收敛唯一入口 `sync_nfos_for`、头像 `'-'` 负缓存都属优秀实践。以下为问题：

- **D1（P1）剧集“跳过”实际是入库为电影行，污染海报墙。** `scan_one` 对 `type=="episode"` 先 `store.upsert_movie_by_path(rel)` 再返回 `skipped_episode_v1`（`scanner.py:947-950`），`media_type` 仍是 `movie`（默认值），列表/统计均无过滤（`store.list_movies` 无 media_type 条件；Library.vue 展示全部行）。**实测证据**：`/tmp/opencode/rev3` 放入 `Some.Show.S01E01.1080p.mkv` 后 `scan_all()` 返回 `skipped_episode_v1`，库里出现行 `(id=1, title='Some Show', tmdb_id=None, media_type='movie')`，`library_stats.no_match=1`。README:13 承诺“剧集跳过”与实现不符。修复（二选一）：①不建行，另建 `skipped_paths` 轻表只防重扫；②建行时写 `media_type='episode'` 并在 `list_movies/facets/stats/搜索` 全面过滤（改动面更大）。
- **D2（P1）`is_sample` 正则误杀片名含 Sample 的正片。** `_SAMPLE_RE`（`scanner.py:27`）允许空格作为分隔符且后缀边界含空格/右括号，导致 `The Sample Movie (2024).mkv`、`Sample.This.2012.mkv` 被判样片。**实测证据**：`.venv/bin/python` 调用 `is_sample`，上述两例均 `True`，`is_sidecar=True` → `scan_one` 直接跳过、**永远不入库**（`scanner.py:938-941`）。修复：要求 `sample` 两侧为 release 分隔符（`.`/`-`/`_`）或行尾/扩展名前，禁止纯空格边界；或改成“release token 语境”判定（`[.\-_ ]sample[.\-_ ]?(1080p|720p|…)` 加行尾样本）。
- **D3（P1）扫描不跳过隐藏目录与 NAS 回收站。** `scan_all` 直接 `os.walk(settings.media_root)`（`scanner.py:1000`），无剪枝：Synology 常见 `#recycle`（含已删视频会被重新入库）、`@eaDir`、`.Trash-1000`、`.git` 等都会进库；`is_sidecar` 只按名字判定、对隐藏目录无效。修复：walk 时 `dirs[:] = [d for d in dirs if d not in {...} and not d.startswith('.')]`，并做可配置 ignore 列表。
- **D4（P2）扫描是同步 HTTP、无进度、无并发保护。** `POST /api/scan` → `scanner.scan_all()`（`movies.py:650-652`）同步跑完才回包；大库首扫（每片一次 TMDB 网络）可能数分钟到数十分钟，前端只能等；重复点击会并发两次扫描（无锁）。参考应用内已有的后台任务模式（`collections.py` backfill，`<job_id>/status/cancel`）改为后台任务 + 进度轮询。
- **D5（P2）TMDB 客户端无重试/退避/限速。** `tmdb.py:28-51` 每函数一次请求、`raise_for_status` 直接抛；无 429/5xx 重试与指数退避，无并发上限（头像线程池 8 并发打图片域名）。批量刷新/首扫遇 TMDB 抖动会大面积失败。建议 `httpx` transport retries + 429 退避 + 复用 Client（当前每次 `_client()` 新建，无连接池，`tmdb.py:9-16`）。
- **D6（P2）`pick_match` 无年份兜底结果也直接采信。** `pick_match` 年份不匹配时返回 `results[0]`（`scanner.py:277-285`）；`needs_review` 只看查询词是否缩水（`scanner.py:989`），因此“标题完全一致但年份差 ≥2”的错配不会标待确认。TMDB 搜索带 year 参数降低了概率，但年份缺失（guessit 未解析出）时仍可能错配。建议年份不符时也置 `needs_review=1`。

### 2. 代码符合度（废弃代码/漏洞）

- **B1（P2）重扫覆盖手动标题（实测复现）。** 无匹配行的处理 `update_movie_meta(mid, title=parsed["title"], year=...)`（`scanner.py:953-954`）不区分手动标题。**实测证据**：`/tmp/opencode/rev3b` 中无匹配影片手动 PATCH `title="我手动改的标题"` 后重扫，标题被改回 `Some Movie`。修复：仅当标题为空/等于上次解析标题时写入，或走 `copy_tmdb_to_movie` 同款 `old_title` 保护；剧集分支（`scanner.py:949`）同理。
- **B2（P2）头像写入会在下载瞬时失败时清空既有头像。** `_sync_jobs._fetch`：远端 profile 变化时先删本地旧头像（`scanner.py:395-400`），随后 `save_person_avatar` 失败返回 `''`，仍以 `avatars[pid]=''` 调 `upsert_person` → `UPDATE persons SET avatar=''`（`store.py:614-615`），旧头像丢失直到下次成功。修复：只在 `avatar` 非空或明确“无照片”时覆盖，失败保留旧值。
- **B3（P2）废弃/冗余代码：**
  - `_KIND_WORDS` 中 `interviews?` 规则重复两行（`scanner.py:60-61`），后者永不可达。
  - `init_db()` 被 `scan_all()` 再次调用（`scanner.py:997`），触发一次全量 FTS 重建（R02 D3），纯浪费。
  - `editions.py` 中 `(加长收藏版|加长版)` 与 `(加长|extended)`（`18,26`）、`(特别版|special.edition)` 与 `(special)`（`25,31`）前后重叠，后者部分不可达；`sanitize_edition/sanitize_spec` 旧名别名（`76-78`）全库无引用（grep 仅剩定义，R09 复核后可删）。
  - `_ROMAN` 不含 XI/XII/XIII（注释解释 V/X 歧义，合理），但 `normalize_title` 的罗马数字表是硬编码小表，建议注明局限（P2）。
- **B4（P2）异常吞噬密度高。** `scanner.py` 24 处 `except Exception`、8 处静默 `pass`；关键路径如 `sync_nfos_for` 兜底 `except Exception: return {"ok": False, ...}`（`scanner.py:681-683`）会把 OSError（磁盘满/权限）与逻辑错误一并吞掉且无日志，用户只看到 NFO 没写。与 R01 B7 同源。
- **B5（P2）`scan_all` 把所有结果塞进 HTTP 响应**（`movies.py:652`）：万级文件时响应对内存/浏览器都是负担；应返回汇总 + 错误列表。
- **B6（P2）`save_person_avatar` 存在性检查与下载非原子**（`scanner.py:365-374`）：并发扫描/刷新同人物时可能重复下载覆盖。影响小，但符合“量产要求”应加临时文件 + `os.replace` 原子落盘（`tmdb.download_poster` 直接 `open(dest,"wb")` 半成品文件，`tmdb.py:65-66`，中断会留下损坏 jpg 且被后续 `os.path.exists` 当成有效缓存——真实风险，P1 边缘）。建议下载到 `.tmp` 再 `os.replace`。
- **B7（P2）`find_subs.py` 只读设计正确**（`file:...?mode=ro`），仅 `except Exception: side=[]` 静默；可接受。

### 3. 代码质量差距（不符合量产要求）

- **Q1（P2）`scanner.py` 1020 行混合“分类判定 / 解析 / 匹配 / 落库编排 / NFO 编排 / 头像线程池”6 类职责**，且 `sync_nfos_for` 单函数 150 行 6 层缩进（`scanner.py:533-683`）。建议拆分：`classify.py`（is_extra/is_sample/extra_kind）、`parse.py`（guessit/normalize/short_candidates）、`match.py`、`persist.py`。
- **Q2（P2）重复的“同类判定”逻辑散落三处**：`scanner.is_extra` 目录规则、`EXTRAS_DIR_NAMES`、`files._sibling_followers` 的 stem 前缀规则、`movies._movie_delete_scope._same_stem`（`movies.py:731-735`）各自实现一遍“同茎兄弟”语义，行为已略有差异（`_sibling_followers` 支持空格，`_same_stem` 支持同样的四种分隔符但独立实现）。建议单源化。
- **Q3（P2）`scan_all` 是“全量重建式”扫描**：每次全走磁盘 + 对无匹配行重复 TMDB 搜索（有 tmdb_id 的跳过），无 mtime 缓存/增量。对 NAS 大库，日常“重扫”成本高；建议记录扫描根 mtime 或文件 inode/mtime 表做增量（长期项）。
- **Q4（P2）命名与注释质量**整体高（含设计缘由），但存在过期描述：`scanner.py:3` 注释“默认永不自动刷新”与 `refresh` 存在但正确；`movie.nfo` 规则注释准确。`main.py` 的 `phase2` 是 R01 已记的另一处。
- **Q5（P2）`parse_filename` 每次都 import editions**（`scanner.py:267`）函数内 import 属循环依赖规避，可接受；但 `parse_filename` 未把 `stack`（`split_stack`）用于返回时的 `cd1` 归一说明，阅读成本高。

### 交叉引用
- FTS：`scan_one` 匹配成功路径经 `apply_*` 内部 `resync_fts`（`scanner.py:770,793`），`no_match`/episode 路径不写 FTS——由于 `update_movie_meta` 自带 `resync_fts`（`store.py:600`），实际都会同步，无遗漏。
- 花絮归属算法正确性（`attribute_extra`）与 `delete_movie` 悬挂引用（R02 B2）相互印证：重扫可自愈，但删除后至重扫前不可见。
- NFO 落盘三规则的边界（共享目录判定 `foreign`）设计合理，正确性归 R10 复核。
- `regions.resolve` 调用点唯一（`scanner.py:305`），符合“唯一来源”约定。
- 头像/海报原子落盘问题同时影响 `stream` 的字体 dump（R13 复核）。
