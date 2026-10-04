---
version: 0.20.2
reviewed: 2026-10-04
search: false
---

# 文档导航

[打开图文帮助首页](index.md)，从当前要完成的任务开始。应用内入口为“帮助”；已安装实例的帮助站点位于 `/help/`，API 调试文档仍在 `/docs`。

[在线图文帮助](https://nojobnopay.github.io/jzmedia/)展示最新正式版本的文档，无需启动应用。查阅已安装版本时使用应用内帮助。

按读者查阅：[安装与维护](getting-started/README.md) · [用户任务目录](user-guide/README.md) · [开发者参考](developer/README.md)。

## 文档基线与维护

这些目录中的 Markdown 是应用内帮助、在线帮助与离线包共用的正文源，修改正文后重新构建即可；生成的 HTML 不单独维护。每篇的适用版本与核对日期记录在页首元信息中，与站点显示的构建版本分别维护。

内容归属、兼容入口、素材与发布检查统一见[文档制作与发布](developer/documentation.md)。开发计划、完成状态与待验收事项保留在仓库 `docs/roadmap/`，不进入帮助站；代码约束以仓库根 `AGENTS.md` 为准。
