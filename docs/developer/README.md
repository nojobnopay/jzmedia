---
search: false
---

# 开发者文档

面向要修改 jzmedia 的开发人员。文档以当前代码为事实源，解释模块边界、关键约束和验证方式；用户操作指南单列在[用户手册](../user-guide/README.md)，数据库版本与迁移见[数据模型](data.md)。

Android TV 是独立 Gradle 工程，可单独进行[日常构建（GitHub）](https://github.com/nojobnopay/jzmedia/blob/main/android-tv/README.md#从源码构建测试与安装)，协议见[客户端协议（GitHub）](https://github.com/nojobnopay/jzmedia/blob/main/android-tv/docs/protocol.md)；Docker 与 APK 的同步交付使用[统一发布](releasing.md)，从同一提交构建并共用版本。

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
| [同步发布 Docker 镜像与 APK](releasing.md) | 标签触发云端发布、固定签名、本机构建与产物追溯 |
| [界面规范与验收](design-system.md) | 设计变量、共享控件、组件目录和隔离浏览器检查 |
| [文档制作与发布](documentation.md) | 任务教程、媒体素材、站点构建与验证 |

## 仓库目录

| 目录 | 维护内容 |
|---|---|
| `app/`、`tests/` | FastAPI 服务、SQLite 存储及后端回归测试 |
| `frontend/` | Vue 应用、前端单元测试与构建配置 |
| [android-tv/（GitHub）](https://github.com/nojobnopay/jzmedia/blob/main/android-tv/README.md) | 独立 Android TV 工程、客户端文档与验证工具 |
| [design/（GitHub）](https://github.com/nojobnopay/jzmedia/blob/main/design/README.md) | 跨端图标和品牌母版、语义令牌、资源清单及验收记录 |
| [docs/](../README.md) | 帮助站正文、教程素材、站点工具和测试 |
| [docs/roadmap/（GitHub）](https://github.com/nojobnopay/jzmedia/blob/main/docs/roadmap/README.md) | 开发计划、完成状态与待验收事项，不随公开帮助发布 |
| [scripts/（GitHub）](https://github.com/nojobnopay/jzmedia/blob/main/scripts/README.md) | 构建、文档制作、隔离冒烟与诊断工具 |
| `data/`、`media/`、`output/` | 本机运行数据、用户媒体与验证产物，Git 忽略；私有操作记录放 `docs/private/` |

新增文件按用途归入上述目录。教程图片和录屏连同来源清单保存在 `docs/assets/`，批次来源见仓库 `docs/assets/README.md`；跨端设计资产从 `design/` 母版修改，通过 `scripts/build_design.py` 生成并提交，具体方法见[界面规范](design-system.md)。

## 维护约定

- 构建目录、缓存和临时验证输出不提交；已有验收证据不因可以重新生成就直接清除。
- 完成的一次性修复脚本从工作树删除，通过 Git 历史追溯。阶段性实施与测试总结写入提交或 PR 说明；长期有效的约束维护在对应技术文档，素材来源维护在素材清单。
- 电视主要播放流程与 ASS/SSA 原生渲染已通过真机验证，当前统一发行默认交付 Debug 试装 APK；设备验收依据及正式签名升级限制见[电视端状态与发布（GitHub）](https://github.com/nojobnopay/jzmedia/blob/main/docs/roadmap/android-tv.md)。
- 运行数据、媒体、凭据与 `docs/private/` 不进入文档发布包；私有记录不因被 Git 忽略就视为废弃文件。

现有接口以本目录参考为准。贡献前检查本地工作区根目录 `AGENTS.md`（该文件仅存于本地，不入库）。[返回文档导航](../README.md)。
