# 仓库维护工具

核对日期：2026-10-03。

以下命令均从仓库根目录执行。Python 工具使用 `.venv/bin/python scripts/文件名.py`，Node 工具使用 `node scripts/文件名.mjs`。安装与常规检查见[开发指南](../docs/developer/development.md)，帮助站工具另见[文档维护](../docs/developer/documentation.md)。

## 构建与检查

| 工具 | 用途与输出 |
| --- | --- |
| [build_frontend.py](build_frontend.py) | 按输入摘要构建 `frontend/dist/`；由 `start.sh` 调用，包含删除检测。 |
| [build_docs.py](build_docs.py) | 按输入摘要构建 `docs/.vitepress/dist/`；由 `start.sh` 调用，排除私有资料、路线图和历史报告。 |
| [build_design.py](build_design.py) | 从 `design/` 母版生成跨端资产、令牌和用途清单；`--check` 只校验，品牌位图使用 `--render-brand`。详见[设计资产](../design/README.md)。 |
| [check_docs_links.py](check_docs_links.py) | 检查仓库 Markdown 的本地链接、锚点和代码围栏；`--root docs` 可限定范围。默认跳过依赖、构建产物、运行数据和私有目录。帮助站渲染结果另用 `npm --prefix docs run check`。 |

## 隔离冒烟与界面审查

这些工具创建临时数据或使用模拟 API；常规运行不需要启动项目的真实服务。浏览器工具使用 `docs/` 的 Playwright 和 `frontend/` 的 Vue/Vite 依赖。

| 工具 | 验证内容 |
| --- | --- |
| [smoke_api.py](smoke_api.py) | 临时后端的扫描、整理、恢复、合集、设置和鉴权等 API；[_smoke_app.py](_smoke_app.py) 是其内部启动器。 |
| [smoke_multi_library.py](smoke_multi_library.py) | 临时双库的扫描、搜索、版本隔离，以及删库保留文件。 |
| [smoke_metadata_offline.py](smoke_metadata_offline.py) | 本地索引、NFO 回放、离线搜索和合成 IMDb 数据导入。 |
| [smoke_settings_ui.mjs](smoke_settings_ui.mjs) | 设置草稿离开保护、文件操作与预览；播放器使用临时 FFmpeg 合成片源。`--drafts-only` 限定设置草稿验证，`--player-only` 限定播放器验证，`--capture-docs` 更新教程截图。 |
| [smoke_ai_ui.mjs](smoke_ai_ui.mjs) | 智能设置、搜索和匹配，模型及 API 均为模拟；`--demo` 保留演示服务，`--capture-docs` 更新教程截图。 |
| [smoke_browse_filters.mjs](smoke_browse_filters.mjs) | 电影/剧集筛选的真实 Vue 页面与模拟 API。 |
| [smoke_tv_airing.mjs](smoke_tv_airing.mjs) | 剧集播出/收藏、官方分集目录、当前媒体库来源、每周推荐与维护状态的真实 Vue 页面检查；使用虚构剧集和模拟 API，`--capture-docs` 更新对应教程截图。 |
| [smoke_design_system.mjs](smoke_design_system.mjs) | 组件和资产目录；`--capture` 保存截图，`--demo` 启动可交互图鉴。 |
| [ui_visual_review.mjs](ui_visual_review.mjs) | 多视口页面审查与改前/改后比较；`--source` 指定源码快照，`--capture-docs` 更新教程图。比较要求相同 fixture 哈希。 |
| [capture_ui_pages.mjs](capture_ui_pages.mjs) | 自动启动模拟演示，截取桌面/手机主要页面，并检查溢出、浏览器错误及控件尺寸；`--label` 命名输出批次。 |

共享 Vue 预览组件与虚构数据位于 [fixtures/](fixtures/)。临时截图、报告和图鉴保存在 `output/playwright/`，不提交；教程正式素材位于 `docs/assets/`，更新时同时维护素材来源清单。

## 文档演示与素材

| 工具 | 使用方式 |
| --- | --- |
| [preview_onboarding.py](preview_onboarding.py) | 启动临时数据库与合成媒体演示，不读取项目 `.env`。`--docs-demo` 增加字幕和整理场景，`--port` 指定端口；进程需在演示期间保持运行。 |
| [capture_docs.py](capture_docs.py) | 需要上一项已运行的 `--docs-demo` 实例；参数为其输出的 `/tmp/jzmedia-preview-*` 目录，仅允许连接该隔离实例。拍摄向导、字幕及整理教程，并补充播放器信息、已有字幕轨选择和 1080p 合成片源降至 720p 的界面示例；播放素材不作为真实影片兼容性证据。 |
| [capture_docs_diagrams.py](capture_docs_diagrams.py) | 生成 `docs/assets/diagrams/` 中的可编辑业务流程 SVG。 |
| [capture_docs_manifest.py](capture_docs_manifest.py) | 更新 `docs/assets/manifest.json` 中对应素材信息，保留其他拍摄批次及未变化素材的来源。 |

`docs/scripts/` 负责帮助站构建后的链接检查、Mermaid 缓存、预览服务及发行包；[docs/package.json](../docs/package.json) 提供统一命令。生成教程素材的完整顺序见[文档维护](../docs/developer/documentation.md)。

## 现有库的只读诊断

这些工具直接读取配置指定的数据库或存储，不需要 HTTP 服务运行。先设置正确的 `DATA_DIR` 和 `MEDIA_ROOT`；SMB 库诊断可能访问 NAS。

| 工具 | 范围 |
| --- | --- |
| [find_subs.py](find_subs.py) | 从电影探测缓存查找字幕、字体附件及本地同名外挂字幕；`--limit` 限量，`--json` 输出数据，不调用 ffprobe。 |
| [tv_parse_report.py](tv_parse_report.py) | 使用扫描器规则预演 TV 解析结果；支持 `--library`、`--media-library`、`--samples`、`--json`。 |
| [tv_structure_report.py](tv_structure_report.py) | 输出 TV 目录整理预览及元数据待办；支持 `--library`、`--media-library`、`--json`。 |

[spike_smb_direct.py](spike_smb_direct.py) 保留为独立 SMB 性能基准：测量顺序读、并发读、随机定位和 FFmpeg Range 链路。`--selftest` 使用临时本地样本；真实共享通过 `--url` 和 `SPIKE_SMB_*` 凭据指定，默认只读。只有显式 `--write-test` 才会在指定测试子目录执行写入、改名和删除。它使用独立实验后端，不能代替生产 `app/storage/` 的回归测试。

## 维护约定

根目录保留稳定的工具入口，跨脚本共享夹具归入 `fixtures/`。后端测试位于 `tests/`，前端测试位于 `frontend/tests/`，帮助站和 Android TV 各自维护测试入口。

已执行且绑定特定本地库、剧集 ID 或历史批次的一次性修复脚本不保留可执行副本；历史实现通过 `git log -- scripts/` 和 `git show <提交>:scripts/<文件名>` 查询。新的通用维护工具需要明确输入、作用域和输出，避免把本地运行记录混入源码或公开帮助站。
