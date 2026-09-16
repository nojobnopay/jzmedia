# R13 字幕系统（抽取/外挂/VTT/ASS/PGS/字体） — status: done

## Scope
- `app/routers/stream.py` 字幕段：`_sub_list`/`_sub_pick`/`hls_subtitle`/`hls_subtitle_ass`/`hls_subtitle_sup`/`_convert_sidecar`/`_extract_embedded`/`_dump_attachments`/`stream_fonts`/`stream_builtin_font`/`stream_attachment_font` + `_SIDECAR_LANG_HINTS`
- `frontend/src/jassubLoader.js`, `frontend/src/pgsLoader.js`, `frontend/src/subStyle.js`
- `PlayerModal.vue` 字幕渲染段（`subKind/mountAss/mountPgs/mountVttLayer/parseVtt/vttRender/applySubs/shiftSubDelay/fallbackBurnSub/syncSubLayerRect`，行 736-1330 + 样式 1827-1853）；播放器其余 UI 归 R14
- 交叉：`playback._subtitle_mode`（R11）、`scanner.sidecar_subtitles`（R03）、会话 burn 分支（R12）

## 目标（用户三问）
1. 功能设计是否合理？更好的方案？
2. 代码是否符合要求？废弃代码/漏洞（带 file:line）？
3. 质量差距（P0/P1/P2 分级，带证据）？

## 进度 Log（倒序追加）
- 2026-09-16 done：后端字幕/字体接口 + 三个 loader + PlayerModal 字幕段逐行；核对索引契约、XSS 面、时间轴公式、降级链。
- 2026-09-16 doing：开始。

## 结论

### 1. 功能设计

**整体判断：这是项目里设计最完整的子系统之一。** 五档模式（none/webvtt/ass_client/pgs_client/burn）实现与文档一致；文本/ASS/PGS 全部客户端渲染 → 切字幕不重开会话；VobSub 唯一烧录；ASS 无字体+CJK 自动降级 VTT；PGS 解码失败/超时自动降级烧录；外挂字幕一层目录 stem 匹配 + 中文后缀词表自动选默认；延迟/外观/位置按版本持久化；`subStyle.js` 是纯函数模块（可单测，AGENTS 承诺属实）。问题：

- **D1（P1，功能正确性）HDR 源 + 烧录时 tonemap 被静默丢弃，且没有 `hdr_no_tonemap` 提示。** `plan()` 在 `hw_can_tonemap()` 为真时设 `tonemap=True`（`playback.py:315-320`），但烧录强制软件路径，`transcode.Backend.video_args(burn=True, tonemap=True)` 直接返回 libx264 参数并忽略 tonemap（`transcode.py:54-59`）。触发组合：HDR/DV 片 + VobSub（必烧）或 PGS 解码失败降级烧录 + 有 HW 后端（NAS 主流）。结果：画面偏灰却无任何提示（reason 里也不会出现 `hdr_no_tonemap`，因为 HW 存在）。修复二选一：软件 zscale tonemap 链（文档说不作默认能力，那就走第二条）或 burn 且 `tonemap` 时补 `reasons.append("hdr_no_tonemap")`，让前端如实提示。
- **D2（P2）内嵌字幕默认轨不自动选，外挂中文才自动选。** `_sub_list` 对源 disposition 的 `default` 原样透传（`stream.py:1394-1400`），但 `playback.plan` 无 `sub_idx` 时 `subtitle_mode=none`（`playback.py:192-193`），前端只自动选「外挂且有 default」的轨（`PlayerModal.vue:637-641`）。外语片常见“源内嵌中字 default”，用户仍需手动选。建议与音轨口径对齐：无用户选择时自动采用合并清单里首条 default（或首条中文）文本轨，且不触发烧录。
- **D3（P2）ASS 降级判定只嗅探前 40KB。** `assNeedsCjk`（`PlayerModal.vue:828-833`）用 `r.text().slice(0, 40000)` 判断是否需要 CJK —— 抽取的 `.ass` 前面通常是样式/字体段，正文在很后面；若样式段为纯英文但正文是中文，会判“不需要 CJK”而不降级（漏判）；反之如果文件前 40KB 有中文注释则一切正常。低概率但真实。建议用后端标记（`ass` 轨的 lang）或扫描全文（README 已说明该降级仅兜底，可接受；建议加注释）。
- **D4（P2）字幕缓存转换无并发保护/非原子。** `_convert_sidecar`/`_extract_embedded`（`stream.py:1313-1361`）先查 mtime 再 `subprocess.run` 直接写 `dest`；两个客户端同时请求同一轨时并发 ffmpeg 写同一文件，另一个可能读到半成品（`FileResponse` 边写边读）。修复：写 `.tmp` + `os.replace`，或按 dest 加进程内锁（字典锁）。
- **D5（P2）`_dump_attachments` 用 `.dumped` 标记一次性 dump。**（`stream.py:1489-1517`）源文件被替换（同名换版重刮）后字体不刷新；而字幕抽取是按 mtime 失效的，策略不一致。建议标记文件写源 mtime。
- **D6（P2）烧录计划的 `sub_ff_index` 依赖探测缓存；`stream.py:637` 的 `int(track.get("ff_index", si))` 在 ff_index 缺失时回落合并序号**——对“有外挂排在前”的场景不可能（外挂总是排在后面），所以回落是安全的；但建议 `ff_index` 缺失直接 422 而非猜测。

### 2. 代码符合度（废弃代码/漏洞）

- **B1（P2）XSS 面已妥善处理（无漏洞）**：VTT 文本经 `textarea.innerHTML → value` 解实体后以 `textContent` 渲染（`PlayerModal.vue:924-931,1053-1057`），ASS/PGS 走 WASM 渲染不进 DOM；字体名/URL 有 `_FONT_RE` + `basename` 约束。未发现注入点。
- **B2（P2）后端抽取命令注入面已封死**：`ff_idx` 来自探测（int），`si` 来自路径 int 参数，`rel` 来自服务端枚举（`_sidecar_abs` 注释明确“不接受客户端路径”）。`-map 0:{ff_idx}` 无字符串拼接。
- **B3（P2）`hls_subtitle`（VTT）对 `track.get("source") == "sidecar"` 的 VobSub `.sub` 会调 `_convert_sidecar(..., "vtt")` 并可能 500**：正常流程不会走到（`_subtitle_mode` 判 burn），但直接请求该 URL 会触发 ffmpeg 失败 → 500 而非 415。建议按 codec 拒绝（与 `.sup` 口一致）。
- **B4（P2）`_extract_embedded` 的 `dest_ext == "sup"` 用 `-c:s copy`**（`stream.py:1342`）：对非 PGS 图片字幕（如 dvd_subtitle）也会 copy 出 `.sup` 扩展名（内容为 vobsub），但 `.sup` 口已按 codec 拒绝，不可达。无害。
- **B5（P2）`_guess_sidecar_lang` 用子串匹配**（`stream.py:1380-1385`）：`"中"` 会命中任意含“中”的后缀（如 `中文配音版`、`中英特效`），也可能误判 `jpn` 与 `jp`、`en` 与 `eng`（顺序已保证）。实际影响仅是默认轨选择/显示名，低风险。建议按分隔符切分的 token 匹配。
- **B6（P2）`stream_attachment_font` 固定 `media_type="font/ttf"`**（`stream.py:1571`）：otf/ttc/woff2 附件 MIME 不准；JASSUB 用 fetch bytes 无影响，浏览器直开才看得出。P2。
- **B7（P2）`parseVtt` 只支持 `HH:MM:SS.mmm`/`MM:SS.mmm`**（`PlayerModal.vue:896-900`）：服务端 ffmpeg 产出的 WebVTT 均带毫秒，OK；手工外部 VTT（`MM:SS` 无毫秒）会解析失败并回退原生 `<track>`（有兜底），可接受。
- **B8（P2）自绘层样式/外观控件在原生 `<track>` 兜底时不生效但控件仍显示**：`subIsVtt`（`PlayerModal.vue:1084`）只看 kind，不看 `vttNativeFallback`；回退原生轨时“背景/描边/位置/字号”下拉仍出现（改了只影响 ::cue 那点全局样式，位置/字号无效）。P2（微小 UX 错位）。
- **B9（P2）前端 console 调用散落**：`console.warn`（`PlayerModal.vue:1691`、`Person.vue:96`）与 `logEvt` 内可能还有；无统一开关（R07 B4 已记，合并处理）。

### 3. 代码质量差距（不符合量产要求）

- **Q1（P2）PlayerModal 的 1853 行里字幕子系统约 600 行**，与播放/会话/音轨/全屏/遥测同文件；建议抽 `useSubtitles()` composable（或 `SubtitleLayer` 组件），把 VTT/ASS/PGS 三挂载器 + 偏移 + 样式 + 降级链移出。R14 汇总。
- **Q2（P2）`applySubs(force)` 是隐式状态机**：`jassub/pgs/vttLayer` 三个模块级实例 + `assKey/pgsKey/vttKey` + `burnOn/autoVttSub/compatSub/forceBurn` 五个标志，组合分支靠 if 链维护（`PlayerModal.vue:1128-1154`）。行为正确（已逐分支核对），但任何新增渲染器都会指数级复杂化；建议收敛为 `{kind, key}` → renderer 注册表。
- **Q3（P2）降级链无上限/无用户否决**：PGS 失败 → burn（`forceBurn` 粘住整个会话）；ASS 无字体 → VTT（`autoVttSub` 按轨）；再遇异常只能手动勾「兼容」。建议在 `posHint` 外提供“撤销降级”入口（低优先）。
- **Q4（P2）字幕时间轴逻辑（mediaStart/subDelay 叠加）只有注释无测试**；`subStyle.js` 已是纯函数可测，同样可把 `parseVtt`/`vttRender` 的命中判定抽纯函数（输入 currentTime/offsets/cues → 输出 active cue ids）做单测——当前命中判定内联在 DOM 渲染里（`PlayerModal.vue:1038-1058`）。建议优先补（字幕错位是最易复发的一类 bug）。
- **Q5（P2）q/sidecar 的“一层 subs/Subs/字幕 目录”是硬编码**（`scanner.py:24`）：与文档一致，但不可配置。P2 记录。

### 交叉引用
- **索引契约（R11 B1）验证通过**：内嵌 `ff_index`=ffprobe 流号，外挂 `ff_index=None`；merged 序号即前端 `subIdx`/`sub` 参数；后端 `_sub_pick`/`_subtitle_mode`/`_spawn_session` burn 分支三处同源，无错轨风险。
- **时间轴对齐公式验证**：`off = (subShift() + subDelay) * 1000`，`subShift()=direct?0:mediaStart`；`mediaStart` 来自会话响应（copy=前一关键帧），与 `playback.actual_media_start` 一致（`PlayerModal.vue:892-895,1042-1043`；`stream.py:651`）。直连/烧录不平移（烧录在服务端对齐）——符合 AGENTS 描述。
- 外挂匹配（stem + 一层目录 + 后缀词表）归 R03（`scanner.sidecar_subtitles`，已审）；本单元确认消费方式正确。
- 字体投放目录 `data/fonts` 的创建归 R01（`db.ensure_dirs`），TTL 清理经 `_purge_old`（R12）覆盖 `TRANSCODE_DIR/<vid>/fonts|subs`。
- 本机无 ffmpeg，`_convert_sidecar`/`_extract_embedded` 的命令行为未实测（代码路径确认）；字体 dump 路径穿越防护经代码核对（basename+后缀白名单+独立 CWD）。
