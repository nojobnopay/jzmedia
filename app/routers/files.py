"""文件整理：默认dry_run预览，确认后执行。

命名模板：标题 (年份)[-版本][-规格][-分卷][-版本N].ext（后缀一律单 `-` 直连无空格）
- 就地整理（preview/rename）：保留原父目录，只做归档子目录+文件名规范化。
- 搬迁（relocate/organize）：搬到 电影/<region>/ 下二级目录，region 取库内派生值，
  未知/未匹配进 “未知”。单源 region 映射见 app/regions.py。
- 多版本同目录靠版本/规格后缀区分（中文优先，见 app/editions.py）；
  最小后缀原则：单版本零后缀，冲突组内才逐级追加；仍撞车用 -版本N 兜底（临时计算、
  永远末尾、执行时入库、可手工改备注）。
- 真分卷（-cd1/-part1 数字后缀）归一为 -partN；续集文字（Part Two）不算分卷。
- 花絮/样片不入库不整理；搬迁时同名前缀的花絮跟随正片。
"""
import errno
import os
import shutil

from fastapi import APIRouter, HTTPException

from .. import store
from ..config import settings
from ..editions import (core_of, detect_edition, detect_spec_list,
                        sanitize_tag, split_stack)
from ..log import get_logger
from ..regions import REGION_ORDER, REGION_UNKNOWN
from ..scanner import (SUBTITLE_EXTS, is_feature_video, is_sidecar,
                       sync_nfos_for)

router = APIRouter(prefix="/api/files")
logger = get_logger("files")

_ILLEGAL = ("\\", "/", ":", "*", "?", '"', "<", ">", "|")

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


def _safe_component(s: str) -> str:
    """文件名安全清洗。与 editions.sanitize_tag 的差异（评审 B8/R09-B3）：本函数不限长、
    不剥首尾点划线（用于路径段）；sanitize_tag 面向标签/后缀（限 20、剥边界）。"""
    s = str(s or "").strip()
    for ch in _ILLEGAL:
        s = s.replace(ch, "")
    return " ".join(s.split())


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


def _check_inside_root(rel: str) -> str:
    """归一并约束在 MEDIA_ROOT 内，返回归一相对路径；非法抛 422。
    除字符串边界外还做 realpath 校验（评审 B6/R09-B1）：根内符号链接指向外部时，
    仅 normpath 检查会放行，实际写入/读取会落到根外。"""
    norm = os.path.normpath((rel or "").strip().strip("/"))
    if not norm or norm == "." or norm.startswith("..") or os.path.isabs(rel or ""):
        raise HTTPException(422, f"illegal path: {rel!r}")
    try:
        root_real = os.path.realpath(settings.media_root)
        abs_real = os.path.realpath(os.path.join(settings.media_root, norm))
    except (OSError, ValueError):
        raise HTTPException(422, f"illegal path: {rel!r}")
    if abs_real != root_real and not abs_real.startswith(root_real + os.sep):
        raise HTTPException(422, f"path escapes media root: {rel!r}")
    return norm


_MAX_ONLY_IDS = 5000


def _only_ids(body: dict | None) -> set[int] | None:
    """body.ids → 选择集合（评审 B6/R09-B2）：强转 int；空=全量。
    此前 set(str) 会让字符串 id 静默不匹配，把“只操作选中项”放大成全量操作。"""
    raw = (body or {}).get("ids") or []
    if not raw:
        return None
    if len(raw) > _MAX_ONLY_IDS:
        raise HTTPException(422, f"too many ids (max {_MAX_ONLY_IDS})")
    try:
        return {int(x) for x in raw}
    except (TypeError, ValueError):
        raise HTTPException(422, "ids must be int list")


def _write_nfos(movie_id: int, dst_abs: str) -> None:
    """整理后 NFO 收敛：委托 scanner.sync_nfos_for（单版本只留 movie.nfo，
    同片多版本才补同名，共享目录只写当前同名）。失败自吞。"""
    try:
        sync_nfos_for(movie_id, dst_abs)
    except Exception:
        pass


def _sibling_followers(src_abs: str) -> list[str]:
    """同名前缀跟随文件：同目录下以正片 stem 开头、本身是花絮/样片/字幕的兄弟。"""
    src_dir = os.path.dirname(src_abs)
    stem = os.path.splitext(os.path.basename(src_abs))[0]
    out = []
    try:
        names = os.listdir(src_dir)
    except OSError:
        return out
    for n in names:
        full = os.path.join(src_dir, n)
        if full == src_abs or not os.path.isfile(full):
            continue
        st, ex = os.path.splitext(n)
        if st == stem or st.startswith(stem + "-") or st.startswith(stem + ".") \
                or st.startswith(stem + "_") or st.startswith(stem + " "):
            rel_probe = os.path.relpath(full, settings.media_root)
            if is_sidecar(rel_probe) or ex.lower() in SUBTITLE_EXTS:
                out.append(full)
    return out


def _cleanup_old_dir(old_dir_abs: str) -> None:
    """旧目录无正片残留时清掉 NFO 残留；空目录则删掉（共享大目录不会为空，无动作）。"""
    try:
        names = os.listdir(old_dir_abs)
    except OSError:
        return
    has_feature = False
    for n in names:
        full = os.path.join(old_dir_abs, n)
        if not os.path.isfile(full):
            continue
        rel = os.path.relpath(full, settings.media_root)
        if is_feature_video(rel):
            has_feature = True
            break
    if has_feature:
        return
    for n in names:
        if n == "movie.nfo" or n.endswith(".nfo"):
            try:
                os.remove(os.path.join(old_dir_abs, n))
            except OSError:
                pass
    try:
        if not os.listdir(old_dir_abs):
            os.rmdir(old_dir_abs)
    except OSError:
        pass


def move_attached_extras(movie_id: int, movie_dir_abs: str) -> int:
    """把已归属花絮搬进指定影片目录的 extras/ 子目录（茎名清洗保留），更新归属路径。
    供 _move_one 跟随与 /api/extras/collect 共用。返回搬迁数。"""
    moved = 0
    try:
        rows = store.list_extras_by_movie(movie_id)
    except Exception:
        return 0
    for e in rows:
        esrc = os.path.join(settings.media_root, e["file_path"])
        if not os.path.isfile(esrc):
            continue
        edst_dir = os.path.join(movie_dir_abs, "extras")
        base = _safe_component(os.path.splitext(os.path.basename(esrc))[0])
        if not base:
            continue
        edst = os.path.join(edst_dir, base + os.path.splitext(esrc)[1])
        try:
            if os.path.normpath(esrc) == os.path.normpath(edst):
                continue
            os.makedirs(edst_dir, exist_ok=True)
            if not os.path.exists(edst):
                os.rename(esrc, edst)
                store.upsert_extra(os.path.relpath(edst, settings.media_root),
                                   movie_id, e.get("kind") or "extra")
                try:
                    store.delete_extra_by_path(e["file_path"])
                except Exception:
                    pass
                moved += 1
        except OSError:
            continue
    return moved


def _resync_old_dir(old_dir_abs: str) -> None:
    """搬迁后旧目录重收敛：还有正片残留（如多版本搬走其一）时，以剩余行重调
    sync（多→单自动删多余同名 NFO）；空了则沿用 _cleanup_old_dir 清场。失败自吞。"""
    try:
        if not os.path.isdir(old_dir_abs):
            return
        try:
            old_rel = os.path.relpath(old_dir_abs, settings.media_root)
            if old_rel == ".":
                old_rel = ""
        except ValueError:
            return
        try:
            rows = store.list_movies_in_dir(old_rel)
        except Exception:
            return
        remaining = []
        for r in rows:
            try:
                fp = r.get("file_path", "")
                if not fp or not is_feature_video(fp):
                    continue
                if os.path.isfile(os.path.join(settings.media_root, fp)):
                    remaining.append(r)
            except Exception:
                continue
        if not remaining:
            return
        remaining.sort(key=lambda r: int(r.get("id", 0)))
        first = remaining[0]
        try:
            sync_nfos_for(int(first["id"]),
                          os.path.join(settings.media_root, first["file_path"]))
        except Exception:
            pass
    except Exception:
        pass


def _rename_or_move(src: str, dst: str) -> None:
    """同盘 rename；跨盘（EXDEV）退化为 shutil.move（评审 B8/R09-D3）。"""
    try:
        os.rename(src, dst)
    except OSError as e:
        if e.errno == errno.EXDEV:
            shutil.move(src, dst)
        else:
            raise


def _move_one(p: dict) -> dict:
    src = os.path.join(settings.media_root, p["from"])
    dst = os.path.join(settings.media_root, p["to"])
    if not os.path.exists(src):
        return {**p, "status": "skipped_missing_src"}
    if os.path.exists(dst):
        return {**p, "status": "conflict_disk_exists"}
    # 库内占用保护（评审 P1-06）：目标路径若挂在另一条库行上（哪怕文件缺失），
    # 先改库后挪盘会撞 UNIQUE(file_path) → 盘已动库未动。此处提前拒绝。
    existing = store.get_by_path(p["to"])
    if existing is not None and int(existing.get("id") or -1) != int(p["id"]):
        return {**p, "status": "conflict_db_occupied"}
    try:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        followers = _sibling_followers(src)
        _rename_or_move(src, dst)
        # 本地写：只改 file_path，不碰 TMDB 镜像列
        try:
            store.update_movie_local(p["id"], file_path=p["to"])
        except Exception:
            # 库写失败回滚移动，保证盘/库一致（正常路径已被上面的占用检查挡住）
            try:
                os.rename(dst, src)
            except OSError:
                logger.error("move rollback failed id=%s dst=%s src=%s", p["id"],
                             p["to"], p["from"])
            raise
        # 规划期识别的版本/编号后缀落库（DB 为空才写，手工值优先），防下次预览回环
        try:
            cur = store.get_movie(p["id"]) or {}
        except Exception:
            cur = {}
        persist: dict = {}
        if p.get("edition") and not cur.get("edition"):
            persist["edition"] = p["edition"]
        if not cur.get("spec"):
            if p.get("numbered"):
                # 存完整渲染串（含前面的规格段），保证下次预览收敛
                full = ((p.get("spec") or "") + "-" if p.get("spec") else "") + p["numbered"]
                persist["spec"] = full
            elif p.get("spec"):
                persist["spec"] = p["spec"]
        if persist:
            try:
                store.update_movie_local(p["id"], **persist)
            except Exception:
                pass
        _write_nfos(p["id"], dst)
        # 花絮/字幕跟随：新 stem + 原后缀
        new_stem = os.path.splitext(os.path.basename(dst))[0]
        old_stem = os.path.splitext(os.path.basename(src))[0]
        followed = 0
        for f in followers:
            suffix = os.path.basename(f)[len(old_stem):]
            fdst = os.path.join(os.path.dirname(dst), new_stem + suffix)
            try:
                if not os.path.exists(fdst):
                    _rename_or_move(f, fdst)
                    followed += 1
            except OSError:
                continue
        _cleanup_old_dir(os.path.dirname(src))
        # 归属花絮跟随：搬进目标 extras/ 子目录（Plex 子目录名 collapsing，茎名清洗保留）
        extras_moved = move_attached_extras(p["id"], os.path.dirname(dst))
        # 旧目录收尾（花絮搬走后再清一次）：无正片则清 NFO，空目录删掉
        _cleanup_old_dir(os.path.dirname(src))
        # 跨目录搬迁：旧目录还有正片残留则重收敛（多→单删多余同名 NFO）
        if os.path.normpath(os.path.dirname(src)) != os.path.normpath(os.path.dirname(dst)):
            _resync_old_dir(os.path.dirname(src))
        # 同目录改名：删掉旧 stem 的 per-file NFO 残留（movie.nfo 已重写）
        if os.path.dirname(src) == os.path.dirname(dst):
            old_nfo = os.path.join(os.path.dirname(dst), old_stem + ".nfo")
            new_nfo = os.path.join(os.path.dirname(dst), new_stem + ".nfo")
            if old_nfo != new_nfo:
                try:
                    if os.path.exists(old_nfo):
                        os.remove(old_nfo)
                except OSError:
                    pass
        return {**p, "status": "moved", "followed": followed,
                "extras_moved": extras_moved}
    except Exception as e:
        logger.warning("move failed id=%s %s -> %s: %s", p.get("id"),
                       p.get("from"), p.get("to"), e)
        return {**p, "status": f"error: {e}"}


def _organize(mode: str, from_prefix: str | None = None,
              to_dir: str | None = None, group_by_region: bool = True,
              only: set | None = None, dry_run: bool = True) -> dict:
    """统一整理入口：inplace=就地归档（target_root=None），relocate=顶层搬迁。"""
    if mode not in ("inplace", "relocate"):
        raise HTTPException(422, "mode must be inplace|relocate")
    if mode == "inplace":
        plans, conflicts = _collect_plans(only=only)
        base: dict = {"dry_run": dry_run, "mode": mode}
    else:
        fp = _check_inside_root(str(from_prefix or "待整理"))
        td = _check_inside_root(str(to_dir or "电影"))
        if os.path.normpath(fp) == os.path.normpath(td):
            raise HTTPException(422, "from_prefix == to_dir, nothing to do")
        rows = store.list_movies(grouped=False, limit=100000)
        if only:
            rows = [m for m in rows if m["id"] in only]
        else:
            # 全量收敛：from_prefix 子树 + 已在目标根下但尚未规范的行
            #（分区过期如改绑换产地、两级分区前的平铺残留）；目标已规范的行无计划
            def _under(m, root: str) -> bool:
                return (os.path.normpath(m["file_path"]) == root
                        or m["file_path"].startswith(root.rstrip("/") + "/"))
            rows = [m for m in rows if _under(m, fp) or _under(m, td)]
        scoped = {m["id"] for m in rows}
        plans, conflicts = _collect_plans(target_root=td,
                                          group_by_region=bool(group_by_region),
                                          only=scoped or {-1})
        base = {"dry_run": dry_run, "mode": mode, "from_prefix": fp,
                "to_dir": td, "group_by_region": bool(group_by_region)}
    if dry_run:
        return {**base, "plans": plans, "conflicts": conflicts}
    return {**base, "results": [_move_one(p) for p in plans],
            "conflicts": conflicts}


@router.post("/organize")
def organize(body: dict | None = None):
    """统一整理口：mode=inplace 就地归档；mode=relocate 顶层搬迁
    （from_prefix→to_dir[/region]/标题 (年份)/文件）。dry_run 默认 true 只预览。"""
    body = body or {}
    return _organize(mode=str(body.get("mode") or "inplace"),
                     from_prefix=body.get("from_prefix"),
                     to_dir=body.get("to_dir"),
                     group_by_region=(True if body.get("group_by_region") is None
                                      else bool(body.get("group_by_region"))),
                     only=_only_ids(body),
                     dry_run=body.get("dry_run", True))


@router.get("/preview")
def preview():
    return _organize("inplace", dry_run=True)


@router.get("/unmatched")
def unmatched():
    """待处理明细（只读，供设置页展示+跳转处理）：
    unmatched=TMDB 无命中；needs_review=模糊命中待确认；
    suspect_title=标题无 CJK（high=非英语片，疑似错配/缺译；info=英语片，多为正常/陈旧）；
    orphan_extras=未归属花絮（文件原地保留）。"""
    import re as _re
    un, nr, high, info, orphans = [], [], [], [], []
    for m in store.list_movies(grouped=False, limit=100000):
        item = {"id": m["id"], "title": m.get("title", ""),
                "original_title": m.get("original_title", ""),
                "year": m.get("year"), "file_path": m["file_path"],
                "tmdb_id": m.get("tmdb_id"),
                "original_language": m.get("original_language", "")}
        if not m.get("tmdb_id"):
            un.append(item)
        else:
            if m.get("needs_review"):
                item["needs_review"] = 1
                nr.append(item)
            title = m.get("title") or ""
            if (title and _re.search(r"[A-Za-z]", title)
                    and not _re.search(r"[\u4e00-\u9fff]", title)):
                (high if (m.get("original_language") or "")
                 not in ("en", "") else info).append(item)
    un.sort(key=lambda x: x["file_path"])
    for e in store.list_orphan_extras():
        orphans.append({"id": e["id"], "file_path": e["file_path"],
                        "kind": e.get("kind") or "extra"})
    return {"unmatched": un, "needs_review": nr,
            "suspect_title_high": high, "suspect_title_info": info,
            "orphan_extras": orphans,
            "total_unmatched": len(un), "total_needs_review": len(nr),
            "total_suspect_high": len(high), "total_suspect_info": len(info),
            "total_orphan_extras": len(orphans)}


@router.post("/clean-sidecars")
def clean_sidecars(body: dict | None = None):
    """清理历史脏行：file_path 命中现行花絮/样片规则的 movies 行，删行+关联+FTS。
    视频文件原地保留（下次扫描按花絮归属），海报与 tmdb_cache 保留。默认 dry_run 预览。"""
    from ..scanner import is_sidecar
    body = body or {}
    dry_run = body.get("dry_run", True)
    only = _only_ids(body)
    # 已匹配行不清理（评审 P1-05）：花絮规则演进可能把真实影片判成花絮/样片，
    # 有 tmdb_id 的行必是扫描/人工确认过的电影，宁可漏清也不能误删。
    cands = [m for m in store.list_movies(grouped=False, limit=100000)
             if (only is None or m["id"] in only) and not m.get("tmdb_id")
             and is_sidecar(m["file_path"])]
    plans = [{"id": m["id"], "title": m.get("title", ""), "year": m.get("year"),
              "file_path": m["file_path"], "tmdb_id": m.get("tmdb_id")}
             for m in cands]
    if dry_run:
        return {"dry_run": True, "total": len(plans), "plans": plans}
    done, failed = [], []
    for m in cands:
        try:
            ok = store.delete_movie(m["id"])
            done.append({"id": m["id"], "file_path": m["file_path"],
                         "status": "deleted" if ok else "already_gone"})
        except Exception as e:
            failed.append({"id": m["id"], "file_path": m["file_path"],
                           "error": str(e)})
    return {"dry_run": False, "total": len(cands), "deleted": len(done),
            "failed": failed, "results": done}


@router.post("/clean-episodes")
def clean_episodes(body: dict | None = None):
    """清理历史剧集脏行（评审 P1-03 配套）：未匹配（tmdb_id 为空）且文件名解析为剧集的
    movies 行，删行+关联+FTS；视频文件原地保留，海报与 tmdb_cache 保留。
    已匹配行不清理（可能是人工改判为电影）。默认 dry_run 预览。"""
    from ..scanner import parse_filename
    body = body or {}
    dry_run = body.get("dry_run", True)
    only = _only_ids(body)
    cands = []
    for m in store.list_movies(grouped=False, limit=100000):
        if only is not None and m["id"] not in only:
            continue
        if m.get("tmdb_id"):
            continue
        try:
            parsed = parse_filename(os.path.basename(m["file_path"]))
        except Exception:
            continue
        if parsed.get("type") == "episode":
            cands.append(m)
    plans = [{"id": m["id"], "title": m.get("title", ""), "year": m.get("year"),
              "file_path": m["file_path"]} for m in cands]
    if dry_run:
        return {"dry_run": True, "total": len(plans), "plans": plans}
    done, failed = [], []
    for m in cands:
        try:
            ok = store.delete_movie(m["id"])
            done.append({"id": m["id"], "file_path": m["file_path"],
                         "status": "deleted" if ok else "already_gone"})
        except Exception as e:
            failed.append({"id": m["id"], "file_path": m["file_path"],
                           "error": str(e)})
    return {"dry_run": False, "total": len(cands), "deleted": len(done),
            "failed": failed, "results": done}


@router.get("/missing")
def missing():
    """预览失效条目：库中有记录但文件已不存在的行（软件外删片/移动后产生）。"""
    out = []
    for m in store.list_movies(grouped=False, limit=100000):
        if not os.path.exists(os.path.join(settings.media_root, m["file_path"])):
            out.append({"id": m["id"], "title": m.get("title", ""),
                        "year": m.get("year"), "file_path": m["file_path"],
                        "tmdb_id": m.get("tmdb_id")})
    return {"total": len(out), "items": out}


@router.post("/clean")
def clean(body: dict | None = None):
    """清理失效条目：彻底删除DB行+演职员关联+FTS（海报与tmdb_cache保留供重扫复用）。
    默认 dry_run 预览（评审 B5a-4/R09-D4：与 clean-sidecars 等保持一致）；
    body.ids 不传则清理全部缺失行；建议先 GET /missing 预览勾选。"""
    body = body or {}
    dry_run = body.get("dry_run", True)
    only_set = _only_ids(body)
    cands = [m for m in store.list_movies(grouped=False, limit=100000)
             if (only_set is None or m["id"] in only_set)
             and not os.path.exists(os.path.join(settings.media_root, m["file_path"]))]
    plans = [{"id": m["id"], "title": m.get("title", ""), "year": m.get("year"),
              "file_path": m["file_path"],
              "tmdb_id": m.get("tmdb_id")} for m in cands]
    if dry_run:
        return {"dry_run": True, "total": len(plans), "plans": plans}
    done, failed = [], []
    for m in cands:
        try:
            ok = store.delete_movie(m["id"])
            done.append({"id": m["id"], "file_path": m["file_path"],
                         "status": "deleted" if ok else "already_gone"})
        except Exception as e:
            failed.append({"id": m["id"], "file_path": m["file_path"],
                           "error": str(e)})
    return {"dry_run": False, "total": len(cands), "deleted": len(done),
            "failed": failed, "results": done}


def _restore_candidates(only: set | None) -> list[dict]:
    """偏离原始位置的行：有原始路径、与当前位置不一致、当前文件仍存在。"""
    out = []
    for m in store.list_movies(grouped=False, limit=100000):
        if only is not None and m.get("id") not in only:
            continue
        orig = (m.get("original_file_path") or "").strip()
        cur = m.get("file_path", "")
        if not orig or os.path.normpath(orig) == os.path.normpath(cur):
            continue
        if not os.path.isfile(os.path.join(settings.media_root, cur)):
            continue
        out.append(m)
    out.sort(key=lambda m: (m.get("file_path", ""), m.get("id", 0)))
    return out


def _restore_one(m: dict, dry_run: bool) -> dict:
    """单行恢复到原始路径。dry_run 只规划；执行时复用 NFO 收敛+旧目录清理。"""
    base = {"id": m["id"], "title": m.get("title", ""),
            "from": m["file_path"], "to": m.get("original_file_path") or ""}
    # 目标边界守卫（评审 B6/R09-B4）：to 来自库内历史值，理论上合法；
    # 防御性再校验一次，防止库被外部工具改坏后恢复到 MEDIA_ROOT 之外
    try:
        base["to"] = _check_inside_root(base["to"])
    except HTTPException as e:
        return {**base, "status": f"error: illegal target ({e.detail})"}
    src = os.path.join(settings.media_root, base["from"])
    dst = os.path.join(settings.media_root, base["to"])
    if not os.path.isfile(src):
        return {**base, "status": "skipped_missing_src"}
    if os.path.exists(dst):
        return {**base, "status": "conflict_disk_exists"}
    # 库内占用保护（评审 P1-06）：同 _move_one，目标挂在别的库行上时先拒绝
    existing = store.get_by_path(base["to"]) if base["to"] else None
    if existing is not None and int(existing.get("id") or -1) != int(m["id"]):
        return {**base, "status": "conflict_db_occupied"}
    if dry_run:
        return {**base, "status": "planned"}
    try:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        old_dir = os.path.dirname(src)
        _rename_or_move(src, dst)
        # 本地写：只改 file_path，不碰 TMDB 镜像列与 original_file_path
        try:
            store.update_movie_local(m["id"], file_path=base["to"])
        except Exception:
            try:
                _rename_or_move(dst, src)
            except OSError:
                pass
            raise
        try:
            sync_nfos_for(m["id"], dst)
        except Exception:
            pass
        _cleanup_old_dir(old_dir)
        if os.path.normpath(old_dir) != os.path.normpath(os.path.dirname(dst)):
            _resync_old_dir(old_dir)
        return {**base, "status": "restored"}
    except Exception as e:
        logger.warning("restore failed id=%s %s -> %s: %s", m.get("id"),
                       base.get("from"), base.get("to"), e)
        return {**base, "status": f"error: {e}"}


@router.get("/restore-candidates")
def restore_candidates():
    """预览偏离原始位置的行（只读，供设置页恢复区展示）。"""
    cands = _restore_candidates(None)
    return {"total": len(cands),
            "items": [{"id": m["id"], "title": m.get("title", ""),
                       "year": m.get("year"),
                       "file_path": m["file_path"],
                       "original_file_path": m.get("original_file_path") or ""}
                      for m in cands]}


@router.post("/restore-original")
def restore_original(body: dict | None = None):
    """恢复到原始位置：把整理/搬迁后偏离原始路径的影片搬回 original_file_path。
    默认 dry_run:true 只预览；确认后 dry_run:false 执行。目标被占/源缺失则跳过上报，绝不覆盖。"""
    body = body or {}
    only = _only_ids(body)
    dry_run = body.get("dry_run", True)
    if dry_run:
        plans = [{"id": m["id"], "title": m.get("title", ""), "year": m.get("year"),
                  "from": m["file_path"], "to": m.get("original_file_path") or "",
                  "status": _restore_one(m, dry_run=True)["status"]}
                 for m in _restore_candidates(only)]
        return {"dry_run": True, "total": len(plans), "plans": plans}
    results = [_restore_one(m, dry_run=False) for m in _restore_candidates(only)]
    ok = sum(1 for r in results if r["status"] == "restored")
    return {"dry_run": False, "total": len(results),
            "restored": ok, "results": results}
