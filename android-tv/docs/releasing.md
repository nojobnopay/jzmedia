# 打包与升级

面向向他人交付 APK 的维护者。本地开发只需 [README](../README.md) 的 `build`／`install`；交付时按本页操作。

> **命令约定：** 以下命令在 `android-tv/` 执行；Windows 将 `python3` 换成 `py -3`。

## 交付 Debug 试装包

### Step 1：确定版本与兼容范围

编辑 [version.properties](../version.properties)。每次交付内容有变化，**同时提高 `versionName` 和 `versionCode`**；本地重复编译不升版。修复升 patch，新功能升 minor，破坏性变更升 major；`0.x` 阶段的破坏性变更升 minor 并说明影响。

- `versionName` 使用 `X.Y.Z`，Debug 后缀由构建添加。
- `versionCode` 全局递增，不按日期编码或随主版本重置。

先核对最近交付记录；换电脑或清空输出目录不代表可以重置版本。

确认需要的服务器协议／能力与设备范围。当前未完成事项见[开发计划](../../docs/roadmap/android-tv.md)，不能用版本增长代替验收。

### Step 2：检查并导出试装包

```sh
python3 tools/tv.py check
python3 tools/tv.py package debug
```

**预期结果：** 在仓库根 `output/android-tv/` 得到：

| 文件 | 用途 |
| --- | --- |
| `jzmedia-tv-X.Y.Z-debug.apk` | 可安装的 Debug 试装包 |
| 同名 `.apk.sha256` | APK 的 SHA-256 校验值 |
| 同名 `.apk.json` | 版本、应用标识、构建类型、哈希、导出时间与源码提交 |

脚本核对 Gradle 元数据和版本源，拒绝覆盖同名但内容不同的 APK。冲突时先核对历史交付，再提高两个版本值重新构建，不删除记录来绕过检查。

已缓存全部依赖时可加 `--offline`。使用源码压缩包或没有 Git 时，提交信息记为未知，交付说明需补充源码来源。

### Step 3：核对签名和覆盖升级

Debug 包已签名，但不同电脑的 Debug 密钥可能不同。用 SDK Build-Tools 中的 `apksigner` 检查新旧包：

```sh
apksigner verify --verbose --print-certs /实际路径/新包.apk
apksigner verify --verbose --print-certs /实际路径/旧包.apk
```

若未将 Build-Tools 加入 PATH，使用 SDK 目录下 `build-tools/36.0.0/apksigner` 的完整路径（Windows 为 `apksigner.bat`）。[apksigner 官方说明](https://developer.android.com/tools/apksigner)

核对新旧证书 SHA-256 相同后，在测试设备执行覆盖安装。

**预期结果：** 可以启动且原连接配置保留。

覆盖升级要求应用标识、签名相同，并遵守本工程递增版本规则。提高版本号不能解决签名不一致；找不到旧密钥时保留旧安装，先解决密钥或迁移方案。Debug 应用标识为 `org.jzmedia.tv.debug`，正式版为 `org.jzmedia.tv`，二者可并存但不共享连接配置。

### Step 4：随包记录验收结果

交付 APK、校验文件及构建信息，在发布说明补充：

- 签名证书指纹与兼容服务器／协议。
- 实际测试设备、Android/API 与已测场景。
- 已知限制。

`.apk.json` 不自动证明签名身份或验收通过。操作清单见[测试与验收](testing.md)。

正式发布使用标签 `android-tv-vX.Y.Z`。APK、密钥、密码和本地报告不提交 Git；源码只维护当前规则，历史发布结论留在对应发布记录中。

## 正式 Release 的额外步骤

### Step 1：导出未签名包

当前 Gradle 没有配置正式签名，以下命令只导出 `jzmedia-tv-X.Y.Z-unsigned.apk`，**不能直接安装或作为正式版分发**：

```sh
python3 tools/tv.py package release
```

### Step 2：签名并验证覆盖升级

正式签名需维护者在仓库外准备并备份密钥，按 Android Studio 的 **Build → Generate Signed Bundle / APK → APK** 向导生成 Release APK，再执行上面的签名验证和真机覆盖升级。具体流程见 [Android 官方签名指南](https://developer.android.com/studio/publish/app-signing)。

不得用 Debug 密钥充当正式密钥。

### Step 3：生成签名包的交付记录

签名后的文件才可命名为 `jzmedia-tv-X.Y.Z.apk`。其校验值和发布记录必须重新针对签名后的字节生成，不能复用未签名包的旁附信息。

> **自动化范围：** 自动签名与签名后交付记录生成目前未接入本工程，`package release` 不代替这两项操作。

## 为什么区分本地构建与交付

本地 APK 可随源码反复生成；交付包须能唯一追溯内容和签名，因此按版本命名且不可被不同内容覆盖。日期、功能名和测试结论放在旁附信息或发布说明，不放进 APK 文件名。服务器与电视各自升级，连接时仍需检查[客户端协议](protocol.md)。
