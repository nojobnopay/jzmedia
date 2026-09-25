#!/usr/bin/env python3
"""生成 TV 改名映射 CSV + 修正记录/遗留问题文档（只读 DB）。"""
import collections
import csv
import os
import re
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.makedirs("docs/private", exist_ok=True)

from app import store                                    # noqa: E402
from app.scanner import tv_nfo_link, tv_organize         # noqa: E402

DB = "data/jzmedia.db"
con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row

moves = [dict(r) for r in con.execute("SELECT * FROM organize_moves ORDER BY id")]
dir_moves = [m for m in moves if m["obj"] == "dir"
             and not str(m["batch_id"]).startswith("repair-")]
dir_moves.sort(key=lambda r: -r["id"])
by_to = collections.defaultdict(list)
for m in moves:
    if m["obj"] == "file" and not str(m["batch_id"]).startswith("repair-"):
        by_to[m["to_path"]].append(m)


def chain(cur):
    """沿审计链回溯（每步 id 更小 = 时间向前），跳过 .fixtmp 修复行。
    hops[0] 为最近一跳；返回 (hops, 最初路径)。"""
    hops, last = [], None
    for _ in range(300):
        cands = [m for m in by_to.get(cur, [])
                 if (last is None or m["id"] < last)
                 and ".fixtmp" not in m["from_path"]]
        if hops and str(hops[-1].get("batch_id", "")).startswith("fix-"):
            # fix 跳后不得穿过「别的文件」的 fix 跳（同一名字被多个内容先后使用）
            cands = [m for m in cands
                     if not str(m.get("batch_id", "")).startswith("fix-")]
        if cands:
            m = max(cands, key=lambda r: r["id"])
            hops.append(m)
            cur = m["from_path"]
            last = m["id"]
            continue
        dm = None
        for cand in dir_moves:            # dir_moves 已按 id 降序
            if last is not None and cand["id"] >= last:
                continue
            tp = cand["to_path"].rstrip("/")
            if cur == tp or cur.startswith(tp + "/"):
                dm = cand
                break
        if dm is None:
            break
        tp = dm["to_path"].rstrip("/")
        cur = dm["from_path"].rstrip("/") + cur[len(tp):]
        last = dm["id"]
        hops.append(dm)
    return hops, cur


def walk(cur):
    return chain(cur)[1]


def pre_fix_path(hops, cur):
    """修正脚本执行前的路径 = 最远一条 fix- 号跳的 from。"""
    earliest = None
    for m in hops:
        if str(m.get("batch_id", "")).startswith("fix-"):
            earliest = m
    return earliest["from_path"] if earliest else walk(cur)


fix_first = min((m["id"] for m in moves if m["batch_id"].startswith("fix-")),
                default=10 ** 9)
shows = {r["id"]: dict(r) for r in con.execute("SELECT * FROM tv_shows")}
libs = {r["id"]: dict(r) for r in con.execute("SELECT * FROM libraries")}

# ---------- episodes CSV ----------
ep_rows, changed, chain_missing = [], 0, []
for e in con.execute("SELECT * FROM tv_episodes ORDER BY show_id, season, episode, id"):
    cur = e["file_path"]
    orig = walk(cur)
    hops, orig = chain(cur)
    before_fix = pre_fix_path(hops, cur)
    touched = any(str(h.get("batch_id", "")).startswith("fix-") for h in hops)
    if orig != cur:
        changed += 1
    if orig == cur:
        chain_missing.append((e["id"], cur))
    show = shows.get(e["show_id"], {})
    ep_rows.append({
        "show_id": e["show_id"], "剧名": show.get("title", ""),
        "媒体库": libs.get(e["library_id"], {}).get("name", ""),
        "S_现": e["season"], "E_现": e["episode"],
        "集末": e["episode_end"], "绝对集号": e["absolute_number"],
        "版本": store.episode_version(cur), "待确认": e["needs_review"],
        "当前路径": cur, "原始路径": orig,
        "修正前路径": before_fix,
        "改名": "是" if orig != cur else "否",
        "fix_touched": "是" if touched else "否",
    })
with open("docs/private/tv-rename-mapping-20260922.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=list(ep_rows[0].keys()))
    w.writeheader()
    w.writerows(ep_rows)

# ---------- extras CSV ----------
ex_rows = []
for x in con.execute("SELECT * FROM extras ORDER BY id"):
    cur = x["file_path"]
    show = shows.get(x["show_id"] or 0, {})
    ex_rows.append({
        "id": x["id"], "库": libs.get(x["library_id"], {}).get("name", ""),
        "归属剧": show.get("title", ""), "movie_id": x["movie_id"],
        "kind": x["kind"], "show_id": x["show_id"],
        "当前路径": cur, "原始路径": walk(cur),
        "改名": "是" if walk(cur) != cur else "否",
    })
with open("docs/private/tv-extras-mapping-20260922.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=list(ex_rows[0].keys()))
    w.writeheader()
    w.writerows(ex_rows)

# ---------- 全量审计 CSV ----------
with open("docs/private/tv-organize-audit-20260922.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=["id", "batch_id", "show_id", "library_id",
                                      "kind", "obj", "action", "from_path", "to_path",
                                      "created_at", "undone_at"])
    w.writeheader()
    w.writerows(moves)

print(f"episodes {len(ep_rows)}（改名 {changed}，无链 {len(chain_missing)}）"
      f" / extras {len(ex_rows)} / moves {len(moves)}")
for sid in (44, 51, 18, 26, 16):
    s = store.get_show(sid)
    eps = [r for r in ep_rows if r["show_id"] == sid]
    no_chain = sum(1 for r in eps if r["原始路径"] == r["当前路径"])
    print(f"  {s['title']}: {len(eps)} 集，其中可回溯改名 "
          f"{sum(1 for r in eps if r['改名'] == '是')}，无链 {no_chain}")
print("\n-- 样例（AoT S01E13 / S00E01 / 老友记 S06E23-part2 / 怪兽 S01E23） --")
for r in ep_rows:
    if (r["show_id"], r["S_现"], r["E_现"]) in ((44, 1, 13), (44, 0, 1), (18, 6, 23), (51, 1, 23)):
        print(f"  [{r['剧名']} S{r['S_现']:02d}E{r['E_现']:02d}] 现在: {r['当前路径']}")
        print(f"      原始: {r['原始路径']}")
        print(f"      修前: {r['修正前路径']}")
