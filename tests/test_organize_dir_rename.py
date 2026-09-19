"""归档整理「影片专属目录整目录改名」语义（2026-09 用户反馈）：
12.Monkeys.1995/ → 十二猴子 (1995)/；合集目录（周星驰.Stephen Chow）保留；
非专属/共享目录仍套一层；目录内非影片内容原名跟随。"""
import pytest
from fastapi.testclient import TestClient

from app import library_paths, store
from app.main import app
from app.routers.files import _organize
from app.routers.files.planner import _dir_owned_by_comp

client = TestClient(app)


def _mk(media_root, rel, *, title, year, tmdb=None, **meta):
    p = media_root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")
    mid = store.upsert_movie_by_path(rel)
    store.update_movie_meta(mid, title=title, year=year, tmdb_id=tmdb, **meta)
    return mid


def _plan_dir(plans, mid):
    by_id = {p["id"]: p for p in plans}
    return by_id.get(mid)


def test_own_dir_renamed_and_idempotent(media_root):
    mid = _mk(media_root, "12.Monkeys.1995/12.Monkeys.1995.mkv",
              title="十二猴子", year=1995, tmdb=99001)
    d = media_root / "12.Monkeys.1995"
    (d / "poster.jpg").write_bytes(b"p")
    (d / "movie.nfo").write_bytes(b"<movie/>")
    (d / "Sample").mkdir()
    (d / "Sample" / "sample.mkv").write_bytes(b"s")
    try:
        prev = _organize("inplace", only={mid}, dry_run=True)
        p = _plan_dir(prev["plans"], mid)
        assert p and p["kind"] == "dir"
        assert p["from"] == "12.Monkeys.1995" and p["to"] == "十二猴子 (1995)"
        assert p["files"][0]["to"] == "十二猴子 (1995)/十二猴子 (1995).mkv"
        out = _organize("inplace", only={mid}, dry_run=False)
        assert out["results"][0]["status"] == "moved", out["results"]
        new_dir = media_root / "十二猴子 (1995)"
        assert (new_dir / "十二猴子 (1995).mkv").is_file()
        assert (new_dir / "poster.jpg").is_file()          # 非影片内容原名跟随
        assert (new_dir / "Sample" / "sample.mkv").is_file()
        assert (new_dir / "movie.nfo").is_file()           # NFO 收敛后仍存在
        assert not d.exists()                              # 旧目录删除
        assert store.get_movie(mid)["file_path"] == "十二猴子 (1995)/十二猴子 (1995).mkv"
        assert store.get_movie(mid)["original_file_path"] == \
            "12.Monkeys.1995/12.Monkeys.1995.mkv"          # 审计路径不动
        again = _organize("inplace", only={mid}, dry_run=True)
        assert again["plans"] == [] and again["conflicts"] == []
    finally:
        store.delete_movie(mid)
        import shutil
        shutil.rmtree(media_root / "十二猴子 (1995)", ignore_errors=True)
        shutil.rmtree(d, ignore_errors=True)


def test_collection_parent_kept(media_root):
    mid = _mk(media_root, "周星驰.Stephen Chow/功夫.Kung.Fu.Hustle.2004/"
                          "Kung.Fu.Hustle.2004.BluRay.1080p.mkv",
              title="功夫", year=2004, tmdb=99002)
    coll = media_root / "周星驰.Stephen Chow"
    (coll / "功夫.Kung.Fu.Hustle.2004" / "cover.jpg").write_bytes(b"c")
    try:
        prev = _organize("inplace", only={mid}, dry_run=True)
        p = _plan_dir(prev["plans"], mid)
        assert p and p["kind"] == "dir"
        assert p["from"] == "周星驰.Stephen Chow/功夫.Kung.Fu.Hustle.2004"
        assert p["to"] == "周星驰.Stephen Chow/功夫 (2004)"
        out = _organize("inplace", only={mid}, dry_run=False)
        assert out["results"][0]["status"] == "moved"
        assert (coll / "功夫 (2004)" / "功夫 (2004).mkv").is_file()
        assert (coll / "功夫 (2004)" / "cover.jpg").is_file()
        assert coll.is_dir()                                # 合集目录保留
        assert not (coll / "功夫.Kung.Fu.Hustle.2004").exists()
    finally:
        store.delete_movie(mid)
        import shutil
        shutil.rmtree(coll, ignore_errors=True)


def test_shared_dir_still_nests(media_root):
    a = _mk(media_root, "待整理/Flat.One.2010.mkv", title="Flat One", year=2010, tmdb=99003)
    b = _mk(media_root, "待整理/Flat.Two.2011.mkv", title="Flat Two", year=2011, tmdb=99004)
    try:
        prev = _organize("inplace", only={a, b}, dry_run=True)
        assert all(p.get("kind") != "dir" for p in prev["plans"])
        tos = {p["id"]: p["to"] for p in prev["plans"]}
        assert tos[a] == "待整理/Flat One (2010)/Flat One (2010).mkv"
        assert tos[b] == "待整理/Flat Two (2011)/Flat Two (2011).mkv"
        out = _organize("inplace", only={a, b}, dry_run=False)
        assert [r["status"] for r in out["results"]] == ["moved", "moved"]
        assert (media_root / tos[a]).is_file() and (media_root / tos[b]).is_file()
    finally:
        store.delete_movie(a)
        store.delete_movie(b)
        import shutil
        shutil.rmtree(media_root / "待整理", ignore_errors=True)


def test_multi_version_dir_plan(media_root):
    rel = "2001.A.Space.Odyssey.1968/2001.A.Space.Odyssey.1968.mkv"
    A = _mk(media_root, rel, title="2001太空漫游", year=1968, tmdb=99005)
    B = _mk(media_root, "2001.A.Space.Odyssey.1968/2001.A.Space.Odyssey.1968.4K.mkv",
            title="2001太空漫游", year=1968, tmdb=99005)
    try:
        prev = _organize("inplace", only={A}, dry_run=True)   # 只选一个版本也整目录带走
        dirs = [p for p in prev["plans"] if p.get("kind") == "dir"]
        assert len(dirs) == 1
        p = dirs[0]
        assert p["from"] == "2001.A.Space.Odyssey.1968"
        assert p["to"] == "2001太空漫游 (1968)"
        assert sorted(p["movie_ids"]) == sorted([A, B])
        assert len(p["files"]) == 2
        out = _organize("inplace", only={A}, dry_run=False)
        assert len(out["results"]) == 1 and out["results"][0]["status"] == "moved"
        paths = [store.get_movie(A)["file_path"], store.get_movie(B)["file_path"]]
        assert all(p.startswith("2001太空漫游 (1968)/") for p in paths)
        assert all((media_root / p).is_file() for p in paths)
        assert not (media_root / "2001.A.Space.Odyssey.1968").exists()
    finally:
        store.delete_movie(A)
        store.delete_movie(B)
        import shutil
        shutil.rmtree(media_root / "2001太空漫游 (1968)", ignore_errors=True)
        shutil.rmtree(media_root / "2001.A.Space.Odyssey.1968", ignore_errors=True)


def test_target_dir_exists_conflict(media_root):
    mid = _mk(media_root, "12.Monkeys.1995/12.Monkeys.1995.mkv",
              title="十二猴子", year=1995, tmdb=99006)
    (media_root / "十二猴子 (1995)").mkdir(parents=True, exist_ok=True)
    (media_root / "十二猴子 (1995)" / "occupied.mkv").write_bytes(b"o")
    try:
        prev = _organize("inplace", only={mid}, dry_run=True)
        assert not prev["plans"]
        assert prev["conflicts"] and prev["conflicts"][0]["status"] == "conflict_disk_exists"
    finally:
        store.delete_movie(mid)
        import shutil
        shutil.rmtree(media_root / "十二猴子 (1995)", ignore_errors=True)
        shutil.rmtree(media_root / "12.Monkeys.1995", ignore_errors=True)


def test_dir_owned_heuristics():
    def comp(name, title, orig):
        return {"from": f"x.y/{name}", "m": {"title": title, "original_title": orig}}
    # 命中
    assert _dir_owned_by_comp("12.Monkeys.1995",
                              comp("12.Monkeys.1995.mkv", "十二猴子", ""))
    assert _dir_owned_by_comp("壮志凌云2.Top.Gun.Maverick.2022",
                              comp("Top.Gun.Maverick.2022.UHD.BluRay.2160p.mkv",
                                   "壮志凌云2：独行侠", "Top Gun: Maverick"))
    assert _dir_owned_by_comp("望夫成龙.Love.is.Love.1990",
                              comp("Love.is.Love.1990.BluRay.1080p.mkv",
                                   "望夫成龙", "Love Is Love"))
    assert _dir_owned_by_comp("加菲猫1.Garfield.1.2004",
                              comp("加菲猫Ⅰ.Garfield.I.2004.mkv", "加菲猫", "Garfield"))
    assert _dir_owned_by_comp("创战纪.2010",
                              comp("Tron.Legacy.2010.USA.BluRay.REMUX.mkv",
                                   "创：战纪", "TRON: Legacy"))
    # 排除
    assert not _dir_owned_by_comp("olddir", comp("Old (2020).mkv", "新标题", ""))
    assert not _dir_owned_by_comp("变形金刚系列.Transformers",
                                  comp("Transformers.Dark.Of.The.Moon.2011.mkv",
                                       "变形金刚3", "Transformers: Dark of the Moon"))
    assert not _dir_owned_by_comp("海贼王剧场版",
                                  comp("One.Piece.Strong.World.2009.mkv",
                                       "海贼王：强者天下", "ONE PIECE FILM STRONG WORLD"))
    assert not _dir_owned_by_comp("周星驰.Stephen Chow",
                                  comp("Kung.Fu.Hustle.2004.mkv", "功夫", "Kung Fu Hustle"))


def test_relocate_moves_whole_dir(media_root):
    mid = _mk(media_root, "待整理/12.Monkeys.1995/12.Monkeys.1995.mkv",
              title="十二猴子", year=1995, tmdb=99007)
    (media_root / "待整理" / "12.Monkeys.1995" / "Sample").mkdir(
        parents=True, exist_ok=True)
    (media_root / "待整理" / "12.Monkeys.1995" / "Sample" / "s.mkv").write_bytes(b"s")
    try:
        prev = _organize("relocate", from_prefix="待整理", to_dir="电影",
                         only={mid}, dry_run=True)
        p = _plan_dir(prev["plans"], mid)
        assert p and p["kind"] == "dir"
        assert p["from"] == "待整理/12.Monkeys.1995"
        assert p["to"] == "电影/十二猴子 (1995)"
        out = _organize("relocate", from_prefix="待整理", to_dir="电影",
                        only={mid}, dry_run=False)
        assert out["results"][0]["status"] == "moved"
        assert (media_root / "电影/十二猴子 (1995)/十二猴子 (1995).mkv").is_file()
        assert (media_root / "电影/十二猴子 (1995)/Sample/s.mkv").is_file()
        assert not (media_root / "待整理" / "12.Monkeys.1995").exists()
    finally:
        store.delete_movie(mid)
        import shutil
        shutil.rmtree(media_root / "电影", ignore_errors=True)
        shutil.rmtree(media_root / "待整理", ignore_errors=True)


def test_hint_owned_dir_prefers_inplace(media_root):
    """NAS 库根就是 Movies：顶层目录是片目录名，归档推荐应是就地整目录改名，不再搬迁。"""
    mid = _mk(media_root, "12.Monkeys.1995/12.Monkeys.1995.mkv",
              title="十二猴子", year=1995, tmdb=99009)
    try:
        d = client.get(f"/api/movies/{mid}/organize-hint").json()
        assert d["needs"] is True and d["params"]["mode"] == "inplace"
        assert d["plans"][0]["kind"] == "dir"
        assert d["plans"][0]["to"] == "十二猴子 (1995)"
    finally:
        store.delete_movie(mid)


def test_prefix_repath_single_slash(media_root):
    """目录改名后前缀改库不得产生双斜杠（子目录花絮行回归）。"""
    mid = _mk(media_root, "Old.Name.2020/Old.Name.2020.mkv",
              title="新名字", year=2020, tmdb=99010)
    d = media_root / "Old.Name.2020"
    (d / "misc").mkdir()
    (d / "misc" / "notes.txt").write_bytes(b"n")
    store.upsert_extra("Old.Name.2020/misc/notes.txt", mid, "extra")
    try:
        out = _organize("inplace", only={mid}, dry_run=False)
        assert out["results"][0]["status"] == "moved"
        assert store.get_movie(mid)["file_path"] == "新名字 (2020)/新名字 (2020).mkv"
        extras = store.list_extras_by_movie(mid)
        assert extras and extras[0]["file_path"] == "新名字 (2020)/misc/notes.txt"
    finally:
        store.delete_extra_by_path("新名字 (2020)/misc/notes.txt")
        store.delete_movie(mid)
        import shutil
        shutil.rmtree(media_root / "新名字 (2020)", ignore_errors=True)
        shutil.rmtree(d, ignore_errors=True)


def test_collection_range_dir_kept(media_root):
    """合集名含数字区间（惊声尖笑.Scary.Movie.1-5.2000-2013）：1/5 部不得因年份/
    音轨数字（DTS-HD.MA.5.1）撞上合集名而越界改名到库根（用户反馈回归）。"""
    coll = "惊声尖笑.Scary.Movie.1-5.2000-2013"
    parts = [
        (1, "惊声尖笑", 2000, 4247, "Scary.Movie.2000"),
        (2, "惊声尖笑2", 2001, 4248, "Scary.Movie.2.2001"),
        (3, "惊声尖笑3", 2003, 4256, "Scary.Movie.3.2003"),
        (4, "惊声尖笑4", 2006, 4257, "Scary.Movie.4.2006"),
        (5, "惊声尖笑5", 2013, 4258, "Scary.Movie.5.2013"),
    ]
    ids = []
    try:
        for _n, title, year, tmdb, sub in parts:
            rel = f"{coll}/{sub}/{sub}.BluRay.1080p.DTS-HD.MA.5.1.x265.10bit-ALT.mkv"
            ids.append(_mk(media_root, rel, title=title, year=year, tmdb=tmdb))
        prev = _organize("inplace", only=set(ids), dry_run=True)
        dirs = [p for p in prev["plans"] if p.get("kind") == "dir"]
        assert len(dirs) == 5, prev["plans"]
        for p in dirs:
            assert p["to"].startswith(coll + "/"), p["to"]   # 全部留在合集内
        tos = sorted(p["to"] for p in dirs)
        assert tos[0] == f"{coll}/惊声尖笑 (2000)"
        assert tos[-1] == f"{coll}/惊声尖笑5 (2013)"
    finally:
        for mid in ids:
            store.delete_movie(mid)
        import shutil
        shutil.rmtree(media_root / coll, ignore_errors=True)


def test_collection_shared_words_kept(media_root):
    """合集与片名共用词（黑衣人.Men.in.Black）：不得把合集当某一部的专属目录上跳。"""
    coll = "黑衣人.Men.in.Black"
    rows = [
        ("黑衣人.Men.in.Black.1997", "黑衣人", 1997, 607),
        ("黑衣人2.Men.in.Black.II.2002", "黑衣人2", 2002, 608),
    ]
    ids = []
    try:
        for sub, title, year, tmdb in rows:
            ids.append(_mk(media_root, f"{coll}/{sub}/{sub}.1080p.BluRay.mkv",
                           title=title, year=year, tmdb=tmdb))
        prev = _organize("inplace", only=set(ids), dry_run=True)
        dirs = [p for p in prev["plans"] if p.get("kind") == "dir"]
        assert len(dirs) == 2
        assert all(p["to"].startswith(coll + "/") for p in dirs), [p["to"] for p in dirs]
    finally:
        for mid in ids:
            store.delete_movie(mid)
        import shutil
        shutil.rmtree(media_root / coll, ignore_errors=True)


def test_mixed_parent_dir_not_renamed(media_root):
    """影片目录里混放另一部片的子目录：父目录不得被整目录改名连坐搬走。"""
    parent = "12.Monkeys.1995"
    a = _mk(media_root, f"{parent}/12.Monkeys.1995.mkv",
            title="十二猴子", year=1995, tmdb=99020)
    b = _mk(media_root, f"{parent}/Other.Movie.2000/Other.Movie.2000.mkv",
            title="别的片", year=2000, tmdb=99021)
    try:
        prev = _organize("inplace", only={a, b}, dry_run=True)
        assert not [p for p in prev["plans"]
                    if p.get("kind") == "dir" and p["from"] == parent]
        out = _organize("inplace", only={a, b}, dry_run=False)
        assert all(r["status"] == "moved" for r in out["results"]), out["results"]
        assert (media_root / parent).is_dir()          # 父目录保留
        assert (media_root / parent / "十二猴子 (1995)" / "十二猴子 (1995).mkv").is_file()
        assert (media_root / parent / "别的片 (2000)" / "别的片 (2000).mkv").is_file()
        assert store.get_movie(b)["file_path"] == \
            f"{parent}/别的片 (2000)/别的片 (2000).mkv"
    finally:
        store.delete_movie(a)
        store.delete_movie(b)
        import shutil
        shutil.rmtree(media_root / parent, ignore_errors=True)


def test_remote_dir_rename_with_extras(smb_lib):
    lib, root, _fake = smb_lib
    rel = "12.Monkeys.1995/12.Monkeys.1995.mkv"
    (root / "12.Monkeys.1995").mkdir()
    (root / rel).write_bytes(b"x")
    (root / "12.Monkeys.1995" / "12.Monkeys.1995-trailer.mkv").write_bytes(b"t")
    (root / "12.Monkeys.1995" / "封面").mkdir()
    (root / "12.Monkeys.1995" / "封面" / "c.jpg").write_bytes(b"c")
    mid = store.upsert_movie_by_path(rel, library_id=lib["id"])
    store.update_movie_meta(mid, title="十二猴子", year=1995, tmdb_id=99008)
    store.upsert_extra("12.Monkeys.1995/12.Monkeys.1995-trailer.mkv", mid,
                       "trailer", library_id=lib["id"])
    try:
        prev = _organize("inplace", only={mid}, dry_run=True, library_id=lib["id"])
        p = _plan_dir(prev["plans"], mid)
        assert p and p["kind"] == "dir" and p["to"] == "十二猴子 (1995)"
        out = _organize("inplace", only={mid}, dry_run=False, library_id=lib["id"])
        assert out["results"][0]["status"] == "moved", out["results"]
        nd = root / "十二猴子 (1995)"
        assert (nd / "十二猴子 (1995).mkv").is_file()
        assert (nd / "封面" / "c.jpg").is_file()
        # 同茎花絮跟随改名但留在原地（不归位进 extras/）；子目录内容原名原位置
        assert (nd / "十二猴子 (1995)-trailer.mkv").is_file()
        assert not (nd / "extras").exists()
        assert store.get_movie(mid)["file_path"] == "十二猴子 (1995)/十二猴子 (1995).mkv"
        extras = store.list_extras_by_movie(mid)
        assert len(extras) == 1 and extras[0]["file_path"] == \
            "十二猴子 (1995)/十二猴子 (1995)-trailer.mkv"
        assert not (root / "12.Monkeys.1995").exists()
    finally:
        store.delete_movie(mid)
        store.delete_extra_by_path("12.Monkeys.1995/12.Monkeys.1995-trailer.mkv",
                                   library_id=lib["id"])
        store.delete_extra_by_path("十二猴子 (1995)/十二猴子 (1995)-trailer.mkv",
                                   library_id=lib["id"])


@pytest.fixture()
def smb_lib(tmp_path, monkeypatch):
    from app.storage import smb
    from _smb_fake import FakeSmbClient
    root = tmp_path / "share"
    root.mkdir()
    fake = FakeSmbClient(root)
    monkeypatch.setattr(smb, "smbclient", fake)
    monkeypatch.setenv("SMB_DRIVER", "direct")
    lib = store.create_library(name=f"dirorg-{tmp_path.name}", kind="movie",
                               source="smb",
                               smb={"host": "nas", "share": "video",
                                    "username": "u", "password": "pw"})
    library_paths.invalidate_cache()
    smb.invalidate()
    yield lib, root, fake
    store.delete_library(lib["id"])
    library_paths.invalidate_cache()
    smb.invalidate()
