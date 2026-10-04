# Android TV 工程约定

本目录继承根目录 `AGENTS.md`。它是同一仓库内可独立开发与测试的 Android 工程，发行时与 Docker 镜像统一版本、统一源码提交并一起构建；当前实现范围见 [README](README.md)，后续工作见 [电视客户端计划](../docs/roadmap/android-tv.md)。

## 工程与接口边界

- 使用 Kotlin 与 Compose for TV；播放阶段使用 Media3/ExoPlayer。Android Studio 直接打开本目录。
- 客户端只通过 HTTP API 访问 jzmedia，不直接读取服务器数据库、媒体目录或 SMB 凭据，不内嵌网页客户端。
- 后端启动、Dockerfile 与网页构建本身不依赖 Android SDK；根发布脚本负责编排 Docker 与 APK 两套构建。工具链版本由本工程锁定。

### 版本与交付

- 以仓库根 `version.properties` 的 `versionName` / `versionCode` 为唯一版本来源，与服务端、网页、帮助站共用发行版本；统一发布标签使用 `vX.Y.Z`。通过根 `scripts/release.py version X.Y.Z --android-code N` 同步版本，不再使用独立的 Android 发行版本或新建 `android-tv-vX.Y.Z` 标签。兼容修复、视觉小调整升 patch，新功能升 minor，破坏性变更升 major；`0.x` 阶段的破坏性变更升 minor 并说明兼容影响。
- 每次向用户交付内容变化的 APK（含 Debug 试装包），都必须同时提升版本号及全局单调递增的 `versionCode`；日常本地重复编译不升版，`versionCode` 不随主次版本重置、不使用日期。版本号不长期附带 `-dev`，Debug 构建只追加 `-debug`。
- 分发使用仓库根 `python3 scripts/release.py build`，从干净同一提交同步构建镜像和 APK，全部成功后交付 `output/releases/vX.Y.Z/`。默认 Debug 试装包；正式 Release 必须完成签名验证，未签名 APK 不进入统一交付。`tools/tv.py package debug|release` 保留为 Android 单独开发打包入口，不能代替统一发行。
- APK 名为 `jzmedia-tv-X.Y.Z-debug.apk`、正式签名的 `jzmedia-tv-X.Y.Z.apk` 或开发用未签名的 `jzmedia-tv-X.Y.Z-unsigned.apk`，不加日期、功能名或 `rebuilt`。统一清单记录完整提交、版本、镜像 ID、APK 哈希及签名类别。
- 日期、提交、哈希、签名、兼容范围和验收状态写入旁附 JSON 或发布说明；同一版本与构建类型不得替换为不同内容或签名的产物，历史 APK 不靠改文件名冒充新版本。

### 兼容与密钥

- 接口联动修改在同次变更中包含兼容说明和对应测试。
- 不假定 APK 与服务端同时升级。开始接入 API 后，明确最低兼容服务端版本；新增播放能力字段必须保留旧网页客户端的默认行为。
- `local.properties`、SDK、Gradle 缓存、构建产物和签名密钥不提交。正式签名密钥在仓库外保管并备份；不得使用 debug 签名发布正式版本。

## 电视交互与播放

- 所有观影操作均可由方向、确定、返回键完成；焦点清晰可见，返回时恢复原条目与列表位置，不依赖触摸、鼠标或长按。
- 使用原生电视控件与焦点机制；网页 Vue 组件不能直接移植。首次连接支持遥控器输入，首版不依赖 Google Play 服务。
- 首版面向家庭局域网，复用全家共享观看进度；扫描、匹配、整理、上传及文件管理保留在网页端。
- 设备能力必须由实际检测和真机验证决定，不能按“Android TV”统一放行 MKV、HEVC、HDR 或音频直通。
- 实现播放前先完成服务端客户端能力与多设备会话隔离。退出、按 Home、待机必须停止声音、保存进度并释放本机资源；关闭本机播放不能中断其他设备。
- 区分 HLS 源时间偏移 `media_start` 与起播位置 `initial_time`；字幕、续播和跳转使用同一源时间口径。

## 验证与记录

- 日常构建、检查、安装与演示统一使用 `tools/tv.py`，命令以 README 为准；普通 Android 界面改动无需重跑完整 Python/网页测试，跨端协议修改必须覆盖关联回归。
- 自动化使用模拟 API 与合成媒体，不为演示扫描、整理或修改真实媒体库。
- 模拟器或构建成功不能代替红米电视验收。记录型号、Android API、遥控器行为及片源编码；型号暂不清楚时明确标记待验收。
- 文档严格区分工程骨架、已实现功能和计划能力。计划保留在 `docs/roadmap/`，不加入公开帮助站清单。

## 文档维护

- 编写、维护或审核文档时使用根目录 `.agents/skills/jzmedia-docs/SKILL.md`，遵循[全仓文档规范](../docs/developer/documentation.md#修改后的评审)。README 维护新手快速开始，`docs/testing.md` 与 `docs/releasing.md` 维护操作流程，其他 `docs/` 页面只解释当前有效的接口与设计约束。
- 变更时替换相关步骤和说明，运行 `python3 ../scripts/check_docs_links.py --root android-tv`；`tv.py test/check` 也包含此检查。历史测试数量、临时包路径和逐轮开发经过写在提交／PR 或发布记录，不新增验证历史页；当前未验收事项只维护在根 `docs/roadmap/android-tv.md`。
