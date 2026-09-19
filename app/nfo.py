"""Kodi兼容movie.nfo生成（Jellyfin/Emby/Plex均可读，方便以后迁移）

字段对照：title/originaltitle/year/plot/rating/genre/country/tag/
uniqueid(tmdb|imdb)/director/actor(name+role)。customrating/douban_rating 为自定义扩展
（Kodi 忽略，Jellyfin 可读同名插件）。
"""
import os
import re
import xml.etree.ElementTree as ET

from .regions import country_name

# XML 1.0 非法控制字符（评审 B7/R10-D1）：标题/简介/标签里混入会让 Kodi/Jellyfin
# 直接解析失败整份 NFO，这里统一剔除（含 0x7f）
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _t(v) -> str:
    """任意值 → 安全文本：None→''、去 XML 非法控制字符（评审 R10-B2：不依赖 ET 宽容行为）"""
    return _CTRL_RE.sub("", str(v if v is not None else ""))


def _positive(v) -> bool:
    """评分是否值得写出（0/None 视为无评分，统一口径；评审 B7/R10-D2）"""
    try:
        return float(v or 0) > 0
    except (TypeError, ValueError):
        return False


def _build_root(movie: dict):
    root = ET.Element("movie")
    ET.SubElement(root, "title").text = _t(movie.get("title"))
    ET.SubElement(root, "originaltitle").text = _t(movie.get("original_title"))
    if movie.get("year"):
        ET.SubElement(root, "year").text = _t(movie["year"])
    overview = movie.get("overview_override") or movie.get("overview")
    ET.SubElement(root, "plot").text = _t(overview)
    # D6：Plex NFO Agent 可读的补充字段（Kodi/Jellyfin 忽略未知标签）
    cache = movie.get("_tmdb_cache") or {}
    if cache.get("premiered"):
        ET.SubElement(root, "premiered").text = _t(cache["premiered"])
    if cache.get("tagline"):
        ET.SubElement(root, "tagline").text = _t(cache["tagline"])
    try:
        runtime = int(cache.get("runtime") or 0)
    except (TypeError, ValueError):
        runtime = 0
    if runtime > 0:
        ET.SubElement(root, "runtime").text = str(runtime)
    for name in (cache.get("studios") or [])[:5]:
        if name:
            ET.SubElement(root, "studio").text = _t(name)
    if _positive(movie.get("tmdb_rating")):
        ET.SubElement(root, "rating").text = _t(movie["tmdb_rating"])
        ratings = ET.SubElement(root, "ratings")
        r = ET.SubElement(ratings, "rating", name="themoviedb", max="10", default="true")
        ET.SubElement(r, "value").text = _t(movie["tmdb_rating"])
    if _positive(movie.get("custom_rating")):
        ET.SubElement(root, "customrating").text = _t(movie["custom_rating"])
    if _positive(movie.get("douban_rating")):
        ET.SubElement(root, "douban_rating").text = _t(movie["douban_rating"])
    for g in movie.get("genres") or []:
        ET.SubElement(root, "genre").text = _t(g)
    # 产地：多产地优先；只有主产地（老种子行）时回退（评审 B7/R10-D3）
    codes = list(movie.get("origin_countries") or [])
    if not codes and movie.get("origin_country"):
        codes = [movie["origin_country"]]
    if cache.get("collection_name"):
        st = ET.SubElement(root, "set")
        ET.SubElement(st, "name").text = _t(cache["collection_name"])
    for code in codes:
        ET.SubElement(root, "country").text = _t(country_name(code))
    for t in movie.get("tags") or []:
        ET.SubElement(root, "tag").text = _t(t)
    if movie.get("tmdb_id"):
        uid = ET.SubElement(root, "uniqueid", type="tmdb")
        uid.text = _t(movie["tmdb_id"])
    if movie.get("imdb_id"):
        uid = ET.SubElement(root, "uniqueid", type="imdb")
        uid.text = _t(movie["imdb_id"])
    for p in movie.get("persons") or []:
        if p.get("role") == "director":
            ET.SubElement(root, "director").text = _t(p.get("name"))
    for p in movie.get("persons") or []:
        if p.get("role") == "actor":
            a = ET.SubElement(root, "actor")
            ET.SubElement(a, "name").text = _t(p.get("name"))
            ET.SubElement(a, "role").text = _t(p.get("character_name"))
            try:
                order = int(p.get("cast_order"))
            except (TypeError, ValueError):
                order = 99
            ET.SubElement(a, "order").text = str(order)
    return root


def render_movie_nfo_bytes(movie: dict) -> bytes:
    """movie dict → NFO XML 字节（远程后端 write 用；与本地落盘内容一致）。"""
    root = _build_root(movie)
    tree = ET.ElementTree(root)
    ET.indent(tree)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def write_movie_nfo(movie: dict, nfo_path: str) -> None:
    data = render_movie_nfo_bytes(movie)
    os.makedirs(os.path.dirname(nfo_path) or ".", exist_ok=True)
    # 原子写（评审 B5a-6）：中断/磁盘满不会留下半截 XML 被 Kodi/Jellyfin 读
    tmp = nfo_path + ".tmp"
    try:
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, nfo_path)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        raise
