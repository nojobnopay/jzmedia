---
version: 0.23.0
reviewed: 2026-10-10
---

# 文档制作与发布

本页的内容组织与编辑规则适用于全仓文档，包括 `android-tv/`；后面的组件、素材与发布流程仅用于 HTML 帮助站。

帮助站点将任务教程、图片、视频和图表构建为 HTML，随应用在 `/help/` 提供，也可独立部署到 GitHub Pages；API 调试文档保持 `/docs`。正文使用 Markdown 与少量受控组件，一处编辑即可生成应用内帮助、在线帮助和离线发布包。

常用入口：[编写与排版](#markdown-排版) · [文档评审](#修改后的评审) · [格式检查与预览](#构建与检查) · [图文组件](#图文与演示组件) · [重新拍摄](#重新拍摄素材)。普通正文修改从编写、评审和检查开始；制作新素材时再使用拍摄流程。

## 内容与导航

普通用户先看 `docs/index.md` 与 `docs/user-guide/` 的任务页；安装、配置和备份位于 `docs/getting-started/`；技术约束位于 `docs/developer/`。计划、完成状态与待验收事项保留在仓库 `docs/roadmap/`，私有记录保留在被 Git 忽略的 `docs/private/`，这两个目录均不发布。

先确定读者和页面用途，再选择结构；以下分类不要求新建四套目录，也不要求所有文档都写成操作步骤。

| 用途 | 读者需要 | 最小结构 |
|---|---|---|
| 教程 | 第一次完成任务 | 目标、准备、步骤、成功判断、失败恢复 |
| 操作指南 | 完成一项具体操作 | 适用条件、最短操作路径、验证与恢复 |
| 参考 | 查找事实或约束 | 按对象分组、简表或示例、限制 |
| 解释 | 理解机制与取舍 | 核心问题、关系、原因与边界 |

教程和操作指南围绕一个结果组织，深入原理与详细参考放在步骤之后或链接到对应页面。准备部分说明适用系统、工作目录与必要工具；每一步给出可复制的命令或明确的界面动作、需要替换的值、预期结果及必要的文件影响。失败恢复从报错现象说明检查办法和重试入口，不堆放完整日志。关键操作不能只出现在截图或视频里。首次配置由[首次入库](../user-guide/onboarding.md)单独维护，其他页面链接该教程。

构建、测试、安装等重复流程优先收进可复用脚本，由脚本检查前置条件、报告失败原因并打印产物位置。新手步骤保留最少的常用命令；高级参数与手动排查另设参考段落。脚本的帮助、参数与正文应在同一变更中更新。

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
| 在线帮助的发行触发、托管设置与失败重试 | [统一发布](releasing.md#online-help)；本页维护构建与预览方法 |

目录 README 以入口为主，独立子项目的 README 可承载唯一的快速开始流程；其他页面链接该流程，不复制命令。旧综合页只保留标题、旧锚点和任务链接，不再追加功能说明。纯导航和兼容页设置 `search: false`，让搜索结果直接定位正文。复用图片引用同一资源路径，不复制文件。

版本、依赖、任务名与默认值以代码、锁文件或脚本为事实来源；正文解释如何使用，避免抄写整份配置和易变的测试数量。必需的最低版本与示例要能追溯到来源。每次行为变更直接替换对应原文并检查引用，不追加“本轮修复”“最新进展”段落。

只保留能帮助完成当前任务、解释有效约束或支持后续决策的信息。删除重复段落前，先将仍有效的独有信息并入对应正文，并核对旧锚点；过期步骤、已解决的临时问题及无后续用途的记录直接删除，不换一个目录继续堆积。删除页面前还需检查公开清单、应用帮助链接及仓库引用。只读限制、文件变更范围等与当前操作直接相关的短提示仍放在步骤旁。

一次性实施和测试总结写入提交或 PR 说明，不新增独立报告目录；`developer/` 保留持续维护的技术参考，素材来源和核对日期维护在素材清单。计划项在 `roadmap/` 中维护当前状态、实现依据与未测范围，不累计每轮开发流水。确有持续验收需要时保留一份可机器读取的清单，记录场景、环境、结果和证据位置；原始输出留在忽略目录或外部产物中，不能用模拟通过代替真机结论。私有库路径、文件移动映射及本地操作报告继续保存在 `private/`，不能因未被引用或被 Git 忽略就当作废弃文件删除。

### Markdown 排版

- **标题**：普通正文页只用一个一级标题；二级标题划分任务或主题，三级标题组织步骤或子主题，不跳级。通常到三级即可，确有独立分支时才用四级标题；不用整段加粗代替章节标题。首页和纯导航页按其模板处理。
- **编号**：含命令、图片或成功判断等分段内容的长流程用 `### Step 1：动作`，在所属二级任务内连续编号；另一个独立流程从 Step 1 开始。短流程用有序列表，单个动作不强加编号，并列条件用无序列表。普通章节不叠加 `1.1.1` 式编号，参考和解释不套步骤模板。
- **表格**：只用于参数、规格和选项的横向比较，优先两到三列，一行对应一个对象。单元格只放短语或短句；长解释、连续操作、代码块及大串路径移到表外。不要用表格承担整页布局，也不靠 `<br>` 强塞多段内容。
- **段落与提示**：一段说明一件事；标题、段落、列表、表格和代码块之间留空行。关键结果可用“**预期结果：**”，操作旁的必要提示可用普通引用块；不整段加粗，也不堆叠提示框。
- **命令**：需逐步复制执行的命令独立成带语言标记的代码块；说明工作目录和占位值。代码块属于某个列表项时保持缩进，避免渲染后脱离步骤或重置编号。
- **导航**：包含多个独立任务的长页可在开头放一行主要入口；不复制完整的多级目录。改标题时同步检查锚点，已有入口尽量保持稳定。
- **图表**：截图定位控件，流程图说明分支，时序图说明协作，架构图说明职责与关系。图名和正文说明应能回答一个具体问题；不强制每页配图，不仅靠颜色表达关系，不手写全仓图表流水号。

新增或重写正文时，让必要标题、步骤和结论在普通 Markdown 中完整可读，帮助站组件只增强布局与交互。关键内容不只放在组件属性中；涉及组件写法时同时核对源码阅读和帮助站渲染，不能假定两者等价。

### 修改后的评审

Agent 编写、同步或审核项目文档时使用仓库 `.agents/skills/jzmedia-docs/SKILL.md`；本页维护通用规则，skill 负责选择正文、核对事实和执行验证，不另存一份规范。功能变更先判断文档影响，仅内部重构且使用方式与约束不变时，不扩大为全仓文档整理。

只要求审核时不修改文件，按“位置、问题、读者影响、依据、建议”报告，并区分事实错误与风格建议。编写或维护时在任务范围内直接更新唯一正文；提交前核对以下五项，并在提交或 PR 说明中记录执行的检查和未覆盖范围：

- 页面结构符合用途；任务页让读者从准备开始完成操作，看到明确结果，失败时知道下一步；参考和解释能定位事实、机制与限制。标题层级、步骤编号和表格遵循上面的排版规则。
- 命令、脚本帮助与当前实现一致；能安全自动化的重复操作已经有统一入口。
- 同一事实只有一个维护位置，功能变更已替换旧文，新增内容有明确的长期用途。
- 删除或移动后链接、锚点与导航仍可用；公开站页面不链接站点外的相对路径。
- 公开页 `version`/`reviewed` 跟内容走（更新时机见下方元信息说明）；lint 的元信息提醒要逐条确认，不是无脑忽略。
- 按变更范围运行格式 lint、链接检查、脚本验证及帮助站构建检查；通过自动检查不等于已验证教程或真机行为。

每个公开页面包含以下元信息；`reviewed` 表示核对内容的日期，不能仅因构建成功就修改。更新时机跟内容走：行为、约束或命令变化时，把 `version` 升到当前开发版本、`reviewed` 写核对当日；纯文字润色、内部页面与私有记录的变化不改。构建不批量改写这两个字段：

```yaml
version: 0.22.3
reviewed: 2026-10-07
```

## 构建与检查

首次或依赖锁文件变化后，在项目根安装依赖：

```bash
npm ci --prefix docs
```

修改公开正文、导航、素材或帮助站实现后，运行完整验证：

```bash
npm --prefix docs run verify
```

`verify` 依次执行格式 lint、源链接检查、帮助站构建、测试和产物检查。部分测试读取构建后的搜索索引、HTML 与 CSP，必须先成功构建，避免测试旧产物。成功时会打印源文件与公开页面的检查结果。

### 选择应用内或在线构建

VitePress 将正文预先生成 HTML，再由浏览器运行搜索、图片放大等交互。应用内由 FastAPI 提供这些静态文件，在线帮助由 GitHub Pages 提供，无需连接用户的应用、数据库或媒体。两种构建共用公开页面清单、导航、组件与素材，部署路径和产物目录分别设置：

| 构建目标 | 访问路径 | 产物目录 |
|---|---|---|
| `app`（默认） | `/help/` | `docs/.vitepress/dist/` |
| `pages` | `/jzmedia/` | `docs/.artifacts/pages/` |

`JZMEDIA_DOCS_TARGET` 选择目标，`JZMEDIA_DOCS_BASE` 指定在线站的路径前缀。下列命令从仓库根执行，验证 GitHub 项目站构建：

```bash
JZMEDIA_DOCS_TARGET=pages JZMEDIA_DOCS_BASE=/jzmedia/ npm --prefix docs run verify
```

部署路径参与资源、导航和旧教程跳转链接生成，不能只把 `/help/` 的成品目录改名上传。两个目标分别输出，不覆盖应用内产物；`package` 始终生成 `/help/` 布局的离线包。修改构建、主题或部署路径相关代码时，分别运行默认与 `pages` 目标的完整验证。

站点显示根 `version.properties` 中的构建版本，并链接对应发行版本。应用内帮助随安装版本交付，在线帮助跟随最新正式版本；页首的 `version` 与 `reviewed` 仍表示该篇内容的适用版本与核对日期，不随构建批量改写。发行时的自动部署、首次托管设置与重试见[在线帮助发布](releasing.md#online-help)。

### 只检查格式与链接

日常编辑或仅修改未发布文档时，先运行快速检查；这两条命令不构建站点：

```bash
npm --prefix docs run lint
python3 scripts/check_docs_links.py
```

两项检查共用源文件清单，覆盖根文档、Android TV、开发与用户文档、工具、设计、计划和 `.agents/skills/`；排除依赖、构建产物、运行数据、私有记录及其他 agent 本地目录。检查规则不改变页面的公开范围。

`lint` 在格式检查后运行 `scripts/check_docs_meta.py`：公开页正文改动而页首 `version`/`reviewed` 未更新（或公开页缺元信息、格式不对）时列出提醒。它只提醒不拦截——确认无需更新时忽略即可；参数与 `--strict` 见仓库 `scripts/README.md`。

格式 lint 由根 `.markdownlint-cli2.mjs` 配置，锁定 `markdownlint-cli2` 版本。它检查标题层级、空行、列表缩进与编号、代码围栏语言和表格结构；`docs/scripts/markdown-rules.mjs` 另外检查每个 H2 内的 Step 连续编号，以及步骤标题不能藏在 `DocStep` 属性中。首页的一级标题由模板生成，只有 `docs/index.md` 对首行 H1 规则例外。

中文行长、表格是否过密、内容重复与操作是否清楚仍需人工判断，不设固定字数或列数。Vue 标签允许保留，组件和最终锚点另由构建及产物检查验证。

只检查某个目录或文件时，参数始终相对仓库根：

```bash
npm --prefix docs run lint -- android-tv
npm --prefix docs run lint -- docs/developer/api.md
```

默认只检查、不改文件。需要修复可自动处理的格式问题时，显式指定 `--fix` 和目标范围，再审阅 diff；内容取舍和步骤改写仍由作者完成：

```bash
npm --prefix docs run lint -- --fix docs/developer/api.md
```

新增或调整操作命令另需核对脚本帮助、实现和相应验证。不要为文案修改运行无关的后端、前端或设备测试。

### 预览与产物检查

需要查看生成页面时，再单独启动预览：

```bash
npm --prefix docs run preview
```

编辑期间可独立运行 `npm --prefix docs run dev`；`preview` 默认在 `http://127.0.0.1:4174/help/` 预览已构建产物。这两个命令持续运行，不应放在非交互检查流水线前面。

在线产物使用同一预览入口，先完成上面的 `pages` 构建，再执行：

```bash
JZMEDIA_DOCS_TARGET=pages JZMEDIA_DOCS_BASE=/jzmedia/ npm --prefix docs run preview
```

访问 `http://127.0.0.1:4174/jzmedia/`。`preview` 与构建应使用相同目标和路径前缀；无需启动 jzmedia 后台。

开发预览和发布产物只使用本地资源；主题与导航配置在 `docs/.vitepress/`。`.vitepress/public-pages.mjs` 是公开页面与导航的单一来源，新增教程需登记到此清单；仓库 Markdown 检查默认排除依赖、产物、运行数据和私有记录。`docs/scripts/check.mjs` 按当前目标检查最终 HTML 的资源/锚点、离线依赖、公开边界、SSR、CSP 和应用帮助入口，必须在 build 成功后执行。

FastAPI 的 `app/help_site.py` 只挂载应用目标的 `docs/.vitepress/dist/`；没有 `index.html` 返回 503，未知页面返回 404。构建生成 `csp-hashes.json`，仅应用内帮助 HTML 的策略加入这些脚本哈希；GitHub Pages 使用其托管服务的响应头。Docker 复制应用目标静态产物；宿主 `start.sh` 通过 `scripts/build_docs.py` 按输入内容摘要检测更新和删除，排除私有记录、路线图及历史报告。两种部署都只发布检查通过的产物，不能把整个 `docs/` 当静态目录发布。

开发参考中的 Mermaid 保留在正文中。修改图定义后运行 `npm --prefix docs run diagrams` 生成并提交 `docs/.vitepress/diagrams/` 中的 SVG；首次生成需要安装 Playwright Chromium。普通文档构建只核验生成图存在，不需要浏览器和联网渲染。

标题结构、图文布局、主题、组件、搜索或交互变化时追加浏览器检查；普通文字修正不要求浏览器全量回归。检查支持已运行的应用，也可以自行启用临时静态服务：

```bash
npm --prefix docs run test:browser -- --url http://127.0.0.1:8080/help/
npm --prefix docs run test:browser
JZMEDIA_DOCS_TARGET=pages JZMEDIA_DOCS_BASE=/jzmedia/ npm --prefix docs run test:browser
```

验证重点是：首页可进入首次入库、搜索可定位教程、旧入口和锚点可达、资源加载成功、窄屏可读、键盘能放大与关闭图片、视频字幕可选、关闭 JavaScript 后仍能读到正文及关键步骤。检查构建清单和发布包，确保私有目录、计划、运行数据与凭据未被复制。

## 图文与演示组件

组件在主题中全局注册。资源路径相对当前 Markdown 文件解析；文件名使用稳定的场景名，不把时间或影片数据库 ID 当成公开资源名。

- `DocStep` 只负责图文布局：在标签前用标准 `### Step N：动作` 写步骤标题，不传 `number`、`title`。文字放默认 slot，图放 `#image` slot；桌面并排，手机按顺序排列。标签前后保留空行，让 Markdown 正常解析。
- `DocFigure` 使用 `src`、具体描述的 `alt`、`caption`，支持查看原图；`marks` 可按图片百分比坐标标注控件，必须核对实际截图。重拍后未经重新核对的旧坐标先移除，正文用实际按钮名称说明操作。不要让图注承担唯一操作说明。
- `DocDiagram` 与图片使用相同字段。适合两层媒体库关系、扫描与整理区别、系统架构等。
- `DocVideo` 使用 `src`、`poster`、`captions`、`title`；slot 放等价文字步骤。视频手动播放，必须有 WebVTT 字幕和封面。

步骤标题与布局分开书写，普通 Markdown 阅读器、站点目录和搜索都能读到同一标题：

```md
### Step 1：检查连接

<DocStep>

在设置中点击“检查连接”。看到连接成功后，继续下一步。

</DocStep>
```

需要配图时按[首次入库](../user-guide/onboarding.md)使用 `#image` slot；没有图文并排需要时直接写 Markdown，无需包组件。

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

访问 `http://127.0.0.1:8090/help/`。`package` 固定构建并检查应用目标，不受在线目标环境变量影响。发布包位于 `docs/.artifacts/jzmedia-help/`，其中 `help/` 与应用内静态产物一致，根 `index.html` 提供跳转。将整个目录复制到另一台机器后以本地 HTTP 服务打开即可；不依赖互联网或应用数据库。

功能界面变更时同时核对关联教程、截图和视频，更新真正失效的素材；未涉及内容不必整批重拍。发布前记录运行过的检查与未覆盖的设备，不用模拟截图代替真机验收。
