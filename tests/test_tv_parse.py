"""T1：TV 规则阶梯解析（真实 NAS 文件名回归语料）。"""
import pytest

from app.scanner import tv_parse


@pytest.mark.parametrize("name,season,episode,end", [
    ("Show.S01E01.mkv", 1, 1, 0),
    ("Show.S01.E01.mkv", 1, 1, 0),
    ("物理魔法使马修S01E01.mp4", 1, 1, 0),
    ("Show.1x01.mkv", 1, 1, 0),
    ("进击的巨人.S04E73E74.mp4", 4, 73, 74),
    ("strike.back.s01e01-e02.720p.mkv", 1, 1, 2),
    ("Show.S02E18-E19.avi", 2, 18, 19),
    ("Show.S01E05-06.mkv", 1, 5, 6),
    ("Usavich.S00E01-E13.WEB.1080p.mkv", 0, 1, 13),
    ("Show.S01E01.1080p.mkv", 1, 1, 0),
    ("出包王女第26集[www.qire123.com].rmvb", None, 26, 0),
    ("犬夜叉.inuyasha.E001.rmvb", None, 1, 0),
    ("犬夜叉.inuyasha.E021-E022.rmvb", None, 21, 22),
    ("Show.EP12.mp4", None, 12, 0),
])
def test_regular_episodes(name, season, episode, end):
    p = tv_parse.parse_episode(name)
    assert p["season"] == season, name
    assert p["episode"] == episode, name
    assert p["episode_end"] == end, name


@pytest.mark.parametrize("name,episode", [
    ("0001.flv", 1),
    ("蜡笔小新.0001.flv", 1),
    ("21.mp4", 21),
    ("01.神秘的龙珠出现 悟空变成了小孩(ED2000.COM).mkv", 1),
    ("黑街01.mp4", 1),
    ("1 残酷.mp4", 1),
    ("血仇.Hatfields.and.McCoys.2012/1.mkv", 1),
    ("BBC.Life.05.Birds.2009.BD.REMUX.h264.1080P.DTSHDHR.DD20.TriAudio.MySilu.ts", 5),
    ("A.Bite.Of.China.II.01.1080p.mp4", 1),
    ("钢之炼金术师FA.01.mp4", 1),
])
def test_bare_numbers(name, episode):
    p = tv_parse.parse_episode(name)
    assert p["episode"] == episode, name
    assert p["season"] is None, name
    assert p["absolute"] is True, name


@pytest.mark.parametrize("name", [
    "random.mkv",
    "Show.1080p.mkv",
    "Show.2019.mkv",
    "Show.2160p.HEVC.mkv",
    "Show (2019).mkv",
])
def test_no_episode(name):
    assert tv_parse.parse_episode(name)["episode"] is None


@pytest.mark.parametrize("name,episode", [
    # 括号数字是集号标记：与分辨率同值（240/360/720/1080/1440）也按集号解析
    ("蜡笔小新_高清版 (240).flv", 240),
    ("蜡笔小新_高清版 (360).flv", 360),
    ("蜡笔小新_高清版 (720).flv", 720),
    ("蜡笔小新_高清版 (1080).flv", 1080),
    ("Show (1440).mkv", 1440),
    ("蜡笔小新_高清版（240）.flv", 240),
    ("Show【720】.mp4", 720),
])
def test_bare_bracketed_numbers(name, episode):
    p = tv_parse.parse_episode(name)
    assert p["episode"] == episode, name
    assert p["season"] is None, name
    assert p["absolute"] is True, name


@pytest.mark.parametrize("name,episode", [
    ("Legal.High.SP.2013.BluRay.1080p.x265.10bit.FRDS.mkv", None),
    ("Legal.High.SP2.2014.BluRay.1080p.x265.10bit.FRDS.mkv", 2),
    ("出包王女.OVA.01.rmvb", 1),
    ("Deadman.Wonderland.OVA [BD 1920x1080 x265 HEVC 10bit FLAC][CHS].mkv", None),
    ("OAD.01.伊尔泽的记事本.mp4", 1),
    ("[DBD-Raws][胜者即是正义SP][1080P][BDRip][HEVC-10bit][FLAC].mkv", None),
])
def test_specials(name, episode):
    p = tv_parse.parse_episode(name)
    assert p["special"] is True, name
    assert p["episode"] == episode, name


def test_date_based():
    p = tv_parse.parse_episode("The.Colbert.Report.2011-11-15.Elijah.Wood.avi")
    assert p["date"] == "2011-11-15"
    assert p["episode"] is None


@pytest.mark.parametrize("name,season", [
    ("Season 01", 1),
    ("season 1", 1),
    ("S01", 1),
    ("S02 无限列车篇TV版", 2),
    ("Season 00", 0),
    ("Specials", 0),
    ("第3季", 3),
    ("Featurettes", None),
    ("[进击的巨人 OAD][8部全]", None),
])
def test_season_from_dir(name, season):
    assert tv_parse.season_from_dir(name) == season


@pytest.mark.parametrize("name,title,year", [
    ("Show (2019)", "Show", 2019),
    ("蜡笔小新.1665集全.国语.1992", "蜡笔小新", 1992),
    ("七龙珠.Z", "七龙珠 Z", None),
    ("A.Bite.of.China", "A Bite of China", None),
    ("聪明的一休.1975", "聪明的一休", 1975),
    ("西部世界.Westworld", "西部世界 Westworld", None),
    ("Breaking.Bad.2008", "Breaking Bad", 2008),
    ("葬送的芙莉莲 Sousou no Frieren", "葬送的芙莉莲 Sousou no Frieren", None),
    ("Show.Name.1080p.BluRay", "Show Name", None),
])
def test_parse_show_dir(name, title, year):
    d = tv_parse.parse_show_dir(name)
    assert d["title"] == title
    assert d["year"] == year


def test_parse_show_dir_hints():
    d = tv_parse.parse_show_dir("The.Office.UK (2001) {tmdb-2996}")
    assert d["title"] == "The Office UK"
    assert d["year"] == 2001
    assert d["hints"] == {"tmdb": "2996"}
    d = tv_parse.parse_show_dir("Show {tvdb-73244}")
    assert d["hints"] == {"tvdb": "73244"}


@pytest.mark.parametrize("name,special,movie", [
    ("[进击的巨人 OAD][8部全]", True, False),
    ("Specials", True, False),
    ("出包王女 OVA", True, False),
    ("Inuyasha Movies", False, True),
    ("[进击的巨人 剧场版][3部全]", False, True),
    ("Season 01", False, False),
    ("七龙珠.Z", False, False),
])
def test_dir_kind(name, special, movie):
    assert tv_parse.is_tv_special_dir(name) is special
    assert tv_parse.is_tv_movie_dir(name) is movie
