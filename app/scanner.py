"""扫描器：遍历MEDIA_ROOT → guessit解析 → TMDB匹配 → 入库+NFO+海报。V1只做电影。

TMDB数据经 tmdb_cache 镜像：新文件同 tmdb_id 直接复用缓存零请求；
默认永不自动刷新，只经手动匹配/手动刷新入口写入远端数据。
"""
import os
import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor

from guessit import guessit

from . import store, tmdb
from .config import settings
from .db import POSTER_DIR, ensure_dirs
from .editions import detect_edition, detect_spec, split_stack
from .log import get_logger
from .nfo import write_movie_nfo
from .regions import resolve as resolve_region

logger = get_logger("scanner")

# 扫描/归属状态字符串（评审 B7/R08-Q3）：集中定义，避免多处字面量拼写漂移
ST_SKIPPED_SAMPLE = "skipped_sample"
ST_EXTRA_ATTACHED = "extra_attached"
ST_EXTRA_ORPHAN = "extra_orphan"
ST_NO_MATCH = "no_match"
ST_EPISODE = "skipped_episode_v1"

VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".ts", ".m2ts", ".mov", ".wmv", ".flv", ".webm"}
SUBTITLE_EXTS = {".srt", ".ass", ".ssa", ".sub", ".idx", ".sup"}
# 扫描剪枝（评审 P1-04）：NAS 回收站/缩略图目录/系统目录里的历史视频会污染库。
# 隐藏目录（. 开头）一律跳过；此处再列已知的系统目录，env SCAN_SKIP_DIRS 可追加。
_SKIP_DIR_NAMES = {"#recycle", "@eaDir", "$RECYCLE.BIN", "lost+found",
                   ".Trash", ".Trash-1000"}


def scan_skip_dirs() -> set[str]:
    """剪枝目录名集合：内置 + env SCAN_SKIP_DIRS（逗号分隔）。"""
    extra = (os.getenv("SCAN_SKIP_DIRS") or "").strip()
    if not extra:
        return set(_SKIP_DIR_NAMES)
    return set(_SKIP_DIR_NAMES) | {x.strip() for x in extra.split(",") if x.strip()}


# 播放器外挂字幕：文本（客户端渲染）+ 图片（PGS 客户端 / VobSub 烧录）
SIDECAR_TEXT_EXTS = {".srt": "srt", ".ass": "ass", ".ssa": "ssa"}
SIDECAR_SUB_DIRS = ("subs", "Subs", "字幕")

# 样片：*.Sample.mkv / *(Sample).mkv / Sample-xxx / Inception.1080p.sample.mkv
# 判定收紧（评审 P1-02）：sample 必须由 ./-/_ 分隔，且其后只允许跟已知发布词或直接到
# stem 末尾；纯空格边界（The Sample Movie (2024)）或任意词（Sample.This.2012）不再误判
# ——误判的代价是正片永不入库，比漏掉一个样片严重得多。
_SAMPLE_TOKENS = (
    r"\d{3,4}[pi]|4k|uhd|hdr\d*|dovi|dv|10bit|8bit|remux|"
    r"web[.\-]?dl|webrip|bluray|bdrip|brrip|hdrip|hdtv|"
    r"x26[45]|h[.\-]?26[45]|hevc|avc|aac|ac3|eac3|dts(?:[.\-]?hd)?|truehd|flac|atmos|proper|repack"
)
_SAMPLE_RE = re.compile(
    r"(?i)(?:(?:^|[.\-_])samples?(?:[.\-_](?:%s)){0,3}|\(samples?\))$" % _SAMPLE_TOKENS)
# 花絮：预告/幕后/删减/特辑/采访/片花/making of + 中文 花絮/预告/特辑/彩蛋
# （short/scene 只认 Plex 式末尾后缀，避免吞掉片名含 Short 的正片）
_EXTRAS_RE = re.compile(
    r"(?i)(behind[ ._\-]*the[ ._\-]*scenes|featurette|deleted[ ._\-]*scenes?|"
    r"bloopers?|interviews?|trailers?|teasers?|"
    r"making[\s.\-_]*of|メイキング|"
    r"[ ._\-]+shorts?$|[ ._\-]+scene$|"
    r"花絮|预告|特辑|彩蛋|幕后)")
# 花絮子目录名（全段匹配，大小写不敏感；Plex 全集 + 中文 + samples）
EXTRAS_DIR_NAMES = {"extras", "extra", "featurettes", "featurette",
                    "behind the scenes", "deleted scenes", "trailers", "trailer",
                    "interviews", "interview", "samples", "sample",
                    "花絮", "预告", "特辑"}
# 太通用的目录名：仅当父目录含正片（即“某部片的子目录”）时才算花絮目录，
# 避免顶层的 Shorts/Other 合集目录被吞掉
_GENERIC_DIR_NAMES = {"scenes", "other", "shorts"}
# 目录名 → kind（归属记录用）
KIND_BY_DIR = {"trailers": "trailer", "trailer": "trailer", "预告": "trailer",
               "behind the scenes": "behindthescenes", "花絮": "behindthescenes",
               "幕后": "behindthescenes", "deleted scenes": "deleted",
               "featurettes": "featurette", "featurette": "featurette",
               "特辑": "featurette", "彩蛋": "featurette",
               "interviews": "interview", "interview": "interview",
               "scenes": "scene", "shorts": "short", "short": "short",
               "other": "other", "samples": "sample", "sample": "sample",
               "extras": "extra", "extra": "extra"}
# 文件名关键词 → kind（按优先级）
_KIND_WORDS: list[tuple] = [
    (re.compile(r"(?i)trailers?|teasers?|预告"), "trailer"),
    (re.compile(r"(?i)behind[ ._\-]*the[ ._\-]*scenes|making[\s.\-_]*of|メイキング|花絮|幕后"), "behindthescenes"),
    (re.compile(r"(?i)deleted[ ._\-]*scenes?|bloopers?"), "deleted"),
    (re.compile(r"(?i)featurette|特辑|彩蛋"), "featurette"),
    (re.compile(r"(?i)interviews?"), "interview"),
    (re.compile(r"(?i)[ ._\-]+scene$"), "scene"),
    (re.compile(r"(?i)[ ._\-]+shorts?$"), "short"),
]

# 归属用种类词剥离：花絮文件名常把种类词粘在片名前后（Making of X / 预告X / X-花絮），
# 库内比对前剥掉再试一轮 fallback（原串优先试，不改变原有精确匹配顺序）
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


def pick_match(results: list[dict], year: int | None) -> tuple[dict | None, bool]:
    """挑结果：优先年份±1 内命中；都超出时退回首个候选，并报告年份未对上
    （评审 B5a-2/R03-D6：此前静默采信错年份结果，不标待确认）。"""
    if not results:
        return None, False
    if year:
        for r in results:
            rd = (r.get("release_date") or "")[:4]
            if rd.isdigit() and abs(int(rd) - year) <= 1:
                return r, False
    m = results[0]
    mismatch = False
    if year:
        rd = (m.get("release_date") or "")[:4]
        mismatch = not (rd.isdigit() and abs(int(rd) - year) <= 1)
    return m, mismatch


def search_with_fallback(title: str, year: int | None) -> tuple[dict | None, str, bool]:
    """依次试短查询，返回(命中, 实际生效的查询词, 年份是否未对上)。"""
    for q in short_candidates(title):
        results = tmdb.search_movie(q, year)
        m, year_mismatch = pick_match(results, year)
        if m:
            return m, q, year_mismatch
    return None, title, False


def meta_from_detail(detail: dict) -> dict:
    """TMDB详情 → 可写入 tmdb_cache 的字典（产地/类型/语言，不含海报/NFO）。"""
    year = None
    if detail.get("release_date", "")[:4].isdigit():
        year = int(detail["release_date"][:4])
    countries = [c.get("iso_3166_1", "") for c in detail.get("production_countries", [])
                 if c.get("iso_3166_1")]
    primary, region = resolve_region(countries, detail.get("original_language"))
    belongs = detail.get("belongs_to_collection") or {}
    try:
        col_id = int(belongs.get("id")) if belongs.get("id") is not None else None
    except (TypeError, ValueError):
        col_id = None
    return {
        "title": detail.get("title", ""),
        "original_title": detail.get("original_title", ""),
        "year": year,
        "overview": detail.get("overview", ""),
        "tmdb_id": detail["id"],
        "imdb_id": (detail.get("external_ids") or {}).get("imdb_id", ""),
        "tmdb_rating": detail.get("vote_average"),
        "genres": [g["name"] for g in detail.get("genres", []) if g.get("name")],
        "genre_ids": [g["id"] for g in detail.get("genres", []) if g.get("id")],
        "original_language": detail.get("original_language", "") or "",
        "origin_countries": countries,
        "origin_country": primary,
        "region": region,
        "media_type": "movie",
        "collection_tmdb_id": col_id,
        "collection_name": belongs.get("name", "") or "",
        "collection_poster_path": belongs.get("poster_path", "") or "",
    }


def extract_credits(detail: dict) -> dict:
    """从 movie_detail.credits 提取最小可用集（导演+前10演员），存入 tmdb_cache 供复用。"""
    cast = []
    for i, c in enumerate((detail.get("credits") or {}).get("cast", [])[:10]):
        if not c.get("id"):
            continue
        cast.append({"id": c["id"], "name": c.get("name", ""),
                     "profile_path": c.get("profile_path"),
                     "character": c.get("character", ""), "order": i})
    crew = []
    for d in (detail.get("credits") or {}).get("crew", []):
        if d.get("job") == "Director" and d.get("id"):
            crew.append({"id": d["id"], "name": d.get("name", ""),
                         "profile_path": d.get("profile_path")})
    return {"cast": cast, "crew": crew}


def jobs_from_credits(credits: dict) -> list[tuple]:
    """credits(原始detail或cache) → jobs[(tmdb_id, name, profile_path, role, character, order)]。"""
    credits = credits or {}
    jobs: list[tuple] = []
    for d in credits.get("crew", []):
        if d.get("id"):
            jobs.append((d["id"], d.get("name", ""), d.get("profile_path"),
                         "director", "", 99))
    for c in (credits.get("cast", []) or [])[:10]:
        if not c.get("id"):
            continue
        jobs.append((c["id"], c.get("name", ""), c.get("profile_path"),
                     "actor", c.get("character", ""), c.get("order", 99)))
    return jobs


def save_person_avatar(person_tmdb_id: int, profile_path: str | None) -> str:
    """人物头像落盘（w185），文件已存在则跳过。返回相对 DATA_DIR 的路径，失败返回 ''。"""
    if not profile_path:
        return ""
    dest = os.path.join(POSTER_DIR, f"person_{person_tmdb_id}.jpg")
    if os.path.exists(dest):
        return os.path.relpath(dest, settings.data_dir)
    if tmdb.download_poster(profile_path, dest, size="w185"):
        return os.path.relpath(dest, settings.data_dir)
    return ""


def _sync_jobs(mid: int, jobs: list[tuple], max_workers: int = 8) -> int:
    """jobs 落库（幂等）：头像按 profile 变化判断重下，关联一致则跳过 link。返回新下载头像数。"""
    now = int(time.time())
    existing = {(l["person_tmdb_id"], l["role"], l["character_name"] or "", l["cast_order"])
                for l in store.get_movie_person_links(mid)}
    uniq: dict = {}
    for job in jobs:
        uniq.setdefault(job[0], job)

    def _fetch(item: tuple) -> tuple:
        pid_tmdb, _, profile_path, _, _, _ = item
        if not profile_path:
            return pid_tmdb, "-", False, ""
        raw = store.get_person_raw(pid_tmdb) or {}
        stored_profile = raw.get("profile_tmdb_path") or ""
        old_avatar = raw.get("avatar") or ""
        dest = os.path.join(POSTER_DIR, f"person_{pid_tmdb}.jpg")
        if (stored_profile == (profile_path or "") and os.path.exists(dest)
                and old_avatar):
            return pid_tmdb, old_avatar, False, profile_path or ""
        # 远端换图/首下：download_poster 自带 tmp+replace 原子写；失败绝不删旧图
        # （评审 B7/R03-B2：此前先删旧文件再下载，失败会把头像清空）
        existed = os.path.exists(dest)
        avatar = save_person_avatar(pid_tmdb, profile_path)
        if avatar:
            return pid_tmdb, avatar, (not existed), profile_path or ""
        if old_avatar and os.path.exists(os.path.join(settings.data_dir, old_avatar)):
            logger.debug("avatar download failed, keep old person=%s old=%s",
                         pid_tmdb, old_avatar)
            return pid_tmdb, old_avatar, False, stored_profile
        logger.debug("avatar download failed person=%s profile=%s", pid_tmdb,
                     profile_path)
        return pid_tmdb, "", False, profile_path or ""

    avatars: dict = {}
    profiles: dict = {}
    downloaded = 0
    if uniq:
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            for pid_tmdb, avatar, is_new, profile in ex.map(_fetch, uniq.values()):
                avatars[pid_tmdb] = avatar
                profiles[pid_tmdb] = profile
                downloaded += 1 if is_new else 0
    for pid_tmdb, name, _, role, character, order in jobs:
        key = (pid_tmdb, role, character or "", order)
        if key in existing:
            # 关联已一致，仍需确保人物基础行存在（首建时可能缺行）
            if not store.get_person_raw(pid_tmdb):
                store.upsert_person(pid_tmdb, name, avatars.get(pid_tmdb),
                                    profiles.get(pid_tmdb, ""), now)
            continue
        pid = store.upsert_person(pid_tmdb, name, avatars.get(pid_tmdb),
                                  profiles.get(pid_tmdb, ""), now)
        store.link_person(mid, pid, role, character, order)
    return downloaded


def sync_persons(mid: int, detail: dict, max_workers: int = 8) -> int:
    """按TMDB credits 落库导演+前10演员（含头像），返回新下载头像数。供扫描/手动匹配/刷新共用。

    无照片的记 avatar='-'（确认无，避免反复重试）；远端换头像（profile_path 变化）才重下；
    关联一致跳过 link。落库串行；同一人多角色去重下载但保留全部关联。
    """
    jobs = jobs_from_credits((detail.get("credits") or {}))
    # 兼容 cache 形态（cast/crew 已为最小集）与原始 detail 形态
    if not jobs and isinstance(detail.get("credits"), dict):
        jobs = jobs_from_credits(detail["credits"])
    return _sync_jobs(mid, jobs, max_workers)


def sync_persons_from_cache(mid: int, tmdb_id: int) -> int:
    """免网络复用人物：cache.credits 有则直接落库；种子行 credits 为空时从兄弟版本复制关联。"""
    cached = store.get_tmdb_cached(tmdb_id)
    credits = (cached or {}).get("credits") or {}
    jobs = jobs_from_credits(credits)
    if jobs:
        return _sync_jobs(mid, jobs)
    sib = store.find_sibling_with_persons(tmdb_id, mid)
    if sib:
        n = store.copy_person_links(sib, mid)
        if n:
            store.resync_fts(mid)
        return 0
    return 0


def ensure_movie_poster(tmdb_id: int, poster_tmdb_path: str | None,
                        old_poster_tmdb_path: str | None = None) -> str:
    """海报本地路径保障：远端 path 未变且文件存在则复用，否则下载（覆盖）。
    返回相对 DATA_DIR 的路径，失败时有文件则复用旧文件，否则 ''。"""
    dest = os.path.join(POSTER_DIR, f"{tmdb_id}.jpg")
    remote = poster_tmdb_path or ""
    if not remote:
        return os.path.relpath(dest, settings.data_dir) if os.path.exists(dest) else ""
    if os.path.exists(dest) and (old_poster_tmdb_path or "") == remote:
        return os.path.relpath(dest, settings.data_dir)
    if tmdb.download_poster(remote, dest):
        return os.path.relpath(dest, settings.data_dir)
    return os.path.relpath(dest, settings.data_dir) if os.path.exists(dest) else ""


def _list_dir_nfos(movie_dir_abs: str) -> tuple[list[str] | None, list[str]]:
    """目录顶层 (全部文件名|None不可读, 正片视频名列表)。只看顶层，不进 extras/ 等子目录。"""
    try:
        names = sorted(os.listdir(movie_dir_abs))
    except OSError:
        return None, []
    feats = []
    for n in names:
        full = os.path.join(movie_dir_abs, n)
        try:
            if not os.path.isfile(full):
                continue
        except OSError:
            continue
        try:
            rel = os.path.relpath(full, settings.media_root)
        except ValueError:
            continue
        if is_feature_video(rel):
            feats.append(n)
    return names, sorted(feats)


def _same_film(row_tmdb, row_title, row_year,
               cur_tmdb, cur_title: str, cur_year) -> bool:
    """两行是否算“同片多版本”。有 tmdb_id 按 id 比；都无 id 按归一标题+年份比；
    一有一无保守判异（→共享目录，不互删 NFO）。"""
    if cur_tmdb and row_tmdb:
        try:
            return int(row_tmdb) == int(cur_tmdb)
        except (TypeError, ValueError):
            return row_tmdb == cur_tmdb
    if not cur_tmdb and not row_tmdb:
        rt = normalize_title(row_title or "")
        ct = normalize_title(cur_title or "")
        if not rt or not ct or rt != ct:
            return False
        if row_year and cur_year:
            try:
                return int(row_year) == int(cur_year)
            except (TypeError, ValueError):
                return False
        return True
    return False


def _ref_title_year(movie: dict, abs_path: str) -> tuple[str, object]:
    t = ((movie or {}).get("title") or "")
    y = (movie or {}).get("year")
    if t:
        return t, y
    try:
        p = parse_filename(os.path.basename(abs_path or ""))
        return p.get("title") or "", p.get("year")
    except Exception:
        return "", y


def sync_nfos_for(mid: int, abs_path: str, dry_run: bool = False) -> dict:
    """NFO 收敛唯一入口（幂等，失败自吞，调用方无需 try）。

    规则：movie.nfo 在独占目录永远保留；仅“同片多行同目录”（同 tmdb_id，
    无 id 则同归一标题+年份）才补各版本同名 .nfo；单版本删历史残留的同名 .nfo；
    不同电影混放的共享目录只写当前文件的同名 .nfo，不碰 movie.nfo、不删别人的。
    返回 {ok, mode, dir, wrote[], deleted[]}（dry_run 只计算不落盘）。
    """
    try:
        movie = store.get_movie(mid)
        if not movie:
            return {"ok": False, "mode": "missing", "dir": "",
                    "wrote": [], "deleted": []}
        movie_dir = os.path.dirname(abs_path)
        try:
            rel_dir = os.path.relpath(movie_dir, settings.media_root)
            if rel_dir == ".":
                rel_dir = ""
        except ValueError:
            rel_dir = os.path.dirname(movie.get("file_path", ""))
        stem = os.path.splitext(os.path.basename(abs_path))[0]
        if movie_dir and not os.path.isdir(movie_dir):
            # 目录尚不存在（如搬迁竞态）：退化为旧双写，保证元数据不丢
            if not dry_run:
                try:
                    write_movie_nfo(movie, os.path.join(movie_dir, "movie.nfo"))
                    if stem and stem != "movie":
                        write_movie_nfo(movie, os.path.join(movie_dir, stem + ".nfo"))
                except Exception as e:
                    logger.warning("nfo fallback write failed mid=%s dir=%s: %s",
                                   mid, rel_dir, e)
                    return {"ok": False, "mode": "fallback", "dir": rel_dir,
                            "wrote": [], "deleted": []}
            return {"ok": True, "mode": "fallback", "dir": rel_dir,
                    "wrote": ["movie.nfo"] + ([stem + ".nfo"] if stem and stem != "movie" else []),
                    "deleted": []}
        names, feats = _list_dir_nfos(movie_dir)
        if names is None:
            if not dry_run:
                try:
                    write_movie_nfo(movie, os.path.join(movie_dir, "movie.nfo"))
                    if stem and stem != "movie":
                        write_movie_nfo(movie, os.path.join(movie_dir, stem + ".nfo"))
                except Exception as e:
                    logger.warning("nfo fallback write failed mid=%s dir=%s: %s",
                                   mid, rel_dir, e)
                    return {"ok": False, "mode": "fallback", "dir": rel_dir,
                            "wrote": [], "deleted": []}
            wrote = ["movie.nfo"] + ([stem + ".nfo"] if stem and stem != "movie" else [])
            return {"ok": True, "mode": "fallback", "dir": rel_dir,
                    "wrote": wrote, "deleted": []}
        if stem == "movie":
            # 文件本身就叫 movie.*：同名 NFO 即 movie.nfo，只写一份
            if not dry_run:
                write_movie_nfo(movie, os.path.join(movie_dir, "movie.nfo"))
            return {"ok": True, "mode": "single-movie-stem", "dir": rel_dir,
                    "wrote": ["movie.nfo"], "deleted": []}
        try:
            rows = store.list_movies_in_dir(rel_dir)
        except Exception as e:
            logger.debug("list movies in dir failed dir=%s: %s", rel_dir, e)
            rows = []
        row_by_base: dict[str, dict] = {}
        for r in rows:
            try:
                if not is_feature_video(r.get("file_path", "")):
                    continue
            except Exception:
                continue
            b = os.path.basename(r.get("file_path", ""))
            if b and b not in row_by_base:
                row_by_base[b] = r
        cur_tmdb = movie.get("tmdb_id")
        ref_title, ref_year = _ref_title_year(movie, abs_path)
        ref_norm = normalize_title(ref_title or "")
        related: set[str] = set()
        foreign: set[str] = set()
        for f in feats:
            row = row_by_base.get(f)
            if row is not None:
                if _same_film(row.get("tmdb_id"), row.get("title"), row.get("year"),
                              cur_tmdb, ref_title, ref_year):
                    related.add(f)
                else:
                    foreign.add(f)
                continue
            # 磁盘有、库无（扫描中途）：文件名相似才算同片，避免把 待整理/ 误判独占
            try:
                p = parse_filename(f)
                pt = normalize_title(p.get("title") or "")
                py = p.get("year")
            except Exception:
                foreign.add(f)
                continue
            same = bool(ref_norm and pt and pt == ref_norm)
            if same and py and ref_year:
                try:
                    same = int(py) == int(ref_year)
                except (TypeError, ValueError):
                    same = False
            (related if same else foreign).add(f)
        # 共享目录：只写当前同名，不碰 movie.nfo、不删任何
        if foreign:
            target = os.path.join(movie_dir, stem + ".nfo")
            if not dry_run:
                write_movie_nfo(movie, target)
            return {"ok": True, "mode": "shared", "dir": rel_dir,
                    "wrote": [stem + ".nfo"], "deleted": []}
        existing_nfos = [n for n in (names or [])
                         if n.endswith(".nfo") and os.path.isfile(os.path.join(movie_dir, n))]
        if len(feats) <= 1:
            deleted = [n for n in existing_nfos if n != "movie.nfo"]
            if not dry_run:
                write_movie_nfo(movie, os.path.join(movie_dir, "movie.nfo"))
                for n in deleted:
                    try:
                        os.remove(os.path.join(movie_dir, n))
                    except OSError as e:
                        logger.debug("nfo remove failed file=%s: %s", n, e)
            return {"ok": True, "mode": "single", "dir": rel_dir,
                    "wrote": ["movie.nfo"], "deleted": sorted(deleted)}
        # 独占多版本：movie.nfo + 每个相关版本各写同名（各用各行的全量 dict，保留手工评分差异）
        wanted = {"movie.nfo"}
        writers: list[tuple[str, dict]] = [("movie.nfo", movie)]
        for f in sorted(related):
            st = os.path.splitext(f)[0]
            if not st or st == "movie":
                continue
            wanted.add(st + ".nfo")
            row = row_by_base.get(f)
            full = movie
            if row is not None and int(row.get("id", -1)) != int(mid):
                try:
                    got = store.get_movie(int(row["id"]))
                    if got:
                        full = got
                except Exception as e:
                    logger.debug("load version row failed id=%s: %s", row.get("id"), e)
            writers.append((st + ".nfo", full))
        deleted = [n for n in existing_nfos if n not in wanted]
        if not dry_run:
            failed = 0
            for name, data in writers:
                try:
                    write_movie_nfo(data, os.path.join(movie_dir, name))
                except Exception as e:
                    failed += 1
                    logger.warning("nfo write failed mid=%s file=%s: %s", mid, name, e)
            for n in deleted:
                try:
                    os.remove(os.path.join(movie_dir, n))
                except OSError as e:
                    logger.debug("nfo remove failed file=%s: %s", n, e)
            if failed:
                return {"ok": False, "mode": "multi", "dir": rel_dir,
                        "wrote": sorted({n for n, _ in writers}), "deleted": sorted(deleted)}
        return {"ok": True, "mode": "multi", "dir": rel_dir,
                "wrote": sorted({n for n, _ in writers}), "deleted": sorted(deleted)}
    except Exception as e:
        logger.warning("sync_nfos_for failed mid=%s dir=%s: %s", mid, abs_path, e)
        return {"ok": False, "mode": "error", "dir": "",
                "wrote": [], "deleted": []}


def _write_nfo_for(mid: int, abs_path: str) -> bool:
    try:
        return bool(sync_nfos_for(mid, abs_path).get("ok"))
    except Exception:
        logger.debug("write nfo failed mid=%s path=%s", mid, abs_path, exc_info=True)
        return False


def _media_needs(tmdb_id: int, mid: int, poster_tmdb: str,
                 old_poster_tmdb: str, detail: dict) -> dict:
    """后台重活是否真有事做（供回包 flags，前端展示“补齐中”）。纯本地判断，不调网。"""
    dest = os.path.join(POSTER_DIR, f"{tmdb_id}.jpg")
    poster = bool(poster_tmdb) and not (
        os.path.exists(dest) and (old_poster_tmdb or "") == poster_tmdb)
    try:
        avatars = store.persons_missing_avatar(mid)
    except Exception:
        avatars = False
    if not avatars:
        try:
            jobs = jobs_from_credits(extract_credits(detail))
            avatars = bool(jobs and store.get_movie_person_links(mid) == [])
        except Exception:
            avatars = False
    return {"poster": bool(poster), "avatars": bool(avatars)}


def finish_tmdb_media(mid: int, detail: dict, abs_path: str,
                      poster_tmdb: str, old_poster_tmdb: str) -> dict:
    """后台重活：海报下载 + 头像同步 + FTS + NFO。幂等，失败自吞（下次刷新/重绑自愈）。"""
    tmdb_id = detail["id"]
    try:
        poster_local = ensure_movie_poster(tmdb_id, poster_tmdb, old_poster_tmdb)
        store.update_movie_meta(mid, poster_path=poster_local)
        sync_persons(mid, detail)
        store.resync_fts(mid)
        nfo = _write_nfo_for(mid, abs_path)
        return {"poster_path": poster_local, "nfo": nfo}
    except Exception as e:
        logger.warning("finish_tmdb_media failed mid=%s tmdb=%s: %s", mid,
                       tmdb_id, e)
        return {"poster_path": "", "nfo": False}


def apply_tmdb_detail(mid: int, detail: dict, abs_path: str,
                      force_title: bool = False) -> dict:
    """把TMDB详情写入镜像并复制到本片（人物/海报/NFO/FTS，全同步版；扫描链路用）。
    force_title=True（手动换绑）时无条件覆盖标题；否则保留手工改过的标题。"""
    out, media = apply_tmdb_detail_fast(mid, detail, abs_path, force_title)
    finish_tmdb_media(mid, detail, abs_path, media["poster_tmdb"],
                      media["old_poster_tmdb"])
    movie = store.get_movie(mid)
    out["nfo"] = _write_nfo_for(mid, abs_path)
    if movie:
        out["title"], out["year"] = movie["title"], movie["year"]
    return out


def apply_tmdb_detail_fast(mid: int, detail: dict, abs_path: str,
                           force_title: bool = False) -> tuple[dict, dict]:
    """快路径（绑定/刷新接口用）：只落镜像 + 复制文字元数据，立即回包；
    海报/头像/NFO 由调用方经 finish_tmdb_media() 放后台。返回 (result, media_args)。"""
    tmdb_id = detail["id"]
    cur = store.get_movie(mid)
    cur_tmdb = (cur or {}).get("tmdb_id")
    cur_title = (cur or {}).get("title") or ""
    if cur_tmdb and cur_tmdb != tmdb_id:
        store.clear_movie_persons(mid)
    if not cur_tmdb or cur_tmdb != tmdb_id:
        store.update_movie_meta(mid, tmdb_id=tmdb_id)
    old_cache = store.get_tmdb_cached(tmdb_id)
    old_title = (old_cache or {}).get("title") or ""
    old_poster_tmdb = (old_cache or {}).get("poster_tmdb_path") or ""
    meta = meta_from_detail(detail)
    poster_tmdb = detail.get("poster_path") or ""
    credits = extract_credits(detail)
    store.upsert_tmdb_cache(tmdb_id, meta, credits, poster_tmdb)
    if force_title:
        store.update_movie_meta(mid, tmdb_id=tmdb_id)
        store.copy_tmdb_to_movie(mid, old_title=cur_title)
        # copy 在 current==old_title 时才会跟随标题；换绑需强制对齐远端标题
        if (store.get_movie(mid) or {}).get("title") != (meta.get("title") or ""):
            store.update_movie_meta(mid, title=meta.get("title") or "")
        # 其余 TMDB 列经 copy 已同步（copy 内含除 poster 外全量）
    else:
        store.copy_tmdb_to_movie(mid, old_title=old_title if old_cache else None)
    # copy_tmdb_to_movie/update_movie_meta 内部已 resync_fts（评审 B7/R05-B1：不再重复）
    movie = store.get_movie(mid) or {}
    needs = _media_needs(tmdb_id, mid, poster_tmdb, old_poster_tmdb, detail)
    out = {"title": movie.get("title", ""), "year": movie.get("year"),
           "tmdb_id": tmdb_id, "nfo": False, "background": needs}
    media = {"poster_tmdb": poster_tmdb, "old_poster_tmdb": old_poster_tmdb}
    return out, media


def apply_cached_to_movie(mid: int, tmdb_id: int, abs_path: str) -> dict:
    """零网络复用：从 tmdb_cache 向新行复制元数据+海报复用+人物复用，供同 tmdb_id 多版本使用。"""
    cur = store.get_movie(mid)
    cur_tmdb = (cur or {}).get("tmdb_id")
    if cur_tmdb and cur_tmdb != tmdb_id:
        store.clear_movie_persons(mid)
    if not cur_tmdb or cur_tmdb != tmdb_id:
        store.update_movie_meta(mid, tmdb_id=tmdb_id)
    store.copy_tmdb_to_movie(mid, old_title=None)
    cached = store.get_tmdb_cached(tmdb_id) or {}
    poster_local = ensure_movie_poster(tmdb_id, cached.get("poster_tmdb_path") or "",
                                       cached.get("poster_tmdb_path") or "")
    store.update_movie_meta(mid, poster_path=poster_local)
    sync_persons_from_cache(mid, tmdb_id)
    store.resync_fts(mid)
    movie = store.get_movie(mid) or {}
    nfo = _write_nfo_for(mid, abs_path)
    return {"title": movie.get("title", ""), "year": movie.get("year"),
            "tmdb_id": tmdb_id, "nfo": nfo}


def finish_refresh_media(jobs: list[dict]) -> int:
    """后台重活：各版本的海报/头像/NFO。幂等，失败自吞。返回处理数。"""
    n = 0
    for j in jobs:
        try:
            mid = j["mid"]
            if not store.get_movie(mid):
                continue
            poster_local = ensure_movie_poster(j["tmdb_id"], j["poster_tmdb"],
                                               j["old_poster_tmdb"])
            store.update_movie_meta(mid, poster_path=poster_local)
            sync_persons(mid, j["detail"])
            store.resync_fts(mid)
            _write_nfo_for(mid, j["abs_path"])
            n += 1
        except Exception:
            continue
    return n


def refresh_tmdb_id(tmdb_id: int) -> dict:
    """手动刷新唯一入口：抓远端 → 写镜像 → 有变化才扇出到所有同 tmdb_id 版本。
    返回 {changed, affected_ids, title, year}。无变化时不碰任何 movies 行。"""
    out, jobs = refresh_tmdb_id_fast(int(tmdb_id))
    if jobs:
        finish_refresh_media(jobs)
    return out


def refresh_tmdb_id_fast(tmdb_id: int) -> tuple[dict, list[dict]]:
    """快路径（刷新接口用）：抓远端 + 写镜像 + 文字扇出，立即回包；
    海报/头像/NFO 由调用方经 finish_refresh_media() 放后台。返回 (result, jobs)。"""
    tmdb_id = int(tmdb_id)
    detail = tmdb.movie_detail(tmdb_id)
    new_meta = meta_from_detail(detail)
    new_poster_tmdb = detail.get("poster_path") or ""
    new_credits = extract_credits(detail)
    old_cache = store.get_tmdb_cached(tmdb_id)
    old_title = (old_cache or {}).get("title") or ""
    old_poster_tmdb = (old_cache or {}).get("poster_tmdb_path") or ""
    changed = store.upsert_tmdb_cache(tmdb_id, new_meta, new_credits, new_poster_tmdb)
    if not changed:
        jobs = []
        for mid in store.list_movie_ids_by_tmdb(tmdb_id):
            # 缺头像补齐放后台；回包 flags 诚实告知
            try:
                if store.persons_missing_avatar(mid):
                    m = store.get_movie(mid)
                    if m:
                        jobs.append({"mid": mid, "tmdb_id": tmdb_id,
                                     "detail": detail, "poster_tmdb": new_poster_tmdb,
                                     "old_poster_tmdb": old_poster_tmdb,
                                     "abs_path": os.path.join(
                                         settings.media_root, m["file_path"])})
            except Exception:
                continue
        cur = store.get_tmdb_cached(tmdb_id) or {}
        return ({"changed": False, "affected_ids": [],
                 "title": cur.get("title", ""), "year": cur.get("year"),
                 "tmdb_id": tmdb_id,
                 "background": {"poster": False, "avatars": bool(jobs)}}, jobs)
    affected: list[int] = []
    jobs = []
    for mid in store.list_movie_ids_by_tmdb(tmdb_id):
        m = store.get_movie(mid)
        if not m:
            continue
        store.copy_tmdb_to_movie(mid, old_title=old_title if old_cache else None)
        abs_path = os.path.join(settings.media_root, m["file_path"])
        affected.append(mid)
        jobs.append({"mid": mid, "tmdb_id": tmdb_id, "detail": detail,
                     "poster_tmdb": new_poster_tmdb,
                     "old_poster_tmdb": old_poster_tmdb, "abs_path": abs_path})
    return ({"changed": True, "affected_ids": affected,
             "title": new_meta.get("title", ""), "year": new_meta.get("year"),
             "tmdb_id": tmdb_id,
             "background": {"poster": bool(new_poster_tmdb),
                            "avatars": bool(jobs)}}, jobs)


def attribute_extra(abs_path: str) -> dict:
    """花絮归属：解析标题/年份 → 库内标题/原标题匹配（年份±1，种类词前后缀剥掉再试一轮）
    → extras 表幂等记录。历史 orphan 在正片后入库/匹配修好后重扫自动补归属
    （已有归属的不碰，手工认领优先）。
    样片永不归属（仅返回 skipped_sample）。返回 {file, status, movie_id?, kind}。"""
    rel = os.path.relpath(abs_path, settings.media_root)
    base = os.path.basename(abs_path)
    if is_sample(base):
        return {"file": rel, "status": ST_SKIPPED_SAMPLE}
    kind = extra_kind(rel)
    if kind == "sample":
        # 样片片段：只认不收（永不归属、不入库、不搬迁）
        return {"file": rel, "status": ST_SKIPPED_SAMPLE}
    parsed = parse_filename(base)
    title = normalize_title(parsed.get("title") or "")
    # 花絮文件名常带原标题（如 Making of おもひでぽろぽろ）：归一后直比；
    # 文件名无意义时（clip/etc）逐级退回祖先目录名（如 Plex 树的 Movie/Other/etc.mkv）
    cands = [(title, parsed.get("year"))]
    parts = rel.replace("\\", "/").split("/")[:-1]
    for anc in reversed(parts):
        if not anc or anc in (".",):
            continue
        try:
            pp = parse_filename(anc)
            t = normalize_title(pp.get("title") or "")
            if t:
                cands.append((t, pp.get("year")))
        except Exception:
            continue
    hit, mid = None, None
    expanded = list(cands)
    for t, y in cands:
        st = strip_kind_affix(t) if t else ""
        if st and normalize_title(st) != normalize_title(t or ""):
            expanded.append((normalize_title(st), y))
    for t, y in expanded:
        if t and (hit := store.find_movie_for_extra(t, y)):
            mid = hit["id"]
            break
    # 已入库（搬迁改路径）先按 basename 认领，避免删建抖动
    claimed = store.repath_extra_by_basename(base, rel, mid, kind)
    if claimed is None:
        store.upsert_extra(rel, mid, kind)
    elif mid and not claimed.get("movie_id"):
        # 历史 orphan：正片后入库/匹配修好后重扫自动补归属（已有归属的不碰，手工认领优先）
        try:
            store.upsert_extra(rel, mid, kind)
        except Exception as e:
            logger.debug("re-attribute extra failed file=%s: %s", rel, e)
    if mid:
        return {"file": rel, "status": ST_EXTRA_ATTACHED, "movie_id": mid,
                "kind": kind, "title": hit.get("title", "")}
    return {"file": rel, "status": ST_EXTRA_ORPHAN, "kind": kind}


def scan_one(abs_path: str) -> dict:
    rel = os.path.relpath(abs_path, settings.media_root)
    if is_sidecar(rel):
        if is_sample(os.path.basename(abs_path)):
            return {"file": rel, "status": "skipped_sample"}
        return attribute_extra(abs_path)
    cached = store.get_by_path(rel)
    if cached and cached.get("tmdb_id"):
        return {"file": rel, "status": "skipped_cached", "title": cached.get("title")}
    parsed = parse_filename(os.path.basename(abs_path))
    parsed["title"] = normalize_title(parsed["title"])
    if parsed["type"] == "episode":
        # V1 仅电影：剧集不入库（评审 P1-03：旧实现建行会让剧集出现在海报墙/统计里，
        # 与 README“剧集跳过”不符）。历史脏行由 POST /api/files/clean-episodes 清理。
        return {"file": rel, "status": ST_EPISODE}
    m, used_q, year_mismatch = search_with_fallback(parsed["title"], parsed["year"])
    if not m:
        mid = store.upsert_movie_by_path(rel)
        # 重扫不覆盖既有标题（评审 B5a-1/R03-B1）：只补空值，人工修正/上次解析
        # 结果都保留；年份不可手工改，允许按文件名更新
        patch: dict = {"year": parsed["year"]}
        if not (cached or {}).get("title"):
            patch["title"] = parsed["title"]
        store.update_movie_meta(mid, **patch)
        try:
            cur = store.get_movie(mid) or {}
        except Exception:
            cur = {}
        local = {}
        if parsed.get("edition") and not cur.get("edition"):
            local["edition"] = parsed["edition"]
        if parsed.get("spec") and not cur.get("spec"):
            local["spec"] = parsed["spec"]
        if local:
            try:
                store.update_movie_local(mid, **local)
            except Exception as e:
                logger.debug("persist edition/spec failed mid=%s: %s", mid, e)
        return {"file": rel, "status": ST_NO_MATCH, "parsed": parsed}
    tmdb_id = int(m["id"])
    mid = store.upsert_movie_by_path(rel)
    # 版本/规格后缀持久化：重扫不覆盖手工改过的值（非空保留）
    try:
        cur = store.get_movie(mid) or {}
    except Exception:
        cur = {}
    local: dict = {}
    if not cur.get("edition") and parsed.get("edition"):
        local["edition"] = parsed["edition"]
    if not cur.get("spec") and parsed.get("spec"):
        local["spec"] = parsed["spec"]
    if local:
        store.update_movie_local(mid, **local)
    if store.get_tmdb_cached(tmdb_id):
        out = apply_cached_to_movie(mid, tmdb_id, abs_path)
    else:
        detail = tmdb.movie_detail(tmdb_id)
        out = apply_tmdb_detail(mid, detail, abs_path)
    needs_review = 1 if (used_q != parsed["title"] or year_mismatch) else 0
    store.update_movie_local(mid, needs_review=needs_review)
    status = "ok" if not needs_review else "ok_needs_review"
    return {"file": rel, "status": status, "query": used_q, **out}


def scan_all() -> list[dict]:
    ensure_dirs()
    store.init_db()
    out = []
    seen_extras: set[str] = set()
    skip_dirs = scan_skip_dirs()
    for root, dirs, files in os.walk(settings.media_root):
        # 剪枝（评审 P1-04）：隐藏目录 + NAS 回收站/缩略图等系统目录不进库
        dirs[:] = sorted(d for d in dirs
                         if not d.startswith(".") and d not in skip_dirs)
        for f in sorted(files):
            if f.startswith("."):
                continue
            if os.path.splitext(f)[1].lower() in VIDEO_EXTS:
                try:
                    r = scan_one(os.path.join(root, f))
                    out.append(r)
                    if r.get("status") in (ST_EXTRA_ATTACHED, ST_EXTRA_ORPHAN):
                        seen_extras.add(r["file"])
                except Exception as e:
                    rel = os.path.relpath(os.path.join(root, f), settings.media_root)
                    logger.debug("scan_one failed file=%s: %s", rel, e, exc_info=True)
                    out.append({"file": rel, "status": f"error: {e}"})
    # 花絮行 GC：文件已不存在的归属记录清掉（正片走 missing/clean 流程）
    try:
        for row in store.list_all_extras():
            if row["file_path"] not in seen_extras and not os.path.exists(
                    os.path.join(settings.media_root, row["file_path"])):
                store.delete_extra_by_path(row["file_path"])
    except Exception as e:
        logger.debug("extras gc failed: %s", e)
    return out
