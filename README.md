---
version: 0.19.0
reviewed: 2026-10-02
---

# jzmedia

把本地硬盘或 NAS 里已有的电影、剧集，变成可以在浏览器里找片、看片的私人影视库。

![电影海报墙与继续观看](docs/assets/screenshots/movie-library.webp)

支持电影与剧集、多版本、合集、资料匹配、字幕、倍速和浏览器播放。连接本地目录或 NAS，扫描后核对资料，即可开始观看；整理目录需单独预览和确认。影片由使用者自行提供，不提供下载源。

<span id="能做什么"></span>
<span id="怎样工作"></span>

## 快速开始

跟随[安装教程](docs/getting-started/deployment.md)部署。服务启动后访问 `http://服务器地址:8080`，点击空库欢迎卡片“开始配置”，按[首次入库图解](docs/user-guide/onboarding.md)完成配置和试播。

已运行的实例在 `/help/` 提供图文帮助，API 调试文档位于 `/docs`。帮助站点可离线随应用使用；仓库中正文与图表源仍可直接阅读。

## 文档

| 当前任务 | 入口 |
|---|---|
| 图文教程与任务导航 | [帮助首页](docs/index.md) · [完整任务目录](docs/user-guide/README.md) |
| 安装、配置、升级与备份 | [部署与维护](docs/getting-started/README.md) |
| 系统架构、API 与开发 | [开发者参考](docs/developer/README.md) |
| 制作文档、截图与演示 | [文档制作与发布](docs/developer/documentation.md) |
| 查看未来计划 | 仓库 `docs/roadmap/`（不随公开帮助发布） |

## 运行边界与参与方式

面向家庭或受信任网络中的单实例部署。可选令牌只保护 API 写操作，浏览、播放和媒体直链仍开放；它不是多用户登录与完整权限系统。转码能力取决于片源、CPU/GPU 和网络。

仓库当前没有 `LICENSE`、公开镜像仓库、下载站或问题追踪平台，对外分发由维护者确定。修改代码前阅读仓库 [AGENTS.md](AGENTS.md) 和开发者参考。运行数据、媒体、凭据与 `docs/private/` 不进入文档发布包。
