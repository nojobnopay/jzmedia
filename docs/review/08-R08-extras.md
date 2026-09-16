# R08 花絮/样片 extras — status: done

## Scope
- `app/routers/extras.py`（71 行逐行）
- `app/scanner.py` 花絮相关段（已随 R03 精读：`is_sample/is_extra/extra_kind/strip_kind_affix/attribute_extra/KIND_*`）
- 交叉：`files.move_attached_extras`（R09 scope，本单元只审 extras 语义相关面）、`files.clean-sidecars`、Settings.vue 的 extras 段（细节归 R14）

## 目标（用户三问）
1. 功能设计是否合理？更好的方案？
2. 代码是否符合要求？废弃代码/漏洞（带 file:line）？
3. 质量差距（P0/P1/P2 分级，带证据）？

## 进度 Log（倒序追加）
- 2026-09-16 done：extras.py + 花絮归属链 + clean-sidecars 调用面精读。
- 2026-09-16 doing：开始。

## 结论

### 1. 功能设计

**整体判断：花絮分类/归属体系设计完善**——目录优先级 + 文件名关键词 + 种类词剥离 fallback + ±1 年 + 祖先目录名兜底 + orphan 保留待人工认领 + 手工认领不被覆盖 + 样片永不归属 + 重扫自愈，覆盖了真实场景。问题：

- **D1（P1，与 R03 D2 同根）`clean-sidecars` 会误删被误判为样片的正片库行。** `is_sidecar(m["file_path"])`（`files.py:559`）在 `is_sample` 误报（如 `The Sample Movie (2024).mkv`、`Sample.This.2012.mkv`，R03 已实测）时命中，把真实影片行按“历史脏行”删除（文件保留、海报/cache 保留，重扫可恢复，但如果 `is_sample` 不修则重扫会再次跳过——**恢复路径也被切断**）。UI 虽有 dry-run 预览（`Settings.vue:607-612`），但预览行看起来就是“Sample 电影”，用户无法判断是误杀。修复顺序：先修 `_SAMPLE_RE`（R03 D2），再为 `clean-sidecars` 增加“tmdb_id 非空的行不清理/需额外确认”保护。
- **D2（P2）`/api/extras/collect` 无分页/无差量，且 `ids` 类型混杂导致静默不匹配。** `only = set(body.get("ids", []) or []) or None`（`extras.py:45`）：JSON 里传字符串 id 时 `m["id"] not in only` 恒真，用户以为“只整理选中的”实际会整库跑；另外每次都 `list_movies(limit=100000)` + 每片一次 `list_extras_by_movie`（N+1）。建议 `only = {int(x) for x in ...}` + 反向查询（按 extras 分组一次取出待归位列表）。
- **D3（P2）`move_attached_extras` 目标已存在时静默跳过，导致 `/collect` 永远报告同一批 pending。** `files.py:334` `if not os.path.exists(edst): rename`，else 无任何记录；DB 行仍指向旧路径 → 下次 collect 仍列出，用户反复“整理”无止境。建议返回 skipped 明细并在响应中区分 `moved/skipped_conflict`（同时暴露重名原因）。
- **D4（P2）orphan 列表只给 `file_path` + `kind`，无标题猜测/年份辅助。** `extras.py:13-15`：手工认领要用户自己读文件名猜影片 ID（Settings 里是“填影片ID认领”，`Settings.vue:139`）。建议后端在 orphan 响应里带 `guessed_title/year`（解析成本低），前端支持按标题搜片选择。
- **D5（P2）`POST /{extra_id}/attach` 可把已归属花絮改挂到任意影片**（`extras.py:19-33` + `store.update_extra_movie` 无“已被认领”保护）。语义上“手工认领优先”，但接口没有“是否覆盖既有归属”的确认，误操作会静默改归属。建议 response 带 `previous_movie_id` 或对已归属项要求 `force:true`。
- **D6（P2）杂项样片（`samples/` 目录/`is_sample`）不入库、不跟随搬迁**，符合 README；但 `extras` 表也不记录，`_movie_delete_scope` 的“独占目录整树删”会连带删除样片（合理），共享目录不会（样片留在原地）——行为不一致但符合“样片不管理”的定位，无需改。

### 2. 代码符合度（废弃代码/漏洞）

- **B1（P2）`extras.py` 依赖 `store.list_all_extras()` 的隐式全表读**（`fs._classify`、`files._move_db_follow` 都靠它逐路径匹配，R01 B3/R02 已记）；本文件自身只用了 `list_orphan_extras`（有 `WHERE movie_id IS NULL`，量小），OK。
- **B2（P2）`_KIND_WORDS` 重复规则**（`scanner.py:60-61` interviews? 两行相同）——R03 B3 已记，此处确认影响：`extra_kind` 返回值不变（前者先命中），纯死代码。
- **B3（P2）`extras.py` 里 `import os as _os` / `from ..config import settings as _settings` 在函数内**（`extras.py:40-42`）——与 R06/R03 的函数内 import 风格一致但同样冗余（模块可直接 import）。
- **B4（P2）`update_extra_movie` 返回 bool，但路由用 404 表达两种失败（extra 不存在/更新失败）**（`extras.py:31-32`），无法区分“extra 不存在”和“DB 写失败”（后者在 store 里 `UPDATE` 不抛错时也返回 True）。语义尚可，建议 store 层校验 `rowcount`。
- **B5（P2）删行留文件的 `clean-sidecars` 不清理关联的 `extras` 归属行**：`store.delete_movie`（R02 B2）不处理 extras.movie_id，因此被误删的正片若恰好有归属花絮，花絮行悬挂；重扫可自愈（R03 已验证）。随 R02 B2 一并修即可。

### 3. 代码质量差距（不符合量产要求）

- **Q1（P2）归属算法无单元测试可依赖**：`strip_kind_affix` 可多轮剥离（3 轮）+ 前缀/后缀 + 祖先目录兜底，是全项目最容易出现回归的逻辑，却没有任何测试（项目无测试框架）。建议至少给 `normalize_title/strip_kind_affix/is_sample/is_extra` 提供 `python -m unittest` 级别的纯函数测试（依赖仅 stdlib+re，成本极低）。
- **Q2（P2）`attribute_extra`（scanner.py:881-933）约 50 行职责密集**：解析、祖先回退、剥离重试、DB 认领、orphan 状态机、异常路径全在一个函数；可拆 `_iter_extra_title_candidates()` 纯函数（可测）+ `_claim_or_create()` 落库。
- **Q3（P2）状态字符串散落**（`skipped_sample/extra_attached/extra_orphan/…`）无集中定义，前后端各凭字符串匹配（`Library.vue:918`、`scanner.py:889,893,931`）；建议常量表（影响面小，但利于长期演进）。
- **Q4（P2）`/api/extras/{id}/attach` 无审计**：手工认领不记录时间/操作来源，extras 表只有 `updated_at`；自用可接受。

### 交叉引用
- 花絮跟随搬迁（正片集中整理时 `move_attached_extras` 进 `extras/`）与 `_collect_plans` 的“已归属花絮”规划归 R09 复核。
- `is_sample` 误报的完整影响面：`is_sidecar` → `scan_one` 跳过（永不入库）、`is_feature_video`（`_cleanup_old_dir` 判断正片残留时漏判）、`clean-sidecars` 删行、`_movie_delete_scope` 不删（把样片当样片）。R03 D2 一处修复多处受益，优先级应列 P1。
- `delete_movie` 悬挂 extras 引用（R02 B2）在本单元的 `/clean-sidecars` 与 `/clean` 路径都会触发，一并记入 99 汇总。
