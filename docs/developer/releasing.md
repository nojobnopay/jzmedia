---
version: 0.20.1
reviewed: 2026-10-04
---

# 同步发布 Docker 镜像与 APK

推送发行标签后，GitHub Actions 从该提交完成全部检查，同步构建 Docker 镜像与 Android TV APK，再发布到 GHCR 和 GitHub Releases。两端共用仓库根 `version.properties` 的版本，云端与本机均复用 `scripts/release.py`。默认 APK 是可安装的 Debug 试装包；真机兼容性仍需单独验收。

普通部署按[安装教程](../getting-started/deployment.md)操作。日常前端、后端和 Android 开发保留各自命令；本页用于一次同步交付两个产物。

## 完成一次统一发布

<span id="cloud-signing"></span>

### Step 1：配置云端签名与权限（首次一次）

工作流为仓库 `.github/workflows/release.yml`，在 GitHub 的 **Actions → Release** 查看。先在 **Settings → Secrets and variables → Actions** 配置：

| 类型与名称 | 内容 |
| --- | --- |
| Secret：`JZMEDIA_ANDROID_DEBUG_KEYSTORE_BASE64` | 已交付 Debug APK 使用的原 `debug.keystore`，编码为无换行的标准 Base64 |
| Variable：`JZMEDIA_ANDROID_DEBUG_CERT_SHA256` | 原签名证书 SHA-256，64 位小写十六进制 |

当前已交付证书指纹为 `606d6cafb3e63bd2d4c7ebcb6578bd893a8650ddb0f86dc5d65f96fdf9b5d2a5`。使用原密钥以延续覆盖升级能力；缺少配置或指纹不符时构建会失败，不自动生成替代签名。密钥与 Base64 内容只保存到 Secret，不提交源码、日志或发行附件；签名核对见仓库 `android-tv/docs/releasing.md`。

工作流自动使用 `GITHUB_TOKEN`，无需额外 PAT。`prepare` 为识别发布草稿具有 `contents: write`，`build` 仅有 `contents: read`，`publish` 具有 `contents: write` 与 `packages: write`。已有 GHCR 包须关联 `nojobnopay/jzmedia`、允许该仓库 Actions 写入，并设为 Public；设置入口见[包的公开与关联步骤](#ghcr-package-settings)。

<span id="release-tag"></span>

### Step 2：确定版本并推送标签

每次交付内容变化，同时提高发行版本与 Android `versionCode`。修复升 patch、新功能升 minor、破坏性变更升 major；`0.x` 阶段的破坏性变更升 minor 并说明兼容影响。Android code 全局递增，不按日期编码或随主版本重置。

在本地仓库使用实际版本替换 `X.Y.Z`，使用高于已交付值的整数替换 `N`；例如 `0.20.1`／code 13 的下一次修复发行可用 `0.20.2`／code 14：

```bash
python3 scripts/release.py version X.Y.Z --android-code N
python3 scripts/release.py check
git status --short
```

审阅并提交本次源码、同步的版本文件和相关文档，使工作树干净。随后将以下 `X.Y.Z` 换成同一版本：

```bash
git tag -a vX.Y.Z -m "Release vX.Y.Z"
git push origin main
git push origin vX.Y.Z
```

**预期结果：**GitHub **Actions → Release** 出现对应标签的运行记录。工作流只对版本标签推送发布；普通分支推送不发布。脚本要求标签严格为 `vX.Y.Z`、版本与根文件一致、检出的提交与远端标签一致。云端不自动升版，也不创建或移动 Git 标签。

### Step 3：查看云端检查与构建

`prepare` 先校验标签、源码和现有 Release。随后 `build` 在 GitHub 托管的 Ubuntu runner 上恢复并核验固定 Debug 签名，调用 `scripts/release.py build`，完成[本机构建所列的全部检查](#local-build-checks)、Docker 与 APK 构建以及隔离容器启动验证。本地电脑无需为这次云端构建安装 Docker 或 Android SDK。

构建通过后，`scripts/github_release.py package` 生成八个发行文件，保存为 Actions artifact **release-bundle-运行次数**，保留 14 天。独立的 `publish` job 使用 `build` 输出的准确名称下载同一 bundle，逐项核验哈希、源码身份、镜像和 APK 签名；先保存完整 Release 草稿附件，再推送 GHCR 版本标签，确认匿名可读的镜像清单，公开 Release，最后更新 `latest`。

只想检验云端环境时，在 **Actions → Release → Run workflow** 选择待验证分支。手动运行使用 `build --validate-only`，仍执行全部检查与两端构建，但不发布镜像、Release 或 APK，也不更新 Git／镜像版本标签；仅上传诊断日志。

### Step 4：核对发布结果

**预期结果：**`prepare`、`build` 与 `publish` 均成功，GitHub Releases 出现 `vX.Y.Z`；GHCR 的 `ghcr.io/nojobnopay/jzmedia:vX.Y.Z` 与 `latest` 指向同一 `linux/amd64` 镜像。

Release 包含八个附件；GitHub 自动提供的源码归档不计入其中：

| 文件 | 用途 |
| --- | --- |
| `jzmedia-vX.Y.Z-linux-amd64.tar.gz` | Docker 镜像归档，保留完整 GHCR 版本标签 |
| `docker-compose.yml` | 移除源码构建段，并固定完整 GHCR 镜像名 |
| `jzmedia-tv-X.Y.Z-debug.apk` | 固定 Debug 签名的可安装试装包 |
| 同名 `.apk.sha256`、`.apk.json` | APK 校验值、源码及签名信息 |
| `manifest.json` | 两端统一版本、完整提交、镜像 ID、APK 哈希和签名类别 |
| `SHA256SUMS`、`LICENSE` | 除校验清单自身以外七个文件的 SHA-256 校验值与项目许可证 |

核对发行清单中的源码提交、版本和镜像身份；安装使用[部署教程](../getting-started/deployment.md)。自动化成功不代表电视真机验收通过，设备、兼容场景和限制仍须在发行说明单独记录。

## 在本机完成统一构建

本机流程用于开发验证或维护者手工交付，使用与云端相同的检查和构建入口；它本身不发布 GHCR 或 GitHub Release。

### Step 1：准备构建环境

在完整 Git 仓库根目录操作。需要 Python 3.12+、可用的 Docker Engine 或 Docker Desktop、已安装 `requirements-dev.txt` 的项目 Python 环境、Node.js/npm，以及 Android JDK、SDK 与构建组件。服务端和网页环境见[开发与验证](development.md)，Android 环境按仓库 `android-tv/README.md` 准备。

首次运行需要下载 Docker 基础镜像、npm/Python 与 Gradle 依赖。先完成网络配置；依赖缓存齐全时可使用后面的离线选项。已有运行容器无需为构建而停止。

```bash
python3 scripts/release.py --help
python3 scripts/release.py check
```

**预期结果：** `check` 报告各端版本与根版本源一致。它只核对版本，不代替测试或构建。

### Step 2：准备干净源码

按上文[版本与提交步骤](#release-tag)准备本次源码；仅在本机构建时无需推送标签。根 `version.properties` 是唯一手工维护的版本源；不要分别修改各端版本来消除报错。本地重复开发验证使用 `--validate-only`，无需为检查而升版。

**预期结果：** 待发布源码已进入同一个 Git 提交，工作树干净。`build` 会拒绝未提交或未跟踪的源码；被忽略的依赖缓存与输出目录不属于发行源码。

<span id="local-build-checks"></span>

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
| `--validate-only` | 完成检查与两端构建，保留验证输出，不更新发行目录或 Git／镜像版本标签 |
| `--offline` | 让 Android Gradle 使用已有依赖缓存 |
| `--build-proxy URL` | 临时覆盖 Docker 构建代理，不修改 `.env` |

`--offline` 不会让 Docker、npm 或 Python 下载自动离线。发布脚本不读取 `.env`；Docker 构建代理使用 `--build-proxy` 或进程环境的 `BUILD_HTTP_PROXY`。这与 Docker daemon 拉取基础镜像的代理不同，故障排查见[构建网络](../getting-started/troubleshooting.md#wsl--docker-desktop-代理故障)。完整参数以 `python3 scripts/release.py build --help` 为准。

### Step 4：核对并交付

打开打印出的目录，核对 APK、同名 `.apk.sha256`／`.apk.json` 和 `manifest.json`。本机构建不会自动打包云端的八个附件，Docker 镜像保存在所连接的 Docker Engine 中。可在 Docker Desktop 的 Images 页面查看标签，或运行：

```bash
docker image inspect jzmedia:vX.Y.Z --format '{{.Id}} {{json .Config.Labels}}'
```

将镜像 ID 与 `manifest.json` 对照。OCI 版本标签为 `X.Y.Z`，Git revision 为完整提交；本地镜像标签使用 `vX.Y.Z`。部署后可用 `/api/health` 的[版本与提交字段](api.md#版本与源码追溯)核对正在运行的镜像。

<span id="docker-image-export"></span>

需要交付镜像文件时，在构建机另行手动导出，用实际版本替换 `X.Y.Z`：

```bash
docker save -o output/releases/vX.Y.Z/jzmedia-vX.Y.Z.tar jzmedia:vX.Y.Z
```

**预期结果：**交付目录中新增镜像 tar，保留 `jzmedia:vX.Y.Z` 标签。将文件与统一清单一并交付；使用 Compose 的接收方还需该版本的 `docker-compose.yml`，`.env.example` 可按需附带作为配置参考。确认镜像架构适用于目标机器后，按[加载镜像文件](../getting-started/deployment.md#加载镜像文件)导入，再选择 Compose 或 `docker run` 启动。镜像 tar 不包含运行实例的数据或媒体，迁移实例另按[备份与恢复](../getting-started/operations.md)处理。

交付 APK 时一并提供其校验文件和统一清单，并注明它是 Debug 试装包。覆盖安装前核对新旧 APK 签名；不同电脑的 Debug 密钥可能不同，升版不能解决签名不一致。详细安装与签名检查见仓库 `android-tv/docs/releasing.md`。

构建与自动检查通过只证明本次产物完成，不证明电视解码、HDR、音轨和遥控器行为已验收。在交付说明中单独记录实际测试设备、兼容服务器／协议、已测场景与限制。

## 补发 GHCR 镜像

以下手工入口适用于已有发行产物；正常标签发布由 Actions 自动完成。统一构建完成后，可将已验证的同一镜像补发到 `ghcr.io/nojobnopay/jzmedia`。先将本地镜像 ID、版本与提交标签同 `manifest.json` 核对；补发只增加分发入口，不重新构建、不改 Git 发行标签，也不替换 Release 镜像归档。

在已登录 GHCR、具备该包写入权限的构建机操作，用实际版本替换 `X.Y.Z`：

```bash
docker tag jzmedia:vX.Y.Z ghcr.io/nojobnopay/jzmedia:vX.Y.Z
docker push ghcr.io/nojobnopay/jzmedia:vX.Y.Z
```

<span id="ghcr-package-settings"></span>

个人账户首次发布的 GHCR 包默认为 Private。新镜像包含指向源码仓库的 `org.opencontainers.image.source` 标签；旧镜像或尚未关联的包可通过网页关联。首次推送成功后，在 GitHub 网页核对以下设置：

1. 打开个人主页的 **Packages**，选择 `jzmedia` 包，在版本列表下点击 **Connect repository**，选择 `nojobnopay/jzmedia` 并确认关联。见 [GitHub 关联仓库说明](https://docs.github.com/en/packages/learn-github-packages/connecting-a-repository-to-a-package#connecting-a-repository-to-a-user-scoped-package-on-github)。
2. 打开 **Package settings → Danger Zone → Change visibility**，选择 **Public**，按页面提示输入包名并确认。包的可见性独立于源码仓库，关联公开仓库不能代替这一步；见 [GitHub 个人包可见性说明](https://docs.github.com/en/packages/learn-github-packages/configuring-a-packages-access-control-and-visibility#configuring-visibility-of-packages-for-your-personal-account)。
3. 为工作流授予写入权限。手工发布后再关联仓库的包默认保留原权限：在 **Package settings → Manage access／Inherited access** 勾选 **Inherit access from repository**；或在 **Manage Actions access → Add repository** 添加 `nojobnopay/jzmedia`，将 **Role** 设为至少 **Write**。仅完成仓库关联或公开可见性设置不能代替这一步；见 [GitHub Actions 包访问权限说明](https://docs.github.com/en/packages/learn-github-packages/configuring-a-packages-access-control-and-visibility#ensuring-workflow-access-to-your-package)。
4. 在未登录环境确认版本标签可拉取，核对拉回镜像的 ID、平台及 OCI 版本／提交与统一清单一致。

当前预构建平台为 `linux/amd64`；已有版本标签须保持相同镜像内容，不能覆盖为另一构建。

版本标签通过核对后，再让 `latest` 指向同一镜像：

```bash
docker tag jzmedia:vX.Y.Z ghcr.io/nojobnopay/jzmedia:latest
docker push ghcr.io/nojobnopay/jzmedia:latest
```

**预期结果：**版本标签与 `latest` 的远端摘要一致，版本标签可公开拉取，镜像身份仍对应原发行清单。随后更新发行说明中的在线获取入口；用户安装步骤见[从 GHCR 拉取镜像](../getting-started/deployment.md#从-ghcr-拉取镜像)。本地 `scripts/release.py build` 只负责统一构建与验证；Actions 通过 `scripts/github_release.py publish` 完成远端发布。

## 构建失败与重试

云端从失败步骤和已有产物判断恢复方式：

- **签名配置缺失或不符：** 修正原 Debug 密钥 Secret 或证书 Variable，不能通过更换签名绕过。密钥恢复在构建前验证。
- **`build` 失败且尚无发布草稿：** 查看运行日志和 `release-logs-运行次数` artifact，修正环境后可重跑失败 job；需要改变源码时使用新提交和新版本。
- **`publish` 失败：** 在同一次 Actions 运行选择 **Re-run failed jobs**，复用成功 `build` 指定的 `release-bundle-运行次数`。已有草稿时不要选 **Re-run all jobs**，重新构建会改变产物，脚本会拒绝。
- **GHCR 匿名检查失败：** 核对包已设为 Public、关联仓库及 Actions 权限，再重跑失败 job；此时草稿附件与固定版本镜像可能已保存，尚未进入后续公开或 `latest` 步骤。
- **bundle 超过 14 天或已删除：** 保留现有发行产物并核对草稿状态，不覆盖已有同版本文件；无法恢复原 bundle 时按新版本重新发布。

已经公开且提交、版本一致的 Release 再次触发时，预检查直接结束，不重新构建。发布阶段如果在公开 Release 后更新 `latest` 失败，仍从原运行重跑失败的 `publish` job，使其复用原 bundle 完成后续步骤。

本机构建按以下情况处理：

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
