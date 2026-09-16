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


# ---------- B7-NFO（R10-D1/D2/D3/B2） ----------

def test_nfo_strips_illegal_control_chars(tmp_path):
    p = tmp_path / "movie.nfo"
    write_movie_nfo(_movie(title="T\x00i\x0btle\x7f ok", overview="a\x0cb"),
                    str(p))
    raw = p.read_text(encoding="utf-8")
    for ch in ("\x00", "\x0b", "\x0c", "\x7f"):
        assert ch not in raw
    root = ET.parse(str(p)).getroot()
    assert root.findtext("title") == "Title ok"
    assert root.findtext("plot") == "ab"


def test_nfo_omits_zero_ratings(tmp_path):
    p = tmp_path / "movie.nfo"
    write_movie_nfo(_movie(tmdb_rating=0.0, custom_rating=0, douban_rating=0.0), str(p))
    root = ET.parse(str(p)).getroot()
    assert root.find("rating") is None
    assert root.find("customrating") is None
    assert root.find("douban_rating") is None


def test_nfo_country_falls_back_to_primary(tmp_path):
    p = tmp_path / "movie.nfo"
    write_movie_nfo(_movie(origin_countries=[], origin_country="JP"), str(p))
    assert [c.text for c in ET.parse(str(p)).getroot().findall("country")] == ["日本"]


def test_nfo_handles_none_text(tmp_path):
    p = tmp_path / "movie.nfo"
    write_movie_nfo(_movie(title=None, overview=None, genres=[None], tags=[None],
                           persons=[{"role": "actor", "name": None,
                                     "character_name": None}]), str(p))
    ET.parse(str(p))    # 不抛错
    roottxt = p.read_text(encoding="utf-8")
    assert "<title></title>" in roottxt or "<title />" in roottxt
