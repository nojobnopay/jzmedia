---
version: 0.20.0
reviewed: 2026-10-04
---

# 升级、备份与迁移

<span id="升级备份与迁移"></span>

[本目录首页](README.md)

按任务阅读：[备份](#一次一致的备份) · [升级](#升级) · [换机与恢复](#换机与恢复)。命令从项目根目录执行；下面以 Docker Compose 和默认数据目录 `./data` 为例。

## 停止与重启

| 要做的事 | 命令 |
|---|---|
| 停止服务 | `docker compose -f docker-compose.yml stop` |
| 重新启动 | `docker compose -f docker-compose.yml up -d` |
| 持续查看日志 | `docker compose -f docker-compose.yml logs -f mymedia` |

以上命令按需选择；查看日志时按 `Ctrl+C` 只退出日志跟踪。

`down` 删除容器和项目网络，绑定到宿主的媒体与数据目录保留。宿主直跑在启动终端按 Ctrl+C；只结束自己启动的服务进程。服务正常退出会回收转码子进程，正在运行的内存任务状态不会保留。

## 要备份什么

准备备份或大批转码前，可查询 `/api/health` 的 `disks`：`data` 与 `transcode` 引用 `filesystems` 中的容量，`available_bytes` 为应用可用字节数。`same_filesystem:true` 表示两者共用空间，不能将容量相加；`disks.ok:false` 时先排查对应目录的错误。容器内看到的是挂载文件系统，不一定对应宿主的一整块物理磁盘。字段与限制见[健康与磁盘容量](../developer/api.md#健康与磁盘容量)。

| 内容 | 保存建议 | 主要用途 |
|---|---|---|
| `jzmedia.db` | 必须备份 | 库配置、匹配与个人记录 |
| `secret.key` | 与数据库一起保存 | 解密远程凭据 |
| `.env`、自定义 Compose | 另行安全保存 | 环境、卷与设备配置 |
| `fonts/` | 保留字体来源或备份 | 自行投放的字幕字体 |
| `posters/` | 建议备份 | 图片缓存与已选图片 |
| `transcode/`、`previews/` | 通常可排除 | 可重新生成的播放缓存 |
| 视频、字幕、NFO、图片 | 另做媒体备份 | 原始媒体及落盘元数据 |

数据库还保存评分、标签、观看进度、整理与归属历史、文件待扫描记录和智能配置/用量。重扫只能重建部分元数据，不能完整恢复个人记录和审计。`secret.key` 遗失后需重新录入相关凭据；图片重取可能依赖网络。`.env` 和 Compose 定制可能包含秘密，应与数据库一样妥善保管。

数据目录下的 `mounts/` 可能是远程挂载，不应把它当作普通缓存递归打包，否则可能把整个远程媒体库纳入备份。

## 一次一致的备份

最易核对的方式是停止应用后复制数据库及相关配置。下面仅适用于默认 `./data`，使用其他 `DATA_HOST_PATH` 时替换目录：

### Step 1：停止服务并打包数据

等待重要任务结束后执行：

```bash
docker compose -f docker-compose.yml stop
mkdir -p backups
tar --exclude='./mounts' --exclude='./transcode' --exclude='./previews' -czf "backups/jzmedia-data-$(date +%Y%m%d-%H%M%S).tar.gz" -C ./data .
```

**预期结果：**命令成功退出，`backups/` 下生成带日期与时间的压缩包，包含数据库及数据目录中的相关文件。若打包失败，先检查目录权限和可用空间，修复后重新执行打包命令。

### Step 2：保存配置并重新启动

另行安全保存 `.env` 和自定义 Compose 文件；数据库备份不包含媒体目录。确认备份文件已经保存后重新启动：

```bash
docker compose -f docker-compose.yml up -d
```

**预期结果：**首页和 `/api/health` 恢复可用。按下文的[恢复检查](#换机与恢复)验证备份副本，不覆盖现用数据。

### 一致性说明

SQLite 使用 WAL，应用运行时只复制 `jzmedia.db` 可能漏掉未合并事务；在线备份应使用 SQLite 备份接口，不要把单文件热拷贝当作一致备份。

## 升级

1. 停止/等待重要任务，备份数据、配置和旧版本源码或镜像。
2. 阅读目标版本变更及迁移说明，获取对应源码。当前仓库不假定存在公共 `git pull` 地址。
3. 保留 `.env`、数据目录及卷映射，比较新的 `.env.example` 后按需补项。已入库路径为 `/media` 的容器，先按[旧路径兼容说明](deployment.md#legacy-media-path)保留原映射。
4. 默认映射执行 `docker compose -f docker-compose.yml up --build -d`。使用旧路径兼容或其他 Compose 覆盖文件时，必须带上配置时相同的全部 `-f` 参数；旧 `/media` 部署使用[兼容说明中的启动命令](deployment.md#legacy-media-path)。宿主方式更新依赖后运行 `./start.sh`。
5. 检查健康接口、媒体库连接、详情及一段视频，确认页面构建已更新。

启动会执行数据库 schema 迁移；服务端版本与 schema 各自维护，当前迁移规则见[数据模型](../developer/data.md)。旧探测缓存会按 probe 版本自动更新。

降级不能只换旧镜像：旧代码未必支持新 schema，应使用升级前的数据备份与相应版本配套恢复。Android TV APK 与镜像统一版本、同步构建，但可分别安装升级；连接时继续通过客户端握手确认协议与可选功能。构建与追溯见[统一发布](../developer/releasing.md)。

## 换机与恢复

### Step 1：解压并检查备份副本

停止目标服务。先把备份恢复到单独目录，确认可用后再让新实例挂载。保留 `secret.key`；尽量维持容器内路径及媒体目录结构，避免已有相对路径失配。

先把备份解到新的临时目录，用只读 SQLite 连接检查副本结构；命令中的备份名替换为实际文件：

```bash
RESTORE_CHECK_DIR=$(mktemp -d /tmp/jzmedia-restore-check.XXXXXX)
tar -xzf backups/jzmedia-data-20261003-120000.tar.gz -C "$RESTORE_CHECK_DIR"
python3 - "$RESTORE_CHECK_DIR/jzmedia.db" <<'PY'
from pathlib import Path
import sqlite3
import sys

database = Path(sys.argv[1]).resolve().as_uri() + "?mode=ro"
with sqlite3.connect(database, uri=True) as connection:
    print("数据库完整性：", connection.execute("PRAGMA integrity_check").fetchall())
    print("schema：", connection.execute("PRAGMA user_version").fetchone()[0])
PY
```

**预期结果：**完整性检查只返回 `ok`；它不验证媒体文件是否齐全。若报错，保留原备份与现用数据，检查备份是否完整，不要继续覆盖现用目录。

### Step 2：配置恢复实例

把检查通过的副本放入目标数据目录，配置数据卷并校正 UID/GID 和硬件设备组。

仅做恢复演练时，使用独立 Compose 项目名、端口和数据目录。复制出来的 DB 仍保存原 SMB/NFS 连接和媒体路径，仅换数据目录并不能隔离真实媒体；先在隔离测试机或容器网络/挂载边界中限制原存储访问，再启动并核对界面。不要复用生产项目直接 `up` 来做演练。

### Step 3：检查连接与实际内容

先检查连接，再扫描/校验存在性。不要因为暂时离线而删除媒体库重建；删库会删除库记录。不要在恢复前运行文件整理来“修复”路径。

**预期结果：**库配置、匹配与个人记录可见，媒体连接成功，影片详情指向正确文件并可播放。

### 恢复范围

媒体文件恢复和数据库恢复是两条链路。撤销整理依赖原位置空闲、文件存在及审计记录，不等同于备份或回收站。[整理与撤销](../user-guide/organizing.md)。
