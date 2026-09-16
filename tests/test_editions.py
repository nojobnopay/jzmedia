"""editions 纯函数回归网（后缀/版本/分卷/清洗）。"""
from app.editions import (core_of, detect_edition, detect_spec, detect_spec_list,
                          sanitize_tag, split_stack)


def test_edition_director_cut():
    assert detect_edition("Movie.2020.1080p.Directors.Cut.mkv") == "导演剪辑版"
    assert detect_edition("电影.导演剪辑版.mkv") == "导演剪辑版"


def test_edition_extended_and_theatrical():
    assert detect_edition("Movie.2020.Extended.2160p.mkv") == "加长版"
    assert detect_edition("Movie.2020.Theatrical.mkv") == "公映版"


def test_edition_none_for_plain_release():
    assert detect_edition("Movie.2020.2160p.WEB-DL.mkv") == ""


def test_spec_list_order_and_dedupe():
    labels = detect_spec_list("Movie.2020.2160p.DV.HDR.BluRay.mkv")
    assert labels[:4] == ["杜比视界", "HDR", "蓝光", "2160P"]
    assert detect_spec("Movie.2020.1080p.mkv") == "1080P"


def test_spec_numbered_version():
    assert "版本2" in detect_spec_list("Movie.2020.1080p-版本2.mkv")


def test_split_stack_digits_only():
    assert split_stack("Movie.2020.cd1") == ("Movie.2020", "part1")
    assert split_stack("Movie.Part.Two") == ("Movie.Part.Two", "")


def test_sanitize_tag():
    assert sanitize_tag('a/b:c*?d"e<f>g|h') == "abcdefgh"
    assert len(sanitize_tag("x" * 50)) == 20


def test_core_of_strips_release_noise():
    assert core_of("Movie.2020.1080p.BluRay.x264-FRDS.mkv") == "movie"
