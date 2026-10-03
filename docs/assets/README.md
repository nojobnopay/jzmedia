# 文档图片与视频资源

截图统一存 `docs/assets/screenshots/`，正文用仓库相对路径引用（如用户手册页用 `../assets/screenshots/xxx.webp`）。同一画面不复制多份。

## 2026-10-03 导航图标与响应式布局复拍

本批使用应用 `0.19.0`、基准提交 `57b9962` 后的当前工作区，更新顶部导航的统一线性图标、频道选中态、桌面工具分组及手机图标加文字布局。`node scripts/ui_visual_review.mjs --capture-docs` 重新生成电影墙与筛选、手机电影墙、合集详情、媒体库连接/新建、系统维护、电影/剧/季详情及电影/剧集上传共 12 张教程图。来源仍为真实 Vue 页面与 `scripts/fixtures/uiReviewData.mjs` 的完整虚构资料、原创 SVG 海报；所有 API 模拟，不读取真实配置、数据库或媒体，不访问外部服务。

桌面视口为 1440×1000、手机为 390×844 CSS，1x，WebP q86；长页面保存全页，上传对话框保存视口。逐图版本、日期、源码与夹具哈希、规格及 SHA-256 见 [manifest.json](manifest.json)，本批 12 个文件的内容哈希已核对，覆盖下方同名历史资源的来源说明。其余未复拍图片和录屏保留各自批次记录。

本批同一份冻结前端源码及夹具完成 35 个场景、1440/390/375px 三种宽度的 105 项布局与状态检查，均通过；其中 1440/390px 保存 70 张审查原图，375px 在全量脚本中只作布局检查。导航专项另保存 5 个代表页面的 15 项布局记录及 375px 补图，并检查 8 种宽度下的长库名、至少 44px 点击区域、Tab 顺序和媒体库作用域、10 个路由入口的频道高亮，以及键盘焦点与帮助新标签页行为。最终清单未记录非预期页面错误、溢出、损坏图片或外部请求；预期失败状态由夹具模拟。这些结果验证网页布局和导航，不代替真实媒体操作、服务连通或电视设备验收。原始证据位于被 Git 忽略的 `output/playwright/navigation-final-v2/` 与 `output/playwright/goal-after/`。

## 2026-10-02 系统性 UI 优化复拍

本轮更新电影墙与筛选、手机电影墙、电影/剧/季详情、合集详情、媒体库连接/新建、系统维护和电影/剧集上传共 12 张教程图。来源是 `scripts/ui_visual_review.mjs --capture-docs`：真实 Vue 页面、完整虚构资料与原创 SVG 海报，所有 API 模拟。桌面视口 1440×1000、手机 390×844 CSS，1x；长页面保存全页，对话框保存视口。逐项来源、源码/数据哈希与尺寸以 [manifest.json](manifest.json) 为准，覆盖下方同名历史资源的来源说明。

同时用各自隔离脚本重拍设置/文件管理与 AI 操作图；新手四步及首播图、视频和字幕则通过新的临时数据库与合成短片实走重拍。没有操作真实媒体或使用真实模型凭据。未涉及的字幕和整理录屏保留原批次记录。

同数据改前/改后对照与审查结果在被 Git 忽略的 `output/playwright/`，可按[整页审查流程](../developer/documentation.md#同数据的整页审查与教程截图)重新生成。

## 2026-10-02 设置与文件管理改造

`scripts/smoke_settings_ui.mjs --capture-docs` 使用真实 Vue 页面和完全模拟的内存 API，未读取实际配置、数据库或媒体。以下截图替代历史同名图，逐图版本、日期与哈希以 [manifest.json](manifest.json) 为准；不能据此证明真实 NAS 文件操作或外部服务连通。

| 文件 | 场景 | 规格 |
|---|---|---|
| `settings-navigation.webp` | 三组导航与概览 | 1440×1000 CSS、1x，WebP q86 |
| `settings-sources.webp` | 在线资料服务 | 同上 |
| `settings-matching.webp` | 按库匹配规则、顺序和试搜 | 同上 |
| `settings-offline.webp` | 独立离线资料入口 | 同上 |
| `file-browser.webp` | 目录树、文件列表和待扫描提示 | 同上 |
| `file-browser-loading.webp` | 延迟模拟响应下的目录加载动画与提示 | 同上 |
| `file-tools-entry.webp` | 独立“管理文件”入口 | 同上 |
| `file-preview-text.webp` | 原地查看合成字幕，关闭后保留目录与选择 | 同上 |
| `file-preview-video.webp` | 合成视频预览，不记录观看进度 | 同上 |
| `file-review.webp` | 离开前的扫描核对提示 | 同上 |
| `file-browser-mobile.webp` | 手机更多操作菜单 | 390×844 CSS、1x，WebP q86 |

文件预览示例中的视频、图片由已安装的 FFmpeg 在临时目录生成；字幕、NFO、PDF 由脚本合成。源媒体不作为真实片源验收，也不导入应用数据库。

## 2026-10-02 智能辅助与 OpenCode Go

本批四张图片使用应用 `0.19.0` 的真实 Vue 页面，所有 API 与资料均由 `scripts/smoke_ai_ui.mjs` 模拟；未加载 `.env`，未读取真实密钥、数据库或媒体，未连接应用后端、NAS、TMDB 或模型服务。图中密钥为虚构测试值，底部横幅与连接测试结果明确标注模拟；不能据此证明 OpenCode Go 可用于影视请求或已真实连通。Go 的用途限制提示来自实际产品界面。

| 文件 | 场景及正文 | 规格 |
|---|---|---|
| `screenshots/ai-settings-go.webp` | Go 预设、用途限制与模拟连接结果；[设置](../user-guide/settings.md) | 1440×1000 CSS、1x，WebP q86 |
| `screenshots/ai-search-preview.webp` | 可编辑解析条件、确认后才应用；[查找电影](../user-guide/find-movies.md) | 同上 |
| `screenshots/ai-match-movie.webp` | 选择电影候选后的独立确认；[资料匹配](../user-guide/metadata.md) | 同上 |
| `screenshots/ai-match-tv.webp` | 整剧建议及目录归属限制；[剧集匹配](../user-guide/tv-matching.md) | 同上 |

重拍与持续演示命令见[无 Key 演示与截图](../developer/documentation.md#智能辅助的无-key-演示与截图)。各图的日期、工作区基线、体积与 SHA-256 单独记入 [manifest.json](manifest.json)；这些逐项来源覆盖该文件的旧批次默认来源，下面的新手素材和历史截图仍保留各自拍摄记录。

## 2026-10-02 帮助站素材

本批新手、字幕和电影整理素材使用 `0.19.0` 的当前工作区（基准提交 `ee5dbcc`，包含新手向导和移动布局更新）。所有操作发生在 `scripts/preview_onboarding.py --docs-demo` 创建的独立本机服务、临时数据库和 `/tmp` 媒体目录；没有读取 `.env`、访问真实片库或复用真实影片。拍摄时全局帮助入口尚未加入，任务内的操作与当前界面一致。

`星海漫游（演示短片）` 为虚构资料，`999900001` 为仅在临时库使用的演示缓存编号；180 秒片源由 FFmpeg 的 `testsrc2` 和静音音轨生成。中文、英文及临时 SRT 都由脚本生成。API、扫描、播放、字幕和文件整理使用真实应用代码，未拦截替换浏览器 API 响应。电影/剧集联网搜索仅在临时应用副本中停用，扫描采用固定 NFO 和本地镜像。

| 文件 | 场景及正文 | 规格 |
|---|---|---|
| `previews/onboarding/00-welcome.webp`–`05-complete.webp` | 欢迎页与四步向导；[新手教程](../user-guide/onboarding.md) | 1440×1000，WebP q86 |
| `previews/onboarding/06-mobile.webp` | 手机宽度完成页；同上 | 407×904 CSS，2x，Chromium 模拟 |
| `previews/onboarding/07-play.webp` | 扫描成功后的首次播放；同上 | 1440×1000，WebP q86 |
| `screenshots/demo-subtitles.webp` | 换轨、延迟和已加载的本地字幕；[字幕](../user-guide/subtitles.md) | 1440×1000，WebP q86 |
| `screenshots/demo-organize-preview.webp` / `demo-organize-result.webp` | 整理前预览与已移动结果；[电影整理](../user-guide/organizing.md) | 1440×1000，WebP q86 |
| `videos/onboarding.mp4` | 欢迎 → 跳过 TMDB → 检查目标库 → 扫描 → 完成 → 播放 | 规格见素材清单 |
| `videos/subtitles.mp4` | 播放 → 齿轮换轨 → 延后 0.5 秒 → 文件选择器加载 SRT | 规格见素材清单 |
| `videos/organizing.mp4` | 目录整理 → 核对预览 → 整理选中确认 → 结果 → 文件管理 | 规格见素材清单 |
| `diagrams/libraries.svg` / `ingestion.svg` / `organizing.svg` | 两层库关系、入库流程和整理前后示例 | 保留可编辑 SVG；生成源为 `scripts/capture_docs_diagrams.py` |

三段视频为连续真实 UI 操作录屏，无静态幻灯片拼接、无倍速处理，使用 H.264、20 fps、1440×1000、`faststart`。同名 `.vtt` 为中文字幕，`.webp` 为第 4 秒封面。逐文件大小、时长、SHA-256、关联页面和日期以 [manifest.json](manifest.json) 为准，不另抄写。原新手 PNG 留作历史原件，正式教程引用 WebP；`previews/onboarding/index.html` 跳转正式 HTML 帮助页。

### 复现

依赖、隔离实例、重拍命令与校验规则统一见[重新拍摄素材](../developer/documentation.md#重新拍摄素材)。这里仅记录来源与拍摄基线；下面历史批次使用过真实库，不属于当前自动录制流程。

## 拍摄基线

- 日期：2026-09-28；代码 `86a7143`（应用 `0.19.0`，schema `28`）。2026-09-29 终检重拍 14 张基线图（2x/3x + q90），并第二批补拍 25 张功能图（下表新增行，规格相同：桌面 1440×900 @2x、WebP q90、单图 <500 KB）。
- 2026-10-02 仅重拍 `mobile-library.webp`，使用当前移动端布局及隔离的前端演示数据；API 与海报请求均拦截为模拟响应，海报为占位图，未访问实际媒体库。视口 407×904 CSS、3x、WebP q90；正文图注注明演示数据。其余图片仍沿用上次拍摄基线。
- 视口：桌面 1440×900 CSS（2x 像素密度，2880×1800 像素），移动按红米 K50 Ultra（407×904 CSS，3x，1221×2712 像素）；浏览器 100% 缩放，深色主题默认。Playwright 无头 Chromium 全视口截图，不含浏览器地址栏。WebP q90（主墙海报图 q85，440 KB，单图均 <500 KB）。
- S01–S06、S09–S10 及第二批功能图取自主实例 `http://localhost:8080`（`./start.sh` 启动）的真实媒体库：`media`（本地）与 `NAS`（SMB 直读）。未执行整理、删除、换绑或重扫；整理面板预览为 dry-run 只读；播放截图产生的观看进度已获允许。
- S07、S08、`match-review.webp`、`rematch-candidates.webp` 取自隔离演示实例 `http://127.0.0.1:8081`（临时 `DATA_DIR`、临时媒体目录，2 秒合成测试片，TMDB 正常匹配；拍完即弃，不影响真实库）。正文引用这些图时须注明演示环境。
- 截图中可见的 `media · 上传演示` 视频库是维护者本地保留的空测试库（0 片 0 集，用于演示上传多目标选择器）；非公共部署会出现的内容，仅作说明，不入文档正文。
- `settings-sources.webp` 中“网络代理”输入框值以内网地址，截图时已用「（内网代理地址，已遮盖）」遮盖；重拍时沿用同一做法。

| 文件 | 画面 | 拍摄入口 | 来源 |
|---|---|---|---|
| `movie-library.webp` | 电影墙、继续观看、排序 | `/?media=2` | 真实库 NAS |
| `movie-detail.webp` | 电影背景、海报、播放入口 | `/m/875?media=2`（古董局中局） | 真实库 NAS |
| `tv-season.webp` | 亮剑第 1 季分集与播放入口 | `/tv/72/s/1?media=2` | 真实库 NAS |
| `player-settings.webp` | 播放画面、进度条、设置面板 | `/m/405?media=1` 点播放再开齿轮 | 真实库本地 |
| `library-connection.webp` | 媒体库与其下视频库 | `/settings?sec=sec-libraries` | 真实库 |
| `scan-review.webp` | 视频库标签、入库三步 | `/settings?sec=sec-libtools&library=2` | 真实库 |
| `organize-preview.webp` | 就地整理预览与目标路径 | 演示实例同页（`library=2`） | 演示环境 |
| `tv-bindings-preview.webp` | 归属变更预览与集号重叠冲突 | 演示实例剧详情归属弹窗 | 演示环境 |
| `file-browser.webp` | 已由 2026-10-02 文件管理器截图替代 | `?sec=sec-files` | 模拟 API，见上方新批次 |
| `movie-filters.webp` | 电影墙筛选展开与维度计数 | `/?media=2` → 筛选 | 真实库 NAS |
| `movie-cast.webp` | 详情下半：演职员与影片信息 | `/m/875?media=2` 滚动至演职员 | 真实库 NAS |
| `movie-similar.webp` | 详情下部：库中类似推荐行 | `/m/875?media=2` 滚动至库中类似 | 真实库 NAS |
| `movie-posters.webp` | 换海报候选弹窗 | `/m/875?media=2` 海报 → 换海报 | 真实库 NAS |
| `movie-multiselect.webp` | 多选圈与批量操作浮条 | `/?media=2` 选中 1 部 | 真实库 NAS |
| `collections.webp` | 合集详情与成员 | `/c/56?media=2`（唐人街探案） | 真实库 NAS |
| `person.webp` | 人物页作品列表 | `/p/1065761?media=2`（陈思诚） | 真实库 NAS |
| `tv-show.webp` | 剧详情 hero 与选季 | `/tv/38?media=2` | 真实库 NAS |
| `tv-versions.webp` | 集列表 V1/V2 版本分组 | `/tv/38/s/1?media=2` | 真实库 NAS |
| `tv-episode-match.webp` | 集详情修正集号匹配 | `/tv/17/s/1/e/1555?media=2` 更多操作 | 真实库 NAS |
| `tv-bindings.webp` | 归属与季号弹窗（三步） | `/tv/72?media=2` 更多操作 | 真实库 NAS |
| `tv-extras.webp` | 剧详情花絮区 | `/tv/17?media=2` 滚动至花絮 | 真实库 NAS |
| `match-review.webp` | 入库整理 ② 核对匹配（未匹配行） | 演示实例 `library=3` | 演示环境 |
| `rematch-candidates.webp` | 重新匹配候选与绑定 | 演示实例 `/m/1` | 演示环境 |
| `player-subtitles.webp` | 播放设置：字幕轨与字幕调整 | `/m/408?media=1` 播放 → 齿轮 | 真实库本地 |
| `player-info.webp` | 播放信息：实际输出与原因 | `/m/408?media=1` 播放 → 更多选项 | 真实库本地 |
| `player-transcode.webp` | 切 720p 后视频转码与实际输出 | `/m/898?media=2` 播放 → 画质 720p | 真实库 NAS |
| `tv-organize-dialog.webp` | 单剧整理弹窗（只读预览） | `/tv/31?media=2` 更多操作 | 真实库 NAS |
| `tv-organize-panel.webp` | 剧集目录整理面板预览（dry-run） | `?sec=sec-libtools&library=3` 预览 | 真实库 NAS |
| `organize-history.webp` | 整理历史/撤销批次 | 同页历史区展开 | 真实库 NAS |
| `restore-panel.webp` | 恢复到原始位置候选 | `?sec=sec-libtools&library=1` 还原位置 | 真实库本地 |
| `library-new.webp` | 新建媒体库表单 | `?sec=sec-libraries` 添加媒体库 | 真实库 |
| `settings-sources.webp` | 已由 2026-10-02 在线资料服务截图替代 | `?sec=sec-tmdb` | 模拟 API，见上方新批次 |
| `tv-maint.webp` | 剧集资料维护面板 | `?sec=sec-libtools&library=3` 资料维护 | 真实库 NAS |
| `settings-index.webp` | 系统维护分区 | `?sec=sec-index` | 真实库 |
| `upload-movie.webp` | 上传影片目标库、文件与开始上传 | `/?media=1` 添加影片 → 上传文件（临时文件仅选定未上传；目标选中 `上传演示` 测试库） | 真实库（本地） |
| `upload-tv.webp` | 上传剧集多选文件模式与季号 | `/tv?media=1` 添加剧集 → 上传文件 | 真实库（本地测试库，未实际上传） |
| `settings-navigation.webp` | 已由 2026-10-02 分组导航截图替代 | `/settings` | 模拟 API，见上方新批次 |
| `mobile-library.webp` | 手机导航、紧凑继续观看与三列海报墙 | `/`（407×904 CSS，3x） | 隔离演示数据，Chromium 移动模拟，2026-10-02 |
| `mobile-player.webp` | 手机宽度下的播放控件 | `/m/405?media=1` 播放（红米 K50 Ultra：407×904 CSS，3x） | 真实库，模拟环境 |

## 注意事项

- `library-connection.webp` 保留了局域网 NAS 名（`\\Joey-DS425\video`），理解 SMB 地址栏需要该上下文；无凭据、无公网地址。重拍时沿用同一原则。
- 移动端两张为 Chromium 移动模拟（407×904 CSS、mobile UA、touch，输出 1221×2712 像素），无手机外框装饰；数据来源和拍摄批次见上表，不能据此视作真机验证。历史记录见 [验收记录](../roadmap/backlog.md#文档改版验收记录)。
- 上表历史批次使用的临时截图工具未提交；当前拍摄脚本已纳入仓库，按[隔离演示流程](../developer/documentation.md#重新拍摄素材)重拍，不能对真实媒体执行扫描、整理或删除。
- 图表源与 SVG 发布规则统一见[文档构建与检查](../developer/documentation.md#构建与检查)。
