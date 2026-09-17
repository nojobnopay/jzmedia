"""外挂字幕松匹配回归（2026-09 用户：上传 大桥下面.srt 后播放器不认识）。

严格规则之外新增「标题同名」宽松匹配（剥语言后缀后 guessit 标题相等），
以及无内嵌字幕时兜底第一条文本外挂为默认轨。
"""
import pathlib

from app import scanner
from app.routers.stream.subtitles import _sub_list


def _touch(root: pathlib.Path, rel: str) -> pathlib.Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")
    return p


def _side(media_root: pathlib.Path, rel_video: str):
    return scanner.sidecar_subtitles(str(media_root / rel_video))


def test_sidecar_title_match_user_case(media_root):
    """正片 大桥下面 (1984).mkv + 纯标题字幕 大桥下面.srt（用户实际上传场景）。"""
    _touch(media_root, "电影/华语/大桥下面 (1984)/大桥下面 (1984).mkv")
    _touch(media_root, "电影/华语/大桥下面 (1984)/大桥下面.srt")
    side = _side(media_root, "电影/华语/大桥下面 (1984)/大桥下面 (1984).mkv")
    assert [s["name"] for s in side] == ["大桥下面.srt"]
    assert side[0]["suffix"] == ""
    assert side[0]["codec"] == "srt"


def test_sidecar_lang_suffix_stripped(media_root):
    """大桥下面.chs.srt：剥语言后缀后标题同名；suffix 保留供语言推断/角标。"""
    _touch(media_root, "华语/大桥下面 (1984)/大桥下面 (1984).mkv")
    _touch(media_root, "华语/大桥下面 (1984)/大桥下面.chs.srt")
    side = _side(media_root, "华语/大桥下面 (1984)/大桥下面 (1984).mkv")
    assert [s["name"] for s in side] == ["大桥下面.chs.srt"]
    assert side[0]["suffix"] == "chs"
    assert scanner._guess_sidecar_lang(side[0]["suffix"])[0] == "chi"


def test_sidecar_title_match_reverse(media_root):
    """反方向：字幕带年份、正片不带（标题键相等即可）。"""
    _touch(media_root, "x/大桥下面.mkv")
    _touch(media_root, "x/大桥下面 (1984).srt")
    side = _side(media_root, "x/大桥下面.mkv")
    assert [s["name"] for s in side] == ["大桥下面 (1984).srt"]


def test_sidecar_title_no_false_positive(media_root):
    """标题续词不同不误配：Alien.srt 不属于 Alien Resurrection (1997)。"""
    _touch(media_root, "欧美/Alien Resurrection (1997)/Alien Resurrection (1997).mkv")
    _touch(media_root, "欧美/Alien Resurrection (1997)/Alien.srt")
    assert _side(media_root, "欧美/Alien Resurrection (1997)/Alien Resurrection (1997).mkv") == []


def test_sidecar_title_still_requires_same_film(media_root):
    """同前缀两部片：纯标题字幕只配标题全等的那一部。"""
    _touch(media_root, "x/Movie (2020)/Movie (2020).mkv")
    _touch(media_root, "x/Movie (2020)/Movie 2 (2021).mkv")
    _touch(media_root, "x/Movie (2020)/Movie.srt")
    assert [s["name"] for s in _side(media_root, "x/Movie (2020)/Movie (2020).mkv")] == ["Movie.srt"]
    assert _side(media_root, "x/Movie (2020)/Movie 2 (2021).mkv") == []


def test_sub_list_auto_default_lone_text_sidecar(media_root):
    """无内嵌 + 唯一非中文文本外挂 → default=1（前端 pickDefaultSub 才会自动加载）。"""
    _touch(media_root, "auto1/大桥下面 (1984)/大桥下面 (1984).mkv")
    _touch(media_root, "auto1/大桥下面 (1984)/大桥下面.srt")
    m = {"id": 1, "file_path": "auto1/大桥下面 (1984)/大桥下面 (1984).mkv"}
    subs = _sub_list(m, {"subs": []})
    assert [(s["source"], s["default"]) for s in subs] == [("sidecar", 1)]


def test_sub_list_no_auto_default_with_embedded(media_root):
    """有内嵌字幕时外挂不参与默认（保持原有语义）。"""
    _touch(media_root, "x/lone.mkv")
    _touch(media_root, "x/lone.srt")
    m = {"id": 2, "file_path": "x/lone.mkv"}
    subs = _sub_list(m, {"subs": [{"codec": "subrip", "index": 0, "default": 0}]})
    assert [s["default"] for s in subs if s["source"] == "sidecar"] == [0]


def test_sub_list_chi_sidecar_preferred_over_other_text(media_root):
    """有中文外挂时优先它，不使用兜底（英文外挂不抢默认）。"""
    _touch(media_root, "x/both.mkv")
    _touch(media_root, "x/both.eng.srt")
    _touch(media_root, "x/both.chs.srt")
    m = {"id": 3, "file_path": "x/both.mkv"}
    subs = _sub_list(m, {"subs": []})
    defaults = [s["sidecar"] for s in subs if s["default"] == 1]
    assert len(defaults) == 1 and defaults[0].endswith("both.chs.srt")
