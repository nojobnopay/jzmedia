# 全仓文档优化计划

核对日期：2026-10-04。全仓排版、格式 lint、构建与浏览器检查已完成；实际新手试读、GitLab/编辑器预览及设备操作仍需各自验证。

采用 **Diátaxis 内容分类 + 统一中文写作规范 + 现有 Markdown/VitePress + Mermaid 图表 + 自动检查**。目标是让读者选对入口、完成任务、查到可信参考，同时让维护者只更新一份事实。

## 当前状态

检查范围现为 58 份源 Markdown，包括根 README、用户与开发文档、Android TV、脚本、设计、代理指令和仓库 skill；帮助站仍公开 40 页。私有记录、依赖和生成物不参与排版或发布范围扩张。

| 工作 | 状态与依据 |
|---|---|
| 统一规范与文档 skill | 已接入 `.agents/skills/jzmedia-docs/SKILL.md`；[制作手册](../developer/documentation.md)是规则唯一来源 |
| 用户与运维排版 | 部署、备份、恢复采用 Step；设置、配置与排障按任务分层 |
| 开发与工具排版 | API、设计系统、脚本和资源索引拆分长表说明，保留已有标题与技术约束 |
| 代理指令与计划 | 按主题展开有效约束，区分当前状态与待验收项 |
| Markdown 与 HTML 步骤 | 两篇图文教程共 7 个步骤改用标准 H3；`DocStep` 只增强布局 |
| 自动检查 | 格式 lint 与源链接共用范围，`verify` 串联构建、测试和产物检查 |

规范、检查命令与组件写法统一维护在[文档制作与发布](../developer/documentation.md)，本页只保留选型依据和剩余验收，避免重复维护已采纳规则。普通页面不强套步骤模板；符合规范的页面无需为了覆盖数量而改写。

## 成熟方案与选型

### 内容组织与写作方法

[Diátaxis](https://diataxis.fr/start-here/)区分教程、操作指南、参考和解释，分别服务于学习、完成任务、查阅事实和理解原理。采用它来判断每页的主要用途；按其[应用建议](https://diataxis.fr/how-to-use-diataxis/)逐页改进，不预建四套目录或为了分类大量搬文件。

[Google 标题规范](https://developers.google.com/style/headings)强调语义层级，不建议给普通章节统一编顺序号；[Microsoft 步骤规范](https://learn.microsoft.com/en-us/style-guide/procedures-instructions/writing-step-by-step-instructions)建议多步操作使用编号，并交代操作位置。项目据此区分章节与步骤，保留已有的长流程 `Step N` 约定，并按中文表达调整标题。

表格用于比较相关信息，长解释和操作移到正文，依据 [Google 表格规范](https://developers.google.com/style/tables)。图表采用 [C4 的架构层次](https://c4model.com/diagrams)和 [W3C 复杂图像指导](https://www.w3.org/WAI/tutorials/images/complex/)：区分读者和范围，提供能说明核心关系的文字。

### 文档站与格式

下表的能力来自官方文档，是否适合迁移是结合本仓实现作出的判断。

| 方案 | 适用优势 | 本项目选择 |
|---|---|---|
| [VitePress 1.x](https://vuejs.github.io/vitepress/v1/guide/markdown) | Markdown 与 Vue 增强，兼容现有主题 | 保留当前锁定版本，优先优化结构与阅读样式 |
| [Docusaurus](https://docusaurus.io/docs/versioning) | 文档版本管理及 React/MDX 生态 | 暂不迁移；需重做 Vue 组件，当前无多版本文档需求 |
| [Material for MkDocs](https://squidfunk.github.io/mkdocs-material/setup/setting-up-navigation/) | 成熟导航及 Markdown/Python 工作流 | 暂不迁移；现有组件和发布检查不能原样沿用 |
| [AsciiDoc](https://docs.asciidoctor.org/asciidoc/latest/sections/numbers/) | 内建章节自动编号，适合结构化长手册 | 将来确需书籍式编号时再评估格式迁移 |

Markdown 足以表达清晰的章节、步骤、代码和表格；字体、留白、目录、图片交互由阅读器决定。仓库 Markdown 应保证内容完整、结构清楚，帮助站承担更丰富的阅读体验。换框架不能自动修复混杂的内容职责。

## 验收与后续维护

### 自动验证

全仓使用锁定版本的 `markdownlint-cli2`；检查标题、空行、列表、围栏语言、表格结构和步骤连续性。首页模板单独声明例外，不以中文行长、固定列数或篇幅作为硬性指标。

运行 `npm --prefix docs run verify`，顺序为格式 lint → 源链接 → 构建 → 测试 → 产物检查。结构和组件改动追加 `npm --prefix docs run test:browser`，验证桌面/手机、步骤目录、搜索、键盘操作和无 JavaScript 正文。源 Markdown 的步骤另与构建 HTML 对照；两种渲染均应保留关键操作。

本轮完整验证与浏览器检查通过，另核对首次入库、部署、API 和制作手册的 1440px/390px 布局。回归覆盖源文件枚举和 lint 的正反例，包括私有目录排除、空范围报错、代码示例不计步骤、标题断号和组件属性误用。公开页面继续由清单登记，计划和私有记录不可进入站点或搜索。

### 实际阅读任务

自动检查不能证明读者已成功完成任务。以下按真实使用反馈验证，不把未运行的教程或设备记为通过：

- **源 Markdown 阅读**：在项目 GitLab 和常用编辑器中查看首次入库、部署与字幕教程，核对标题、代码、表格及组件间的正文；必要时调整具体页面。
- **新手操作**：从部署入口完成首次入库，记录找不到的入口、缺失前提和无法判断成功的步骤。
- **运维操作**：在隔离环境按教程完成备份恢复，确认能定位路径、检查结果和失败恢复入口。
- **开发与电视端**：按文档查到 API/播放约束；电视端构建、测试、安装及设备验收范围继续以 [Android TV 计划](android-tv.md)为准。

帮助站的浏览器布局检查与源 Markdown 解析可自动复现；GitLab/编辑器实际外观、新手试读和真实设备操作分别记录证据，不能互相替代。不以删字数量、增加图表数量或文件数量减少作为成功指标。

### 持续维护

功能变更在同一提交更新对应正文、脚本帮助和失效素材；先确认唯一归属，再决定是否拆页或新增图。已有 Mermaid 和素材流程继续复用，不为排版重做全部图表。

贡献者按 `jzmedia-docs` 执行编写或纯审核流程。`reviewed` 只在实际核对内容后更新；检查结果和一次性实施过程写入提交或 PR，本计划只保留当前状态及仍有价值的待验收项。当前没有 CI，以后接入时复用相同验证命令。

[返回开发计划](README.md)
