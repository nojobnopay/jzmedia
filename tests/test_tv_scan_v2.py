"""T1：TV 扫描器 v2 集成（绝对集号/子剧拆分/特典/花絮/增量/GC/目录提示）。"""
import pytest

from app import library_paths, scanner, store


@pytest.fixture()
def tv_lib(tmp_path):
    root = tmp_path / "tvlib"
    root.mkdir()
    lib = store.create_library(name=f"tv2-{tmp_path.name}", kind="tv", path=str(root))
    library_paths.invalidate_cache()
    yield lib, root
    store.delete_media_library(lib["media_library_id"])
    library_paths.invalidate_cache()


def _touch(root, rel):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")


def _scan(lib, **kw):
    return scanner.scan_all(library_id=lib["id"], **kw)


def test_absolute_numbering_flat_root(tv_lib):
    lib, root = tv_lib
    _touch(root, "钢炼/21.mp4")
    _touch(root, "钢炼/01.mp4")
    res = _scan(lib)
    assert [r["status"] for r in res] == ["tv_ok", "tv_ok"]
    show = store.list_shows(lib["id"])[0]
    assert show["title"] == "钢炼"
    eps = store.get_show(show["id"])["episodes"]
    assert [(e["season"], e["episode"], e["absolute_number"]) for e in eps] == \
        [(1, 1, 1), (1, 21, 21)]


def test_rmvb_and_season_dir_suffix(tv_lib):
    lib, root = tv_lib
    _touch(root, "鬼灭/S01/1 残酷.rmvb")
    _touch(root, "鬼灭/S02 无限列车篇TV版/1 炎柱.mp4")
    res = _scan(lib)
    assert all(r["status"] == "tv_ok" for r in res)
    show = store.list_shows(lib["id"])[0]
    detail = store.get_show(show["id"])
    assert {e["season"] for e in detail["episodes"]} == {1, 2}


def test_year_not_season(tv_lib):
    lib, root = tv_lib
    _touch(root, "武林外传/武林外传.My Own Swordsman.2006.EP01.DVDRip.mkv")
    _scan(lib)
    eps = store.get_show(store.list_shows(lib["id"])[0]["id"])["episodes"]
    assert eps[0]["season"] == 1 and eps[0]["episode"] == 1


def test_multi_episode_range(tv_lib):
    lib, root = tv_lib
    _touch(root, "Usavich/Usavich.S00E01-E13.WEB.1080p.mkv")
    _scan(lib)
    eps = store.get_show(store.list_shows(lib["id"])[0]["id"])["episodes"]
    assert (eps[0]["season"], eps[0]["episode"], eps[0]["episode_end"]) == (0, 1, 13)


def test_special_sequential_numbering(tv_lib):
    lib, root = tv_lib
    _touch(root, "Legal.High/Legal.High.SP.2013.BluRay.mkv")
    _touch(root, "Legal.High/Legal.High.SP2.2014.BluRay.mkv")
    _scan(lib)
    eps = store.get_show(store.list_shows(lib["id"])[0]["id"])["episodes"]
    got = sorted((e["season"], e["episode"]) for e in eps)
    assert got == [(0, 1), (0, 2)]


def test_subshow_split(tv_lib):
    lib, root = tv_lib
    _touch(root, "七龙珠/七龙珠.Z/001.mkv")
    _touch(root, "七龙珠/七龙珠.GT/01.mkv")
    _scan(lib)
    titles = sorted(s["title"] for s in store.list_shows(lib["id"]))
    assert titles == ["七龙珠 GT", "七龙珠 Z"]
    assert store.count_episodes(lib["id"]) == 2


def test_tv_movie_and_extras_registered(tv_lib):
    """T4：剧库内电影/花絮登记为剧花絮（show_id），样片跳过。"""
    lib, root = tv_lib
    _touch(root, "Inuyasha/Season 01/Inuyasha.E001.rmvb")
    _touch(root, "Inuyasha/Movies/Inuyasha.Movie.1.rmvb")
    _touch(root, "Show/Behind The Scene/gag.mkv")
    _touch(root, "Show/Season 01/Show.S01E01.mkv")
    res = _scan(lib)
    statuses = [r["status"] for r in res]
    assert statuses.count("tv_ok") == 2
    assert statuses.count("extra_attached") == 2
    inu = next(s for s in store.list_shows(lib["id"]) if s["title"] == "Inuyasha")
    ex = store.list_extras_by_show(inu["id"])
    assert [x["kind"] for x in ex] == ["movie"] and ex[0]["show_id"] == inu["id"]
    show = next(s for s in store.list_shows(lib["id"]) if s["title"] == "Show")
    fx = store.list_extras_by_show(show["id"])
    assert [x["kind"] for x in fx] == ["behindthescenes"]


def test_incremental_and_gc(tv_lib):
    lib, root = tv_lib
    _touch(root, "Show/Season 01/Show.S01E01.mkv")
    _touch(root, "Show/Season 01/Show.S01E02.mkv")
    _scan(lib)
    assert store.count_episodes(lib["id"]) == 2
    res = _scan(lib)
    assert all(r["status"] == "skipped_unchanged" for r in res)
    # 删一个文件 → 重扫 GC 掉该集，剧保留
    (root / "Show/Season 01/Show.S01E02.mkv").unlink()
    res = _scan(lib)
    assert store.count_episodes(lib["id"]) == 1
    assert store.count_shows(lib["id"]) == 1
    # 整剧目录删除 → 剧行也清掉
    (root / "Show/Season 01/Show.S01E01.mkv").unlink()
    _scan(lib)
    assert store.count_episodes(lib["id"]) == 0
    assert store.count_shows(lib["id"]) == 0


def test_force_reparses(tv_lib):
    lib, root = tv_lib
    _touch(root, "Show/Season 01/Show.S01E01.mkv")
    _scan(lib)
    res = _scan(lib, force=True)
    assert [r["status"] for r in res] == ["tv_ok"]


def test_tmdb_cache_movie_tv_same_id_coexist():
    """v21 复合主键：同一数值 tmdb_id 的电影/剧集缓存互不覆盖。"""
    tid = 990000123
    meta = {"title": "Twin", "original_title": "", "year": 2020, "overview": "",
            "imdb_id": "", "tmdb_rating": 7.0, "genres": [], "genre_ids": [],
            "origin_country": "", "origin_countries": [], "original_language": "en",
            "region": "US"}
    assert store.upsert_tmdb_cache(tid, dict(meta, title="Movie Side"),
                                   media_type="movie") is True
    assert store.upsert_tmdb_cache(tid, dict(meta, title="TV Side"),
                                   media_type="tv") is True
    assert store.get_tmdb_cached(tid)["title"] == "Movie Side"
    assert store.get_tmdb_cached(tid, "tv")["title"] == "TV Side"
    assert store.upsert_tmdb_cache(tid, dict(meta, title="Movie Side"),
                                   media_type="movie") is False


def test_dir_hints_bind_tmdb(tv_lib):
    lib, root = tv_lib
    _touch(root, "The Office (2001) {tmdb-2996}/Season 01/Office.S01E01.mkv")
    _scan(lib)
    show = store.list_shows(lib["id"])[0]
    assert show["tmdb_id"] == 2996
    assert show["match_source"] == "hint"
    assert show["year"] == 2001
