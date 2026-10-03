---
version: 0.19.0
reviewed: 2026-10-03
---

# 数据模型与迁移

[开发者文档](README.md)

数据源在 `app/store/_base.py`，使用 Python 标准库 `sqlite3`，不使用 ORM。当前服务端/网页版本为 `0.19.0`，数据库 `SCHEMA_VERSION=31`；两种版本号不是一一对应。启动 `init_db()` 检查 SQLite JSON1/FTS5、建表、按 `PRAGMA user_version` 执行幂等迁移，再视需要重建索引。连接启用 WAL、`busy_timeout=5000` 和 `synchronous=NORMAL`。

最近迁移为 v28 剧集目录归属及历史、v29 文件实际变更日志 `fs_changes`、v30 媒体探测帧率 `media_info.fps`、v31 独立剧集播出快照与检查队列。当前 ffprobe 缓存版本为 `PROBE_VERSION=4`，旧探测结果在播放时重新获取；新增字段不要求用户重新扫描整库。

## 主要实体

```mermaid
erDiagram
    media_libraries ||--o{ libraries : "一个媒体库多个视频库"
    libraries ||--o{ movies : "按视频库归属"
    libraries ||--o{ tv_shows : "按视频库归属"
    tv_shows ||--o{ tv_seasons : "剧含多季"
    tv_seasons ||--o{ tv_episodes : "季含多集"
    movies ||--o{ extras : "movie_id 归属"
    tv_shows ||--o{ extras : "show_id 归属"
    movies }o--o{ persons : "movie_person 关联"
    movies }o--o{ collections : "海报粒度成员"
    libraries ||--o{ tv_directory_bindings : "目录归属规则"
```

外键只表达归属方向；`media_info`、`playback_progress` 按 `(kind, item_id)` 键隔离电影/分集/花絮，不做硬外键。`tmdb_cache` 主键是 `(media_type, tmdb_id)`，电影与剧集的数值 id 空间独立。合集成员按海报粒度（同 `tmdb_id` 全版本）归并，不是按单个文件行。

| 表 | 作用/关键关联 |
|---|---|
| `media_libraries` | 存储连接/根目录、凭据、只读、健康状态；一个媒体库有多个视频库 |
| `libraries` | 视频库类型 `movie/tv`、`media_library_id`、子路径、命名档、落盘策略及来源链 |
| `movies` | 文件版本行，`UNIQUE(library_id,file_path)`；同片多个文件经匹配关系归并展示 |
| `tv_shows` / `tv_seasons` / `tv_episodes` | 剧、季、集镜像；分集含本地匹配/观看等字段，另有 `match_source`（归属来源）与 `binding_conflict`（归属冲突标记） |
| `tv_directory_bindings` | 目录归属规则：主键 `(library_id, path)` → `(show_id, season, override_season)`；扫描与重扫沿用，整理/恢复同步改写路径 |
| `extras` | 电影/剧集花絮，分别通过 `movie_id`/`show_id` 归属 |
| `media_info` | 按 `(kind,item_id)` 缓存 ffprobe 结果、帧率 `fps` 与 `probe_ver`，音轨 JSON 保存声道与采样率等信息 |
| `playback_progress` | 按 `(kind,item_id)` 隔离电影、分集、花絮续播 |
| `scan_state` | 文件大小/mtime/扫描状态，用于增量与整理后的路径同步 |
| `fs_changes` | 按视频库记录已发生的文件变更；成功完整扫描只清除开始时水位以内的记录，期间新增变更保留 |
| `tv_binding_history` | 归属变更的预览/执行快照与撤销状态机（`preview` → 已执行/已撤销），预览 token 15 分钟有效 |
| `organize_moves` | 剧集整理逐步审计：批次、源/目标、文件或目录、撤销时间 |
| `tmdb_cache`、`external_meta`、`match_index` | 已获取的来源详情和可离线检索索引 |
| `tv_airing_snapshots` | 按 `tmdb_id` 保存播出快照、首次基线时间、成功/尝试/下次检查时间、错误与任务租约 |
| `tv_airing_catalogs` | 主键 `(tmdb_id,season)`，保存官方季目录、缓存时钟、失败重试、`pending`、失效修订号 `revision` 与租约 |
| `tv_airing_events` | 按稳定 `event_id` 去重近期播出事件，保存官方分集身份、季集号、播出日期、首次发现时间及 `active` 有效标记 |
| `tv_airing_control` | 单行保存有效配置指纹、共享错误和下次重试时间，用于凭据错误、限流等整体退避 |
| `persons`、`movie_person`、`collections`、`collection_members` | 演职员关联和媒体库级合集 |
| `app_settings` | 数据库优先的运行配置、来源冷却、新手配置状态和智能辅助配置 |
| `ai_usage` | `app/ai/settings.py` 按需建表，按 UTC 日累计实际请求次数与上游报告的 token；不另增 schema 版本 |

### 剧集播出缓存与收藏对照

v31 的四张表由迁移 `_m31` 幂等建立。快照与季目录按 TMDB 身份共享，不从本地剧 ID 派生，避免不同媒体库重复请求；收藏对照仍限定当前媒体库内启用的剧集视频库，返回实际本地 `show_id/library_id/episode_id`。官方目录不创建 `tv_episodes` 行，也不改变观看进度或目录归属。

播出缓存使用独立 `checked_at/next_check_at`，不将普通刮削的 `fetched_at` 当作播出检查时间。成功快照只同步现有剧表的播出状态和最后播出日期，保留本地编号、季号偏移、标题、通用更新时间和完整资料刮削时间。收藏计算去重多版本及可信多集覆盖，排除 `missing` 文件，无法确认的编号显示待核对。

任务租约保存在 SQLite，防止并发或重启重复认领；过期租约可恢复，过期执行者不能提交结果。季目录的 `revision` 防止旧请求覆盖后来失效的版本；维护状态另以 `catalog_pending/catalog_failed` 统计待处理或失败目录。

事件更新修正季集号、标题与播出日期，但保留原 `event_id/discovered_at`。来源改为未来/无效日期或完整季目录撤回分集时置 `active=0`，不再推荐；恢复后复用原身份。有效事件按官方播出日期保留 30 天，停用事件按首次发现时间保留 30 天。浏览器的每周曝光时间与已展示事件保存在 `localStorage`，不写入这些表。服务端检查周期和浏览器推荐周期彼此独立。

## FTS 与字段所有权

`movies_fts` 是 FTS5 索引，**没有 SQL trigger**。修改 `movies`、`persons`、`movie_person` 相关可检索字段后需调用 `store.resync_fts(movie_id)`；直接手写 SQL 若漏同步会使搜索与详情不一致。启动在迁移或检测到不一致时重建 FTS；设置页也提供手动重建。搜索对中文分词不足的场景可能走 LIKE 回退，大库要留意性能。

`TMDB_FIELDS` 与 `LOCAL_FIELDS` 在 `store/_base.py` 分列，来源缓存和本地自有字段不能随意混写。`movies.title_auto` 标明标题能否被更可信来源自动覆盖；`added_at` 是首次入库时间，不应在重扫时刷新。`overview_override` 与观看/评分/标签是本地资料，刷新来源时须保护。

电视客户端的片名拼音检索及演员作品查询分别位于 `store/tv_search.py`、`store/tv_actors.py`，读取现有电影/剧集/人物资料，不另建远程搜索服务，也不替代网页电影 FTS。`playback_progress` 的完成判定由 `app/playback_completion.py` 统一；剧集继续观看响应的 `id` 是剧 ID，实际分集 ID 位于 `progress.version_id`。

## 安全迁移步骤

1. 在 `_base.py` 增加新 schema 列/表及对应迁移步骤；确保旧库可顺序升级，新库直接建全量 schema。
2. 为已有数据提供回填和默认值，迁移过程可重复运行而不破坏数据。
3. 添加空库、旧版本升级和重复初始化测试；注意 `scan_state`/FTS 及外部索引关联。
4. 不在模块导入期 `init_db()`；仅启动 lifespan 或显式测试初始化。
5. 升级前备份数据目录及远程凭据密钥；旧程序未必能直接打开新 schema。

库路径变动要同步数据库和扫描状态，不能只移动磁盘文件。现有整理/恢复提供审计路径；具体约束见 [存储设计](storage.md)。
