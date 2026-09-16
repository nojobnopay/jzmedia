"""scanner.parse（自 app/scanner.py 拆分，评审 B9/R03-Q1；对外经 app.scanner 门面使用）。"""
import os
import re
import unicodedata
from guessit import guessit
from ..editions import detect_edition
from ..editions import detect_spec
from ..editions import split_stack
from ..log import get_logger
logger = get_logger("scanner.parse")
__all__ = ['_ROMAN', 'normalize_title', 'short_candidates', 'parse_filename']

_ROMAN = {"II": "2", "III": "3", "IV": "4", "VI": "6",
           "VII": "7", "VIII": "8", "IX": "9"}


def normalize_title(s: str) -> str:
    """NFKC：全角→半角、Ⅱ→II等兼容字符归一；独立多字母罗马数字→阿拉伯数字；压空白。
    注：单字母V/X歧义大（如V字仇杀队/Project X），故意不转。"""
    s = unicodedata.normalize("NFKC", s or "")
    s = re.sub(r"\b(II|III|IV|VI|VII|VIII|IX)\b",
               lambda m: _ROMAN[m.group(1)], s)
    return re.sub(r"\s+", " ", s).strip()


def short_candidates(title: str) -> list[str]:
    """长标题fallback：冒号后段、去剧场版前缀、去加长版后缀，逐个重试。"""
    cands = [title]
    for sep in ("：", ":"):
        if sep in title:
            cands.append(title.split(sep)[-1].strip())
    m = re.search(r"剧场版\s*\d*\s*[:：]?\s*(.+)$", title)
    if m:
        cands.append(m.group(1).strip())
    tail = re.sub(r"\s*(加长版|导演剪辑版|加长收藏版)\s*$", "", title).strip()
    if tail != title:
        cands.append(tail)
    seen, out = set(), []
    for x in cands:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


def parse_filename(name: str) -> dict:
    try:
        g = guessit(name)
    except Exception:
        g = {}
    title = g.get("title") or os.path.splitext(name)[0]
    if isinstance(title, list):
        title = title[0]
    stem = os.path.splitext(name)[0]
    _, stack = split_stack(stem)
    return {"title": str(title), "year": g.get("year"),
            "type": g.get("type", "movie"),
            "edition": detect_edition(name, g.get("edition")),
            "spec": detect_spec(name),
            "stack": stack}

