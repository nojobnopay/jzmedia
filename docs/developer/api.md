# API 与后台任务

[开发者文档](README.md)

运行实例的 `/docs` 是各请求体和响应字段的即时来源。下面只列业务分组及会影响客户端实现的约定，避免将文档固定在每个小字段上。

常用入口：[库作用域](#库作用域) · [认证和权限](#认证和安全边界) · [任务模式](#任务模式) · [健康与容量](#健康与磁盘容量) · [缩略图与缓存](#进度缩略图与缓存) · [客户端例子](#客户端例子)。

| 前缀 | 主要功能 |
|---|---|
| `/api/health`、`/api/data-usage`、`/api/settings` | 健康、应用数据占用、设置来源及保存 |
| `/api/onboarding`、`/api/tmdb/check` | 新手配置进度、TMDB 凭据直连验证 |
| `/api/media-libraries`、`/api/libraries` | 媒体库连接和视频库 CRUD/检查 |
| `/api/movies`、`/api/search`、`/api/facets` | 电影、搜索筛选、详情、匹配、批量操作 |
| `/api/tv` | 剧/季/集、播出与收藏对照、待确认、手工绑定、观看和播放文件 |
| `/api/tv-client` | TV 客户端的片名/拼音搜索、本库演员候选和参演作品 |
| `/api/collections`、`/api/persons` | 合集、人物作品 |
| `/api/files`、`/api/fs`、`/api/extras` | 归档预览/执行、文件操作、花絮 |
| `/api/jobs` | 扫描、刮削、整理、重建 NFO/索引/元数据等任务 |
| `/api/stream` | 能力决策、会话/HLS、字幕、断点、预缓存和缩略图 |
| `/api/cast` | DLNA 发现、前台电视 App 接收、播放控制、投屏状态和电视取流 |
| `/api/metadata` | 外部来源状态及诊断 |
| `/api/viewing` | 观看模式、内容可见性与儿童筛选资料补全 |
| `/api/ai` | 智能辅助配置、连接检查、搜索条件与匹配建议 |
| `/api/online/settings`、`/api/online/sources`、`/api/online/network` | 在线功能开关、来源、网络配置及持久身份；当前仅提供管理接口 |

## 库作用域

读接口可以按 `library` 视频库或 `media_library` 媒体库聚合过滤；写任务通常在 body 传 `library_id` 或 `media_library_id`。视频库工具应显式传 `library_id`，不要依赖缺省全库行为。未知媒体库的读筛选要返回空集合而非退化成全库。新增接口要覆盖“未指定、单视频库、媒体库聚合、不存在的媒体库”四种情况。

文件操作显式指定的视频库不存在时返回 404；只有省略库参数的兼容入口才使用默认库。扫描全部时仅处理视频库及其父媒体库均已启用的范围，全部停用时不启动默认库扫描。

当前四档播放由 POST `/api/stream/{id}/decide` 接收 `caps`；GET 兼容变体仅用保守默认。电影、分集、花絮播放时通过 `kind` 隔离媒体探测、进度和会话。文件直链及 HLS 分片读取是 GET，支持断点/Range 的接口不能被通用 JSON 包装破坏。

网页 `caps.mp4_compat=true` 使用普通 MP4 短片兼容传输；`versions.transport` 为 `mp4`，`sessions` 返回 `media_url`、空 `playlist_url`，并保留 `media_start/initial_time`。`clip_duration` 为完成文件的实际秒数，客户端据此准备下一片，不能依赖缺失的浏览器媒体元数据；其他传输此字段为 0。MP4 的 `complete` 只表示当前片段完成，客户端不得把它当作全片结束；获取下一片仍用原片时间创建会话，关闭时释放当前和预取 sid。格式及时间轴规则见[播放核心设计](playback.md#浏览器检测与兼容回退)。

## 认证和安全边界

写操作令牌、当前设备的观看会话和管理解锁分别检查，提供写令牌并不代表已解锁管理或隐私内容。

### 写操作令牌

配置 `JZMEDIA_TOKEN` 或设置页令牌后，`app/main.py` 对 `/api` 下的 POST/PUT/PATCH/DELETE 校验 `X-Api-Token` 或 `Authorization: Bearer`；两者同时提供时优先使用非空 `X-Api-Token`。设置变更立即改变有效令牌。`/api/viewing/mode`、`unlock`、`lock`、`activity` 四个观看会话端点不要求此令牌，仍执行各自的会话与密码检查；密码设置、内容策略修改和其他写操作继续要求令牌。

设置页新令牌须至少 8 位，使用可打印 ASCII 字符（英文字母、数字或半角符号），不能包含中文、全角字符或控制字符。`PUT /api/settings` 先校验本次全部字段，再在同一事务中保存；字段校验或数据库写入失败时，不保留本次请求的部分修改。字段省略或为 `null` 时保持原值，显式空串恢复对应环境配置或默认值。

网页播放的 POST `decide/sessions/progress` 同样需要写令牌。常规 GET、海报或媒体读取不受写令牌限制，仍受下述观看模式限制；在线来源配置是例外，其管理 GET 也校验已配置的访问令牌。新增配置或媒体写操作须使用 `/api` 路径和写 HTTP 方法，不能通过 GET 绕过令牌；现有读取接口可能按需填充图片或探测缓存。

### 观看会话与管理解锁

`GET /api/viewing/session` 创建或读取当前设备会话，返回 `mode`、`enabled`、`management_unlocked`、`generation` 等状态。网页使用服务端设置的 `jz_viewing` HttpOnly Cookie；电视以 `X-Viewing-Client: tv` 请求该接口，从响应读取 `session_token`，后续携带 `X-Viewing-Session`。会话凭据与写操作令牌独立，不能互相替代。

启用保护后，普通、儿童、隐私模式分别限制当前设备可见的列表、详情、图片和媒体。管理接口还需有效管理授权，GET 请求也可能被拒绝。请求范围由 `app/viewing_http.py` 与 `app/viewing.py` 共同检查；公开用户操作见[儿童模式与隐私模式](../user-guide/settings.md#儿童模式与隐私模式)。

`POST /api/viewing/unlock` 接收保护密码并授予当前会话管理权限，不切换观看模式；儿童模式需先通过 `/api/viewing/mode` 验证密码退出，再解锁管理。进入隐私模式同样使用 `/mode`。`/lock` 撤销管理与隐私授权，儿童模式保持；密码变更会撤销既有授权。客户端需识别响应的 `X-Viewing-Generation`，在模式或授权变化后刷新内容，清除不再可用的页面状态。

### 区分认证失败

| 响应 | 含义 | 客户端处理 |
|---|---|---|
| `401`，无观看保护 `code` | 写操作令牌缺失或错误 | 核对实例令牌 |
| `401`，`code=viewing_locked` | 设备会话失效或隐私授权过期 | 按会话状态重新连接或验证密码 |
| `403`，`code=management_required` | 当前会话未解锁管理 | 退出儿童模式后验证保护密码 |

保护密码错误另以 `protection_password_invalid` 返回，不能当成写令牌错误。敏感响应、日志和诊断不得泄露保护密码、会话凭据、写令牌或 SMB 密码。

## 在线来源配置

这些接口只保存开关与来源配置，不执行外部搜索、连接检查、播放地址解析或媒体取流。总开关和新来源均默认关闭；关闭时仍可管理来源，以便先完成配置。正式网页入口为“在线”，管理入口为“设置 → 在线视频源”；目录与播放分别使用下述独立接口。

| 请求 | 行为 |
|---|---|
| `GET /api/online/settings` | 返回总开关 `{enabled:false}` |
| `PATCH /api/online/settings` | 必须提供布尔 `enabled`，持久保存总开关 |
| `GET /api/online/sources` | 返回 `{items:[...]}`，按优先级、创建时间和 ID 排序 |
| `POST /api/online/sources` | 创建来源并返回 `201`；返回持久随机 ID |
| `GET /api/online/sources/{source_id}` | 读取单个来源；不存在返回 `404` |
| `PATCH /api/online/sources/{source_id}` | 原子更新已提供的字段；可用 `enabled:false` 停用并保留身份 |

创建来源必须提供 `name`、`provider`、`api_base_url`，提供方支持 `maccms` 与固定开放样例 `open_catalog`。可选 `allowed_hosts` 为精确主机名列表，自动包含 API 主机，不支持通配符；`enabled` 默认 `false`，`priority` 为 0–1000 的整数、默认 100，数值越小越优先。地址只接受无内嵌账号密码、查询参数或片段的 HTTP(S) 基础地址；保存时只检查结构和字面地址，不发 DNS 或网络请求。实际读取执行[公网目标与头部策略](playback.md#在线远程读取)，保存成功不代表来源已经可播放。

`provider_options` 为严格对象，`search_action` 可选 `list/detail/videolist`、默认 `list`，`detail_action` 可选 `detail/videolist`、默认 `detail`。更新时提交该对象会整体替换，未提供的子项恢复默认；省略对象则保留原值。MacCMS 据此选择 Provide JSON 的 `ac` 参数；开放样例忽略这两个兼容选项。开放样例使用固定基础地址与媒体主机，见[配置参考](../getting-started/configuration.md#在线视频源)。

更新时省略字段保持原值，显式 `null`、未知字段、修改 `provider` 或无效地址返回 `422`，本次其它字段也不保存。停用来源或修改其 `api_base_url`、`allowed_hosts`、`provider_options` 会撤销相关活动会话；仅改名或优先级不撤销。验证错误不回显提交的地址或凭据。上述全部管理接口（包括 GET）均校验已配置的访问令牌，保护启用时还需管理解锁；令牌未配置时保持现有局域网开放行为。配置响应使用 `Cache-Control: no-store`。

稳定身份见[在线数据模型](data.md#在线来源与稳定身份)，播放描述的同源路径与处理约束见[在线播放描述](playback.md#在线播放描述)。

## 在线网络管理

这些接口沿用在线来源配置的管理解锁、访问令牌及 `no-store` 规则。关闭在线功能时仍可配置；请求不连接代理或目标站点，不提供任意 URL 的连通性测试、解析或转发。

| 请求 | 行为 |
|---|---|
| `GET /api/online/network/profiles` | 返回 `{items:[...]}`，仅包含公开配置和 `has_credentials` |
| `POST /api/online/network/profiles` | 创建直连或代理档案并返回 `201` |
| `GET /api/online/network/profiles/{profile_id}` | 读取档案，凭据不回显 |
| `PATCH /api/online/network/profiles/{profile_id}` | 部分更新；类型不可更改，省略凭据保留，明确清除或替换凭据 |
| `DELETE /api/online/network/profiles/{profile_id}` | 未被引用时删除并返回 `204`；存在引用返回 `409` |
| `GET /api/online/network/default` | 返回应用默认 `{rule,revision}`，初始规则为 `direct` |
| `PATCH /api/online/network/default` | 必须提供完整 `rule`；应用默认不能选择 `inherit` |
| `GET /api/online/sources/{source_id}/network` | 返回 `{source_id,policy,revision}`；未配置的来源规则默认全部继承 |
| `PATCH /api/online/sources/{source_id}/network` | 部分更新来源 `default` 或 `api/media/image/subtitle/subscription` 规则；每个已提供的规则整体替换 |
| `GET /api/online/sources/{source_id}/network/effective?purpose=media` | 解释指定用途的有效出口、选择层级、档案与修订号、配置指纹及 DNS 边界；不连接站点 |

档案必需 `name`、`mode`。`proxy` 档案还需 `proxy_url`；账号密码使用只写的 `username/password` 字段，不能嵌在地址中。可配置连接/读取超时、每池连接数和 CA 路径；完整参数范围及凭据更新语义见[配置参考](../getting-started/configuration.md#在线来源的网络策略)。未知字段、显式 `null`、错误类型或不匹配的档案引用返回 `422`，不存在的来源或档案返回 `404`。错误不反射提交的凭据或地址。

规则格式为 `{mode:"inherit"|"direct"|"proxy",profile_id:null|"np_..."}`；`inherit` 不能带档案，`proxy` 必须引用代理档案，`direct` 可不带档案或引用直连档案。例如将 API 设为代理、媒体设为直连时，提交两个对应用途规则；其它用途保持继承。

有效规则解释不包含代理凭据。直连与代理均返回 `target_dns:"local_pinned"`、`destination_validation:"required_per_request"`，表示内部读取客户端必须在每次请求执行目标策略；该端点不解析 DNS、不连接站点，也不验证来源已经可播放。代理目标使用已批准公网 IP 的 CONNECT 隧道，无法使用该隧道时失败，不切换出口。

`create_online_client(source_id,purpose)` 是提供方与会话网关使用的服务端内部工厂，从同一数据库读快照检查总开关、来源启用状态，并绑定来源允许主机与网络策略。API 查询用 `api`，媒体与 HLS 子资源用 `media`。上游认证授权由服务端独立的 `HeaderGrant` 提供，不接受管理请求中的 jzmedia 令牌、观看 Cookie 或任意上游请求头。GET/HEAD、逐跳重定向与认证隔离的完整约束见[在线远程读取](playback.md#在线远程读取)。公开读取仅使用固定来源的目录或已登记会话和资源，不提供任意 URL 转发接口。

## 在线目录

目录接口需要有效设备观看会话，即使未启用保护也须先调用 `GET /api/viewing/session`。它们不要求管理解锁，儿童模式仍拒绝；来源管理接口继续要求管理权限。只允许下表中的精确 GET 路径和参数，未知或重复参数返回固定 `422 invalid_online_catalog_request`。

| 请求 | 返回与行为 |
|---|---|
| `GET /api/online/catalog/sources` | `{enabled,items:[{id,name,provider,priority,capabilities}]}`；总开关关闭时列表为空，不访问上游 |
| `GET /api/online/catalog/search?source_id=src_...&q=片名&page=1` | 单源搜索；返回来源、元数据条目、`page/page_count/total` |
| `GET /api/online/catalog/detail?source_id=src_...&external_id=123` | 返回稳定条目身份、简介、年份、全部线路、分集和可选归因链接 |

`q` 最多 100 字符，MacCMS 要求非空；开放样例可空词浏览。`page` 为 1–1000；`external_id` 为 1–128 个 ASCII 字母、数字、下划线或连字符。搜索只给 `external_id` 与文本元数据，不创建本地媒体记录；详情按来源和内容种类保存独立在线身份。

详情的每条线路为 `{id,label,entries}`，播放项为 `{label,identity,transport,supported,reason}`。支持项携带稳定播放身份；不支持的封装、播放器网页或不在允许主机中的目标返回 `supported:false`、`identity:null`，不泄露上游地址。简介按纯文本显示；当前页面不请求外部封面。只有开放样例归因中的官方项目和许可链接可作为外部超链接返回，不能当作媒体入口。

搜索、详情和播放解析共用有界提供方入口：每进程最多 4 个、每设备最多 1 个活动查询，无等待队列；30 秒总期限覆盖 DNS、连接池等待和上游读取。JSON 最多 2 MiB、每页最多 100 项、每条目最多 16 线路、每线路最多 500 项、总共最多 2000 分集。取消 HTTP 查询或创建会话会中断上游 I/O，并由实际工作退出后释放槽位；取消等待不提前释放仍在工作的配额。

读取前后复核观看权限、总开关、来源启用及影响解析的来源配置指纹；变化返回 `online_source_changed`，不把旧结果交付成新配置的结果。错误使用固定 `code`：例如 `provider_catalog_busy` 为 `429`，`provider_timeout` 为 `504`，上游限流为 `provider_rate_limited`，不把上游认证错误当成 jzmedia 登录失败，也不返回上游响应正文。

### 最近来源观察

`GET /api/online/catalog/health?source_id=src_...` 需要同样的设备观看身份，不需要管理解锁。它只解释已有观察与当前配置，不发上游请求、不解析 DNS、不解密代理凭据或建立 TLS 上下文。未知或重复查询参数返回固定 `422`。

响应为 `{source_id,cache_scope:"process",max_age_seconds:1800,purposes:{api,media}}`。每个用途包含 `state`（`unknown/success/failure/stale/disabled/local`）、有效 `mode`（`direct/proxy`）、`configuration_fingerprint`、观察时间、距今秒数、`stage`（`catalogue/manifest/bytes`）、固定类别、耗时与 `reason`；不返回目标地址、代理凭据或路径。取消不作为来源失败；旧操作晚到的结果不会覆盖更新的操作。含义和容量边界见[在线来源观察](playback.md#在线来源观察)。

## 在线收藏与进度

`/api/online/state` 接口使用设备观看 Cookie 或既有电视观看会话头，即使保护关闭也不可省略；不需要管理解锁，儿童模式拒绝。服务端在同一数据库事务内确定设备、普通／隐私模式及权限，客户端不能传 owner、mode、上游地址或本地媒体 ID。所有响应使用 `no-store`，错误返回固定 `code`，不反射输入。

| 请求 | 返回与行为 |
|---|---|
| `GET /api/online/state/library?section=continue&limit=20&offset=0` | 收藏、继续观看或历史列表，返回 `{items,total,limit,offset,generation,scope_token}` |
| `GET /api/online/state/items/{item_id}?episode_id=ep_...&line_id=ln_...` | 读取收藏状态、条目最近进度与精确选中身份进度，以及当前可播放状态 |
| `PUT /api/online/state/items/{item_id}/favorite` | 请求 `{favorite:true}` 或 `{favorite:false}`，返回条目 ID 和最终收藏状态 |
| `DELETE /api/online/state/items/{item_id}/history` | 删除该条目所有分集／线路进度，返回删除数量与新的 `scope_token` |
| `POST /api/online/state/progress/claim` | 请求 `{session_id,expected_revision}`，为已归属会话申请写入资格 |
| `POST /api/online/state/progress` | 提交当前 writer 的有序位置、暂停与真实完播快照 |

`section` 为 `favorites/continue/history`，`limit` 为 1–100、默认 20，`offset` 为 0–1000。列表项为 `{item,source,favorite,progress,available,reason}`；条目包含稳定 ID、来源、提供方外部 ID、类型和标题，来源只给名称、类型及启用状态。`available` 描述总开关与来源启用状态，不替代解析或设备播放验证。最近已完成内容不进入“继续观看”；未成功写入首帧后的进度也不进入历史。读取单项返回 `{item_id,favorite,last_progress,selected_progress,available,reason,generation,scope_token}`，按可选分集和线路选择精确断点，不自动借用另一线路的位置。

所有成功状态响应都包含服务端 `generation` 与不透明 `scope_token`。每次 PUT、POST、DELETE 必须携带发起该意图时冻结的 `X-Online-State-Scope`，同时遵循已配置的全局写令牌；GET 不要求这个 header。token 绑定设备、模式、观看 generation、保护 revision 与清理历史的命名空间 revision，不能把旧回调换成当前页面最新 token 后重发。失效 token 返回 `409 online_state_scope_changed`。

claim 不接收任意播放身份，而是核对 `session_id` 的设备归属。客户端先读精确选中状态，用其 `writer_revision`（没有则 0）作为 `expected_revision`。成功返回 `{identity,writer_id,writer_revision,expires_at,progress,generation,scope_token}`；GET 不返回 `writer_id`。申请资格不会改变已有播放位置或最近观看时间，未真正写入过的 claim 响应 `progress:null`；单项读取可能返回 `updated_at:0` 的资格行，仅供下一次比较 revision，不能当作已观看记录。

进度请求严格为 `{writer_id,writer_revision,seq,position,duration,paused,completed}`：`seq` 从正整数开始递增，位置为有限非负秒数，时长为正秒数或 `null`。相同序号与相同内容幂等，旧序号、旧 writer 或不同内容重用同一序号返回 `409 online_progress_writer_conflict`；发生冲突不自动重新读取和抢占 writer。writer 的 2 小时空闲期限只由完整校验通过的新序号保存延长，重复幂等包不续期，过期或被替代后不能靠迟到包恢复。用户向后 seek 可以减少位置。`completed:true` 仅描述真实 ended，要求有效时长及接近实际终点的位置，不接受归零的完播包；新一轮重播需新会话与新资格。

来源或总开关关闭后，已有记录可读、可取消收藏、可清历史，未过期且权限仍有效的已有 writer 可提交最终快照；新收藏与新 claim 要求来源启用。每设备与模式最多 500 个收藏、1000 条完整身份进度，达到相应上限返回 `429 online_favorite_limit` 或 `online_progress_limit`。普通／隐私模式和不同设备彼此隔离，不承诺跨设备账户同步。

删除历史在同一事务中递增本设备、本模式的状态命名空间，返回新的 `scope_token`；旧 writer 与在途 claim 不能将删除的记录写回。客户端采纳新 token 只用于之后新的显式操作，其它旧页面须重新开始播放。数据键与写入期限见[在线收藏与观看进度](data.md#在线收藏与观看进度)。

## 在线播放会话

会话网关与独立网页播放器连接 MacCMS JSON 或显式启用的开放样例提供方。创建前重新读取所选内容、线路与分集的当前地址；不能在请求中提交播放 URL 或上游请求头。提供方支持不等于该站点已经通过连通性或长期播放验收。前端接口及开发机隔离入口见[在线网页播放器](playback.md#在线网页播放器)。

客户端先调用 `GET /api/viewing/session` 并保留设备 Cookie；电视端使用已有 `x-viewing-session` 机制。即使保护功能关闭，创建、读取和关闭也须携带有效设备会话。启用保护后的儿童模式暂时返回 `403`，不会获得未分类的在线资源。POST 和 DELETE 沿用配置过的全局写令牌，GET/HEAD 不要求该写令牌，但必须与创建者为同一设备及有效观看 generation。

| 请求 | 行为 |
|---|---|
| `POST /api/online/sessions` | 请求体直接使用 `OnlinePlaybackIdentity`；提供方可用时返回 `201` 和同源播放描述 |
| `GET /api/online/sessions/{session_id}/media` | 读取已绑定入口；MP4 支持单 Range，HLS 返回完整重写清单，即使收到合法单 Range 探测也返回 `200` |
| `HEAD /api/online/sessions/{session_id}/media` | 读取入口元信息，不读取媒体响应体 |
| `GET/HEAD /api/online/sessions/{session_id}/resources/{resource_id}` | 读取当前会话登记的不透明资源 |
| `DELETE /api/online/sessions/{session_id}` | 关闭本设备当前会话并返回 `204`，中断其上游读取 |

身份包含 `source_id`、`item_id`、`kind`；电影的 `kind` 为 `movie`，分集为 `episode` 且必须带 `episode_id`。可选 `line_id` 为 `ln_` 加 32 位小写十六进制字符，未选择时从序列化结果省略，兼容早期单线路身份。多线路电影未选择线路时返回 `provider_line_required`；失效线路或分集明确失败，不自动改播其它线路或集。服务端核对来源、条目种类、分集及线路归属，未知身份返回 `404`。URL、headers、处理方式或其它未知字段均不接受，验证失败返回固定 `422`，不回显请求内容。播放描述固定 `processing:"passthrough"`、`time_offset_seconds:0`，不包含上游 URL、凭据或本地文件路径。

创建请求断连或取消时中断提供方的上游操作，同步工作仍持有其结果；工作结束后会回收尚未交付的会话，不能仅取消等待而遗失创建结果。描述经过序列化、连接状态和来源指纹复核后才提交；仍能返回错误响应时，创建取消使用固定 `410`、`code:"online_create_cancelled"`。这不保证网络传输一定送达客户端：响应提交后的丢包仍依赖客户端释放和服务端期限兜底。前端对收到的晚到会话执行释放，也不会把它挂到已关闭或换身份的媒体元素。

所有响应使用 `Cache-Control:no-store` 和 `X-Content-Type-Options:nosniff`。未知或其它设备的会话/资源返回 `404`；设备会话缺失、失效或观看权限撤销返回 `401`。达到会话、并发或字节预算返回 `429`，无效/超限 Range 返回 `416`，超大上游响应可返回 `413`，协议错误返回固定类别而不转发上游正文。发送响应头前耗尽单次取流总期限返回 `504`，发送后出现超时或错误则中断响应体。来源停用和关闭总开关会撤销活动会话，权限变化由新请求和每秒维护任务复核。期限、缓冲、HLS 支持范围及精确字节限额集中见[在线会话与透传限额](playback.md#在线会话与透传限额)。

## 任务模式

`app/jobkit.py` 的注册表保留进程内进行中及近期已完成任务。启动端点通常返回 `job_id`，前端轮询状态；提供取消接口的任务才可请求取消。取消属于协作式，worker 要检查停止标志，已完成子操作不自动回滚。单个任务的审计或持久结果（例如 `organize_moves`、`fs_changes`）仍在 DB，与内存任务状态分开。页面刷新可继续查询现有任务；服务重启或已完成记录被淘汰后，旧 job ID 不保证可查询，应检查实际库状态再决定是否重启任务。

整片预缓存有独立持有关系，当前没有用户取消入口，关闭播放器不会取消；使用方法与限制见[远程预缓存](../user-guide/playback-previews.md#为远程播放预缓存)。

整理、恢复、缓存清理等接口以 dry-run/预览体现计划，再经显式执行参数确认。应用路由层应验证请求范围，执行层仍需再次校验只读、目标占用及源存在性。[存储设计](storage.md)。

## 健康与磁盘容量

### 版本与源码追溯

`GET /api/health` 返回根版本源的 `version` 和 Android 递增编号 `version_code`，完整源码提交放在 `build_commit`；旧字段 `build` 保留提交前 12 位。统一构建的镜像通过构建信息提供提交，源码运行时读取本仓库 Git；无法取得提交时返回 `unknown`。这些字段用于与[发布清单](releasing.md)核对，不代表运行实例的设备兼容性已验收。

### 容量含义

`GET /api/health` 的 `disks` 返回数据目录与转码目录所在文件系统的容量，不扫描媒体库或统计缓存文件。`data`、`transcode` 各含 `ok` 和 `filesystem_id`；容量按标识去重放在 `filesystems`，每项包含 `id`、`total_bytes`、`used_bytes`、`available_bytes`。`same_filesystem` 为 `true` / `false`；某一目录的文件系统无法识别时为 `null`。标识只用于当前运行环境内的关联，不能作为跨机器或重启后的永久磁盘身份。

`available_bytes` 是应用进程可用的空间，可能少于总量减去已用量（文件系统可保留部分空间）。同卷的两个目录共享一份容量，不能相加。容量是容器/进程可见文件系统的快照，不等于媒体库大小、转码缓存限额或宿主物理磁盘总量。

`GET /api/data-usage` 另外遍历应用数据目录，返回 `total_bytes`、`warning_bytes`（应用数据总预算，默认 20 GiB）、`over_limit`、`categories`（`database/posters/previews/transcode/other`）与 `ok/error`。不跟随符号链接，不进入 `DATA_DIR/mounts` 或配置的 `MEDIA_ROOT`；不把原始媒体算进总量。占用统计缓存约一分钟，`?refresh=true` 强制重新统计；总预算及超限状态每次按当前配置计算。达到总预算会提示并暂停闲时进度缩略图，不会删除数据库或海报；两项缓存各自的回收规则仍生效。

### 采集失败与监控

采集失败时对应目录返回 `ok:false` 和 `{code,message}` 错误，`disks.ok` 为 `false`，不回显原始路径或异常；目录不存在时不创建目录，也不改查父目录猜测容量。错误码包括 `not_found`、`permission_denied`、`not_directory`、`invalid_path`、`unsupported`、`unavailable`。现有顶层 `status` 仍由数据库和媒体可读性决定；监控容量时应同时检查 `disks.ok`，空间告警阈值由部署者确定。

## 进度缩略图与缓存

### 缓存额度

`GET /api/cache-settings` 返回全服务当前生效的 `data_warning_gb`、`transcode_cache_gb`、`preview_cache_gb`，结构和数据库字段保持兼容；`data_warning_gb` 在界面解释为“应用数据总预算（软上限）”。默认分别为 20、10、10；未保存时后两项读取 `TRANSCODE_CACHE_GB` / `PREVIEW_CACHE_GB`。读取保留已有超配组合和旧播放额度 0，不因联动校验回落默认值；0 仍表示仅按 24 小时 TTL 清理。

`PUT /api/cache-settings` 要求完整提交这三个数值，单位 GiB，支持一位小数，最大 102400；保存时最小值依次为 1、0.1、0.1。非法、非有限数或未知字段返回 422。保存前强制获取最新完整占用统计：基础数据为 `database + posters + other`，其预留为基础数据加 `max(基础数据 × 10%, 1 GiB)`，向上取整至 0.1 GiB。两项缓存额度加该预留超过总预算时返回带可读原因的 422；统计失败返回 503。这些失败均不写入任何一项。

实际缓存占用超过新额度不阻止保存有效分配。成功时以单条 `app_settings.cache_limits` JSON 原子持久化，立即优先于环境变量，并唤醒闲时队列重新检查条件；本请求不执行缓存删除，后续维护沿用活动内容保护规则。基础数据增长不自动修改用户额度，客户端提示重新分配。两接口均受管理权限约束，PUT 同时遵循写操作令牌鉴权。[界面分配与清理流程](../user-guide/settings.md#调整应用数据与缓存)。

### 队列与批次

自动生成时段、暂停状态及全服务预览排队总量由 `GET /api/stream/previews/auto` 返回，`queued` 包括当前正在处理的影片。立即批量生成、手动重试和闲时生成共用持久影片队列与单个执行器，未完成项在服务重启后恢复。`GET /api/stream/previews/queue?library_id=<id>&offset=0&limit=50` 分页返回未开始项 `items`，另以 `running` 返回当前影片；`total` 只统计等待项，不包含当前影片。省略 `library_id` 查询全服务，最多每页 100 项。同一队列快照的分页共享 `revision`，完成项退出队列。

队列项与当前项的 `source` 区分 `auto`（闲时生成）、`retry`（手动重试）和 `batch`（立即生成），保留原有当前项的 `auto` 布尔字段；批次成员另含 `batch_id`。响应中的 `batches:[{batch_id,pending,running,remaining}]` 按当前库的全部队列项汇总，不受分页影响；`remaining` 为本批等待与处理中数量之和，批次无剩余项后不再返回。客户端用队列数量、当前片的 `frames/frame_total` 与批次剩余量表示进度，不能用单片 job 的 `done/total` 表示整队进度。立即请求不抢占当前任务；调度可跳过暂不符合运行条件的闲时项，但不改变这些项的显示顺序或闲时规则。

`POST /api/stream/previews/jobs {"library_id":<id>}` 将当前库尚未完成新版预览的可播放项作为一个立即批次持久入队，返回 `{state:"queued",batch_id,total,added,promoted,ready,already_queued,unavailable}`。`added` 为新增影片数，`promoted` 为从闲时任务提升为本批立即任务的数量；已运行、已单独重试或属于其他立即批次的影片计入 `already_queued`，保留原归属。已有新版成品计入 `ready`，源文件不可用计入 `unavailable`；没有新增或提升项时 `batch_id` 为 `null`。立即批次和手动重试不受自动生成开关与闲时时段限制，仍遵守播放转码让行规则；普通闲时项继续遵守原有时段、播放与预算条件。

### 已生成与失败清单

`GET /api/stream/previews/completed?library_id=<id>&offset=0&limit=50` 按当前视频库分页列出可用预览的电影、分集和花絮；省略 `library_id` 查询全服务，`limit` 为 1–100。返回 `{items,total,offset,limit}`，每项包含 `kind/id/library_id/title/file_name/frames/interval/quality_revision/needs_update`；`frames` 为实际可用帧数，兼容的旧画质成品以 `needs_update:true` 标记。每部影片最多一项，按视频库、电影/分集/花絮、ID 顺序排列。仅计入与当前片源、采样配置匹配并通过完整性校验的普通成品或手动接纳的片尾缺失成品；后者另含 `completion/available_count/coverage_end/duration/warning/key/confirmed_at`。离线、过期和未经确认的部分产物不计入；抽帧达到 100% 不等于完整成品。

完成清单复用持久目录索引，按当前库批量读取候选资料并核对源文件属性；已验证且目录、清单签名未变的成品复用结果，返回页内项目仍检查图片页。`total` 不是每次对全部拼图的物理审计：外部原地破坏未展示项目的图片时，可在该页校验后纠正计数。旧缓存首次使用时需发现目录、读取清单并校验本库匹配成品，索引不可用时回退直接读取缓存；[索引更新与校验机制](playback.md#倍速缩略图与预缓存)见播放设计。接口可维护可重建索引，不修改媒体或预览清单，不探测原片、启动 FFmpeg 或更新缓存访问时间，使用与队列接口相同的管理权限；网页展开已生成清单时按需调用，刷新及分页独立于队列轮询。

`GET /api/stream/previews/failed?library_id=<id>&offset=0&limit=50` 返回 `{items,total,offset,limit}`，参数范围与已生成接口一致；每项包含 `kind/id/library_id/title/file_name/failed_at/error_code/error`，`failed_at` 为 Unix 秒，按失败时间倒序排列。每个视频仅保留最近一次未解决失败，名称和库归属使用当前媒体记录，已删除媒体不显示。查询为片尾确认候选读取片源文件属性及缓存清单，不读取媒体内容、解析读取 URL 或启动探测与生成；符合条件的条目另含 `tail_acceptance:{key,failed_at,available_count,coverage_end,duration,missing_seconds}`。失败记录独立持久化，生成成功或手动接纳片尾缺失后清除，取消或重新排队时保留；已有旧版成品但升级失败时可同时出现在两份清单。读取失败返回可读错误，不将损坏记录作为空清单。接口受管理权限保护。

### 手动接纳片尾缺失

`POST /api/stream/previews/failed/{kind}/{id}/accept-tail` 提交 `{library_id,key,failed_at,available_count,confirmed:true}`，其中快照字段来自失败条目的 `tail_acceptance`。仅允许当前片源最近的解码失败，已有连续完整页覆盖至少 95% 时长且未覆盖尾段不超过 300 秒，并且没有该项的排队或运行任务。服务端再次检查片源缓存键、清单、图片页和失败记录；条件或快照变化返回 409，库归属错误返回 404。成功返回 `state:ready`，保留原页图片和原 `duration/count`，不启动抽帧；普通超时、网络与环境错误不能通过此接口接纳。

带提示清单使用 `completion:"known_tail_missing"`、`available_count`、排他的 `coverage_end`，以及 `warning:{code:"confirmed_tail_damage",message:...}`。`available_count` 是已提交的完整页格数，`coverage_end` 是实际可显示预览的截止秒数，不代表精确的片源损坏位置。客户端在此边界之外返回无图；不能缩小原 `count` 后把末图重复到片尾。完成清单、自动队列和批量生成将其视为已完成，警示与普通失败 `error` 分开。

`DELETE /api/stream/previews/completed/{kind}/{id}/tail-acceptance` 提交 `{library_id,key,confirmed_at}`，快照字段来自已生成条目。成功返回 `state:partial`，撤销确认并恢复原解码失败，已有图片页保留；不自动排队，需要继续时调用失败重试接口。普通完整成品不能撤销片尾确认。两项写接口遵循管理权限和写操作令牌鉴权，确认只对当前片源及采样配置生效。

### 重试与取消

`POST /api/stream/previews/failed/{kind}/{id}/retry {"library_id":<id>}` 将失败视频作为手动请求加入处理列表末尾，返回 `{"state":"queued"|"already_queued"|"running"}`。仅在闲时队列中的同项会改为手动重试并移到队尾；已在立即任务或重试队列、或正在运行时不重复加入。手动重试不受自动生成开关和时段限制，沿用立即批量生成的播放转码让行规则。排队请求持久化，重启后恢复；可用现有单项移出接口取消并移出当前重试，仅影响这一项，失败原因保留。本接口遵循管理权限及写操作令牌鉴权。

`POST /api/stream/previews/queue {"library_id":<id>}` 将已有本地库中未完成预览的可播放项加入闲时队列，返回新增、已有成品、已排队、源文件不可用的数量。`DELETE /api/stream/previews/queue/{kind}/{version_id}?library_id=<id>` 只移出指定影片，无论来源是闲时、重试还是立即批次；目标正在处理时同时取消该片，其余影片继续。批量移出使用 `POST /api/stream/previews/queue/remove`，请求包含 `library_id`、从列表获得的 `revision`，以及 `items:[{"kind":"movie","id":1}]`；全选跨分页时改传 `all_pending:true`，可用 `exclude` 排除个别 `{kind,id}`。服务端先保存持久队列，再提交移出结果，始终排除正在处理项；清空后仅当前影片继续完成。队列版本变化返回 409，需刷新后重新选择。移出保留已有预览页及上次失败原因。

`POST /api/stream/previews/batches/{batch_id}/cancel {"library_id":<id>}` 取消指定库中该批剩余影片，包括属于本批的当前影片，返回 `{removed,cancelled}`；`removed` 为移出数量，`cancelled` 表示是否同时取消当前影片。其他批次、单独重试和闲时任务不受影响；批次已经结束或移出时返回 `{removed:0,cancelled:false}`。原有 `POST /api/stream/previews/jobs/{job_id}/cancel` 保留单片运行任务的取消兼容，不能用于取消持久批次；播放器内发起单片生成的接口保持不变。上述写接口遵循管理权限及写操作令牌鉴权。

### 清理与单片状态

`POST /api/stream/previews/cache/clean {"dry_run":true|false}` 先预览再清理全部进度缩略图，不删除持久队列，生成任务运行时返回 409。原有 `POST /api/stream/cache/clean` 只清理可回收播放缓存。扫描摘要的 `preview_auto.queued` 表示新排队项数。

单片 `GET /api/stream/{version_id}/previews` 返回该片预览清单；运行中的任务另含 `frames`、`frame_total`（当前片已抽帧数与总数），尚未轮到该片时返回 `queued`。客户端以清单 `state=ready` 判断是否完成，不能只凭已有图片页数判断。

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

TMDB 上游 HTTP 失败保留状态码与简短原因，不在接口错误或异常文本中回显含 API Key 的请求地址；图片下载失败日志也不记录原始 URL。普通资料请求对 429 和可重试的 5xx 仍最多重试两次；后台播出检查保持单次请求，由持久队列安排重试。

### 儿童筛选资料补全

`POST /api/viewing/refresh` 接收可选的 `library_id`：正整数指定单个视频库，`null`、省略字段或省略请求体表示全部媒体库；不存在的视频库返回 404。网页在“访问保护”中独立选择范围，不复用扫描或顶栏选库。任务仅处理范围内已有 TMDB 匹配、但儿童筛选资料不足的电影和剧集，不扫描或修改媒体文件。

范围在启动时固定，并随任务状态的 `library_id` 返回。单库任务的本地资料写入、电影搜索索引同步和剧集分集重算均限于所选视频库；其他库中的同一 TMDB 作品不随本次任务更新。TMDB 资料缓存仍跨库共享，同库多版本可共用补全结果，远程请求按作品去重。

使用 `GET /api/viewing/refresh/{job_id}` 查询进度，`POST /api/viewing/refresh/{job_id}/cancel` 取消；取消后应等待 `worker_finished:true` 再启动新任务。同范围的运行中任务返回原任务及 `resumed:true`，其他未结束任务冲突返回 409。任务不存在或服务重启后返回 404，客户端应清理旧任务引用；其他状态读取失败可重试。接口受管理权限保护，POST 同时遵循写操作令牌鉴权。

### 系列信息补全

`POST /api/collections/suggest/backfill` 接收 `media_library`（兼容 `media_library_id`）、`limit`（1–200，默认 50）和 `force`。媒体库范围在启动时固定，仅选取该范围内缺少系列信息的电影；省略范围表示全服务。旧字段 `library` / `library_id` 按视频库映射到父媒体库，未知 ID 对应空范围。不会为仅存在于其他库的作品发起补全；同一 TMDB 作品仍共用资料缓存，资料变化时按既有规则同步其各库版本的文字资料，保留手工标题与标签，不写媒体文件或 NFO。

启动响应和 `GET /api/collections/suggest/backfill/status?job_id=<id>` 状态包含 `media_library_id`（全服务为 `null`）、`force` 和 `limit`。仅当运行中任务的范围、强制参数及批量上限全部一致时复用任务并返回 `resumed:true`；存在其他参数的运行中任务时返回 409。网页切换媒体库或离开页面后停止旧任务的前端自动续跑，已经提交的后台批次继续处理原范围；用户也可通过取消按钮停止剩余处理。

状态查询及 `POST /api/collections/suggest/backfill/cancel {"job_id":"<id>"}` 对明确指定但已失效的任务返回 404，不改查或取消另一任务；兼容入口省略任务 ID 时仍处理最近任务。网页收到任务已失效的响应后停止跟踪，可重新发起补全。

### 合集成员清理

`POST /api/jobs/clean-collections` 同步清理历史遗留的悬挂合集成员，不创建后台 job。省略 body 或 `dry_run` 时仅预览；`media_library_id` 可限定待处理合集，省略则检查全部合集。此接口不使用 `library_id` 限定视频库，也不按磁盘是否在线判定成员失效；成员存在性规则见[数据模型](data.md#主要实体)。

先传 `{"dry_run":true,"media_library_id":2}` 核对结果，再以同一作用域传 `dry_run:false` 执行。两种响应都含 `dry_run`、待清理数 `total` 和清理后将为空的 `empty_ids`；预览另含最多 20 条 `sample`，执行另含实际移除数 `removed`。预览不锁定快照，执行会重新检查当前记录。仅删除合集成员行，保留空合集和物理文件；`empty_ids` 不包含原本就没有成员的空合集。实例设置了令牌时须按下方[认证规则](#认证和安全边界)发送凭据。

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

`POST /api/jobs/tmdb-refresh` 同为后台任务（立即返回 `{job_id, total}`，同上轮询/取消；进度含 `changed` 与 `failed[{tmdb_id,title,error}]`，失败项带原因且不中断整批）：

```http
POST /api/jobs/tmdb-refresh {"library_id": 2}
→ {"job_id": "…", "resumed": false, "total": 308}

GET /api/jobs/tmdb-refresh/{job_id}
→ {"state": "running", "done": 12, "total": 308, "changed": 3, "failed": [], "current": "星际穿越"}

POST /api/jobs/tmdb-refresh/{job_id}/cancel   # 协作式取消，已完成项保留
```

归属预览/执行（token 15 分钟有效；磁盘或规则变化后确认返回 409，需重新预览）：

```http
POST /api/tv/bindings/preview {"library_id": 3, "tmdb_id": 12345, "target_show_id": 72, "directories": [{"path": "Season 01"}]}
→ {"groups": [...], "conflicts": [...], "can_apply": true, "token": "…"}

POST /api/tv/bindings/apply {"token": "…"}   # 用户确认上述预览后执行
```

`/apply` 只接收预览 token，没有 `dry_run` 模式；需要重新核对时调用 `/preview`。撤销的 `/undo` 才通过 `dry_run:true|false` 区分预览与执行。

`kind=movie|episode|extra` 隔离探测、断点和会话目录；同一整数 ID 不能跨 kind 复用。`kind=episode` 的版本接口只返回同剧同季同集的多版本。`library_id` 指视频库，`media_library_id` 指媒体库，写任务优先显式传视频库 id。

### 原生客户端握手与会话

`GET /api/stream/client-info` 返回 `{protocol_version:1, features:["android_tv","independent_sessions","tv_search","tv_actor_search","viewing_modes"], auth_required}`，另含局域网发现使用的 `name`、`version` 与持久实例身份 `server_id`。GET 不验证写令牌；`POST /api/stream/client-check` 返回相同信息并通过现有写操作鉴权校验令牌，不改变用户配置或媒体。首次握手可能生成并保存 `server_id`。功能通过 `features` 单独协商，新增检索与发现字段没有提高播放协议号；电视扫描范围和确认流程见仓库 `android-tv/docs/protocol.md`。

TV 客户端对 `versions/decide/sessions/prewarm` 显式传 `client:"android_tv"` 及原生 `caps`，旧客户端省略 `client` 仍按 web 处理。具体容器、HDR、音轨和逐片格式能力字段见仓库 `android-tv/docs/protocol.md`。`GET /api/tv-client/search` 支持 `q/kind/media_library/limit/offset`；演员 `/actors` 与 `/actor-works` 使用同一媒体库范围，作品查询中的 `actor` 为服务器返回的不透明 key。

每次 `POST /api/stream/{id}/sessions` 返回独立 `session_id`，相同输出共享转码任务和成品；DELETE 只释放该 sid。创建会话请求可带成对的 `client_id`（16–64 位字母、数字、`_`、`-`）与递增整数 `request_seq`：同一播放器的新请求撤销旧请求，过期请求返回 409。新产物占满 `MAX_TRANSCODES` 时返回 429，不能关闭其他设备来腾出名额。时间轴、复用及回收约定见[播放设计](playback.md#会话时间轴与清理)。

P5 专用计划通过 `plan.color_pipeline="dovi_p5_sdr"`、`color_revision`、`decoder`、`tone_mapper` 和 `encoder_backend` 标识实际转换路径；`height` 仍表示实际输出高度。会话创建可额外传 `auto_max_height:720`，仅限制 P5 自动画质的输出，其他画质和视频不受影响。客户端应在同一播放器内保留这一降档上限，避免自动升回 1080p。

`GET /api/stream/backends` 的 `dovi_p5.enabled` 表示网页试验开关（默认关闭），`experimental:true` 标记试验状态，`available` 仅表示硬件能力。关闭时网页计划返回 `blocked/dovi_p5_web_disabled`，电视原视频不受影响。`dovi_p5` 分别给出 HEVC Main10 解码、H.264 编码、Intel Vulkan 和组合链的检查结果；`validated_real_p5:false` 表示合成能力检查不能代替实际片源验收。P5 会话心跳和诊断增加 `processing_speed`、实际解码／转色／编码后端，以及 `failure_code`、中文 `failure_reason` 和可选的 `recommended_height:720`。自动 1080p 因性能失败时可降档一次；`dovi_metadata_invalid`、`dovi_pipeline_unavailable` 或 720p 再次失败应停止，不进入普通软件转码或封装兼容重试。

`POST /api/stream/sessions/{sid}/ping` 认领并保活当前 sid，可省略请求体，兼容已有客户端；新客户端可传可选字段 `position`、`paused` 和 `playback_rate`。`position` 为原片绝对秒数，不是 HLS 片内时间，需为非负有限值；`paused` 为布尔值，`playback_rate` 范围为 0.25～4。服务端结合视频分片需求控制提前生成窗口，不修改持久观看进度；仅发送旧式空心跳时，仍可按实际视频分片请求推进需求。窗口与预缓存共享规则见[按需生成窗口](playback.md#按需生成窗口)。

心跳响应和会话调试提供 `buffer_paused`、`ahead_seconds`、`lookahead_seconds`、`window_supported` 和 `flow_reason`。`running` 表示 FFmpeg 尚未退出，包含缓冲足够而暂停的情况；不能把窗口暂停当作生成失败或整片完成。心跳另用 `complete`、`failed` 区分完整成品与中断，`ahead_seconds` 在尚无可用列表或整片预缓存时可为 `null`。

`GET /api/tv/recent-played` 的条目 `id/show_id` 指剧，实际可播放分集在 `progress.version_id`，不能把 `id` 直接传给分集播放。电影、分集完成判定共同使用至少 95%，或至少 80% 且剩余不超过 300 秒的条件。

电影/剧集列表、继续观看、相关推荐、人物作品、合集及其成员，以及 TV 客户端搜索/演员作品的卡片条目提供 `media_library_id` 和 `media_library_name`；名称属于父媒体库，区别于视频库的 `library_id/library_name`。电影和剧集条目提供可空的 `tmdb_rating`；剧集继续观看使用与剧名/海报对应的整剧评分，合集不合成评分。字段为向后兼容追加，不改变现有筛选或去重范围。

## 详情图片

`GET /api/movies/{id}/backdrop` 优先返回本地横版背景缓存；缺图时只按已有 TMDB 缓存中的图片路径下载，不重新刮削影片。缺少背景为 404，下载失败为 502，前端保留渐变底色；图片请求独立于电影详情 JSON，避免拖慢资料加载。季和单集详情的 `show_backdrop_path` 沿用所属剧集的本地背景，空字符串表示暂无图片。

## DLNA 投屏

| 请求 | 行为 |
| --- | --- |
| `POST /api/cast/discover` | 搜索服务器局域网的 MediaRenderer；返回 `items`、建议的 `base_url` 和 `scope=server_lan` |
| `GET /api/cast/devices` | 仅读设备缓存，不触发搜索 |
| `POST /api/cast/devices` | 接收 `{location}`，从指定局域网 HTTP 描述地址验证 DLNA MediaRenderer 并加入设备缓存；返回 `{item,base_url}` |
| `POST /api/cast/sessions` | 接收 `{device_id,version_id,kind,position,base_url?,receiver_type?,audio?,sub?}`；`receiver_type=dlna`（默认）或 `jzmedia_tv`，`kind=movie\|episode`，`position` 为源视频绝对秒数，`audio` 为源音轨零基索引，`sub=-1` 关闭字幕、非负为服务器字幕索引 |
| `GET /api/cast/current?version_id=…&kind=…` | 返回该版本当前有效投屏的 `{session}`；无投屏为 `null` |
| `GET /api/cast/sessions/{sid}` | 返回内存中的最近电视状态，不发起网络轮询 |
| `POST /api/cast/sessions/{sid}/control` | `{command,value?}`；命令为 `play/pause/stop/seek/volume`，`seek` 单位秒，音量范围 0–100 |
| `GET /api/cast/media/{sid}`、`HEAD` | DLNA 会话限定的原文件或已完成单音轨 MP4 读取；GET 复用 Range，响应 MIME 与 DLNA 头一致 |

搜索、手动注册与控制遵循现有写请求令牌；电视取流不要求全局令牌，URL 仅含随机会话 ID。手动注册要求完整的私有 IPv4 HTTP 描述地址，服务端不使用 DNS、代理或重定向，并核对 MediaRenderer 与 AVTransport；设备保存在当前进程内，重启后需重新添加。设备列表超过 120 秒需重新搜索或注册，搜索请求在 5 秒内复用结果，重新搜索时会保留本次运行中手动注册的设备。媒体 ID 由现有播放行与存储层解析，不接受任意文件路径或外部媒体 URL；不存在的版本返回 404，文件缺失 410，离线 503。投屏网络和固定地址见[部署指南](../getting-started/deployment.md#dlna-投屏网络)。

会话包含 `receiver_type` 与不透明 `id`，客户端不可把 App ID 作为 DLNA 描述地址。`state=PREPARING` 表示 NAS 正在准备完整单音轨文件，`prepare_progress` 为 0–1 或 `null`，`prepare_message` 说明阶段；只允许 `stop` 取消，不对电视发送播放控制。`TRANSITIONING` 表示请求已发出、状态仍待电视确认；`PLAYING` 是电视回报的状态。创建、查询和控制返回的会话包含 `resume_pending`（布尔）及 `resume_target`（源视频绝对秒数或 `null`）：起点尚待电视完成续播时为待恢复状态，目标不是实报位置。客户端应在发起瞬间冻结绝对起点，不发送 HLS 分片内的相对时间。

服务端独立轮询，确认自有 `TrackURI` 与电视就绪后限次请求起始 Seek，并以电视有效位置确认完成；待恢复时 `position_known=false`，临时零秒不覆盖断点。恢复完成、取消或限次失败后 `resume_pending=false`、`resume_target=null`，失败原因保留在 `warning`，之后仅按有效电视实报记录。手动 `seek/stop` 取消自动续播目标。

只有回报的 `TrackURI` 与当前 DLNA 投屏一致、位置有效时才记录进度，`position_known=false` 不可推算当前位置；`last_position` 保留最近有效断点，供电视停播后切回手机使用。电视切换到其他媒体后标为 `REPLACED`，不覆盖进度或继续控制新媒体。设备命令失败返回 502，格式未获接收端声明支持返回 422。DLNA 会话为内存状态，最多保留 24 小时；服务重启需重新投屏。

DLNA 显式选择多音轨中的音轨时，异步准备视频 copy、所选声音转 AAC 双声道的完整 faststart MP4；旧请求省略 `audio` 仍使用原文件。不能安全重封装的 HDR／DV 等返回 422，推荐电视 App。`sub>=0` 的 DLNA 请求返回 422 并推荐 App，不增加字幕烧录。App 接收则传递源音轨与服务器字幕索引，`base_url` 不参与 App 播放。

### 前台电视 App 接收

握手 `features` 中的 `tv_app_handoff` 为协议 1 的可选能力。旧服务端返回 404 时，APK 关闭接收功能并保留手动播放。

| 请求 | 行为 |
| --- | --- |
| `GET /api/cast/apps` | 返回 `{items,protocol_version:1}`；仅列在线 App 的公开名称、地址、不透明 `id` 和 `receiver_type=jzmedia_tv`，不含接收密钥 |
| `POST /api/cast/apps/register` | `{device_id,name,protocol_version:1}`；安装标识为稳定 UUID，返回 `receiver_id`、临时 `receiver_token`、`heartbeat_seconds=3`、`ttl_seconds=15` |
| `POST /api/cast/apps/{receiver_id}/poll` | `{foreground,session_id?,state,position?,duration?,error?}`；返回 `{command:null\|{id,session_id,action,payload,valid_for_seconds}}` |
| `POST /api/cast/apps/{receiver_id}/ack` | `{command_id,accepted,error?}`；接收器完成本地动作后确认，重复同一结果幂等，取消／替换／过期的迟到确认返回 409 |
| `DELETE /api/cast/apps/{receiver_id}` | 退出前台／连接时注销，不再接收命令 |

注册遵循全局访问令牌和观看会话认证；轮询、确认、注销还需 `X-Receiver-Token`。密钥只保留在内存和请求头。注册绑定电视当前观看会话，手机和电视权限都允许时才能发送媒体，不向电视委托手机权限。重新注册同一安装标识会轮换密钥并废弃旧命令。

公开设备的 `name` 与会话 `device.name` 使用同一显示名，`app_name` 保留 App 原注册名。服务端仅在实际连接来源为局域网 IP、该 IP 只有一个在线 App 且已有发现缓存只有一个 DLNA 设备 ID 时，采用其友好名；不读取转发头、不为命名发起搜索。已确认名称在本次注册期间保留，发现缓存过期不会使它退回型号；重新注册重新核对，出现同 IP 歧义时撤销关联。未关联时回退到 App 注册名，原有不透明设备 ID 和控制接口不变，兼容旧 APK。

初次 `play` 的 payload 为 `{version_id,kind,title,position,audio,sub}`；恢复播放的 `play`、`pause`、`stop` 为 `{}`，`seek` 为 `{position}`。命令 60 秒过期并通过 ID 去重；`valid_for_seconds` 是本次响应时剩余有效时间，APK 用单调时钟与前台代际再次核对。App 会话为 `WAITING_FOR_APP → LOADING →` 真实状态，仅向已在线且明确选中的 App 发出，不提供离线排队或后台唤醒。

App 控制仍用统一 sessions/status/control，操作最多等待 8 秒 ACK；未确认返回 504，并不假设电视已停。App 系统音量由电视遥控器操作，`volume` 返回 422。App 的实际进度接口负责保存断点，轮询只报告状态。图片字幕在此入口仅尝试精确映射的原生直放，不能支持时提示并关闭；不调用自动烧录。完整客户端生命周期见仓库 `android-tv/docs/protocol.md` 的“手机请求电视 App 播放”。

## 上传与浏览体验接口

### 上传与入库结果

`POST /api/uploads` 保持电影返回字段兼容；查询参数新增 `media_type=movie|tv`、`mode=files|dir`。类型必须与目标视频库一致，停用和只读库返回 409。TV 散文件须传 `show_id` 或 `show_title`，以及 `season`；已有剧必须属于目标库。

返回 `media_type/library_id/show_id/episode_id/status`，无法识别的文件返回 `skipped_tv_unknown`，写入成功但登记异常为 `stored_scan_warn`，不要当作已成功入库。

TV 资料补全另用 `tv-scrape {library_id, ids, force:true}`，整理仍调用预览/确认接口。远程上传使用 `open_write(overwrite=False)` 原子提交，目标并发出现同样返回 409。

### 扫描状态复用

`GET /api/jobs/scan` 返回正在运行或最近的扫描任务，墙上与设置工具共用状态。扫描和 TV 资料任务仅复用相同作用域/选项；不同范围返回 409，不能把另一库的任务当成本次完成。

### 相邻分集与版本

`GET /api/tv/episodes/{id}` 新增 `previous_episode/next_episode`，与 `/next` 使用相同版本与多集区间规则。季详情支持 `version` 筛选和分页，新增 `versions/distinct_count`；`episode_count/watched_count` 仍为全季文件数。

### 概览待办

`/api/tv/stats` 保留 `shows/episodes`，增加 `seasons/pending/episode_review/by_library`；电影 `/api/jobs/stats` 增加 `by_library` 待办统计。概览待办深链指定具体 `library`。

### 资料重建

`rebuild-meta` 新增 `nfo` 开关（默认 true），与 `artwork` 独立，至少选择一项；旧客户端行为不变。单片与全库修复仍分别使用 `ids` 和 `library_id`。

## 剧集播出与收藏对照

网页端使用以下增量接口，现有 `/shows/{id}`、季/集详情、播放、版本筛选和观看进度接口保持原有本地文件语义。收藏对照按当前剧所属媒体库内启用的 TV 视频库合并同一已确认 TMDB 作品，不能跨媒体库借用收藏；官方目录项不代表可播放文件。

### 收藏与官方目录

`GET /api/tv/shows/{show_id}/collection`

仅读本地 DB；返回 `status/status_text`、`checked_at/next_check_at/error`、`latest_episode`、`seasons`、`missing_seasons` 与 `confirmed/media_library_id`。不请求 TMDB、不核验 NAS 文件。

`GET /api/tv/shows/{show_id}/seasons/{season}/catalog`

按需读取官方季目录，命中有效缓存时不联网；缺失/过期时受租约与冷却约束获取。返回季信息及 `items`、`checked_at/stale/refreshing/error`。

`GET /api/tv/shows/{show_id}/seasons/{season}/poster?tmdb_id=<id>`

只按已缓存官方季海报路径按需下载到应用图片缓存；显式 TMDB ID 必须仍与本剧匹配。已有文件直接返回，缺图/身份不符为 404，下载失败为 503 并有短期冷却。

### 推荐与检查状态

`GET /api/tv/updates?media_library=<id>`

必须指定正整数媒体库 ID；仅读本地播出事件及收藏记录，返回 `{items,checked_at,error}`。每部剧一条，含本地详情入口与尚未收藏的 `events`；未知媒体库返回空集合。

`GET /api/tv/airing/status`

仅读状态，返回 `configured,total,checked,pending,failed,catalog_pending,catalog_failed,last_checked_at,next_check_at,running,current,error,retry_at`。剧级计数按启用库中已确认 TMDB 剧去重。

`POST /api/tv/airing/check`

`{}` 检查全部合格剧，或 `{show_id}` 检查指定本地剧对应的 TMDB 作品；沿用写操作鉴权，返回状态快照及 `status:"queued"`，不等待联网完成。手动操作不绕过失败冷却。

### 收藏数量与状态

季卡的 `official_count` 是来源集数，`collected_count` 是可确认的去重收藏数，`local_count` 仅指当前本地剧的有效编号数。剧快照与季目录独立缓存，`collected_count` 超过旧快照的 `official_count` 时客户端保留收藏数量并显示“总数待更新”，不生成大于 1 的比值。

`collection_state` 为 `collected|uncollected|uncertain`；客户端遇 `uncertain` 不应把未能对照的条目算成缺集。`airing_state` 独立为 `aired|upcoming|unknown`，日期按 UTC 日比较。`missing_seasons` 只含已播、完全未收藏的正季，不含特别篇和未来季。

### 无法确认收藏的原因

`collection_reason` 区分资料缺失、编号不明和需要人工处理的情况：

| 值 | 含义 |
| --- | --- |
| `''` | 没有待说明原因 |
| `catalog_missing` | 官方目录信息不足 |
| `numbering_unresolved` | 本地季或已知官方目标季的编号暂不能可靠对照 |
| `coverage_incomplete` | 跨季覆盖范围不明，不能断言其他季缺集 |
| `match_review` | 分集实际存在 `needs_review/binding_conflict` |
| `show_unconfirmed` | 整剧尚无 TMDB ID，或剧级匹配待确认 |

`catalog_missing` 不否定已有 TMDB 匹配，也不表示后台进度；客户端不显示人工待核对徽标。`coverage_incomplete` 同样使用中性说明，不显示人工核对徽标。`match_review` 只影响实际需要核对的季或分集；`show_unconfirmed` 不使用尚未确认作品的官方总数。

数量展示遵循以下规则：

- 官方总数已知，且收藏按官方坐标确认：显示“已确认收藏 N/M 集”；本地数量更大时另列“本地 X 集”。
- 只有 `local_count`：显示“已收藏 X 集 · 官方 M 集”，不能将本地数量直接用作官方分母的分子。
- 总数未知或非官方季：省略分母。
- 收藏状态已确定：显示“已收藏 N/M 集”。

状态仍保守保留 `uncertain`，不能因改善文案而放开缺集推荐；已经可靠确认的单集可独立返回 `collected` 和空原因。此字段不触发额外周期请求。

### 检查覆盖率与本地来源

状态中的 `checked` 表示至少成功取得过一次剧级快照的数量，不等同于全部资料当前均有效；`pending/failed` 包含有待处理或失败季目录的剧，`catalog_pending/catalog_failed` 另列季目录数量。`last_checked_at` 是最近一次剧级成功时间。墙响应的 `checked_at` 同样不能代替全库覆盖率，完整检查状态应读取 `/airing/status`。

分集目录与最近播出条目的 `sources` 指向真实本地来源，含 `show_id/library_id/library_name/season/episode/episode_id`；跨季手工匹配时必须使用这里的本地编号导航或播放，不把官方条目 ID 当作本地集 ID。季卡来源按实际本地剧与季聚合。多版本只计一个官方集，跨季多集或绝对编号无法验证时保守返回待核对。

### 缓存周期与事件修正

服务启动后先补齐启用库的播出基线，再按独立时钟维护：非结束剧每 7 天，已完结/取消剧每 90 天；季目录通常缓存 7 天，结束剧目录缓存 30 天。无有效凭据时不联网。首次基线只产生近 7 天的播出候选，后续保留最近 30 天；推荐资格按真实播出日期和当前收藏实时计算，不以发现时间冒充新播出。网页每周展开和事件去重属于当前浏览器偏好，不能用来调整服务端检查时间。

后续来源修正会同步事件的季集号、标题和播出日期，保留原 `event_id` 与首次发现时间。改为未来/无效日期的事件或在完整季目录中被撤回的分集会停用，不再进入推荐；再次恢复有效时沿用身份，避免重新当作新事件。

### 失败响应与前端保留

上游失败返回缓存及业务 `error` 码，包括 `not_configured/invalid_credentials/rate_limited/timeout/unavailable/invalid_response/not_found`；失败不覆盖上次成功时间。上游认证失败不能转换为应用 401，只有应用写令牌未通过时才返回 401。剧不存在为 404，尚未确认 TMDB 匹配的目录/手动检查为 422，目录获取期间换绑为 409。

客户端需保留旧内容、显示资料时间，取消或作废切剧/切库后的晚响应。维护面板应同时识别 POST 的排队状态和状态接口的 `running`，只在面板可见时轮询进度。

## 剧集目录归属

归属规则、历史记录及分集字段见[数据模型](data.md#主要实体)。扫描按最长路径前缀应用规则，目录整理/恢复同步改写规则路径；已确认目录的资料刷新只按精确季集匹配。

| 接口（前缀 `/api/tv/bindings`） | 用途 |
|---|---|
| `GET /directories?library_id=&show_id=` | 仅从 SQLite 返回已扫描目录与绑定，首屏不访问媒体存储；`show_id` 可选 |
| `POST /directories/refresh` | `{library_id, show_id?}`；启动实体目录盘点，发现尚未扫描的目录 |
| `POST /suggest`、`POST /suggest/start` | `{library_id, paths, tmdb_id?}`；最多 12 个目录，`/start` 为后台任务版本 |
| `POST /preview`、`POST /preview/start` | 预览归属变更；最多 50 个非嵌套目录，请求结构见下方 |
| `POST /apply`、`POST /apply/start` | `{token}`；执行服务器保存的预览，15 分钟有效，同 token 重试幂等 |
| `GET /jobs/{job_id}` | 查询盘点、建议、预览或执行任务；结果位于完成态的 `result` |
| `GET /history?library_id=` | 最近 30 条已执行/已撤销记录 |
| `POST /undo` | `{token, dry_run:true}` 预览；`dry_run:false` 撤销 |

预览请求结构（`?` 表示可选字段）：

```text
{
  library_id, tmdb_id, target_show_id?,
  directories: [{path, season?, override_season?}],
  replace_manual?, allow_duplicates?
}
```

预览返回 `groups/conflicts/warnings/duplicates/can_apply`；只有无冲突才返回 `token`。确认重新校验磁盘指纹、源目录快照、目标剧与分集签名，在 SQLite 事务内变更归属和季号，沿用现有 episode ID。观看状态不参与过期校验、不随撤销回滚。

规则/路径/新增分集变化会拒绝旧预览或撤销（409）；存储不可读返回 503，资料请求 HTTP 错误返回 502。接口不移动或写入媒体文件，可用于只读媒体库。规则绑定的剧在普通重新匹配入口禁止直接换成其他条目，改用归属弹窗。

## 新手配置

### 读取进度

`GET /api/onboarding` 仅查询本地数据，返回 `version/status/step/library_id/kind/import_mode/tmdb_skipped`、最近一次 `upload_result` 和派生的 `target_valid/library_verified/tmdb_verified/upload_allowed/max_step/can_complete/show_welcome/content`。`content` 含全局记录数、目标库入库数、待处理数和最多 5 条详情入口数据；读取不触发网络诊断、媒体盘点或任务。

### 更新进度与完成条件

`PATCH /api/onboarding` 部分更新步骤（1–4）、目标、导入方式及 `status=active|deferred|completed`。目标可以清空。初始状态为 `not_started`；进度按实例保存至 `app_settings.onboarding_state`，无需新增表或迁移。客户端报告的上传数量仅供展示，不作为入库完成证据；完成条件不足返回 409，非法字段返回 422。

### 验证 TMDB 凭据

`POST /api/tmdb/check` 不接收临时凭据，使用已保存的有效配置和代理直接访问 TMDB `/3/authentication`。单请求短超时、不重试，不查缓存或备用来源。诊断成功或上游错误均返回 200 和 `{ok, code, message, elapsed_ms}`；上游 401 映射为 `invalid_credentials`，不冒充应用鉴权错误。应用自身未授权仍由中间件返回 401。

### 验证视频库

`POST /api/libraries/{id}/check` 沿用原响应，记录与当前库配置关联的检查结果。配置变化后旧检查失效。只读不妨碍扫描和完成入库。

### 复用既有导入接口

向导沿用 `/api/settings`、媒体库／视频库接口、`/api/jobs/scan` 与 `/api/uploads`，所有操作显式指定目标视频库。`UploadDialog.libraryId` 可固定上传目标；`busy` 和 `result` 事件支持页面离开保护和结果展示，原 `done/close` 保持兼容。

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

### 服务商与配置

配置 `provider` 为 `deepseek`、`opencode_go` 或 `compatible`。地址是 API 根目录，客户端追加 `/chat/completions`，不跟随重定向；不继承 TMDB 代理或系统代理。

`opencode_go` 只接受 `https://opencode.ai/zen/go/v1`，UI 预填模型 `glm-5.3-flash`，直接传模型 ID、不加 `opencode-go/` 前缀；仅支持 Chat Completions，不切换到 Messages 或 Responses 协议。

自定义兼容服务允许空 Key，DeepSeek 与 OpenCode Go 要求 Key。API 部分更新不会因仅修改 `provider` 就自动重置地址或模型，应一并提交完整有效组合。

### 密钥与环境值

`api_key` 空串表示保留；`clear_api_key:true` 明确删除 DB 密钥并恢复环境值，不可与新 Key 同时提交。只有一套有效配置，切换服务商时由操作者填写对应 Key，不存在分服务商密钥槽位。配置错误返回 400，请求体不符合业务 schema 返回 422。

有效配置按 DB → 对应环境变量 → 默认值解析，环境变量为 `AI_ENABLED/AI_PROVIDER/AI_BASE_URL/AI_MODEL/AI_API_KEY/AI_TIMEOUT_SECONDS/AI_DAILY_LIMIT`。默认关闭、超时 12 秒、每日上限 100 次；超时可设 2–60 秒，每日上限为 1–10000 次。设置存在数据库中，脱敏回显不等于加密存储。

### OpenCode Go 的会话与用途

OpenCode Go 请求使用真实 jzmedia User-Agent 和每次业务操作的 `x-opencode-session`；一次匹配的标题提取与排序共享会话，不同操作独立。会话不含媒体路径或凭据，不进入结果缓存键。官方目前面向编程代理，本接口不伪装编程任务，也不绕过用途限制；影视用途及真实连通性未验证。参见[官方使用范围](https://opencode.ai/docs/go/#where-can-i-use-it)。

### 用量与限额

`usage` 含 UTC `date`、`requests/input_tokens/output_tokens`。每次实际尝试在网络请求前原子预占次数，失败也计次；本地输入校验失败、未启用、缺少必需 Key 或缓存命中不计次。Token 按上游返回的有效使用量累加，未报告时不推算费用。匹配最多调用两次模型；第二次失败时仍可返回来源已验证的候选，但注明尚未完成 AI 核对。

### 搜索范围与建议

搜索范围由请求上下文在服务器解析，显式不存在或无对应类型的视频库返回 `empty_scope`，不会扩为全库。允许的输出只有关键词、类型、产地、国家、年份/年代、标签、最低评分与来源、观看状态、排序和剧集连载状态；模型不能返回库 ID、影片 ID、SQL 或额外字段。

电影关键词搜索仍按相关度，TV 不支持豆瓣评分。当前接口不支持时长、排除类型、年龄适宜性或向量相似搜索，无法表达的要求应列为提示，不能伪造已实现的条件。

### 候选绑定约束

匹配候选的 `bindable` 由服务器根据 TMDB 标识、详情来源、可信外源缓存及既有目录归属确定；`false` 时前端不展示绑定按钮，显示 `bind_reason`。原因可能是仅有索引线索，也可能是剧集已有目录绑定、需要通过“归属与季号”调整；已有目录绑定仍允许确认当前同一 TMDB 作品。外源确认通过既有 `/bind-external` 接口按来源和 ID 恢复可信缓存或取得详情，缺失详情不能用当前旧标题冒充新绑定。业务校验拒绝的模型结果会移出 AI 缓存，重试可重新请求。

### 失败响应与人工确认

连接检查及业务请求遇模型不可用时返回 HTTP 200 与 `{ok:false, code, message}`，包括 `disabled/not_configured/invalid_credentials/rate_limited/timeout/daily_limit/invalid_result` 等；上游 401 不转换成应用令牌提示。条目不存在返回 404，应用自身鉴权失败仍为 401。前端保留普通搜索与手动匹配，只有用户确认建议后才调用原有查询或绑定接口。[架构与验收边界](metadata.md#智能辅助的边界)。
