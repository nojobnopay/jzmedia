#!/usr/bin/env python3
"""聪明的一休 Disc50–53：发布编号（298 集口径）→ TMDB 编号（296 集，偏移 −2）。

背景：中版 298 集在发布 E276/E277 处插入了 2 集日版年末特别篇（烤麻雀/一文互助，
TMDB 未收录），导致发布 E281+ 整体比 TMDB 大 2。实测确认各碟首集后做此映射：
  Disc50 E281-E285 → TMDB E279-E283（卸下的招牌与将军的梦）
  Disc51 E286-E290 → TMDB E284-E288（调皮公主与霞之宴）
  Disc52 E291-E294 → TMDB E289-E292（丽心小姐的夫婿与嫉妒的秀念）
  Disc53 E295-E298 → TMDB E293-E296（漏雨的安国寺与意外之财）
Disc49（E276-E280）头两集 TMDB 无对应，走 local_only，不在本脚本内。

重编号的唯一原因：apply_tv_detail 按精确 (季,集) 回填元数据——只改绑定不改号，
下一次重刮会把标题/绑定改回错的（与 AoT S04 同一思路）。

用法：
    DATA_DIR=./data MEDIA_ROOT=./media .venv/bin/python scripts/fix_ikkyu_tail_discs.py
    DATA_DIR=./data MEDIA_ROOT=./media .venv/bin/python scripts/fix_ikkyu_tail_discs.py --execute

执行逐条写 organize_moves 审计（批次 fix-*），可用「整理历史/撤销」还原；
执行后需对 show 7 做 force 重刮（拿正确集的标题/简介/剧照）+ 重写 NFO。
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import store  # noqa: E402
from app.scanner import tv_organize  # noqa: E402

SHOW_ID = 7
LIB_ID = 3
SEASON = 1
SHOW_TITLE = "聪明的一休"

# (旧集号, 旧集末, 新集号, 新集末, tmdb_episode_id, tmdb 标题)
PLAN = [
    (281, 285, 279, 283, 2293216, "卸下的招牌与将军的梦"),
    (286, 290, 284, 288, 2293221, "调皮公主与霞之宴"),
    (291, 294, 289, 292, 2293226, "丽心小姐的夫婿与嫉妒的秀念"),
    (295, 298, 293, 296, 2293230, "漏雨的安国寺与意外之财"),
]


def _backend():
    from app import storage
    return storage.backend_for(LIB_ID)


def build_plan(be):
    rows = {(int(e["season"]), int(e["episode"])): e
            for e in store.list_episodes(SHOW_ID)}
    ops, missing = [], []
    for old_ep, old_end, new_ep, new_end, tmdb_id, title in PLAN:
        e = rows.get((SEASON, old_ep))
        if not e:
            missing.append((old_ep, "DB 无该行（可能已处理）"))
            continue
        if (int(e["episode"]), int(e.get("episode_end") or 0)) == (new_ep, new_end):
            print(f"  行 ep={e['id']} 已是 E{new_ep}-E{new_end}，跳过")
            continue
        fp = str(e["file_path"])
        src_dir, base = os.path.split(fp)
        stem, ext = os.path.splitext(base)
        new_stem = f"{SHOW_TITLE}-S{SEASON:02d}E{new_ep:02d}-E{new_end:02d}-{title}"
        names = [x["name"] for x in be.list(src_dir)]
        files = [(fp, f"{src_dir}/{new_stem}{ext}")]
        for n in names:
            if n != base and n.startswith(stem + "."):
                files.append((f"{src_dir}/{n}", f"{src_dir}/{new_stem}{n[len(stem):]}"))
        ops.append({"ep_id": int(e["id"]), "new_ep": new_ep, "new_end": new_end,
                    "tmdb_id": tmdb_id, "title": title,
                    "from": fp, "to": files[0][1], "files": files})
    return ops, missing


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--execute", action="store_true", help="真的执行（默认只预览）")
    args = ap.parse_args()
    store.init_db()
    be = _backend()
    ops, missing = build_plan(be)
    if missing:
        print("!! 跳过：")
        for m in missing:
            print("   ", m)
    if not ops:
        print("没有需要重编号的行")
        return 0
    print(f"== 计划：{len(ops)} 行 / {sum(len(o['files']) for o in ops)} 个文件 ==")
    for o in ops:
        print(f"  E… → S{SEASON:02d}E{o['new_ep']:02d}-E{o['new_end']:02d}"
              f"  {os.path.basename(o['from'])}")
    if not args.execute:
        print("\n（预览模式，未做任何修改；确认后加 --execute 执行）")
        return 0

    batch = tv_organize._new_batch_id("fix")
    failed = 0
    file_ops = [f for o in ops for f in o["files"]]
    for src, dst in file_ops:
        try:
            if not be.exists(src):
                if be.exists(dst):
                    continue
                print("  !! 源与目标都不存在:", src)
                failed += 1
                continue
            if be.exists(dst):
                print("  !! 目标已存在，跳过:", dst)
                failed += 1
                continue
            be.rename(src, dst)
        except Exception as e:
            print("  !! rename 失败:", src, str(e)[:120])
            failed += 1

    mapping = {o["from"]: o["to"] for o in ops}
    for o in ops:
        store.update_episode_meta(o["ep_id"], season=SEASON, episode=o["new_ep"],
                                  episode_end=o["new_end"], needs_review=0,
                                  local_only=0)
    moved = store.move_tv_paths(LIB_ID, mapping)

    audit = []
    for o in ops:
        audit.append({"library_id": LIB_ID, "show_id": SHOW_ID, "kind": "episode",
                      "obj": "file", "action": "renumber",
                      "from_path": o["from"], "to_path": o["to"]})
        for src, dst in o["files"][1:]:
            audit.append({"library_id": LIB_ID, "show_id": SHOW_ID, "kind": "file",
                          "obj": "file", "action": "renumber",
                          "from_path": src, "to_path": dst})
    store.record_organize_moves(batch, audit, library_id=LIB_ID)
    print(f"\n完成：文件 {len(file_ops) - failed}，DB 行 {moved}，失败 {failed}，"
          f"审计批次 {batch}")
    print("下一步：force 重刮 show 7（填充新 TMDB 集的标题/简介/剧照）+ 重写 NFO")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
