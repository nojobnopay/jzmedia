---
version: 0.19.0
reviewed: 2026-10-03
---

# 开发与验证

[开发者文档](README.md)

## 环境

依赖安装、路径配置与后端启动统一见 [宿主直接运行](../getting-started/deployment.md#宿主直接运行)；Docker 方式见同页 [Docker Compose](../getting-started/deployment.md#docker-compose)。

前端开发服务器在 `frontend/` 执行 `npm run dev`，监听 5173，将 `/api`、`/posters`、`/help` 代理到 8080；后端服务仍须运行。帮助站独立开发与构建见[文档制作与发布](documentation.md#构建与检查)。Docker 本机热重载 override 文件不适用于 NAS 部署。

## 修改功能的最短路径

1. 查 [模块划分](modules.md) 找拥有该逻辑的层；先确认接口/数据模型是否已有相近字段。
2. 对纯解析、计划和规则写有区分度的测试；对存储/数据库变更测试真实迁移、只读、目标冲突与范围。
3. 涉及媒体文件的测试使用临时目录和合成片源，不在开发期对真实媒体库执行整理或删除。
4. 修改前后端契约时同步请求模型、页面状态、异常路径及对应文档；播放改动检查元素销毁、会话晚到、字幕时间轴与 HLS 缓存。
5. 在提交前运行适当测试；每项功能形成一条可独立审阅的本地提交。仓库目前没有 CI。

## 验证命令

以下命令均从仓库根目录执行。

### 后端与前端

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m pyflakes app
python3 scripts/build_design.py --check
npm test --prefix frontend
npm run lint --prefix frontend
npm run build --prefix frontend
```

后端测试包含 schema 迁移、多库隔离、SMB、扫描、整理、外源、Android 原生能力及多客户端播放会话；前端测试包含纯函数、模板绑定和字幕 composable 初始化。`tests/conftest.py` 在导入应用前设置临时数据/媒体目录；`pytest.ini` 每例超时 120 秒，线程堆栈兜底 240 秒。改动范围小时先运行相关用例，提交前按仓库要求检查全套结果；如果仓库中已有与本次无关的警告/失败，记录基线及差异，不把未通过说成通过。

### 隔离冒烟

辅助冒烟会在隔离临时目录写测试数据，不访问真实片库：

```bash
.venv/bin/python scripts/smoke_multi_library.py
.venv/bin/python scripts/smoke_metadata_offline.py
```

全部维护工具的用途、读写边界与运行前提见仓库 `scripts/README.md`。

### 帮助站与 Android TV

修改帮助站后运行 `npm --prefix docs run verify`，依次完成格式 lint、源链接检查、构建、测试及产物检查，确保测试读取本次构建；局部文档检查见[文档制作与发布](documentation.md#构建与检查)。

Android TV 使用独立 Gradle 工程，先读仓库 `android-tv/AGENTS.md` 与 `android-tv/README.md`，不通过前端 npm 或 `start.sh` 构建 APK。

### 运行诊断

线上诊断从 `/api/health`、`/api/stream/backends`、媒体库 check/diag、任务状态和播放器“复制诊断信息”开始。日志由 `app/log.py` 统一输出。数据库和数据目录都位于 `settings.data_dir`，除测试临时目录外不要在模块导入期创建。

## 文档同步

修改部署变量同步 [配置参考](../getting-started/configuration.md)；改变页面流程同步 [用户手册](../user-guide/README.md)；改变模块/关键不变量同步本目录；计划、完成状态与待验收事项进入仓库 `docs/roadmap/`；阶段性测试总结写入提交或 PR 说明，长期约束并入对应参考文档。根 README 保持入口作用，避免重新堆入完整 API 与历史实施日志。页面、媒体素材与构建规范见[文档制作与发布](documentation.md)。

Agent 文档工作使用仓库 `.agents/skills/jzmedia-docs/SKILL.md`。先判断本次变更影响的正文，按读者用途更新或审核；纯审核只报告问题，不修改文件。仅内部重构且使用方式、接口与约束未变时，无需为了同步而新增文档。
