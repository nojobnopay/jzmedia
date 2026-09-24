#!/usr/bin/env python3
"""一次性修正：文件名里的"分辨率同值"括号编号行补 absolute_number（2026-09 执行）。

背景：`蜡笔小新_高清版 (240).flv` 这类文件名，数字命中 tv_parse 的分辨率词表
（240/360/480/576/720/1080/1440…）→ 旧解析器拒绝，落入 guessit —— `(240)` 被
guessit 拆成 S02E40，其余 6 个虽编号对但 `absolute_number` 为空、标 needs_review，
换绑后无法参与 `store.remap_absolute_episodes` 的（爱奇艺分季 → S1~S4）重映射。

本脚本（仅改 DB，不动文件）对指定剧的集行：
  - 文件名尾部能解析出数字 N（新解析器口径，含括号），且行 `absolute_number` 为空时：
      * 行内 (season, episode) 与 N 不一致 → 归一为 (1, N)（季号交给 remap 按绑定条目推导）；
      * 补 absolute_number=N。
  - 已有 absolute_number（正常绝对编号行）一律不动。

用法（仓库根目录）：
    python scripts/fix_tv_resolution_rows.py                 # 预览（默认，不动 DB）
    python scripts/fix_tv_resolution_rows.py --execute       # 执行
    python scripts/fix_tv_resolution_rows.py --show-id 69
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import store                      # noqa: E402
from app.scanner import tv_parse           # noqa: E402


def plan_fixes(show_id: int) -> list[dict]:
    show = store.get_show_meta(show_id)
    if not show:
        raise SystemExit(f"show not found: {show_id}")
    out: list[dict] = []
    for e in store.list_episodes(show_id):
        if e.get("absolute_number") is not None:
            continue                       # 正常绝对编号行不动
        base = os.path.basename(str(e.get("file_path") or ""))
        n = tv_parse._bare_number(os.path.splitext(base)[0]) if base else None
        if not n:
            continue
        season, episode = int(e.get("season") or 0), int(e.get("episode") or 0)
        target_season, target_episode = (1, n) if (season, episode) != (1, n) \
            else (season, episode)
        out.append({"id": int(e["id"]), "file": base, "n": int(n),
                    "from": (season, episode, e.get("absolute_number")),
                    "to": (target_season, target_episode, int(n))})
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--show-id", type=int, default=69)
    ap.add_argument("--execute", action="store_true")
    args = ap.parse_args()

    show = store.get_show_meta(args.show_id) or {}
    print(f"show {args.show_id}: {show.get('title')} (tmdb_id={show.get('tmdb_id')})")
    fixes = plan_fixes(args.show_id)
    if not fixes:
        print("无需修正（没有 absolute_number 为空且文件名可解析的行）")
        return
    for f in fixes:
        s0, e0, a0 = f["from"]
        s1, e1, a1 = f["to"]
        print(f"  #{f['id']} {f['file']}: S{s0:02d}E{e0:02d} abs={a0} -> "
              f"S{s1:02d}E{e1:02d} abs={a1}")
    print(f"共 {len(fixes)} 行；" + ("执行写入…" if args.execute else "预览模式（--execute 执行）"))
    if not args.execute:
        return
    for f in fixes:
        s1, e1, a1 = f["to"]
        store.update_episode_meta(f["id"], season=s1, episode=e1, absolute_number=a1)
    print("完成。下一步：重新匹配 TMDB 条目（如 323180），remap 会自动按季拆分归位。")


if __name__ == "__main__":
    main()
