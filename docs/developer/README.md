---
version: 0.20.0
reviewed: 2026-10-04
search: false
---

# 开发者文档

面向要修改 jzmedia 的开发人员。文档以当前代码为事实源，解释模块边界、关键约束和验证方式；用户操作指南单列在[用户手册](../user-guide/README.md)，数据库版本与迁移见[数据模型](data.md)。

Android TV 是独立 Gradle 工程，日常构建与协议分别见仓库 `android-tv/README.md` 和 `android-tv/docs/protocol.md`；Docker 与 APK 的同步交付使用[统一发布](releasing.md)。

| 章节 | 内容 |
|---|---|
| [系统架构](architecture.md) | 请求与后台任务怎样穿过前端、API、存储和播放层 |
| [模块划分](modules.md) | 前后端目录、职责与扩展点 |
| [数据与迁移](data.md) | 核心表、FTS、迁移、配置及索引一致性 |
| [存储和文件操作](storage.md) | 本地/SMB/NFS 分流、路径作用域、原子操作与整理审计 |
| [扫描与元数据](metadata.md) | 分类、匹配、离线来源、NFO 和图片落盘 |
| [播放设计](playback.md) | 能力决策、FFmpeg/HLS、字幕、缓存、会话生命周期 |
| [API 与任务](api.md) | 路由、鉴权、异步任务与响应约定 |
| [开发与验证](development.md) | 环境、测试、变更步骤和排障入口 |
| [同步发布 Docker 镜像与 APK](releasing.md) | 统一版本、同源构建、签名与产物追溯 |
| [界面规范与验收](design-system.md) | 设计变量、共享控件、组件目录和隔离浏览器检查 |
| [文档制作与发布](documentation.md) | 任务教程、媒体素材、站点构建与验证 |

开发计划、完成状态与待验收事项保留在仓库 `docs/roadmap/`，不进入帮助发布包；现有接口以本目录参考为准。贡献前检查仓库根目录 `AGENTS.md`。[返回文档导航](../README.md)。
