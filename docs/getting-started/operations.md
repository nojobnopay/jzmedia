# 升级、备份与迁移

[本目录首页](README.md)

## 停止与重启

```bash
docker compose -f docker-compose.yml stop
docker compose -f docker-compose.yml up -d
docker compose -f docker-compose.yml logs -f mymedia
```

`down` 删除容器和项目网络，绑定到宿主的媒体与数据目录保留。宿主直跑在启动终端按 Ctrl+C；只结束自己启动的服务进程。服务正常退出会回收转码子进程，正在运行的内存任务状态不会保留。

## 要备份什么

| 内容 | 作用 | 能否重建 |
|---|---|---|
| 数据目录中的 `jzmedia.db` | 库配置、匹配、评分、标签、观看进度、整理审计等 | 部分元数据可重扫，个人记录和审计不能靠重扫完整恢复 |
| `secret.key` | 解密库中远程凭据 | 遗失后需重新录入相关凭据 |
| `.env`、Compose 定制 | 环境及卷/设备配置 | 需自行保存；可能含秘密 |
| `fonts/` | 自行投放的字幕字体 | 从原来源重新投放 |
| `posters/` | 图片缓存及已选择图片 | 可能需要网络重取，建议备份 |
| `transcode/`、`previews/` | 转码与进度缩略图缓存 | 可重新生成，耗时且占空间 |
| 媒体目录中的视频、字幕、NFO、图片 | 原始媒体及落盘元数据 | 数据库备份不包含这些内容 |

数据目录下的 `mounts/` 可能是远程挂载，不应把它当作普通缓存递归打包，否则可能把整个远程媒体库纳入备份。

## 一次一致的备份

最易核对的方式是停止应用后复制数据库及相关配置。下面仅适用于默认 `./data`，使用其他 `DATA_HOST_PATH` 时替换目录：

```bash
docker compose -f docker-compose.yml stop
mkdir -p backups
tar --exclude='./mounts' --exclude='./transcode' --exclude='./previews' -czf "backups/jzmedia-data-$(date +%Y%m%d-%H%M%S).tar.gz" -C ./data .
docker compose -f docker-compose.yml up -d
```

另行安全保存 `.env` 和自定义 Compose 文件。SQLite 使用 WAL，应用运行时只复制 `jzmedia.db` 可能漏掉未合并事务；在线备份应使用 SQLite 备份接口，不要把单文件热拷贝当作一致备份。

## 升级

1. 停止/等待重要任务，备份数据、配置和旧版本源码或镜像。
2. 阅读目标版本变更及迁移说明，获取对应源码。当前仓库不假定存在公共 `git pull` 地址。
3. 保留 `.env`、数据目录及卷映射，比较新的 `.env.example` 后按需补项。
4. 执行 `docker compose -f docker-compose.yml up --build -d`；宿主方式更新依赖后运行 `./start.sh`。
5. 检查健康接口、媒体库连接、详情及一段视频，确认页面构建已更新。

启动会执行数据库 schema 迁移。降级不能只换旧镜像：旧代码未必支持新 schema，应使用升级前的数据备份与相应版本配套恢复。

## 换机与恢复

停止目标服务，把备份恢复到一个单独、可检查的数据目录，再让新实例挂载该目录。保留 `secret.key`；校正 UID/GID 和硬件设备组。尽量维持容器内路径及媒体目录结构，避免已有相对路径失配。

先检查连接，再扫描/校验存在性。不要因为暂时离线而删除媒体库重建；删库会删除库记录。不要在恢复前运行文件整理来“修复”路径。

媒体文件恢复和数据库恢复是两条链路。撤销整理依赖原位置空闲、文件存在及审计记录，不等同于备份或回收站。[整理与撤销](../user-guide/organizing.md)。
