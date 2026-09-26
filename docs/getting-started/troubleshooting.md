# 部署排障

[本目录首页](README.md)

| 现象 | 按顺序检查 |
|---|---|
| 无法访问网页 | Compose `ps`、服务日志、端口映射、服务器地址、宿主防火墙；服务器外访问不要使用 localhost |
| 启动时报 Permission denied | `.env` 的 UID/GID、数据目录属主/ACL、媒体可读性；不要把宿主路径填成容器路径或反之 |
| 端口被占 | Linux/WSL 用 `ss -ltnp` 查看；改 APP_PORT 并重建容器，或明确结束占用端口的自有进程 |
| 页面还是旧界面 | Docker 重新构建镜像并创建容器；宿主执行 `npm run build --prefix frontend`；再刷新浏览器，确认访问正确端口 |
| Docker 构建/拉取超时 | 区分基础镜像拉取、pip/npm 下载、运行时 TMDB 三条网络路径；BUILD_HTTP_PROXY 只影响镜像构建中的下载步骤 |
| TMDB 401 / 无候选 | 设置页检查凭据来源，数据库覆盖是否还在；Read Token/API Key 是否有效；测试连接；查看来源冷却状态 |
| SMB 连不上 | 媒体库「连接」或新建时测试，核对共享名、帐号、权限及连接地址；SMB direct 无需 mount 权限 |
| NFS 或强制挂载失败 | 检查宿主挂载能力及容器权限；可先在宿主完成挂载再以本地路径建库 |
| health 为 degraded | 查看 JSON 的 db/media 子项；修复实际失败点。SMB 直读连接需再通过媒体库连接检查确认 |
| 视频不能播放 | health 的 ffmpeg/ffprobe、播放器的播放信息、服务器日志；详见播放器排障 |
| 硬件后端显示 software | 设备映射、组权限、驱动和编码支持；auto 回落软件属于允许行为，不代表硬件已经可用 |
| 写操作提示鉴权失败 | 设置正确访问令牌；GET 能打开不表示写令牌有效 |


### WSL / Docker Desktop 代理故障

若宿主 `curl` 正常，而 Docker 拉基础镜像报 HTTPS proxy 或 timeout，先区分 **Docker daemon 拉镜像** 与 **镜像构建中的 pip/npm 下载**。前者需要修复 Docker Desktop 的代理配置；`BUILD_HTTP_PROXY` 只传入构建阶段，不能修复 daemon 拉取。

已有 `crane` 且可通过可用代理访问镜像仓库时，也可以在宿主拉取所需基础镜像并导入 Docker 后重试构建，例如：

```bash
crane pull --platform linux/amd64 python:3.12-slim /tmp/jzmedia-python-3.12.tar
docker load -i /tmp/jzmedia-python-3.12.tar
```

这只是针对对应架构和镜像的应急方法；Dockerfile 中其他基础镜像仍需可获取。构建期下载超时则在 `.env` 设置可从容器访问的 `BUILD_HTTP_PROXY`。运行时 TMDB 请求单独使用 `TMDB_PROXY`，不要混用三种代理配置。

提交问题时提供应用版本、部署方式、脱敏错误信息和可复现步骤。播放问题可复制播放器诊断信息；分享前检查文件名、路径或网络信息。不要贴 `.env`、访问令牌、SMB 密码或数据库。

继续查阅 [播放器手册](../user-guide/player.md) 与 [功能排障](../user-guide/troubleshooting.md)。
