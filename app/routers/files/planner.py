"""routers.files.planner（自 app/routers/files.py 拆分，评审 B9/R09-Q1；经 files 门面使用）。

命名档（D5）：库级 `naming_profile`。
- kodi：`标题 (年份)[-版本][-规格][-分卷][-版本N].ext`（单 `-` 直连，兼容旧库）
- plex：`标题 (年份) {edition-版本}/标题 (年份) {edition-版本} - 规格 - 分卷.ext`
- off ：不参与改名（直接跳过该库所有行）

归档一律扁平（D5，不再按大区建二级目录）。
"""
import os
import re
from ... import library_paths
from ... import store
from ...editions import core_of
from ...editions import detect_edition
from ...editions import detect_spec_list
from ...editions import sanitize_tag
from ...editions import split_stack
from ...scanner import is_sidecar
from ...log import get_logger
logger = get_logger("files.planner")
from .paths import _safe_component, _exists, _is_file
__all__ = ['_ordered_plans', '_MOVIE_DIR_RE', '_is_movie_dir_name', '_dir_owned_by_comp',
           '_components',
           '_stem_of', '_target_for', '_finalize', '_keeper_of', '_collect_plans']


_MOVIE_DIR_RE = re.compile(r"^.+ \(\d{4}\)(\s*\{edition-[^}]+\})?$")  # 片目录形态（含 plex edition）


def _is_movie_dir_name(name: str) -> bool:
    return bool(_MOVIE_DIR_RE.match((name or "").strip()))


# 罗马数字/带圈序号归一：Ⅰ→1、Ⅻ→12（含小写），便于「加菲猫Ⅰ」↔「加菲猫1」
_ROMAN_TRANS = {}
for _i in range(12):
    _ROMAN_TRANS[0x2160 + _i] = str(_i + 1)   # Ⅰ-Ⅻ
    _ROMAN_TRANS[0x2170 + _i] = str(_i + 1)   # ⅰ-ⅻ
_ASCII_ROMAN = {"i": "1", "ii": "2", "iii": "3", "iv": "4", "v": "5", "vi": "6",
                "vii": "7", "viii": "8", "ix": "9", "x": "10"}
# CJK 之间的装饰性标点合并（创：战纪 → 创战纪；燃烧吧!!热战·烈战 → 燃烧吧热战烈战）
_CJK_PUNCT_RE = re.compile(
    r"(?<=[\u4e00-\u9fff])[·．.、，,：:；;！!？?（）()\[\]【】《》〈〉「」『』\-—～~]+"
    r"(?=[\u4e00-\u9fff])")


def _norm_tokens(s) -> set:
    s = str(s or "").lower().translate(_ROMAN_TRANS)
    s = _CJK_PUNCT_RE.sub("", s)
    out = set()
    for t in re.split(r"[^0-9a-z\u4e00-\u9fff]+", s):
        if t:
            out.add(_ASCII_ROMAN.get(t, t))
    return out


def _dir_owned_by_comp(name: str, comp: dict) -> bool:
    """父目录名是否像本片自己的目录：目录名与「文件名茎 ∪ 标题 ∪ 原名」分词覆盖率 ≥60%。
    证据：12.Monkeys.1995/、壮志凌云2.Top.Gun.Maverick.2022/、望夫成龙.Love.is.Love.1990/；
    排除：olddir/、变形金刚系列.Transformers/（50%）、海贼王剧场版/、周星驰.Stephen Chow/。"""
    dt = _norm_tokens(name)
    if not dt:
        return False
    m = comp.get("m") or {}
    stem = os.path.splitext(os.path.basename(comp.get("from") or ""))[0]
    ft = (_norm_tokens(stem) | _norm_tokens(m.get("title"))
          | _norm_tokens(m.get("original_title")))
    if not ft:
        return False
    inter = dt & ft
    return bool(inter) and len(inter) * 10 >= len(dt) * 6


def _dir_of(base: str, edition: str, profile: str) -> str:
    """片目录名：plex 档 edition 进目录（官方推荐文件夹+文件名都带）。"""
    if profile == "plex" and edition:
        ed = re.sub(r"[{}]", "", edition)
        return f"{base} {{edition-{ed}}}"
    return base


def _stem_of(base: str, edition: str, specs: list[str], stack: str,
             numbered: str = "", profile: str = "kodi") -> str:
    if profile == "plex":
        s = base
        if edition:
            ed = re.sub(r"[{}]", "", edition)
            s += f" {{edition-{ed}}}"
        for sp in specs:
            if sp:
                s += f" - {sp}"
        if stack:
            s += f" - {stack}"
        if numbered:
            s += f" - {numbered}"
        return s
    s = base
    if edition:
        s += f"-{edition}"
    for sp in specs:
        if sp:
            s += f"-{sp}"
    if stack:
        s += f"-{stack}"
    if numbered:
        s += f"-{numbered}"
    return s


def _plex_warnings(stem: str, comp: dict, numbered: str) -> list[str]:
    """Plex 兼容性检查（仅 plex 档）：返回人类可读告警。"""
    out: list[str] = []
    if "{" in stem or "}" in stem:
        out.append("文件名含花括号，可能破坏 Plex edition 语法")
    if re.search(r'[<>:"\\|?*]', stem):
        out.append("文件名含非法字符")
    if comp.get("stack"):
        out.append(f"分卷（{comp['stack']}）在 Plex NFO Agent 下不支持，仅作多版本命名")
    if numbered and not comp.get("edition") and not comp.get("labels"):
        out.append("同目标多版本仅用「版本N」区分，Plex 可能合并显示")
    if comp.get("labels"):
        for sp in comp["labels"][:1]:
            if sp and not re.match(r"^[A-Za-z0-9 ._\-]{1,32}$", sp):
                out.append(f"规格「{sp}」含非 ASCII，Plex 识别不稳定")
    return out


def _components(m: dict) -> dict | None:
    """单行规划要素：base/edition/spec标签列表/stack/命名档。DB 为空时从现文件名识别回填。"""
    if not m.get("title") or not m.get("year"):
        return None
    if is_sidecar(m["file_path"]):
        return None
    title = _safe_component(m["title"])
    if not title:
        return None
    lib_id = m.get("library_id") or library_paths.DEFAULT_LIBRARY_ID
    profile = library_paths.naming_profile(lib_id)
    if profile == "off":
        return None
    basename = os.path.basename(m["file_path"])
    edition = sanitize_tag(m.get("edition") or "") or detect_edition(basename)
    db_spec = sanitize_tag(m.get("spec") or "")
    labels = [db_spec] if db_spec else detect_spec_list(basename)
    cur_stem = os.path.splitext(basename)[0]
    _, stack = split_stack(cur_stem)
    return {"id": m["id"], "from": m["file_path"],
            "ext": os.path.splitext(m["file_path"])[1],
            "base": f"{title} ({m['year']})",
            "edition": edition, "labels": labels, "stack": stack,
            "profile": profile,
            "core": core_of(basename), "m": m}


def _target_for(comp: dict, stem: str, target_root: str | None) -> str:
    if target_root is None:
        parent = os.path.dirname(comp["from"])
        # 影片专属目录不再套娃：父目录为规范片目录形态（含旧标题/plex edition），或目录名
        # 与影片文件名/标题高度匹配（12.Monkeys.1995/）时上跳取祖父；连续上跳（≤8）顺带
        # 修复历史套娃。合集目录（周星驰.Stephen Chow 等名字不匹配的）保留，向下套一层。
        depth = 0
        while parent and depth < 8:
            name = os.path.basename(parent)
            if (name == comp["base"] or _is_movie_dir_name(name)
                    or _dir_owned_by_comp(name, comp)):
                parent = os.path.dirname(parent)
                depth += 1
            else:
                break
        return os.path.join(parent, _dir_of(comp["base"], comp["edition"],
                                            comp["profile"]), stem + comp["ext"])
    return os.path.join(target_root, _dir_of(comp["base"], comp["edition"],
                                             comp["profile"]), stem + comp["ext"])


def _finalize(comp: dict, stem: str, target_root: str | None,
              numbered: str = "", spec_used: str = "") -> dict | None:
    new_rel = _target_for(comp, stem, target_root)
    if os.path.normpath(new_rel) == os.path.normpath(comp["from"]):
        return None
    lib_id = comp["m"].get("library_id") or library_paths.DEFAULT_LIBRARY_ID
    d = {"id": comp["id"], "from": comp["from"], "to": new_rel,
         "title": comp["m"].get("title", ""),
         "tmdb_id": comp["m"].get("tmdb_id"),
         "library_id": lib_id,
         "edition": comp["edition"], "spec": spec_used,
         "stack": comp["stack"], "naming_profile": comp["profile"]}
    if numbered:
        d["numbered"] = numbered
    if comp["profile"] == "plex":
        warns = _plex_warnings(stem, comp, numbered)
        if warns:
            d["plex_warnings"] = warns
    if not _is_file(lib_id, comp["from"]):
        d["status"] = "source_missing"   # 预览即标注（评审 R09-D6），执行会跳过
    return d


def _keeper_of(comp: dict) -> dict:
    m = comp["m"]
    return {"id": comp["id"], "file_path": comp["from"],
            "title": m.get("title", ""), "tmdb_id": m.get("tmdb_id")}


def _collect_plans(target_root: str | None = None,
                   only: set | None = None,
                   all_rows: list | None = None,
                   library_id: int | None = None) -> tuple[list, list]:
    # all_rows 允许调用方传入已读的全表（评审 99 §2.2：relocate 免二次全表读）；
    # 库内占用检查需要全表，因此不能只传 scoped 子集。
    all_rows = (all_rows if all_rows is not None
                else store.list_movies(grouped=False, limit=100000))
    # 目标路径 → 占用行 id。按 (库, 路径) 键控（多库 v12）：不同库同名相对路径互不冲突。
    db_owner = {}
    db_dir_keys: dict[tuple[int, str], set] = {}   # (库, 目录) → 影片标识集合
    for m in all_rows:
        lid = int(m.get("library_id") or library_paths.DEFAULT_LIBRARY_ID)
        rel = os.path.normpath(m["file_path"])
        db_owner[(lid, rel)] = m["id"]
        d = os.path.dirname(rel)
        if d:
            db_dir_keys.setdefault((lid, d), set()).add(
                m.get("tmdb_id") or -int(m["id"]))
    rows = all_rows
    if library_id is not None:
        rows = [m for m in rows if int(m.get("library_id")
                                       or library_paths.DEFAULT_LIBRARY_ID) == int(library_id)]
    if only:
        rows = [m for m in rows if m["id"] in only]

    # naming_profile=off 的库不参与改名（_components 返回 None 即被过滤）
    comps = [c for m in rows if (c := _components(m))]

    def _dir_of_comp(c: dict) -> tuple[int, str] | None:
        lid = int(c["m"].get("library_id") or library_paths.DEFAULT_LIBRARY_ID)
        d = os.path.dirname(os.path.normpath(c["from"]))
        return (lid, d) if d else None

    # 目录级归属判定（多版本目录中只要有一个文件名/标题与目录名匹配即算专属目录）：
    # 目录是操作单位，只在“该目录只属于这一部影片”时参与整目录改名。
    cand_dirs = {k for c in comps if (k := _dir_of_comp(c))}
    dir_comps: dict[tuple[int, str], list] = {}
    if cand_dirs:
        for m in all_rows:
            lid = int(m.get("library_id") or library_paths.DEFAULT_LIBRARY_ID)
            d = os.path.dirname(os.path.normpath(m["file_path"]))
            if not d or (lid, d) not in cand_dirs:
                continue
            c = _components(m)
            if c:
                dir_comps.setdefault((lid, d), []).append(c)
    dir_owned: dict[tuple[int, str], bool] = {}
    for key, cs in dir_comps.items():
        name = os.path.basename(key[1])
        if len(db_dir_keys.get(key, ())) > 1:
            dir_owned[key] = False          # 多片共享目录：绝不整目录改名
        elif _is_movie_dir_name(name):
            dir_owned[key] = True
        else:
            dir_owned[key] = any(_dir_owned_by_comp(name, c) for c in cs)

    # 专属目录内全部影片行一并纳入（only 子集执行也整目录带走，避免半拉）；
    # 非专属目录只保留选中行（共享目录里别的片不能被顺带移动）
    expanded: list = []
    seen_ids: set = set()

    def _add(c: dict, owned: bool) -> None:
        if c["id"] in seen_ids:
            return
        seen_ids.add(c["id"])
        c["dir_owned"] = owned
        expanded.append(c)

    for c in comps:
        key = _dir_of_comp(c)
        if key and dir_owned.get(key):
            for cc in dir_comps.get(key, []):
                _add(cc, True)
        else:
            _add(c, False)
    comps = expanded
    comp_by_id = {c["id"]: c for c in comps}
    current_paths = {(int(c["m"].get("library_id") or library_paths.DEFAULT_LIBRARY_ID),
                      os.path.normpath(c["from"])) for c in comps}

    # 第一遍：版本+分卷（无规格），按目标分组
    groups: dict[str, list] = {}
    stem0: dict[int, str] = {}
    for c in comps:
        s = _stem_of(c["base"], c["edition"], [], c["stack"], profile=c["profile"])
        stem0[c["id"]] = s
        groups.setdefault(os.path.normpath(
            _target_for(c, s, target_root)), []).append(c)
    plans, conflicts = [], []
    for _, members in sorted(groups.items()):
        members = sorted(members, key=lambda x: x["id"])
        if len(members) == 1:
            p = _finalize(members[0], stem0[members[0]["id"]], target_root)
            if p:
                plans.append(p)
            continue
        # 多行同目标：用户已手工设互异规格→直接信任；主干一致→真变体渐进消解；
        # 否则疑似错配（等人工重匹配，不自动加后缀）
        db_specs = [sanitize_tag(c["m"].get("spec") or "") for c in members]
        if all(db_specs) and len(set(zip(
                [c["edition"] for c in members], db_specs))) == len(members):
            for c, sp in zip(members, db_specs):
                p = _finalize(c, _stem_of(c["base"], c["edition"], [sp],
                                          c["stack"], profile=c["profile"]),
                              target_root, spec_used=sp)
                if p:
                    plans.append(p)
            continue
        cores = {c["core"] for c in members}
        if len(cores) > 1:
            # 疑似错配（如 Part.1/Part.2 綁同 tmdb）：不自动加后缀，等人工重匹配
            keeper = _keeper_of(members[0])
            for c in members:
                conflicts.append({"id": c["id"], "from": c["from"],
                                  "to": _target_for(c, stem0[c["id"]], target_root),
                                  "edition": c["edition"], "stack": c["stack"],
                                  "title": c["m"].get("title", ""),
                                  "tmdb_id": c["m"].get("tmdb_id"),
                                  "library_id": c["m"].get("library_id")
                                  or library_paths.DEFAULT_LIBRARY_ID,
                                  "status": "conflict_needs_rematch",
                                  "kind": "suspect_mismatch",
                                  "conflict_with": keeper["id"],
                                  "keeper": keeper})
            continue
        # 真变体：逐级追加规格（最小后缀），仍撞车用 -版本N 兜底
        maxd = max(len(c["labels"]) for c in members)
        resolved = False
        for d in range(1, maxd + 1):
            cand = {c["id"]: _stem_of(c["base"], c["edition"],
                                      list(c["labels"][:d]), c["stack"],
                                      profile=c["profile"])
                    for c in members}
            if len(set(cand.values())) == len(members):
                for c in members:
                    specs = list(c["labels"][:d])
                    p = _finalize(c, cand[c["id"]], target_root,
                                  spec_used="-".join(specs))
                    if p:
                        plans.append(p)
                resolved = True
                break
        if resolved:
            continue
        # 规格词完全一致的双胞胎：首个保持，其余 -版本N（临时计算、永远末尾）
        tied: dict[str, list] = {}
        for c in members:
            specs = list(c["labels"][:maxd]) if maxd else []
            tied.setdefault(_stem_of(c["base"], c["edition"], specs,
                                     c["stack"], profile=c["profile"]), []).append(c)
        for _, group in sorted(tied.items()):
            group = sorted(group, key=lambda x: x["id"])
            first = group[0]
            specs = list(first["labels"][:maxd]) if maxd else []
            p = _finalize(first, _stem_of(first["base"], first["edition"],
                                          specs, first["stack"],
                                          profile=first["profile"]),
                          target_root, spec_used="-".join(specs))
            if p:
                plans.append(p)
            for i, c in enumerate(group[1:], start=2):
                numbered = f"版本{i}"
                p = _finalize(c, _stem_of(c["base"], c["edition"], specs,
                                          c["stack"], numbered,
                                          profile=c["profile"]),
                              target_root, numbered=numbered,
                              spec_used="-".join(specs))
                if p:
                    plans.append(p)

    # 目录计划：专属目录（单影片）内全部文件目标同一新目录且目录名要变时，
    # 合并为「整目录改名」——Sample/封面/截图等非影片内容原名跟随，正片按上面
    # 解析出的规范名在目录内改名（用户需求 2026-09）。
    kept, by_dir = [], {}
    for p in plans:
        c = comp_by_id.get(p["id"])
        lib_id = int(p.get("library_id") or library_paths.DEFAULT_LIBRARY_ID)
        from_dir = os.path.dirname(os.path.normpath(p["from"]))
        to_dir = os.path.dirname(os.path.normpath(p["to"]))
        if (c and c.get("dir_owned") and from_dir
                and len(db_dir_keys.get((lib_id, from_dir), ())) <= 1
                and to_dir and os.path.normpath(to_dir) != os.path.normpath(from_dir)):
            by_dir.setdefault((lib_id, from_dir), []).append(p)
        else:
            kept.append(p)
    dir_plans = []
    for (lib_id, from_dir), members in sorted(by_dir.items()):
        targets = {os.path.normpath(os.path.dirname(m["to"])) for m in members}
        if len(targets) != 1:
            kept.extend(members)      # plex 多 edition 各自目录：退回逐文件
            continue
        members.sort(key=lambda x: x["id"])
        files = [m for m in members if m.get("status") != "source_missing"]
        p = {"kind": "dir", "id": members[0]["id"],
             "movie_ids": sorted({m["id"] for m in members}),
             "from": from_dir, "to": targets.pop(), "files": files,
             "title": members[0].get("title", ""),
             "tmdb_id": members[0].get("tmdb_id"),
             "library_id": lib_id,
             "edition": members[0].get("edition") or "",
             "naming_profile": members[0].get("naming_profile") or "kodi",
             "flatten": True}
        if not _exists(lib_id, from_dir):
            p["status"] = "source_missing"
        dir_plans.append(p)
    plans = kept + dir_plans

    # 磁盘/库内占用检查：目标已被库外文件或另一条库行占用则冲突
    final_plans = []
    claimed_dirs: dict[tuple[int, str], int] = {}
    current_dirs = {(int(c["m"].get("library_id") or library_paths.DEFAULT_LIBRARY_ID),
                     os.path.dirname(os.path.normpath(c["from"]))) for c in comps}
    for p in plans:
        lib_id = int(p.get("library_id") or library_paths.DEFAULT_LIBRARY_ID)
        if p.get("kind") == "dir":
            dst_dir = os.path.normpath(p["to"])
            occupied = db_dir_keys.get((lib_id, dst_dir), set())
            owner_ok = occupied <= set(p["movie_ids"])
            file_bad = None
            for f in p.get("files") or []:
                o = db_owner.get((lib_id, os.path.normpath(f["to"])))
                if o is not None and o not in p["movie_ids"]:
                    file_bad = "conflict_db_occupied"
                    break
            if claimed_dirs.get((lib_id, dst_dir)) is not None:
                conflicts.append({**p, "status": "conflict_disk_exists",
                                  "kind": "disk"})
            elif (_exists(lib_id, p["to"])
                  and (lib_id, dst_dir) not in current_dirs):
                conflicts.append({**p, "status": "conflict_disk_exists",
                                  "kind": "disk"})
            elif not owner_ok or file_bad:
                conflicts.append({**p, "status": "conflict_db_occupied",
                                  "kind": "db"})
            else:
                claimed_dirs[(lib_id, dst_dir)] = p["id"]
                final_plans.append(p)
            continue
        dst = os.path.normpath(p["to"])
        owner = db_owner.get((lib_id, dst))
        if (_exists(lib_id, p["to"]) and (lib_id, dst) not in current_paths):
            conflicts.append({**p, "status": "conflict_disk_exists",
                              "kind": "disk"})
        elif owner is not None and owner != p["id"]:
            conflicts.append({**p, "status": "conflict_db_occupied",
                              "kind": "db"})
        else:
            final_plans.append(p)
    return final_plans, conflicts


def _ordered_plans(plans: list) -> list:
    """链式改名/换位排序（评审 R09-D2）：先执行目标不被其他计划占用的计划。

    例：A→B 且 B→C：B→C 的目标 C 无人占用 → 先做；再做 A→B，避免无谓的
    conflict_disk_exists。存在环（A↔B）时退回原顺序（执行侧不覆盖，重跑可收敛）。"""
    rest = list(plans)
    out: list = []
    while rest:
        progressed = False
        for p in list(rest):
            tgt = os.path.normpath(p["to"])
            blocked = any(q is not p and os.path.normpath(q["from"]) == tgt for q in rest)
            if not blocked:
                out.append(p)
                rest.remove(p)
                progressed = True
        if not progressed:
            out.extend(rest)   # 环：无法拓扑排序，原顺序执行
            break
    return out
