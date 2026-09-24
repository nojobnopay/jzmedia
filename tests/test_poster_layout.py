"""data/posters 功能子目录：命名单源 / 旧名映射 / 启动迁移 / 读回退。"""
import os

from app import posters, store
from app.db import POSTER_DIR, ensure_dirs


def test_subdirs_created_by_ensure_dirs():
    ensure_dirs()
    for sub in posters.SUBDIRS:
        assert os.path.isdir(os.path.join(POSTER_DIR, sub)), sub


def test_rel_builders():
    assert posters.movie_poster_rel(7) == "posters/movies/7.jpg"
    assert posters.movie_orig_rel(7) == "posters/orig/7.jpg"
    assert posters.movie_backdrop_rel(7) == "posters/backdrops/movie_7.jpg"
    assert posters.person_avatar_rel(7) == "posters/persons/7.jpg"
    assert posters.tv_poster_rel(7) == "posters/tv/7.jpg"
    assert posters.tv_backdrop_rel(7) == "posters/backdrops/tv_7.jpg"
    assert posters.tv_season_poster_rel(7, 2) == "posters/tv/7_s2.jpg"
    assert posters.episode_still_rel(9) == "posters/stills/9.jpg"


def test_legacy_rel_mapping():
    assert posters.legacy_rel("479455.jpg") == "movies/479455.jpg"
    assert posters.legacy_rel("479455_orig.jpg") == "orig/479455.jpg"
    assert posters.legacy_rel("backdrop_1.jpg") == "backdrops/movie_1.jpg"
    assert posters.legacy_rel("person_10.jpg") == "persons/10.jpg"
    assert posters.legacy_rel("tv_100.jpg") == "tv/100.jpg"
    assert posters.legacy_rel("tv_backdrop_100.jpg") == "backdrops/tv_100.jpg"
    assert posters.legacy_rel("tv_100_s1.jpg") == "tv/100_s1.jpg"
    assert posters.legacy_rel("tv_e55.jpg") == "stills/55.jpg"
    # 非图片归档目标：原样不碰
    assert posters.legacy_rel("479455_04c1dba25e40.jpg") == ""
    assert posters.legacy_rel("h632_abcdef123456.jpg") == ""
    # 已是新相对路径：按 basename 幂等（不二次嵌套）
    assert posters.legacy_rel("movies/1.jpg") == "movies/1.jpg"


def test_remap_db_value():
    assert posters.remap_db_value("movies", "poster_path", "54186.jpg") == \
        "posters/movies/54186.jpg"
    assert posters.remap_db_value("movies", "poster_path", "posters/54186.jpg") == \
        "posters/movies/54186.jpg"
    assert posters.remap_db_value("persons", "avatar", "posters/person_1.jpg") == \
        "posters/persons/1.jpg"
    assert posters.remap_db_value("tv_shows", "poster_path", "tv_100.jpg") == \
        "posters/tv/100.jpg"
    assert posters.remap_db_value("tv_shows", "backdrop_path", "tv_backdrop_100.jpg") == \
        "posters/backdrops/tv_100.jpg"
    assert posters.remap_db_value("tv_seasons", "poster_path", "tv_100_s1.jpg") == \
        "posters/tv/100_s1.jpg"
    # 已是新值 / TMDB 远端 / 占位：不动
    assert posters.remap_db_value("movies", "poster_path", "posters/movies/1.jpg") == \
        "posters/movies/1.jpg"
    assert posters.remap_db_value("movies", "poster_path", "/abc123.jpg") == "/abc123.jpg"
    assert posters.remap_db_value("persons", "avatar", "-") == "-"
    assert posters.remap_db_value("persons", "avatar", "") == ""


def test_resolve_prefers_new_and_falls_back(tmp_path):
    base = tmp_path / "p"
    (base / "movies").mkdir(parents=True)
    (base / "movies" / "1.jpg").write_bytes(b"NEW")
    (base / "2.jpg").write_bytes(b"ROOT")
    data = tmp_path / "data"
    data.mkdir()
    # 新 DATA_DIR 相对值
    assert posters.resolve(str(base), "posters/movies/1.jpg", str(data)) == \
        os.path.join(str(base), "movies", "1.jpg")
    # 新 POSTER_DIR 相对值
    assert posters.resolve(str(base), "movies/1.jpg") == \
        os.path.join(str(base), "movies", "1.jpg")
    # 旧裸文件名 → 映射到新位置
    assert posters.resolve(str(base), "1.jpg") == \
        os.path.join(str(base), "movies", "1.jpg")
    # 新位置没有、根部有旧文件 → 回退根部
    assert posters.resolve(str(base), "2.jpg") == os.path.join(str(base), "2.jpg")
    assert posters.resolve(str(base), "") == ""
    assert posters.resolve(str(base), "nope.jpg") == ""


def test_migrate_moves_files_and_idempotent(tmp_path):
    base = tmp_path / "p"
    base.mkdir()
    legacy = {"1.jpg": "movies/1.jpg", "1_orig.jpg": "orig/1.jpg",
              "backdrop_1.jpg": "backdrops/movie_1.jpg",
              "person_2.jpg": "persons/2.jpg", "tv_3.jpg": "tv/3.jpg",
              "tv_backdrop_3.jpg": "backdrops/tv_3.jpg",
              "tv_3_s1.jpg": "tv/3_s1.jpg", "tv_e9.jpg": "stills/9.jpg"}
    for name in legacy:
        (base / name).write_bytes(b"img-" + name.encode())
    (base / "keep.txt").write_bytes(b"x")
    out = posters.migrate_posters(poster_dir=str(base), data_dir=str(tmp_path))
    assert out["moved"] == len(legacy), out
    for old, new in legacy.items():
        assert not (base / old).exists(), old
        assert (base / new).read_bytes() == b"img-" + old.encode(), new
    assert (base / "keep.txt").is_file()
    # 幂等：二跑无搬迁
    out2 = posters.migrate_posters(poster_dir=str(base), data_dir=str(tmp_path))
    assert out2["moved"] == 0 and out2["reused"] == 0, out2


def test_migrate_rewrites_db_legacy_values(tmp_path):
    mid = store.upsert_movie_by_path("layout/Legacy (2020).mkv")
    store.update_movie_meta(mid, title="Legacy", year=2020, tmdb_id=990011,
                            poster_path="99001122.jpg")
    pid = store.upsert_person(99001133, "Layout Actor", avatar="person_99001133.jpg",
                              profile_tmdb_path="/x.jpg")
    try:
        empty = tmp_path / "empty"
        empty.mkdir()
        out = posters.migrate_posters(poster_dir=str(empty), data_dir=str(tmp_path))
        assert out["db_updated"] >= 2, out
        assert store.get_movie(mid)["poster_path"] == "posters/movies/99001122.jpg"
        assert store.get_person_raw(99001133)["avatar"] == "posters/persons/99001133.jpg"
        # 幂等：二跑不再改写
        out2 = posters.migrate_posters(poster_dir=str(empty), data_dir=str(tmp_path))
        assert out2["db_updated"] == 0, out2
    finally:
        store.delete_movie(mid)
        with store._lock, store._conn() as c:
            c.execute("DELETE FROM persons WHERE id=?", (pid,))
