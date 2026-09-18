# jzmedia 多视频库 + 远程库 + 离线刮削方案（定稿）

> 状态：**已定稿**（2026-09-18 用户确认 D1–D8），按 §13 A–G 阶段实施；本文件兼作实施设计基线。
> 目标版本：A–G 收口为 `v0.9.0`；完整 TV（剧集刮削/命名/NFO）为后续独立版本。
> 前置阅读：`AGENTS.md`、`docs/plans/NAS_Web_Video_Player_Development_Plan.md`。

---

## 1. 场景与目标

用户场景：

1. NAS 上 `volume1/media/Movies` 与 `volume1/media/TV Shows` 两棵库，当前由 Plex 管理；
2. NAS/电脑装有 Tailscale，局域网与外网均可 SMB/AFP/NFS/FTP/rsync 访问；
3. 开发机需要接入 NAS 影视库做功能/播放测试，同时保留本地测试库；
4. 「同时支持本地库与远程库」是长期亮点功能，不是一次性调试手段；
5. 允许 jzmedia 修改 NAS 上的目录/文件名，但必须保持 Plex 可识别；
6. jzmedia 最终跑在 NAS（Docker 优先，WebStation 备选）；
7. 先改善 Plex 体验（海报/匹配），最终替代 Plex；
8. 本地 DB 兼作离线元数据库，TMDB 不可达时仍能匹配；
9. 无 TMDB Token 时有多级降级刮削方案（NFO/内嵌/无 key API/爬虫/IMDb 数据集）。

### 1.1 目标

- 一个 jzmedia 实例管理**多个相互独立的媒体库**，库类型仅 `movie` / `tv`。
- 库来源 `local` / `smb` / `nfs`；应用内挂载 + 宿主已挂载登记两条路线。
- **TV 库本期做只读清单**：解析 `SxxEyy`，按剧/季/集浏览与播放；不刮削、不改名、不写 NFO。
- **归档一律扁平**（对齐 Plex 推荐结构），不再做大区二级目录；`region` 仅作筛选元数据。
- 库级命名档 `plex|kodi|off`；`plex` 档产物满足 Plex 命名规范；本地海报/背景图写进媒体目录。
- 元数据 Provider 链 + `match_index` 离线库；无网/无 Token 可扫描、可匹配、可手动绑定。
- 凭据 Fernet 加密落库，绝不明文。

### 1.2 非目标（本期不做）

- `mixed` 混合库（已取消）；完整 TV（TMDB TV 刮削、tvshow/episode NFO、Plex 剧集命名与改名）→ 后续版本。
- 「全部库」聚合视图（同名影片会显示两次）。
- 跨库移动/复制文件（跨库搬运用系统工具或复制功能）。
- 客户端直连 SMB 播放（浏览器无法读 `smb://`；播放仍由 jzmedia 代理/转码）。
- Plex API 调用 / Plex 数据库读写（仅文件级兼容）。
- 多用户/权限体系（沿用单用户 + `JZMEDIA_TOKEN`）。

---

## 2. 决策记录（已确认）

| # | 议题 | 决策 |
|---|---|---|
| D1 | 库模型 | **一库一根**（独立源），库间不相关；同名片可同时存在于两库，不合并版本/观看/合集 |
| D2 | 库类型 | 仅 `movie` / `tv`；**不做 mixed**；TV 本期只读清单，完整支持后续独立排期 |
| D3 | 远程接入 | 来源 `local\|smb\|nfs`；开发机首选宿主挂载登记 `local`，应用内挂载为产品能力（需 SYS_ADMIN，失败给指引） |
| D4 | 凭据安全 | SMB/NFS 凭据 **Fernet 加密**（`data/secret.key` 0600 + `cryptography`），API 只写不读，日志脱敏 |
| D5 | 命名与归档 | 库级 `naming_profile=kodi\|plex\|off`；**所有档位归档扁平**（`Title (Year)/…`，无大区层）；`plex` 档用 Plex 文件名规范；归档预览做 Plex 兼容性检查 |
| D6 | Plex 配合 | 文件级：规范命名 + 本地 `poster.jpg`/`fanart.jpg`；**不要求切换 Plex Agent**；NFO 照写（Kodi/Jellyfin/离线库），Plex NFO Agent 为可选加分 |
| D7 | 降级刮削 | 默认基座（文件名/NFO/内嵌/本地缓存）+ 无 key API（TVmaze/Wikidata）+ 爬虫（默认关）+ IMDb 数据集（手动导入） |
| D8 | Plex API/DB | 不调用、不读写 |

后续评审若改动以上任一项，需同步修订本文档 §5–§9。

---

## 3. 行业调研结论

三家成熟方案的库模型高度一致：**「库（虚拟分组）+ 类型 + 1..N 物理路径」**。

| | 库模型 | 一库多目录 | 类型 | 混合库 | 远程存储 |
|---|---|---|---|---|---|
| Plex | Library（section） | 支持 | 强类型 | 无 | 必须 OS 层挂载 |
| Emby | Media Library | 支持 | 强类型 | 官方标注支持有限 | 必须 OS 层挂载 |
| Jellyfin | 同 Emby | 支持 | movies/shows/music/mixed | mixed 已 deprecated | 必须 OS 层挂载 |

三条共识及其映射：

1. **远程库先挂载成 POSIX 路径**；jzmedia 用挂载管理器在容器内 `mount.cifs`/NFS，无权限时回落「宿主挂载 + 登记路径」。
2. **混合库是公认的坑** → D2 直接不做。
3. **库是逻辑分组** → 本方案用独立源，绕开跨库版本聚合歧义。

参考：Plex Naming & Organizing（Movies/TV）、Plex Local Media Assets、Plex NFO Agent（PMS ≥1.43.1）、Plex `.plexmatch`（TV，后续版本用）、Jellyfin/Emby Libraries。

---

## 4. 现状评估（单根耦合点）

现状：`settings.media_root` 单根（`app/config.py:12`），`movies.file_path`、`extras.file_path`、`scan_state.file_path` 存根内相对路径；后端共 **88 处** `settings.media_root` 引用（已核对）。

**有利条件**：API 与前端从不传绝对路径（`direct_url` 也是相对 `file_path`），加库只需给行加 `library_id`，前端 URL 契约基本不变。

难点与风险（按严重度排序）：

| # | 问题 | 位置 |
|---|---|---|
| 1 | `movies.file_path UNIQUE`、`extras.file_path UNIQUE`、`scan_state.file_path PK` 全局唯一；多库后相对路径可重复 → 重建三表 | `app/store/_base.py:17,70,137` |
| 2 | extras GC 按 `media_root` 存在性删行 → 逐库扫描会误删其他库 extras | `app/scanner/scan.py:227-234` |
| 3 | 版本聚合全局按 `tmdb_id` / `COALESCE(tmdb_id,-id)` 展开 → 跨库串味、批量越库 | `app/store/search.py:110,250`、`_base.py:477-490`、`movies.py:103-122` |
| 4 | 单根写路径：归档目标、原始路径恢复、`/api/fs/*`、上传默认目录、`_region_stale`/分区 | `files/planner.py`、`files/routes.py:36-37`、`movies/routes.py:713-719` |
| 5 | 播放链路全部 `join(media_root, rel)` | `stream/common.py:152`、`stream/media.py:138,161,245`、`stream/subtitles.py:39,104`、`playback/cmd.py:137` |
| 6 | 过滤/统计需加库 scope | `store/search.py:295-458`、`store/movies.py:190-208` |
| 7 | NFO/花絮归属依赖 root-relative 推导 | `scanner/nfo_link.py:27,85`、`scanner/persist.py:289,304`、`routers/extras.py:99` |
| 8 | 配置/部署单根：health、compose 单 volume、start.sh 重映射 | `routers/health.py:31,64`、`docker-compose.yml`、`start.sh` |

另：`media_info`/`playback_progress` 以 `movie_id` 为键，TV 集播放需改为 `(kind, item_id)` 复合键。

---

## 5. 数据模型（v12 + v13）

### 5.1 v12：多库 + 元数据/策略

```sql
CREATE TABLE libraries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT UNIQUE NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN ('movie','tv')),
  source TEXT NOT NULL DEFAULT 'local' CHECK(source IN ('local','smb','nfs')),
  path TEXT NOT NULL,                    -- local: 容器内绝对路径；smb/nfs: 自动挂载点
  read_only INTEGER NOT NULL DEFAULT 0,
  auto_mount INTEGER NOT NULL DEFAULT 1, -- 仅 smb/nfs 有意义
  enabled INTEGER NOT NULL DEFAULT 1,
  sort_order INTEGER NOT NULL DEFAULT 0,
  -- 命名/落盘策略（D5/D6）
  naming_profile TEXT NOT NULL DEFAULT 'kodi' CHECK(naming_profile IN ('plex','kodi','off')),
  artwork_mode TEXT NOT NULL DEFAULT 'nfo' CHECK(artwork_mode IN ('none','nfo','nfo_art')),
  organize_target TEXT NOT NULL DEFAULT '电影',
  inbox_dir TEXT NOT NULL DEFAULT '待整理',
  metadata_providers TEXT NOT NULL DEFAULT '',   -- JSON 数组；空=全局默认链
  -- 远程凭据（仅 smb/nfs；密码存 Fernet 密文，API 只写不读）
  smb_host TEXT DEFAULT '', smb_share TEXT DEFAULT '', smb_subpath TEXT DEFAULT '',
  smb_domain TEXT DEFAULT '', smb_username TEXT DEFAULT '', smb_password TEXT DEFAULT '',
  smb_options TEXT DEFAULT '',
  nfs_export TEXT DEFAULT '', nfs_options TEXT DEFAULT '',
  -- 运行状态
  last_status TEXT DEFAULT '', last_error TEXT DEFAULT '', last_check_at INTEGER DEFAULT 0,
  created_at INTEGER NOT NULL DEFAULT 0, updated_at INTEGER NOT NULL DEFAULT 0
);

ALTER movies ADD COLUMN library_id INTEGER NOT NULL DEFAULT 0;
ALTER movies ADD COLUMN match_source TEXT DEFAULT '';   -- tmdb|local|nfo|tvmaze|…
ALTER movies ADD COLUMN nfo_hash TEXT DEFAULT '';       -- 上次写 NFO 的哈希（所有权保护）
ALTER extras ADD COLUMN library_id INTEGER NOT NULL DEFAULT 0;
ALTER collections ADD COLUMN library_id INTEGER NOT NULL DEFAULT 0;
-- 重建：movies UNIQUE(library_id, file_path)；extras UNIQUE(library_id, file_path)
-- 重建：scan_state PRIMARY KEY(library_id, file_path)
-- 索引：idx_movies_library(library_id)

ALTER tmdb_cache ADD COLUMN source TEXT DEFAULT 'tmdb';
ALTER tmdb_cache ADD COLUMN payload_json TEXT DEFAULT '{}';   -- 归一化详情快照（离线重放）
ALTER tmdb_cache ADD COLUMN premiered TEXT DEFAULT '';
ALTER tmdb_cache ADD COLUMN tagline TEXT DEFAULT '';
ALTER tmdb_cache ADD COLUMN runtime INTEGER DEFAULT 0;
ALTER tmdb_cache ADD COLUMN studios TEXT DEFAULT '[]';
ALTER tmdb_cache ADD COLUMN backdrop_tmdb_path TEXT DEFAULT '';
ALTER tmdb_cache ADD COLUMN logo_tmdb_path TEXT DEFAULT '';

CREATE TABLE match_index (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT NOT NULL,                 -- local|tmdb|nfo|embedded|tvmaze|wikidata|douban|imdb
  source_id TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL DEFAULT 'movie',
  title TEXT DEFAULT '', original_title TEXT DEFAULT '',
  year INTEGER,
  tmdb_id INTEGER, imdb_id TEXT DEFAULT '',
  payload TEXT DEFAULT '{}',
  fetched_at INTEGER DEFAULT 0,
  UNIQUE(source, source_id)
);
CREATE VIRTUAL TABLE match_index_fts USING fts5(title, original_title, tokenize='unicode61');
```

- **不引入 `library_roots` 中间层**（一库一根）；将来合并展示是 UI 层聚合，不动表。
- 全局共享：`tmdb_cache`、`match_index`、`persons`、`movie_person`、海报/头像、`movies_fts`；按库隔离：movies/extras/scan_state/collections。
- `movies.original_file_path` 语义不变（同库相对路径），恢复永不跨库。

### 5.2 v13：TV 只读清单 + 播放键

```sql
CREATE TABLE tv_shows (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  library_id INTEGER NOT NULL DEFAULT 0,
  title TEXT DEFAULT '', sort_title TEXT DEFAULT '', year INTEGER,
  tmdb_id INTEGER,                     -- 本期恒 NULL
  needs_review INTEGER DEFAULT 0,
  updated_at INTEGER DEFAULT 0,
  UNIQUE(library_id, title, year)
);
CREATE TABLE tv_episodes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  show_id INTEGER NOT NULL,
  library_id INTEGER NOT NULL DEFAULT 0,
  file_path TEXT NOT NULL,
  season INTEGER DEFAULT 0, episode INTEGER DEFAULT 0,
  title TEXT DEFAULT '',
  updated_at INTEGER DEFAULT 0,
  UNIQUE(library_id, file_path)
);
-- 重建（存量 kind='movie', item_id=movie_id；派生数据，缺失自动重探）
media_info        PRIMARY KEY(kind, item_id)
playback_progress PRIMARY KEY(kind, item_id)
```

- `parse_filename` 增补 `season/episode/episode_title`；剧名优先取 `Show Name (Year)` 父目录，回退文件名。
- `S00`/绝对集号/多集文件不猜错归属：落行 + `needs_review`，供人工确认（本期不做剧集 UI 的编辑闭环，仅标记）。
- 播放接口统一加 `kind` 参数（默认 `movie`）；`store.get_item(kind,id)` 取行。

### 5.3 凭据：`app/secrets.py`

- 首次使用生成 `data/secret.key`（0600）；`encrypt_str()/decrypt_str()` 供库 CRUD 与 `app/mounts.py`。
- 密文只存 `libraries.smb_password`/`nfs_password`；API 恒返回 `****`；`last_error` 分类 `auth_failed|unreachable|not_mounted|error`，绝不回显凭据。
- 换机携带 `data/secret.key` 即可；丢 key 需重新输入密码（文档说明）。

### 5.4 版本迁移与兼容（v12 → v13 一次落地）

1. `SCHEMA_VERSION` 11 → 12 → 13，`_MIGRATION_STEPS` 追加 `_m12/_m13`（幂等）。
2. 建 `libraries`；为空用 `settings.media_root` 播种「默认库」（movie/local/kodi/nfo）。
3. 事务内重建 `movies`/`extras`/`scan_state`（create new → INSERT SELECT 带默认 `library_id` → drop → rename → 索引）。
4. `collections` 加列回填默认库；`match_index` 启动从 `tmdb_cache`/`movies` 播种。
5. `media_info`/`playback_progress` 重建 + `INSERT SELECT kind='movie'`。
6. `rebuild_fts()`；health/日志标注迁移耗时与版本。
7. 回退策略：迁移前文档提示备份 `data/jzmedia.db`；发布 tag `v0.9.0`，旧代码不识别 `library_id`，回退需恢复备份。

兼容原则：

- `MEDIA_ROOT` env 仅用于 v12 自举与 health 展示；库以 DB 为准。
- API 省略 `library` 参数 = 全量（脚本/健康检查不破坏）；前端始终携带当前库。
- 无库可用（用户删光）：海报墙引导建库，`/api/health` 返回 `media.ok=false` 与原因。

---

## 6. 路径解析层（`app/library_paths.py`）

```python
list_libraries(only_enabled=False) / get_library(lid)
resolve(lid, rel) -> abs            # 替代 join(media_root, rel)（全部 88 处）
locate(abs_path) -> (lid, rel)      # 替代 relpath(abs, media_root)；多库重叠取最长前缀
check_inside(lid, rel) -> rel       # 替代 routers/files/paths.py:_check_inside_root
default_library() -> dict | None    # 播种库 / 单库快捷方式
invalidate_cache()                  # 库 CRUD/挂载状态变化后调用
```

- 进程内缓存库表（`(id, path)` 快照），带显式失效；`resolve` 对不存在库/路径不抛，由调用方给 404/409。
- 建库时校验：路径存在可读（local）或挂载成功（smb/nfs）、不与现有库根嵌套（A 是 B 子目录默认拒绝）、名称唯一。
- `settings.media_root` 仅保留为 v12 自举与 health 兼容字段，新代码一律走本模块。

---

## 7. 远程接入（`app/mounts.py`）

### 7.1 流程

```
建库(source=smb|nfs) → 能力检测(CAP_SYS_ADMIN + mount.cifs/mount.nfs)
→ 凭据 Fernet 加密入库 → 写临时凭据文件(data_dir/mounts/.cred_<id>, 0600)
→ 创建挂载点 data_dir/mounts/lib_<id> → mount
   SMB: //host/share[/subpath] -t cifs -o credentials=…,uid,gid,iocharset=utf8,soft,retrans=3,vers=3.0,nobrl[,ro]
   NFS: host:/export -t nfs -o vers=4.1,soft,timeo=…[,ro]
→ 可读探测 → libraries.last_status 落库 → 可选立即扫描
```

- 挂载点固定 `data_dir/mounts/lib_<id>`（重启路径稳定，无需 compose 变更）。
- 生命周期：启动后台重挂 `auto_mount` 库（不阻塞 lifespan）；60s 看门狗 `os.path.ismount`，失联退避重挂（5s→5min）；删除库卸载并清理凭据文件/挂载点；`ALLOW_SMB_MOUNT=0` 总开关。
- 能力降级：检测失败 → `POST /api/libraries/{id}/check` 返回 `mount_supported:false` + 宿主命令/fstab 片段（`_netdev` + 凭据文件写法）；用户可把宿主已挂路径登记为 `local`。
- AFP/FTP/rsync：不做应用内挂载（无 POSIX 随机读语义）；文档指引宿主挂载/rclone 后登记 `local`。
- 工程配套：`Dockerfile` 加 `cifs-utils`（可选 `nfs-common`）；`docker-compose.yml` 注释版 `cap_add: [SYS_ADMIN]`（DSM 可能需 `privileged: true`）；`start.sh` 打印能力检测结果。

### 7.2 Tailscale 与开发机

- NAS/开发机宿主装 Tailscale；jzmedia 只监听 8080，经 tailnet IP/MagicDNS 访问。
- 容器内 MagicDNS 可能不解析 → 文档建议挂载用 tailnet IP 或配 `dns:`；SMB(TCP 445)/NFS(2049) over Tailscale 可用。
- 开发机（WSL）首选 Windows/WSL 宿主挂 NAS 后登记 `local`；应用内挂载作为产品能力单独验证。

---

## 8. 命名与落盘（D5/D6）

### 8.1 归档一律扁平

- `POST /api/files/organize`（`mode=relocate`）目标固定 `to_dir/标题 (年份)/…`；**移除 `group_by_region` 参数与 UI 勾选**（连同 `_region_stale`/「原分区过期」角标）。
- `region` 仍由 `app/regions.py` 派生并存 DB，仅用于筛选/facets，不再影响路径。
- 存量已分区归档的库用一次 relocate 全量收敛拍平（先 dry-run）；执行后向上清理空祖先目录（止于目标根，`executor._cleanup_old_dir` 增强）。
- `inplace` 照旧（不涉及分区）。

### 8.2 命名档

| 档 | 产物 |
|---|---|
| `plex` | 单版本 `Title (Year)/Title (Year).ext`；多版本同目录 `Title (Year) - 规格.ext`；剪辑 `Title (Year) {edition-版本}/Title (Year) {edition-版本}.ext`；兜底 ` - 版本N` |
| `kodi` | 现模板 `标题 (年份)[-版本][-规格][-分卷][-版本N].ext`（单 `-` 直连） |
| `off` | 不做文件改名（浏览/播放/刮削照常） |

- 归档预览新增 `plex_warnings`（仅 plex 档）：缺年份、非法字符 `<>:"/\|?*`、分卷（Plex NFO Agent 不支持多段）、同目录无 edition 的重复版本、非规范花絮目录。
- 花絮目录：`kodi` 档保持 `extras/`；`plex` 档按 kind 映射 `Trailers/Featurettes/Deleted Scenes/Interviews/Behind The Scenes/Scenes/Shorts/Other`；`scanner.classify.EXTRAS_DIR_NAMES` 同步扩展，防误判。

### 8.3 本地图片（`app/artwork.py`）

- `artwork_mode=nfo_art` 时写 `poster.jpg` + `fanart.jpg` 到影片目录（多版本补 `<stem>-poster.jpg`）；Plex 本地图片优先于在线刮削（需库启用本地媒体资源），直接解决「Plex 加载不出海报」。
- 原子写；内容不变不重写；只读库/`off|none` 拒写；来源 `data/posters/<tmdb_id>.jpg`（复制）与 backdrop 下载（v12 `backdrop_tmdb_path`）。
- 后续可选 `logo.png`（v12 已留 `logo_tmdb_path`）。

### 8.4 NFO（`app/nfo.py` 扩展）

- 补 Plex NFO Agent 可读字段：`<ratings><rating name="themoviedb|imdb" max=10 default=…>`、`<premiered>`、`<tagline>`、`<set><name><overview>`、`<actor><order>`；保留 `<uniqueid type=tmdb|imdb>`（Plex 稳定 GUID）；`customrating`/`douban_rating` 扩展保留。
- **所有权保护**：`movies.nfo_hash` 记录上次写入内容哈希；磁盘被外部改过后默认不覆盖，仅 `POST /api/jobs/rebuild-nfo {"force":true}` 覆盖。
- 独占单版本 `movie.nfo` / 多版本 `<stem>.nfo` 规则不变；TV NFO 留待完整 TV 版本。

---

## 9. 离线匹配与降级刮削（D7）

### 9.1 Provider 抽象（`app/metadata/`）

```python
class Provider:
    name: str
    kinds: set[str]                  # {"movie"} / {"tv"} / {"movie","tv"}
    def available() -> bool
    def search(title, year, kind) -> list[Candidate]   # 含稳定 id 与置信度
    def detail(cand) -> CanonicalMeta                  # 归一为 tmdb_cache 结构
```

默认链（库可通过 `metadata_providers` 覆盖）：`local_cache → tmdb(有 token) → nfo/embedded(扫描时) → wikidata → tvmaze(tv) → douban/bangumi(默认关) → imdb_dataset(已导入)`。

- 失败冷却：`provider_state` 落 `app_settings`（fail_count/cooldown_until），连续失败暂停；设置页展示链路状态与最近错误。
- 写路径统一：Provider 结果经 `upsert_tmdb_cache`（有 tmdb_id）或 `match_index`（无 tmdb_id 候选）。

### 9.2 离线匹配

- `match_index` 启动从 `tmdb_cache` + movies 存量播种；cache/NFO/外部候选更新后同步；FTS5 召回 + `normalize_title` 精确/别名 + 年份 ±1 + 阈值打分。
- `scan_one` 刮削失败不再直接 `scan_failed`：先走本地匹配，高置信自动绑 `tmdb_id`（`match_source=local`），否则建行 + `needs_review` 候选列表。
- `GET /api/tmdb/search` 支持 `provider=auto|local|tmdb|…`；无 token/断网自动回退本地；`POST /api/movies/{id}/match` 接受本地候选。
- NFO 导入：冷启动读 `movie.nfo` 补标题/年份/简介/唯一 ID（零网络）。
- 内嵌元数据：ffprobe format tags 进候选。

### 9.3 降级来源

| 来源 | 说明 |
|---|---|
| NFO 导入 | 无 DB 记录时读同目录 NFO；本地、零网络 |
| 内嵌元数据 | ffprobe format tags（title/year/genre） |
| 本地缓存/`match_index` | 历史刮削结果离线重放 |
| TVmaze | 剧集，无 key（本期先接入框架，完整 TV 时启用） |
| Wikidata/Wikipedia | 标题+年份 → 实体 → IMDb/TMDB ID 桥接 |
| 豆瓣/番组 | 爬虫类，**默认关闭**，设置页显式开启；限速 + 冷却；不阻塞主链 |
| IMDb 数据集 | 设置页手动导入 `title.basics` 等 → `match_index(source='imdb')`；GB 级二次确认 |

---

## 10. 关键语义

### 10.1 库 scope 清单

按库生效：海报墙/搜索/facets/stats、扫描任务与进度、未匹配/待匹配/花絮/缺文件列表、归档与恢复、上传、FS 浏览器、相似推荐、合集与系列建议、NFO/图片写入、TV 清单。
不按库：TMDB 缓存、`match_index`、人物身份页数据（作品列表按当前库过滤，`/p/:tmdb_id?lib=`，无参数全量）、海报/头像文件、转码产物（按版本 id 天然隔离）。

### 10.2 扫描

- `POST /api/jobs/scan` 增加 `library_id`（缺省 = 全部启用库，逐库独立进度/结果）。
- `scan_all` 遍历启用库；extras GC 按库分区；`scan_one` 携带 `library_id`。
- `kind=tv`：解析 `SxxEyy` 入 `tv_shows/tv_episodes`；不做花絮归属/NFO/改名；`kind=movie` 走既有电影链路。
- 挂载失联的库：扫描跳过并写 `last_status`，任务结果明确报错，不中断其他库。

### 10.3 只读库

`read_only=1`：归档/移动/改名/删除/上传/NFO //图片写入/花絮归集/rebuild 全部 409；扫描、浏览、播放、字幕抽取照常（字幕/字体产物在 `data_dir`）。

### 10.4 删库事务

单事务：取消该库 job 与会话 → 删 `movies_fts` rowid/`movie_person`/`media_info`/`playback_progress`/`extras`/`scan_state`/`tv_*`/`movies`/`collections` 与成员 → 删 `libraries` 行 → SMB/NFS 卸载 + 清凭据/挂载点。保留 `tmdb_cache`/`persons`/海报；**绝不触碰磁盘媒体文件**；UI 两步确认。

### 10.5 归档默认值

库字段 `organize_target`（默认 `电影`）/`inbox_dir`（默认 `待整理`）；relocate 一律扁平（D5）。

---

## 11. API 草案

```
GET    /api/libraries                    → {items:[{id,name,kind,source,path,read_only,enabled,
                                              naming_profile,artwork_mode,movie_count,
                                              last_status,last_error}], default_id}
POST   /api/libraries                    {name,kind,source,path|smb:{...}|nfs:{...},
                                           read_only,auto_mount,naming_profile,artwork_mode,scan_now}
PATCH  /api/libraries/{id}               改名/类型/只读/启用/排序/命名档/artwork/元数据链
DELETE /api/libraries/{id}               只清记录（§10.4），响应含清理统计
POST   /api/libraries/{id}/check         路径/挂载/可写性 + 影片数 + mount_supported + suggested_cmd
POST   /api/libraries/{id}/mount|unmount  仅 smb/nfs，手动触发

现有端点增加 library 参数：
  GET  /api/movies /api/search /api/search/suggest /api/facets /api/jobs/stats
  POST /api/jobs/scan /api/jobs/backfill-meta /api/jobs/rebuild-nfo
  GET/POST /api/fs/*  /api/files/*  /api/extras*  /api/uploads
  POST /api/movies/{id}/rescan /match（行自带库，通常无需参数）
播放：/api/stream/* 增加 kind=movie|episode（默认 movie）
影片 payload 增加 library_id/library_name；GET /api/settings 增加 libraries 摘要
TV：GET /api/tv/shows /api/tv/shows/{id} /api/tv/episodes/{id}
```

写操作鉴权沿用 `main.py:_auth_write`；管理库与凭据属写操作，文档建议开启 `JZMEDIA_TOKEN`。

---

## 12. 前端改造

| 位置 | 改动 |
|---|---|
| 新增 `frontend/src/libraries.js` | 库列表/当前库（localStorage `jzmedia.lib`）、`withLib(params)` |
| `App.vue` | 顶栏库切换器（名称 + movie/tv 角标 + 只读角标） |
| 新增 `LibrariesPanel.vue` | 设置页 `sec-libraries`：库 CRUD、local/smb/nfs 表单、连接测试、挂载状态、只读、命名档、artwork、元数据链顺序；无挂载能力展示宿主指引 |
| `Library.vue` | `readUrl/syncUrl/buildParams` 纳入 `lib`；search/facets 带库 |
| 新增 TV 页 | `/tv` 剧集墙 + `/tv/:id` 季/集列表；`PlayerModal` 集播放传 `kind=episode` |
| `FsBrowser.vue` | 当前库 prop；根=当前库根 |
| `OrganizePanel.vue` | 删分区勾选与「原分区过期」角标；显示库归档目标 |
| `Detail.vue` / `UploadDialog.vue` | 去 `group_by_region`；路径显示库名 |
| 待匹配/未匹配/统计 | 跟随当前库或加库筛选 |

---

## 13. 阶段计划与验收

| 阶段 | 内容 | 验收标准 |
|---|---|---|
| **A 数据层**（2–3d） | v12/v13 迁移；`app/library_paths.py`；替换 88 处单根引用；`app/secrets.py`；`media_info` 复合键；health/settings 最小适配 | 存量单根库升级后列表/搜索/facets/播放/归档行为与升级前一致；FTS 行数对账；三种形态（存量/空库/仅 unmatched）自测；`/api/health` 正常 |
| **B 库化后端**（5–7d） | 库 CRUD/删库事务；按库扫描与 GC；分组键 `(library_id, film)`；所有读写口加 `library`；只读写保护 | 两库同名文件搜索/版本/批量/facets 不串联；删库无残留、文件完好；只读库写口全 409 |
| **C 远程接入**（3–4d） | mounts（SMB/NFS + Fernet）；看门狗退避；Tailscale/WSL 文档；Dockerfile/compose/start.sh | SMB 库挂载/卸载/重启自恢复；断网自愈；无 cap 环境返回降级指引；NAS compose 双卷可用 |
| **D 命名与落盘**（3–4d） | 命名档 + 扁平化改造；Plex 兼容检查；`artwork.py`；NFO 扩展 + nfo_hash；Plex 花絮目录 | 存量分区库可预览并拍平；plex 档产物在 Plex 中匹配；用户改过的 NFO 不被覆盖；本地海报生效 |
| **E 离线与降级**（4–6d） | `match_index`；Provider 注册表；离线扫描/手动匹配；TVmaze/Wikidata；豆瓣（默认关）；IMDb 导入 | 断网/无 token 全库可扫可匹；误配抽样达标；provider 冷却生效；豆瓣默认不联网 |
| **F TV 清单**（3–5d） | 集解析/表/浏览页/播放 `kind` | TV 库可扫、按剧/季/集浏览与播放；不改名不刮削；乱命名有 `needs_review` |
| **G 文档与回归**（1–2d） | 更新 README/AGENTS/本文档；smoke 脚本 | `scripts/smoke_multi_library.py` / `smoke_metadata_offline.py` 通过；pytest 全绿 |

后续版本：完整 TV（TMDB TV、tvshow/episode NFO、Plex 剧集命名与 `.plexmatch`、TV 改名整理）；电视端播放路线（Android TV 浏览器/DLNA/投屏）另行评审。

测试与验证（沿用仓库习惯）：pytest（`tests/`）+ `scripts/smoke_api.py` 扩展双库 fixture + 新增只读 smoke 脚本（建两库→放同名文件→扫描→断言搜索/统计隔离→删库→断言文件仍在）；迁移在 `data/jzmedia.db` 副本上预演。

---

## 14. 风险与对策

| 风险 | 对策 |
|---|---|
| v12 重建三表 + FTS（不可逆） | 单事务、幂等、三种库形态自测；文档要求备份 `data/jzmedia.db`；发布说明写明回退需恢复备份 |
| 跨库聚合遗漏（同 tmdb 串味） | grep 清单（`COALESCE(tmdb_id`、`GROUP BY`、`expand_ids_to_versions`、`collection_members`）逐一改造 + smoke 断言 |
| SMB/NFS 抖动/挂死 | `soft,retrans=3` + 看门狗退避重挂 + 根级健康状态；播放/扫描遇失联给明确错误 |
| DSM 权限与 AppArmor | 文档给 `cap_add: SYS_ADMIN` 与 `privileged: true` 两种写法 + 宿主挂载兜底；`ALLOW_SMB_MOUNT=0` |
| 凭据与库管理安全 | Fernet + `secret.key` 0600；API 只写不读；日志/错误脱敏；建议开启 `JZMEDIA_TOKEN` |
| 只读写保护遗漏 | 写入点 grep 清单 + smoke 断言（只读库归档/上传/NFO/图片重建均 409） |
| Plex 兼容检查误报/漏报 | 规则集中在 planner 单点；预览可解释（`plex_warnings` 带原因）；实机验证 NAS 样本 |
| 离线匹配误配 | 高置信才自动绑定，否则候选 + `needs_review`；`match_source` 可追溯 |
| 爬虫反爬/合规 | 默认关闭、限速、失败冷却、仅提示私有使用 |
| 大库迁移耗时 | 迁移日志记录耗时；`INSERT SELECT` + 索引重建在几万行量级可控 |

---

## 15. 变更记录

| 日期 | 变更 |
|---|---|
| 2026-09-17 | 初稿：调研结论、独立库模型、v12 迁移、SMB 挂载管理器、P0–P4 阶段计划（待评审） |
| 2026-09-18 | 定稿：并入用户场景 9 条；确认 D1–D8（一库一根、仅 movie/tv、Fernet、扁平归档、Plex 文件级兼容、TV 只读清单、离线/降级刮削）；补 v13（TV/播放键）、`match_index`、artwork/NFO 所有权、A–G 阶段；删除 mixed 与大区分区 |
