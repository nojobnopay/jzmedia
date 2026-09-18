"""本地离线匹配（E 阶段）：基于 match_index（TMDB 缓存/NFO/外部候选的统一索引）。

- 中文部分词召回靠 LIKE（FTS unicode61 对 CJK 分词不友好），评分在 Python 侧做归一化比对。
- 高置信阈值默认 45（标题精确 + 年份命中），仅供 scan 自动绑定；低分候选给人选。
"""
from .. import store
from ..scanner.parse import normalize_title
from .base import Candidate

__all__ = ['search', 'best', 'score_row', 'MIN_AUTO_SCORE']

MIN_AUTO_SCORE = 45.0


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
