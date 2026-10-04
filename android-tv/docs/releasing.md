# 打包与升级

Docker 镜像与 Android TV APK 通过仓库根[统一发布流程](../../docs/developer/releasing.md)同步构建、统一版本并记录同一源码提交。默认交付可安装的 Debug 试装包；本页说明 Android 签名、覆盖升级及单端开发打包。

版本来源是根 [version.properties](../../version.properties)。统一发布使用 `vX.Y.Z` 标识；旧的 `android-tv-vX.Y.Z` 仅保留历史，不再创建独立 Android 发行版本。

## 交付 Debug 试装包

### Step 1：取得统一交付产物

按[统一发布](../../docs/developer/releasing.md)完成版本更新、提交、检查和构建。在仓库根 `output/releases/vX.Y.Z/` 核对 APK、同名 `.apk.sha256`／`.apk.json` 及 `manifest.json`。清单应将 APK 与 Docker 镜像关联到相同完整 Git 提交。

默认 APK 为 `jzmedia-tv-X.Y.Z-debug.apk`。`-debug` 表示 Debug 构建与签名，不代表正式版；未签名的 `-unsigned.apk` 不能安装或交付。

### Step 2：核对签名和覆盖升级

Debug 包已签名，但不同电脑的 Debug 密钥可能不同。用 SDK Build-Tools 中的 `apksigner` 检查新旧包：

```sh
apksigner verify --verbose --print-certs /实际路径/新包.apk
apksigner verify --verbose --print-certs /实际路径/旧包.apk
```

若未将 Build-Tools 加入 PATH，使用 SDK 目录下 `build-tools/36.0.0/apksigner` 的完整路径（Windows 为 `apksigner.bat`）。[apksigner 官方说明](https://developer.android.com/tools/apksigner)

核对新旧证书 SHA-256 相同后，在测试设备执行覆盖安装，安装入口见 [README](../README.md#已有-apk安装并连接)。

**预期结果：** 可以启动且原连接配置保留。

覆盖升级要求应用标识、签名相同，并遵守递增版本规则。提高版本号不能解决签名不一致；找不到旧密钥时保留旧安装，先解决密钥或迁移方案。Debug 应用标识为 `org.jzmedia.tv.debug`，正式版为 `org.jzmedia.tv`，二者可并存但不共享连接配置。

### Step 3：随包记录验收结果

交付 APK、校验文件及统一清单，在发布说明补充：

- 签名证书指纹与兼容服务器／协议。
- 实际测试设备、Android/API 与已测场景。
- 已知限制。

旁附 JSON 记录源码和签名检查结果，但不能证明真机验收通过。操作清单见[测试与验收](testing.md)，历史调试签名和当前限制见[开发计划](../../docs/roadmap/android-tv.md)。APK、密钥、密码和本地报告不提交 Git；历史发布结论留在对应交付记录中。

## 正式 Release 的额外步骤

### Step 1：准备正式密钥

正式密钥在仓库外保管并备份；密码通过环境提供，不写进源码或命令历史。配置以下变量后，统一发布才允许使用 `build --apk-variant release`：

| 环境变量 | 内容 |
| --- | --- |
| `JZMEDIA_ANDROID_KEYSTORE` | 仓库外正式 keystore 文件的绝对路径 |
| `JZMEDIA_ANDROID_STORE_PASSWORD` | keystore 密码 |
| `JZMEDIA_ANDROID_KEY_ALIAS` | 签名密钥别名 |
| `JZMEDIA_ANDROID_KEY_PASSWORD` | 签名密钥密码 |

四项必须完整。`apksigner` 验证失败或使用 Debug 证书时，不能完成正式交付。密钥创建与保管见 [Android 官方签名指南](https://developer.android.com/studio/publish/app-signing)。

### Step 2：构建并验证升级

从仓库根运行统一发布的正式变体，再按上面的签名核对流程执行真机覆盖升级。产物名为 `jzmedia-tv-X.Y.Z.apk`；哈希与证书记录对应签名后的 APK 字节。

**预期结果：** 正式签名验证通过，测试设备安装成功、同签名升级保留配置，发布说明记录验证范围。脚本完成不代表这些真机步骤已经执行。

## 单端开发打包

日常构建和安装只需 [README](../README.md) 的 `build`／`install`。需要查看带版本名的 Android 本地产物时，在 `android-tv/` 执行：

```sh
python3 tools/tv.py package debug
```

默认输出位于仓库根 `output/android-tv/`。脚本核对 Gradle 元数据和根版本源，生成 APK、`.apk.sha256` 与 `.apk.json`，拒绝同名但内容不同的覆盖。此入口仅构建 Android，不生成双端发布清单；对外交付仍用统一流程。

`package release` 在未提供正式签名配置时只能生成 `jzmedia-tv-X.Y.Z-unsigned.apk`。它不能直接安装，也不能替代统一发布的签名检查。依赖缓存完整时可加 `--offline`；Windows 将 `python3` 换成 `py -3`。

## 为什么区分本地构建与交付

本地 APK 可随源码反复生成；交付包须能唯一追溯内容和签名，因此按统一版本命名并与镜像绑定到同一源码提交。日期、功能名和验收结论放在发布记录中，不放进 APK 文件名。服务器与电视仍可分别安装升级，连接时继续检查[客户端协议](protocol.md)。
