---
version: 0.19.0
reviewed: 2026-10-02
---

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
| `/api/ai` | 智能辅助配置、连接检查、搜索条件与匹配建议 |

## 库作用域

读接口可以按 `library` 视频库或 `media_library` 媒体库聚合过滤；写任务通常在 body 传 `library_id` 或 `media_library_id`。视频库工具应显式传 `library_id`，不要依赖缺省全库行为。未知媒体库的读筛选要返回空集合而非退化成全库。新增接口要覆盖“未指定、单视频库、媒体库聚合、不存在的媒体库”四种情况。

当前四档播放由 POST `/api/stream/{id}/decide` 接收 `caps`；GET 兼容变体仅用保守默认。电影、分集、花絮播放时通过 `kind` 隔离媒体探测、进度和会话。文件直链及 HLS 分片读取是 GET，支持断点/Range 的接口不能被通用 JSON 包装破坏。

## 客户端例子

### 文件管理变更与扫描

`GET /api/fs/changes?library=<id>` 返回该视频库的 `{library_id,pending,count,revision,actions,last_changed_at,paths,active_jobs}`；省略 `library` 返回 `{items,count}`。变更按成功物理操作写入持久日志（schema v29）；预览和未落盘的失败不记入，部分成功保留。`paths` 仅展示最近至多 20 个路径。

`active_jobs` 包括收到取消请求但 `worker_finished:false` 的复制任务；客户端不能仅凭 `state:cancelled` 判断后台写入已经结束。同库仍在复制时启动扫描返回 409。成功完整扫描只清除开始时的变更水位；失败、取消、离线或扫描过程中新增的变更仍待核对。扫描结果 `summary.fs_changes` 提供已清除数量及剩余摘要。

电影与分集的改名/移动保留原记录 ID 和播放断点；删除立即同步记录。`rename/move` 预览中的 `followers` 列出同茎关联文件及冲突。`GET /api/fs/list` 的 `limit/offset/has_more/total_files` 需要完整处理，不能只显示默认第一页。

### 文件内容预览与下载

`GET /api/fs/blob?library=<id>&path=<库内相对路径>` 精确读取指定视频库的文件，`library` 和 `path` 必填；未知库/缺失文件/目录返回 404，非法或越界路径返回 422，拒读返回 403，离线返回 503。只读库可读取；不回退默认库、不猜同名文件、不创建媒体记录或待扫描变更。

默认下载原文件，支持本地与远程 Range（206 / 416）；`inline=1` 仅对允许的图片、PDF、视频类型内嵌显示，其他类型仍按附件下载。`mode=text` 仅返回前 65536 源字节解码后的纯文本，响应头 `X-Preview-Truncated: true|false`、`X-Preview-Limit: 65536` 说明截断情况。HTML/SVG 不作为页面执行。

已入库视频预览仍使用现有电影/分集/花絮播放接口；前端 `PlayerModal.preview` 默认 `false`，文件预览设为 `true` 后禁用观看进度读写、清除、已看和连播事件。未入库视频仅原文件预览，不新增临时入库身份。

### 匹配规则测试

`GET /api/metadata/test-search?library=<id>&q=<片名>` 必须指定存在的视频库和非空片名。服务端根据库类型选择电影或剧集，并严格按该库保存的来源顺序查找；响应为 `{library_id,kind,chain,items,source,elapsed_ms}`。空结果的 `source` 为 `null`，不能视为连接检测成功。此接口不调用 AI，也不绑定媒体；TMDB 凭据直连验证仍使用 `POST /api/tmdb/check`。

### 常用调用

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

归属规则、历史记录及分集字段见[数据模型](data.md#主要实体)。扫描按最长路径前缀应用规则，目录整理/恢复同步改写规则路径；已确认目录的资料刷新只按精确季集匹配。

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

## 智能辅助

AI 服务默认关闭。所有产生外部调用的入口使用 POST，沿用应用写操作鉴权；GET 设置只返回公开配置与用量，不触发模型请求。

| 接口 | 请求与行为 |
|---|---|
| `GET /api/ai/settings` | 返回启用状态、服务商、地址、模型、超时、每日上限、`api_key_set/api_key_masked/api_key_source` 和当日 `usage`，不返回完整 Key |
| `PATCH /api/ai/settings` | 部分更新 `enabled/provider/base_url/model/api_key/timeout_seconds/daily_limit`；完整校验成功才保存，保存后清空 AI 结果缓存 |
| `POST /api/ai/check` | 使用已保存的有效配置做一次真实 JSON 输出检查；关闭智能辅助时也可显式测试，不使用缓存，计入当日次数 |
| `POST /api/ai/search` | `{q, kind:"movie"\|"tv", media_library_id?, library_id?}`；`q` 最多 500 字符，返回 `filters/summary/warnings` 供用户核对，不执行本地查询 |
| `POST /api/ai/match` | `{kind:"movie"\|"tv", id}`；按该行所属视频库查候选，返回 `query/year/candidates/summary/warnings`，不更改绑定或媒体文件 |

配置 `provider` 为 `deepseek`、`opencode_go` 或 `compatible`。地址是 API 根目录，客户端追加 `/chat/completions`，不跟随重定向；不继承 TMDB 代理或系统代理。`opencode_go` 只接受 `https://opencode.ai/zen/go/v1`，UI 预填模型 `glm-5.3-flash`，直接传模型 ID、不加 `opencode-go/` 前缀；仅支持 Chat Completions，不切换到 Messages 或 Responses 协议。自定义兼容服务允许空 Key，DeepSeek 与 OpenCode Go 要求 Key。API 部分更新不会因仅修改 `provider` 就自动重置地址或模型，应一并提交完整有效组合。

`api_key` 空串表示保留；`clear_api_key:true` 明确删除 DB 密钥并恢复环境值，不可与新 Key 同时提交。只有一套有效配置，切换服务商时由操作者填写对应 Key，不存在分服务商密钥槽位。配置错误返回 400，请求体不符合业务 schema 返回 422。

OpenCode Go 请求使用真实 jzmedia User-Agent 和每次业务操作的 `x-opencode-session`；一次匹配的标题提取与排序共享会话，不同操作独立。会话不含媒体路径或凭据，不进入结果缓存键。官方目前面向编程代理，本接口不伪装编程任务，也不绕过用途限制；影视用途及真实连通性未验证。参见[官方使用范围](https://opencode.ai/docs/go/#where-can-i-use-it)。

有效配置按 DB → 对应环境变量 → 默认值解析，环境变量为 `AI_ENABLED/AI_PROVIDER/AI_BASE_URL/AI_MODEL/AI_API_KEY/AI_TIMEOUT_SECONDS/AI_DAILY_LIMIT`。默认关闭、超时 12 秒、每日上限 100 次；超时可设 2–60 秒，每日上限为 1–10000 次。设置存在数据库中，脱敏回显不等于加密存储。

`usage` 含 UTC `date`、`requests/input_tokens/output_tokens`。每次实际尝试在网络请求前原子预占次数，失败也计次；本地输入校验失败、未启用、缺少必需 Key 或缓存命中不计次。Token 按上游返回的有效使用量累加，未报告时不推算费用。匹配最多调用两次模型；第二次失败时仍可返回来源已验证的候选，但注明尚未完成 AI 核对。

搜索范围由请求上下文在服务器解析，显式不存在或无对应类型的视频库返回 `empty_scope`，不会扩为全库。允许的输出只有关键词、类型、产地、国家、年份/年代、标签、最低评分与来源、观看状态、排序和剧集连载状态；模型不能返回库 ID、影片 ID、SQL 或额外字段。电影关键词搜索仍按相关度，TV 不支持豆瓣评分。当前接口不支持时长、排除类型、年龄适宜性或向量相似搜索，无法表达的要求应列为提示，不能伪造已实现的条件。

匹配候选的 `bindable` 由服务器根据 TMDB 标识、详情来源、可信外源缓存及既有目录归属确定；`false` 时前端不展示绑定按钮，显示 `bind_reason`。原因可能是仅有索引线索，也可能是剧集已有目录绑定、需要通过“归属与季号”调整；已有目录绑定仍允许确认当前同一 TMDB 作品。外源确认通过既有 `/bind-external` 接口按来源和 ID 恢复可信缓存或取得详情，缺失详情不能用当前旧标题冒充新绑定。业务校验拒绝的模型结果会移出 AI 缓存，重试可重新请求。

连接检查及业务请求遇模型不可用时返回 HTTP 200 与 `{ok:false, code, message}`，包括 `disabled/not_configured/invalid_credentials/rate_limited/timeout/daily_limit/invalid_result` 等；上游 401 不转换成应用令牌提示。条目不存在返回 404，应用自身鉴权失败仍为 401。前端保留普通搜索与手动匹配，只有用户确认建议后才调用原有查询或绑定接口。[架构与验收边界](metadata.md#智能辅助的边界)。
