---
version: 0.19.0
reviewed: 2026-10-03
---

# 文档制作与发布

帮助站点将任务教程、图片、视频和图表构建为 HTML，随应用在 `/help/` 提供；API 调试文档保持 `/docs`。正文使用 Markdown 与少量受控组件，一处编辑即可生成应用内帮助和离线发布包。

## 内容与导航

普通用户先看 `docs/index.md` 与 `docs/user-guide/` 的任务页；安装、配置和备份位于 `docs/getting-started/`；技术约束位于 `docs/developer/`。计划、完成状态与待验收事项保留在仓库 `docs/roadmap/`，私有记录保留在被 Git 忽略的 `docs/private/`，这两个目录均不发布。

每个任务页围绕一个结果，按“目标 → 准备 → 操作步骤 → 完成后检查 → 常见问题 → 下一步”组织。参考表和总导航可按用途简化；关键动作、预期结果和文件影响必须写进正文，不能只出现在截图或视频里。首次配置由[首次入库](../user-guide/onboarding.md)单独维护，其他页面链接该教程。

电影、剧集、播放器旧综合页保留原章节标题作为兼容入口，链接到新任务页。改标题或拆页时保留原锚点，并更新站点导航和应用内帮助目标。站点外的仓库文件使用代码路径说明，不创建无法在发布包解析的相对链接。

### 内容归属与去重

`developer/`、`getting-started/`、`user-guide/` 是正文源目录；`.vitepress/dist/` 和 `.artifacts/` 是可重新生成的发布产物。已有 HTML 不代表对应 Markdown 可以删除。

| 内容 | 唯一维护位置 |
|---|---|
| 安装依赖、启动命令与路径 | [安装教程](../getting-started/deployment.md)；开发环境文档只补充开发专用步骤 |
| 首次配置到首播 | [首次入库](../user-guide/onboarding.md)；部署与设置页只指向此流程 |
| 设置入口、来源选择、界面偏好 | [设置与维护](../user-guide/settings.md)；来源能力另见[扫描与元数据](../user-guide/metadata.md) |
| 匹配、编辑、整理与撤销 | 各任务页；前置任务完成后链接下一步，不复制整段后续操作 |
| 控件与续播、字幕、预览缓存 | 对应播放任务页；实现与兼容决策集中在[播放链路](playback.md) |
| 数据表、字段与迁移 | [数据模型](data.md)；API 文档说明请求与行为，不重复字段定义 |
| 拍摄方法、检查与打包命令 | 本页；素材清单记录逐文件信息，资源 README 保留批次来源与历史说明 |

目录 README 只提供入口；旧综合页只保留标题、旧锚点和任务链接，不再追加功能说明。纯导航和兼容页设置 `search: false`，让搜索结果直接定位正文。复用图片引用同一资源路径，不复制文件。

删除重复段落前，先将独有信息并入对应正文，并核对旧锚点；删除页面前还需检查公开清单、应用帮助链接及仓库引用。只读限制、文件变更范围等与当前操作直接相关的短提示仍放在步骤旁。

一次性实施和测试总结写入提交或 PR 说明，不新增独立报告目录；`developer/` 保留持续维护的技术参考，素材来源和核对日期维护在素材清单。计划项在 `roadmap/` 中记录状态，标为已完成时附上实现与验证依据，未测范围继续明确列出。私有库路径、文件移动映射及本地操作报告继续保存在 `private/`，不能因未被引用或被 Git 忽略就当作废弃文件删除。

每个公开页面包含以下元信息；`reviewed` 表示核对内容的日期，不能仅因构建成功就修改：

```yaml
version: 0.19.0
reviewed: 2026-10-03
```

## 图文与演示组件

组件在主题中全局注册。资源路径相对当前 Markdown 文件解析；文件名使用稳定的场景名，不把时间或影片数据库 ID 当成公开资源名。

- `DocStep` 使用 `number`、`title`，文字放默认 slot，图放 `#image` slot；桌面并排，手机按顺序排列。标签前后保留空行，让 Markdown 正常解析。
- `DocFigure` 使用 `src`、具体描述的 `alt`、`caption`，支持查看原图；`marks` 可按图片百分比坐标标注控件，必须核对实际截图。重拍后未经重新核对的旧坐标先移除，正文用实际按钮名称说明操作。不要让图注承担唯一操作说明。
- `DocDiagram` 与图片使用相同字段。适合两层媒体库关系、扫描与整理区别、系统架构等。
- `DocVideo` 使用 `src`、`poster`、`captions`、`title`；slot 放等价文字步骤。视频手动播放，必须有 WebVTT 字幕和封面。

先用截图定位控件，再用短句说明点击后的结果。连续交互使用短视频，选路和关系使用图表；命令、参数与详细限制仍以可检索和复制的文字呈现。图上需要标注时保留可修改源，避免只存不可编辑的合成图。

资源目录为仓库 `docs/assets/screenshots/`、`docs/assets/videos/`、`docs/assets/diagrams/`；新手图位于 `docs/assets/previews/onboarding/`。新素材逐文件信息记录在仓库 `docs/assets/manifest.json`，批次来源和旧素材基线保留在 `docs/assets/README.md`。历史真实库截图与新隔离演示截图分别标明，不将移动模拟当作真机测试。

## 隔离演示

需要亲自核对新手流程时，在项目根运行：

```bash
.venv/bin/python scripts/preview_onboarding.py
```

然后访问 `http://localhost:18080/`；端口占用可加 `--port 18081`。每次运行在 `/tmp/jzmedia-preview-*` 创建独立数据库、媒体目录和前端产物，不读取项目 `.env`，也不修改主实例的 `frontend/dist`。需已安装项目依赖、前端依赖与 FFmpeg。

演示目录自带六秒合成短片和本地 NFO，临时副本禁用 TMDB 电影与剧集搜索。按“开始配置 → 稍后配置 TMDB → 检查并使用此视频库 → 扫描此视频库 → 查看入库结果”完成导入。使用预设的“隔离演示 · 临时数据”库；手动添加真实目录后，应用对该目录的操作仍然会生效。

`Ctrl+C` 停止实例，临时目录保留；再次运行创建新的空库。录制整理和字幕演示同样使用隔离数据与合成片源，真实媒体库不能作为自动写入操作的拍摄对象。

## 重新拍摄素材

文档录屏使用专门的演示数据。先启动全新实例并保持运行：

```bash
.venv/bin/python scripts/preview_onboarding.py --port 18128 --docs-demo
```

该拍摄器使用 Python Playwright，`docs/` 的 Node 依赖不能代替 Python 包。另开终端准备独立拍摄环境，将命令中的目录替换为启动时打印的新临时目录：

```bash
python3 -m venv /tmp/jzmedia-docs-python
/tmp/jzmedia-docs-python/bin/python -m pip install playwright==1.63.0
/tmp/jzmedia-docs-python/bin/python -m playwright install chromium
/tmp/jzmedia-docs-python/bin/python scripts/capture_docs.py /tmp/jzmedia-preview-实际目录
.venv/bin/python scripts/capture_docs_diagrams.py
```

拍摄脚本真实操作隔离实例，生成新手、字幕、整理三段视频及对应截图，并补拍播放信息和实际降档界面；从系统 PATH 或项目静态 FFmpeg 依赖中寻找 FFmpeg/ffprobe。`--docs-demo` 生成 180 秒的 1920×1080 H.264/AAC 合成视频，供真实软件转码到 720p；普通演示仍为 640×360、6 秒。需先安装前端依赖。完整重拍必须使用全新 `--docs-demo` 实例；`--only subtitles` 或 `--only organizing` 用于已经完成入库、尚未整理的演示重拍，`--only player` 只重拍已入库演示的播放信息与降档截图。

脚本限制目标为 `/tmp/jzmedia-preview-*`，拒绝主服务端口，并校验视频编码、30–90 秒时长及 10 MB 大小上限。演示启动器复制已构建的帮助站供本地预览；尚未构建时，旧 `/preview/index.html` 展示构建命令和继续配置链接。原始 WebM 与运行校验文件留在拍摄器打印的 `/tmp/jzmedia-docs-capture-*`，不随仓库发布。

仓库 `docs/assets/manifest.json` 保存素材关联信息、时长、体积与校验值，`docs/assets/README.md` 记录基线。拍摄成功后自动更新本次实际拍摄项，内容哈希相同的重新拍摄也会记录此次核对；仅单独运行 `scripts/capture_docs_manifest.py` 整理清单时，未变化素材保留原来源和日期。三段视频保留真实操作时长、无旁白，中文说明由 WebVTT 字幕提供。用户概念图由 `scripts/capture_docs_diagrams.py` 生成，图表源可维护，不依赖截图中的文字。

### 同数据的整页审查与教程截图

跨页面改版先冻结改前源码，再用同一份完整模拟资料比较；旧教程截图只能说明历史界面，不能当作同数据基线。`scripts/ui_visual_review.mjs` 使用 `scripts/fixtures/uiReviewData.mjs` 的虚构影片、剧集、合集、文件和设置，以及原创 SVG 海报；真实 Vue 页面独立构建到临时目录，不读取项目 `.env`、数据库、媒体或 NAS，不连接外网。

```bash
node scripts/ui_visual_review.mjs --demo
node scripts/ui_visual_review.mjs --source /tmp/jzmedia-goal-before --label goal-before
node scripts/ui_visual_review.mjs --label goal-after
node scripts/ui_visual_review.mjs --only settings-tmdb,settings-libraries --label settings-check
node scripts/ui_visual_review.mjs --capture-docs
```

`--source` 指向包含 `frontend/` 的冻结源码目录；fixture 始终来自当前脚本，改前和改后的 fixture 哈希必须一致。`--demo` 打印独立本机地址供手动体验，输入与操作只修改内存模拟状态。完整审查覆盖电影/剧集/合集浏览与详情、设置、四步向导、筛选、勾选、上传与删除弹窗，以及加载、空、无结果和失败状态。`--core` 只保留主要页面，适合局部布局迭代，不能代替完整验收。

脚本保存 1440×1000 和 390×844 CSS 视口截图，另在 375×812 检查窄屏。`output/playwright/goal-before/manifest.json` 和 `goal-after/manifest.json` 记录源码与 fixture 哈希、场景、页面错误和布局测量；`output/playwright/ui-review/index.html` 展示成对图片，`comparisons.json` 的 `comparable` 标记两组 fixture 是否一致。拍摄过程中不要修改前端源码；脚本会核对拍摄前后的源码哈希。局部重拍仅在源码与 fixture 均一致时合并旧结果，避免一组截图混入多个实现版本。

`--capture-docs` 必须使用当前工作区的完整审查，不与 `--source`、`--only`、`--core` 或 `--demo` 混用。脚本从通过检查的场景生成 WebP q86，只更新对应教程图和素材清单条目，保留其他来源记录；需要现有 FFmpeg。新增或调整教程图映射时同步检查实际图片、正文、替代文字和图注。设置/文件与 AI 的专门拍摄脚本仍负责其操作场景，见下文；截图通过不代表真实 NAS、TMDB 或模型服务已验证。

### 智能辅助的无 Key 演示与截图

智能辅助使用真实 Vue 页面与完全模拟的 API，不需要真实 API Key。脚本不加载项目 `.env`，不启动应用后端，不读取数据库、媒体或 NAS。Vite 将当前前端独立构建到 `/tmp`，临时 HTTP 服务仅监听 `127.0.0.1`，只提供构建资源和显式模拟的 API；浏览器策略阻止外部请求。页面底部始终标明模拟来源，连接测试返回“模拟响应：未连接 OpenCode Go 或其他模型服务”。这些画面用于说明操作，不能证明 Go 支持影视请求、实际连通或中文效果。

先安装前端与文档依赖；截图还需要文档 Playwright 的 Chromium，以及 PATH 中的 FFmpeg 或项目已准备的 `static-ffmpeg`：

```bash
npm ci --prefix frontend
npm ci --prefix docs
node docs/node_modules/playwright/cli.js install chromium
node scripts/smoke_ai_ui.mjs --demo
```

打开启动时打印的本机地址，可以操作 Go 设置、电影与剧集智能搜索、匹配建议及二次确认；示例标题、密钥状态和返回候选都是内存中的虚构值，请勿输入真实密钥。此演示仅覆盖列出的页面，其他 API 返回明确的模拟缺失错误。`Ctrl+C` 停止服务、清除临时构建并丢弃内存修改。

重新拍摄四张教程图：

```bash
node scripts/smoke_ai_ui.mjs --capture-docs
```

拍摄前先运行同一脚本的浏览器冒烟，随后保存 Go 设置与模拟测试提示、可编辑搜索条件、电影确认候选和剧集目录归属限制。Playwright 在 1440×1000 CSS、1x 视口生成原始 PNG，FFmpeg 转为 WebP q86，写入 `docs/assets/screenshots/ai-*.webp`；原图留在命令打印的 `/tmp/jzmedia-ai-ui-*` 供核对。默认不传参数只执行冒烟，不更新教程图片。

截图成功后只更新素材清单内对应 AI 条目，每项分别记录版本、日期、代码基线、视口、哈希及“真实界面 + 模拟 API”的来源；旧批次全局来源和其他素材记录保留。`scripts/capture_docs_manifest.py` 同样保留已有 AI 条目及未变化历史素材的来源。更新后查看实际图片，确保 Go 用途提示、确认按钮和目录锁原因可读，再执行文档构建与检查。

## 构建与检查

在项目根安装依赖，先测试和构建，再检查生成产物：

```bash
npm ci --prefix docs
python3 scripts/check_docs_links.py
npm --prefix docs test
npm --prefix docs run build
npm --prefix docs run check
npm --prefix docs run preview
```

编辑期间可独立运行 `npm --prefix docs run dev`；`preview` 默认在 `http://127.0.0.1:4174/help/` 预览已构建产物。这两个命令持续运行，不应放在非交互检查流水线前面。

开发预览和发布产物只使用本地资源。构建产物位于 `docs/.vitepress/dist/`；主题与导航配置在 `docs/.vitepress/`。`.vitepress/public-pages.mjs` 是公开页面与导航的单一来源，新增教程需登记到此清单；仓库 Markdown 检查默认排除依赖、产物、运行数据和私有记录。`docs/scripts/check.mjs` 另外检查最终 HTML 的资源/锚点、离线依赖、公开边界、SSR、CSP 和应用帮助入口，必须在 build 成功后执行。

FastAPI 的 `app/help_site.py` 只挂载该构建目录；没有 `index.html` 返回 503，未知页面返回 404。构建生成 `csp-hashes.json`，仅帮助 HTML 的策略加入这些脚本哈希。Docker 复制帮助静态产物；宿主 `start.sh` 通过 `scripts/build_docs.py` 按输入内容摘要检测更新和删除，排除私有记录、路线图及历史报告。不能把整个 `docs/` 当静态目录发布。

开发参考中的 Mermaid 保留在正文中。修改图定义后运行 `npm --prefix docs run diagrams` 生成并提交 `docs/.vitepress/diagrams/` 中的 SVG；首次生成需要安装 Playwright Chromium。普通文档构建只核验生成图存在，不需要浏览器和联网渲染。

浏览器检查支持已运行的应用，也可以自行启用临时静态服务：

```bash
npm --prefix docs run test:browser -- --url http://127.0.0.1:8080/help/
npm --prefix docs run test:browser
```

验证重点是：首页可进入首次入库、搜索可定位教程、旧入口和锚点可达、资源加载成功、窄屏可读、键盘能放大与关闭图片、视频字幕可选、关闭 JavaScript 后仍能读到正文及关键步骤。检查构建清单和发布包，确保私有目录、计划、运行数据与凭据未被复制。

## 设置与文件管理的隔离验收

设置与文件管理的隔离回归和截图使用：

```bash
node scripts/smoke_settings_ui.mjs
node scripts/smoke_settings_ui.mjs --drafts-only
node scripts/smoke_settings_ui.mjs --capture-docs
```

该脚本构建真实 Vue 页面，但使用内存 API 模拟；不读取 `.env`、真实数据库或媒体。覆盖设置分组、按库草稿与来源顺序、明确打开/返回入口、刷新后返回原工具标签、目录分页、右键改名、退出守卫、切库与扫描目标一致性，以及手机更多菜单。预览用 FFmpeg 在临时目录生成测试视频和图片，另有合成字幕/NFO/PDF；真实播放器对照验证预览不触发任何观看进度请求或已看/连播事件，普通播放仍读取、清除、保存进度。截图来源逐项记录在 `docs/assets/manifest.json`；不能当作真实 NAS 写入或网络服务验证。运行需要已安装 FFmpeg（可使用现有 static-ffmpeg），`--capture-docs` 同时用它生成 WebP。

## 离线发布包

```bash
npm --prefix docs run package
python3 -m http.server 8090 --directory docs/.artifacts/jzmedia-help
```

访问 `http://127.0.0.1:8090/help/`。发布包位于 `docs/.artifacts/jzmedia-help/`，其中 `help/` 与应用内静态产物一致，根 `index.html` 提供跳转。将整个目录复制到另一台机器后以本地 HTTP 服务打开即可；不依赖互联网或应用数据库。

功能界面变更时同时核对关联教程、截图和视频，更新真正失效的素材；未涉及内容不必整批重拍。发布前记录运行过的检查与未覆盖的设备，不用模拟截图代替真机验收。
