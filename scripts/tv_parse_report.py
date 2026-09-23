#!/usr/bin/env python3
"""TV 解析体检（只读，不写 DB、不改盘）：用扫描器同一套规则预演入库结果。

列出：每部剧的解析命中/未知/花絮、季号分布、绝对集号与多集文件数、
失败样本（按目录聚合），用于 T1 扫描器 v2 的验收与排障。

库与媒体路径与主程序同源：环境变量 DATA_DIR（默认 ./data）、MEDIA_ROOT（默认 ./media）。
SMB 直读库走应用内存储后端（凭据取自 DB，不触碰挂载点）。

用法：
    python scripts/tv_parse_report.py                     # 全部启用 TV 视频库
    python scripts/tv_parse_report.py --library 3         # 指定视频库
    python scripts/tv_parse_report.py --media-library 2   # 指定媒体库下全部 TV 库
    python scripts/tv_parse_report.py --samples 8         # 每剧失败样本条数
    python scripts/tv_parse_report.py --json              # 机器可读
"""
import argparse
import json
import os
import sys
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import library_paths, storage                      # noqa: E402
from app.scanner import scan as scan_mod                    # noqa: E402
from app.scanner import tv_parse                            # noqa: E402
from app.scanner.classify import (VIDEO_EXTS, is_sample,    # noqa: E402
                                  is_sidecar, scan_skip_dirs)


def _tv_libraries(args) -> list[dict]:
    libs = library_paths.list_libraries(only_enabled=True)
    if args.media_library is not None:
        libs = [l for l in libs
                if int(l.get("media_library_id") or 0) == int(args.media_library)]
    if args.library is not None:
        libs = [l for l in libs if int(l.get("id") or 0) == int(args.library)]
    return [l for l in libs if str(l.get("kind") or "movie") == "tv"]


def _walk(backend):
    entries = list(backend.iter_tree("", skip_dirs=scan_skip_dirs()))
    vids = [e for e in entries if not e.is_dir
            and os.path.splitext(e.name)[1].lower() in VIDEO_EXTS]
    return entries, vids


def scan_library(lib: dict, samples: int) -> dict:
    lib_id = int(lib["id"])
    backend = storage.backend_for(lib_id)
    entries, vids = _walk(backend)
    plan = scan_mod.tv_plan(entries, vids)
    shows = defaultdict(lambda: {"files": 0, "ok": 0, "unknown": 0, "sidecar": 0,
                                 "special": 0, "absolute": 0, "multi": 0,
                                 "seasons": Counter(), "sources": Counter(),
                                 "fail": []})
    unknown = sidecar_n = ok_n = 0
    for e in vids:
        base = os.path.basename(e.rel)
        p = plan.get(e.rel) or {}
        show_root = p.get("show_root") or ""
        sd = (tv_parse.parse_show_dir(os.path.basename(show_root)) if show_root
              else {"title": "", "year": None})
        title = sd["title"] or "(库根散文件)"
        st = shows[title]
        st["files"] += 1
        if is_sidecar(e.rel, backend=backend, tv=True) or is_sample(base):
            st["sidecar"] += 1
            sidecar_n += 1
            continue
        num = scan_mod.resolve_tv_numbers(base, os.path.dirname(e.rel).rsplit("/", 1)[-1],
                                          show_root, p.get("episode_hint"))
        if not num["ok"]:
            st["unknown"] += 1
            unknown += 1
            if len(st["fail"]) < samples:
                st["fail"].append(e.rel)
            continue
        st["ok"] += 1
        ok_n += 1
        st["seasons"][num["season"]] += 1
        st["sources"][num["source"]] += 1
        if num["special"]:
            st["special"] += 1
        if num["absolute"] is not None:
            st["absolute"] += 1
        if num["episode_end"]:
            st["multi"] += 1
    return {"library_id": lib_id, "name": lib.get("name") or "", "files": len(vids),
            "ok": ok_n, "unknown": unknown, "sidecar": sidecar_n, "shows": dict(shows)}


def _print_report(rep: dict, samples: int) -> None:
    print(f"== 视频库 #{rep['library_id']} {rep['name']!r} ==")
    print(f"视频文件 {rep['files']}：解析成功 {rep['ok']} / 未知 {rep['unknown']}"
          f" / 花絮跳过 {rep['sidecar']}")
    for title in sorted(rep["shows"]):
        st = rep["shows"][title]
        seasons = ",".join(f"S{k}:{v}" for k, v in sorted(st["seasons"].items()))
        flags = []
        if st["special"]:
            flags.append(f"特典 {st['special']}")
        if st["absolute"]:
            flags.append(f"绝对号 {st['absolute']}")
        if st["multi"]:
            flags.append(f"多集文件 {st['multi']}")
        if st["sidecar"]:
            flags.append(f"花絮 {st['sidecar']}")
        print(f"  {title} | 文件 {st['files']} 成功 {st['ok']} 未知 {st['unknown']}"
              f" | 季 {seasons or '-'} | 规则 {dict(st['sources'])}"
              + (" | " + " ".join(flags) if flags else ""))
        for rel in st["fail"][:samples]:
            print(f"      UNKNOWN {rel}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--library", type=int, default=None, help="视频库 id")
    ap.add_argument("--media-library", type=int, default=None, help="媒体库 id")
    ap.add_argument("--samples", type=int, default=5, help="每剧失败样本条数")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()
    libs = _tv_libraries(args)
    if not libs:
        print("没有匹配的 TV 视频库（检查 --library/--media-library 或库 kind）")
        return 1
    reports = []
    for lib in libs:
        try:
            reports.append(scan_library(lib, args.samples))
        except storage.StorageError as e:
            print(f"库 #{lib.get('id')} 不可达：{e}")
    if args.json:
        print(json.dumps(reports, ensure_ascii=False, indent=2))
    else:
        for rep in reports:
            _print_report(rep, args.samples)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
