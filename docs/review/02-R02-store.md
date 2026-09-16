# R02 数据层 Store/FTS/facets/过滤 — status: done

## Scope
- `app/store.py`（1931 行，逐行两遍）、`app/db.py`
- 交叉验证：`scanner.apply_cached_to_movie`/`attribute_extra`（R03）、`movies.py` 的 limit 传参（R04）、`fs.py` 的调用面（R01）

## 目标（用户三问）
1. 功能设计是否合理？更好的方案？
2. 代码是否符合要求？废弃代码/漏洞（带 `file:line`）？
3. 质量差距（P0/P1/P2 分级，带证据）？

## 进度 Log（倒序追加）
- 2026-09-16 done：全文精读 + 自动化辅助验证（无锁连接扫描、未用函数扫描、PRAGMA/索引检查、参数顺序人工复核）。
- 2026-09-16 doing：开始。

## 结论

### 1. 功能设计

**整体判断：数据模型设计质量高于预期**——`tmdb_cache` 镜像表与 `movies` 物化副本分离（`store.py:71-95,179-189,491-531`）、`LOCAL_FIELDS` 白名单隔离本地写（`577-583`）、FTS 显式 `resync_fts` 替代 trigger（`543-561`）、探测结构版本 `probe_ver` 自愈（`store.py:307-325` + `media.py`）、系列信息阴性盖戳 `collection_checked_at`（`391-397`）都是很好的设计决策。以下为可改进项：

- **D1（P2）迁移策略是“启动时 ad-hoc ALTER 探测”，无版本号。** `init_db`（`253-331`）用 `PRAGMA table_info` + 一长串 `ALTER TABLE` 补列；每次启动都全量比较，且列清单手工维护、无顺序保证、无 downgrade 语义。量产做法：`PRAGMA user_version` 或 `schema_migrations` 表，按版本号顺序执行。当前实现功能正确（幂等），但 13+7+4+12 列的自愈列表会持续膨胀，长期维护成本高。
- **D2（P2）无 WAL、无 busy_timeout、每次调用新建连接。** `_conn()`（`172-176`）`sqlite3.connect(DB_PATH, check_same_thread=False)`：`check_same_thread=False` 对“一调用一连接”毫无意义（连接从不跨线程复用，已扫描确认无无锁连接）；缺 `PRAGMA journal_mode=WAL`/`busy_timeout`（全库无 PRAGMA 设置，已 grep）。单进程内有 `_lock` RLock 串行化所以现状安全，但任何外部工具（如 `scripts/find_subs.py`、未来 CLI）与 server 并发读写时会立刻撞 `database is locked`。建议：进程级单连接 + WAL + `busy_timeout=5000`，或保持一调用一连接但补 PRAGMA。
- **D3（P2）FTS 启动全量重建。** `init_db` 末尾 `rebuild_fts()` 无条件删除/插入每一行的 FTS（`534-540` 对每部电影单独 `resync_fts` → 每部一次建连接+3 条语句）。5000 部电影 = 5000 次连接开关 + 15000 条写语句，全发生在应用启动、阻塞 `/api/health` 可用性。更好：仅当 FTS 行数与 movies 行数不一致/启动参数 `--reindex` 时重建；或单连接批量重写。
- **D4（P2）facets 全表加载进 Python 聚合。** `get_facets`（`1854-1931`）`SELECT * FROM movies` 取全行（含 overview 大文本）后在内存分组；`grouped=True` 时还要遍历。前端每次翻过滤条件都可能请求（R04 复核）。当前库规模可用，1 万行级会明显；建议 SQL 侧 `GROUP BY`（`json_each` 展开 genres/tags）+ 60s 内存缓存 + 写操作失效。
- **D5（P2）搜索无索引兜底可接受，但 LIKE 前缀通配注定全表**；`_search_like`（`1613-1638`）与 `suggest_titles/people`（`1641-1691`）均 `%tok%`。对中文部分词这是产品必需的功能妥协，可接受；建议在 README 标注“大库（>2 万）搜索会退化”。
- **D6（P2）`get_collection` 对每个成员的聚合查询 + 每次列表重算封面。** `_collection_cover`（`1182-1190`）在 `list_collections`/`get_collection` 里 N+1 次执行；`_collection_cover` 的 `LEFT JOIN ... OR ...` 条件也难走索引。合集会随使用变多，建议一次 JOIN 聚合。

### 2. 代码符合度（废弃代码/漏洞）

- **B1（P2）SQL 层没有真正的高危漏洞，但有 3 个健壮性缺口：**
  - `add_collection_members`/`remove_collection_members`（`1301-1349`）：`tuple(int(x) for x in rep_ids)` 对非数字入参直接 `ValueError` → 500（路由层应校验，R06 复核）。`remove` 用 `c.total_changes` 计数（`1345`）依赖“新连接”这一实现细节，一旦改用连接池就会算错累计值。建议显式 `cur.rowcount`。
  - `list_collections(q)` 的 `LIKE` 未转义 `%`/`_`（`1197-1198`），用户搜 `_` 会全匹配。建议复用 `_like_esc`。
  - `store.get_setting`/`get_all_settings`/`set_setting` 里手写 `CREATE TABLE IF NOT EXISTS`（`201-203,219-221,243-245`）冗余（SCHEMA 已建表），且异常静默返回 `""`/`{}`（配置读取失败会被当成“未配置”静默回落 env，用户无从察觉）。建议去掉重复建表 + 记录日志。
- **B2（P2，数据一致性）`delete_movie` 不清理 `extras.movie_id` 悬挂引用。** `store.py:852-870` 删 movies 行及关联，但 extras 表 `movie_id` 仍指向已删 id（全库 grep 无 `UPDATE extras SET movie_id=NULL`）。后果：①`list_orphan_extras`（`757-760`）只查 `IS NULL`，这些花絮不再出现在“未归属”列表；②详情页/花絮页不可见；③`attached_extras` 统计遗漏；④只有再次全量扫描时 `attribute_extra` 经 `upsert_extra(file_path)` 才会自愈（`scanner.py:923-929`，已验证会更新 movie_id）。修复：`delete_movie` 内 `UPDATE extras SET movie_id=NULL WHERE movie_id=?`（或删行），一行成本。
- **B3（P2）废弃代码：**
  - `get_all_settings()`（`store.py:215-233`）全库无调用者（grep 确认只剩定义），死代码。
  - `_conn()` 的 `check_same_thread=False` 无意义（每次新建连接，不跨线程）。
  - `repath_extra_by_basename` 内部 `import os as _os` / `from .config import settings as _settings`（`793-794`）冗余：模块顶部已有 `os`、`settings`。
  - `config.py` 相关 dead field 已记 R01（B4）。
- **B4（P2）SQLite 特性依赖未声明。** `_structured_where` 依赖 JSON1（`json_each`，`1784,1804,1824,1844`）、FTS5（`162-165`）与 SQLite 的 `MAX()` 裸列行为（`346,1228,1356,1381,1419,1489,1588,1634,1658`）。Python 3.12 自带的 3.45 满足，Docker 基础镜像也满足（生产 SQLite 3.45.1 已本地验证），但这些隐式依赖没有注释/启动自检。建议 `init_db` 开头做一次 `SELECT json_valid('1')` + `fts5` 探测，失败给出明确启动错误。
- **B5（P2）`upsert_media_info` 末尾 `assert out is not None`（`965`）**：断言用于控制流，`-O` 运行时会被去掉；虽然当前不可能为 None（同事务刚写），量产代码应改为显式分支。
- **B6（P2）`_dump_list`（`334-338`）序列化失败静默返回 `[]`**，数据静默丢失且无日志。
- **B7（P2）索引缺失：** `movie_person` 无 `(person_id)` 索引（PK 是 `(movie_id, person_id, role)`，`get_person`/`suggest_people`/`persons_missing_avatar` 都要按 person 侧回查，`646-653,665-670,1683-1689`）；`extras` 无 `(movie_id)` 索引（`list_extras_by_movie`，`751-754`）。`tmdb_cache`/`collection_members` 索引完整。小库无感，上万行后明显。

### 3. 代码质量差距（不符合量产要求）

- **Q1（P2）模块级全局可变状态与导入期执行**：`_lock` 全局单例（`12`）+ `main.py` 导入期 `init_db()`（R01 Q1）。无 DI/无 repo 实例，单测无法隔离 DB；建议至少让 `DB_PATH` 可注入（现在 `db.py:4` 在导入期从 settings 计算常量，`DATA_DIR` 变化后无法重定向）。
- **Q2（P2）同步阻塞 I/O 混进 async 应用**：store 全是同步函数，FastAPI 会丢线程池（`def` 端点）执行，尚无问题；但 `rebuild_fts` 在启动路径把连接开销放大（见 D3）。
- **Q3（P2）巨型模块**：`store.py` 1931 行混合“schema 迁移 / 电影 CRUD / 人物 / 花絮 / 合集 / 播放缓存 / 设置 / 搜索 / facets”7 个领域。建议按 `store/{schema,movies,persons,extras,collections,search,facets}.py` 拆分（或引入轻量 repository 分层），对外保持 `store.xxx` 聚合导出以免大改调用方。
- **Q4（P2）错误处理风格不一致**：同一文件里混用 `try/except Exception: return ""`（`205-212`）、`except Exception as e: return {...}`、`assert`（`965`）三类；无日志导致线上排障只能靠复现。与 R01 B7 同源，建议统一“不吞异常/记录 + 返回结构化结果”。
- **Q5（P2）注释与文档质量高但存在陈旧描述**：如 `store.py:71-72` 注释“movies 表的 TMDB 列只经 copy_tmdb_to_movie() 复制”，实际上 `update_movie_meta` 也直接允许写 TMDB 列（`586-591`）且多处调用（`scanner.py:752,786,949,954`）。注释与实现不一致，建议改注释或收紧 API。
- **Q6（P2）`random`/未用 import 检查**：`store.py` 顶部 `import re/os/sqlite3/threading/time/json` 全部使用（已核）；无 `print`/`TODO`。整体卫生良好。

### 交叉引用
- B2 需 R08（extras 生命周期）确认影响面后定级；当前按 P1 记录。
- `search_fts`/`list_movies` 参数顺序与 SQL 占位符顺序已逐条人工核对，无错位（`1613-1638,1719-1737`）；FTS 语法异常仅捕 `sqlite3.OperationalError` 是够的（FTS5 MATCH 语法错误即此类型）。
- 镜像复制与标题保护逻辑（`copy_tmdb_to_movie`）设计正确，R05（刷新/换绑）只做行为一致性复核。
- 系列/合集读取函数的“纯本地只读”承诺成立（无网络调用），R06 只复核并发语义。
