---
layout: home
markdownStyles: false
version: 0.19.0
reviewed: 2026-10-03
hero:
  name: jzmedia 帮助中心
  text: 从第一部影片开始
  tagline: 跟着图解连接片库、添加内容和播放；需要时再学习匹配、整理与维护。
  actions:
    - theme: brand
      text: 添加第一部影片
      link: /user-guide/onboarding.html
    - theme: alt
      text: 安装 jzmedia
      link: /getting-started/deployment.html
features:
  - title: 还没安装
    details: 在家用电脑或 NAS 上安装，检查服务，再开始添加自己的媒体。
    link: /getting-started/deployment.html
  - title: 添加第一部影片
    details: 已有可用实例，跟着四步图解连接片库、添加内容并第一次播放。
    link: /user-guide/onboarding.html
  - title: 日常观看与管理
    details: 找片、追剧、调字幕，或按任务查扫描、匹配、上传和整理。
    link: /user-guide/README.html
  - title: 遇到问题
    details: 影片看不到、资料匹配错、播放无声或字幕不对，从现象开始检查。
    link: /user-guide/troubleshooting.html
---

<div class="docs-home-content">

## 常用操作

[找电影](user-guide/find-movies.md) · [追剧与连播](user-guide/watch-tv.md) · [音轨与字幕](user-guide/subtitles.md) · [扫描与匹配](user-guide/metadata.md) · [整理目录](user-guide/organizing.md)

## 先看到结果，再按需深入

<DocFigure src="./assets/screenshots/movie-library.webp" alt="电影海报墙中显示继续观看与电影海报" caption="连接自己的本地目录或 NAS，扫描后即可按海报找片；截图为当前界面的隔离演示，使用虚构媒体与原创海报。" />

jzmedia 管理你已经拥有的媒体文件，不提供下载源。首次配置完成后，能够找到内容并播放就已经可以开始使用；目录整理可稍后单独预览与确认。

## 一次入库，四个独立动作

<DocDiagram src="./assets/diagrams/ingestion.svg" alt="扫描发现文件，匹配确定作品，按策略写资料，整理需要另行确认" caption="扫描、资料匹配与移动文件有不同影响，先完成首次试播，再决定是否整理。" />

[完整任务目录](user-guide/README.md) · [配置参考](getting-started/configuration.md) · [开发者文档](developer/README.md)

</div>
