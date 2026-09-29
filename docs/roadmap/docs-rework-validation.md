# 文档改版验收记录

- 日期：2026-09-28；代码基线 `86a7143`（应用 `0.19.0`，schema `28`）；任务书 `DOCS_REWORK_BRIEF.md`（基线一致，实施中无新增功能提交）。
- 验证环境：宿主 `./start.sh`（`./data`/`./media`），`http://localhost:8080`，`/api/health` 全部 ok（DB schema 28、ffmpeg/ffprobe 可用、转码后端 nvenc、构建 `86a7143`）。媒体库：`media`（本地）与 `NAS`（SMB 直读）；播放截图产生的观看进度已获用户允许。

## 改动清单

- 根 `README.md`：按任务书 §4 七段重写，主图 S01 + 详情/剧集/播放器三图 + Mermaid 原理图。
- 新增图片 14 张（`docs/assets/screenshots/`，合计约 2.6 MB；桌面 1440 CSS @2x、移动 390 CSS @3x，WebP q90、主墙图 q85，单张均 <500 KB）与 `docs/assets/README.md` 来源记录。2026-09-29 按清晰度要求全部重拍一遍（2x/3x + q90），字体已逐张验看。
- 用户手册 10 页：入口更新为当前名称（媒体库连接/扫描与整理/资料来源），电影三操作分离（重新匹配/更新资料/修复资料文件），元数据双案例，剧集归属实例、整理前后目录树、设置作用范围表。
- 开发者 7 页：schema 28（含归属两表与分集新列）、ER 图、匹配流程图、播放决策图与 `media_start` 数值例、存储读写路径图、API 实例、架构端到端流程、目录归属服务。
- 入门 3 页 + 导航 2 页：部署入口名、选择表、任务路径表、隔离恢复演练。
- 路线图 `backlog.md`：逐项核对代码，已完成项退出待办（文档链接检查脚本落地）；其余仍为候选/待验收。
- 新增 `scripts/check_docs_links.py`（本地链接/图片/锚点检查）。

## 操作核验（§10.3 六条）

| 路径 | 结果 |
|---|---|
| 推荐部署 → 首页 | `docker compose -f docker-compose.yml up --build -d` 与 `./start.sh` 命令已核命令与目录说明；健康检查字段已列；宿主直跑验证通过（本环境即此方式） |
| 建库 → 扫描 → 核对 → 首播 | 演示实例全流程实跑：建库、扫描（3 文件，电影 TMDB 匹配、剧集入库）、核对、本地片直接播放（readyState 4、1920 宽） |
| 电影错配 → 重新匹配 → 文件修复 | 按钮名与代码一致（Detail.vue 重新匹配/更新资料/修复资料文件）；修复分支为只读说明，未在真实库执行写操作 |
| 剧集归属 → 季号预览 → 整理预览 | 演示实例真实预览：两目录同集 S01E01 报集号重叠冲突，需勾选保留全部版本；确认仅改 DB/缓存（截图 S08） |
| 播放 → 音轨/字幕/画质 → 诊断 | 真实播放开设置面板截图；EAC3/AC3 默认转 AAC、HDR/DV 条件与代码一致；诊断入口为文字说明 |
| 备份 → 隔离目录恢复 | 命令为文档示例，未实际执行恢复（避免动线上数据）；WAL 单文件热拷贝风险已在正文说明 |

真正执行的命令：`git diff --check`（通过）、`scripts/check_docs_links.py`（34 页 0 问题，2026-09-29 终检时为 51 个文件 0 问题）、`pyflakes scripts/check_docs_links.py`（干净）。未为文案调整重跑整套 pytest；新增脚本已验证。

## 2026-09-29 终检

按 §10 复查发现并修复的问题：

1. `developer/metadata.md` 匹配流程图围栏粘连在段落行尾，渲染失败 → 拆独立成行；并补齐电影/剧集管线分开叙述与分集绑定三档回退说明。
2. `user-guide/metadata.md` 案例二混入英文残句 "symptom" → 改为中文表述。
3. `developer/metadata.md` 歧义案例混入英文残句 "repairs" → 改写。
4. `assets/screenshots/file-browser.webp` 与 `scan-review.webp` 字节相同（首版误存同图，违反"不复制同内容文件"）→ 重拍「文件管理」标签页实际画面（2880×1800 @2x，WebP q90，109 KB），图注与画面一致。
5. `assets/README.md` 修正过时记录：`上传演示` 残留测试库说明（实际仍在拍摄实例中）、"临时第二电影库已删"改为如实描述、"scripts/ 下截图脚本"指向不存在的脚本 → 改为实际复现方式。
6. `check_docs_links.py` 增加代码围栏独立成行检查（此前盲区）。

终检命令：`scripts/check_docs_links.py`（51 个文件 0 问题）、`pyflakes scripts/check_docs_links.py`（干净）、`git diff --check`（通过）、全部截图与运行实例逐张比对一致。

## 未覆盖与缺口（需后续补充）

- 移动端截图：S11 已补（`mobile-library.webp`/`mobile-player.webp`，红米 K50 Ultra 参数 407×904 CSS @3x + touch，无外框装饰；真机 Safari/手势/字体渲染仍未测，`acceptance.md` 手机真机行保持未测）。
- 上传弹窗局部图：已补（`upload-movie.webp`/`upload-tv.webp`，本地测试库选定未上传；`上传演示` 测试库仍留在拍摄实例中，见 `assets/README.md` 说明）。
- Safari/Windows 字体/NAS 上传真实网络：沿用 `acceptance.md` 未测声明，本次未新增实测。
- 演示实例（`:8081`、临时库、2 秒合成片）已停止并可删除（`/tmp/opencode/docshot/`）；真实库未被修改（整理/删除/换绑只发生在演示实例）。
- `library-connection.webp` 保留局域网 NAS 名 `\\Joey-DS425\video`（理解 SMB 地址栏必需，无凭据与公网地址）。
