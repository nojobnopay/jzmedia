# R11 播放决策（probe/caps/plan/后端/HDR） — status: done

## Scope
- `app/media.py`（398 行逐行）、`app/caps.py`（67）、`app/playback.py`（621）、`app/transcode.py`（148）
- `frontend/src/caps.js`（141）
- 交叉：`routers/stream.py` 的调用面（归 R12）、`caps.default_caps()` 消费方（R12/R13）

## 目标（用户三问）
1. 功能设计是否合理？更好的方案？
2. 代码是否符合要求？废弃代码/漏洞（带 file:line）？
3. 质量差距（P0/P1/P2 分级，带证据）？

## 进度 Log（倒序追加）
- 2026-09-16 done：四后端文件 + caps.js 精读；核对命令构造/能力契约/探针索引；本机无 ffmpeg（宿主未装、static 未下载），无法跑命令级实验，相关结论均标注“代码路径确认”。
- 2026-09-16 doing：开始。

## 结论

### 1. 功能设计

**整体判断：这是全项目工程化程度最高的模块。** 四档决策 + reasons 人话映射 + 逐片码串实测回传（`caps.probes` 三态） + probe_ver 自愈 + 可重试错误不落库 + fMP4 单进程多音轨 + 关键帧对齐（`media_start`）+ 后端冒烟探测（不只看设备节点）+ 硬件失败软件重试——设计质量高。问题：

- **D1（P2，可复现的输出浪费）显式档位会向上放大分辨率。** `_target_height` 对 `1080p/720p` 直接返回目标值（`playback.py:228-231`），`downgrade` 只判 `height > target`（`299`），需要重编时（编码不支持等）会把 480p 源放大到 720p/1080p 再编码（`video_args` 按 height 缩放，`transcode.py:57-58`），浪费 CPU 且画质无增益。修复：`target_height = min(target, source_height)`（source 未知时保持）。
- **D2（P2）`caps.mse=false` 无处理。** `normalize_caps` 接受并归一 `mse`（`caps.py:42`），但 backend 与前端全库无消费（已 grep 确认），此时仍可能返回 `remux/audio_transcode/video_transcode`（HLS 方法），无 MSE 的浏览器必然播不了。建议：`mse=False` 且非 kodi/非 native_hls 时强制 `direct` 或 `blocked` + reason。
- **D3（P2）`MAX_AUDIO_RENDITIONS=8` 与 `audio_idx` 选择不一致。** `audio_variants` 截断为前 8 条（`playback.py:133`），但 `plan` 的 `want_audio`/`a_ok` 按用户选择（可 >8），fMP4 命令又只 map 前 8 条（`playback.py:526-527`）→ 用户选第 9 条音轨时会话里没有该音轨，前端无从切换。`plan` 应把选择钳到前 8 或显式提示。
- **D4（P2）HDR 直通与 tonemap 的可用性依赖 `hw_backend()`，首次调用可能阻塞。** `_target_height`/`hw_can_tonemap` 都能触发 `transcode.detect()`（`playback.py:239,72`），冒烟探测最坏 3×15s；虽有启动线程预热（`main.py:19`），但首个 `/decide` 与探测竞争时仍会等锁。建议缩短 smoke 超时（0.2s 素材 2-3s 足够）并把 `hw_backend()` 结果在 plan 里只取一次。
- **D5（P2）DV P5 无兼容基底时启用 tonemap 属于“错误色彩但能播”。** `_hdr_blocks_direct` 对 compat=0 返回 `dovi_not_supported` + 需 tonemap（`playback.py:177`），VAAPI/QSV 的 tonemap 只按 HDR10 处理 IPT 基底（P5 无基底）会偏色；文档已提示“色彩偏灰”。产品取舍可接受，建议 reason 里区分 `dovi_no_base_tonemap` 让前端提示更准确。
- **D6（P2）软编档位偏保守但不自适应码率。** `video_args` 软编固定 `-crf 23 -preset veryfast`（`transcode.py:55-56`）；NVENC 无 `-cq/-b:v`（默认码率策略可能偏大/偏小，`transcode.py:75`）；QSV 无码率参数（默认可能质量偏低）。目标文档未约束码率策略，属可优化项；建议统一目标码率或质量参数（软编 crf / nvenc cq / qsv global_quality）并允许 env 覆盖。
- **D7（P2）NVENC 不支持 tonemap 的提示路径只在有 `hdr_tonemap` 时生效**（`playback.py:315-320`）：`hdr_no_tonemap` reason + 直接软编（`tonemap=False`）会让 HDR 内容以 HDR 编码（软编也保留 HDR？软编 libx264 保留 color_transfer，浏览器 SDR 屏显示偏灰）——与提示“色彩偏灰”一致；OK。

### 2. 代码符合度（废弃代码/漏洞）

- **B1（P2）探针索引契约需前后端对齐（关键正确性面）。** `media.probe` 给音轨/字幕写入 `index`（过滤列表序号）与 `ff_index`（媒体流序号，`media.py:279,290`）；`playback` 里 `audio_map` 用过滤序号（`a:{i}`）、烧录用 `sub_ff_index`。R12/R13 将确认前端 `sub_idx` 的语义与 `_sub_list` 合并后（内嵌+外挂）的序号是否一致——**若不一致会烧错轨/抽错轨**，属高危交叉点，已在 R13 列为必查。
- **B2（P2）`_static_bins_if_present` 有一段死路径。** `media.py:32-34` 先拼死 `.venv/lib/python3.12/site-packages`，随后 `39-42` 用包内实际路径覆盖；首段永远无效（且硬编码 Python 3.12）。建议删首段。
- **B3（P2）`audio_caps()` 不做 `norm_codec`**（`media.py:361-363`），调用方 `decorate` 已归一，但公共函数自身脆弱：`audio_caps("AAC")` 返回空。建议函数内归一。
- **B4（P2）封面色图流判定不完全。** `probe` 跳过 `png/mjpeg/bmp` 仅当 `len(videos)>1`（`media.py:229-230`）；仅有封面图的文件会被当成视频（width/height 为图片尺寸）并 `playable=True`。建议加 `disposition.attached_pic` 判断（ffprobe 会带）。
- **B5（P2）caps 输入校验偏宽。** `_bools` 用 `bool(src.get(k))`（`caps.py:27-29`）：字符串 `"false"` → True；`probes` 值同样 `bool(v)`（`caps.py:38`）。恶意/异常客户端可伪造能力（自用场景风险低），建议只认 `is True`/`is False`。
- **B6（P2）`caps_hash` 用 SHA-1 截断 12 hex**（`caps.py:47-53`）：非安全用途，但 `sha1` 在现代规范里应换成 `blake2b(digest_size=8)` 之类的非加密摘要以避免安全扫描告警。P2。
- **B7（P2）`_build_cmd_fmp4` 中 `variants` 空时 `-c:a` 相关参数全部省略**（正确），但 `-var_stream_map "v:0,name:video"` 仍是合法单视频 rendition；master CODECS 由 `stream._write_master` 生成，需 R12 确认对“无音轨/全 copy”不产 CODECS 的分支处理正确。
- **B8（P2）`media.probe` 的 `vbitrate` 混用格式总码率与视频流码率**（`media.py:220,264`）：初始取 format.bit_rate（含音频），视频流有值才覆盖。下游若用 vbitrate 判断“高码率直通”会偏差；建议字段分离或只取视频流。
- **B9（P2）`transcode.Backend.video_args` 的 QSV/VAAPI 滤镜串用 Python 拼字符串**（`transcode.py:60-73`）：参数来自内部常量，无注入面；但滤镜串不可测（无单测）。建议抽成纯函数返回 list（同 `build_cmd` 的测试缺口）。
- **B10（P2）安全面：`build_cmd` 的命令不经 shell（list 形式）**，路径已 `abspath`；无 `shell=True`、无用户输入拼参数（quality 走白名单、height 为 int），未发现注入漏洞。`_sub_overlay_filter` 的 `int(plan["sub_ff_index"])` 有 KeyError 风险（`sub` 为 burn 且内嵌时必须带 `sub_ff_index`，由 stream 层保证，R13 确认）。

### 3. 代码质量差距（不符合量产要求）

- **Q1（P2）`playback.py` 621 行内混合“决策矩阵 / 打分 / 关键帧探测 / 命令构造（fMP4+TS 两套）”，`plan` 100 行、`_build_cmd_*` 各 70 行**；另外 fMP4 与 TS 两个 build 函数有大量重复（seek/genpts/编码器/映射）。建议抽 `cmd_common` 并参数化封装；长期目标是把决策表变成数据驱动（便于加新 codec）。
- **Q2（P2）决策矩阵与命令构造无测试，且高度依赖真机行为**（EAC3 无声、EVENT 列表 bug、`cwd` 约束）。这些“实测知识”目前只在注释里（很好），但无法防回归；建议至少把 `plan()` 做成纯函数表驱动测试（media/caps 字典 → method），覆盖文档 §12 的 HDR/DV 矩阵全部组合。
- **Q3（P2）`transcode.py` 的探测结果在进程内缓存且无失败原因持久化**：`backend_info` 返回 reason（`transcode.py:144-148`），但探测失败后不会自动重探（除非 `refresh=1`）。NAS 上插拔设备后需手动刷新；建议加低频后台重探或在 `/api/health` 提示。
- **Q4（P2）`media.PROBE_VERSION=3` 的自愈机制正确**（`store.upsert_media_info` + `probe_ver` 比较），但版本号与 `main.py` 应用版本解耦，注释里只记录了 v3 变更；建议维护一个简短 CHANGELOG 注释块（当前仅一行）。
- **Q5（P2）caps 24h 缓存 + `envKey`（UA+动态范围）**（`caps.js:118-138`）合理；但 `sessionStorage` 在标签页间不共享、`basePromise` 进程内单次——同一浏览器多标签会各自实测（开销小）。可接受。

### 交叉引用
- `stream.py` 如何使用 `plan()`/`build_cmd()`/`audio_variants`、`media_start` 与字幕对齐、会话复用键是否包含 `seg`——全部归 R12（本单元已在计划中核对接口假设，未发现契约冲突）。
- 字幕 `sub_idx` 语义与 `_sub_list`（内嵌+外挂）合并后的索引空间一致性——R13 必查（B1）。
- `AUDIO_COPY_SAFE`/`AUDIO_COPY_SAFE_NATIVE` 的 env 读取（`_env_set`）每次调用都读环境变量（`playback.py:41-45`），热路径小开销；文档一致性归 R12/README 复核。
- 本机无 ffmpeg/ffprobe（宿主直跑未下载 static 包），R11 未能做命令级验证；R12/R13 的 ffmpeg 行为类结论将基于代码与文档，标注“未实测”。
