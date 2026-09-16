"""scanner.classify（自 app/scanner.py 拆分，评审 B9/R03-Q1；对外经 app.scanner 门面使用）。"""
import os
import re
from ..config import settings
from ..log import get_logger
logger = get_logger("scanner.classify")
__all__ = ['VIDEO_EXTS', 'SUBTITLE_EXTS', '_SKIP_DIR_NAMES', 'scan_skip_dirs', 'SIDECAR_TEXT_EXTS', '_sidecar_sub_dirs', 'SIDECAR_SUB_DIRS', '_SAMPLE_TOKENS', '_SAMPLE_RE', 'strip_kind_affix', 'extra_kind', 'is_sample', '_parent_has_feature', 'is_extra', 'is_sidecar', 'is_feature_video', '_stem_matches', 'sidecar_subtitles', '_EXTRAS_RE', 'EXTRAS_DIR_NAMES', '_GENERIC_DIR_NAMES', 'KIND_BY_DIR', '_KIND_WORDS', '_STRIP_LEAD_RES', '_STRIP_TRAIL_RE']

VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".ts", ".m2ts", ".mov", ".wmv", ".flv", ".webm"}


SUBTITLE_EXTS = {".srt", ".ass", ".ssa", ".sub", ".idx", ".sup"}


_SKIP_DIR_NAMES = {"#recycle", "@eaDir", "$RECYCLE.BIN", "lost+found",
                   ".Trash", ".Trash-1000"}


def scan_skip_dirs() -> set[str]:
    """剪枝目录名集合：内置 + env SCAN_SKIP_DIRS（逗号分隔）。"""
    extra = (os.getenv("SCAN_SKIP_DIRS") or "").strip()
    if not extra:
        return set(_SKIP_DIR_NAMES)
    return set(_SKIP_DIR_NAMES) | {x.strip() for x in extra.split(",") if x.strip()}


SIDECAR_TEXT_EXTS = {".srt": "srt", ".ass": "ass", ".ssa": "ssa"}


def _sidecar_sub_dirs() -> tuple:
    """外挂字幕查找目录（评审 B8/R13-Q5）：默认同目录 + subs/Subs/字幕，可用
    env SIDECAR_SUB_DIRS 覆盖（逗号分隔）。"""
    raw = (os.getenv("SIDECAR_SUB_DIRS") or "").strip()
    if not raw:
        return ("subs", "Subs", "字幕")
    return tuple(x.strip() for x in raw.split(",") if x.strip())


SIDECAR_SUB_DIRS = _sidecar_sub_dirs()


_SAMPLE_TOKENS = (
    r"\d{3,4}[pi]|4k|uhd|hdr\d*|dovi|dv|10bit|8bit|remux|"
    r"web[.\-]?dl|webrip|bluray|bdrip|brrip|hdrip|hdtv|"
    r"x26[45]|h[.\-]?26[45]|hevc|avc|aac|ac3|eac3|dts(?:[.\-]?hd)?|truehd|flac|atmos|proper|repack"
)


_SAMPLE_RE = re.compile(
    r"(?i)(?:(?:^|[.\-_])samples?(?:[.\-_](?:%s)){0,3}|\(samples?\))$" % _SAMPLE_TOKENS)


def strip_kind_affix(title: str) -> str:
    """剥花絮种类词前后缀（归属 fallback 用）：剥空/无 affix 返回原串，可多轮（如 预告-特辑-X）。"""
    s = (title or "").strip()
    if not s:
        return s
    for _ in range(3):
        changed = False
        for pat in _STRIP_LEAD_RES:
            m = pat.match(s)
            if m and (m.group(2) or "").strip():
                s = m.group(2).strip()
                changed = True
                break
        if not changed:
            m = _STRIP_TRAIL_RE.match(s)
            if m and (m.group(1) or "").strip():
                s = m.group(1).strip()
                changed = True
        if not changed:
            break
    return s


def extra_kind(rel_path: str) -> str:
    """花絮归属类型：先看所处花絮子目录（由内向外），再看文件名关键词，默认 extra。"""
    parts = (rel_path or "").replace("\\", "/").split("/")
    for p in reversed(parts[:-1]):
        k = KIND_BY_DIR.get((p or "").strip().lower())
        if k:
            return k
    if is_sample(os.path.basename(rel_path or "")):
        return "sample"
    stem = os.path.splitext(parts[-1] if parts else "")[0]
    for pat, k in _KIND_WORDS:
        if pat.search(stem):
            return k
    return "extra"


def is_sample(basename: str) -> bool:
    return bool(_SAMPLE_RE.search(os.path.splitext(basename)[0]))


def _parent_has_feature(abs_dir: str) -> bool:
    """父目录是否含正片文件（二级规则：scenes/other/shorts 类通用名目录的判定用）。
    只看文件名级特征（不递归调 is_extra，避免循环）。"""
    try:
        names = os.listdir(abs_dir)
    except OSError:
        return False
    for n in names:
        full = os.path.join(abs_dir, n)
        if not os.path.isfile(full):
            continue
        if os.path.splitext(n)[1].lower() not in VIDEO_EXTS:
            continue
        if is_sample(n):
            continue
        stem = os.path.splitext(n)[0]
        if _EXTRAS_RE.search(stem):
            continue
        return True
    return False


def is_extra(rel_path: str) -> bool:
    """rel_path 为 MEDIA_ROOT 下相对路径：文件名命中花絮词，或所处花絮子目录。
    scenes/other/shorts 类通用目录名仅当其父目录含正片文件（即“某部片的子目录”
    语义，如 Movie/Shorts/）时生效；顶层 Shorts/ 合集目录不受影响。"""
    parts = (rel_path or "").replace("\\", "/").split("/")
    for i, p in enumerate(parts[:-1]):
        low = (p or "").strip().lower()
        if low in _GENERIC_DIR_NAMES:
            above = os.path.join(settings.media_root, *parts[:i]) \
                if i else settings.media_root
            if _parent_has_feature(above):
                return True
        elif low in EXTRAS_DIR_NAMES:
            return True
    return bool(_EXTRAS_RE.search(os.path.splitext(parts[-1] if parts else "")[0]))


def is_sidecar(rel_path: str) -> bool:
    """扫描应跳过的非正片：花絮或样片。"""
    base = os.path.basename(rel_path or "")
    return is_sample(base) or is_extra(rel_path)


def is_feature_video(rel_path: str) -> bool:
    return (os.path.splitext(rel_path)[1].lower() in VIDEO_EXTS
            and not is_sidecar(rel_path))


def _stem_matches(video_stem: str, name_stem: str) -> bool:
    """外挂字幕命名匹配：与正片 stem 相同，或以 正片stem + [-._ 空格] 开头。"""
    if name_stem == video_stem:
        return True
    return any(name_stem.startswith(video_stem + sep) for sep in ("-", ".", "_", " "))


def sidecar_subtitles(abs_video: str) -> list[dict]:
    """正片同名外挂字幕清单（播放器用；只读磁盘，不入库）。
    同目录（含一层 subs/Subs/字幕 子目录）内 stem 匹配的：
    - 文本 .srt/.ass/.ssa → codec srt|ass|ssa, image=0（客户端渲染）
    - .sup → codec pgs, image=1（客户端渲染）
    - .sub/.idx 成对 → codec vobsub, image=1（仅烧录；同 stem 只列一条，优先 .sub）
    返回 [{rel, name, codec, image, suffix}]，suffix 为文件名中 stem 之后的部分（供语言推断）。"""
    video_stem = os.path.splitext(os.path.basename(abs_video))[0]
    src_dir = os.path.dirname(abs_video)
    dirs = [src_dir] + [os.path.join(src_dir, d) for d in SIDECAR_SUB_DIRS]
    found: dict[tuple, dict] = {}
    for d in dirs:
        try:
            names = sorted(os.listdir(d))
        except OSError:
            continue
        for n in names:
            full = os.path.join(d, n)
            if not os.path.isfile(full):
                continue
            stem, ex = os.path.splitext(n)
            ex = ex.lower()
            if not _stem_matches(video_stem, stem):
                continue
            if ex in SIDECAR_TEXT_EXTS:
                codec, image = SIDECAR_TEXT_EXTS[ex], 0
            elif ex == ".sup":
                codec, image = "pgs", 1
            elif ex in (".sub", ".idx"):
                codec, image = "vobsub", 1
            else:
                continue
            suffix = stem[len(video_stem):].lstrip("-._ ")
            key = (d, stem) if codec == "vobsub" else (d, n)
            item = {"rel": os.path.relpath(full, settings.media_root), "name": n,
                    "codec": codec, "image": image, "suffix": suffix, "path": full}
            # VobSub 成对（.sub/.idx 同 stem）：优先 .sub，避免重复列轨
            if key in found and not (codec == "vobsub" and ex == ".sub"):
                continue
            found[key] = item
    out = sorted(found.values(), key=lambda x: (os.path.dirname(x["rel"]), x["name"]))
    for it in out:
        it.pop("path", None)
    return out


_EXTRAS_RE = re.compile(
    r"(?i)(behind[ ._\-]*the[ ._\-]*scenes|featurette|deleted[ ._\-]*scenes?|"
    r"bloopers?|interviews?|trailers?|teasers?|"
    r"making[\s.\-_]*of|メイキング|"
    r"[ ._\-]+shorts?$|[ ._\-]+scene$|"
    r"花絮|预告|特辑|彩蛋|幕后)")


EXTRAS_DIR_NAMES = {"extras", "extra", "featurettes", "featurette",
                    "behind the scenes", "deleted scenes", "trailers", "trailer",
                    "interviews", "interview", "samples", "sample",
                    "花絮", "预告", "特辑"}


_GENERIC_DIR_NAMES = {"scenes", "other", "shorts"}


KIND_BY_DIR = {"trailers": "trailer", "trailer": "trailer", "预告": "trailer",
               "behind the scenes": "behindthescenes", "花絮": "behindthescenes",
               "幕后": "behindthescenes", "deleted scenes": "deleted",
               "featurettes": "featurette", "featurette": "featurette",
               "特辑": "featurette", "彩蛋": "featurette",
               "interviews": "interview", "interview": "interview",
               "scenes": "scene", "shorts": "short", "short": "short",
               "other": "other", "samples": "sample", "sample": "sample",
               "extras": "extra", "extra": "extra"}


_KIND_WORDS: list[tuple] = [
    (re.compile(r"(?i)trailers?|teasers?|预告"), "trailer"),
    (re.compile(r"(?i)behind[ ._\-]*the[ ._\-]*scenes|making[\s.\-_]*of|メイキング|花絮|幕后"), "behindthescenes"),
    (re.compile(r"(?i)deleted[ ._\-]*scenes?|bloopers?"), "deleted"),
    (re.compile(r"(?i)featurette|特辑|彩蛋"), "featurette"),
    (re.compile(r"(?i)interviews?"), "interview"),
    (re.compile(r"(?i)[ ._\-]+scene$"), "scene"),
    (re.compile(r"(?i)[ ._\-]+shorts?$"), "short"),
]


_STRIP_LEAD_RES = (
    re.compile(r"(?i)^(making[\s.\-_]*of|behind[ ._\-]*the[ ._\-]*scenes?|featurette|"
               r"deleted[ ._\-]*scenes?|bloopers?|interviews?|trailers?|teasers?|"
               r"メイキング|花絮|预告|特辑|彩蛋|幕后)[\s.\-_：:・·\-—–~～]+(.+)$"),
    re.compile(r"^(花絮|预告|特辑|彩蛋|幕后|メイキングオブ|メイキング)(.+)$"),
    re.compile(r"(?i)^(making[\s.\-_]*of)(.+)$"),
)


_STRIP_TRAIL_RE = re.compile(
    r"(?i)^(.+?)[\s.\-_：:・·\-—–~～]+(featurette|deleted[ ._\-]*scenes?|bloopers?|"
    r"interviews?|trailers?|teasers?|メイキング|花絮|预告|特辑|彩蛋|幕后|shorts?|scenes?)$")

