# 添加第一部影片

<script setup>
import DocLibraryMap from '../.vitepress/theme/components/DocLibraryMap.vue'
</script>

<span id="新手配置向导"></span>

这份向导带你把一部电影或一集电视剧加入片库并试播。请先[安装 jzmedia](../getting-started/deployment.md)。

## 准备

准备好存放视频的目录，或一个可以从电脑、手机上传的视频文件。使用 TMDB 获取片名、海报等资料时，再准备自己的 Read Token。

<details>
<summary>媒体库与视频库有什么不同</summary>

媒体库是存放文件的位置，例如 `/app/media`。下面的 `Movies` 和 `TV Shows` 可以分别建成电影和剧集视频库，扫描或上传时选择其中一个。

<DocLibraryMap />

</details>

首次打开空库，点“开始配置”。片库已有内容时，从“设置 → 新手配置”进入。

<DocFigure src="../assets/previews/onboarding/00-welcome.webp" mobile-src="../assets/previews/onboarding/00-welcome-mobile.webp" alt="空库欢迎卡片中的开始配置按钮" caption="本页用合成短片和本地 NFO 演示从空库到试播的过程。" />

## 跟随四步完成首次入库

### Step 1：选择资料来源

有 TMDB 凭据时，填写自己的 Read Token，点“保存并测试 TMDB 连接”。令牌可按界面中的官方说明获取。同时填写 API Key 和 Read Token 时优先使用后者。测试会验证服务器与 TMDB 接口的连接，海报下载另走图片服务。

点“稍后配置 TMDB”也能继续，先使用影片旁的 NFO 资料文件、本地缓存或已启用的备用来源。

<DocFigure src="../assets/previews/onboarding/01-source.webp" mobile-src="../assets/previews/onboarding/01-source-mobile.webp" alt="资料来源步骤中保存并测试与稍后配置 TMDB 的入口" caption="演示点击“稍后配置 TMDB”；跳过不妨碍登记本地影片。" />

### Step 2：检查并使用视频库

选择电影或剧集，再选择使用已有视频库、在已有位置添加视频库，或添加文件存放位置；手机通过“添加方式”下拉框切换。可添加本地、SMB、NFS 存储与视频子目录。

点击“检查并使用此视频库”，看到目录检查成功再继续。首次启动的默认库也需要检查。

填写目录时核对：

- 电影与剧集目录不能重叠；已有内容的库不能直接改类型。
- Docker 中填应用看得见的路径（默认 `/app/media`），不是浏览器所在电脑的路径。
- 本地可写库按需创建子目录，远程子目录需预先存在。

<DocFigure src="../assets/previews/onboarding/02-library.webp" mobile-src="../assets/previews/onboarding/02-library-mobile.webp" alt="视频库步骤显示电影目标库与检查并使用此视频库按钮" caption="演示选择已有电影视频库；实际使用时核对自己的存储位置与子目录。" />

### Step 3：扫描或上传内容

先核对顶部的目标视频库，再按文件所在位置选择：

- 视频已在服务器或 NAS 上：选择“扫描服务器已有文件”，再点“扫描此视频库”。
- 视频在当前电脑或手机上：选择“从当前设备上传”。

只读库可以扫描和播放。上传需要写权限，同名文件会跳过。电影可选多个文件或整个文件夹。剧集可上传带剧名的整个文件夹，单独上传分集时需指定所属剧和季号。

扫描可能按库设置写入 NFO 和图片，命名和写入选项可在建库表单中展开。导入剧集后会自动补充缺少的资料。若出现[目录整理预览](tv-organizing.md)，可选“稍后处理”，确认整理后才会移动或改名。

<DocFigure src="../assets/previews/onboarding/03-import.webp" mobile-src="../assets/previews/onboarding/03-import-mobile.webp" alt="添加内容步骤中扫描此视频库与上传入口" caption="点击“扫描此视频库”开始；演示目录预置合成短片，扫描后登记为一部电影。" />

### Step 4：核对结果并试播

在结果页点“查看详情与播放”，核对片名、年份或集号，再点播放。若显示待匹配，可在详情点“匹配资料”搜索候选。

目标库中至少登记了一部电影或一集，才能点“完成引导”。如果上传后登记数仍为零，检查文件是否为正片、剧集文件名是否带有可识别的集号。字幕和花絮不计入首次入库。完成后还可以继续添加另一个视频库。

<DocFigure src="../assets/previews/onboarding/04-result.webp" mobile-src="../assets/previews/onboarding/04-result-mobile.webp" alt="结果页列出已登记的演示影片与详情入口" caption="演示结果中已登记一部电影，可从条目进入详情与播放。" />

<details>
<summary>查看首次试播画面</summary>

<DocFigure src="../assets/previews/onboarding/07-play.webp" alt="演示影片在播放器中打开" caption="先确认能够播放，再按需完善匹配和整理目录。" />

</details>

## 看一遍完整操作

<DocVideo src="../assets/videos/onboarding.mp4" poster="../assets/videos/onboarding.webp" captions="../assets/videos/onboarding.vtt" title="从空库到首次播放">

文字步骤：开始配置 → 选择资料来源或跳过 → 检查并使用目标视频库 → 扫描或上传 → 核对入库结果 → 打开详情播放。

</DocVideo>

## 完成后检查

回到海报墙，打开刚添加的影片，确认播放的是正确文件。片名或海报不对时，可以稍后[核对匹配资料](metadata.md)。

## 暂缓与恢复

“稍后继续”或“稍后导入”保留配置和内容，并停止自动欢迎提醒；之后从设置继续。直接离开未完成向导，首页显示“继续配置”。同一安装的浏览器共享引导进度。

扫描是后台任务，离开页面仍继续；上传依赖当前浏览器，需取消后才能离开。刷新或关闭浏览器后重新选择文件，已传完同名文件会跳过。传输结束却没有登记内容时，可扫描目标库确认。服务重启后任务详情可能消失，已入库内容和引导进度仍保留。

返回时，向导会核对目标库是否存在、已启用且类型一致。连接配置有变动时，回到第 2 步重新检查。部分文件入库成功也可完成引导，其余文件可按结果重试。

## 常见问题

- **目录选不上：**确认填写的是服务器可见路径；远程库先检查连接，参见[连接媒体库](libraries.md)。
- **TMDB 测试失败：**可以明确跳过，到“设置 → 在线资料服务”补配；备用搜索成功不等于 TMDB 连接成功。
- **影片有记录但没有海报：**先核对匹配和资料来源，见[修正匹配](metadata.md#案例二发现匹配错了)。

## 不影响现有数据的预览

上面的图解和录屏无需连接实例。贡献者需要亲自运行隔离演示时，按[文档制作说明](../developer/documentation.md#隔离演示)操作。

## 下一步

[找片与播放](find-movies.md) · [音轨与字幕](subtitles.md) · [添加更多文件](files.md)
