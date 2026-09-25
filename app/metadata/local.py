"""本地离线匹配（E 阶段）：基于 match_index（TMDB 缓存/NFO/外部候选的统一索引）。

- 中文部分词召回靠 LIKE（FTS unicode61 对 CJK 分词不友好），评分在 Python 侧做归一化比对。
- 高置信阈值默认 45（标题精确 + 年份命中），仅供 scan 自动绑定；低分候选给人选。
"""
from .. import store
from ..scanner.parse import normalize_title
from .base import Candidate

__all__ = ['search', 'best', 'score_row', 'library_index', 'library_hit',
           'parse_title_variants', 'parse_path_variants', 'parse_path_year',
           'MIN_AUTO_SCORE']

MIN_AUTO_SCORE = 45.0


def library_index() -> dict:
    """跨库已匹配影片索引（远程库扫描"本地优先"用）：
    {(归一标题|原标题, 年份): (tmdb_id, 来源)}。一次全表读，扫描批次共享。"""
    idx: dict = {}
    try:
        rows = store.list_movies(grouped=False, limit=100000)
    except Exception:
        return idx
    for m in rows:
        tid = m.get("tmdb_id")
        if not tid:
            continue
        try:
            tid = int(tid)
        except (TypeError, ValueError):
            continue
        year = m.get("year")
        try:
            year = int(year) if year is not None else None
        except (TypeError, ValueError):
            year = None
        for key in (normalize_title(m.get("title") or ""),
                    normalize_title(m.get("original_title") or "")):
            if key:
                idx.setdefault((key, year), (tid, "library"))
    return idx


def library_hit(title: str, year, original_title: str = "",
                index: dict | None = None):
    """本地优先命中：其他库已匹配的同名片（归一标题 + 年份精确）→ Candidate（零网络）。

    查询键：文件名标题、原标题；年份缺失时仅当该标题跨年唯一才绑定。
    都没命中返回 None（继续 TMDB 搜索，之后的离线兜底仍会走 match_index，见 `best`）。"""
    keys = [k for k in (normalize_title(title or ""),
                        normalize_title(original_title or "")) if k]
    if not keys:
        return None
    try:
        y = int(year) if year is not None else None
    except (TypeError, ValueError):
        y = None
    idx = library_index() if index is None else index
    for k in keys:
        hit = idx.get((k, y))
        if hit:
            return Candidate(title=title or "", original_title=original_title or "",
                             year=y, tmdb_id=hit[0], imdb_id="", source="library",
                             source_id=str(hit[0]), score=60.0, payload={})
    if y is None:
        tids = {v[0] for (k, _yy), v in idx.items() if k in keys}
        if len(tids) == 1:
            tid = next(iter(tids))
            return Candidate(title=title or "", original_title=original_title or "",
                             year=None, tmdb_id=tid, imdb_id="", source="library",
                             source_id=str(tid), score=55.0, payload={})
    return None


def parse_title_variants(basename: str) -> list[str]:
    """文件名 → 本地优先查询键（除 guessit 标题外，补点分段的**中文**段）：
    `告白.Confessions.2010` → ['Confessions', '告白']（guessit 只认英文段，中文段靠这里补）。
    只收含 CJK 的段，避免英文短词（Love/It 等）误命中其他片。"""
    import os as _os
    stem = _os.path.splitext(basename or "")[0]
    out: list[str] = []

    def _add(v: str) -> None:
        v = (v or "").strip()
        if v and v not in out:
            out.append(v)
    try:
        from ..scanner.parse import parse_filename
        t = (parse_filename(basename).get("title") or "").strip()
        # 单字符标题（如 x.mkv）不作查询键：太短容易误命中
        if len(t) >= 2 or any("\u4e00" <= ch <= "\u9fff" for ch in t):
            _add(t)
    except Exception:
        pass
    for seg in stem.split("."):
        seg = seg.strip()
        if len(seg) < 2 or seg.isdigit():
            continue
        if not any("\u4e00" <= ch <= "\u9fff" for ch in seg):
            continue
        _add(seg)
    return out


def parse_path_variants(rel: str) -> list[str]:
    """库内相对路径 → 本地优先查询键：文件名变体 + 父目录各段的中文候选
    （`告白.Confessions.2010/Confessions.2010.mp4` → ['Confessions', '告白']）。"""
    import os as _os
    rel = str(rel or "").replace("\\", "/")
    out: list[str] = []

    def _add(v: str) -> None:
        v = (v or "").strip()
        if v and v not in out:
            out.append(v)
    for v in parse_title_variants(_os.path.basename(rel)):
        _add(v)
    parts = [p for p in rel.split("/")[:-1] if p and p != "."]
    for d in parts:
        for v in parse_title_variants(d):
            _add(v)
    return out


def parse_path_year(rel: str, fallback=None):
    """父目录里的年份优先（`告白.Confessions.2010/` → 2010）；无则用 fallback。"""
    import os as _os
    import re as _re
    rel = str(rel or "").replace("\\", "/")
    for d in reversed([p for p in rel.split("/")[:-1] if p and p != "."]):
        m = _re.search(r"\((\d{4})\)", d) or _re.search(r"[._\- ]((?:19|20)\d{2})(?:[._\- ]|$)", d)
        if m:
            try:
                return int(m.group(1))
            except (TypeError, ValueError):
                pass
    return fallback


def score_row(term: str, year, row: dict) -> float:
    nt = normalize_title(term or "")
    t = normalize_title(row.get("title") or "")
    ot = normalize_title(row.get("original_title") or "")
    s = 0.0
    if nt and t and t == nt:
        s += 45
    elif nt and ot and ot == nt:
        s += 40
    elif nt and t and (nt in t or t in nt):
        s += 18
    elif nt and ot and (nt in ot or ot in nt):
        s += 14
    elif nt and _alt_hit(nt, row.get("alt_titles")):
        # 别名精确（v27）：低于主标题/原标题，但配合年份可过自动绑定阈
        s += 36
    if year and row.get("year"):
        try:
            d = abs(int(row["year"]) - int(year))
        except (TypeError, ValueError):
            d = 99
        s += 12 if d == 0 else (8 if d == 1 else 0)
    if row.get("tmdb_id"):
        s += 6
    if row.get("imdb_id"):
        s += 3
    return s


def _alt_hit(nt: str, alt_titles) -> bool:
    """别名串（换行拼接）里是否有归一后与查询精确相等的项。"""
    for a in str(alt_titles or "").splitlines():
        a = a.strip()
        if a and normalize_title(a) == nt:
            return True
    return False


def search(title: str, year: int | None = None, kind: str = "movie",
           limit: int = 10) -> list[Candidate]:
    term = (title or "").strip()
    if not term:
        return []
    try:
        # 不按年份硬过滤（人工选候选要看到年份不符的结果），年份只参与打分
        rows = store.search_match_index(term, kind=kind, limit=max(limit * 4, 20),
                                        year=None)
    except Exception:
        return []
    out: list[Candidate] = []
    for r in rows:
        s = score_row(term, year, r)
        if s <= 0:
            continue
        tmdb_id = r.get("tmdb_id")
        try:
            tmdb_id = int(tmdb_id) if tmdb_id is not None else None
        except (TypeError, ValueError):
            tmdb_id = None
        if not tmdb_id and r.get("imdb_id"):
            # imdb → tmdb 离线桥（P1.2）：NFO/IMDb 数据集候选也能直接绑定缓存链路
            tmdb_id = store.find_tmdb_by_imdb(str(r["imdb_id"]), kind=kind)
        out.append(Candidate(
            title=r.get("title") or "", original_title=r.get("original_title") or "",
            year=r.get("year"), tmdb_id=tmdb_id, imdb_id=r.get("imdb_id") or "",
            source=r.get("source") or "local", source_id=r.get("source_id") or "",
            score=s, payload=r.get("payload") or {}))
    out.sort(key=lambda c: (-c.score, -(c.year or 0)))
    return out[:max(1, int(limit))]


def best(title: str, year: int | None = None, kind: str = "movie",
         min_score: float = MIN_AUTO_SCORE) -> Candidate | None:
    """高置信自动绑定：必须带 tmdb_id（能继续走详情/缓存链路），
    且年份已知时双方年份差 ≤1（防同名错年误配）。"""
    def _year_ok(c: Candidate) -> bool:
        if year is None or c.year is None:
            return True
        try:
            return abs(int(c.year) - int(year)) <= 1
        except (TypeError, ValueError):
            return True

    for c in search(title, year, kind, limit=3):
        if c.score >= float(min_score) and c.tmdb_id and _year_ok(c):
            return c
    return None
