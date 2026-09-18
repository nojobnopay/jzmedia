"""同目录 NFO 导入（离线元数据来源之一）：Kodi `<movie>` 格式。

扫描时若 TMDB 不可用，可先用 NFO 里的标题/年份/`<uniqueid>` 建行或匹配缓存，
保证断网/无 Token 场景仍能恢复元数据与匹配。
"""
import os
import xml.etree.ElementTree as ET

from .base import Candidate

__all__ = ['candidates_for', 'read_nfo']

_NFO_NAMES = ("movie.nfo",)


def _read(path: str) -> Candidate | None:
    try:
        root = ET.parse(path).getroot()
    except Exception:
        return None
    title = (root.findtext("title") or "").strip()
    original = (root.findtext("originaltitle") or "").strip()
    year = None
    y = (root.findtext("year") or "").strip()
    if y[:4].isdigit():
        year = int(y[:4])
    tmdb_id = None
    imdb_id = ""
    for u in root.findall("uniqueid"):
        t = (u.get("type") or "").strip().lower()
        v = (u.text or "").strip()
        if t == "tmdb" and v.isdigit():
            tmdb_id = int(v)
        elif t == "imdb":
            imdb_id = v
    if not title and not tmdb_id:
        return None
    return Candidate(title=title, original_title=original, year=year,
                     tmdb_id=tmdb_id, imdb_id=imdb_id, source="nfo",
                     source_id=os.path.basename(path), score=60.0)


def read_nfo(path: str) -> Candidate | None:
    return _read(path) if path and os.path.isfile(path) else None


def candidates_for(abs_path: str) -> Candidate | None:
    """优先 `<stem>.nfo`，其次 `movie.nfo`（与写盘收敛规则一致）。"""
    if not abs_path:
        return None
    d = os.path.dirname(abs_path)
    stem = os.path.splitext(os.path.basename(abs_path))[0]
    names = ([stem + ".nfo"] if stem else []) + list(_NFO_NAMES)
    for name in names:
        cand = _read(os.path.join(d, name))
        if cand is not None:
            return cand
    return None
