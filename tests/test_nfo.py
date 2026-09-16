"""NFO 生成回归网（Kodi 字段 + XML 转义 + 落盘）。"""
import xml.etree.ElementTree as ET

from app.nfo import write_movie_nfo


def _movie(**kw):
    base = {
        "title": "Title", "original_title": "Original", "year": 2020,
        "overview": "plot", "overview_override": "",
        "tmdb_rating": 7.5, "custom_rating": None, "douban_rating": None,
        "genres": ["剧情"], "origin_countries": ["US", "CN"],
        "tags": ["tag1"], "tmdb_id": 123, "imdb_id": "tt0000001",
        "persons": [{"role": "director", "name": "D"},
                    {"role": "actor", "name": "A", "character_name": "C"}],
    }
    base.update(kw)
    return base


def test_nfo_fields(tmp_path):
    p = tmp_path / "movie.nfo"
    write_movie_nfo(_movie(), str(p))
    root = ET.parse(str(p)).getroot()
    assert root.tag == "movie"
    assert root.findtext("title") == "Title"
    assert root.findtext("year") == "2020"
    assert [g.text for g in root.findall("genre")] == ["剧情"]
    assert [c.text for c in root.findall("country")] == ["美国", "中国大陆"]
    assert root.findtext("director") == "D"
    actor = root.find("actor")
    assert actor.findtext("name") == "A"
    assert actor.findtext("role") == "C"
    types = {u.get("type"): u.text for u in root.findall("uniqueid")}
    assert types == {"tmdb": "123", "imdb": "tt0000001"}


def test_nfo_overview_override_wins(tmp_path):
    p = tmp_path / "movie.nfo"
    write_movie_nfo(_movie(overview="orig", overview_override="override"), str(p))
    assert ET.parse(str(p)).getroot().findtext("plot") == "override"


def test_nfo_xml_escaping(tmp_path):
    p = tmp_path / "movie.nfo"
    write_movie_nfo(_movie(title='A & B <c> "q"'), str(p))
    raw = p.read_text(encoding="utf-8")
    assert "&amp;" in raw and "&lt;c&gt;" in raw
    assert ET.parse(str(p)).getroot().findtext("title") == 'A & B <c> "q"'


def test_nfo_creates_parent_dir(tmp_path):
    p = tmp_path / "sub" / "movie.nfo"
    write_movie_nfo(_movie(), str(p))
    assert p.is_file()
