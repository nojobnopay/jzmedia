"""routers.files.planner（自 app/routers/files.py 拆分，评审 B9/R09-Q1；经 files 门面使用）。"""
import os
import re
from ... import store
from ...config import settings
from ...editions import core_of
from ...editions import detect_edition
from ...editions import detect_spec_list
from ...editions import sanitize_tag
from ...editions import split_stack
from ...regions import REGION_ORDER
from ...regions import REGION_UNKNOWN
from ...scanner import is_sidecar
from ...log import get_logger
logger = get_logger("files.planner")
from .paths import _safe_component
__all__ = ['_ordered_plans', '_REGION_SET', '_MOVIE_DIR_RE', '_is_movie_dir_name', '_region_stale', '_components', '_stem_of', '_target_for', '_finalize', '_keeper_of', '_collect_plans']

_REGION_SET = set(REGION_ORDER)


_MOVIE_DIR_RE = None  # 惰性编译：片目录形态 `X (YYYY)`


def _is_movie_dir_name(name: str) -> bool:
    global _MOVIE_DIR_RE
    if _MOVIE_DIR_RE is None:
        import re as _re
        _MOVIE_DIR_RE = _re.compile(r"^.+ \(\d{4}\)$")
    return bool(_MOVIE_DIR_RE.match((name or "").strip()))


def _region_stale(file_path: str, region: str) -> bool:
    """路径中含分区名且与 DB region 不一致（如改绑换了产地）：提示用搬迁修复。"""
    if not (region or "").strip():
        return False
    parts = (file_path or "").replace("\\", "/").split("/")
    return any(p in _REGION_SET and p != region for p in parts)


def _components(m: dict) -> dict | None:
    """单行规划要素：base/edition/spec标签列表/stack。DB 为空时从现文件名识别回填。"""
    if not m.get("title") or not m.get("year"):
        return None
    if is_sidecar(m["file_path"]):
        return None
    title = _safe_component(m["title"])
    if not title:
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
            "core": core_of(basename), "m": m}


def _stem_of(base: str, edition: str, specs: list[str], stack: str,
             numbered: str = "") -> str:
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


def _target_for(comp: dict, stem: str, target_root: str | None,
                group_by_region: bool) -> str:
    if target_root is None:
        parent = os.path.dirname(comp["from"])
        # 已在归档目录内不再套娃：父目录名即 base（同名）或任一片目录形态 X (YYYY)
        #（如改绑换了标题，父目录是旧 base）时改取祖父目录，保证就地整理幂等
        if (os.path.basename(parent) == comp["base"]
                or _is_movie_dir_name(os.path.basename(parent))):
            parent = os.path.dirname(parent)
        return os.path.join(parent, comp["base"], stem + comp["ext"])
    region = (comp["m"].get("region") or "").strip() or REGION_UNKNOWN
    parts = [target_root]
    if group_by_region:
        parts.append(region)
    parts += [comp["base"], stem + comp["ext"]]
    return os.path.join(*parts)


def _finalize(comp: dict, stem: str, target_root: str | None,
              group_by_region: bool, numbered: str = "",
              spec_used: str = "") -> dict | None:
    new_rel = _target_for(comp, stem, target_root, group_by_region)
    if os.path.normpath(new_rel) == os.path.normpath(comp["from"]):
        return None
    d = {"id": comp["id"], "from": comp["from"], "to": new_rel,
         "title": comp["m"].get("title", ""),
         "tmdb_id": comp["m"].get("tmdb_id"),
         "edition": comp["edition"], "spec": spec_used,
         "stack": comp["stack"],
         "region_stale": _region_stale(comp["from"], comp["m"].get("region") or "")}
    if numbered:
        d["numbered"] = numbered
    if not os.path.isfile(os.path.join(settings.media_root, comp["from"])):
        d["status"] = "source_missing"   # 预览即标注（评审 R09-D6），执行会跳过
    return d


def _keeper_of(comp: dict) -> dict:
    m = comp["m"]
    return {"id": comp["id"], "file_path": comp["from"],
            "title": m.get("title", ""), "tmdb_id": m.get("tmdb_id")}


def _collect_plans(target_root: str | None = None,
                   group_by_region: bool = False,
                   only: set | None = None) -> tuple[list, list]:
    all_rows = store.list_movies(grouped=False, limit=100000)
    # 目标路径 → 库内占用行 id。磁盘缺失的 missing 行也占 UNIQUE(file_path)，
    # 执行前必须挡掉（评审 P1-06：否则 rename 成功而 DB 更新失败，盘库不一致）。
    db_owner = {os.path.normpath(m["file_path"]): m["id"] for m in all_rows}
    rows = [m for m in all_rows if m["id"] in only] if only else all_rows
    comps = [c for m in rows if (c := _components(m))]
    current_paths = {os.path.normpath(m["file_path"]) for m in rows}
    # 第一遍：版本+分卷（无规格），按目标分组
    groups: dict[str, list] = {}
    stem0: dict[int, str] = {}
    for c in comps:
        s = _stem_of(c["base"], c["edition"], [], c["stack"])
        stem0[c["id"]] = s
        groups.setdefault(os.path.normpath(
            _target_for(c, s, target_root, group_by_region)), []).append(c)
    plans, conflicts = [], []
    for _, members in sorted(groups.items()):
        members = sorted(members, key=lambda x: x["id"])
        if len(members) == 1:
            p = _finalize(members[0], stem0[members[0]["id"]],
                          target_root, group_by_region)
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
                                          c["stack"]),
                              target_root, group_by_region, spec_used=sp)
                if p:
                    plans.append(p)
            continue
        cores = {c["core"] for c in members}
        if len(cores) > 1:
            # 疑似错配（如 Part.1/Part.2 綁同 tmdb）：不自动加后缀，等人工重匹配
            keeper = _keeper_of(members[0])
            for c in members:
                conflicts.append({"id": c["id"], "from": c["from"],
                                  "to": _target_for(c, stem0[c["id"]],
                                                    target_root, group_by_region),
                                  "edition": c["edition"], "stack": c["stack"],
                                  "title": c["m"].get("title", ""),
                                  "tmdb_id": c["m"].get("tmdb_id"),
                                  "region_stale": _region_stale(
                                      c["from"], c["m"].get("region") or ""),
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
                                      list(c["labels"][:d]), c["stack"])
                    for c in members}
            if len(set(cand.values())) == len(members):
                for c in members:
                    specs = list(c["labels"][:d])
                    p = _finalize(c, cand[c["id"]], target_root,
                                  group_by_region,
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
                                     c["stack"]), []).append(c)
        for _, group in sorted(tied.items()):
            group = sorted(group, key=lambda x: x["id"])
            first = group[0]
            specs = list(first["labels"][:maxd]) if maxd else []
            p = _finalize(first, _stem_of(first["base"], first["edition"],
                                          specs, first["stack"]),
                          target_root, group_by_region,
                          spec_used="-".join(specs))
            if p:
                plans.append(p)
            for i, c in enumerate(group[1:], start=2):
                numbered = f"版本{i}"
                p = _finalize(c, _stem_of(c["base"], c["edition"], specs,
                                          c["stack"], numbered),
                              target_root, group_by_region, numbered=numbered,
                              spec_used="-".join(specs))
                if p:
                    plans.append(p)
    # 磁盘/库内占用检查：目标已被库外文件或另一条库行占用则冲突
    final_plans = []
    for p in plans:
        dst = os.path.normpath(p["to"])
        owner = db_owner.get(dst)
        if (os.path.exists(os.path.join(settings.media_root, p["to"]))
                and dst not in current_paths):
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
