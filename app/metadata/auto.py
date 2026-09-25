"""外部候选自动绑定门（P2.4）：本地/NFO 高置信直绑，无 key API 源从严。

规则（与电影/TV 既有相似门同语义，防错配）：
- local/nfo：score ≥ 45 且年份 ±1（与 `metadata.local.best` 一致）；
- 外源（wikidata/tvmaze/bgm/douban）：归一标题相似 ≥0.7 且年份 ±1 → 自动，
  年份不符或相似中档 → `needs_review=1`，太低 → 只进手动候选不绑定；
- 双方任一年份缺失时外源一律标待确认（保守）。
"""
from ..log import get_logger
from .base import Candidate

logger = get_logger("metadata.auto")

SIM_THRESHOLD = 0.7
MIN_LOCAL_SCORE = 45.0
LOCAL_SOURCES = {"library", "local", "nfo", "tmdb"}
EXTERNAL_SOURCES = {"wikidata", "tvmaze", "bgm", "douban"}


def _year_ok(year, cand_year, tolerance: int = 1) -> bool:
    if year is None or cand_year is None:
        return True
    try:
        return abs(int(cand_year) - int(year)) <= tolerance
    except (TypeError, ValueError):
        return True


def _sim(query: str, cand: Candidate) -> float:
    try:
        from ..scanner.match import title_similar
    except Exception:
        return 1.0
    best = max(title_similar(query, cand.title),
               title_similar(query, cand.original_title))
    for a in (cand.payload or {}).get("detail", {}).get("aliases") or []:
        best = max(best, title_similar(query, str(a)))
    return best


def pick_auto(cands: list[Candidate], query: str, year,
              kind: str = "movie") -> tuple[Candidate | None, int, str]:
    """候选列表 → (命中的候选|None, needs_review, 说明)。按列表顺序取首个可用者。"""
    for c in cands or []:
        if not isinstance(c, Candidate):
            continue
        src = str(c.source or "")
        if src in ("library", "local", "nfo"):
            if float(c.score or 0) >= MIN_LOCAL_SCORE and _year_ok(year, c.year):
                return c, 0, "local"
            continue
        if src == "tmdb":
            return c, 0, "tmdb"
        if src not in EXTERNAL_SOURCES:
            continue
        s = _sim(query, c)
        if s < SIM_THRESHOLD:
            continue
        if year is None or c.year is None:
            return c, 1, "year_unknown"
        if _year_ok(year, c.year):
            return c, 0, "external"
        return c, 1, "year_mismatch"
    return None, 0, ""


__all__ = ['pick_auto', 'SIM_THRESHOLD', 'MIN_LOCAL_SCORE',
           'LOCAL_SOURCES', 'EXTERNAL_SOURCES']
