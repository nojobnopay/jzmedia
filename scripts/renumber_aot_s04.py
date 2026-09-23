#!/usr/bin/env python3
"""进击的巨人 S04：本地绝对编号 E60–E87 → TMDB 编号 E01–E28。

用法：
    DATA_DIR=./data MEDIA_ROOT=./media .venv/bin/python scripts/renumber_aot_s04.py
    DATA_DIR=./data MEDIA_ROOT=./media .venv/bin/python scripts/renumber_aot_s04.py --execute

执行逐条写 organize_moves 审计（批次 fix-*），可用「整理历史/撤销」按批次还原。
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import store  # noqa: E402
from app.scanner import tv_organize  # noqa: E402
from app.scanner.tv_nfo_link import sync_tv_nfos_for  # noqa: E402

SHOW_ID = 44
LIB_ID = 3
SEASON = 4
OFFSET = 59
MIN_LOCAL_EP = 60


def _backend():
    from app import storage
    return storage.backend_for(LIB_ID)


def _stem_parts(stem: str):
    m = re.match(r"^(.*?-S\d{2}E)(\d{2,3})(?:-E(\d{2,3}))?-(.+)$", stem)
    if not m:
        return None
    return m.group(1), int(m.group(2)), int(m.group(3) or 0), m.group(4)


def build_plan(be):
    ops, missing = [], []
    episodes = store.list_episodes(SHOW_ID)
    rows = [e for e in episodes
            if int(e.get("season") or 0) == SEASON
            and int(e.get("episode") or 0) >= MIN_LOCAL_EP]
    dirs = {}
    for e in rows:
        fp = str(e["file_path"])
        src_dir, base = os.path.split(fp)
        stem, ext = os.path.splitext(base)
        parts = _stem_parts(stem)
        if not parts:
            missing.append((e["id"], fp, "文件名不符合 SxxEyy 模板"))
            continue
        prefix, old_ep, old_end, title = parts
        new_ep = old_ep - OFFSET
        new_end = (old_end - OFFSET) if old_end else 0
        if new_ep <= 0:
            missing.append((e["id"], fp, "偏移后集号非法"))
            continue
        new_stem = f"{prefix}{new_ep:02d}" + (f"-E{new_end:02d}" if new_end else "")
        new_stem += f"-{title}"
        if src_dir not in dirs:
            dirs[src_dir] = [x["name"] for x in be.list(src_dir)]
        sidecars = [n for n in dirs[src_dir]
                    if n != base and n.startswith(stem + ".")]
        files = [(fp, f"{src_dir}/{new_stem}{ext}")]
        for n in sidecars:
            files.append((f"{src_dir}/{n}", f"{src_dir}/{new_stem}{n[len(stem):]}"))
        ops.append({"ep_id": int(e["id"]), "old_ep": old_ep, "old_end": old_end,
                    "new_ep": new_ep, "new_end": new_end, "title": title,
                    "from": fp, "to": f"{src_dir}/{new_stem}{ext}", "files": files})
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
        print("没有需要重编号的集（可能已处理过）")
        return 0
    print(f"== 计划：{len(ops)} 集 / {sum(len(o['files']) for o in ops)} 个文件 ==")
    for o in ops:
        end = f"-E{o['new_end']:02d}" if o["new_end"] else ""
        print(f"  E{o['old_ep']:02d} → E{o['new_ep']:02d}{end}  {os.path.basename(o['from'])}")
    if not args.execute:
        print("\n（预览模式，未做任何修改；确认后加 --execute 执行）")
        return 0

    batch = tv_organize._new_batch_id("fix")
    file_ops = [f for o in ops for f in o["files"]]
    failed = 0
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
                                  episode_end=o["new_end"], needs_review=0)
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

    try:
        sync_tv_nfos_for(SHOW_ID, backend=be, force=True, episode_nfo=True)
        print("NFO 已重写")
    except Exception as e:
        print("!! NFO 重写失败:", str(e)[:160])

    print(f"\n完成：文件 {len(file_ops) - failed}，DB 行 {moved}，失败 {failed}，审计批次 {batch}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
