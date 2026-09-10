"""Kodi兼容movie.nfo生成（Jellyfin/Emby/Plex均可读，方便以后迁移）"""
import os
import xml.etree.ElementTree as ET

from .regions import country_name


def write_movie_nfo(movie: dict, nfo_path: str) -> None:
    root = ET.Element("movie")
    ET.SubElement(root, "title").text = movie.get("title", "")
    ET.SubElement(root, "originaltitle").text = movie.get("original_title", "")
    if movie.get("year"):
        ET.SubElement(root, "year").text = str(movie["year"])
    overview = movie.get("overview_override") or movie.get("overview", "")
    ET.SubElement(root, "plot").text = overview
    if movie.get("tmdb_rating"):
        ET.SubElement(root, "rating").text = str(movie["tmdb_rating"])
    if movie.get("custom_rating") is not None:
        ET.SubElement(root, "customrating").text = str(movie["custom_rating"])
    if movie.get("douban_rating") is not None:
        ET.SubElement(root, "douban_rating").text = str(movie["douban_rating"])
    for g in movie.get("genres", []) or []:
        ET.SubElement(root, "genre").text = g
    for code in movie.get("origin_countries", []) or []:
        ET.SubElement(root, "country").text = country_name(code)
    for t in movie.get("tags", []) or []:
        ET.SubElement(root, "tag").text = t
    if movie.get("tmdb_id"):
        uid = ET.SubElement(root, "uniqueid", type="tmdb")
        uid.text = str(movie["tmdb_id"])
    if movie.get("imdb_id"):
        uid = ET.SubElement(root, "uniqueid", type="imdb")
        uid.text = movie["imdb_id"]
    for p in movie.get("persons", []) or []:
        if p.get("role") == "director":
            ET.SubElement(root, "director").text = p.get("name", "")
    for p in movie.get("persons", []) or []:
        if p.get("role") == "actor":
            a = ET.SubElement(root, "actor")
            ET.SubElement(a, "name").text = p.get("name", "")
            ET.SubElement(a, "role").text = p.get("character_name", "")
    tree = ET.ElementTree(root)
    ET.indent(tree)
    os.makedirs(os.path.dirname(nfo_path) or ".", exist_ok=True)
    tree.write(nfo_path, encoding="utf-8", xml_declaration=True)
