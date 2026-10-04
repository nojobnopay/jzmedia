# jzmedia Android TV

与 jzmedia 服务端同仓管理、独立构建和发版的电视客户端，使用 Kotlin、Compose for TV 和 Media3/ExoPlayer。Android Studio 可单独打开本目录。

**连接、浏览和观影代码已接入，当前是待验收的开发版本。** 已实现服务器连接与令牌验证、继续观看、最近加入、媒体库切换、电影/剧集/合集浏览、搜索筛选排序、版本与季集选择、花絮、原生播放和服务端 HLS。播放器包含遥控器控件、画质/音轨/字幕、倍速、字幕字号与延迟、观看进度及下一集。

搜索页支持片名首字母、全拼、英文和演员搜索：用遥控器方向/确定键输入 `SQ` 等缩写即可查看本库候选，中文输入保留系统键盘入口。演员作品基于库内已保存的参演资料，不触发联网刮削或扫描。搜索历史和最近演员只保存在电视本机，可清除，并按服务器与媒体库隔离；接口边界见[选集与搜索设计](docs/search-design.md)。

选季使用海报卡片，缺图时依次尝试整剧海报和文字占位；选集使用紧凑网格，保留版本、合并集、已看、续播和离线状态。浏览分页、详情返回及播放退出恢复对应条目焦点。搜索、频道、媒体库和设置使用统一导航，当前频道与遥控器焦点分别显示；图标与控件维护见[电视设计系统](docs/design-system.md)。

暂停状态跨跳转与换流保留，连续播放沿用倍速和音轨／字幕偏好。服务端、网页和电视按相同阈值判断接近看完：观看达到 95%，或观看至少 80% 且剩余不超过 5 分钟；实际结束仍由播放器结束事件判断。旧服务端的推荐结果可能仍受旧阈值影响，需同步更新服务端及网页构建并重启；无需数据库迁移或重扫。

构建、模拟 API、模拟器和红米真机是不同层次的证据。家中红米电视的型号、Android 版本、中文输入与真实解码能力仍待核实；不能据代码完成或模拟器运行宣称家庭电视已经验收。待验收与分发事项见 [开发计划](../docs/roadmap/android-tv.md)，接口约定见 [客户端协议](docs/protocol.md)。

## 使用边界

- 首版面向家庭局域网，不依赖 Google Play 服务。只通过 HTTP API 使用服务器，不直接读取数据库、媒体目录或 SMB 凭据。
- 扫描、整理、匹配、上传和文件管理继续使用网页。电视与网页沿用全家共享进度，同片同时观看以最后保存为准。
- 服务器需支持原生电视协议 1（`/api/stream/client-info`、`/client-check`、`android_tv` 能力及独立会话）；旧版服务器需先更新。本版尚未正式发布，最低服务端发行版本待发布时锁定，不以界面显示的版本号代替握手验证。
- Android 最低 API 23 是工程基线，须由设备安装与实际播放验证。HDR、HEVC、多声道音频能力按检测及设备实测判断，不按电视品牌放行。
- 文本字幕使用 WebVTT 并由电视端显示；ASS/SSA 转换为兼容文本，特效和复杂排版会简化。图片字幕使用服务器烧录，切换时重新准备播放。

## 构建、测试与安装

使用本目录的 Gradle Wrapper。准备 JDK 17 或更新版本、Android SDK Platform 36、Build Tools 36.0.0；通过 Android Studio 或未入库的 `local.properties` 指定 SDK 位置。

| 配置 | 当前值 |
|---|---|
| Android SDK | min 23；compile/target 36 |
| Gradle / Android Gradle Plugin | 9.1.0 / 9.0.0 |
| Kotlin / Compose compiler plugin | AGP 内置 Kotlin 2.2.10 / 2.2.10 |
| Compose BOM / TV Material | 2025.08.01 / 1.0.1 |
| Media3 / OkHttp | 1.10.1 / 4.12.0 |
| 应用标识 | 正式版 `org.jzmedia.tv`；Debug `org.jzmedia.tv.debug` |
| 开发版本 | `0.5.0-dev`，`versionCode=8` |

在本目录执行：

```sh
./gradlew :app:testDebugUnitTest :app:assembleDebug :app:lintDebug :app:assembleRelease
```

- 可安装的 Debug 包：`app/build/outputs/apk/debug/app-debug.apk`。
- 本轮统一设计系统的本地试装包：`../output/android-tv/jzmedia-tv-0.5.0-dev-design-system-20261003-debug.apk`，旁附 `.apk.sha256`。它与已验证的最终构建逐字节相同，沿用 `versionCode=8` 和原 Debug 签名；旧历史导航包已清理，请按文件名选择设计系统版。用户电视安装与真机验收尚无完成记录。
- 另保留 `../output/android-tv/jzmedia-tv-0.5.0-dev-rebuilt-debug.apk` 及校验文件，重建日志在 `output/android-tv/rebuild/`（仓库根目录）。此包与上述设计系统验收包内容不同，验收记录需分别对应。两包均早于 2026-10-04 的 A「点映」品牌更新。
- 未签名 Release 包：`app/build/outputs/apk/release/app-release-unsigned.apk`，不能直接安装或作为正式发行包。
- 单元测试报告：`app/build/reports/tests/testDebugUnitTest/index.html`；Lint：`app/build/reports/lint-results-debug.html`。

连接已获准调试的电视或电视模拟器后运行 `./gradlew :app:installDebug`，也可用 `adb -s SERIAL install -r app/build/outputs/apk/debug/app-debug.apk` 指定设备。通过电视系统允许的本地 APK 安装方式也可安装 Debug 包。电视和服务器应在可互访的局域网内。

首次启动填写完整服务器地址，例如 `http://192.168.1.10:8080`；若服务器设置了访问令牌，填写同一令牌。检查成功后保存连接。地址和令牌不会添加到媒体链接的查询参数；令牌使用 Android Keystore 加密保存，连接配置不参加系统备份。地址不能含用户名、密码、查询参数或 URL 片段；若服务器重定向，应填写最终地址。

首次 Gradle 构建需要下载工具链和依赖。`local.properties`、SDK、缓存、APK、签名密钥和测试报告均不提交，Android 构建不进入 `start.sh`、Docker 或网页构建链。

首字母搜索需要服务端的 `tv_search` 能力：同步更新服务端代码，安装当前 `requirements.txt`（包含 `pypinyin==0.55.0`），然后重启服务；Docker 部署需重建应用镜像。无数据库迁移或重扫要求。旧服务器上的浏览/播放继续可用，搜索页会提示升级；浏览页“筛选 / 排序”保留原文关键词查询。

演员搜索另需 `tv_actor_search` 能力；缺少此接口时仅演员页提示升级，片名搜索照常。没有保存结构化演员资料的影片暂不能按演员找到；旧混合“演职员姓名”字段包含导演和创作者，不作为演员身份依据。

## 隔离演示与复验

[tools/smoke_server.py](tools/smoke_server.py) 是独立的模拟 API 服务：只使用 Python 标准库和本机已有 FFmpeg，不导入 jzmedia 应用，不读取真实数据库、配置、令牌或 NAS。它在临时目录生成 11 分钟的彩条/测试音、原创模拟海报和 WebVTT，并提供电影、剧集、合集及内存观看进度。

电影海报使用带 `posters/` 的路径，剧集使用 `tv/...` 相对路径，保持与真实接口一致；两类图片均由 `/posters/` 提供。复验首页继续观看、剧集墙及详情时应同时检查图片，不能只检查文字和导航。

以下命令从**仓库根目录**执行。先指定已有 FFmpeg 的绝对路径；系统已安装时可用 `TV_FFMPEG="$(command -v ffmpeg)"`。若使用本仓库虚拟环境中已经下载的 `static_ffmpeg`，可只读取得本地路径：

```sh
TV_FFMPEG="$(.venv/bin/python -c 'from pathlib import Path; import static_ffmpeg; print(next((Path(static_ffmpeg.__file__).parent / "bin").glob("*/ffmpeg")).resolve())')"
python android-tv/tools/smoke_server.py --ffmpeg "$TV_FFMPEG" --port 18888
```

也可直接把 `TV_FFMPEG` 设置为本机 FFmpeg 二进制的绝对路径。脚本不会自动下载 FFmpeg。看到输出的 `url` 和 `fixtures` 后表示媒体已生成、服务开始监听；输出和请求记录均为合成演示资料。

另开终端，使用 `adb devices` 中的设备序列号替换 `SERIAL`，设置反向端口转发：

```sh
adb -s SERIAL reverse tcp:18888 tcp:18888
```

电视客户端填写 `http://127.0.0.1:18888`，令牌留空。服务只监听开发机的 `127.0.0.1`，因此设备需要上述 ADB 转发；它不会公开到家庭局域网。模拟器连接该地址时同样按上述方式设置转发。

默认模式提供 MP4 原文件和 Range 请求。按 `Ctrl+C` 停止后，可分别用以下模式重启；同一端口一次只启动一个实例，重启会重置演示进度：

```sh
# 固定返回完整 fMP4 HLS，验证 HLS 加载、片内续播及文本字幕。
python android-tv/tools/smoke_server.py --ffmpeg "$TV_FFMPEG" --port 18888 --hls

# HLS 加模拟令牌认证；也可不带 --hls，检查原文件播放下的写请求认证。
python android-tv/tools/smoke_server.py --ffmpeg "$TV_FFMPEG" --port 18888 --hls --require-token
```

认证模式的实际 CLI 参数是 `--require-token`；固定演示令牌为 `demo-tv-token`。先以空值/错误值检查连接提示，再输入该演示令牌；不要填入真实服务器令牌。只读握手仍允许访问，POST/DELETE 使用演示令牌校验。

每次复验记录 APK、模拟器/API、启动模式和操作：连接、分页/筛选、详情返回焦点、电影/剧集/花絮入口、暂停/跳转/字幕、进度和 Home 退出。请求轨迹位于启动输出的 `fixtures` 目录内 `requests.jsonl`，不记录认证头或请求正文；需留存时在退出前复制到 `output/android-tv/`。截图和日志同样放该目录，不提交 Git。复验后 `Ctrl+C` 结束服务，执行 `adb -s SERIAL reverse --remove tcp:18888` 撤销转发；正常退出会删除临时媒体。

使用 `--browse-stress` 可切换到 120 项选集夹具，覆盖分页、长标题、合并集、多版本及离线状态；搜索 `SQ` 命中原创合成片名“沙丘回声”，`YYDDT` 命中“遥远的灯塔”。夹具契约检查：`python -m unittest discover -s android-tv/tools -p test_smoke_server.py`。模拟拼音映射只用于界面复验，真实搜索算法另由服务端临时数据库测试覆盖。

演员夹具可搜 `ZXH`，得到合成人物“周星河”和“周晓河”；前者有超过一页的电影和电视剧作品。`LWT` 测试无人物 ID、无头像且仅参演电视剧的演员；`GYZ` 只关联第二媒体库。空词列出超过一页演员。人名、关系、头像和媒体均为合成资料，不依赖真实演员或真实媒体库。

使用 `--season-stress` 展示 0–7 共八季，覆盖特别篇、长季名、空季名、不同已看状态与续播；可与 `--browse-stress` 合用。S0、S1、S2、S6 使用不同颜色的原创海报；S3、S7 的季海报路径为空，S4 返回 HTTP 404，S5 返回 HTTP 200 的非图片正文，模拟图片损坏。默认保留整剧海报，用于检查季图失败后回退；追加 `--no-show-poster` 切换为整剧缺图模式，检查最终文字占位。还可核对季卡片换行、进入选集后返回原季，以及纯集号和状态双行按钮的居中。

**这些模拟结果仅验证客户端协议接线、界面和选定合成媒体的表现。** 模拟播放决策由启动模式固定，不能验证真实后端的能力判断、动态转码、会话共享、资源满额或真实媒体库；模拟器也不能代表红米电视的硬件解码、HDR、音频输出及遥控器。

### 真实后端与临时媒体库

[tools/backend_fixture.py](tools/backend_fixture.py) 使用实际 FastAPI、SQLite、探测和转码代码，所有配置与数据在导入应用前隔离到新建的 `/tmp/jzmedia-tv-backend-*` 目录，不继承真实令牌、TMDB、AI 或 NAS 配置。它生成 H.264/AAC MP4，以及 H.264/双 MP2 音轨 MKV 和中文 SRT；后者用于检验真实服务器生成的独立视频、双 AAC 音轨 fMP4 HLS。

```sh
# 仓库根目录，使用已安装依赖及已有 FFmpeg/FFprobe；不自动下载二进制。
.venv/bin/python android-tv/tools/backend_fixture.py --port 18889
adb -s SERIAL reverse tcp:18889 tcp:18889
```

客户端连接 `http://127.0.0.1:18889`，令牌留空。先播放“直接播放”，再播放“HLS 双音轨字幕”，检查首帧、时间推进、切音轨后对应 rendition 请求、字幕及 Home 退出。输出目录的 `fixture-info.json` 记录合成来源，`requests.jsonl` 记录真实接口请求，不含认证头或正文。结束用 `Ctrl+C` 或向该实例输出的 PID 发送 `SIGTERM`，后端正常关闭并回收 FFmpeg；再移除 `tcp:18889` 转发。临时目录保留供复核，可在不再需要证据时自行删除。

## 遥控器操作

| 按键 | 行为 |
|---|---|
| 方向键 | 页面中移动焦点；播放控件隐藏时，左右预选跳转位置，上下唤出控件 |
| 确定 | 激活选项；播放控件隐藏时暂停/继续；预选跳转时确认位置 |
| 返回 | 先关设置或取消跳转预选，再隐藏控件、退出播放器、返回上页，首页可退出应用 |
| 音量 | 使用电视系统音量 |

控件提供明确的播放、快退/快进、设置及下一集入口。下一集倒计时只由播放器真正结束触发，自动连播可关闭。退出、按 Home 或应用进入后台会保存进度、停止声音并释放本机播放资源；网络失败时可重试或返回。焦点恢复和中文软键盘兼容性仍需在实际遥控器上检查。

## 独立发布与升级

APK 单独维护版本，发布标签为 `android-tv-vX.Y.Z`，每次正式分发递增 `versionCode`。正式版使用同一仓库外签名密钥，并单独备份密钥与密码；可通过 Android Studio 的 Generate Signed Bundle / APK 完成签名。签名后执行 `apksigner verify --verbose`，并在测试设备验证同签名覆盖升级及连接配置保留。

Debug 与正式版应用标识不同，可以并存；Debug 安装成功不能证明正式版覆盖升级可行。不得使用 Debug 签名发布正式版，也不得把密钥写入 Gradle 配置或 Git。

发布记录必须包括 APK 版本/校验值、兼容服务端版本与协议、签名身份、已验收的型号/Android API 和限制。APK 和服务器不要求同时升级，协议不兼容时由连接检查提示。

## 验证记录与真机清单

核对日期：2026-10-03。各开发版本的测试计数、构建、模拟器运行和覆盖安装证据已移至[开发验证历史](docs/validation-history.md)。最近一次统一设计系统验收见[当轮结果](docs/validation-history.md#统一设计系统验收)及[跨端验收清单](../design/acceptance/2026-10-03.json)。这些记录明确区分模拟 API、真实后端与合成媒体、软件模拟器及待验收的真实设备。

红米真机、真实扬声器听感、系统中文输入法及正式签名升级仍待验收。当前试装包、对应验收资料和最近播放核对证据保留在仓库根目录 `output/android-tv/`；已被替代的历史包和中间产物可清理，历史文档中的原始路径不保证仍存在。

每台电视记录型号、Android/API、APK/服务器版本，并依次验证：

1. 安装、电视桌面入口、中文输入、方向/确定/返回操作与焦点恢复。
2. 原文件和 HLS、MP4/MKV、H.264/HEVC、多音轨、文本和图片字幕；4K/HDR 单独记录效果。
3. 网页与电视相互续播、片中跳转、字幕时间、正常结束后连播。
4. 两台设备同片并发，分别切档、跳转和关闭互不打断；服务器满额提示可返回。
5. Home、待机、断网、重试与退出无残留声音；同签名正式版覆盖升级保留配置。

工程约定见 [AGENTS.md](AGENTS.md)。图标、字标、启动图标和横幅以仓库根目录 `design/` 为唯一设计源，生成的向量/位图及设计令牌分别供电视与网页消费；正常 APK 构建不依赖 Python 或 Node.js。按钮、缺图占位、遥控焦点及专用几何见 [跨端图标与电视控件](docs/design-system.md)。
