# jzmedia

自用本地影视库管理 + 浏览器播放：扫描视频目录 → TMDB 自动刮削（简介/类型/产地/演员/海报/评分）→ 海报墙浏览、多维过滤、手动评分打标签 → 按规范整理文件 → 在线播放（Direct Play / remux / 音频单转 / 硬件转码，ASS/JASSUB 与 PGS 客户端字幕，多音轨 rendition 切换）。Kodi 兼容 NFO 落盘，Jellyfin / Emby 可直接读取。

技术栈：FastAPI + SQLite（FTS5 全文检索）+ Vue 3 + Vite，前后端同源托管（后端同时 serving 前端构建产物）。播放侧：ffprobe 探测 + fMP4 HLS（`-var_stream_map`）+ hls.js + JASSUB/libpgs（WASM 客户端字幕渲染）。

## 功能

- **媒体库**（`/`）：海报墙，关键词搜索（片名/原名/简介/演员/标签/类型），多维过滤——类型 / 产地大区（华语/日本/韩国/欧美/其他亚洲/其他）/ 国家·地区（大陆/香港/台湾细分）/ 年代+年份 / 自定义标签（多选 AND）/ 观看（已看/未看）/ 合集 / 评分（TMDB/豆瓣/自评来源 + 9+/8+/7+/6+ 档位）。多选模式可批量标已看/未看、批量加/去标签、加入合集。过滤条件同步到 URL，可分享链接
- **合集**（`/collections`、`/c/:id`）：手工合集（任意选片，如周星驰合集）+ TMDB 系列一键建（如功夫熊猫系列，详情页提示）；成员海报粒度，同片多版本自动跟随
- **详情页**（`/m/:id`）：TMDB 星级 + 豆瓣/自评分数（缺失自动隐藏）、演员点名反查、多版本文件列表；可手动改标题、自评/豆瓣分（0–10）、标签、简介覆盖；刮削错了可搜 TMDB 手动绑定
- **在线播放**（详情页 ▶）：按客户端实测能力四档决策——原文件直发（零 CPU）/ 仅换封装 / 仅音频转码 / 视频转码；HLS 输出 fMP4 + 多音轨 rendition（切音轨不重开）；字幕客户端渲染（文本 VTT、ASS/SSA→JASSUB、PGS→libpgs，仅 VobSub 烧录），支持外挂同名字幕与字幕延迟；硬件转码自动探测（VAAPI/QSV/NVENC，失败回落软件并自动重试）；HDR/DV 客户端支持则直通、否则提示复制直链外放；断点续播 + 夜间预转码静态秒播
- **扫描刮削**：遍历媒体目录，文件名解析 → TMDB 匹配 → 入库 + 海报下载 + 同目录写 NFO（独占单版本只留 `movie.nfo`，同片多版本才补各版本同名 `.nfo`，共享目录只写当前同名）；已入库跳过，剧集跳过（当前仅支持电影），年份容差 ±1，模糊命中标待确认
- **文件整理**：按 `标题 (年份)[-版本][-规格][-分卷][-版本N].ext` 规划（`POST /api/files/organize`，`mode=inplace|relocate`），默认只预览（dry-run），确认后执行并联动更新库与 NFO；冲突分疑似错配（人工重匹配）与规格变体（自动区分）。首次入库路径记为原始位置，搬错可用设置页「恢复到原始位置」（`POST /api/files/restore-original`，同样先预览再执行、绝不覆盖）搬回
- **设置页**（`/settings`）：TMDB 配置（Token/代理/语言，库优先免重启）+ 文件整理预览/执行

## 目录结构

```
app/            后端：main（应用+前端托管）/ store（SQLite+FTS+过滤）/ scanner / tmdb /
                regions（产地映射唯一来源）/ nfo / config /
                media（ffprobe 探测）/ caps（客户端能力）/ playback（四档决策+命令）/
                transcode（转码后端探测）/ routers（health|movies|files|jobs|stream…）
frontend/src/   前端：views（Library|Detail|Settings）/ components（PlayerModal）/ api.js /
                caps.js（能力检测）/ jassubLoader.js / pgsLoader.js / ratings.js
scripts/        本地工具：find_subs.py（只读列出可测字幕片源）
data/           运行数据：jzmedia.db + posters/ + transcode/ + fonts/（gitignored，不提交）
sample_media/   本地试玩用媒体目录（gitignored）
```

## 启动服务

### 方式一：Docker Compose（推荐，WSL 与 NAS 通用）

```bash
cp .env.example .env   # 首次：按需改 .env（媒体路径、TMDB 密钥见下文“配置”）
mkdir -p sample_media/电影 data
docker compose up --build -d
```

- 前台看日志：`docker compose up --build`（不加 `-d`）
- `docker-compose.override.yml` 仅本机开发用（热重载 + 宿主用户运行），compose 会自动加载；**部署到 NAS 时不要上传该文件**
- 验证：浏览器打开 http://localhost:8080（前端）或 http://localhost:8080/docs（接口文档），`/api/health` 应返回 `{"status":"ok",...}`

### 方式二：宿主直跑（不装 Docker 时调试用）

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
export DATA_DIR=./data MEDIA_ROOT=./sample_media TMDB_LANGUAGE=zh-CN
export TMDB_READ_TOKEN=$(grep -E '^TMDB_READ_TOKEN=' .env | cut -d= -f2-)
export TMDB_PROXY=$(grep -E '^TMDB_PROXY=' .env | cut -d= -f2-)
.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8080
```

注意：`.env` 里的是容器内路径（`/app/data`），宿主直跑必须如上用 `DATA_DIR`/`MEDIA_ROOT` 覆盖回去，只从 `.env` 取 TMDB 相关变量。

### 前端单独开发（可选）

```bash
cd frontend && npm install && npm run dev   # http://localhost:5173，/api 与 /posters 已代理到 8080
```

改完前端记得回根目录逻辑：`npm run build`（产物进 `frontend/dist`，由后端托管；compose 构建镜像时会自动构建）。

## 停止服务

```bash
docker compose stop    # 停止容器，保留容器（推荐的日常停止）
docker compose down    # 彻底删除容器；媒体与数据库是宿主 bind 挂载，不会丢失
docker compose logs -f # 看日志
```

宿主直跑的停止：前台启动的按 `Ctrl+C`；后台启动的按端口查 PID 再杀：

```bash
ss -ltnp | grep 8080   # 找到 python 进程的 pid=
kill <pid>
```

⚠️ 不要用 `pkill -f uvicorn`——匹配的是完整命令行，会把执行这条命令的 shell 自己也杀掉。

前端 dev server：前台 `Ctrl+C` 即可。

## 配置（`.env`）

| 变量 | 说明 |
|---|---|
| `APP_PORT` | 对外端口，默认 8080 |
| `MEDIA_HOST_PATH` / `DATA_HOST_PATH` | 宿主侧媒体目录 / 数据目录（compose 挂载用）。WSL 试玩用 `./sample_media`；NAS 上改为 `/volume1/video` 等 |
| `TMDB_READ_TOKEN` | TMDB Bearer Token（优先于 `TMDB_API_KEY`），没有则刮削不可用，库管理功能正常 |
| `TMDB_PROXY` | 运行时 TMDB API + 海报下载走的代理，直连不稳时填 |
| `TMDB_LANGUAGE` | 刮削语言，默认 `zh-CN` |
| `BUILD_HTTP_PROXY` | 仅镜像构建期 pip/npm 用，NAS 直连留空 |
| `UID` / `GID` | 容器运行用户，填宿主 `id -u`/`id -g`，避免容器建的文件宿主删不掉 |
| `TRANSCODER` | 转码后端：`auto`（默认；冒烟探测 VAAPI→QSV→NVENC，失败回落软件）\| `sw` \| `vaapi` \| `qsv` \| `nvenc`。NAS 启用硬件转码还需 compose 映射 `/dev/dri`（见 `docker-compose.yml` 注释与「部署到 NAS」） |
| `HLS_SEGMENT_TYPE` | `fmp4`（默认）\| `ts`（回滚旧 MPEG-TS 输出） |
| `AUDIO_COPY_SAFE` | 音频直通安全集覆盖（默认 hls.js 只信 `aac,mp3`；实测 EAC3 可用时可填 `aac,mp3,eac3,ac3`） |

TMDB 密钥申请（约 3 分钟）：注册 https://www.themoviedb.org/signup → 头像 Settings → API → Create → Developer → 应用名用途随便填 → 把 `API Read Access Token` 填进设置页「TMDB 配置」（或 `.env` 的 `TMDB_READ_TOKEN`；或把 `API Key` 填进 `TMDB_API_KEY`）。

日常改 Token/代理/语言直接在设置页改，库里的值优先于 `.env`、免重启生效；`.env` 只做首次启动兜底。`PUT /api/settings` 读写库配置（密钥只返脱敏后 4 位），缺席字段不动、显式空串=清空该项恢复跟随 `.env`。

## 数据存放

- `./data/jzmedia.db`：主库；`./data/posters/<tmdb_id>.jpg`：海报，对外服务于 `/posters`
- `./data/transcode/<版本id>/`：HLS 会话产物与字幕/字体抽取缓存（24h TTL 自清）；`subs/` 为 VTT/ASS/SUP 抽取，`fonts/` 为 MKV 附件字体 dump
- `./data/fonts/*.woff2|ttf|otf|ttc`：**ASS 渲染兜底字体投放目录**（不放仓库；中文 ASS 建议放一个中文字体，播放器自动加载）
- NFO：独占单版本目录只留 `movie.nfo`（标题/原标题/年份/简介/评分/类型/产地/演职员 + TMDB/IMDb ID），同片多版本（同目录同 `tmdb_id`）才为每个版本补 `<视频文件名>.nfo`，共享混放目录只写当前同名、不碰 `movie.nfo`；Kodi / Jellyfin / Emby 通用，`POST /api/jobs/rebuild-nfo` 可一键全量收敛历史残留
- 以上全部 gitignored，只在本地与 NAS 上存在

## 播放接口一览

| 方法与路径 | 说明 |
|---|---|
| `POST /api/stream/{id}/decide` · `POST /api/stream/versions` | 四档决策与同片多版本聚合（带客户端 `caps`；返回 `method/reasons/plan/media/direct_url`） |
| `POST /api/stream/{id}/sessions` · `GET /api/stream/sessions/{sid}/master.m3u8` | HLS 渐进式转码会话（前 3 分片即回）/ 主列表；`GET /sessions/{sid}/{name}` 取变体列表/init/分片 |
| `POST /api/stream/sessions/{sid}/ping` · `DELETE /api/stream/sessions/{sid}` | 心跳保活（10min 无心跳回收）/ 关播杀进程 |
| `GET /api/stream/sessions/{sid}/debug` | 自证口：进程/分片/各 rendition ENDLIST/实际后端/重试次数/caps 摘要/ffmpeg 尾日志 |
| `GET /api/stream/progress` · `POST` · `DELETE` | 单版本断点续播（读/写/清） |
| `POST /api/stream/prewarm` · `GET /api/stream/prewarm/{job_id}` | 夜间预转码（后台整片转完 → 静态 VOD 秒播）/ 进度 |
| `GET /api/stream/{id}/sub/{idx}.vtt|.ass|.sup` · `GET /api/stream/{id}/fonts` | 字幕抽取（文本→VTT、ASS/SSA→ASS、PGS→SUP）与字体清单；外挂同名 `.srt/.ass/.ssa/.sup` 自动并入 |
| `GET /api/stream/backends?refresh=1` | 转码后端探测结果（software/vaapi/qsv/nvenc + 判定原因） |
| `POST /api/stream/probe-missing` | 给无探测缓存（或探测结构过期）的版本补 ffprobe（离线本地） |

## 部署到 NAS（Synology 示例）

1. 把本目录拷到 NAS（如 `/volume1/docker/jzmedia`），**不含** `docker-compose.override.yml`
2. `.env` 设置：`MEDIA_HOST_PATH=/volume1/video`、`DATA_HOST_PATH=/volume1/docker/jzmedia/data`、`UID/GID` 按 DSM 用户填写，直连则 `BUILD_HTTP_PROXY` 留空
3. **硬件转码（可选但推荐）**：`docker-compose.yml` 里取消 `devices: [/dev/dri:/dev/dri]` 与 `group_add` 注释，`VIDEO_GID/RENDER_GID` 用 DSM 上 `stat -c '%g' /dev/dri/renderD128`（通常 render=109、video=44）填写；`TRANSCODER=auto` 即可。启动后 `GET /api/stream/backends` 应报 `vaapi`/`qsv`（报 software 说明设备/驱动/权限没到位）
4. Container Manager → 新增项目 → 路径选该目录 → 启动；浏览器打开 `http://NAS_IP:8080` 验证
5. 多阶段镜像已内置前端构建（node 构建 + python 运行），NAS 上无需装 Node

## 接口一览（媒体库）

| 方法与路径 | 说明 |
|---|---|
| `GET /api/health` · `GET /api/settings` · `PUT /api/settings` | 健康检查；配置摘要（密钥脱敏+来源/代理/图片源）与保存（库优先+env 兜底） |
| `POST /api/scan` | 全量扫描刮削 |
| `GET /api/movies` · `GET /api/search?q=` | 列表 / 全文检索；共同支持 `genre region country year decade tag`（可重复或逗号分隔，facet 内 OR、跨 facet AND，`tag` 多选为 AND）与 `min_rating` + `rating_source=tmdb\|douban\|custom`（单阈值 `>=`）、`watched=1\|0`（已看/未看）、`collection`（合集 ID，可重复或逗号分隔）；`decade=2020` 表示 2020–2029 |
| `GET /api/facets` | 各维度实时计数（类型/大区/国家/年/年代/标签/评分离散档/观看/合集），只返回有片的项 |
| `GET /api/movies/{id}` · `PATCH /api/movies/{id}` | 详情；手动改 `title overview_override douban_rating custom_rating tags edition spec watched` |
| `POST /api/movies/batch` | 海报墙多选批量：`{ids, ops:{watched, add_tags/remove_tags/set_tags, douban_rating, custom_rating}}`，海报粒度（同 tmdb 多版本自动跟随） |
| `GET/POST /api/collections` · `GET/PATCH/DELETE /api/collections/{id}` | 合集列表/新建/详情/改名/删除；成员海报粒度 |
| `POST /api/collections/{id}/members` · `POST /api/collections/{id}/members/remove` | 加入/移出合集（`{movie_ids}`，代表行 id 即可）；`POST /api/collections/from-tmdb-series {movie_id}` 按 TMDB 系列一键建合集 |
| `GET /api/collections/suggest` · `POST /api/collections/suggest/backfill` | 系列推荐（纯本地只读，库内同系列≥2部；已收录的不再推荐，有新片则进 `topups`；忽略态存浏览器 localStorage）；补全为后台任务（立即返回 job_id，轮询 `./status` 看进度，可取消，仅补系列信息不碰海报） |
| `POST /api/collections/{id}/members/top-up` | 一键补齐：把库内同系列新片收进已有合集（服务端实时重算差集；扫描/刷新/补全永不自动写成员） |
| `GET /api/tmdb/search?q=` · `POST /api/movies/{id}/match` | 手动匹配两步：搜 TMDB 候选 → 按 `tmdb_id` 强制绑定 |
| `GET /api/files/preview` · `POST /api/files/rename` | 整理预览；执行（默认 `dry_run:true` 只预览） |
| `GET /api/files/restore-candidates` · `POST /api/files/restore-original` | 偏离原始位置的影片预览；搬回首次入库位置（默认 `dry_run:true`，目标被占/源缺失跳过不上报覆盖） |
| `POST /api/jobs/backfill-meta` | 给存量影片补产地/类型等新元数据（不重下海报/NFO，保留手动标题）；`{"limit":N,"force":bool}` |
| `POST /api/jobs/rebuild-nfo` | 按收敛规则重建全库 NFO 并清历史同名残留；`{"limit":N,"dry_run":bool}`，返回 `wrote/deleted/by_mode` |
| `POST /api/jobs/douban-fetch` | 占位，固定 `501`（默认不爬豆瓣） |

## 排障

- `docker pull/build` 报 `Docker Desktop has no HTTPS proxy` 或 timeout，但宿主机 curl 正常：Docker Desktop 的代理垫片转发失败，容器内直连代理是通的。解法：WSL 侧用 crane 经可用代理拉镜像再 `docker load`，跳过 daemon 拉取：
  ```bash
  /tmp/crane pull --platform linux/amd64 python:3.12-slim /tmp/py312.tar
  docker load -i /tmp/py312.tar
  ```
  或 `.env` 配 `BUILD_HTTP_PROXY=http://nas:7890` 让构建期 pip 走该代理；可选根治是把 Docker Desktop 代理主机名换成固定 IP 后重启（会影响运行中容器）。
- 页面显示旧版：确认 `frontend/dist` 已 `npm run build`，且访问的是后端端口（8080）而非 dev 端口；compose 构建会重新打包前端。
- 8080 端口被占：`ss -ltnp | grep 8080` 查 PID，`kill <pid>`（见上文警告）。
- 刮削 401：`TMDB_READ_TOKEN` 无效或容器没读到 `.env`（`GET /api/settings` 看 `tmdb_configured`）。
- **播放没声音**：多为音轨直通失败（EAC3/AC3 等）。看播放器「⚙ 设置」里的原因行；`GET /api/stream/sessions/{sid}/debug` 看 `plan`/`backend`。默认只允许 AAC/MP3 直通，实测某编码可用时设 `AUDIO_COPY_SAFE=aac,mp3,eac3,ac3`。
- **ASS 字幕没样式/没文字**：中文字体缺失。把任意中文字体（`woff2/ttf/otf/ttc`）放进 `data/fonts/`（NAS 上为 `${DATA_HOST_PATH}/fonts/`），播放器会自动加载；实在没有可勾「兼容」用 VTT（丢样式但一定可见）。`python scripts/find_subs.py` 可列出可测片源。
- **转码慢/CPU 高**：`GET /api/stream/backends` 应报 `vaapi`/`qsv`（报 software 说明没吃到 `/dev/dri`：compose 未映射、`group_add` 组号不对或驱动缺失）。也可在「⚙ 设置」里降到 720p；4K HDR/DV 建议用「复制直链」交给电视/Kodi。
- **画面被控件挡住/字幕位置怪**：属字幕画布或控件条问题，先硬刷新（`frontend/dist` 需重新构建）；仍异常请在「调试」里复制信息反馈。
