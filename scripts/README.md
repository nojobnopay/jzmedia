# 仓库维护工具

核对日期：2026-10-15。

以下命令均从仓库根目录执行。Python 工具使用 `.venv/bin/python scripts/文件名.py`，Node 工具使用 `node scripts/文件名.mjs`。

安装与常规检查见[开发指南](../docs/developer/development.md)，帮助站工具另见[文档维护](../docs/developer/documentation.md)。

## 构建与检查

| 工具 | 用途与输出 |
| --- | --- |
| [release.py](release.py) | 同步统一版本，`check` 校验版本与修改摘要；`notes` 预览摘要，`notes --draft` 从 Git 提交生成待审阅草稿。从同一提交检查并构建 Docker 镜像和 APK；`build --validate-only` 不要求版本说明、不更新发行标签。见[统一发布](../docs/developer/releasing.md)。 |
| [github_release.py](github_release.py) | Actions 的 `preflight/package/publish` 入口：检查标签身份和版本说明、打包八个附件、核验并发布 GHCR 和 GitHub Release；Release 摘要取自同提交的 `CHANGELOG.md`。 |
| [github_pages.py](github_pages.py) | 在线帮助的 `select/verify-source/check-current` 身份检查：核对最新正式发行、源码与部署时状态；流程见[在线帮助发布](../docs/developer/releasing.md#online-help)。 |
| [github_signing.py](github_signing.py) | 从 Actions Secret 恢复既有 Debug 密钥并按证书 Variable 核验；配置见[云端签名](../docs/developer/releasing.md#cloud-signing)。 |
| [check_python.py](check_python.py) | 严格运行 Python 静态检查，仅保留固定导出门面与同名重导出例外。 |
| [build_frontend.py](build_frontend.py) | 按输入摘要构建 `frontend/dist/`；由 `start.sh` 调用，包含删除检测。 |
| [build_docs.py](build_docs.py) | 按输入摘要构建 `docs/.vitepress/dist/`；由 `start.sh` 调用，排除私有资料、路线图和历史报告。 |
| [build_design.py](build_design.py) | 从 `design/` 母版生成跨端资产、令牌和用途清单；`--check` 只校验，品牌位图使用 `--render-brand`。详见[设计资产](../design/README.md)。 |
| [check_docs_links.py](check_docs_links.py) | 检查仓库 Markdown 的本地链接、锚点和代码围栏。 |
| [check_docs_meta.py](check_docs_meta.py) | 提醒公开帮助页正文改动后 `version`/`reviewed` 未随内容更新、缺失或格式不对；默认只提醒不改变退出码，`--strict` 计入退出码，`--base 基线` 指定比较点（默认 HEAD）。`docs` 的 lint 已自动运行它。 |

链接检查默认跳过依赖、构建产物、运行数据和私有目录；`--root docs` 可限定范围。帮助站渲染结果另用 `npm --prefix docs run check`。

## 隔离冒烟与界面审查

这些工具创建临时数据或使用模拟 API；常规运行不需要启动项目的真实服务。浏览器工具使用 `docs/` 的 Playwright 和 `frontend/` 的 Vue/Vite 依赖。

| 工具 | 验证内容 |
| --- | --- |
| [smoke_api.py](smoke_api.py) | 临时后端的扫描、整理、恢复、合集、设置和鉴权等 API；[_smoke_app.py](_smoke_app.py) 是其内部启动器。 |
| [smoke_multi_library.py](smoke_multi_library.py) | 临时双库的扫描、搜索、版本隔离，以及删库保留文件。 |
| [smoke_metadata_offline.py](smoke_metadata_offline.py) | 本地索引、NFO 回放、离线搜索和合成 IMDb 数据导入。 |
| [smoke_settings_ui.mjs](smoke_settings_ui.mjs) | 设置草稿离开保护、文件操作与预览、合成片源播放及 ASS 全屏合成。 |
| [smoke_casting.mjs](smoke_casting.mjs) | 合成电影/分集、真实手机播放器、隔离后端与模拟 DLNA 接收端；需要项目 `.venv` 和已安装的 FFmpeg/FFprobe，不搜索真实设备。 |
| [smoke_ai_ui.mjs](smoke_ai_ui.mjs) | 智能设置、搜索和匹配，模型及 API 均为模拟。 |
| [smoke_browse_filters.mjs](smoke_browse_filters.mjs) | 电影/剧集筛选的真实 Vue 页面与模拟 API。 |
| [smoke_tv_airing.mjs](smoke_tv_airing.mjs) | 剧集播出/收藏、官方目录、库范围、每周推荐与维护状态。 |
| [smoke_design_system.mjs](smoke_design_system.mjs) | 组件和资产目录；`--capture` 保存截图，`--demo` 启动可交互图鉴。 |
| [ui_visual_review.mjs](ui_visual_review.mjs) | 多视口页面审查与改前/改后比较。 |
| [capture_ui_pages.mjs](capture_ui_pages.mjs) | 主要页面的桌面/手机截图、溢出、浏览器错误及控件尺寸。 |

### 选择验证范围与输出

- `smoke_settings_ui.mjs`：播放器使用临时 FFmpeg 合成片源，ASS 检查使用合成样式字幕和依赖包字体，覆盖真实 worker、透明合成、手机布局下的全屏画面及字幕像素、兼容切换与关闭清理；`--drafts-only` 限定设置草稿，`--player-only` 限定播放器，`--capture-docs` 更新教程图。手机布局模拟不能代替真机验收。
- `smoke_casting.mjs`：默认验证六个底栏快捷控件、画面右上角工具、竖向音量、倍速/轨道面板及全屏投屏，使用隔离应用与模拟 DLNA 接收端；`--capture-only` 只拍当前界面，配合 `--source 源码快照目录` 拍改前界面。对照图输出到 `output/playwright/casting/before/` 与 `after/`，比较前核对合成媒体 SHA-256；这两个选项不更新正式教程素材。
- `smoke_ai_ui.mjs`：`--demo` 保留演示服务，`--capture-docs` 更新教程图。
- `smoke_tv_airing.mjs`：使用虚构剧集和模拟 API；`--capture-docs` 更新对应教程图。
- `ui_visual_review.mjs`：`--source` 指定源码快照，比较要求相同 fixture 哈希；`--capture-docs` 更新教程图。
- `capture_ui_pages.mjs`：自动启动模拟演示；`--label` 命名输出批次。

共享 Vue 预览组件与虚构数据位于 [fixtures/](fixtures/)。临时截图、报告和图鉴保存在 `output/playwright/`，不提交；教程正式素材位于 `docs/assets/`，更新时同时维护素材来源清单。

## 文档演示与素材

| 工具 | 使用方式 |
| --- | --- |
| [preview_onboarding.py](preview_onboarding.py) | 临时数据库与合成媒体演示，不读取项目 `.env`。 |
| [capture_docs.py](capture_docs.py) | 拍摄向导、字幕、整理和播放器教程。 |
| [capture_docs_diagrams.py](capture_docs_diagrams.py) | 生成 `docs/assets/diagrams/` 中的可编辑业务流程 SVG。 |
| [capture_docs_manifest.py](capture_docs_manifest.py) | 更新 `docs/assets/manifest.json` 中对应素材信息，保留其他拍摄批次及未变化素材的来源。 |

### 演示与拍摄的前后关系

`preview_onboarding.py` 的 `--docs-demo` 增加字幕和整理场景，`--port` 指定端口；进程需在演示期间保持运行。

`capture_docs.py` 连接已经运行的 `--docs-demo` 实例，参数为演示输出的 `/tmp/jzmedia-preview-*` 目录，仅允许连接该隔离实例。除了向导、字幕和整理，还拍摄播放器信息、已有字幕轨选择，以及 1080p 合成片源降至 720p 的示例；播放素材不作为真实影片兼容性证据。

`docs/scripts/` 负责全仓格式 lint、帮助站构建后的链接检查、Mermaid 缓存、预览服务及发行包；[docs/package.json](../docs/package.json) 提供统一命令。生成教程素材的完整顺序见[文档维护](../docs/developer/documentation.md)。

## 现有库的只读诊断

这些工具直接读取配置指定的数据库或存储，不需要 HTTP 服务运行。先设置正确的 `DATA_DIR` 和 `MEDIA_ROOT`；SMB 库诊断可能访问 NAS。

| 工具 | 范围 |
| --- | --- |
| [find_subs.py](find_subs.py) | 从电影探测缓存查找字幕、字体附件及本地同名外挂字幕；`--limit` 限量，`--json` 输出数据，不调用 ffprobe。 |
| [tv_parse_report.py](tv_parse_report.py) | 使用扫描器规则预演 TV 解析结果；支持 `--library`、`--media-library`、`--samples`、`--json`。 |
| [tv_structure_report.py](tv_structure_report.py) | 输出 TV 目录整理预览及元数据待办；支持 `--library`、`--media-library`、`--json`。 |

### SMB 性能基准

[spike_smb_direct.py](spike_smb_direct.py) 保留为独立 SMB 性能基准：测量顺序读、并发读、随机定位和 FFmpeg Range 链路。`--selftest` 使用临时本地样本；真实共享通过 `--url` 和 `SPIKE_SMB_*` 凭据指定，默认只读。

只有显式 `--write-test` 才会在指定测试子目录执行写入、改名和删除。它使用独立实验后端，不能代替生产 `app/storage/` 的回归测试。

## 维护约定

根目录保留稳定的工具入口，跨脚本共享夹具归入 `fixtures/`。后端测试位于 `tests/`，前端测试位于 `frontend/tests/`，帮助站和 Android TV 各自维护测试入口。

已执行且绑定特定本地库、剧集 ID 或历史批次的一次性修复脚本不保留可执行副本；历史实现通过 `git log -- scripts/` 和 `git show <提交>:scripts/<文件名>` 查询。新的通用维护工具需要明确输入、作用域和输出，避免把本地运行记录混入源码或公开帮助站。
