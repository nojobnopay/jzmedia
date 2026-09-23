#!/usr/bin/env python3
"""TV 结构与元数据体检（只读，不写 DB、不改盘）：整理前后对照 / 定位不规范剧集。

输出：
  ① 结构：包装层、补 Season、花絮拍平、跨季同名冲突、做种阻断（复用 tv_organize 预览）；
  ② 元数据：待确认剧数、未匹配集号、同集多版本、花絮/剧场版登记数。

库与媒体路径与主程序同源：环境变量 DATA_DIR（默认 ./data）、MEDIA_ROOT（默认 ./media）。

用法：
    python scripts/tv_structure_report.py                # 全部启用 TV 视频库
    python scripts/tv_structure_report.py --library 3
    python scripts/tv_structure_report.py --media-library 2
    python scripts/tv_structure_report.py --json
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import library_paths, store                      # noqa: E402
from app.scanner import tv_organize                       # noqa: E402


def _tv_libraries(args) -> list[dict]:
    libs = library_paths.list_libraries(only_enabled=True)
    if args.media_library is not None:
        libs = [l for l in libs
                if int(l.get("media_library_id") or 0) == int(args.media_library)]
    if args.library is not None:
        libs = [l for l in libs if int(l.get("id") or 0) == int(args.library)]
    return [l for l in libs if str(l.get("kind") or "movie") == "tv"]


def _meta_report(lib_id: int) -> dict:
    shows = store.list_shows_for_scrape(library_ids=[lib_id], force=True)
    out = {"shows": len(shows), "episodes": 0, "review_shows": 0,
           "unmatched_eps": 0, "duplicate_pairs": 0, "extras": 0, "movies": 0,
           "show_rows": []}
    for s in shows:
        eps = store.list_episodes(int(s["id"]))
        unmatched = [e for e in eps if not e["tmdb_episode_id"]]
        seen: dict = {}
        dup = 0
        for e in eps:
            key = (int(e.get("season") or 0), int(e.get("episode") or 0))
            if key in seen:
                dup += 1
            seen[key] = 1
        ex = store.list_extras_by_show(int(s["id"]))
        movies = sum(1 for x in ex if x.get("kind") == "movie")
        out["episodes"] += len(eps)
        out["unmatched_eps"] += len(unmatched)
        out["duplicate_pairs"] += dup
        out["extras"] += len(ex) - movies
        out["movies"] += movies
        if int(s.get("needs_review") or 0):
            out["review_shows"] += 1
        if unmatched or dup or not s.get("tmdb_id") or int(s.get("needs_review") or 0):
            out["show_rows"].append({
                "show_id": s["id"], "title": s.get("title"),
                "needs_review": int(s.get("needs_review") or 0),
                "unmatched": len(unmatched),
                "duplicate": dup,
                "extras": len(ex) - movies, "movies": movies,
                "unmatched_sample": [f"S{int(e.get('season') or 0):02d}E"
                                     f"{int(e.get('episode') or 0):02d}"
                                     for e in unmatched[:6]],
            })
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--library", type=int, default=None, help="视频库 id")
    ap.add_argument("--media-library", type=int, default=None, help="媒体库 id")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()
    libs = _tv_libraries(args)
    if not libs:
        print("没有匹配的 TV 视频库（检查 --library/--media-library 或库 kind）")
        return 1
    reports = []
    for lib in libs:
        lib_id = int(lib["id"])
        structure = tv_organize.plan_tv_organize(library_ids=[lib_id],
                                                 allow_torrent=False)
        meta = _meta_report(lib_id)
        reports.append({"library_id": lib_id, "name": lib.get("name") or "",
                        "structure": {
                            "counts": structure["counts"],
                            "conflicts": structure["conflicts"],
                            "blocked": structure["blocked"],
                            "plans": [{"show_id": p["show_id"], "title": p["title"],
                                       "wrapper": len(p["dir_moves"]),
                                       "season": sum(1 for m in p["file_moves"]
                                                     if m["action"] == "season"),
                                       "extras": sum(1 for m in p["file_moves"]
                                                     if m["action"] == "extras")
                                       + len(p["dir_renames"]),
                                       "conflicts": len(p["conflicts"]),
                                       "blocked": p.get("blocked", False),
                                       "warnings": p["warnings"]}
                                      for p in structure["plans"]],
                        },
                        "meta": meta})
    if args.json:
        print(json.dumps(reports, ensure_ascii=False, indent=2))
        return 0
    for rep in reports:
        st, me = rep["structure"], rep["meta"]
        print(f"== TV 视频库 #{rep['library_id']} {rep['name']!r} ==")
        print(f"剧 {me['shows']} / 集 {me['episodes']}；待确认剧 {me['review_shows']}，"
              f"未匹配集 {me['unmatched_eps']}，同集多版本 {me['duplicate_pairs']}，"
              f"花絮 {me['extras']}，剧场版 {me['movies']}")
        print(f"结构待整理：包装层 {st['counts'].get('wrapper', 0)}，"
              f"补 Season {st['counts'].get('season', 0)}，"
              f"花絮拍平 {st['counts'].get('extras', 0)}；"
              f"同名冲突 {st['conflicts']}，做种阻断 {st['blocked']} 部")
        for p in st["plans"]:
            tags = []
            if p["wrapper"]:
                tags.append(f"包装层 {p['wrapper']}")
            if p["season"]:
                tags.append(f"补 Season {p['season']}")
            if p["extras"]:
                tags.append(f"花絮 {p['extras']}")
            if p["conflicts"]:
                tags.append(f"冲突 {p['conflicts']}")
            if p["blocked"]:
                tags.append("做种阻断")
            if tags:
                print(f"  {p['title']}: " + "，".join(tags))
        for row in me["show_rows"]:
            bad = []
            if row["unmatched"]:
                bad.append(f"未匹配集 {row['unmatched']}（{','.join(row['unmatched_sample'])}…）")
            if row["duplicate"]:
                bad.append(f"同集多版本 {row['duplicate']}")
            if row["needs_review"]:
                bad.append("待确认")
            if bad:
                print(f"  [元数据] {row['title']}: " + "；".join(bad))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
