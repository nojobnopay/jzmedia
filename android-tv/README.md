# jzmedia Android TV

在电视上连接 jzmedia，浏览电影、剧集和合集，用遥控器搜索、播放和续看。扫描、匹配、整理与上传仍在网页端操作。

目前是**真机待验收的开发版本**。已有 APK 可直接试装；自己编译请按下面的 Step 操作。发行版本与 Docker 镜像共用根 [version.properties](../version.properties)，维护者通过[统一发布](../docs/developer/releasing.md)同步交付两端。设备验收和升级限制见[当前状态](../docs/roadmap/android-tv.md)。

[安装已有 APK](#已有-apk安装并连接) · [从源码构建](#从源码构建测试与安装) · [日常命令与排错](#日常命令与常见问题)

## 已有 APK：安装并连接

### Step 1：确认拿到可安装的包

从 [GitHub Releases](https://github.com/nojobnopay/jzmedia/releases) 下载可安装的 `jzmedia-tv-X.Y.Z-debug.apk`，`X.Y.Z` 是版本号。随包提供 `.apk.sha256`、`.apk.json` 和统一 `manifest.json`，记录校验值、源码及签名信息；它仍是 Debug 试装包，真机兼容性待验收。

维护者另行提供正式签名包时，文件名为 `jzmedia-tv-X.Y.Z.apk`；文件名含 `-unsigned` 的包不能安装。源码仓库不提交 APK；标签触发的 Actions 将交付包上传至 GitHub Releases，本机构建目录为 `output/releases/vX.Y.Z/`。

### Step 2：在电视安装

将 APK 复制到 U 盘，用电视支持的文件管理器打开，按系统提示允许该来源安装，然后选择“安装”。各品牌入口不同。

也可用电脑安装：按下节准备 Python、SDK Platform-Tools 并[连接设备](#step-3连接电视或模拟器)后，在 `android-tv/` 执行：

```sh
python3 tools/tv.py install --apk /实际路径/文件.apk
```

将路径换成拿到的 APK，含空格时加引号。此命令安装指定包，无需 JDK 或构建；完成后在电视应用列表手动打开。

安装后应能在电视应用列表找到 jzmedia。覆盖升级须使用同一签名；遇到“签名不一致”先向包提供者索取同签名包，卸载会丢失本机连接配置。具体规则见[打包与升级](docs/releasing.md)。

### Step 3：填写服务器地址

1. 打开应用，选择“扫描局域网”，从弹出的服务器列表中选择一台并确认连接（名称 · 版本 · 地址）；附近没有或扫描无结果时手动输入完整地址，例如 `http://192.168.1.10:8080`，将示例 IP 换成你的 jzmedia 服务器局域网 IP。
2. 服务器设置了访问令牌时填写同一令牌，否则留空。
3. 选择检查连接，成功后保存。服务器换了 IP 后再次扫描，会提示更新已保存的地址。

**预期结果：** 进入媒体首页。

电视和服务器需能互访；普通连接不要填写 `localhost` 或 `127.0.0.1`，它们指电视自身。提示协议不兼容时先更新服务器；没有服务器、只想体验界面，可按[隔离演示](docs/testing.md#step-2运行隔离演示)操作。

## 从源码构建、测试与安装

### Step 1：准备环境（首次一次）

1. **安装并打开工程。** 安装 [Android Studio](https://developer.android.com/studio) 和 [Python](https://www.python.org/downloads/)（本工具要求 Python 3.10+）。取得完整仓库后，用 Studio 的 **Open** 打开 `android-tv/`。首次同步若提示缺少 SDK，完成下一项后重新同步。

2. **安装 SDK 组件。** 打开 **Tools → SDK Manager**，按下面的清单选择组件，再点 **Apply** 完成下载和许可确认。[SDK Manager 官方说明](https://developer.android.com/studio/intro/update#sdk-manager)

   | 位置 | 安装内容 |
   | --- | --- |
   | SDK Platforms | Android API 36 |
   | SDK Tools | Android SDK Platform-Tools |
   | SDK Tools → Show Package Details | Android SDK Build-Tools 36.0.0 |
   | SDK Tools（需要模拟器时） | Android Emulator |

3. **检查环境。** 在仓库根目录打开终端，执行：

   ```sh
   cd android-tv
   python3 tools/tv.py doctor
   ```

> **命令约定：** 后续命令均在 `android-tv/` 执行。Windows PowerShell 将 `python3` 换成 `py -3`，例如 `py -3 tools/tv.py doctor`。Windows 新手建议让 Studio、Python 和 SDK 都运行在 Windows，避免与 WSL 路径混用。

`doctor` 会检查 JDK、SDK 和构建组件，缺少什么就给出修复提示。使用 JDK 17 或 21；脚本会查找常见位置的 Studio 自带 JDK。自定义安装路径时设置 `JAVA_HOME` 为 JDK 目录、`ANDROID_HOME` 为 SDK 目录，或让 Studio 生成 `local.properties`。Studio 内的 JDK 设置与终端环境可能不同，详见 [JDK 官方说明](https://developer.android.com/build/jdks)。

**预期结果：** 终端显示“环境检查通过”。FFmpeg 只在生成演示媒体时需要。

首次构建需联网下载 Gradle 和依赖，不必另装 Gradle，也不需要启动 jzmedia 后端。

### Step 2：一条命令完成测试和构建

```sh
python3 tools/tv.py check
```

脚本依次运行工具测试、文档链接检查、Android 单元测试、Lint 和 Debug／Release 构建，失败即停止。

**预期结果：** 终端显示“检查通过”，并生成以下产物：

| 产物 | 位置（相对 `android-tv/`） |
| --- | --- |
| 可安装的本地 Debug 包 | `app/build/outputs/apk/debug/app-debug.apk` |
| 单元测试报告 | `app/build/reports/tests/testDebugUnitTest/index.html` |
| Lint 报告 | `app/build/reports/lint-results-debug.html` |

没有配置正式签名时，Release 构建产出 `app-release-unsigned.apk`，仅供后续签名。向他人交付 APK 时使用[统一发布](../docs/developer/releasing.md)，签名与覆盖升级要求见[打包与升级](docs/releasing.md)。

### Step 3：连接电视或模拟器

选择下面一种方式即可。

#### 使用模拟器

1. 在 Android Studio 打开 **View → Tool Windows → Device Manager**。
2. 点击 **+ → Create Virtual Device**，选择 TV 设备及兼容电脑的 TV 系统镜像（API 23 以上）。
3. 创建完成后点击启动按钮，等待电视桌面出现。

具体入口见[模拟器官方说明](https://developer.android.com/studio/run/managing-avds)。

#### 使用实体电视

按电视厂商说明开启开发者选项和 ADB 调试，让电脑与电视处于同一局域网。若提供无线配对，先运行：

```sh
python3 tools/tv.py pair 电视IP:配对端口
```

输入电视屏幕上的配对码，然后连接；只有网络调试入口的设备可直接从这里开始：

```sh
python3 tools/tv.py connect 电视IP:调试端口
```

将中文占位换成电视实际显示的地址；配对端口与调试端口可能不同。电视弹出授权时选择允许。[ADB 官方说明](https://developer.android.com/tools/adb)

#### 确认设备已连接

两种方式最后都执行：

```sh
python3 tools/tv.py devices
```

**预期结果：** 设备右侧显示 `device`。`unauthorized` 表示尚未授权，`offline` 表示需要重新连接。

### Step 4：安装并启动

```sh
python3 tools/tv.py install
```

脚本自动构建 Debug 包、安装到唯一在线设备并启动。源码没变化时 Gradle 会复用构建结果。有多个设备时，复制上一步列出的序列号，执行 `python3 tools/tv.py install --serial 实际序列号`。

**预期结果：** 电视打开客户端；首次使用显示连接页。按上面的[填写服务器地址](#step-3填写服务器地址)完成连接。安装失败时脚本不会卸载旧应用，见下文排查。

## 日常命令与常见问题

### 日常命令

无需每次重做环境准备，按当前任务选择一个命令：

| 任务 | 命令 |
| --- | --- |
| 只构建本地 Debug APK | `python3 tools/tv.py build` |
| 只跑工具测试、文档检查、Android 单测与 Lint | `python3 tools/tv.py test` |
| 完整检查并构建两种包 | `python3 tools/tv.py check` |
| 构建、安装、启动 | `python3 tools/tv.py install` |
| 启动隔离演示并自动设置设备转发 | `python3 tools/tv.py demo` |
| 导出带版本号的本地 Debug 包 | `python3 tools/tv.py package debug` |
| 查看各命令参数 | `python3 tools/tv.py --help` 或子命令后的 `--help` |

构建类命令可加 `--offline` 使用已有缓存；第一次构建不能依赖此选项。单独 `package` 的产物位于仓库根 `output/android-tv/`，供开发检查；对外交付使用统一发布目录。

### 常见问题

- **找不到 Python／Java／SDK：** 先完成环境准备；修改环境变量后重开终端，再运行 `doctor`。SDK 路径可在 SDK Manager 的 Android SDK Location 查看。
- **Gradle 下载失败或超时：** 检查终端网络、代理和 SDK 许可后重试；只有完整缓存时才使用 `--offline`。
- **没有设备／多台设备：** 等模拟器进入桌面，或重新连接并授权电视；多设备加 `--serial`。
- **`INSTALL_FAILED_UPDATE_INCOMPATIBLE`：** 签名不同，索取同签名包；不要用卸载作为默认修复。
- **`INSTALL_FAILED_VERSION_DOWNGRADE`：** 安装包内部版本更旧，使用更高版本；脚本不强制降级。
- **连接失败／搜索提示升级：** 先在同网电脑浏览器检查服务器地址并核对令牌。浏览播放可用但搜索缺能力时，更新服务器依赖并重启。

## 使用方式与原理

方向键移动焦点，确定键激活，返回键逐层退出。播放控件隐藏时，左右预选跳转位置、确定确认；无跳转预选时确定可暂停／继续。控件含音轨、字幕、倍速和下一集；按 Home 或退出播放会保存进度并释放本机播放资源。

客户端使用 Kotlin、Compose for TV 和 Media3，只通过 HTTP API 连接服务器。电视和网页共享观看进度；各设备播放会话独立。Android 日常构建可单独执行，统一发布脚本负责编排镜像和 APK。设备是否能播放 HEVC、HDR 或多声道音频，由实际能力检测和真机验证决定。

文本字幕由电视显示；ASS/SSA 转兼容文本后会简化特效，图片字幕由服务器烧录。片名首字母／全拼搜索和演员搜索分别需要服务器的可选能力。完整规则只在下列参考页维护：

| 想了解什么 | 阅读 |
| --- | --- |
| 合成媒体演示、真实后端验证和真机检查 | [测试与验收](docs/testing.md) |
| 版本、交付、签名和覆盖升级 | [打包与升级](docs/releasing.md) |
| 握手、认证、播放能力和时间轴 | [客户端协议](docs/protocol.md) |
| 搜索接口、选集和演员资料边界 | [选集与搜索](docs/search-design.md) |
| 图标、尺寸、字体和遥控焦点 | [电视设计系统](docs/design-system.md) |
| 未完成事项与当前验收限制 | [开发计划](../docs/roadmap/android-tv.md) |

依赖版本以 [Gradle 版本目录](gradle/libs.versions.toml)、[Wrapper 配置](gradle/wrapper/gradle-wrapper.properties)和[应用配置](app/build.gradle.kts)为准。修改本工程前阅读 [AGENTS.md](AGENTS.md)；文档按[维护规范](../docs/developer/documentation.md#修改后的评审)同步更新，不往 README 追加每轮开发记录。
