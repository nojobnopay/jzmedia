---
version: 0.19.0
reviewed: 2026-10-02
---

# 安装与维护

<span id="项目简介与部署教程"></span>

jzmedia 把已有电影和剧集目录变成浏览器里的海报墙与播放器。它不提供影片下载源；安装后首次入库请跟随[图文向导](../user-guide/onboarding.md)。

## 运行前准备

- 能运行 Docker Compose 的家用电脑或 NAS；直接运行源码则需 Linux/WSL、Python、Node.js 与 FFmpeg。
- 一个可读取的媒体目录和一个可写的数据目录。仅体验浏览时可把媒体库设为只读。
- 可访问服务器的浏览器。转码能力取决于片源、CPU/GPU 和网络。
- 元数据可以来自既有 NFO、本地缓存或网络来源。没有 TMDB 凭据也能使用已有资料，但不保证自动匹配所有影片。

<span id="阅读顺序"></span>

## 选择当前任务

| 当前任务 | 教程 |
|---|---|
| 第一次安装到电脑或 NAS | [安装与首次使用](deployment.md) |
| 服务已启动，准备添加内容 | [首次入库与试播](../user-guide/onboarding.md) |
| 修改路径、凭据、代理或转码 | [配置参考](configuration.md) |
| 升级、备份、恢复或换机 | [升级、备份与迁移](operations.md) |
| 启动失败、权限或网络异常 | [部署排障](troubleshooting.md) |

目标是家庭或受信任网络中的单实例部署。可选写操作令牌不等于用户账号或完整公网访问控制。仓库当前没有声明公开发布地址、镜像仓库或许可证，分发安排由维护者确定。

[帮助首页](../index.md) · [日常操作](../user-guide/README.md) · [开发与验证](../developer/development.md)
