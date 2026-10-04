---
version: 0.20.0
reviewed: 2026-10-04
---

# 同步发布 Docker 镜像与 APK

从同一个 Git 提交构建 jzmedia 镜像与 Android TV APK，并生成可追溯的交付清单。两端共用仓库根 `version.properties` 的版本，统一入口为 `scripts/release.py`。默认 APK 是可安装的 Debug 试装包；真机兼容性仍需单独验收。

普通部署按[安装教程](../getting-started/deployment.md)操作。日常前端、后端和 Android 开发保留各自命令；本页用于一次同步交付两个产物。

## 完成一次统一发布

### Step 1：准备构建环境

在完整 Git 仓库根目录操作。需要 Python 3.12+、可用的 Docker Engine 或 Docker Desktop、已安装 `requirements-dev.txt` 的项目 Python 环境、Node.js/npm，以及 Android JDK、SDK 与构建组件。服务端和网页环境见[开发与验证](development.md)，Android 环境按仓库 `android-tv/README.md` 准备。

首次运行需要下载 Docker 基础镜像、npm/Python 与 Gradle 依赖。先完成网络配置；依赖缓存齐全时可使用后面的离线选项。已有运行容器无需为构建而停止。

```bash
python3 scripts/release.py --help
python3 scripts/release.py check
```

**预期结果：** `check` 报告各端版本与根版本源一致。它只核对版本，不代替测试或构建。

### Step 2：确定版本并提交源码

每次交付内容发生变化，同时提高发行版本 `versionName` 与 Android `versionCode`。修复升 patch、新功能升 minor、破坏性变更升 major；`0.x` 阶段的破坏性变更升 minor 并说明兼容影响。Android code 全局递增，不按日期编码或随主版本重置。

用实际版本号替换 `X.Y.Z`，用大于最近交付值的整数替换 `N`：

```bash
python3 scripts/release.py version X.Y.Z --android-code N
python3 scripts/release.py check
git status --short
```

审阅并提交源码、同步的版本文件及相关文档。根 `version.properties` 是唯一手工维护的版本源；不要分别修改各端版本来消除报错。本地重复开发构建不需要升版。

**预期结果：** 待发布源码已进入同一个 Git 提交，工作树干净。`build` 会拒绝未提交或未跟踪的源码；被忽略的依赖缓存与输出目录不属于发行源码。

### Step 3：同步检查并构建

```bash
python3 scripts/release.py build
```

脚本从干净提交导出同一份源码快照，运行后端全量测试、Python 静态检查、设计资产检查、前端测试与 lint、帮助站完整验证，以及 Android 工具测试、JVM 测试与 lint。随后并行构建镜像与 Debug APK，并用断网、临时数据容器检查镜像启动、主页、帮助站和健康信息。

镜像同时包含网页和帮助站；APK 使用同一发行版本并保留 `-debug` 标记。两端成功后才更新版本镜像标签、`latest` 和最终交付目录，并创建指向同一提交的 Git annotated tag `vX.Y.Z`。已有同名 Git 标签指向其他提交时拒绝发行。

**预期结果：** Docker Desktop/Engine 中存在 `jzmedia:vX.Y.Z` 与 `jzmedia:latest`，终端打印仓库根 `output/releases/vX.Y.Z/`。脚本不会启动部署容器、安装设备、推送镜像或导出镜像 tar。

按环境需要添加构建选项：

| 选项 | 用途 |
| --- | --- |
| `--python 路径` | 指定运行项目验证的 Python，例如 `.venv/bin/python` |
| `--offline` | 让 Android Gradle 使用已有依赖缓存 |
| `--build-proxy URL` | 临时覆盖 Docker 构建代理，不修改 `.env` |

`--offline` 不会让 Docker、npm 或 Python 下载自动离线。发布脚本不读取 `.env`；Docker 构建代理使用 `--build-proxy` 或进程环境的 `BUILD_HTTP_PROXY`。这与 Docker daemon 拉取基础镜像的代理不同，故障排查见[构建网络](../getting-started/troubleshooting.md#wsl--docker-desktop-代理故障)。完整参数以 `python3 scripts/release.py build --help` 为准。

### Step 4：核对并交付

打开打印出的目录，核对文件：

| 文件 | 用途 |
| --- | --- |
| `jzmedia-tv-X.Y.Z-debug.apk` | 默认的可安装 Debug 试装包 |
| 同名 `.apk.sha256`、`.apk.json` | APK 校验值、源码及签名信息 |
| `manifest.json` | 两端统一版本、完整提交、镜像 ID、APK SHA-256 与签名类别 |

Docker 镜像保存在所连接的 Docker Engine 中，交付目录不包含镜像文件。可在 Docker Desktop 的 Images 页面查看标签，或运行：

```bash
docker image inspect jzmedia:vX.Y.Z --format '{{.Id}} {{json .Config.Labels}}'
```

将镜像 ID 与 `manifest.json` 对照。OCI 版本标签为 `X.Y.Z`，Git revision 为完整提交；本地镜像标签使用 `vX.Y.Z`。部署后可用 `/api/health` 的[版本与提交字段](api.md#版本与源码追溯)核对正在运行的镜像。

<span id="docker-image-export"></span>

需要交付镜像文件时，在构建机另行手动导出，用实际版本替换 `X.Y.Z`：

```bash
docker save -o output/releases/vX.Y.Z/jzmedia-vX.Y.Z.tar jzmedia:vX.Y.Z
```

**预期结果：**交付目录中新增镜像 tar，保留 `jzmedia:vX.Y.Z` 标签。将文件、统一清单及该版本的 `.env.example` 一并交付；使用 Compose 的接收方还需该版本的 `docker-compose.yml`。确认镜像架构适用于目标机器后，按[加载镜像文件](../getting-started/deployment.md#加载镜像文件)导入，再选择 Compose 或 `docker run` 启动。镜像 tar 不包含运行实例的数据或媒体，迁移实例另按[备份与恢复](../getting-started/operations.md)处理。

交付 APK 时一并提供其校验文件和统一清单，并注明它是 Debug 试装包。覆盖安装前核对新旧 APK 签名；不同电脑的 Debug 密钥可能不同，升版不能解决签名不一致。详细安装与签名检查见仓库 `android-tv/docs/releasing.md`。

构建与自动检查通过只证明本次产物完成，不证明电视解码、HDR、音轨和遥控器行为已验收。在交付说明中单独记录实际测试设备、兼容服务器／协议、已测场景与限制。

## 构建失败与重试

- **版本不一致：** 从根版本源重新同步，审阅改动并提交后再运行 `check`。
- **工作树不干净：** 提交本次发行需要的源码；无关修改先妥善保存，再从干净提交重试。不要为通过检查直接丢弃用户修改。
- **依赖下载失败：** 核对报错来自 Docker 拉取、Docker 构建还是 Gradle 下载，修复对应网络后重跑 `build`。
- **检查或其中一端构建失败：** 查看终端打印的暂存目录及 `logs/`，定位失败环节。失败不更新已有发行标签或 `latest`，不产生成功的最终交付目录；若报告回滚异常，先核对标签和目录状态再重试。
- **版本已存在且内容不同：** 提高统一版本和 Android code，提交后重新构建，不删除历史产物来复用版本。

同一版本的发布清单与 APK 必须持续对应同一内容。对已完成的同一版本再次运行时，脚本核对现有产物后退出，不重新构建或移动 `latest`。保留需要交付的清单和产物；`output/` 被 Git 忽略，不是远端归档或备份。

## Docker 构建缓存

发布命令不变，Dockerfile 使用 BuildKit 缓存。基础镜像、依赖清单和安装设置未变化时，普通代码或 Git 提交变化可复用依赖安装层；发行版本与提交信息在依赖安装后写入。系统包单独安装，修改 `requirements.txt` 不会重新安装 FFmpeg 等系统依赖。

网页与帮助站共享 `jzmedia-npm` 下载缓存，`npm ci --prefer-offline` 优先复用已有包；Python 使用独立的 `jzmedia-pip` 缓存。两者均使用锁定的缓存挂载协调并发访问。升级项目版本会修改 npm 的包描述和锁文件，可能重新执行 `npm ci`，但已下载的包仍可复用；首次填充缓存或出现新依赖时仍需下载。

这些缓存由当前 Docker 构建器保存，不进入最终镜像或 `output/`。更换构建器、清理相应构建缓存或 Docker Desktop 数据后，可能需要重新下载依赖。缓存提高复用率，不保证完全离线；发布命令的 `--offline` 仍只用于 Android Gradle。缓存机制见 [Docker 官方说明](https://docs.docker.com/build/cache/optimize/)。

## 正式签名与升级边界

默认流程交付 Debug 试装包。需要正式版时使用 `build --apk-variant release`，并在仓库外准备正式密钥及四个环境变量：`JZMEDIA_ANDROID_KEYSTORE`、`JZMEDIA_ANDROID_STORE_PASSWORD`、`JZMEDIA_ANDROID_KEY_ALIAS`、`JZMEDIA_ANDROID_KEY_PASSWORD`。其中 keystore 使用绝对路径。脚本要求配置完整并通过 `apksigner` 验证；未签名包或 Debug 证书不能当作正式 APK 交付。密码和密钥不进入源码或发布清单。

正式签名流程不代表已完成真机覆盖升级验收。APK 与服务器可以分别安装升级，运行时继续通过协议和可选能力确认兼容性；版本相同不能替代握手。Android 的详细升级限制与设备待验收项仍按客户端文档维护。
