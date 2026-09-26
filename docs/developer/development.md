# 开发与验证

[开发者文档](README.md)

## 环境

Docker 路径见 [部署教程](../getting-started/deployment.md)。宿主开发可在项目根目录安装：

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
npm ci --prefix frontend
./start.sh
```

`start.sh` 根据前端源文件时间决定是否重建 `frontend/dist`，按宿主路径使用 `./data`、`./media`，再启动单进程 uvicorn。前端开发服务器可在 `frontend/` 执行 `npm run dev`（5173 代理 API/海报到 8080），后端服务仍须运行。Docker 本机热重载 override 文件不适用于 NAS 部署。

## 修改功能的最短路径

1. 查 [模块划分](modules.md) 找拥有该逻辑的层；先确认接口/数据模型是否已有相近字段。
2. 对纯解析、计划和规则写有区分度的测试；对存储/数据库变更测试真实迁移、只读、目标冲突与范围。
3. 涉及媒体文件的测试使用临时目录和合成片源，不在开发期对真实媒体库执行整理或删除。
4. 修改前后端契约时同步请求模型、页面状态、异常路径及对应文档；播放改动检查元素销毁、会话晚到、字幕时间轴与 HLS 缓存。
5. 在提交前运行适当测试；每项功能形成一条可独立审阅的本地提交。仓库目前没有 CI。

## 验证命令

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m pyflakes app
npm test --prefix frontend
npm run lint --prefix frontend
npm run build --prefix frontend
```

后端测试包含 schema 迁移、多库隔离、SMB、扫描、整理、外源和播放会话；前端测试包含纯函数、模板绑定和字幕 composable 初始化。`pytest.ini` 为每例设置超时。改动范围小时先运行相关用例，提交前按仓库要求检查全套结果；如果仓库中已有与本次无关的警告/失败，记录基线及差异，不把未通过说成通过。

只读、临时目录的辅助冒烟：

```bash
.venv/bin/python scripts/smoke_multi_library.py
.venv/bin/python scripts/smoke_metadata_offline.py
```

线上诊断从 `/api/health`、`/api/stream/backends`、媒体库 check/diag、任务状态和播放器“复制诊断信息”开始。日志由 `app/log.py` 统一输出。数据库和数据目录都位于 `settings.data_dir`，除测试临时目录外不要在模块导入期创建。

## 文档同步

修改部署变量同步 [配置参考](../getting-started/configuration.md)；改变页面流程同步 [用户手册](../user-guide/README.md)；改变模块/关键不变量同步本目录；未实施计划才进入 [开发计划](../roadmap/README.md)。根 README 保持入口作用，避免重新堆入完整 API 与历史实施日志。
