# 投屏发现后续计划

[开发计划](README.md) · 核对日期：2026-10-13。

本阶段已完成手动添加 DLNA 电视及隔离片源验证。后续优先开发其他功能；投屏自动发现、NAS 部署和真实声画验收按需要重新启动，不把这些计划视为已实现能力。

## 已有能力与证据

- 网页投屏由服务器执行 SSDP `MediaRenderer:1` 搜索、电视 HTTP 控制和媒体供给。普通手机浏览器无法直接收发 SSDP 所需的原始 UDP；当前仓库只有网页前端和独立 Android TV 客户端，没有手机原生客户端。[Chrome Direct Sockets 说明](https://developer.chrome.com/docs/iwa/direct-sockets)
- `POST /api/cast/devices` 可接收完整的 DLNA 描述地址，由服务器验证并加入进程内设备列表；投屏面板提供手动输入。此入口没有手机自动发现能力，重启服务后也不保留手动设备。代码位置及接口约定见[播放核心设计](../developer/playback.md#dlna-接收端播放)与[投屏 API](../developer/api.md#dlna-投屏)。
- 本机 Windows WLAN 可收到一台小米电视的 SSDP 回复，当前 WSL NAT、Docker Desktop 桥接容器和临时 host 容器均未收到。固定 UDP 端口映射也未解决。该容器可读取电视描述并调用控制接口，故本机已定位的缺口是自动发现；不能由此推断 NAS 或其他电视的表现。
- 使用同一 Docker 网络中的临时 jzmedia 实例、隔离数据库和 15 秒合成 MP4，手动注册该小米电视后，媒体接口收到 Range 请求并返回 HTTP 206，电视上报 `PLAYING` 和约 3 秒进度，停止命令成功。未观察到电视实际画面或声音；未验收长时间播放、真实片源兼容性和另一台 Redmi 电视。
- 临时实例与隔离数据已清理。当前本机 `58080` 容器仍是先前镜像，尚未包含本阶段代码；NAS 未更新。部署前需重新构建镜像并按实际环境核对网络和代理。

本机电视地址、容器网络与代理测试的易变细节只记录在本机忽略的 `docs/private/project-memory.md`，重新测试时以现场状态为准。

## 后续实现路径

**优先验证 Android 手机发现。** 在手机原生代码中由用户触发 SSDP M-SEARCH，接收电视的单播回复，提取 `LOCATION` 并交给现有手动注册 API 验证；成功后在投屏面板显示设备。Android 提供 UDP socket；目标系统版本对应的局域网权限需在实现时核对，搜索结束及时释放网络资源。[Android DatagramSocket](https://developer.android.com/reference/java/net/DatagramSocket)、[局域网权限](https://developer.android.com/privacy-and-security/local-network-permission)

先用最小 Android 验证程序确认受测手机能发现两台电视并上报设备；如要在手机网页里点击“投屏”即完成搜索，需要将网页与原生发现能力集成到同一手机应用，或提供明确的跨应用交接。普通网页及添加到主屏幕的 PWA 不具备这项 UDP 能力。客厅 Redmi 电视是否响应 DLNA MediaRenderer 搜索尚未验证；若只支持其他协议，应先识别其协议，再决定是否扩展接收端类型。

手机上报只能补足发现。服务器仍须能通过 HTTP 连接电视，电视也须能访问服务器的 `/api/cast/media/{sid}` 地址。NAS 的物理网络位置、到电视的路由、电视到 NAS 的取流以及 host 模式下的 TMDB 代理均待单独验证；本机 Docker Desktop 的 host 模式失败不代表原生 Linux NAS 的结果。

## 重新启动时的验收条件

1. 在真实 Android 手机上点击投屏，自动发现受测电视并显示可辨认的名称；处理无权限、离开 Wi-Fi、重复回复、设备离线和描述端口变化，不要求用户填写地址。
2. 后端继续验证收到的描述地址和 DLNA 服务，遵守写请求令牌；搜索失败、设备不支持及服务器无法连接电视时，给出可恢复的提示。现有手动入口仍可用于排查。
3. 用隔离合成片源在目标部署中验证电视屏幕画面和声音、HTTP Range 取流、暂停/跳转/停止及一段持续播放；记录设备、网络、服务版本和真实观察结果。
4. 若目标为 NAS，分别验证 NAS 到电视的控制连接、电视到 NAS 的媒体连接和 TMDB 代理请求，再决定是否切换 host 网络。不要把手机搜到设备视为 NAS 可投屏的证据。

本阶段后端投屏定向测试、前端测试与浏览器冒烟已通过。后续实现不设发布日期；继续开发时按改动范围重新回归。
