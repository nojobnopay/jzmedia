"""版本（edition）+ 规格（spec）归一：中文优先。

约定：
- 映射表集中在此文件，改文案只改这里（对标 regions.py 的单源原则）。
- detect_edition() 返回剪辑类版本（导演剪辑/加长/未删减…），无则 ""。
- detect_spec_list() 返回按优先级排序的规格标签列表（杜比视界/HDR/蓝光/…），
  供冲突组做最小后缀消解；detect_spec() 取首个（扫描自动填充用）。
- sanitize_tag() 做文件名安全清洗 + 限长，供 rename/PATCH 共用。
- 编码/音频编码/声道数/压制组永不参与后缀；未知新词不猜英文，直接走编号兜底。
- stacking（单部分多卷）判定也在此：仅数字后缀 cd/dvd/disc/disk/part，
  "Part Two"/续集文字不算，避免把续集合并。
"""
import re

# (匹配正则, 规范中文)，按优先级排序，先命中先赢
EDITION_MAP: list[tuple] = [
    (re.compile(r"导演剪辑版"), "导演剪辑版"),
    (re.compile(r"加长收藏版|加长版"), "加长版"),
    (re.compile(r"未删减版"), "未删减版"),
    (re.compile(r"未分级版"), "未分级版"),
    (re.compile(r"公映版|剧场版|影院版"), "公映版"),
    (re.compile(r"终极版"), "终极版"),
    (re.compile(r"重制版|redux"), "重制版"),
    (re.compile(r"修复版|remaster"), "修复版"),
    (re.compile(r"特别版|special.edition"), "特别版"),
    (re.compile(r"加长|extended"), "加长版"),
    (re.compile(r"uncut"), "未删减版"),
    (re.compile(r"unrated"), "未分级版"),
    (re.compile(r"theatrical"), "公映版"),
    (re.compile(r"ultimate"), "终极版"),
    (re.compile(r"special"), "特别版"),
    # DC 必须带词边界，且排除常见压制组误伤；单独 DC 视为导演剪辑版
    (re.compile(r"director'?s.?cut|导演剪辑"), "导演剪辑版"),
    (re.compile(r"(?<![a-z0-9])dc(?![a-z0-9])"), "导演剪辑版"),
]

# 规格映射：按分组优先级排序（特色画质 > 片源 > 分辨率 > 音频特性）。
# 同组多命中只取首个；分辨率五档互不合并（2160P/1080P/4K/720P/480P）。
SPEC_MAP: list[tuple] = [
    (re.compile(r"(?i)(?:dovi|(?<![a-z0-9])dv(?![a-z0-9]))"), "杜比视界"),
    (re.compile(r"(?i)(?:hdr10|(?<![a-z0-9])hdr(?![a-z0-9]))"), "HDR"),
    (re.compile(r"(?i)(?<![a-z0-9])imax(?![a-z0-9])"), "IMAX"),
    (re.compile(r"(?i)(?<![a-z0-9])3d(?![a-z0-9])"), "3D"),
    (re.compile(r"(?i)blu[\s.\-]?ray|(?<![a-z0-9])bd(?![a-z0-9])|(?<![a-z0-9])uhd(?![a-z0-9])"), "蓝光"),
    (re.compile(r"(?i)(?<![a-z0-9])remux(?![a-z0-9])"), "原盘"),
    (re.compile(r"(?i)web[\s.\-]?dl|itunes|dsnp|amzn|netflix|hdtv"), "网播版"),
    (re.compile(r"(?i)(?<![a-z0-9])2160p(?![a-z0-9])"), "2160P"),
    (re.compile(r"(?i)(?<![a-z0-9])1080p(?![a-z0-9])"), "1080P"),
    (re.compile(r"(?i)(?<![a-z0-9])4k(?![a-z0-9])"), "4K"),
    (re.compile(r"(?i)(?<![a-z0-9])720p(?![a-z0-9])"), "720P"),
    (re.compile(r"(?i)(?<![a-z0-9])480p(?![a-z0-9])"), "480P"),
    (re.compile(r"(?i)(?<![a-z0-9])atmos(?![a-z0-9])"), "全景声"),
]

_ILLEGAL = re.compile(r'[\\/:*?"<>|]')

# 非版本词：来源/编码/压制组，命中这些不产生 edition
_NON_EDITION = re.compile(
    r"(?i)^(blu[\s.\-]?ray|web[\s.\-]?dl|hdtv|dvdrip|bdrip|remux|"
    r"x26[45]|xvid|hevc|avc|hdr\d*|dolby|dovi|atmos|truehd|dts|ddp?[\s.\-]?5[\s.\-]?1|"
    r"2160p|1080p|720p|480p|4k|2audio|3audio|4audio|5audios|10bit|8bit|"
    r"dreamhd|batweb|ctrlhd|parkhd|sonyhd|mnhd|frds|wiki|chd|beast|ltt|mgB|etrg|"
    r"itunes|dsnp|netflix|amzn|max)$"
)

# 编号兜底 token：文件名已有 -版本N 时识别为 spec（避免改名回环），入库后可手工改备注
_NUMBERED_RE = re.compile(r"版本([2-9][0-9]?)$")


def sanitize_tag(s: str, limit: int = 20) -> str:
    s = _ILLEGAL.sub("", str(s or ""))
    s = re.sub(r"\s+", " ", s).strip().strip(".-_ ")
    return s[:limit]


# 兼容旧名
sanitize_edition = sanitize_tag
sanitize_spec = sanitize_tag


def detect_edition(filename: str, guessit_edition=None) -> str:
    """filename 为完整文件名（含扩展名亦可）；guessit_edition 为 guessit 的 edition 字段。"""
    hay = str(filename or "")
    if guessit_edition:
        if isinstance(guessit_edition, list):
            hay += " " + " ".join(str(x) for x in guessit_edition)
        else:
            hay += " " + str(guessit_edition)
    low = hay.lower()
    for pat, cn in EDITION_MAP:
        if pat.search(hay) or pat.search(low):
            return cn
    # 回退：guessit 给出的英文原词（清洗后），非版本词则丢弃
    if guessit_edition:
        cand = guessit_edition[0] if isinstance(guessit_edition, list) else guessit_edition
        cand = sanitize_tag(str(cand))
        if cand and not _NON_EDITION.match(cand.lower().replace(" ", "").replace(".", "")):
            # 中文原词直接保留，英文原词首字母大写保留原样
            return cand
    return ""


def detect_spec_list(filename: str) -> list[str]:
    """按 SPEC_MAP 优先级返回命中的规格标签（去重保序）；编号 token 版本N 也识别。"""
    hay = str(filename or "")
    out: list[str] = []
    for pat, cn in SPEC_MAP:
        if pat.search(hay) and cn not in out:
            out.append(cn)
    stem = hay.rsplit(".", 1)[0] if "." in hay else hay
    tail = stem.split("-")[-1].split(" ")[-1].strip()
    m = _NUMBERED_RE.search(tail)
    if m and f"版本{m.group(1)}" not in out:
        out.append(f"版本{m.group(1)}")
    return out


def detect_spec(filename: str) -> str:
    """扫描自动填充用：取首个规格标签（最高优先级），无则 ''。"""
    labels = detect_spec_list(filename)
    return labels[0] if labels else ""


# stacking：文件名 stem 末尾的 -cd1/-dvd1/-disc2/-part1（数字限定，续集文字不算）
_STACK_RE = re.compile(
    r"(?i)[ ._\-]+(cd|dvd|disc|disk|part)[ ._\-]*([1-9][0-9]?)$")


def split_stack(stem: str) -> tuple[str, str]:
    """返回 (去后缀基名, stack标签如 'part1'，无则 '')。"""
    m = _STACK_RE.search(stem or "")
    if not m:
        return stem, ""
    num = m.group(2)
    return stem[:m.start()], f"part{num}"


# 冲突 kind 启发式：去掉版本/规格/分卷/年份/分隔符后主干一致 → 真变体，否则疑似错配
_CORE_NOISE = re.compile(
    r"(?i)blu[\s.\-]?ray|remux|web[\s.\-]?dl|itunes|dsnp|amzn|netflix|hdtv|"
    r"dovi|\bdv\b|hdr(?:10)?|imax|3d|uhd|\bbd\b|2160p|1080p|720p|480p|4k|atmos|"
    r"x26[45]|h[\s.\-]?26[45]|xvid|hevc|avc|10bit|8bit|truehd|hd[\s.\-]?ma|dts|ddp?|ac3|"
    r"[0-9]+audio|[578]\.[01]|(?<![0-9])[57]1(?![0-9])|"
    r"(?<=[\s.\-_])(?:USA|UK|JPN?|KR|CN|TW|HK|EU|US)(?![a-zA-Z0-9])|"
    r"director'?s.?cut|\bdc\b|extended|uncut|unrated|theatrical|ultimate|special|"
    r"remaster|redux|导演剪辑版|加长收藏版|加长版|未删减版|未分级版|公映版|剧场版|"
    r"影院版|终极版|重制版|修复版|特别版|版本[0-9]+|"
    r"蓝光|原盘|网播版|杜比视界|全景声|"
    r"\bcd\b|\bdvd\b|disc|disk|\bpart\b|(19|20)[0-9]{2}")

# 压制组（库内实测 30+，片名不会包含这些词，主干比对时剔除）
_RELEASE_RE = re.compile(
    r"(?i)(?<![a-z0-9])(?:mn[\s.\-_]?hd|frds|dreamhd|wiki|alt|parkhd|quickio|"
    r"pandaqt|chd|sonyhd|beast|minihd|depth|ctrlhd|ssdsse|rarbg|mgb|etrg|3li|"
    r"ltt|nohd|siluhd|publichd|mysilu|momohd|gpthd|batweb|hdsweb?|diy|hdchina|"
    r"hdwing|bbqddq)(?![a-z0-9])")


def core_of(basename: str) -> str:
    stem = basename.rsplit(".", 1)[0] if "." in (basename or "") else (basename or "")
    s = _STACK_RE.sub("", stem)
    s = re.sub(r"\S+@\S+", "", s)  # 10017@BBQDDQ.COM 类水印
    s = _RELEASE_RE.sub("", s)
    s = _CORE_NOISE.sub("", s)
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", s.lower())
