"""TV 目录规范化（T4.2 v2）：剧根改名 / 季目录规范化 / 包装层拍平 / 补 Season /
特典归位 / 花絮目录上移 / 正片统一命名 —— 目录只移动，正片文件按 Plex 模板改名。

- `plan_tv_organize()` 纯计算（DB + 目录快照），输出按剧的动作清单/冲突/警告/需手动；
- `execute_tv_organize()` 经 StorageBackend 执行；移动与改名合并为单次 rename；
  同步 DB 路径（`store.move_tv_paths` / 前缀重写），清空目录，执行后补写季海报；
  每次执行逐条写审计（`organize_moves`，v24），可用 `plan_restore/execute_restore` 撤销；
- 做种保护：剧根存在 `.torrent` 的剧默认跳过（移动/改名会破坏做种），
  `allow_torrent=True` 显式放行；只读库跳过。

动作：
  root    ：剧根目录 → `中文标题 (年份)`（Plex 规范；匹配剧才改，最后执行）
  seasondir：`season 1`/`S04`/`第3季` → `Season NN`；`Specials/特典` → `Season 00`
  wrapper ：`Show/<release>/Season NN/…` → `Show/Season NN/…`（整目录 rename）
  season  ：剧根散集 → `Season NN/`（同茎字幕/NFO 跟随）
  specials：season==0 的特典 → `Season 00/`（同茎字幕/NFO 跟随；needs_review 只报告）
  extras  ：类型目录（Deleted Scenes/…）整目录上移到剧根（仅深度 ≤2）；`Behind The
            Scene` → `Behind The Scenes`；更深层/散文件只报告（需手动整理）
  rename  ：正片 → `剧名-S01E01[-E02]-集名.ext`（同集多版本加 `-V2`；S00 特典同名式；
            同茎字幕/NFO 跟随；整季单文件（S01E01-E13）按同一模板正常改名；
            needs_review/未匹配/绝对集号风险（且确有正片需改名）→ 只报告；
            local_only 本地集 → `kept`（保持原名，不计需手动））
"""
import os
import re
import time
import uuid

from .. import store
from ..log import get_logger
from .classify import EXTRAS_DIR_NAMES, _dir_tokens
from .tv_nfo_link import show_dir_of
from .tv_parse import is_tv_special_dir, season_from_dir

logger = get_logger("scanner.tv_organize")
__all__ = ['plan_tv_organize', 'execute_tv_organize', 'summarize_plan', 'plan_restore',
           'execute_restore', 'TV_ORGANIZE_ACTIONS']

TV_ORGANIZE_ACTIONS = ("root", "seasondir", "wrapper", "season", "specials", "extras",
                       "rename")

_SINGULAR_FIX = {"behind the scene": "Behind The Scenes"}
_MOVE_SIBLING_EXTS = (".srt", ".ass", ".ssa", ".sup", ".nfo", ".sub", ".idx")
_ILLEGAL_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')   # `/` 必须清洗，否则会建出目录
_MAX_TITLE_LEN = 80
_ABS_RISK_RATIO = 0.5      # 绝对集号映射占比 ≥50% → 该剧默认不勾选改名
_MANUAL_CAP = 30

_SUGGEST = {
    "absolute": "该剧依赖绝对集号映射，Plex 的季/集拆分可能不同；建议先在 Plex 核对，"
                "确认无误后再勾选该剧执行",
    "unmatched": "先到剧详情页匹配 TMDB，再重新预览",
    "needs_review": "先到剧详情页绑定该集的 TMDB 集号，再重新预览",
    "bad_episode": "该行集号缺失或非法，无法生成规范名；请手动确认文件与 TMDB 集号",
    "no_title": "缺少剧名（可能未匹配 TMDB），先匹配后再改名",
    "exists": "目标名已存在（可能已有同名文件），请手动处理",
}

_LABELS = {"root": "剧根改名", "seasondir": "季目录规范化", "wrapper": "包装层拍平",
           "season": "补 Season 目录", "specials": "特典归位", "extras": "花絮目录上移",
           "rename": "正片统一命名"}


class _DirCache:
    """目录快照（一次 list，随写失效），减少 SMB 往返。"""

    def __init__(self, backend):
        self.backend = backend
        self._cache: dict = {}

    def entries(self, rel: str) -> dict:
        rel = self.backend.norm(rel or "")
        if rel not in self._cache:
            try:
                self._cache[rel] = {str(e["name"]): bool(e.get("is_dir"))
                                    for e in self.backend.list(rel)}
            except Exception as e:
                logger.debug("tv organize list failed dir=%s: %s", rel, e)
                self._cache[rel] = {}
        return self._cache[rel]

    def add(self, rel: str, name: str, is_dir: bool = False) -> None:
        rel = self.backend.norm(rel or "")
        if rel in self._cache:
            self._cache[rel][str(name)] = bool(is_dir)

    def discard(self, rel: str, name: str) -> None:
        rel = self.backend.norm(rel or "")
        if rel in self._cache:
            self._cache[rel].pop(str(name), None)

    def clear(self) -> None:
        self._cache.clear()


def _join(d: str, name: str) -> str:
    return f"{d}/{name}" if d else name


def _ancestors_with_self(rel: str) -> list[str]:
    """路径的祖先目录（由外到内，最后一项是自身目录）。"""
    parts = [p for p in str(rel or "").replace("\\", "/").split("/") if p]
    return ["/".join(parts[:i]) for i in range(1, len(parts))]


def _project(path: str, maps: list[dict]) -> str:
    """按执行顺序链式套用目录映射（wrapper → 季目录 → …）。"""
    for r in maps or []:
        if path == r["from"] or path.startswith(r["from"] + "/"):
            path = r["to"] + path[len(r["from"]):]
    return path


def _lower(snap: dict) -> set:
    return {str(k).casefold() for k in (snap or {})}


def _same_stem_as(fstem: str, stem: str) -> bool:
    """同茎判定：等名 / `stem-…` / `stem.语言`（`E01.chs.srt`）。"""
    return fstem == stem or fstem.startswith(stem + "-") or fstem.startswith(stem + ".")


def _is_extras_dir(name: str) -> bool:
    return any(t in EXTRAS_DIR_NAMES for t in _dir_tokens(name))


def _torrent_present(cache: _DirCache, show_dir: str) -> bool:
    return any(str(n).lower().endswith(".torrent")
               for n in cache.entries(show_dir))


def _lca_dir(paths) -> str:
    """所有文件路径的最深公共目录。"""
    dirs = [os.path.dirname(str(p or "")) for p in paths]
    if not dirs:
        return ""
    common = dirs[0].split("/")
    for d in dirs[1:]:
        parts = d.split("/")
        i = 0
        while i < len(common) and i < len(parts) and common[i] == parts[i]:
            i += 1
        common = common[:i]
    return "/".join(p for p in common if p)


def _has_other_shows(prefix: str, library_id, show_id) -> bool:
    """前缀目录下是否有**其它剧**的集/花絮（有则不能当作本剧的包装层往上跑）。"""
    if not prefix or not show_id:
        return False
    try:
        return store.has_other_show_under_prefix(prefix, library_id, show_id)
    except Exception as e:
        logger.debug("has_other_show_under_prefix failed %s: %s", prefix, e)
        return False


def _resolve_show_dir(episodes: list[dict], show_id=None, library_id=None) -> str:
    """剧根解析：公共目录去掉尾部季/特典目录后，向上跳包装层——
    但**父目录含其它剧时不再上溯**（防 `七龙珠/龙珠 (1986)/Season 01` 被当成
    `七龙珠/Season 01` 或把 `七龙珠` 当剧根）。"""
    paths = [str(e.get("file_path") or "") for e in episodes]
    base = _lca_dir(paths)
    if not base:
        dirs = {os.path.dirname(p) for p in paths}
        direct = {d for d in dirs if not is_tv_special_dir(os.path.basename(d))}
        return show_dir_of(paths[0], direct or dirs)
    while True:                       # 去掉尾部季/特典目录（如 Season 01 / Specials）
        p = os.path.dirname(base)
        if not p:
            break
        if (season_from_dir(os.path.basename(base)) is not None
                or is_tv_special_dir(os.path.basename(base))):
            base = p
            continue
        break
    cur = base
    while True:                       # 包装层上溯（父目录只属于本剧）
        if cur.count("/") == 0:
            break                     # 已是库内顶层目录
        parent = os.path.dirname(cur)
        if not parent:
            break
        if _has_other_shows(parent, library_id, show_id):
            break
        cur = parent
    return cur


def _kind_dir_of(fp: str, show_dir: str) -> str | None:
    for a in reversed(_ancestors_with_self(fp)):
        if not a.startswith(show_dir + "/"):
            continue
        if _is_extras_dir(os.path.basename(a)):
            return a
    return None


def _extras_placement(kind_dir: str, show_dir: str) -> str:
    """canonical（Plex 可见）/ promote（深度 2 上移剧根）/ deep（更深，需手动）。"""
    if kind_dir == show_dir or os.path.dirname(kind_dir) == show_dir:
        return "canonical"
    parent = os.path.dirname(kind_dir)
    if os.path.dirname(parent) == show_dir:
        if season_from_dir(os.path.basename(parent)) is not None:
            return "canonical"       # 季级花絮 Season NN/Deleted Scenes
        return "promote"
    return "deep"


def _clean_name(text, limit: int = _MAX_TITLE_LEN) -> str:
    s = _ILLEGAL_RE.sub(" ", str(text or ""))
    s = re.sub(r"\s+", " ", s).strip(" .")
    s = re.sub(r"\s*-\s*", "-", s)      # 分隔符统一 `-`，不留多余空格
    if limit and len(s) > limit:
        s = s[:limit].rstrip(" .")
    return s


def _season_dir_name(n: int) -> str:
    return f"Season {int(n):02d}"


def _is_canonical_specials_dir(d: str, show_dir: str) -> bool:
    if os.path.dirname(d) != show_dir:
        return False
    return os.path.basename(d) in ("Season 00", "Specials", "特别篇", "特典", "番外")


def _episode_base_title(show_title: str, ep: dict, ver: int = 0) -> str | None:
    """`剧名[-V2]-S01E01[-E02]`；集号非法返回 None。

    多版本从第 2 版起用**版本前缀**（`剧名-V2-S01E01-…`）：文件列表里 V1 一组、
    V2 一组（`S` < `V`），Plex 仍按 `SxxEyy` 识别为同集多版本。"""
    try:
        season = int(ep.get("season") or 0)
        start = int(ep.get("episode") or 0)
        end = int(ep.get("episode_end") or 0)
    except (TypeError, ValueError):
        return None
    if start <= 0:
        return None
    title = _clean_name(show_title, 120)
    if not title:
        return None
    tag = f"-V{int(ver)}" if int(ver or 0) > 1 else ""
    head = f"{title}{tag}-S{season:02d}E{start:02d}"
    if end and end > start:
        head += f"-E{end:02d}"
    return head


def _version_num(stem: str) -> int:
    """版本号（与 store.episode_version 同源）；无标记=1。"""
    return store.episode_version(stem)


def _absolute_risk(episodes: list[dict]) -> bool:
    total = len(episodes or [])
    if not total:
        return False
    abs_n = sum(1 for e in episodes if e.get("absolute_number") is not None)
    return abs_n / total >= _ABS_RISK_RATIO


def _target_stem(show_title: str, ep: dict, ver: int = 0) -> str | None:
    """规范名茎（不含扩展名）`剧名[-V2]-S01E01[-E02]-集名`；集号非法返回 None。"""
    head = _episode_base_title(show_title, ep, ver=ver)
    if not head:
        return None
    ep_title = _clean_name(ep.get("title"))
    return f"{head}-{ep_title}" if ep_title else head


def _strip_part_suffix(stem: str) -> str:
    pm = re.search(r"-part\d+$", stem, re.I)
    return stem[:pm.start()] if pm else stem


def _abs_rename_pending(episodes: list[dict], show: dict, ver_of: dict) -> bool:
    """绝对集号风险仅在**确有正片需要改名**时提示/拦截：已全部规范名（既往已整理）
    的剧不再打扰；local_only/待确认行本来就不改名，不参与判定。"""
    for e in episodes:
        if (e.get("local_only") or int(e.get("needs_review") or 0)
                or not e.get("tmdb_episode_id")):
            continue
        stem = os.path.splitext(os.path.basename(str(e.get("file_path") or "")))[0]
        target = _target_stem(show.get("title") or "", e,
                              ver=ver_of.get(int(e.get("id")), 0))
        if target and _strip_part_suffix(stem).casefold() != target.casefold():
            return True
    return False


def _root_target(show: dict, profile: str) -> str | None:
    if str(profile or "").lower() == "off":
        return None
    if not show.get("tmdb_id"):
        return None
    title = _clean_name(show.get("title"), 120)
    if not title:
        return None
    try:
        year = int(show.get("year") or 0)
    except (TypeError, ValueError):
        year = 0
    return f"{title} ({year})" if year else title


def _plan_bound_roots(show, episodes, plan, actions, cache, allow_torrent, profile):
    """Consolidate separately confirmed flat season directories via audited renames.

    Complex/mixed roots remain explicitly manual. Never pick the first physical
    root as the destination for an entire multi-root show.
    """
    from .tv_nfo_link import show_dirs_for
    from ..tv_binding_rules import contains
    if not store.list_tv_bindings(show_id=show['id']):
        return None
    roots = show_dirs_for(show['id'], episodes)
    if len(roots) <= 1:
        return None
    if (len({os.path.dirname(r) for r in roots}) == 1
            and all(season_from_dir(os.path.basename(r)) is not None for r in roots)):
        return None
    target = _root_target(show, profile)
    plan['show_dir'] = target or ''
    for e in episodes:
        directory = os.path.dirname(e['file_path'])
        plan['dir_totals'][directory] = plan['dir_totals'].get(directory, 0) + 1
    plan['warnings'].append('本剧分布在多个目录；先归并季目录，完成后可再次预览统一文件名。')
    rules = {r['path']: r for r in store.list_tv_bindings(show_id=show['id'])}
    if not target or not {'root', 'season'}.issubset(actions):
        plan['manual'].append({'file': '', 'reason': 'multi_root',
            'suggestion': '归并多目录需要启用“剧根改名”和“补 Season 目录”，并先确认各目录季号'})
        return plan
    if _has_other_shows(target, show.get('library_id'), show['id']):
        plan['conflicts'].append({'from': '', 'to': target, 'reason': '目标目录属于其他剧集'})
        return plan
    destinations = set()
    cleanup_parents = set()
    for root in roots:
        rule = rules.get(root)
        local = [e for e in episodes if contains(root, e['file_path'])]
        sn = rule.get('season') if rule else None
        if (sn is None or not local or any(e['season'] != sn or os.path.dirname(e['file_path']) != root
                                           or e.get('binding_conflict') for e in local)
                or _has_other_shows(root, show.get('library_id'), show['id'])):
            plan['manual'].append({'file': root, 'reason': 'multi_root',
                'suggestion': '该目录含嵌套季、特典或其他归属，请先核对目录范围；暂不整体搬迁'})
            continue
        dst = _join(target, _season_dir_name(sn))
        if root == dst:
            continue
        if _torrent_present(cache, root) and not allow_torrent:
            plan['blocked'] = True
            plan['warnings'].append(f'{root} 存在 .torrent，默认跳过整理')
        if dst in destinations or cache.backend.exists(dst):
            plan['conflicts'].append({'from': root, 'to': dst, 'reason': '目标季目录已存在，不覆盖或合并'})
            continue
        # Avoid nesting a directory inside itself and moving a target containing
        # source material. Only sibling independent roots can be consolidated.
        if contains(root, target):
            plan['conflicts'].append({'from': root, 'to': dst, 'reason': '目标位于源目录内部'})
            continue
        destinations.add(dst)
        plan['dir_moves'].append({'action': 'season', 'kind': 'episode', 'from': root, 'to': dst})
        parent = os.path.dirname(root)
        if parent and parent != target:
            cleanup_parents.add(parent)
    plan['rmdirs'].extend(sorted(cleanup_parents, key=lambda x: (-len(x), x)))
    plan['rmdirs'] = sorted(set(plan['rmdirs']), key=lambda x: (-len(x), x))
    if not plan['blocked']:
        totals = {}
        for directory, count in plan['dir_totals'].items():
            final = _project(directory, plan['dir_moves'])
            totals[final] = totals.get(final, 0) + count
        plan['dir_totals'] = totals
    return plan


def _plan_show(show: dict, actions, cache: _DirCache, allow_torrent: bool,
               profile: str = "kodi", claimed: dict | None = None,
               allow_absolute: bool = False) -> dict | None:
    show_id = int(show["id"])
    episodes = store.list_episodes(show_id)
    if not episodes:
        return None
    direct = {os.path.dirname(str(e["file_path"])) for e in episodes}
    show_dir = _resolve_show_dir(episodes, show_id=show_id,
                                 library_id=show.get("library_id"))
    plan = {"show_id": show_id, "title": show.get("title") or "",
            "library_id": show.get("library_id"), "show_dir": show_dir,
            "dir_moves": [], "file_moves": [], "dir_renames": [],
            "rmdirs": [], "untouched": [], "manual": [], "manual_more": 0,
            "kept": [], "rename_count": 0, "absolute_risk": False, "root_move": None,
            "conflicts": [], "warnings": [], "blocked": False, "dir_totals": {}}
    multi = _plan_bound_roots(show, episodes, plan, actions, cache, allow_torrent, profile)
    if multi is not None:
        return multi
    if _torrent_present(cache, show_dir) and not allow_torrent:
        plan["blocked"] = True
        plan["warnings"].append("存在 .torrent（移动/改名会破坏做种，执行时默认跳过）")
    show_snap = cache.entries(show_dir)
    planned: set[str] = set()
    proj: list[dict] = []            # 已计划目录移动（链式投影用）

    def _manual(path: str, reason: str) -> None:
        if len(plan["manual"]) < _MANUAL_CAP:
            plan["manual"].append({"file": path, "reason": reason,
                                   "suggestion": _SUGGEST.get(reason, "")})
        else:
            plan["manual_more"] += 1

    # ---- 1) 花絮单数目录改名（Behind The Scene → Behind The Scenes） ----
    extras_rows = [x for x in store.list_extras_by_show(show_id)
                   if str(x.get("kind") or "") != "movie"]
    if "extras" in actions:
        for x in extras_rows:
            fp = str(x.get("file_path") or "")
            for a in reversed(_ancestors_with_self(fp)):
                if not a.startswith(show_dir + "/"):
                    continue
                for tok in _dir_tokens(os.path.basename(a)):
                    want = _SINGULAR_FIX.get(tok)
                    if not want or any(r["from"] == a for r in plan["dir_renames"]):
                        continue
                    parent = os.path.dirname(a)
                    snap_parent = cache.entries(parent)
                    if snap_parent.get(os.path.basename(a)) is not True:
                        continue
                    if want in snap_parent:
                        continue
                    plan["dir_renames"].append({"action": "extras", "kind": "dir",
                                                "from": a, "to": _join(parent, want)})

    # ---- 2) 包装层拍平：季目录比剧根深（Show/<release>/Season NN）→ 整目录上移 ----
    if "wrapper" in actions:
        for d in sorted(direct):
            if d == show_dir or not d.startswith(show_dir + "/"):
                continue
            target = _join(show_dir, os.path.basename(d))
            if target == d:
                continue          # 已在剧根（常规 Season 目录）
            if target in planned or str(os.path.basename(d)).casefold() in _lower(show_snap):
                plan["conflicts"].append({"action": "wrapper", "from": d, "to": target,
                                          "reason": "目标已存在"})
                continue
            planned.add(target)
            plan["dir_moves"].append({"action": "wrapper", "kind": "episode",
                                      "from": d, "to": target})
            proj.append({"from": d, "to": target})
        wrappers = set()
        for d in direct:
            for a in _ancestors_with_self(d):
                if a != show_dir and a.startswith(show_dir + "/") and a not in direct:
                    wrappers.add(a)
        plan["rmdirs"].extend(sorted(wrappers, key=lambda x: (-len(x), x)))

    # ---- 3) 季目录规范化：season 1 / S04 / 第3季 → Season NN；Specials → Season 00 ----
    if "seasondir" in actions:
        for d in sorted(direct):
            d2 = _project(d, proj)
            if os.path.dirname(d2) != show_dir:
                continue
            base = os.path.basename(d2)
            n = season_from_dir(base)
            if n is None:
                continue
            if int(n) == 0 and base in ("Season 00", "Specials", "特别篇", "特典", "番外"):
                continue
            want = _season_dir_name(n)
            if base == want:
                continue
            if not any(p == d2 or p.startswith(d2 + "/") for p in direct):
                continue
            target = _join(show_dir, want)
            if target in planned or want.casefold() in _lower(show_snap):
                plan["conflicts"].append({"action": "seasondir", "from": d2, "to": target,
                                          "reason": "目标 Season 目录已存在"})
                continue
            planned.add(target)
            plan["dir_moves"].append({"action": "seasondir", "kind": "episode",
                                      "from": d2, "to": target})
            proj.append({"from": d2, "to": target})

    # ---- 4+5) 散集补 Season / S00 特典归位（先算终态目录，再与改名合并） ----
    move_dir: dict[int, dict] = {}
    moved_dirs: set[str] = set()
    if "season" in actions or "specials" in actions:
        for e in episodes:
            fp = str(e["file_path"] or "")
            d2 = _project(os.path.dirname(fp), proj)
            try:
                season = int(e.get("season") or 0)
            except (TypeError, ValueError):
                season = 0
            in_show = d2 == show_dir
            # 已在季目录内（Season NN/Specials/特典…）：重绑后季拆分变化（如平台合集
            # 拼接号 S1~S4）时按库内季号归位；深度 >1 的目录交给 wrapper/untouched。
            in_season_dir = (os.path.dirname(d2) == show_dir
                             and season_from_dir(os.path.basename(d2)) is not None)
            target_dir = None
            action = None
            if season == 0:
                if "specials" in actions and not _is_canonical_specials_dir(d2, show_dir):
                    target_dir = _join(show_dir, "Season 00")
                    action = "specials"
            elif "season" in actions:
                if in_show:
                    target_dir = _join(show_dir, _season_dir_name(season))
                    action = "season"
                elif in_season_dir:
                    want = _join(show_dir, _season_dir_name(season))
                    if want != d2:
                        target_dir = want
                        action = "season"
            if not target_dir:
                continue
            move_dir[int(e["id"])] = {"dir": target_dir, "action": action}
            cur = os.path.dirname(fp)
            while cur and cur != show_dir and cur.startswith(show_dir + "/"):
                moved_dirs.add(cur)
                cur = os.path.dirname(cur)

    # ---- 6) 正片统一命名（与移动合并：每个文件只动一次） ----
    rename_on = "rename" in actions
    used: dict[str, set] = {}
    # 多版本版本号：组内稳定排序（已带 Vn 标记的排后），重复执行不互换
    ver_of: dict[int, int] = {}
    if rename_on:
        by_ep: dict[tuple, list[dict]] = {}
        for e in episodes:
            by_ep.setdefault((int(e.get("season") or 0),
                              int(e.get("episode") or 0)), []).append(e)
        for members in by_ep.values():
            members.sort(key=lambda e: (
                _version_num(str(e.get("file_path") or "")),
                str(e.get("file_path") or "")))
            ver_used: set[int] = set()
            nxt_free = 1
            for e in members:
                path = str(e.get("file_path") or "")
                v = _version_num(path)
                if v <= 1 and re.search(r"-part\d+$",
                                        os.path.splitext(os.path.basename(path))[0],
                                        re.I):
                    v = 1                         # Plex 拆分集（-partN）：同一版本
                elif v <= 1:                      # 未标记：按 1,2,… 顺序补位
                    while nxt_free in ver_used:
                        nxt_free += 1
                    v = nxt_free
                ver_used.add(v)
                ver_of[int(e["id"])] = v          # 已带 Vn 标记的原样保留
    # 绝对集号风险：仅当确有正片需要改名时才提示/拦截；已全部规范名的剧不打扰
    plan["absolute_risk"] = bool(
        rename_on and _absolute_risk(episodes)
        and _abs_rename_pending(episodes, show, ver_of))
    if plan["absolute_risk"]:
        if allow_absolute:
            plan["warnings"].append(
                "绝对集号风险：已显式勾选，按 TMDB 编号改名（Plex 季/集拆分可能不同）")
        else:
            plan["manual"].append({"file": show_dir, "scope": "show",
                                   "reason": "absolute",
                                   "suggestion": _SUGGEST["absolute"]})
    rename_abs_ok = bool(allow_absolute) and rename_on
    if rename_on and not show.get("tmdb_id"):
        plan["manual"].append({"file": show_dir, "scope": "show", "reason": "unmatched",
                               "suggestion": _SUGGEST["unmatched"]})
    # 输出/执行顺序：先版本分组（V1 全部在前，再 V2…），再季/集
    ep_sorted = sorted(episodes, key=lambda e: (
        ver_of.get(int(e["id"]), 1),
        int(e.get("season") or 0), int(e.get("episode") or 0),
        str(e.get("file_path") or "")))
    # 最终归属目录分布（含无需移动的正片）：预览用，回答"执行后每季各多少集"，
    # 避免"Season 01 只有 1 项"被误读（1 项=需移动的，480 项=已就位不再列出）。
    dir_totals: dict[str, int] = {}
    for e in ep_sorted:
        eid = int(e["id"])
        fp = str(e["file_path"] or "")
        src_dir = os.path.dirname(fp)
        base = os.path.basename(fp)
        stem, ext = os.path.splitext(base)
        mv = move_dir.get(eid)
        final_dir = mv["dir"] if mv else _project(src_dir, proj)
        dir_totals[final_dir] = dir_totals.get(final_dir, 0) + 1
        action = mv["action"] if mv else "rename"
        new_base = None
        new_stem = None
        if rename_on:
            idx = ver_of.get(eid, 0)
            if (plan["absolute_risk"] and not rename_abs_ok) or not show.get("tmdb_id"):
                new_base = None
            elif e.get("local_only"):
                plan["kept"].append(fp)          # 已确认本地集：保持原名，不计需手动
            elif int(e.get("needs_review") or 0):
                _manual(fp, "needs_review")
            elif not e.get("tmdb_episode_id"):
                _manual(fp, "needs_review")
            else:
                new_stem = _target_stem(show.get("title") or "", e, ver=idx)
                if not new_stem:
                    _manual(fp, "no_title"
                            if not (show.get("title") or "") else "bad_episode")
                else:
                    new_base = new_stem + ext
        if new_base and new_stem:
            pm = re.search(r"-part\d+$", stem, re.I)
            if pm and stem[:pm.start()].casefold() == new_stem.casefold():
                new_base = None      # 已是 Plex 拆分集命名（-partN），保留不动
        if new_base and new_base.casefold() == base.casefold():
            new_base = None          # 已是规范名（重复预览/执行幂等）
        elif new_base:
            key_dir = final_dir.casefold()
            if (new_base.casefold() in used.get(key_dir, set())
                    or new_base.casefold() in _lower(cache.entries(final_dir))):
                _manual(fp, "exists")
                new_base = None
        moved = final_dir != src_dir
        renamed = bool(new_base) and new_base != base
        if renamed:
            plan["rename_count"] += 1
        if not (moved or renamed):
            continue
        target = _join(final_dir, new_base or base)
        if target in planned:
            continue
        planned.add(target)
        used.setdefault(final_dir.casefold(), set()).add((new_base or base).casefold())
        plan["file_moves"].append({"action": action, "kind": "episode", "from": fp,
                                   "to": target, "dir": final_dir})
        # 同茎字幕/NFO 跟随（改名的用新茎，未改名仅跟随移动）
        if moved or renamed:
            for f in sorted(cache.entries(src_dir)):
                if f == base or cache.entries(src_dir).get(f):
                    continue
                fstem, fext = os.path.splitext(f)
                if fext.lower() not in _MOVE_SIBLING_EXTS:
                    continue
                if not _same_stem_as(fstem, stem):
                    continue
                if new_base:
                    suffix = f[len(stem):]
                    fname = (new_base[:-len(ext)] if ext else new_base) + suffix
                else:
                    fname = f
                t2 = _join(final_dir, fname)
                if t2 in planned:
                    continue
                if final_dir != src_dir and fname.casefold() in _lower(cache.entries(final_dir)):
                    continue
                planned.add(t2)
                used.setdefault(final_dir.casefold(), set()).add(fname.casefold())
                plan["file_moves"].append({"action": action, "kind": "file",
                                           "from": _join(src_dir, f), "to": t2,
                                           "dir": final_dir})

    plan["dir_totals"] = dir_totals

    # ---- 7) 花絮：类型目录整目录上移（深度 ≤2；更深只报告） ----
    if "extras" in actions:
        rename_map = [{"from": r["from"], "to": r["to"]} for r in plan["dir_renames"]]
        ep_dirs = direct
        promoted: dict[str, str] = {}
        for x in extras_rows:
            fp = _project(str(x.get("file_path") or ""), rename_map)
            if not fp.startswith(show_dir + "/"):
                continue
            kind_dir = _kind_dir_of(fp, show_dir)
            if not kind_dir:
                continue
            placement = _extras_placement(kind_dir, show_dir)
            if placement == "canonical":
                continue                      # Plex 已可见（剧根/季级花絮目录）
            if kind_dir in promoted:
                continue
            if placement == "deep":
                plan["untouched"].append(fp)  # 更深层：需手动整理
                continue
            if any(d == kind_dir or d.startswith(kind_dir + "/") for d in ep_dirs):
                plan["warnings"].append(f"{kind_dir} 内含集文件，跳过上移")
                plan["untouched"].append(fp)
                continue
            target = _join(show_dir, os.path.basename(kind_dir))
            if (target in planned or target in {r["to"] for r in plan["dir_renames"]}
                    or os.path.basename(kind_dir).casefold() in _lower(show_snap)):
                plan["conflicts"].append({"action": "extras", "from": kind_dir,
                                          "to": target,
                                          "reason": "剧根同名目录已存在（不合并）"})
                plan["untouched"].append(fp)
                continue
            planned.add(target)
            promoted[kind_dir] = target
            plan["dir_moves"].append({"action": "extras", "kind": "extra",
                                      "from": kind_dir, "to": target})
        # 散文件 / 无类型目录 / 深层目录：只报告（需手动整理）
        for x in extras_rows:
            fp = _project(str(x.get("file_path") or ""), rename_map)
            d = os.path.dirname(fp)
            if not d.startswith(show_dir + "/") or d == show_dir:
                continue
            if any(d == kd or d.startswith(kd + "/") for kd in promoted):
                continue
            kind_dir = _kind_dir_of(fp, show_dir)
            if (kind_dir and kind_dir == d
                    and _extras_placement(kind_dir, show_dir) == "canonical"):
                continue                      # 直接位于剧根/季级花絮目录 → Plex 可见
            plan["untouched"].append(fp)
        for kd in promoted:
            cur = os.path.dirname(kd)
            while cur and cur != show_dir and cur.startswith(show_dir + "/"):
                moved_dirs.add(cur)
                cur = os.path.dirname(cur)
    plan["rmdirs"].extend(sorted(moved_dirs, key=lambda x: (-len(x), x)))
    plan["rmdirs"] = sorted(set(plan["rmdirs"]), key=lambda x: (-len(x), x))
    plan["untouched"] = sorted(set(plan["untouched"]))
    plan["kept"] = sorted(set(plan["kept"]))

    # ---- 8) 剧根改名（最后执行；必须在所有内部动作之后） ----
    if "root" in actions:
        target_name = _root_target(show, profile)
        cur_base = os.path.basename(show_dir)
        if target_name and target_name.casefold() != cur_base.casefold():
            parent = os.path.dirname(show_dir)
            target = _join(parent, target_name)
            parent_snap = cache.entries(parent)
            if target in (claimed or {}) or target_name.casefold() in _lower(parent_snap):
                plan["conflicts"].append({"action": "root", "from": show_dir, "to": target,
                                          "reason": "剧根目标名已存在（不覆盖）"})
            else:
                plan["root_move"] = {"from": show_dir, "to": target}
                if claimed is not None:
                    claimed[target] = show_id
    return plan


def plan_tv_organize(library_ids=None, ids=None, actions=TV_ORGANIZE_ACTIONS,
                     allow_torrent: bool = False,
                     allow_absolute_shows=None) -> dict:
    """预览整理计划（不落盘）。`actions` ⊆ 见 TV_ORGANIZE_ACTIONS；只读库跳过。

    `allow_absolute_shows`：显式勾选（同意）的剧 id 集合——绝对集号风险剧在其内时
    才按 TMDB 编号改名（否则只列 manual 不动）。"""
    acts = tuple(a for a in (actions or TV_ORGANIZE_ACTIONS) if a in TV_ORGANIZE_ACTIONS)
    shows = store.list_shows_for_scrape(library_ids=library_ids, ids=ids, force=True)
    allow_abs = {int(x) for x in (allow_absolute_shows or [])}
    plans, counts = [], {a: 0 for a in TV_ORGANIZE_ACTIONS}
    claimed: dict = {}
    for show in shows:
        if show.get("library_id") and _read_only(show.get("library_id")):
            continue
        backend = _backend(show.get("library_id"))
        if backend is None:
            continue
        profile = _naming_profile(show.get("library_id"))
        try:
            plan = _plan_show(show, acts, _DirCache(backend), allow_torrent,
                              profile=profile, claimed=claimed,
                              allow_absolute=int(show["id"]) in allow_abs)
        except Exception as e:
            logger.warning("tv organize plan failed show=%s: %s", show.get("id"), e)
            plan = {"show_id": show.get("id"), "title": show.get("title"),
                    "library_id": show.get("library_id"),
                    "dir_moves": [], "file_moves": [], "dir_renames": [],
                    "rmdirs": [], "untouched": [], "manual": [], "manual_more": 0,
                    "kept": [], "rename_count": 0, "absolute_risk": False,
                    "root_move": None,
                    "conflicts": [], "blocked": False, "dir_totals": {},
                    "warnings": [f"plan error: {str(e)[:160]}"]}
        if not plan:
            continue
        for m in plan["dir_moves"] + plan["dir_renames"]:
            k = m.get("action")
            if k in counts:
                counts[k] += 1
        for m in plan["file_moves"]:
            k = m.get("action")
            if k in counts:
                counts[k] += 1
        if plan.get("root_move"):
            counts["root"] += 1
        if (plan["file_moves"] or plan["dir_moves"] or plan["dir_renames"]
                or plan.get("root_move") or plan["conflicts"] or plan["warnings"]
                or plan.get("untouched") or plan.get("manual") or plan.get("kept")):
            plans.append(plan)
    return {"plans": plans, "counts": counts, "total": sum(counts.values()),
            "conflicts": sum(len(p["conflicts"]) for p in plans),
            "untouched": sum(len(p.get("untouched") or []) for p in plans),
            "manual": sum(len(p.get("manual") or []) + int(p.get("manual_more") or 0)
                          for p in plans),
            "kept": sum(len(p.get("kept") or []) for p in plans),
            "absolute": sum(1 for p in plans if p.get("absolute_risk")),
            "blocked": sum(1 for p in plans if p.get("blocked"))}


def summarize_plan(plan: dict, samples: int = 3) -> dict:
    """把完整计划压成分组摘要（dry-run 返回给前端；大库不传全量 moves）。"""
    groups: list[dict] = []
    if plan.get("root_move"):
        groups.append({"action": "root", "label": _LABELS["root"],
                       "from": plan["root_move"]["from"], "to": plan["root_move"]["to"],
                       "count": 1, "samples": [], "dir": True})
    for m in plan.get("dir_moves") or []:
        groups.append({"action": m.get("action"), "label": _LABELS.get(m.get("action"),
                       m.get("action")), "from": m["from"], "to": m["to"], "count": 1,
                       "samples": [], "dir": True})
    for m in plan.get("dir_renames") or []:
        groups.append({"action": m.get("action"), "label": "目录改名",
                       "from": m["from"], "to": m["to"], "count": 1,
                       "samples": [], "dir": True})
    buckets: dict[tuple, dict] = {}
    for m in plan.get("file_moves") or []:
        key = (m.get("action"), os.path.dirname(m["to"]))
        b = buckets.get(key)
        if b is None:
            b = buckets[key] = {"action": m.get("action"),
                                "label": _LABELS.get(m.get("action"), m.get("action")),
                                "to": os.path.dirname(m["to"]), "count": 0,
                                "episodes": 0, "files": 0,
                                "samples": [], "renamed": 0}
        b["count"] += 1
        if str(m.get("kind") or "") == "file":
            b["files"] += 1          # 同茎字幕/NFO 等附属文件（跟随正片）
        else:
            b["episodes"] += 1
        if os.path.basename(m["to"]) != os.path.basename(m["from"]):
            b["renamed"] += 1
        if len(b["samples"]) < samples:
            b["samples"].append({"from": m["from"], "to": m["to"]})
    groups.extend(buckets.values())
    untouched_dirs: dict[str, dict] = {}
    for p in plan.get("untouched") or []:
        d = os.path.dirname(p)
        g = untouched_dirs.setdefault(d, {"dir": d, "count": 0, "samples": []})
        g["count"] += 1
        if len(g["samples"]) < samples:
            g["samples"].append(p)
    counts: dict = {}
    for g in groups:
        if g.get("dir"):
            counts[g["action"]] = counts.get(g["action"], 0) + 1
        else:
            counts[g["action"]] = counts.get(g["action"], 0) + g["count"]
    counts["rename"] = int(plan.get("rename_count") or counts.get("rename", 0))
    dir_totals = [{"dir": d, "count": int(n)}
                  for d, n in sorted((plan.get("dir_totals") or {}).items())]
    kept = sorted(set(plan.get("kept") or []))
    return {"show_id": plan.get("show_id"), "title": plan.get("title"),
            "library_id": plan.get("library_id"), "show_dir": plan.get("show_dir"),
            "blocked": bool(plan.get("blocked")), "absolute_risk":
            bool(plan.get("absolute_risk")), "warnings": plan.get("warnings") or [],
            "counts": counts, "groups": groups, "dir_totals": dir_totals,
            "untouched": [untouched_dirs[k] for k in sorted(untouched_dirs)],
            "untouched_count": len(plan.get("untouched") or []),
            "manual": plan.get("manual") or [], "manual_more":
            int(plan.get("manual_more") or 0),
            "kept": kept, "kept_count": len(kept),
            "conflicts": plan.get("conflicts") or []}


def _read_only(library_id) -> bool:
    try:
        from .. import library_paths
        return library_paths.is_read_only(int(library_id))
    except Exception:
        return False


def _naming_profile(library_id) -> str:
    try:
        from .. import library_paths
        return library_paths.naming_profile(int(library_id))
    except Exception:
        return "kodi"


def _backend(library_id):
    try:
        from .. import storage
        return storage.backend_for(int(library_id))
    except Exception as e:
        logger.debug("tv organize backend failed lib=%s: %s", library_id, e)
        return None


def _new_batch_id(prefix: str) -> str:
    return f"{prefix}-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"


def execute_tv_organize(plans: list[dict], should_stop=None, progress_cb=None,
                        allow_torrent: bool = False, batch_id: str | None = None) -> dict:
    """执行整理计划：目录改名 → 目录移动 → 文件移动/改名 → 空目录清理 → 季海报 →
    剧根改名（最后）。逐条写审计（organize_moves）供撤销；返回 {moved, renamed,
    skipped, failed, errors[], shows[], batch_id}；失败逐条记录并继续。"""
    batch_id = str(batch_id or "") or _new_batch_id("org")
    out = {"moved": 0, "renamed": 0, "skipped": 0, "failed": 0, "errors": [],
           "shows": [], "batch_id": batch_id}
    total = sum(len(p.get("dir_renames") or []) + len(p.get("dir_moves") or [])
                + len(p.get("file_moves") or []) + len(p.get("rmdirs") or [])
                + (1 if p.get("root_move") else 0)
                for p in plans or [])
    done = 0

    def _tick():
        nonlocal done
        done += 1
        if progress_cb:
            try:
                progress_cb(done, total)
            except Exception as e:
                logger.debug("progress_cb failed: %s", e)

    for plan in plans or []:
        if should_stop and should_stop():
            break
        if plan.get("blocked") and not allow_torrent:
            out["skipped"] += 1
            continue
        show_id = int(plan.get("show_id") or 0)
        lib_id = plan.get("library_id") or store.DEFAULT_LIBRARY_ID
        backend = _backend(lib_id)
        if backend is None or not show_id:
            out["failed"] += 1
            continue
        mov = {"renamed": 0, "moved": 0, "skipped": 0, "failed": 0, "errors": []}
        audit: list[dict] = []
        dir_map: list[dict] = []
        cache = _DirCache(backend)
        made_dirs: set[str] = set()

        def _one_dir(from_rel: str, to_rel: str) -> str:
            if not backend.exists(from_rel):
                return "skipped"
            if backend.exists(to_rel):
                return "skipped"
            parent = os.path.dirname(to_rel)
            if parent:
                backend.mkdir(parent, parents=True)
            backend.rename(from_rel, to_rel)
            return "done"

        def _move_file(from_rel: str, to_rel: str) -> str:
            """单次 rename：源存在性交给 rename 自身（省一次 SMB stat）；
            目标占用用目录快照本地判定，防覆盖。"""
            d = os.path.dirname(to_rel)
            if d and d not in made_dirs:
                backend.mkdir(d, parents=True)
                made_dirs.add(d)
            base = os.path.basename(to_rel)
            if base.casefold() in _lower(cache.entries(d)):
                return "skipped"
            try:
                backend.rename(from_rel, to_rel)
            except Exception as e:
                if _is_not_found(e):
                    return "skipped"
                raise
            cache.discard(os.path.dirname(from_rel), os.path.basename(from_rel))
            cache.add(d, base)
            return "done"

        # 1) 花絮单数目录改名
        for m in plan.get("dir_renames") or []:
            if should_stop and should_stop():
                break
            try:
                st = _one_dir(m["from"], m["to"])
                if st == "done":
                    store.repath_extras_prefix(int(lib_id), m["from"], m["to"])
                    dir_map.append({"from": m["from"], "to": m["to"]})
                    cache.clear()
                    audit.append({"library_id": int(lib_id), "show_id": show_id,
                                  "kind": m.get("kind") or "dir", "obj": "dir",
                                  "action": m.get("action") or "rename",
                                  "from_path": m["from"], "to_path": m["to"]})
                    mov["renamed"] += 1
                else:
                    mov["skipped"] += 1
            except Exception as e:
                mov["failed"] += 1
                mov["errors"].append(f"{m['from']}: {str(e)[:160]}")
            _tick()
        # 2) 目录移动（包装层/季目录/花絮上移）
        for m in plan.get("dir_moves") or []:
            if should_stop and should_stop():
                break
            src = _project(m["from"], dir_map)
            dst = m["to"]
            try:
                st = _one_dir(src, dst)
                if st == "done":
                    store.repath_tv_episodes_prefix(int(lib_id), src, dst)
                    store.repath_extras_prefix(int(lib_id), src, dst)
                    dir_map.append({"from": src, "to": dst})
                    cache.clear()
                    audit.append({"library_id": int(lib_id), "show_id": show_id,
                                  "kind": m.get("kind") or "dir", "obj": "dir",
                                  "action": m.get("action") or "wrapper",
                                  "from_path": src, "to_path": dst})
                    mov["renamed"] += 1
                else:
                    mov["skipped"] += 1
            except Exception as e:
                mov["failed"] += 1
                mov["errors"].append(f"{src}: {str(e)[:160]}")
            _tick()
        # 3) 文件移动/改名（from 路径应用已执行的目录映射；to 已是终态）
        mapping: dict[str, str] = {}
        for m in plan.get("file_moves") or []:
            if should_stop and should_stop():
                break
            src = _project(m["from"], dir_map)
            dst = m["to"]
            try:
                st = _move_file(src, dst)
                if st == "done":
                    mapping[src] = dst
                    audit.append({"library_id": int(lib_id), "show_id": show_id,
                                  "kind": m.get("kind") or "file", "obj": "file",
                                  "action": m.get("action") or "move",
                                  "from_path": src, "to_path": dst})
                    mov["moved"] += 1
                else:
                    mov["skipped"] += 1
            except Exception as e:
                mov["failed"] += 1
                mov["errors"].append(f"{src}: {str(e)[:160]}")
            _tick()
        if mapping:
            try:
                store.move_tv_paths(int(lib_id), mapping)
            except Exception as e:
                logger.warning("tv organize db move failed show=%s: %s", show_id, e)
                mov["errors"].append(f"db move: {str(e)[:160]}")
        # 4) 空目录清理（仅空目录，失败忽略）
        for d in plan.get("rmdirs") or []:
            if should_stop and should_stop():
                break
            try:
                d2 = _project(d, dir_map)
                if backend.exists(d2):
                    backend.delete(d2)
                    cache.clear()
                    audit.append({"library_id": int(lib_id), "show_id": show_id,
                                  "kind": "rmdir", "obj": "dir", "action": "rmdir",
                                  "from_path": d2, "to_path": ""})
            except Exception as e:
                logger.debug("tv organize rmdir skipped dir=%s: %s", d, e)
            _tick()
        # 4.5) 审计落库（撤销/还原依据；逐剧本批，失败不阻塞整理）
        if audit:
            try:
                store.record_organize_moves(batch_id, audit, library_id=lib_id)
            except Exception as e:
                logger.warning("tv organize audit failed show=%s: %s", show_id, e)
                mov["errors"].append(f"audit: {str(e)[:160]}")
        # 5) 季海报补写（新增 Season 目录 / 目录改名后）
        if mov["moved"] or mov["renamed"]:
            try:
                from .. import artwork
                artwork.write_for_show(show_id, backend=backend)
            except Exception as e:
                logger.debug("tv organize artwork failed show=%s: %s", show_id, e)
        # 6) 剧根改名（最后：内部动作全部完成后整目录 rename）
        rm = plan.get("root_move")
        if rm and not (should_stop and should_stop()):
            try:
                st = _one_dir(rm["from"], rm["to"])
                if st == "done":
                    store.repath_tv_episodes_prefix(int(lib_id), rm["from"], rm["to"])
                    store.repath_extras_prefix(int(lib_id), rm["from"], rm["to"])
                    audit.append({"library_id": int(lib_id), "show_id": show_id,
                                  "kind": "dir", "obj": "dir", "action": "root",
                                  "from_path": rm["from"], "to_path": rm["to"]})
                    mov["renamed"] += 1
                    try:
                        store.record_organize_moves(
                            batch_id, [audit[-1]], library_id=lib_id)
                    except Exception as e:
                        logger.warning("tv organize root audit failed: %s", e)
                    try:
                        from .. import artwork
                        artwork.write_for_show(show_id, backend=backend)
                    except Exception as e:
                        logger.debug("tv organize root art failed show=%s: %s", show_id, e)
                else:
                    mov["skipped"] += 1
            except Exception as e:
                mov["failed"] += 1
                mov["errors"].append(f"{rm['from']}: {str(e)[:160]}")
            _tick()
        out["moved"] += mov["moved"]
        out["renamed"] += mov["renamed"]
        out["skipped"] += mov["skipped"]
        out["failed"] += mov["failed"]
        out["errors"].extend(mov["errors"][:20])
        out["shows"].append({"show_id": show_id, "title": plan.get("title"), **mov})
    return out


def _is_not_found(e: BaseException) -> bool:
    try:
        from .. import storage
        for name in ("StorageNotFound",):
            cls = getattr(storage, name, None)
            if cls is not None and isinstance(e, cls):
                return True
    except Exception:
        pass
    return False


# ---- 撤销整理（v24 审计）：把 batch 内移动逐条反向搬回 ---------------------------------

def plan_restore(batch_id: str | None = None, library_ids=None, shows=None, kinds=None,
                 limit: int = 20000) -> dict:
    """预览「撤销整理」：按审计把移动反向搬回（不落盘）。

    `batch_id` 缺省取最近一次批次；`library_ids` 限视频库；`shows` 为 show_id 列表，
    `kinds` 为 episode|extra|file|dir|rmdir 过滤。输出 {batch_id, plans[], counts,
    total, conflicts}；冲突（源缺失/原路径被占）只报告不执行。"""
    if not batch_id:
        batches = store.list_organize_batches(limit=1)
        batch_id = str(batches[0]["batch_id"]) if batches else ""
    moves = store.list_organize_moves(batch_id=batch_id, include_undone=False,
                                      limit=limit)
    if library_ids:
        want_libs = {int(x) for x in library_ids}
        moves = [m for m in moves if int(m.get("library_id") or 0) in want_libs]
    if kinds:
        want = {str(k) for k in kinds}
        moves = [m for m in moves if str(m.get("kind") or "") in want]
    if shows:
        want_shows = {int(s) for s in shows}
        moves = [m for m in moves if int(m.get("show_id") or 0) in want_shows]
    # 反向时序还原：后发生的先还原（先撤文件、再撤目录，否则文件会随目录先搬走）
    moves.sort(key=lambda m: -int(m.get("id") or 0))
    # 当前落盘路径重建：审计里的 to_path 是「当时」路径；之后发生的目录整理（root/wrapper…）
    # 会改前缀。计划内的高 id 目录移动在还原时先被撤销 → 反向（to→from）；
    # 计划外（本次不撤）的目录移动仍然生效 → 正向（from→to）。
    plan_dir_ids = {int(m["id"]) for m in moves
                    if str(m.get("obj") or "") == "dir" and str(m.get("kind") or "") != "rmdir"}
    dir_fwd = sorted(
        ({"id": int(m["id"]), "from": str(m.get("from_path") or ""),
          "to": str(m.get("to_path") or "")} for m in moves
         if str(m.get("obj") or "") == "dir" and str(m.get("kind") or "") != "rmdir"
         and m.get("from_path") and m.get("to_path")),
        key=lambda r: r["id"])

    def _path_now(path: str, row_id: int) -> str:
        """当前落盘位置：套用之后发生的全部目录移动（审计 to_path 是「当时」路径）。"""
        for r in dir_fwd:
            if r["id"] > row_id and (path == r["from"]
                                     or path.startswith(r["from"] + "/")):
                path = r["to"] + path[len(r["from"]):]
        return path

    def _path_at_restore_time(path_now: str, row_id: int) -> str:
        """还原到该行时的位置：计划内的高 id 目录移动会先被撤销（反向）。"""
        for r in dir_fwd:
            if (r["id"] > row_id and r["id"] in plan_dir_ids
                    and (path_now == r["to"] or path_now.startswith(r["to"] + "/"))):
                path_now = r["from"] + path_now[len(r["to"]):]
        return path_now

    groups: dict[tuple, list[dict]] = {}
    for m in moves:
        groups.setdefault((int(m.get("library_id") or 0),
                           int(m.get("show_id") or 0)), []).append(m)
    plans, counts = [], {}
    for (lib_id, show_id), rows in groups.items():
        backend = _backend(lib_id)
        show = store.get_show(show_id) or {}
        plan = {"library_id": lib_id, "show_id": show_id,
                "title": show.get("title") or f"#{show_id}",
                "moves": [], "conflicts": []}
        planned_targets: set[str] = set()
        for m in rows:
            kind = str(m.get("kind") or "file")
            counts[kind] = counts.get(kind, 0) + 1
            dst = str(m.get("from_path") or "")
            recorded = str(m.get("to_path") or "")
            src_now = _path_now(recorded, int(m["id"]))
            src = _path_at_restore_time(src_now, int(m["id"]))
            obj = str(m.get("obj") or "file")
            if kind == "rmdir":
                if dst:
                    plan["moves"].append({"kind": kind, "obj": "dir",
                                          "action": "restore_rmdir", "from": "", "to": dst,
                                          "move_id": int(m["id"]), "show_id": show_id,
                                          "library_id": lib_id})
                continue
            if not src or not dst:
                continue
            entry = {"kind": kind, "obj": obj, "action": "restore", "from": src,
                     "to": dst, "move_id": int(m["id"]), "show_id": show_id,
                     "library_id": lib_id}
            if src == dst:
                # 已被更上层的目录还原带回原位（目录内文件没有逐条平移）
                entry["action"] = "restore_covered"
                plan["moves"].append(entry)
                continue
            if backend is None:
                plan["conflicts"].append({**entry, "reason": "存储后端不可用"})
                continue
            try:
                if not backend.exists(src_now):
                    plan["conflicts"].append(
                        {**entry, "reason": "源不存在（可能已还原或已被手动移动）"})
                    continue
                if dst in planned_targets or backend.exists(dst):
                    plan["conflicts"].append({**entry, "reason": "原路径已被占用"})
                    continue
            except Exception as e:
                plan["conflicts"].append({**entry, "reason": f"检查失败: {str(e)[:80]}"})
                continue
            planned_targets.add(dst)
            plan["moves"].append(entry)
        if plan["moves"] or plan["conflicts"]:
            plans.append(plan)
    return {"batch_id": batch_id, "plans": plans, "counts": counts,
            "total": sum(len(p["moves"]) for p in plans),
            "conflicts": sum(len(p["conflicts"]) for p in plans)}


def execute_restore(plans: list[dict], should_stop=None, progress_cb=None,
                    batch_id: str | None = None) -> dict:
    """执行撤销：逐条反向搬回 + DB 路径回写 + 清增量状态 + 标记原移动已撤销。

    自身也写审计（action=restore），可再次撤销=重做。返回
    {restored, skipped, failed, errors[], shows[], batch_id}。"""
    batch_id = str(batch_id or "") or _new_batch_id("undo")
    out = {"restored": 0, "skipped": 0, "failed": 0, "errors": [], "shows": [],
           "batch_id": batch_id}
    total = sum(len(p.get("moves") or []) for p in plans or [])
    done = 0

    def _tick():
        nonlocal done
        done += 1
        if progress_cb:
            try:
                progress_cb(done, total)
            except Exception as e:
                logger.debug("progress_cb failed: %s", e)

    for plan in plans or []:
        if should_stop and should_stop():
            break
        lib_id = int(plan.get("library_id") or 0) or store.DEFAULT_LIBRARY_ID
        show_id = int(plan.get("show_id") or 0)
        backend = _backend(lib_id)
        if backend is None:
            out["failed"] += len(plan.get("moves") or [])
            out["errors"].append(f"lib {lib_id}: 存储后端不可用")
            continue
        mov = {"restored": 0, "skipped": 0, "failed": 0, "errors": []}
        audit: list[dict] = []
        mapping: dict[str, str] = {}
        undone_ids: list[int] = []
        cache = _DirCache(backend)

        def _flush_mapping() -> None:
            """逐条精确回写 DB（目录还原前必须先冲掉，否则旧前缀对不上）。"""
            if not mapping:
                return
            try:
                store.move_tv_paths(lib_id, dict(mapping))
                store.delete_scan_state_paths(lib_id, list(mapping.keys()))
            except Exception as e:
                logger.warning("tv restore db move failed show=%s: %s", show_id, e)
                mov["errors"].append(f"db move: {str(e)[:160]}")
            mapping.clear()

        for m in plan.get("moves") or []:
            if should_stop and should_stop():
                break
            kind, src, dst = m["kind"], m["from"], m["to"]
            if m.get("action") == "restore_covered":
                # 目录还原已把它带回原位：只销账不搬
                if m.get("move_id"):
                    undone_ids.append(int(m["move_id"]))
                mov["restored"] += 1
                _tick()
                continue
            is_dir = str(m.get("obj") or "file") == "dir"
            try:
                if kind == "rmdir":
                    if dst and not backend.exists(dst):
                        backend.mkdir(dst, parents=True)
                        mov["restored"] += 1
                    else:
                        mov["skipped"] += 1
                    if m.get("move_id"):
                        undone_ids.append(int(m["move_id"]))
                    _tick()
                    continue
                if not backend.exists(src):
                    mov["skipped"] += 1
                    _tick()
                    continue
                if backend.exists(dst):
                    mov["skipped"] += 1
                    _tick()
                    continue
                if is_dir:
                    _flush_mapping()
                parent = os.path.dirname(dst)
                if parent:
                    backend.mkdir(parent, parents=True)
                backend.rename(src, dst)
                cache.clear()
                if is_dir:
                    # 目录内文件没有逐条审计 → 立即按前缀把 DB 行搬回旧根
                    store.repath_tv_episodes_prefix(lib_id, src, dst)
                    store.repath_extras_prefix(lib_id, src, dst)
                    store.delete_scan_state_prefix(lib_id, src)
                else:
                    mapping[src] = dst
                undone_ids.append(int(m["move_id"]))
                audit.append({"library_id": lib_id, "show_id": show_id, "kind": kind,
                              "obj": str(m.get("obj") or "file"),
                              "action": "restore", "from_path": src, "to_path": dst})
                mov["restored"] += 1
            except Exception as e:
                mov["failed"] += 1
                mov["errors"].append(f"{src}: {str(e)[:160]}")
            _tick()
        _flush_mapping()
        if undone_ids:
            try:
                store.mark_organize_undone(undone_ids)
            except Exception as e:
                logger.warning("tv restore mark undone failed show=%s: %s", show_id, e)
        if audit:
            try:
                store.record_organize_moves(batch_id, audit, library_id=lib_id)
            except Exception as e:
                mov["errors"].append(f"audit: {str(e)[:160]}")
        # 注意：还原不再自动写 NFO/海报（避免生成"不属于本次移动"的新文件）；
        # 目录内原有 NFO/海报随目录 rename 一起还原，如需重写请跑「重建剧集 NFO/海报」。
        out["restored"] += mov["restored"]
        out["skipped"] += mov["skipped"]
        out["failed"] += mov["failed"]
        out["errors"].extend(mov["errors"][:20])
        out["shows"].append({"show_id": show_id, "title": plan.get("title"), **mov})
    return out
