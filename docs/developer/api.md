# API 与后台任务

[开发者文档](README.md)

运行实例的 `/docs` 是各请求体和响应字段的即时来源。下面只列业务分组及会影响客户端实现的约定，避免将文档固定在每个小字段上。

| 前缀 | 主要功能 |
|---|---|
| `/api/health`、`/api/settings` | 健康、设置来源及保存 |
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

## 认证和安全边界

`app/main.py` 中间件仅在配置令牌时保护 `/api` 下的 POST/PUT/PATCH/DELETE，接收 `X-Api-Token` 或 `Authorization: Bearer`。GET、海报、视频直链继续可读。设置变更立即改变有效令牌。新增写接口须保持 `/api` 路径和写 HTTP 方法，敏感返回避免暴露令牌与 SMB 密码；也不能通过 GET 执行实际变更。

## 任务模式

`app/jobkit.py` 的注册表保留进程内进行中及近期已完成任务。启动端点通常返回 `job_id`，前端轮询状态并可请求取消。取消属于协作式，worker 要检查停止标志，已完成子操作不自动回滚。单个任务的审计或持久结果（例如 `organize_moves`）仍在 DB，与内存任务状态分开。页面刷新或重启后，旧 job ID 不保证可查询；检查实际库状态再决定是否重启任务。

整理、恢复、缓存清理等接口以 dry-run/预览体现计划，再经显式执行参数确认。应用路由层应验证请求范围，执行层仍需再次校验只读、目标占用及源存在性。[存储设计](storage.md)。
