# jzmedia

自用本地影视库管理：扫描视频目录 → TMDB 自动刮削（简介/类型/产地/演员/海报/评分）→ 海报墙浏览、多维过滤、手动评分打标签 → 按规范整理文件。**不做播放**，只做媒体库管理；Kodi 兼容 NFO 落盘，Jellyfin / Emby 可直接读取。

技术栈：FastAPI + SQLite（FTS5 全文检索）+ Vue 3 + Vite，前后端同源托管（后端同时 serving 前端构建产物）。

## 功能

- **媒体库**（`/`）：海报墙，关键词搜索（片名/原名/简介/演员/标签/类型），多维过滤——类型 / 产地大区（华语/日本/韩国/欧美/其他亚洲/其他）/ 国家·地区（大陆/香港/台湾细分）/ 年代+年份 / 自定义标签（多选 AND）/ 评分（TMDB/豆瓣/自评来源 + 9+/8+/7+/6+ 档位）。过滤条件同步到 URL，可分享链接
- **详情页**（`/m/:id`）：TMDB 星级 + 豆瓣/自评分数（缺失自动隐藏）、演员点名反查、多版本文件列表；可手动改标题、自评/豆瓣分（0–10）、标签、简介覆盖；刮削错了可搜 TMDB 手动绑定
- **扫描刮削**：遍历媒体目录，文件名解析 → TMDB 匹配 → 入库 + 海报下载 + 同目录写 `movie.nfo`；已入库跳过，剧集跳过（当前仅支持电影），年份容差 ±1，模糊命中标待确认
- **文件整理**：按 `电影名 (年份)/电影名 (年份).ext` 规划，默认只预览（dry-run），确认后执行并联动更新库与 NFO
- **设置页**（`/settings`）：TMDB 配置摘要 + 文件整理预览/执行

## 目录结构

```
app/            后端：main（应用+前端托管）/ store（SQLite+FTS+过滤）/ scanner / tmdb /
                regions（产地映射唯一来源）/ nfo / routers（health|movies|files|jobs）/ config
frontend/src/   前端：views（Library|Detail|Settings）/ components / api.js / ratings.js
data/           运行数据：jzmedia.db + posters/（gitignored，不提交）
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

TMDB 密钥申请（约 3 分钟）：注册 https://www.themoviedb.org/signup → 头像 Settings → API → Create → Developer → 应用名用途随便填 → 把 `API Read Access Token` 填进 `TMDB_READ_TOKEN`（或把 `API Key` 填进 `TMDB_API_KEY`）。

## 数据存放

- `./data/jzmedia.db`：主库；`./data/posters/<tmdb_id>.jpg`：海报，对外服务于 `/posters`
- `movie.nfo`：写在每部影片同目录（标题/原标题/年份/简介/评分/类型/产地/演职员 + TMDB/IMDb ID），Kodi / Jellyfin / Emby 通用
- 以上全部 gitignored，只在本地与 NAS 上存在

## 接口一览

| 方法与路径 | 说明 |
|---|---|
| `GET /api/health` · `GET /api/settings` | 健康检查；配置摘要（密钥是否已配/代理/图片源） |
| `POST /api/scan` | 全量扫描刮削 |
| `GET /api/movies` · `GET /api/search?q=` | 列表 / 全文检索；共同支持 `genre region country year decade tag`（可重复或逗号分隔，facet 内 OR、跨 facet AND，`tag` 多选为 AND）与 `min_rating` + `rating_source=tmdb\|douban\|custom`（单阈值 `>=`）；`decade=2020` 表示 2020–2029 |
| `GET /api/facets` | 各维度实时计数（类型/大区/国家/年/年代/标签/评分离散档），只返回有片的项 |
| `GET /api/movies/{id}` · `PATCH /api/movies/{id}` | 详情；手动改 `title overview_override douban_rating custom_rating tags` |
| `GET /api/tmdb/search?q=` · `POST /api/movies/{id}/match` | 手动匹配两步：搜 TMDB 候选 → 按 `tmdb_id` 强制绑定 |
| `GET /api/files/preview` · `POST /api/files/rename` | 整理预览；执行（默认 `dry_run:true` 只预览） |
| `POST /api/jobs/backfill-meta` | 给存量影片补产地/类型等新元数据（不重下海报/NFO，保留手动标题）；`{"limit":N,"force":bool}` |
| `POST /api/jobs/douban-fetch` | 占位，固定 `501`（默认不爬豆瓣） |

## 部署到 NAS（Synology 示例）

1. 把本目录拷到 NAS（如 `/volume1/docker/jzmedia`），**不含** `docker-compose.override.yml`
2. `.env` 设置：`MEDIA_HOST_PATH=/volume1/video`、`DATA_HOST_PATH=/volume1/docker/jzmedia/data`、`UID/GID` 按 DSM 用户填写，直连则 `BUILD_HTTP_PROXY` 留空
3. Container Manager → 新增项目 → 路径选该目录 → 启动；浏览器打开 `http://NAS_IP:8080` 验证
4. 多阶段镜像已内置前端构建（node 构建 + python 运行），NAS 上无需装 Node

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
