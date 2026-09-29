# 模块划分

[开发者文档](README.md)

## 后端

| 目录或文件 | 主要职责 | 变更时的约束 |
|---|---|---|
| `app/main.py` | FastAPI 实例、lifespan、写接口鉴权、SPA 和静态资源 | 不在导入期写盘；单 worker 假设 |
| `app/config.py`、`app/log.py`、`app/db.py` | 配置优先级、统一日志、运行目录 | 区分宿主/容器路径；脱敏秘密 |
| `app/store/` | SQLite schema/迁移、电影/剧集/合集/进度/搜索/来源索引 | 手写 SQL 写电影等后同步 FTS；迁移幂等 |
| `app/library_paths.py`、`app/storage/`、`app/mounts.py` | 视频库路径、Local/SMB/NFS 后端、挂载、缓存和 Range 代理 | 跨库范围、只读、写缓存失效、离线不能当“文件不存在” |
| `app/tv_bindings.py`、`app/tv_binding_rules.py`、`app/store/tv_bindings.py` | 剧集目录归属规则、纯规则判定、归属表与历史表的读写 | 预览 token 有效期、事务内应用、规则路径随整理/恢复改写 |
| `app/scanner/` | 文件分类、电影和剧集扫描、匹配、NFO 同步与目录整理 | 分类/解析优先纯函数，整理先计划后执行 |
| `app/metadata/`、`app/tmdb.py` | 来源链、外源候选/详情、NFO 导入、TMDB 客户端 | 来源缺省与冷却状态；保守匹配门 |
| `app/nfo.py`、`app/artwork.py`、`app/posters.py` | NFO 格式、媒体目录图片、数据目录图片路径 | 遵守库的落盘策略与已有文件所有权 |
| `app/media.py`、`app/caps.py`、`app/playback/`、`app/transcode.py` | ffprobe、客户端能力、四档计划、FFmpeg 命令和后端选择 | 测试命令生成与真实转码分开；保持时间轴规则 |
| `app/routers/` | HTTP 参数/作用域/响应 | 不把 HTTP 结构泄漏到存储/纯计划逻辑 |
| `app/jobkit.py` | 后台任务进度、结果、协作取消 | 进程内状态，不能假定重启可续任务 ID |

`app/routers/movies/`、`app/routers/files/`、`app/routers/fs/`、`app/routers/stream/` 已拆成包；旧入口可能仍通过 `__init__.py` 门面导出。新增逻辑应写在相应子模块，不继续扩大门面。TV 主路由目前在 `app/routers/tv.py`。

## 前端

| 目录或文件 | 主要职责 |
|---|---|
| `frontend/src/router.js`、`views/` | 路由与页面（电影、详情、人物、合集、剧、季、集、设置） |
| `frontend/src/components/LibraryToolsPanel.vue` 等 | 视频库工具标签页、入库流程和维护面板 |
| `frontend/src/components/PlayerModal.vue`、`PlayerSettings.vue`、`PlayerSeekbar.vue`、`PlayerIcon.vue` | 播放生命周期、设置、进度交互和控件 |
| `frontend/src/useSubtitles.js`、`jassubLoader.js`、`pgsLoader.js` | 文本/ASS/PGS 字幕选择与渲染 |
| `frontend/src/caps.js`、`playbackControls.js`、`usePlaybackPreviews.js` | 能力探测、倍速/时间轴/拼图映射、缩略图任务状态 |
| `frontend/src/api.js`、`libraries.js` | API 请求/取消，媒体库与视频库客户端状态 |
| `frontend/src/styles/`、`frontend/src/player.css` | 全局设计变量和动态字幕层/共享播放按钮样式 |

前端页面路由级懒加载，`frontend/dist` 由构建生成并由 FastAPI 同源托管。HLS、ASS/PGS 资源按播放场景加载；修改播放器时既要验证编译和 lint，也要检查浏览器真实媒体元素生命周期、全屏和字幕定位。

## 常见改动去处

- 新的视频库类型：库 schema/迁移、StorageBackend 路径范围、扫描分支、HTTP 作用域、前端 Tab、测试和文档都要改。
- 新元数据来源：在 `metadata` 中实现候选与详情，加入受控来源链、冷却与来源字段，测试失败/空结果/离线情况。
- 新播放质量档：同步请求模型、决策、会话键、缓存复用、前端选项和测试；不能只改选择框。
- 新整理动作：先写纯计划和冲突分类，再写执行与审计、恢复和 UI 预览。

[开发与验证](development.md)
