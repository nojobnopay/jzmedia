# R14 播放器 UI + 设置页/通用前端 — status: done

## Scope
- `frontend/src/components/PlayerModal.vue`（1853 行逐行；字幕渲染段已在 R13 审，本单元覆盖会话/引擎/控件/全屏/看门狗/遥测/清理）
- `frontend/src/views/Settings.vue`（1060 行）、`clipboard.js`、`router.js`、`App.vue`、`main.js`、`components/Spinner.vue`、`index.html`、`vite.config.js`、`prefs.js`（R04 已审）

## 目标（用户三问）
1. 功能设计是否合理？更好的方案？
2. 代码是否符合要求？废弃代码/漏洞（带 file:line）？
3. 质量差距（P0/P1/P2 分级，带证据）？

## 进度 Log（倒序追加）
- 2026-09-16 done：PlayerModal 非字幕段 + Settings.vue 全文 + 通用前端文件精读；核对生命周期清理/confirm 流/全局样式。
- 2026-09-16 doing：开始。

## 结论

### 1. 功能设计

**整体判断：播放器 UI 的工程投入显著高于同类自用项目**——自绘控件条（避免原生控件遮挡画面/字幕）、三级恢复（重挂 playlist → 换 video 元素 → 停止并提示）、冻结帧过渡、seek=关旧开新 + `seekPending` 看门狗、`reloadGen` 防并发 reload、设置弹层 Teleport 进全屏、复制直链兜底输入框、调试快照一键复制。`onUnmounted` 清理完整（`PlayerModal.vue:1721-1736`：saveNow/closeSession/unbind/destroy 全部渲染器/定时器/监听）。设置页的危险操作几乎都有两步确认（清配置/清理脏行/归位/整片删除/恢复），整理与恢复执行时服务端重新规划（不信任预览列表）——设计正确。问题：

- **D1（P2）首屏批量重负载**：`Settings.vue` `onMounted` 串行发起 6 个重请求（settings→stats→fs→organize 预览→missing→unmatched，`Settings.vue:972-979`），其中 stats/missing/unmatched/orgPreview 都是全表扫描（R02/R09 已记）。大库设置页首开可能数十秒。建议 `Promise.all` 并行 + 折叠区懒加载（IntersectionObserver 已有，可直接复用触发）。
- **D2（P2）文件浏览器改名/移动直接执行（无预览）**：`doFsRename`/`doFsMove` 传 `dry_run:false`（`Settings.vue:898,917`），而同页删除有 `requires_confirm` 两步。改名/移动虽可逆，但批量误操作成本高；建议统一先 `dry_run:true` 展示计划再确认（`/api/fs/*` 已支持）。
- **D3（P2）关闭页面时最后一次进度可能丢失**：`saveNow` 用 `api()`（fetch），`beforeunload`/`onUnmounted` 触发后浏览器可能取消请求；已有 10s 周期自动保存兜底（`PlayerModal.vue:1270`），最坏丢 10s。可选优化：`fetch(..., {keepalive:true})` 或 `navigator.sendBeacon`。
- **D4（P2）调试口挂全局**：`window.__jzPlayerDebug/__jzHls/__jzAss/__jzPgs`（`PlayerModal.vue:1504-1509`，caps.js 的 `window.__jzCaps` 同理）——自用调试友好，但属于隐式全局契约，发版前建议注释说明或收进一个 `window.__jz` 命名空间。
- **D5（P2）无路由懒加载/无 404 路由**：`router.js:9-18` 全静态 import、无 catch-all；未知路径渲染空 `<router-view>`（App 仍显示导航）。建议 `component: () => import(...)` 按路由分包（PlayModal/hls 已懒加载，但 caps/subStyle/各 View 仍在主包），并加 `{ path: '/:pathMatch(.*)*', redirect: '/' }`。

### 2. 代码符合度（废弃代码/漏洞）

- **B1（P2）`bindVideo`/`unbindVideo` 不对称**：绑定处有 8 个匿名监听（waiting/stalled/seeking/seeked/emptied/suspend/abort/canplay，`PlayerModal.vue:1594-1601`），解绑处只移除 5 个具名（`1605-1616`）。当前元素在恢复/卸载时被整体丢弃，无实际泄漏；但若未来复用同一元素或原地重绑会残留监听，应收敛为具名 handler 成对增删。
- **B2（P2）全屏点击关闭设置弹层的判定用 `closest('.pd-setwrap')`**（`PlayerModal.vue:214-219`）：Teleport 到全屏顶栏的元素仍包在 `.pd-setwrap` 内，行为正确；若未来把弹层 Teleport 到更外层会失效。注释已说明宿主设计，可接受。
- **B3（P2）`App.vue` 重复样式**：`.card img` 定义两次（`App.vue:20,30`）；全局变量（颜色/圆角）大量硬编码 hex 分散在 App 与各 view，主题化/暗色调整成本高。建议至少去重并把主色（`#e50914`）抽 CSS 变量。
- **B4（P2）console 残留**：全前端 3 处 `console.warn`（`Person.vue:96`、`PlayerModal.vue:454,1691`）；其余静默 `catch {}`。统一走 `logEvt`/可控日志开关更一致（与 R07 B4 合并）。
- **B5（P2）`index.html` 无 CSP/安全元标签**：配合 R01 B8（后端无安全响应头），当前 XSS 面小（无 v-html、文本均 textContent/插值），但建议补 `Content-Security-Policy`（或至少 `X-Content-Type-Options`）作为纵深防御。
- **B6（P2）可访问性欠账**：海报/演员图无 `alt`（`Library.vue:108`、`Detail.vue:87`、`Person.vue:33`），多选圆点有 `aria-label`（好），弹层无 `role="dialog"`/焦点陷阱（键盘 Tab 可跑出弹层）。自用项目可接受，属量产化差距。

### 3. 代码质量差距（不符合量产要求）

- **Q1（P2）PlayerModal 1853 行**：引擎会话（~350 行）、看门狗/恢复（~150）、字幕三渲染器（R13 已记 ~600）、控件/全屏/键盘（~250）、遥测/调试（~200）。建议拆 `useHlsEngine()`/`useWatchdog()`/`useSubtitles()`/`PlayerControls` 组件；当前单文件改动风险高（本次评审即发现异步竞态需要逐条推演才能确认正确）。
- **Q2（P2）设置页 1060 行 + 四个独立功能域**（TMDB/入库维护/匹配确认/元数据/显示/文件浏览/整理/恢复）。建议拆子组件（`TmdbConfig`、`FsBrowser`、`OrganizePanel`、`RestorePanel`），每个带自己的 preview/confirm 状态，减少跨域 `busy` 字符串耦合（`busy` 单值在 12 处使用）。
- **Q3（P2）前端零测试/零 lint/零类型**（`package.json` 仅 dev/build；R04 Q6、R11 Q2、R13 Q4 均已记）。全前端共 6 个纯逻辑模块（`ratings/subStyle/caps/prefs/clipboard/api`）与字幕解析/命中等核心函数值得最简单测（vitest 或 node:test，无框架依赖也可先上 node 断言脚本）；至少应加 eslint 防 `console`/未用变量类问题。
- **Q4（P2）错误提示直出 `e.message`（含 HTTP body）** 在 Settings/Detail/Player 多处重复出现（R05 Q6 已记），长正文会撑破布局；建议 `api()` 统一截断并附状态码语义。
- **Q5（P2）两套样式体系**（App.vue 全局 + 各 view scoped + PlayerModal 第二全局块）无文档约定；新增组件容易踩“JS 动态元素吃不到 scoped”（R13 已按全局块解决）。建议在 AGENTS 或组件头部注明约定（当前 PlayerModal:1827 注释做到了，值得推广）。

### 交叉引用
- 字幕渲染段的结论（含 P1 HDR+burn tonemap）归 `13-R13-subtitles.md`；本单元只覆盖非字幕 UI。
- 会话生命周期/心跳/TTL 的前后端契约归 `12-R12-stream-session.md`（本单元确认前端 ping 10s、关闭即 DELETE、`watchStall` 阈值与 R12 D8 的 600s idle 相容）。
- Settings 的整理/恢复/清理交互归 `09-R09-files.md`；上传交互在 Library/Detail（R04/R05）。
- `prefs.js`/`Spinner.vue`/`main.js` 无问题（R04 已审 prefs）。
