# R01 基础架构/配置/健康/SPA 托管/部署 — status: done

## Scope
- `app/main.py`, `app/config.py`, `app/db.py`
- `app/routers/health.py`, `app/routers/fs.py`
- `Dockerfile`, `docker-compose.yml`, `docker-compose.override.yml`, `start.sh`, `.env.example`, `requirements.txt`, `frontend/vite.config.js`, `frontend/index.html`

## 目标（用户三问）
1. 功能设计是否合理？更好的方案？
2. 代码是否符合要求？废弃代码/漏洞（带 `file:line`）？
3. 质量差距（P0/P1/P2 分级，带证据）？

## 进度 Log（倒序追加）
- 2026-09-16 done：读完全部 scope 文件 + 对 UID/GID、SPA 路径前缀、dead config、异常吞噬做验证性 grep。
- 2026-09-16 doing：开始。

## 结论

### 1. 功能设计

**整体判断：合理，符合“个人 NAS 自托管”定位。** FastAPI 单体 + 同源托管 SPA + DB-first 运行时配置（免重启）是恰当选择；SPA catch-all 托管方式与 Vite 哈希资产天然防缓存（`main.py:46-59`）设计正确。

设计层问题（按重要度）：

- **D1（P1）无任何认证/授权，且存在文件删除等破坏性 API。** 所有 `/api/*` 完全开放（`app/main.py:29-37`），`POST /api/fs/delete` 可删媒体文件（`app/routers/fs.py:323-383`），`PUT /api/settings` 可写第三方凭证（`health.py:108-119`）。作为局域网自用产品可以接受，但 NAS 上常见端口暴露/反向代理误配，风险与收益不对称。建议最低成本方案：支持 `JZMEDIA_TOKEN`（env/设置页），对写操作要求 `Authorization: Bearer` 或 `X-Api-Token`，未配置时保持现状（不破坏自用体验）；或在 README 显著标注“禁止公网暴露”。
- **D2（P2）单进程内存态会话与多 worker 不兼容。** `stream.py` 的会话/预转码状态在进程内（R12 复核），部署上必须锁死单 worker；目前 Dockerfile/start.sh 都未声明 `--workers`，未暴露问题，但应在 README/AGENTS 明确“必须单 worker”。
- **D3（P2）SPA catch-all 对所有未知路径返回 200 HTML。** 包括拼错的 `/api/xxx` 与缺失的静态文件（如 `/favicon-xxx.png`），API 客户端拿到 HTML 而非 404 JSON（`main.py:46-59`）。更好：catch-all 内先判断 `full_path.startswith("api/")` → 返回 JSON 404；带扩展名且文件不存在 → 404。
- **D4（P2）健康检查语义过弱。** `/api/health` 永远 `status: ok`，不检查 DB 可写、媒体根可读、磁盘余量；`"phase": "phase2"` 是开发期遗留字段（`health.py:24`）。建议改为 `status` 反映 DB/媒体根可用性（真正的编排 healthcheck 需要），删除 `phase`。

### 2. 代码符合度（废弃代码/漏洞）

- **B1（P1）UID/GID 文档与实现不一致，NAS 部署实际以 root 运行。** README:128 与 `.env.example:20-22` 要求 NAS 用户配置 `UID/GID` 防文件属主问题，但唯一使用它们的是 **WSL 专用** 的 `docker-compose.override.yml:5`（部署 NAS 时明确要求删除该文件）；`docker-compose.yml` 全文不含 `user:`，也未把 UID/GID 传入 `environment`。结果：NAS 上容器 root 运行，NFO/海报/转码产物在宿主挂载卷里变成 root 属主，与文档承诺相反。修复：主 compose 增加 `user: "${UID:-0}:${GID:-0}"`（或 Dockerfile 建非 root 用户 + `--user`）。
- **B2（P2，受限路径穿越）SPA 静态文件前缀判断不是路径边界判断。** `main.py:50-52` 用 `candidate.startswith(DIST)`：由于 `candidate` 是 `normpath(join(DIST, full_path))`，`../dist-x/a` 会归一为 `/app/frontend/dist-x/a`，字符串前缀仍匹配 `DIST`（已用 Python 复现），即可读取 `dist` 同级、名字以 `dist` 开头的目录内文件（需知道文件名且该目录存在）。修复：`candidate == DIST or candidate.startswith(DIST + os.sep)`。
- **B3（P2）`_classify` 每个文件全表扫描 extras，目录列表复杂度 O(files × extras)。** `fs.py:61-64` 对非影片文件 `for e in store.list_all_extras()`（R02 复核该函数是否全表；即使有索引也是逐文件一次查询）。大目录（数百文件）页面会明显变慢。修复：`fs_list` 入口一次性拉 `{path: extra}` 映射传入 `_classify`。
- **B4（P2）废弃代码：**
  - `fs.py:152,207` 局部 import `is_sidecar as _is_sidecar` 后仅 `_ = _is_sidecar`，无任何作用。
  - `fs.py:280-281` `"/" in name` 死判断：`_safe_component`（`files.py:49-53`）已删除 `/`，永假。
  - `config.py:6-7` `app_port`、`env` 字段全库无引用（`APP_PORT` 实际由 compose/start.sh 消费，与 Settings 无关）。
  - `health.py:24` `"phase": "phase2"` 开发期遗留。
- **B5（P2）`start.sh` 变量转发不完整。** `.env` 只回读 `TMDB_API_KEY TMDB_READ_TOKEN TMDB_PROXY TMDB_LANGUAGE APP_PORT`（`start.sh:21`），宿主直跑时 `TMDB_IMAGE_BASE`、`TRANSCODER`、`HLS_SEGMENT_TYPE`、`AUDIO_COPY_SAFE` 被静默忽略。修复：改为白名单循环（排除 `MEDIA_HOST_PATH`/`DATA_HOST_PATH`/`BUILD_HTTP_PROXY` 等容器专用键）或 `set -a; . ./.env; set +a` + 覆盖容器路径键。
- **B6（P2）`GET /api/settings` 明文返回 `tmdb_proxy`。** `health.py:65` 直接返回有效值，而代理 URL 常含 `user:pass@host` 凭证；同一响应里 token 却已脱敏，标准不一致。修复：代理也返回 `mask_secret` 或仅返 `scheme://host:port`。
- **B7（P1，跨模块质量债）全应用零日志、76 处静默吞异常。** 全后端无 `import logging`（grep 证实），`except Exception: pass` 统计：`store.py 8 / scanner.py 8 / stream.py 23 / movies.py 12 / files.py 9 / fs.py 8 / media.py 8` 共 76 处。除 `stream.py` 有 debug 口外，扫描/整理/搬迁失败时用户与运维都无从追溯（文件操作部分错误被吞掉后返回 `skipped` / 无提示）。这不是 R01 独有，作为全局 P1 记入 99 报告；R01 侧建议先落地 `logging` 基础配置（`main.py` 入口 `logging.basicConfig(level=...)`，env `LOG_LEVEL`）。
- **B8（P2）无任何安全响应头/CSP。** FastAPI 未加 `SecurityHeaders`（`X-Content-Type-Options`、CSP、`Referrer-Policy`）；SPA 自绘字幕层若未来引入 `v-html` 会放大 XSS 面（R14 复核）。轻量中间件即可。

### 3. 代码质量差距（不符合量产要求）

- **Q1（P2）导入期副作用：** `main.py:27-28` 在模块导入时 `ensure_dirs()` + `store.init_db()`，`_DB` 连接等亦为模块级单例（R02 复核）。无法在测试中隔离、多进程导入行为隐式。量产做法：放入 lifespan 启动段（并保留 CLI 初始化入口）。
- **Q2（P2）构建可复现性：** `Dockerfile:5-7` 复制了 `package-lock.json` 却用 `npm install`，应为 `npm ci`；镜像无可选 `pip` 版本 pin（`requirements.txt` 全 `>=`），NAS 上重建可能拉到不兼容版本。建议 lock 到 `pip-compile`/`uv` 产物。
- **Q3（P2）未声明容器 PID 1 信号处理/健康检查：** `docker-compose.yml` 无 `healthcheck`，无 `init: true`；uvicorn 收到 SIGTERM 会走 lifespan（`main.py:21-22` 杀 ffmpeg），行为正确但无编排级探活。
- **Q4（P2）`fs.py` 结构问题：** 单文件同时承担“浏览/分类/删除/移动/DB 联动”，且 `_exec_move_one` 把 6 类副作用（DB、NFO、兄弟跟随、目录清理、resync）堆在一个函数里（`fs.py:150-211`）；错误处理层层 `try/except` 吞异常，任何一步失败都留下不一致状态。量产要求：操作前置校验 → 执行 → 后置补偿三段式，失败要有日志与回滚/重试语义。
- **Q5（P2）`requirements.txt` 混装运行依赖与宿主兜底包：** `static-ffmpeg` 仅宿主直跑需要（`requirements.txt:7-9`），Docker 镜像里多装一份无用依赖（增加镜像体积与供应链面）。建议使用 `requirements-dev.txt` 或 extras。
- **Q6（P2）`fs_list` 无分页/上限**（`fs.py:214-250`），超大目录一次性返回全部条目 + 每文件 DB 查询；建议 `limit/offset` 或至少将 `_classify` 批量化（同 B3）。

### 交叉引用
- B1 属部署文档一致性问题，需与 README §部署同步修。
- B7 为跨模块全局问题，最终计入 `99-final-report.md`。
- `_check_inside_root`/`_safe_component` 的路径约束正确性归 R09 深审；R01 仅确认 `fs.py` 全路径都经 `_check_inside_root`（`fs.py:31,262,278,285,305,309,342` 均调用），无裸拼外部输入。
- `/posters` `StaticFiles` 无目录列表且只读（`main.py:38`），未发现穿越面。
