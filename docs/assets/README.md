# 文档素材维护

本目录保存帮助站正文使用的截图、视频、字幕和概念图。维护说明核对于 **2026-10-03**；这不表示所有素材都在当天复拍。逐文件来源以 [manifest.json](manifest.json) 为准，拍摄环境与完整命令见[文档维护](../developer/documentation.md#重新拍摄素材)。

## 目录与用途

| 目录 | 内容 | 维护入口 |
|---|---|---|
| `screenshots/` | 正式教程 WebP 截图 | 按下表选择负责该场景的拍摄脚本 |
| `previews/onboarding/` | 新手向导截图与旧入口跳转页 | `scripts/capture_docs.py`；`index.html` 保留为帮助站兼容入口 |
| `videos/` | 连续操作录屏；每段包含 `.mp4`、`.webp` 封面和 `.vtt` 字幕 | `scripts/capture_docs.py` |
| `diagrams/` | 媒体库关系、入库流程、整理路径三个 SVG 概念图 | `scripts/capture_docs_diagrams.py` |
| `design/` | 帮助站使用的品牌 SVG、favicon 及位图 | `design/` 母版与 `scripts/build_design.py`；不直接修改生成物 |

2026-10-03 品牌修订 2 改为红白 J/播放一体轮廓，帮助站导航与 favicon 已同步。既有教程截图与录屏可能显示旧版黄色三角；本次未改变页面入口或操作步骤，保留各素材实际拍摄记录，不标记为重新拍摄。

正文通过 `DocFigure`、`DocVideo`、`DocDiagram` 引用相对路径；同一画面复用同一文件。Mermaid 渲染缓存位于 `docs/.vitepress/diagrams/`，由 `npm --prefix docs run diagrams` 管理，和这里的业务概念图分开。

## 当前拍摄流程

| 脚本 | 对应素材 | 来源边界 |
|---|---|---|
| `node scripts/ui_visual_review.mjs --capture-docs` | 浏览、详情、库连接、上传、资料匹配与整理面板等通用场景 | 真实 Vue 页面、完整模拟 API、虚构资料与原创 SVG 海报 |
| `node scripts/smoke_settings_ui.mjs --capture-docs` | 设置、未保存修改提醒、文件管理、文件预览及播放器控件 | 真实 Vue 页面、模拟 API；媒体预览使用临时合成文件 |
| `node scripts/smoke_ai_ui.mjs --capture-docs` | AI 设置、搜索与匹配 | 真实 Vue 页面、模拟 API 与模型响应，无真实 Key 或模型调用 |
| `python scripts/capture_docs.py /tmp/jzmedia-preview-实际目录` | 四步向导、首播、字幕、电影整理及连续录屏 | `preview_onboarding.py --docs-demo` 的隔离应用、临时数据库、本地 NFO 和合成片源；浏览器 API 不模拟 |
| `python scripts/capture_docs.py /tmp/jzmedia-preview-实际目录 --only player` | 已入库演示实例的播放信息与实际 720p 输出 | 同一隔离应用；当前文档演示片源为 1920×1080、H.264/AAC、180 秒 |
| `python scripts/capture_docs_diagrams.py` | 三个业务概念 SVG | 仓库代码生成，无浏览器拍摄或媒体数据 |

演示目录的准备方式和 Python Playwright 安装前置见[重新拍摄素材](../developer/documentation.md#重新拍摄素材)。普通新手预览仍使用 640×360、6 秒短片，不能据此演示从 1080p 降到 720p。原始浏览器截图和审查证据放在被忽略的 `output/playwright/` 或临时目录，正式文档只提交选定素材及来源记录。

先确认截图中的操作、名称与正文一致，再更新对应条目。录屏内容改变时同时检查封面与中文字幕的时序。实际 CSS 视口、像素密度、应用版本及压缩规格按条目记录；移动浏览器模拟不能当作手机或电视真机验收。模拟 API 截图也不能证明真实 NAS 操作、模型效果或外部服务连通。

拍摄必须使用隔离实例和合成媒体。不得为更新教程对真实媒体执行扫描、整理、删除或换绑。画面避免泄露凭据、私人路径和服务器地址；AI 模拟结果必须保留来源提示。

## 清单与日期规则

清单顶层声明 `provenance_scope: per_asset`，表示混合批次；不存在适用于全部文件的应用版本、提交、视口或拍摄脚本。`assets[]` 每项记录文件路径、用途、字节数和 SHA-256，并尽量记录来源、脚本、夹具、版本与视口。

- `verified_at` 保留各条目的历史登记日期；明确复拍后才由拍摄流程更新。Python 拍摄流程的新记录同时包含 `captured_at`，新登记的脚本生成 SVG 使用 `generated_at`；其他拍摄脚本沿用自己的逐项字段。
- 仅核对原文件与当前逻辑时记录 `reviewed_at` 和 `review_basis`，保持原拍摄日期。这适用于内容仍正确、无需重新生成的概念图。
- `source_commit` 表示提交基线，不代表工作区没有改动。新增 `source_sha256` 与 `source_digest_scope` 标识登记时指定范围的源码内容；具体脚本的冻结源码与夹具摘要按其记录解释。
- 旧清单从顶层继承的来源迁入对应条目时注明 `provenance_note`，不改拍摄日期。原先已有独立来源的其他批次保持自己的记录。
- `capture_docs_manifest.py` 只为 `captured` 明确列出的文件登记复拍，包括真实重拍后字节恰好相同的文件。拍摄脚本负责提交这个集合；不要用它为人工修改的文件补造拍摄证明。
- 单独运行 `python scripts/capture_docs_manifest.py` 只迁移记录格式、保留来源并检查差异。新增或内容变化但未提供拍摄证据的文件进入 `unverified_assets`，旧记录保持原哈希和日期；文件缺失也会提示。应通过对应拍摄或生成流程补齐证据后处理，不能仅刷新日期消除差异。

明确核对未变化的概念图时，可调用 `main(reviewed={"diagrams/libraries.svg": "核对过的源码路径及行为说明"})`；脚本要求该文件与现有清单的字节和哈希一致。`reviewed` 与 `captured` 表达不同事实。

`npm --prefix docs run check` 检查公开正文的三个图文组件引用：本目录中的截图、SVG、视频、封面与字幕必须登记，字节数和 SHA-256 必须匹配。检查不要求当天日期，也不将 Mermaid 缓存或品牌生成物当作浏览器拍摄素材。修改后按[文档构建与检查](../developer/documentation.md#构建与检查)完成测试、构建与检查。

## 概念图的核对依据

2026-10-03 核对现有三图，内容仍适用；无需为改变日期而重新生成。

| 图 | 核对内容 |
|---|---|
| `libraries.svg` | 媒体库存连接，电影/剧集视频库选择其子目录；扫描、上传、整理按视频库作用域执行。对应 `app/routers/media_libraries.py`、`app/routers/libraries.py`。 |
| `ingestion.svg` | 已有文件扫描，设备文件上传；导入后核对并播放，整理需要独立预览与确认。对应四步向导、上传入口及 `LibraryToolsPanel`。 |
| `organizing.svg` | 就地整理预览原/目标路径，正片与同茎字幕随行，目标冲突不覆盖；撤回使用还原位置。文件名为概念示例，实际结果以应用预览为准。 |

## 历史来源

以下是来源沿革，不能作为同名文件当前仍使用旧截图的依据；文件被重拍后以该条目的新记录为准。

- **2026-09-28 至 09-29**：早期教程基线为提交 `86a7143`、schema 28；曾使用主实例的本地/NAS 片库和独立演示实例。桌面主要为 1440×900 CSS、2x，移动为 407×904 CSS、3x。部分画面包含当时的库名称与真实影片资料。这些历史来源事实不追溯改写成合成媒体拍摄。
- **2026-10-02**：帮助站加入隔离应用的新手、字幕、整理截图与录屏（早期基线 `ee5dbcc`）；同时开始用独立脚本重拍通用 UI、设置/文件管理和 AI 教程图。这些流程的真实应用与模拟 API 来源不同。
- **2026-10-03**：导航、筛选及后续教程核对继续分批更新素材，早期基线包括 `57b9962`、`8884427`。具体文件日期、源码与夹具哈希按清单记录，不能将某一批检查项数或复拍日期套用到其他素材。

不再使用的重复 PNG、被替代导出和失效截图，在确认正文、兼容入口和脚本无引用后删除；历史原件通过 Git 查询，不在正式素材目录永久保留重复副本。场景仍有教学价值但画面过期时，优先用其所属隔离脚本复拍同一路径，并同步清单与正文。
