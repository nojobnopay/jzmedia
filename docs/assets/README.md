# 文档图片资源

截图统一存 `docs/assets/screenshots/`，正文用仓库相对路径引用（如用户手册页用 `../assets/screenshots/xxx.webp`）。同一画面不复制多份。

## 拍摄基线

- 日期：2026-09-28；代码 `86a7143`（应用 `0.19.0`，schema `28`）。2026-09-29 终检重拍：全部 14 张（2x/3x + q90），`file-browser.webp` 重拍为「文件管理」标签页实际画面（首版误存为扫描面板同图）。
- 视口：桌面 1440×900 CSS（2x 像素密度，2880×1800 像素），移动按红米 K50 Ultra（407×904 CSS，3x，1221×2712 像素）；浏览器 100% 缩放，深色主题默认。Playwright 无头 Chromium 全视口截图，不含浏览器地址栏。WebP q90（主墙海报图 q85，440 KB，单图均 <500 KB）。
- S01–S06、S09–S10 取自主实例 `http://localhost:8080`（`./start.sh` 启动）的真实媒体库：`media`（本地）与 `NAS`（SMB 直读）。未执行整理、删除、换绑或重扫；播放截图产生的观看进度已获允许。
- S07、S08 取自隔离演示实例 `http://127.0.0.1:8081`（临时 `DATA_DIR`、临时媒体目录，2 秒合成测试片，TMDB 正常匹配；拍完即弃，不影响真实库）。正文引用这两张图时须注明演示环境。
- 截图中可见的 `media · 上传演示` 视频库是维护者本地保留的空测试库（0 片 0 集，用于演示上传多目标选择器）；非公共部署会出现的内容，仅作说明，不入文档正文。

| 文件 | 画面 | 拍摄入口 | 来源 |
|---|---|---|---|
| `movie-library.webp` | 电影墙、继续观看、排序 | `/?media=2` | 真实库 NAS |
| `movie-detail.webp` | 电影背景、海报、播放入口 | `/m/875?media=2`（古董局中局） | 真实库 NAS |
| `tv-season.webp` | 亮剑第 1 季分集与播放入口 | `/tv/72/s/1?media=2` | 真实库 NAS |
| `player-settings.webp` | 播放画面、进度条、设置面板 | `/m/405?media=1` 点播放再开齿轮 | 真实库本地 |
| `library-connection.webp` | 媒体库与其下视频库 | `/settings?sec=sec-libraries` | 真实库 |
| `scan-review.webp` | 视频库标签、入库三步 | `/settings?sec=sec-libtools&library=2` | 真实库 |
| `organize-preview.webp` | 就地整理预览与目标路径 | 演示实例同页（`library=2`） | 演示环境 |
| `tv-bindings-preview.webp` | 归属变更预览与集号重叠冲突 | 演示实例剧详情归属弹窗 | 演示环境 |
| `file-browser.webp` | 文件管理、目录与操作入口 | `?sec=sec-libtools&library=2` → 「文件管理」标签 | 真实库 |
| `upload-movie.webp` | 上传影片目标库、文件与开始上传 | `/?media=1` 添加影片 → 上传文件（临时文件仅选定未上传；目标选中 `上传演示` 测试库） | 真实库（本地） |
| `upload-tv.webp` | 上传剧集多选文件模式与季号 | `/tv?media=1` 添加剧集 → 上传文件 | 真实库（本地测试库，未实际上传） |
| `settings-navigation.webp` | 七个分区与概览内容 | `/settings` | 真实库 |
| `mobile-library.webp` | 手机宽度下的导航、海报和观看入口 | `/?media=2`（红米 K50 Ultra：407×904 CSS，3x） | 真实库，模拟环境 |
| `mobile-player.webp` | 手机宽度下的播放控件 | `/m/405?media=1` 播放（同上） | 真实库，模拟环境 |

## 注意事项

- `library-connection.webp` 保留了局域网 NAS 名（`\\Joey-DS425\video`），理解 SMB 地址栏需要该上下文；无凭据、无公网地址。重拍时沿用同一原则。
- 移动端两张为 Chromium 移动模拟（390×844、mobile UA、touch），无手机外框装饰；真机未验证，见 [验收记录](../roadmap/backlog.md#文档改版验收记录)。
- 原理图优先用正文内嵌 Mermaid，不存静态图；若渲染器不支持 Mermaid，再导出静态并保留源定义。代码围栏必须独立成行（`scripts/check_docs_links.py` 会检查）。
- 复现：主实例 `./start.sh` 启动后，用 Playwright 无头 Chromium 按上表「拍摄入口」逐张重拍（视口/密度/质量见拍摄基线）；截图脚本属本地临时工具，未随仓库提交。演示实例（S07/S08）为临时 `DATA_DIR` + 2 秒合成测试片，拍完即弃，详见 [验收记录](../roadmap/backlog.md#文档改版验收记录)。
