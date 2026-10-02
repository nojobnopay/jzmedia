---
version: 0.19.0
reviewed: 2026-10-02
---

# 添加第一部影片

<span id="新手配置向导"></span>

跟随四步向导连接自己的片库，登记一部电影或一集电视剧，并打开播放。完整流程以已经安装好的 jzmedia 为起点；还未安装请先看[安装教程](../getting-started/deployment.md)。

## 准备

准备一个媒体目录或待上传的视频。已有 NFO 时可以跳过 TMDB；需要网络资料时准备自己的 Read Token。媒体库是存储位置，视频库是其中的电影或剧集目录。

<details>
<summary>媒体库与视频库有什么不同</summary>

<DocDiagram src="../assets/diagrams/libraries.svg" alt="一个媒体库存储连接下分别建立电影和剧集视频库" caption="扫描与上传先确认视频库；电影和剧集分开登记。" />

</details>

首次打开空库，点击“开始配置”；已有内容时从“设置 → 新手配置”进入。顶部显示当前步数，已完成步骤显示勾号；手机也使用相同四步，可通过“稍后继续”保留配置。

<DocFigure src="../assets/previews/onboarding/00-welcome.webp" alt="空库欢迎卡片中的开始配置按钮" caption="以下截图和录屏来自隔离演示环境，使用合成短片与本地 NFO。" />

## 跟随四步完成首次入库

<DocStep number="1" title="选择资料来源">

填写自己的 TMDB Read Token，点“保存并测试 TMDB 连接”。也可以点“稍后配置 TMDB”，先用已有 NFO、本地缓存及启用的备用来源。

测试成功表示服务器能够验证 TMDB 凭据和 API，不代表海报下载已验证。令牌获取见界面中的官方说明；Read Token 优先于 API Key。

<template #image>

<DocFigure src="../assets/previews/onboarding/01-source.webp" alt="资料来源步骤中保存并测试与稍后配置 TMDB 的入口" caption="演示点击“稍后配置 TMDB”；跳过不妨碍登记本地影片。" />

</template>

</DocStep>

<DocStep number="2" title="检查并使用视频库">

选择电影或剧集，再选择使用已有视频库、在已有位置添加视频库，或添加文件存放位置；手机通过“添加方式”下拉框切换。可添加本地、SMB、NFS 存储与视频子目录。点击“检查并使用此视频库”，看到目录检查成功再继续。

默认库仍需检查。电影与剧集目录不能重叠；已有内容的库不能直接改类型。Docker 中填应用看得见的路径（如 `/media`），不是浏览器所在电脑的路径。本地可写库按需创建子目录，远程子目录需预先存在。

<template #image>

<DocFigure src="../assets/previews/onboarding/02-library.webp" alt="视频库步骤显示电影目标库与检查并使用此视频库按钮" caption="点击“检查并使用此视频库”确认目标；演示使用隔离库，实际使用请选择自己的库。" />

</template>

</DocStep>

<DocStep number="3" title="扫描或上传内容">

先核对顶部的目标视频库。视频已在服务器或 NAS 上，选择“扫描服务器已有文件”，再点“扫描此视频库”；视频在当前电脑或手机上，选择“从当前设备上传”。两种方式都使用上一步确认的目标。

只读库可以扫描和播放，上传需要写权限。电影支持多文件和文件夹；剧集优先上传带剧名的整个文件夹，散集需指定所属剧与季号。同名文件跳过，不覆盖。命名档和资料落盘选项在建库表单的折叠区，扫描可能按策略写入 NFO 和图片。

<template #image>

<DocFigure src="../assets/previews/onboarding/03-import.webp" alt="添加内容步骤中扫描此视频库与上传入口" caption="点击“扫描此视频库”开始；演示目录预置合成短片，扫描后登记为一部电影。" />

</template>

</DocStep>

<DocStep number="4" title="核对结果并试播">

查看已登记内容、待匹配提示与上传失败数。点击条目的“查看详情与播放”，核对片名、年份或集号，再点播放。未匹配的电影或整部剧可在详情点击“匹配资料”，展开搜索后继续核对。已成功登记至少一部电影或一集时，可完成引导；还可以继续添加另一个视频库。

<template #image>

<DocFigure src="../assets/previews/onboarding/04-result.webp" alt="结果页列出已登记的演示影片与详情入口" caption="文件传完不等于已登记；仅字幕、花絮或不能识别集号的文件不算完成导入。" />

<DocFigure src="../assets/previews/onboarding/07-play.webp" alt="演示影片在播放器中打开" caption="先确认能够播放，再按需完善匹配和整理目录。" />

</template>

</DocStep>

## 看一遍完整操作

<DocVideo src="../assets/videos/onboarding.mp4" poster="../assets/videos/onboarding.webp" captions="../assets/videos/onboarding.vtt" title="从空库到首次播放">

文字步骤：开始配置 → 选择资料来源或跳过 → 检查并使用目标视频库 → 扫描或上传 → 核对入库结果 → 打开详情播放。

</DocVideo>

## 完成后检查

海报墙能找到刚添加的内容，详情指向正确文件，播放能开始。未匹配或资料不完整的内容可以先登记，之后到[扫描与匹配](metadata.md)处理。目录整理需要另行预览和确认，向导不会自动移动或改名媒体文件。

## 暂缓与恢复

“稍后继续”或“稍后导入”保留配置和内容，并停止自动欢迎提醒；之后从设置继续。直接离开未完成向导，首页显示“继续配置”。同一安装的浏览器共享引导进度。

扫描是后台任务，离开页面仍继续；上传依赖当前浏览器，需取消后才能离开。刷新或关闭浏览器后重新选择文件，已传完同名文件会跳过。传输结束却没有登记内容时，可扫描目标库确认。服务重启后任务详情可能消失，已入库内容和引导进度仍保留。

返回时会检查目标库是否存在、启用、类型一致；配置变化需重新检查。部分内容入库、部分失败时可以完成引导，但应核对失败并按需重试。

## 常见问题

- **目录选不上：**确认填写的是服务器可见路径；远程库先检查连接，参见[连接媒体库](libraries.md)。
- **TMDB 测试失败：**可以明确跳过，到“设置 → 在线资料服务”补配；备用搜索成功不等于 TMDB 连接成功。
- **影片有记录但没有海报：**先核对匹配和资料来源，见[修正匹配](metadata.md#案例二发现匹配错了)。

## 不影响现有数据的预览

上面的图解和录屏无需连接实例。贡献者需要亲自运行隔离演示时，按[文档制作说明](../developer/documentation.md#隔离演示)操作。

## 下一步

[找片与播放](find-movies.md) · [音轨与字幕](subtitles.md) · [添加更多文件](files.md)
