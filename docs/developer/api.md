# API 与后台任务

[开发者文档](README.md)

运行实例的 `/docs` 是各请求体和响应字段的即时来源。下面只列业务分组及会影响客户端实现的约定，避免将文档固定在每个小字段上。

| 前缀 | 主要功能 |
|---|---|
| `/api/health`、`/api/settings` | 健康、设置来源及保存 |
| `/api/onboarding`、`/api/tmdb/check` | 新手配置进度、TMDB 凭据直连验证 |
| `/api/media-libraries`、`/api/libraries` | 媒体库连接和视频库 CRUD/检查 |
| `/api/movies`、`/api/search`、`/api/facets` | 电影、搜索筛选、详情、匹配、批量操作 |
| `/api/tv` | 剧/季/集、待确认、手工绑定、观看和播放文件 |
| `/api/collections`、`/api/persons` | 合集、人物作品 |
| `/api/files`、`/api/fs`、`/api/extras` | 归档预览/执行、文件操作、花絮 |
| `/api/jobs` | 扫描、刮削、整理、重建 NFO/索引/元数据等任务 |
| `/api/stream` | 能力决策、会话/HLS、字幕、断点、预缓存和缩略图 |
| `/api/metadata` | 外部来源状态及诊断 |

## 库作用域

读接口可以按 `library` 视频库或 `media_library` 媒体库聚合过滤；写任务通常在 body 传 `library_id` 或 `media_library_id`。视频库工具应显式传 `library_id`，不要依赖缺省全库行为。未知媒体库的读筛选要返回空集合而非退化成全库。新增接口要覆盖“未指定、单视频库、媒体库聚合、不存在的媒体库”四种情况。

当前四档播放由 POST `/api/stream/{id}/decide` 接收 `caps`；GET 兼容变体仅用保守默认。电影、分集、花絮播放时通过 `kind` 隔离媒体探测、进度和会话。文件直链及 HLS 分片读取是 GET，支持断点/Range 的接口不能被通用 JSON 包装破坏。

## 客户端例子

以下 id 均为示例值，演示用隔离环境或只读查询，不要对真实库执行写操作。

范围过滤（`library` 视频库，`media_library` 媒体库聚合；未知媒体库返回空集合）：

```http
GET /api/movies?media_library=2&limit=1
→ {"items": [{"id": 875, "title": "古董局中局", "library_id": 2, ...}]}

GET /api/movies?media_library=9999&limit=1
→ {"items": [], "has_more": false, ...}
```

任务启动/查询/取消（扫描示例；响应 `state` 含 `idle/running/done`）：

```http
POST /api/jobs/scan {"library_id": 2}
→ {"job_id": "…", "resumed": false, ...}

GET /api/jobs/scan
→ {"state": "idle"}              # 无任务时
→ {"job_id": "…", "state": "running", "done": 12, "total": 300, ...}

POST /api/jobs/scan/{job_id}/cancel   # 协作式取消，已完成项保留
# 不同范围的扫描并发启动返回 409（“另一个范围的扫描正在进行”），同范围返回 resumed:true 复用。
```

归属预览/执行（token 15 分钟有效；磁盘或规则变化后确认返回 409，需重新预览）：

```http
POST /api/tv/bindings/preview {"library_id": 3, "target_show_id": 72, "directories": [{"path": "Season 01"}]}
→ {"groups": [...], "conflicts": [...], "can_apply": true, "token": "…"}

POST /api/tv/bindings/apply {"token": "…", "dry_run": true}   # 先预览
POST /api/tv/bindings/apply {"token": "…"}                    # 再执行
```

`kind` 隔离播放：电影 `m{id}`、分集 `e{id}`、花絮 `x{id}` 的探测、断点、会话目录互相隔离；`kind=episode` 的版本接口只返回同剧同季同集的多版本。`library_id` 指视频库，`media_library_id` 指媒体库，写任务优先显式传视频库 id。

## 详情图片

`GET /api/movies/{id}/backdrop` 优先返回本地横版背景缓存；缺图时只按已有 TMDB 缓存中的图片路径下载，不重新刮削影片。缺少背景为 404，下载失败为 502，前端保留渐变底色；图片请求独立于电影详情 JSON，避免拖慢资料加载。季和单集详情的 `show_backdrop_path` 沿用所属剧集的本地背景，空字符串表示暂无图片。

## 认证和安全边界

`app/main.py` 中间件仅在配置令牌时保护 `/api` 下的 POST/PUT/PATCH/DELETE，接收 `X-Api-Token` 或 `Authorization: Bearer`。GET、海报、视频直链继续可读。设置变更立即改变有效令牌。新增写接口须保持 `/api` 路径和写 HTTP 方法，敏感返回避免暴露令牌与 SMB 密码；也不能通过 GET 执行实际变更。

## 任务模式

`app/jobkit.py` 的注册表保留进程内进行中及近期已完成任务。启动端点通常返回 `job_id`，前端轮询状态并可请求取消。取消属于协作式，worker 要检查停止标志，已完成子操作不自动回滚。单个任务的审计或持久结果（例如 `organize_moves`）仍在 DB，与内存任务状态分开。页面刷新或重启后，旧 job ID 不保证可查询；检查实际库状态再决定是否重启任务。

整理、恢复、缓存清理等接口以 dry-run/预览体现计划，再经显式执行参数确认。应用路由层应验证请求范围，执行层仍需再次校验只读、目标占用及源存在性。[存储设计](storage.md)。

## 上传与浏览体验接口

- `POST /api/uploads` 保持电影返回字段兼容；查询参数新增 `media_type=movie|tv`、`mode=files|dir`。类型必须与目标视频库一致，停用和只读库返回 409。TV 散文件须传 `show_id` 或 `show_title`，以及 `season`；已有剧必须属于目标库。返回 `media_type/library_id/show_id/episode_id/status`，无法识别的文件返回 `skipped_tv_unknown`，写入成功但登记异常为 `stored_scan_warn`，不要当作已成功入库。TV 资料补全另用 `tv-scrape {library_id, ids, force:true}`，整理仍调用预览/确认接口。远程上传使用 `open_write(overwrite=False)` 原子提交，目标并发出现同样返回 409。
- `GET /api/jobs/scan` 返回正在运行或最近的扫描任务，墙上与设置工具共用状态。扫描和 TV 资料任务仅复用相同作用域/选项；不同范围返回 409，不能把另一库的任务当成本次完成。
- `GET /api/tv/episodes/{id}` 新增 `previous_episode/next_episode`，与 `/next` 使用相同版本与多集区间规则。季详情支持 `version` 筛选和分页，新增 `versions/distinct_count`；`episode_count/watched_count` 仍为全季文件数。
- `/api/tv/stats` 保留 `shows/episodes`，增加 `seasons/pending/episode_review/by_library`；电影 `/api/jobs/stats` 增加 `by_library` 待办统计。概览待办深链指定具体 `library`。
- `rebuild-meta` 新增 `nfo` 开关（默认 true），与 `artwork` 独立，至少选择一项；旧客户端行为不变。单片与全库修复仍分别使用 `ids` 和 `library_id`。

## 剧集目录归属

Schema v28：`tv_directory_bindings` 持久化 `(library_id, path) → (show_id, season, override_season)`；`tv_binding_history` 保存预览、执行快照与撤销状态。分集增加 `match_source` 和 `binding_conflict`。扫描按最长路径前缀应用规则，目录整理/恢复同步改写规则路径；已确认目录的资料刷新只按精确季集匹配。

| 接口（前缀 `/api/tv/bindings`） | 用途 |
|---|---|
| `GET /directories?library_id=&show_id=` | 仅从 SQLite 返回已扫描目录与绑定，首屏不访问媒体存储；`show_id` 可选 |
| `POST /directories/refresh` | `{library_id, show_id?}`；启动实体目录盘点，发现尚未扫描的目录 |
| `POST /suggest`、`POST /suggest/start` | `{library_id, paths, tmdb_id?}`；最多 12 个目录，`/start` 为后台任务版本 |
| `POST /preview`、`POST /preview/start` | `{library_id, tmdb_id, target_show_id?, directories:[{path, season?, override_season?}], replace_manual?, allow_duplicates?}`；最多 50 个非嵌套目录 |
| `POST /apply`、`POST /apply/start` | `{token}`；执行服务器保存的预览，15 分钟有效，同 token 重试幂等 |
| `GET /jobs/{job_id}` | 查询盘点、建议、预览或执行任务；结果位于完成态的 `result` |
| `GET /history?library_id=` | 最近 30 条已执行/已撤销记录 |
| `POST /undo` | `{token, dry_run:true}` 预览；`dry_run:false` 撤销 |

预览返回 `groups/conflicts/warnings/duplicates/can_apply`；只有无冲突才返回 `token`。确认重新校验磁盘指纹、源目录快照、目标剧与分集签名，在 SQLite 事务内变更归属和季号，沿用现有 episode ID。观看状态不参与过期校验、不随撤销回滚。规则/路径/新增分集变化会拒绝旧预览或撤销（409）；存储不可读返回 503，资料请求 HTTP 错误返回 502。接口不移动或写入媒体文件，可用于只读媒体库。规则绑定的剧在普通重新匹配入口禁止直接换成其他条目，改用归属弹窗。

## 新手配置

- `GET /api/onboarding` 仅查询本地数据，返回 `version/status/step/library_id/kind/import_mode/tmdb_skipped`、最近一次 `upload_result` 和派生的 `target_valid/library_verified/tmdb_verified/upload_allowed/max_step/can_complete/show_welcome/content`。`content` 含全局记录数、目标库入库数、待处理数和最多 5 条详情入口数据；读取不触发网络诊断、媒体盘点或任务。
- `PATCH /api/onboarding` 部分更新步骤（1–4）、目标、导入方式及 `status=active|deferred|completed`。目标可以清空。初始状态为 `not_started`；进度按实例保存至 `app_settings.onboarding_state`，无需新增表或迁移。客户端报告的上传数量仅供展示，不作为入库完成证据；完成条件不足返回 409，非法字段返回 422。
- `POST /api/tmdb/check` 不接收临时凭据，使用已保存的有效配置和代理直接访问 TMDB `/3/authentication`。单请求短超时、不重试，不查缓存或备用来源。诊断成功或上游错误均返回 200 和 `{ok, code, message, elapsed_ms}`；上游 401 映射为 `invalid_credentials`，不冒充应用鉴权错误。应用自身未授权仍由中间件返回 401。
- `POST /api/libraries/{id}/check` 沿用原响应，记录与当前库配置关联的检查结果。配置变化后旧检查失效。只读不妨碍扫描和完成入库。
- 向导沿用 `/api/settings`、媒体库／视频库接口、`/api/jobs/scan` 与 `/api/uploads`，所有操作显式指定目标视频库。`UploadDialog.libraryId` 可固定上传目标；`busy` 和 `result` 事件支持页面离开保护和结果展示，原 `done/close` 保持兼容。

TMDB／存储凭据不保存到引导公开状态；内部验证指纹不返回客户端。升级安装已有电影或分集时不自动展示欢迎卡片，自动生成的空默认库则仍需引导。
