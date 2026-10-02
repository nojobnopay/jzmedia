---
version: 0.19.0
reviewed: 2026-10-02
---

# 文档制作与发布

帮助站点将任务教程、图片、视频和图表构建为 HTML，随应用在 `/help/` 提供；API 调试文档保持 `/docs`。正文使用 Markdown 与少量受控组件，一处编辑即可生成应用内帮助和离线发布包。

## 内容与导航

普通用户先看 `docs/index.md` 与 `docs/user-guide/` 的任务页；安装、配置和备份位于 `docs/getting-started/`；技术约束位于 `docs/developer/`。计划保留在仓库 `docs/roadmap/`，私有记录保留在 `docs/private/`，两者均不发布。

每个任务页围绕一个结果，按“目标 → 准备 → 操作步骤 → 完成后检查 → 常见问题 → 下一步”组织。参考表和总导航可按用途简化；关键动作、预期结果和文件影响必须写进正文，不能只出现在截图或视频里。首次配置由[首次入库](../user-guide/onboarding.md)单独维护，其他页面链接该教程。

电影、剧集、播放器旧综合页保留原章节标题作为兼容入口，链接到新任务页。改标题或拆页时保留原锚点，并更新站点导航和应用内帮助目标。站点外的仓库文件使用代码路径说明，不创建无法在发布包解析的相对链接。

每个公开页面包含以下元信息；`reviewed` 表示核对内容的日期，不能仅因构建成功就修改：

```yaml
version: 0.19.0
reviewed: 2026-10-02
```

## 图文与演示组件

组件在主题中全局注册。资源路径相对当前 Markdown 文件解析；文件名使用稳定的场景名，不把时间或影片数据库 ID 当成公开资源名。

- `DocStep` 使用 `number`、`title`，文字放默认 slot，图放 `#image` slot；桌面并排，手机按顺序排列。标签前后保留空行，让 Markdown 正常解析。
- `DocFigure` 使用 `src`、具体描述的 `alt`、`caption`，支持查看原图；`marks` 可按图片百分比坐标标注控件，必须核对实际截图。不要让图注承担唯一操作说明。
- `DocDiagram` 与图片使用相同字段。适合两层媒体库关系、扫描与整理区别、系统架构等。
- `DocVideo` 使用 `src`、`poster`、`captions`、`title`；slot 放等价文字步骤。视频手动播放，必须有 WebVTT 字幕和封面。

先用截图定位控件，再用短句说明点击后的结果。连续交互使用短视频，选路和关系使用图表；命令、参数与详细限制仍以可检索和复制的文字呈现。图上需要标注时保留可修改源，避免只存不可编辑的合成图。

资源目录为仓库 `docs/assets/screenshots/`、`docs/assets/videos/`、`docs/assets/diagrams/`；新手图位于 `docs/assets/previews/onboarding/`。拍摄来源、版本、视口、更新日期和对应教程记录在仓库 `docs/assets/README.md`。历史真实库截图与新隔离演示截图分别标明，不将移动模拟当作真机测试。

## 隔离演示

需要亲自核对新手流程时，在项目根运行：

```bash
.venv/bin/python scripts/preview_onboarding.py
```

然后访问 `http://localhost:18080/`；端口占用可加 `--port 18081`。每次运行在 `/tmp/jzmedia-preview-*` 创建独立数据库、媒体目录和前端产物，不读取项目 `.env`，也不修改主实例的 `frontend/dist`。需已安装项目依赖、前端依赖与 FFmpeg。

演示目录自带六秒合成短片和本地 NFO，临时副本禁用 TMDB 电影与剧集搜索。按“开始配置 → 稍后配置 TMDB → 检查并使用此视频库 → 扫描此视频库 → 查看入库结果”完成导入。使用预设的“隔离演示 · 临时数据”库；手动添加真实目录后，应用对该目录的操作仍然会生效。

`Ctrl+C` 停止实例，临时目录保留；再次运行创建新的空库。录制整理和字幕演示同样使用隔离数据与合成片源，真实媒体库不能作为自动写入操作的拍摄对象。

## 重新拍摄素材

文档录屏使用专门的演示数据。先启动全新实例并保持运行：

```bash
.venv/bin/python scripts/preview_onboarding.py --port 18128 --docs-demo
```

在另一个终端中，使用已安装 Playwright 和 Chromium 的 Python 环境，将命令中的目录替换为启动时打印的新临时目录：

```bash
python -m playwright install chromium
python scripts/capture_docs.py /tmp/jzmedia-preview-实际目录
.venv/bin/python scripts/capture_docs_diagrams.py
```

拍摄脚本真实操作隔离实例，生成新手、字幕、整理三段视频及对应截图；从系统 PATH 或项目静态 FFmpeg 依赖中寻找 FFmpeg/ffprobe。需先安装前端依赖。完整重拍必须使用全新 `--docs-demo` 实例；`--only subtitles` 或 `--only organizing` 用于已经完成入库、尚未整理的演示重拍。

仓库 `docs/assets/manifest.json` 保存素材关联信息、时长、体积与校验值，`docs/assets/README.md` 记录基线。拍摄成功后自动更新清单，也可单独运行 `scripts/capture_docs_manifest.py`。三段视频保留真实操作时长、无旁白，中文说明由 WebVTT 字幕提供。用户概念图由 `scripts/capture_docs_diagrams.py` 生成，图表源可维护，不依赖截图中的文字。

## 构建与检查

在项目根安装依赖并运行：

```bash
npm ci --prefix docs
npm --prefix docs run dev
npm --prefix docs run check
npm --prefix docs run test
npm --prefix docs run build
npm --prefix docs run preview
```

开发预览和发布产物只使用本地资源。构建产物位于 `docs/.vitepress/dist/`；主题与导航配置在 `docs/.vitepress/`。检查脚本和浏览器检查位于 `docs/scripts/`，已有仓库 Markdown 链接检查仍可辅助检查源文件。

开发参考中的 Mermaid 保留在正文中。修改图定义后运行 `npm --prefix docs run diagrams` 生成并提交 `docs/.vitepress/diagrams/` 中的 SVG；首次生成需要安装 Playwright Chromium。普通文档构建只核验生成图存在，不需要浏览器和联网渲染。

浏览器检查支持已运行的应用，也可以自行启用临时静态服务：

```bash
npm --prefix docs run test:browser -- --url http://127.0.0.1:8080/help/
npm --prefix docs run test:browser
```

验证重点是：首页可进入首次入库、搜索可定位教程、旧入口和锚点可达、资源加载成功、窄屏可读、键盘能放大与关闭图片、视频字幕可选、关闭 JavaScript 后仍能读到正文及关键步骤。检查构建清单和发布包，确保私有目录、计划、运行数据与凭据未被复制。

## 离线发布包

```bash
npm --prefix docs run package
python3 -m http.server 8090 --directory docs/.artifacts/jzmedia-help
```

访问 `http://127.0.0.1:8090/help/`。发布包位于 `docs/.artifacts/jzmedia-help/`，其中 `help/` 与应用内静态产物一致，根 `index.html` 提供跳转。将整个目录复制到另一台机器后以本地 HTTP 服务打开即可；不依赖互联网或应用数据库。

功能界面变更时同时核对关联教程、截图和视频，更新真正失效的素材；未涉及内容不必整批重拍。发布前记录运行过的检查与未覆盖的设备，不用模拟截图代替真机验收。
