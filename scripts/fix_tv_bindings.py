#!/usr/bin/env python3
"""一次性修正：TV 集/特典编号口径与 TMDB 对齐（2026-09 用户审核通过）。

覆盖 5 部剧：
  - 进击的巨人（#44）：S01 重排（本地 13=总集篇→S00E01；14-26=官方 13-25）、
    S04E85→S04E26、S05E01/02→S00E36/E37、S00 的 8 个 OAD 重绑（S00E07/E13-E19）；
  - 怪兽8号（#51）：S02E01（保科の休日）→S00E18；S02E02-E12→S01E13-E23；
  - 老友记（#18）：双长集拆分文件绑到同一 TMDB 集（part1/part2，Plex 规范）；
    S06 按探测结果整体重排（E16-E25）；
  - 神探夏洛克（#26）：S03E04（可恶的新娘）→S00E09；
  - 黑镜（#16）：S00E01（白色圣诞节）→S02E04。

文件改名/移动逐个写审计（organize_moves，批次 fix-<ts>，可用「整理历史/撤销」回退），
随后重刮元数据 + force 重写逐集 NFO/海报，最后自行重扫校验。

用法（在仓库根目录、带 DATA_DIR/MEDIA_ROOT 环境）：
    python scripts/fix_tv_bindings.py            # 预览（默认，不动任何文件/DB）
    python scripts/fix_tv_bindings.py --execute  # 执行
"""
import argparse
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import store, storage, tmdb                    # noqa: E402
from app.scanner import tv_organize, tv_nfo_link, tv_persist  # noqa: E402

SIDECAR_EXTS = (".srt", ".ass", ".ssa", ".sup", ".nfo", ".sub", ".idx")

# (show_id, [(from_season, from_episode, to_season, to_episode, part_or_None), ...])
MAPPING = [
    (44, [
        (1, 13, 0, 1, None),                     # 13.5 总集篇《从那天起》
        *[(1, 14 + i, 1, 13 + i, None) for i in range(13)],   # 14-26 → 官方 13-25
        (4, 85, 4, 26, None, True),              # 背叛（保留本地绝对编号 85，只绑元数据）
        (5, 1, 0, 36, None), (5, 2, 0, 37, None),  # 最终季 完结篇 上/下
        *[(0, 1 + i, 0, n, None) for i, n in enumerate((7, 13, 14, 15, 16, 17, 18, 19))],
    ]),
    (51, [
        (2, 1, 0, 18, None),                     # 保科の休日
        *[(2, 2 + i, 1, 13 + i, None) for i in range(11)],    # 第二季 1-11
    ]),
    (18, (
        [(sn, 23, sn, 23, 1) for sn in (4, 5, 7, 8)]
        + [(sn, 24, sn, 23, 2) for sn in (4, 5, 7, 8)]
        + [(6, 15, 6, 15, 1), (6, 16, 6, 15, 2)]
        + [(6, 17 + i, 6, 16 + i, None) for i in range(7)]    # E17-E23 → E16-E22
        + [(6, 24, 6, 23, 1), (6, 25, 6, 23, 2)]
    )),
    (26, [(3, 4, 0, 9, None)]),                  # 可恶的新娘
    (16, [(0, 1, 2, 4, None)]),                  # 白色圣诞节
]


def _backend(lib_id):
    return storage.backend_for(int(lib_id))


def _sidecars(backend, src_dir, stem):
    """源目录下与 stem 同茎的附属文件（.nfo/字幕），返回 [(name, suffix)]。"""
    out = []
    try:
        entries = backend.list(src_dir) if src_dir else backend.list("")
    except Exception:
        return out
    for e in entries:
        if e.get("is_dir"):
            continue
        name = str(e["name"])
        fstem, fext = os.path.splitext(name)
        if fext.lower() not in SIDECAR_EXTS:
            continue
        if fstem == stem or fstem.startswith(stem + ".") or fstem.startswith(stem + "-"):
            out.append((name, name[len(stem):]))
    return out


def build_plan():
    """生成操作清单（只读）：[{show_id,title,lib_id,ep_id,from,to,season,episode,
    tmdb_id,tmdb_name,part,audit_kind}, ...] + conflicts。"""
    ops, conflicts, missing = [], [], []
    tmdb_index: dict[tuple, dict] = {}
    for show_id, rows in MAPPING:
        show = store.get_show(show_id)
        if not show:
            missing.append(f"show {show_id} 不存在")
            continue
        title = tv_organize._clean_name(show.get("title"), 120)
        tmdb_id = int(show.get("tmdb_id") or 0)
        eps = {int(e["id"]): e for e in store.list_episodes(show_id)}
        by_key = {(int(e["season"] or 0), int(e["episode"] or 0)): e for e in eps.values()}
        by_tok: dict[tuple, list] = {}
        for e in eps.values():
            m = re.search(r"S(\d{2})E(\d{2})", os.path.basename(str(e["file_path"] or "")), re.I)
            if m:
                by_tok.setdefault((int(m.group(1)), int(m.group(2))), []).append(e)
        for row in rows:
            fs, fe, ts, te, part = row[:5]
            keep = bool(row[5]) if len(row) > 5 else False
            cands = by_tok.get((fs, fe), [])
            e = cands[0] if len(cands) == 1 else None
            if e is None:
                e = by_key.get((fs, fe)) or by_key.get((ts, te))
            if e is None:
                missing.append(f"{show.get('title')} S{fs:02d}E{fe:02d} 本地不存在")
                continue
            key = (tmdb_id, int(ts), int(te))
            if key not in tmdb_index:
                try:
                    data = tmdb.tv_season(tmdb_id, ts)
                except Exception as ex:
                    conflicts.append(f"{show.get('title')} TMDB S{ts:02d} 查询失败: {ex}")
                    continue
                for ep in data.get("episodes") or []:
                    if int(ep.get("episode_number") or 0) == te:
                        tmdb_index[key] = {"id": int(ep.get("id")),
                                           "name": str(ep.get("name") or "")}
                        break
                else:
                    conflicts.append(f"{show.get('title')} TMDB 无 S{ts:02d}E{te:02d}")
                    continue
            tm = tmdb_index[key]
            db_s, db_e = (fs, fe) if keep else (ts, te)
            base = f"{title}-S{db_s:02d}E{db_e:02d}"
            ep_name = tv_organize._clean_name(tm.get("name") or "")
            stem = f"{base}-{ep_name}" if ep_name else base
            if part:
                stem += f"-part{int(part)}"
            src = str(e["file_path"])
            src_dir = os.path.dirname(src)
            src_stem = os.path.splitext(os.path.basename(src))[0]
            ext = os.path.splitext(src)[1]
            ops.append({"show_id": show_id, "title": title, "lib_id": int(e["library_id"]),
                        "ep_id": int(e["id"]), "tmdb_id": tm.get("id"),
                        "tmdb_name": tm.get("name") or "",
                        "season": int(db_s), "episode": int(db_e), "part": part,
                        "keep": keep,
                        "from": src, "stem": stem, "ext": ext,
                        "src_dir": src_dir, "src_stem": src_stem, "to": None,
                        "kind": "episode"})
    return ops, conflicts, missing


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--execute", action="store_true", help="真的执行（默认只预览）")
    args = ap.parse_args()
    store.init_db()
    ops, conflicts, missing = build_plan()
    if missing:
        print("!! 缺行：")
        for m in missing:
            print("   ", m)
    if conflicts:
        print("!! 冲突/查询失败：")
        for c in conflicts:
            print("   ", c)

    # 解析终态路径（含同茎附属文件）
    backend_cache: dict[int, object] = {}
    plan: list[dict] = []
    for op in ops:
        be = backend_cache.setdefault(op["lib_id"], _backend(op["lib_id"]))
        season_dir = op["src_dir"].rsplit("/", 1)[0]  # 剧根（源文件所在季目录的父目录）
        target_dir = f"{season_dir}/Season {op['season']:02d}"
        op["to"] = f"{target_dir}/{op['stem']}{op['ext']}"
        op["files"] = [(op["from"], op["to"])]
        for name, suffix in _sidecars(be, op["src_dir"], op["src_stem"]):
            op["files"].append((f"{op['src_dir']}/{name}",
                                f"{target_dir}/{op['stem']}{suffix}"))
        # 断点续跑：主文件已在终态时，从目标目录反推附属文件（避免漏审计导致撤销不完整）
        try:
            if not be.exists(op["from"]) and be.exists(op["to"]):
                known = {f[1] for f in op["files"]}
                for name, suffix in _sidecars(be, target_dir, op["stem"]):
                    tpath = f"{target_dir}/{name}"
                    if tpath in known:
                        continue
                    op["files"].append((f"{op['src_dir']}/{op['src_stem']}{suffix}",
                                        tpath))
        except Exception:
            pass
        plan.append(op)

    print(f"\n== 修正明细（{len(plan)} 集 / {sum(len(o['files']) for o in plan)} 个文件）==")
    cur = None
    for op in plan:
        if op["title"] != cur:
            cur = op["title"]
            print(f"\n[{cur}]")
        part = f" part{op['part']}" if op["part"] else ""
        keep = " [保留本地编号]" if op.get("keep") else ""
        print(f"  S{op['season']:02d}E{op['episode']:02d}{part}{keep}  "
              f"{os.path.basename(op['from'])}\n      -> {op['to']}")
    if not args.execute:
        print("\n（预览模式，未做任何修改；确认后加 --execute 执行）")
        return 0

    # ---- 执行：逐剧处理（文件改名两阶段防环 → DB 行更新 → 审计） ----
    batch = tv_organize._new_batch_id("fix")
    total_files = total_db = total_failed = 0
    for show_id in dict.fromkeys(o["show_id"] for o in plan):
        show_ops = [o for o in plan if o["show_id"] == show_id]
        be = backend_cache[show_ops[0]["lib_id"]]
        title = show_ops[0]["title"]
        print(f"\n[{title}] 执行…", flush=True)
        # 1) 收集所有文件操作并做防环改名
        file_ops = [f for o in show_ops for f in o["files"]]
        done: set = set()
        guard = 0
        while len(done) < len(file_ops) and guard < len(file_ops) * 4:
            guard += 1
            progressed = False
            for i, (src, dst) in enumerate(file_ops):
                if i in done:
                    continue
                try:
                    if not be.exists(src):
                        if be.exists(dst):
                            done.add(i)          # 已完成（断点续跑）
                            continue
                        print(f"    !! 源与目标都不存在: {src}")
                        done.add(i)
                        total_failed += 1
                        continue
                    if be.exists(dst):
                        continue
                    be.rename(src, dst)
                    done.add(i)
                    progressed = True
                except Exception as ex:
                    print(f"    !! rename 失败 {src} -> {dst}: {str(ex)[:120]}")
                    done.add(i)
                    total_failed += 1
            if not progressed:
                # 环：把第一个未完成项换成临时名
                for i, (src, dst) in enumerate(file_ops):
                    if i in done:
                        continue
                    tmp = f"{src}.fixtmp"
                    try:
                        be.rename(src, tmp)
                        file_ops[i] = (tmp, dst)
                    except Exception as ex:
                        print(f"    !! 临时改名失败 {src}: {str(ex)[:120]}")
                        done.add(i)
                        total_failed += 1
                    break
        total_files += len(done)
        # 2) DB：更新 season/episode/绑定/标题，并搬移路径
        mapping = {}
        for o in show_ops:
            try:
                meta = {"season": o["season"], "episode": o["episode"],
                        "needs_review": 0}
                if o["tmdb_id"]:
                    meta["tmdb_episode_id"] = o["tmdb_id"]
                store.update_episode_meta(o["ep_id"], **meta)
                mapping[o["from"]] = o["to"]
                total_db += 1
            except Exception as ex:
                print(f"    !! DB 更新失败 ep={o['ep_id']}: {str(ex)[:120]}")
                total_failed += 1
        if mapping:
            store.move_tv_paths(show_ops[0]["lib_id"], mapping)
        # 3) 审计
        audit = []
        for o in show_ops:
            audit.append({"library_id": o["lib_id"], "show_id": show_id,
                          "kind": "episode", "obj": "file", "action": "fix-bind",
                          "from_path": o["from"], "to_path": o["to"]})
            for src, dst in o["files"][1:]:
                audit.append({"library_id": o["lib_id"], "show_id": show_id,
                              "kind": "file", "obj": "file", "action": "fix-bind",
                              "from_path": src, "to_path": dst})
        if audit:
            store.record_organize_moves(batch, audit, library_id=show_ops[0]["lib_id"])
        # 4) 清空目录（源季目录若空）
        for d in sorted({o["src_dir"] for o in show_ops}, key=lambda x: -x.count("/")):
            try:
                if be.exists(d):
                    be.delete(d)
                    print(f"    rmdir {d}")
            except Exception:
                pass
        # 5) 重刮元数据 + force 重写 NFO/海报
        try:
            show = store.get_show(show_id)
            tv_persist.scrape_show(show, force=True)
            print("    scrape ok")
        except Exception as ex:
            print(f"    !! scrape 失败: {str(ex)[:140]}")
        try:
            tv_nfo_link.sync_tv_nfos_for(show_id, backend=be, force=True,
                                         episode_nfo=True)
            from app import artwork
            artwork.write_for_show(show_id, backend=be)
            print("    NFO/海报已重写")
        except Exception as ex:
            print(f"    !! NFO 重写失败: {str(ex)[:140]}")

    print(f"\n完成：文件 {total_files}，DB 行 {total_db}，失败 {total_failed}，"
          f"审计批次 {batch}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
