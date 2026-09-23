"""TV 剧集解析（T1 扫描器 v2）：纯函数，不触网/不读写 DB。

背景（NAS 实测）：guessit 对中文发布名、绝对集号、多集文件覆盖差——
`Show.2006.EP01` 会把年份当季号、`0001.flv` 把绝对集号当 Season 0、
`S01E001-E006` 整段丢失。本模块提供确定性规则阶梯，扫描器**优先**用它，
guessit 仅在无结果时兜底（剧名/年份仍可来自目录名）。

规则顺序（先到先得）：
  1. SxxEyy（含 S01E01-E02 / S04E73E74 / S01.E01）
  2. NxNN
  3. 第X集/话/話
  4. E01 / EP01
  5. 日期 YYYY-MM-DD（一天一集；仅返回 date，由调用方编号）
  6. 特典关键词 SP/OVA/OAD/特别篇/特典/番外（可带编号）
  7. 裸数字（绝对集号或季内集号；排除年份与分辨率）
"""
import re

__all__ = ['parse_episode', 'season_from_dir', 'parse_show_dir',
           'is_tv_special_dir', 'is_tv_movie_dir', 'clean_show_title',
           'map_absolute', 'TV_SPECIAL_DIR_TOKENS', 'TV_MOVIE_DIR_TOKENS']


def map_absolute(absolute: int, season_counts) -> tuple[int, int] | None:
    """绝对集号 → (season, episode)：按正季集数累计偏移（TMDB aired order）。
    `season_counts` = [(season, episode_count)] 升序、仅正季；越界返回 None。"""
    try:
        a = int(absolute)
    except (TypeError, ValueError):
        return None
    if a <= 0:
        return None
    remain = a
    for season, count in season_counts or ():
        try:
            season, count = int(season), int(count)
        except (TypeError, ValueError):
            continue
        if count <= 0:
            continue
        if remain <= count:
            return season, remain
        remain -= count
    return None

# 纯数字 token 排除集：年份段 + 常见分辨率（避免 `1080p` 之外的裸数字误判）
_RESOLUTIONS = {240, 360, 480, 576, 720, 1080, 1440, 2160, 4320}

# --- 规则 1/2：季集号 ---
_EP_RANGE = re.compile(
    r"(?i)(?<![a-z0-9])s(\d{1,3})[\s._-]*e(?:p)?(\d{1,4})"
    r"[\s._-]*(?:-|~|至)[\s._-]*e?(?:p)?(\d{1,4})(?![\dpi])")
_EP_DOUBLE = re.compile(
    r"(?i)(?<![a-z0-9])s(\d{1,3})[\s._-]*e(?:p)?(\d{1,4})e(?:p)?(\d{1,4})(?![\dpi])")
_EP_SINGLE = re.compile(
    r"(?i)(?<![a-z0-9])s(\d{1,3})[\s._-]*e(?:p)?(\d{1,4})(?![\dpi])")
_XNN = re.compile(r"(?<!\d)(\d{1,2})x(\d{1,3})(?!\d)")

# --- 规则 3/4：中文集号 / E 前缀 ---
_CN_EP = re.compile(r"第\s*(\d{1,4})\s*[集话話]")
_EP_PREFIX_RANGE = re.compile(
    r"(?i)(?:^|[\s._\[\(【-])e(?:p)?(\d{1,4})[\s._-]*(?:-|~|至)[\s._-]*e?(?:p)?(\d{1,4})(?![\dpi])")
_EP_PREFIX_DOUBLE = re.compile(
    r"(?i)(?:^|[\s._\[\(【-])e(?:p)?(\d{1,4})e(?:p)?(\d{1,4})(?![\dpi])")
_EP_PREFIX = re.compile(r"(?i)(?:^|[\s._\[\(【-])e(?:p)?[\s._-]?(\d{1,4})(?![\dpi])")

# --- 规则 5：日期（一天一集） ---
_DATE = re.compile(r"(?<!\d)((?:19|20)\d{2})[-._](\d{1,2})[-._](\d{1,2})(?!\d)")

# --- 规则 6：特典 ---
_BOUNDARY = r"(?:^|[\s._\[\(【\-]|[\u4e00-\u9fff])"   # 行首/分隔符/中文紧贴（`正义SP`）
_SPECIAL_NUM = re.compile(
    r"(?i)" + _BOUNDARY + r"(?:sp|ova|oad|ncop|nced|特别篇|特典|番外|剧场版|劇場版)"
    r"[\s._-]*(\d{1,3})(?!\d)")
_SPECIAL_WORD = re.compile(
    r"(?i)" + _BOUNDARY + r"(?:sp|ova|oad|ncop|nced|special)(?=$|[\s._\]\)】\-]|\d)"
    r"|特别篇|特典|番外")

_TOKEN_SPLIT = re.compile(r"[\s._\-\[\]\(\)【】{}]+")
_TRAIL_NUM = re.compile(r"(?<=[^\d\s])(0*\d{1,4})$")
# 发布规格 token：裸数字是分辨率/规格时跳过（`Show.1080.BluRay`），
# 但后面跟的是正片名而非规格时按集号处理（`240.巨大的希望…mkv`）。
_RELEASE_TAG = {"bluray", "blu", "bdrip", "brrip", "web", "dl", "webrip", "hdrip",
                "hdtv", "dvdrip", "remux", "x264", "x265", "h264", "h265", "hevc",
                "avc", "aac", "ac3", "eac3", "dts", "dtshd", "truehd", "flac",
                "10bit", "8bit", "4k", "uhd", "hd", "sd"}

# --- 季目录 / 剧目录 ---
_SEASON_DIR_RE = re.compile(r"(?i)^(?:season|s)[\s._-]*0*(\d{1,3})(?:[\s._-].*)?$")
_SEASON_CN_RE = re.compile(r"^第\s*(\d{1,3})\s*季")
_SPECIAL_DIR_RE = re.compile(r"(?i)^(?:specials?|season[\s._-]*0+|特别篇|特典|番外)$")
_HINT_RE = re.compile(r"\{\s*(tmdb|tvdb|imdb)\s*[-:_]?\s*([A-Za-z0-9]+)\s*\}", re.I)
_PAREN_YEAR_RE = re.compile(r"\(((?:19|20)\d{2})\)")
_TAIL_YEAR_RE = re.compile(r"(?:^|[\s._-])((?:19|20)\d{2})$")

TV_SPECIAL_DIR_TOKENS = {"oad", "ova", "sp", "specials", "ncop", "nced", "pv"}
TV_SPECIAL_DIR_CJK = ("特别篇", "特典", "番外")
TV_MOVIE_DIR_TOKENS = {"movies", "movie", "films", "film"}
TV_MOVIE_DIR_CJK = ("剧场版", "劇場版", "真人版", "电影", "電影")

_RELEASE_RE = re.compile(
    r"(?i)[\s._-]+(?:1080p?|2160p?|4k|uhd|bluray|blu-?ray|bdrip|brrip|web-?dl|webrip|"
    r"hdrip|hdtv|dvdrip|remux|x26[45]|h\.?26[45]|hevc|avc|aac|ac3|eac3|dts(?:-hd)?|"
    r"truehd|flac|10bit|8bit)(?=$|[\s._-])")
_CN_JUNK_RE = re.compile(
    r"[\s._-]+(?:\d+\s*集全|\d+\s*季全|全集|完结|国语|粤语|双语|中字|简繁|繁中|简体|"
    r"高清|收藏版|珍藏版|合集|部全)(?=$|[\s._-])")
_SEP_RE = re.compile(r"[._]+")


def _valid_season(v) -> int | None:
    try:
        v = int(v)
    except (TypeError, ValueError):
        return None
    if 0 <= v <= 99:
        return v
    return None


def _valid_episode(v) -> int | None:
    try:
        v = int(v)
    except (TypeError, ValueError):
        return None
    if 0 < v <= 9999:
        return v
    return None


def _range_end(start, end) -> int:
    """区间集号合法性：单调且跨度合理（防 `S01E01-1080` 类误判）。"""
    a, b = _valid_episode(start), _valid_episode(end)
    if not a or not b or b <= a or (b - a) > 200:
        return 0
    return b


def _bare_number(stem: str) -> int | None:
    """裸数字：① 整名纯数字（`1080.flv`）② 纯数字 token（排除年份/规格）
    ③ 标题紧贴数字（`黑街01`）。"""
    s = stem.strip()
    if s.isdigit():
        v = int(s)
        if 0 < v <= 9999:
            return v
    toks = _TOKEN_SPLIT.split(s)
    for i, tok in enumerate(toks):
        if not tok.isdigit():
            continue
        v = int(tok)
        if v <= 0 or v > 9999 or 1900 <= v <= 2099:
            continue
        if not tok.startswith("0") and v in _RESOLUTIONS:
            nxt = (toks[i + 1] if i + 1 < len(toks) else "").strip().lower()
            if (not nxt or nxt in _RELEASE_TAG or nxt.isdigit()
                    or re.match(r"^\d{3,4}[pi]$", nxt)):
                continue
        return v
    m = _TRAIL_NUM.search(s)
    if m:
        digits = m.group(1)
        v = int(digits)
        if 0 < v <= 9999 and not (1900 <= v <= 2099):
            if digits.startswith("0") or v not in _RESOLUTIONS:
                return v
    return None


def parse_episode(name: str) -> dict:
    """解析文件名（可含扩展名）。返回 dict：

    - season/episode/episode_end：集号（None=未识别；episode_end=0 表示单集）
    - special：特典关键词命中（调用方决定是否落 Season 0）
    - absolute：来自裸数字规则（无季号，绝对集号候选）
    - date：`YYYY-MM-DD` 日期集（调用方按顺序编号）
    - source：命中规则名（测试/排查用）
    """
    stem = (name or "").rsplit("/", 1)[-1]
    if "." in stem:
        stem = stem.rsplit(".", 1)[0]
    out = {"season": None, "episode": None, "episode_end": 0,
           "special": False, "absolute": False, "date": "", "source": ""}

    for pat in (_EP_RANGE, _EP_DOUBLE):
        m = pat.search(stem)
        if not m:
            continue
        season, episode = _valid_season(m.group(1)), _valid_episode(m.group(2))
        if season is not None and episode is not None:
            out.update(season=season, episode=episode,
                       episode_end=_range_end(m.group(2), m.group(3)),
                       source="sxxeyy")
            return out
    m = _EP_SINGLE.search(stem)
    if m:
        season, episode = _valid_season(m.group(1)), _valid_episode(m.group(2))
        if season is not None and episode is not None:
            out.update(season=season, episode=episode, source="sxxeyy")
            return out
    m = _XNN.search(stem)
    if m:
        season, episode = _valid_season(m.group(1)), _valid_episode(m.group(2))
        if season is not None and episode is not None:
            out.update(season=season, episode=episode, source="nxnn")
            return out
    m = _CN_EP.search(stem)
    if m and _valid_episode(m.group(1)) is not None:
        out.update(episode=_valid_episode(m.group(1)), source="cn")
        return out
    for pat in (_EP_PREFIX_RANGE, _EP_PREFIX_DOUBLE):
        m = pat.search(stem)
        if not m or _valid_episode(m.group(1)) is None:
            continue
        out.update(episode=_valid_episode(m.group(1)),
                   episode_end=_range_end(m.group(1), m.group(2)), source="eprefix")
        return out
    m = _EP_PREFIX.search(stem)
    if m and _valid_episode(m.group(1)) is not None:
        out.update(episode=_valid_episode(m.group(1)), source="eprefix")
        return out
    m = _DATE.search(stem)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            out.update(date=f"{y:04d}-{mo:02d}-{d:02d}", source="date")
            return out
    if _SPECIAL_WORD.search(stem):
        out["special"] = True
        m = _SPECIAL_NUM.search(stem)
        if m and _valid_episode(m.group(1)) is not None:
            out.update(episode=_valid_episode(m.group(1)), source="special")
        else:
            out["source"] = "special"
        return out
    v = _bare_number(stem)
    if v:
        out.update(episode=v, absolute=True, source="bare")
    return out


def season_from_dir(name: str) -> int | None:
    """季目录名 → 季号：`Season 01` / `S02 无限列车篇TV版` / `第3季` / `Specials`。"""
    s = str(name or "").strip()
    if not s:
        return None
    m = _SEASON_DIR_RE.match(s)
    if m:
        return _valid_season(m.group(1))
    m = _SEASON_CN_RE.match(s)
    if m:
        return _valid_season(m.group(1))
    if _SPECIAL_DIR_RE.match(s):
        return 0
    return None


def clean_show_title(name: str) -> str:
    """剧目录名清洗：分隔符归一 + 去发布规格/中文合集后缀，供展示与 TMDB 检索。"""
    s = _SEP_RE.sub(" ", str(name or ""))
    s = _RELEASE_RE.sub(" ", s)
    s = _CN_JUNK_RE.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip(" -_")


def parse_show_dir(name: str) -> dict:
    """剧目录名 → {title, year, hints}；hints 支持 `{tmdb-123}`/`{tvdb-…}`/`{imdb-tt…}`。"""
    raw = str(name or "").strip()
    hints: dict[str, str] = {}
    for m in _HINT_RE.finditer(raw):
        hints[m.group(1).lower()] = m.group(2)
    s = _HINT_RE.sub(" ", raw)
    year = None
    m = _PAREN_YEAR_RE.search(s)
    if m:
        year = int(m.group(1))
        s = s[:m.start()] + " " + s[m.end():]
    else:
        m = _TAIL_YEAR_RE.search(s)
        if m:
            year = int(m.group(1))
            s = s[:m.start()]
    return {"title": clean_show_title(s), "year": year, "hints": hints}


def is_tv_special_dir(name: str) -> bool:
    """特典/OVA 目录：其下文件仍属本剧（Season 0），不做子剧拆分。"""
    s = str(name or "")
    low = s.lower()
    if any(t in low for t in TV_SPECIAL_DIR_TOKENS):
        toks = _TOKEN_SPLIT.split(low)
        if any(t in TV_SPECIAL_DIR_TOKENS for t in toks):
            return True
    return any(t in s for t in TV_SPECIAL_DIR_CJK)


def is_tv_movie_dir(name: str) -> bool:
    """剧库内的电影目录（剧场版/真人版/Movies）：非正片，扫描跳过。"""
    s = str(name or "")
    low = s.lower()
    toks = _TOKEN_SPLIT.split(low)
    if any(t in TV_MOVIE_DIR_TOKENS for t in toks):
        return True
    return any(t in s for t in TV_MOVIE_DIR_CJK)
