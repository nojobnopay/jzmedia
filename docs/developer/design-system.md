---
version: 0.19.0
reviewed: 2026-10-02
search: false
---

# 界面规范与验收

jzmedia 使用深色影音界面：海报与背景图承载内容，红色表示主要操作，中性灰组织管理工具。电影、剧集、合集共享浏览模式；设置和文件管理采用紧凑密度。统一的是同类控件、状态和操作语义，不要求所有页面使用同样的信息密度。

## 样式的职责

- `frontend/src/styles/tokens.css`：表面、文字、边框、状态色、字号、间距、圆角、控件高度和浮层层级。基本色为背景 `#141414`、表面 `#1c1c1c`、较高表面 `#262626`、正文 `#eee`、辅助文字 `#aaa`、主操作 `#e50914`。
- `styles/base.css`：原生表单、全局焦点、主导航、海报播放入口和已有共享卡片。基础选择器保持低权重；导航外观仅作用于 `.app-nav`，不能影响页内标签或面包屑。
- `JzButton`、`JzField`、`JzDialog`、`AppIcon`：控件的外观与行为。跨页面和 Teleport 到 body 后保持同样表现。
- `browse.css`、`mediaPages.css`、`settings.css`、`setup.css`：各类页面布局。组件 scoped 样式只管理自身结构和业务特例。

中文使用本地 PingFang SC / Microsoft YaHei，系统字体作为后备，不依赖外部字体下载。正文与辅助文字区分字号、明度；标题有明确层级，路径和长标题允许换行。圆角按控件、卡片、弹窗区分。常规控件 40px、管理页紧凑控件 36px、手机与触摸控件至少 44px；海报勾选标记等专门交互沿用自身规则。

CSS 变量不能直接用于媒体查询条件；断点使用明确的 `700px` 等字面量，并在相应布局中维护。不要新增高权重祖先选择器来覆盖已有控件主题，应删除被新组件取代的旧声明。

## 组件约定

| 组件 | 用法与边界 |
| --- | --- |
| `JzButton` | `variant="primary / secondary / danger / ghost"`，`size="default / compact"`；默认 `type="button"`。表单提交显式设 `type="submit"`；`loading` 禁止重复点击并播报 busy。单一区域保留明确的主要操作。 |
| `JzField` | 提供 `id`、`label`，可加 `hint` 或 `error`。插槽控件连接 `id`、`aria-describedby` 和 `aria-invalid`；组件不接管业务校验。 |
| `JzDialog` | 提供标题或 `labelledby`；支持 `header`、内容和 `footer` 插槽。`busy` 禁止关闭，`layer` 为 dialog / preview / auth。保留业务二次确认，关闭后返回焦点。复杂播放器和文件预览保留专门生命周期，只复用视觉规则。 |
| `AppIcon` | 通用操作采用 24×24、1.7 线宽 SVG。纯图标按钮设置中文 `aria-label`；播放器继续使用 `PlayerIcon`。 |
| `EmptyState` | loading / error / empty / no-results；失败提供重试或明确下一步，首个请求完成前不能显示“没有内容”。 |
| `BrowseToolbar` | 电影和剧集共享搜索、联想、筛选开关；请求、路由和播放业务留在页面。 |

设置页标题下明确作用范围：全部媒体库、选中的视频库或当前浏览器。用户教程与引导采用同一导航名称，例如“在线资料服务”。

## 组件目录与浏览器验收

在已安装前端和文档依赖、Playwright Chromium 的开发环境运行：

```sh
node scripts/smoke_design_system.mjs
node scripts/smoke_design_system.mjs --demo
node scripts/smoke_design_system.mjs --capture
node scripts/capture_ui_pages.mjs
```

组件目录是独立开发预览，没有接入正式产品导航，不连接应用 API、数据库或媒体。展示按钮、字段、反馈和弹窗状态，截图写入被 Git 忽略的 `output/playwright/`。演示地址由脚本打印，结束后按 Ctrl+C。

`capture_ui_pages.mjs` 自动启动隔离演示，截取电影墙、电影详情、剧集详情、设置和合集的桌面与手机界面，检查页面溢出、浏览器错误和控件尺寸；结果位于 `output/playwright/ui-after/`，可用 `--label 名称` 指定另一组输出目录。

功能验收复用 `scripts/smoke_settings_ui.mjs`、`scripts/smoke_ai_ui.mjs` 的真实 Vue 页面与模拟 API；向导和播放用 `scripts/preview_onboarding.py --docs-demo` 的临时数据库与合成媒体。不能为了截图对真实库执行扫描、整理或删除。

桌面 1440px、手机 390px 是常规截图基线，375px 检查窄屏。覆盖正常、加载、空、搜索无结果、失败、长文本、弹窗以及键盘焦点。检查 Tab 循环、Escape、关闭后焦点返回、任务进行中不可关闭、减少动画偏好。截图使用同一份模拟数据；仓库历史教程图片不能直接当作当前版本基线。

改动后运行前端 `npm test`、`npm run lint`、`npm run build`，再运行受影响的隔离浏览器脚本。教程页面和相关素材按[文档维护流程](documentation.md)同步检查。审查记录保留在 `docs/private/`，不进入公开帮助或搜索索引。
