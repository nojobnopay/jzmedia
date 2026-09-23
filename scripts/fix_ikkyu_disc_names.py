#!/usr/bin/env python3
"""聪明的一休 Disc01–48：发行组原名 → 统一模板名（纯改名，不碰集号/标题/绑定）。

背景：Disc01–48 是「一碟多集」（每文件 5–6 集），整理器 `_MAX_EP_RANGE=3`
守卫只列 manual 不自动改名，所以一直保留原名
（如 `聪明的一休.Disc01.Ikkyu.San.1975.S01E001-E006.DVD5.X264.AAC.HALFCD-NORM.mkv`）。
Disc49–53 已由 fix_ikkyu_tail_discs.py 改成模板名，本脚本把前 48 个对齐
同一风格：`聪明的一休-S01E01-E06-妈妈的布娃娃.mkv`
（规则与 tv_organize.rename 同源：`_episode_base_title` + `_clean_name`，
标题取 DB 区间首集标题；集号零填充 E001→E01，重扫可正常解析）。

用法：
    DATA_DIR=./data MEDIA_ROOT=./media .venv/bin/python scripts/fix_ikkyu_disc_names.py
    DATA_DIR=./data MEDIA_ROOT=./media .venv/bin/python scripts/fix_ikkyu_disc_names.py --execute

执行逐条写 organize_moves 审计（批次 fix-*），可用「整理历史/撤销」还原；
集号不变故 NFO 内容继续有效、无需重刮。
"""

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import store  # noqa: E402
from app.scanner import tv_organize  # noqa: E402
from app.scanner.tv_organize import _clean_name, _episode_base_title  # noqa: E402

SHOW_ID = 7
LIB_ID = 3
SHOW_TITLE = "聪明的一休"
TEMPLATE_RE = re.compile(r"^聪明的一休-S\d+E\d+", re.I)


def _backend():
    from app import storage
    return storage.backend_for(LIB_ID)


def build_plan(be):
    try:
        names = [x["name"] for x in be.list("聪明的一休 (1975)/Season 01")]
    except Exception as e:
        print("!! 目录列举失败:", str(e)[:200])
        return [], [("list", "Season 01 目录不可读")]
    name_set = set(names)
    ops, missing, skipped = [], [], []
    for e in sorted(store.list_episodes(SHOW_ID), key=lambda x: int(x["episode"])):
        if int(e["episode"]) >= 276:
            continue  # Disc49+ 已是模板名
        fp = str(e["file_path"])
        src_dir, base = os.path.split(fp)
        stem, ext = os.path.splitext(base)
        if TEMPLATE_RE.match(stem):
            skipped.append((fp, "已是模板名"))
            continue
        title = _clean_name(e.get("title"))
        head = _episode_base_title(SHOW_TITLE, dict(e), ver=0)
        if not head or not title:
            missing.append((fp, "无标题/集号非法"))
            continue
        new_stem = f"{head}-{title}"
        new_base = new_stem + ext
        if new_base.casefold() == base.casefold():
            skipped.append((fp, "已是规范名"))
            continue
        if new_base in name_set:
            missing.append((fp, f"目标已存在: {new_base}"))
            continue
        files = [(fp, f"{src_dir}/{new_base}")]
        for n in names:
            if n != base and n.startswith(stem + "."):
                files.append((f"{src_dir}/{n}", f"{src_dir}/{new_stem}{n[len(stem):]}"))
        ops.append({"ep_id": int(e["id"]), "episode": int(e["episode"]),
                    "episode_end": int(e.get("episode_end") or 0),
                    "from": fp, "to": files[0][1], "files": files})
    return ops, missing, skipped


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--execute", action="store_true", help="真的执行（默认只预览）")
    args = ap.parse_args()
    store.init_db()
    be = _backend()
    ops, missing, skipped = build_plan(be)
    if missing:
        print("!! 跳过/冲突：")
        for m in missing:
            print("   ", m)
    if skipped:
        print(f"-- 已是模板名跳过：{len(skipped)}")
    if not ops:
        print("没有需要改名的行")
        return 0
    print(f"== 计划：{len(ops)} 行 / {sum(len(o['files']) for o in ops)} 个文件 ==")
    for o in ops:
        print(f"  E{o['episode']:03d}-E{o['episode_end']:03d}"
              f"  {os.path.basename(o['from'])}")
        print(f"    -> {os.path.basename(o['to'])}"
              + (f"  (+{len(o['files']) - 1} 附属)" if len(o["files"]) > 1 else ""))
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
                    continue  # 已执行过（断点续跑幂等）
                print("  !! 源与目标都不存在:", src)
                failed += 1
                continue
            if be.exists(dst):
                print("  !! 目标已存在，跳过:", dst)
                failed += 1
                continue
            be.rename(src, dst)
        except Exception as ex:
            print("  !! rename 失败:", src, str(ex)[:120])
            failed += 1

    mapping = {o["from"]: o["to"] for o in ops}
    moved = store.move_tv_paths(LIB_ID, mapping)

    audit = []
    for o in ops:
        audit.append({"library_id": LIB_ID, "show_id": SHOW_ID, "kind": "episode",
                      "obj": "file", "action": "rename",
                      "from_path": o["from"], "to_path": o["to"]})
        for src, dst in o["files"][1:]:
            audit.append({"library_id": LIB_ID, "show_id": SHOW_ID, "kind": "file",
                          "obj": "file", "action": "rename",
                          "from_path": src, "to_path": dst})
    store.record_organize_moves(batch, audit, library_id=LIB_ID)
    print(f"\n完成：文件 {len(file_ops) - failed}，DB 路径 {moved}，失败 {failed}，"
          f"审计批次 {batch}")
    print("下一步：重扫 show 7（0 缺失）+ organizer dry-run show 7（0 移动）")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
