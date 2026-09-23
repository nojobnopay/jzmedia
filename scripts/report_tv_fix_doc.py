#!/usr/bin/env python3
"""生成《剧集修正记录与遗留问题》文档（只读）。"""
import collections
import csv
import os
import re
import sqlite3
import subprocess
import sys

ROOT = "."
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import importlib.util                                    # noqa: E402

from app import store                                    # noqa: E402
from app.scanner import tv_nfo_link                      # noqa: E402

spec = importlib.util.spec_from_file_location("fix_tv_bindings", "scripts/fix_tv_bindings.py")
_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(_mod)
OLD_BY_NEW = {(sid, ts, te): (fs, fe, part)
              for sid, rows in _mod.MAPPING
              for (fs, fe, ts, te, part, *rest) in [list(r) + [None] for r in rows]}

con = sqlite3.connect("data/jzmedia.db")
con.row_factory = sqlite3.Row
moves = [dict(r) for r in con.execute("SELECT * FROM organize_moves ORDER BY id")]

ep = list(csv.DictReader(open("docs/tv-rename-mapping-20260922.csv", encoding="utf-8-sig")))
by_show = collections.defaultdict(list)
for r in ep:
    by_show[int(r["show_id"])].append(r)

FIVE = [(44, "进击的巨人"), (51, "怪兽8号"), (18, "老友记"),
        (26, "神探夏洛克"), (16, "黑镜")]
ABS = ["蜡笔小新 合集", "龙珠Z", "龙珠GT", "血仇", "黑街"]


def bn(p):
    return os.path.basename(p or "")


def seal(p):
    m = re.search(r"S(\d{1,2})E(\d{1,2})", bn(p), re.I)
    return f"S{int(m.group(1)):02d}E{int(m.group(2)):02d}" if m else "?"


def show_dir(sid):
    eps = by_show[sid]
    if not eps:
        return ""
    return tv_nfo_link.show_dir_of(eps[0]["当前路径"])


def row_of_current(path):
    for r in ep:
        if r["当前路径"] == path:
            return r
    return None


L = []
w = L.append

w("# 剧集修正记录与遗留问题（2026-09-22）")
w("")
w("> 数据来源：`organize_moves` 审计表 + `scan_state` 尺寸台账 + DB（只读）。配套数据文件：")
w("> - `docs/tv-rename-mapping-20260922.csv` — 全部 3877 集：当前路径 / 原始路径 / 修正前路径")
w("> - `docs/tv-extras-mapping-20260922.csv` — 274 个花絮/剧场版同样三列")
w("> - `docs/tv-organize-audit-20260922.csv` — 5361 条移动审计（organize_moves 全量导出）")
w(">")
w("> 重新生成（如后续又有改名）：先 `scripts/report_tv_mapping.py` 再 `scripts/report_tv_fix_doc.py`"
  "（只读，需 `DATA_DIR=./data MEDIA_ROOT=./media`）。")
w("")
w("## 0. 一分钟概览")
w("")
w("- 本次修正 **5 部剧**：进击的巨人、怪兽8号、老友记、神探夏洛克、黑镜；共 **58 个视频 + 58 个同茎 NFO**，"
  "全部逐条写审计（批次 `fix-20260922-230102-af79ba`），可反向撤销。")
w("- 执行漂移修复（`.fixtmp` 链 / 未移动文件）另记 **45 条**（批次 `repair-20260922-*`）。")
w("- 修正后重扫：**60 剧 / 3877 集 / 274 花絮剧场版**；5 部剧 **0 文件缺失、0 待确认**（黑镜剩 1 条待定）。")
w("- 剩余待确认 2 项：**黑镜 Bandersnatch**、**马达加斯加 S01E04**（见 §3.1）。")
w("- **5 部绝对集号剧已排除未动**：蜡笔小新 合集、龙珠Z、龙珠GT、血仇、黑街（见 §3.2）。")
w("- 一次性修正脚本：`scripts/fix_tv_bindings.py`（默认 dry-run，`--execute` 执行；映射规则见下表）。")
w("")
w("## 1. 逐文件映射（修正前 → 现在）")
w("")
w("「原始」= 本工具首次记录到的文件（NAS 原名）；「修正前」= 修正脚本运行前一刻的名字"
  "（凡与「原始」相同的行标 `（同原始）`）。集号列为 **本地旧号 → TMDB 新号**。")
w("")
w("口径说明：回溯沿审计链按时间倒推（`repair-*` 恢复跳不参与，避免漂移环）；"
  "AoT S01 旋转批次的内容归属 = fix 审计跳（内容随名字走）+ 尺寸台账（repair 按唯一 size 归位）。")
w("")

for sid, title in FIVE:
    rows = [r for r in by_show[sid] if r.get("fix_touched") == "是"]
    def _old_key(r):
        m = re.search(r"S(\d{1,2})E(\d{1,2})", bn(r["修正前路径"]), re.I)
        if m:
            return (int(m.group(1)), int(m.group(2)))
        info = OLD_BY_NEW.get((sid, int(r["S_现"] or 0), int(r["E_现"] or 0)))
        return (info[0], info[1]) if info else (99, 99)
    rows.sort(key=lambda r: (_old_key(r), r["当前路径"]))
    w(f"### 1.{FIVE.index((sid, title)) + 1} {title}（show #{sid}，共 {len(by_show[sid])} 集，"
      f"本次绑定修正 {len(rows)} 个）")
    w("")
    if rows:
        w("| 旧→新 | 原始文件 | 修正前文件 | 现在文件 |")
        w("|---|---|---|---|")
        for r in rows:
            pre = "（同原始）" if r["修正前路径"] == r["原始路径"] else bn(r["修正前路径"])
            old = seal(r["修正前路径"])
            if old == "?":
                info = OLD_BY_NEW.get((sid, int(r["S_现"] or 0), int(r["E_现"] or 0)))
                if info:
                    old = f"S{info[0]:02d}E{info[1]:02d}" + (f"（part{info[2]}）" if info[2] else "")
            w(f"| {old} → {seal(r['当前路径'])} "
              f"| {bn(r['原始路径'])} | {pre} | {bn(r['当前路径'])} |")
        w("")
    if sid == 44:
        w("- 特例：`S04E85《背叛》` 只绑 TMDB `S04E26`，文件名保留本地绝对编号（未改动，见 §3.3）。")
        w("- 另：S01 本地 13/21（BD 版）与 TV 版画面有差异（未删节），已确认保留。")
        w("")
    if sid == 18:
        w("- part1/part2 = 原蓝光双长集被拆成两文件，绑到同一 TMDB 集（Plex 规范，播放器视为同一集多段）。")
        w("")

w("## 2. 修正后校验结果")
w("")
w("| 项目 | 结果 |")
w("|---|---|")
w(f"| 全库重扫 | {store.count_shows(3)} 剧 / {store.count_episodes(3)} 集 |")
w("| 5 部剧缺失文件 | 0（逐行比对 DB 路径与盘上存在性） |")
w("| 5 部剧待确认集 | 0（黑镜 1 条 = Bandersnatch，见 §3.1） |")
w("| 修复脚本二次运行 | 0 移动 / 0 删除 / 0 缺失 |")
w("| 整理计划（除排除的 5 部剧） | total 0 / conflicts 0 |")
w("| AoT | S01 = E01–E25（25 文件）；Season 00 = 总集篇 + OAD×8 + 完结篇×2；"
  "旧 `[进击的巨人 OAD][8部全]` 包装层与空 `Season 05` 已删 |")
w("| 怪兽8号 | S01 = E01–E23；Season 00 = S00E18；空 `Season 02` 已删 |")
w("| 老友记 | 234 集（TMDB 228 + 6 个 part 文件）；S04/05/07/08 各 1 集拆 part1+part2；"
  "S06 两处双长集（E15+E16、E24+E25） |")
w("| 神探夏洛克 | S00E09《可恶的新娘》 |")
w("| 黑镜 | S02E04《白色圣诞节》 |")
w("| AoT S01 旋转内容依据 | fix 审计跳（内容随名字走）+ 尺寸台账（`scan_state` 唯一 size 归位）；"
  "**建议抽查**：S00E01 应为总集篇《从那天起》、S01E14 起 OP 应为《自由の翼》 |")
w("| 测试 | 后端 629 passed；前端 98 tests；lint 0 error；pyflakes 干净 |")
w("")
w("## 3. 遗留问题（明天继续）")
w("")
w("### 3.1 待确认内容（2 项）")
w("")
w("1. **黑镜 Bandersnatch**（`needs_review`）：")
w("   - 盘上：`黑镜 (2011)/Season 00/Black Mirror (2011) - S00E02 - Bandersnatch (1080p NF WEB-DL x265 Silence).mkv`")
w("   - TMDB 里 Bandersnatch 是 **电影**（id 569547），不在任何 S00 里。")
w("   - 需要决定：a) 加「确认无对应集」标记（需要一个小功能：清 needs_review 但不绑集）；"
  "b) 移到剧场版/电影区；c) 保留现状。")
w("2. **马达加斯加 S01E04**（`needs_review`，无标题）：")
w("   - 盘上：`马达加斯加 (2011)/Season 01/Madagascar.S01E04.2011.Bluray.1080p.DTS-HDMA2.0.x264-BlackTV.mkv`")
w("   - 其他集标题：E01 奇迹之岛 / E02 失落世界 / E03 焦土之境；E04 起全部待确认（整套是蓝光排序）。")
w("   - 需要你确认 E04 往后的内容对应 TMDB 哪一集（或整季是「蓝光第 4 集」这种未收录内容）。")
w("")
w("### 3.2 已排除的 5 部绝对集号剧（保持原样，等你决定）")
w("")
w("| 剧 | 集数 | 当前盘上位置 |")
w("|---|---|---|")
for t in ABS:
    r = con.execute("SELECT id FROM tv_shows WHERE title=? AND library_id=3", (t,)).fetchone()
    n = con.execute("SELECT COUNT(*) FROM tv_episodes WHERE show_id=?", (r["id"],)).fetchone()[0]
    d = tv_nfo_link.show_dir_of(con.execute(
        "SELECT file_path FROM tv_episodes WHERE show_id=? LIMIT 1", (r["id"],)).fetchone()[0])
    w(f"| {t} | {n} | `{d}` |")
w("")
w("这些剧的集号是「全集连续绝对号」或与 TMDB 无法对齐，整理器默认只列 `manual` 不改名；"
  "如需处理，要在 UI 勾选「绝对集号剧允许改名」（`allow_absolute_shows`）并先人工核对集号。")
w("")
w("### 3.3 进击的巨人 S04：本地绝对编号 E60–E87（重要）")
w("")
s4 = con.execute("""SELECT e.season,e.episode,e.file_path FROM tv_episodes e
                    WHERE e.show_id=44 AND e.season=4 ORDER BY e.episode""").fetchall()
w(f"- 盘上 {len(s4)} 个文件名为 `S04E{int(s4[0]['episode']):02d}–S04E{int(s4[-1]['episode']):02d}`"
  f"（本地绝对号），TMDB 该季是 28 集（E01–E28）：本地 E60→TMDB E01 … E87→E28（偏移 −59）。")
w("- `E85` 已绑 TMDB `E26《背叛》`（保留本地编号）。Plex 若按 TMDB 顺序可能识别不了这一季。")
w("- 两个选项：**A)** 整季改名为 TMDB 编号 `S04E01–E28`（一次性脚本，28 视频 + NFO 一起改，需你确认）；"
  "**B)** 保留本地编号（Plex 里手动匹配或用 TVDB 绝对顺序）。")
w("")
w("当前文件（按集号）：")
w("")
w("```")
for r in s4:
    w(bn(r["file_path"]))
w("```")
w("")
w("### 3.4 其他待办 / 待决策")
w("")
w("- **老友记 part1/part2 显示**：应用里同一集显示为两行（同号，版本 1）；Plex 会当同一集的多段。"
  "如果要合并成一行播放（先 part1 后 part2 连播），需要改 UI/连播逻辑。")
w("- **全库特典错绑只读审计**（建议做）：脚本思路 = 对每部剧拉 TMDB S00 列表，"
  "把本地特典文件名（含标题）与 TMDB 集名做相似度 + 时长比对，列出疑似错绑（本次 OAD 类问题的通用排查）。")
w("- **匹配器加固**：本次 `AoT S01E26 → TMDB S04E26` 误绑的根因是跨季唯一集号回退"
  "（`app/scanner/tv_persist.py` 的 `_episode_index` 绝对号回退）；建议加「同季优先、跨季仅绝对号命中」守卫。")
w("- **服务重启**：8080 仍在跑旧代码（本次的 v24 迁移、整理面板、修复脚本改动都要重启后生效）。")
w(f"- **未提交**：工作区有 {len(subprocess.run(['git', 'status', '--short'], capture_output=True, text=True).stdout.splitlines())} 个改动文件，"
  "建议 commit（版本号已是 `0.17.0`）。")
w("")
w("### 3.5 Plex 刷新核对清单（5 部剧）")
w("")
w("| 剧 | 核对点 |")
w("|---|---|")
w("| 进击的巨人 | S01 = E01–E25；特典：S00E01 总集篇、S00E07/E13–E19 OAD、S00E36/E37 完结篇；S04 见 §3.3 |")
w("| 怪兽8号 | S01 = E01–E23；S00E18 保科的休息日；Season 02 应已消失 |")
w("| 老友记 | S04/05/07/08 的双长集应合并为新的一集；S06 E15–E25 整体前移一位 |")
w("| 神探夏洛克 | S00E09 可恶的新娘 |")
w("| 黑镜 | S02E04 白色圣诞节 |")
w("")
w("## 4. 恢复 / 撤销手段")
w("")
w("全部移动都在 `organize_moves` 表（已导出 `docs/tv-organize-audit-20260922.csv`）：")
w("")
w("| 批次 | 条数 | 内容 |")
w("|---|---|---|")
batches = collections.defaultdict(int)
for m in moves:
    key = m["batch_id"]
    if key.startswith("org-"):
        key = "org-*（8 批）"
    batches[key] += 1
DESC = {
    "legacy-20260922": "事故快照回填（绝命毒师 / 胜者即是正义等）",
    "undo-20260922-175425-078a74": "还原绝命毒师 223 + 胜者即是正义 14（已用完）",
    "fix-20260922-230102-af79ba": "本次 5 部剧绑定修正（视频+NFO）",
    "org-*（8 批）": "全库目录整理（55 部剧：剧根/季目录/包装层/特典/统一改名）",
}
for k in sorted(batches):
    if k.startswith("repair-"):
        continue
    w(f"| {k} | {batches[k]} | {DESC.get(k, '—')} |")
w(f"| repair-*（6 批） | {sum(v for k, v in batches.items() if k.startswith('repair-'))} "
  "| 执行漂移修复（`.fixtmp` 链 + 怪兽/神探未移动文件） |")
w("")
w("- 撤销接口：`POST /api/jobs/tv-organize-restore {\"batch_id\": \"fix-...\", \"dry_run\": true}`"
  "（设置页「库工具 → 剧集目录整理 → 整理历史/撤销」，按**反向时序**还原，冲突只报告不覆盖）。")
w("- 撤销**不会**自动重扫，也不会重写 NFO/海报（需要时跑「重建剧集 NFO/海报」）。")
w("- 本仓库所有脚本/改动可安全重跑校验：")
w("")
w("```bash")
w("# 结构体检（只读）")
w("python scripts/tv_structure_report.py --library 3")
w("# 整理计划幂等性（除 5 部排除剧应为 total 0）")
w("DATA_DIR=./data MEDIA_ROOT=./media .venv/bin/python - <<'PY'")
w("from app import store; from app.scanner import tv_organize")
w("store.init_db(); p = tv_organize.plan_tv_organize(library_ids=[3])")
w("print(p['total'], p['counts'], p['conflicts'], p['manual'])")
w("PY")
w("```")

open("docs/tv-fix-and-pending-20260922.md", "w", encoding="utf-8").write("\n".join(L) + "\n")
print("written docs/tv-fix-and-pending-20260922.md", len(L), "行")
