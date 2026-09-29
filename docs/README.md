# 文档导航

文档按读者和任务分成四个独立目录。每个目录都有自己的入口、阅读顺序和上下文；不必先阅读开发记录才能使用软件。

| 目录 | 读者 | 覆盖内容 |
|---|---|---|
| [getting-started](getting-started/README.md) | 普通用户、部署者 | 项目定位、安装、配置、首次入库、升级备份和部署排障 |
| [user-guide](user-guide/README.md) | 深度使用者 | 按功能讲解入口、步骤、结果、限制和常见误区 |
| [developer](developer/README.md) | 开发人员 | 系统结构、模块、数据模型、存储、扫描、播放、接口与开发验证 |
| [roadmap](roadmap/README.md) | 开发人员、维护者 | 当前计划、优先级建议、验收条件、完成记录和历史索引 |

普通用户从部署教程开始；已安装用户直接查用户手册；修改代码前先看开发者文档。开发计划描述未来工作，不代表当前已经支持。

| 你现在想做什么 | 最短路径 |
|---|---|
| 第一次安装 | [入门与部署](getting-started/README.md)：准备条件 → 安装 → 首次入库 |
| 已经在用，想完成某个操作 | [用户手册](user-guide/README.md)：按任务找入口、步骤与结果 |
| 出了问题 | 先看[功能排障](user-guide/troubleshooting.md)，部署类问题看[部署排障](getting-started/troubleshooting.md) |
| 参与开发 | [开发者文档](developer/README.md)，再看[开发计划](roadmap/README.md) 确认范围 |

## 文档基线与维护

- 核对日期：2026-09-28；应用版本 `0.19.0`，数据库 schema `28`（含剧集目录归属 `tv_directory_bindings` 与归属历史 `tv_binding_history`，及分集 `match_source`/`binding_conflict`）。
- 界面入口以当前 Vue 组件为准，接口参数以运行实例的 `/docs` 和后端模型为准。
- 部署说明不包含私有环境地址、真实媒体清单或凭据。`docs/private/` 是本地资料，不属于公共文档。
- 已完成能力写入手册/设计；未完成事项集中在开发计划。旧 [BACKLOG](BACKLOG.md) 仅保留兼容入口。
- 修改功能时同步对应文档，并检查相对链接。开发约束仍以仓库根目录的 [AGENTS.md](../AGENTS.md) 为准。

[返回项目首页](../README.md)
