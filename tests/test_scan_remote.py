"""Batch A：远程直读库扫描（fake SMB）——遍历/入库/离线不删/写降级/重扫。"""
import pytest
from fastapi.testclient import TestClient

from app import library_paths, library_roots, scanner, storage, store
from app.main import app
from app.storage import smb
from _smb_fake import FakeSmbClient


def _make_lib(tmp_path, monkeypatch, kind="movie"):
    root = tmp_path / "share"
    root.mkdir()
    fake = FakeSmbClient(root)
    monkeypatch.setattr(smb, "smbclient", fake)
    monkeypatch.setenv("SMB_DRIVER", "direct")
    lib = store.create_library(name=f"smbscan-{tmp_path.name}", kind=kind, source="smb",
                               smb={"host": "nas", "share": "video",
                                    "username": "u", "password": "pw"})
    library_paths.invalidate_cache()
    smb.invalidate()
    return lib, root, fake


@pytest.fixture()
def smb_movie_lib(tmp_path, monkeypatch):
    lib, root, fake = _make_lib(tmp_path, monkeypatch)
    monkeypatch.setattr(scanner.scan, "search_with_fallback",
                        lambda title, year: (None, title, False))
    yield lib, root, fake
    store.delete_media_library(lib["media_library_id"])
    library_paths.invalidate_cache()
    smb.invalidate()


def test_iter_tree_skips_hidden_and_system(smb_movie_lib):
    lib, root, _fake = smb_movie_lib
    (root / "电影").mkdir()
    (root / "电影" / "a.mkv").write_bytes(b"x")
    (root / ".hidden").mkdir()
    (root / ".hidden" / "h.mkv").write_bytes(b"x")
    (root / "@eaDir").mkdir()
    (root / "@eaDir" / "t.mkv").write_bytes(b"x")
    (root / "b.txt").write_bytes(b"x")
    backend = storage.backend_for(lib["id"])
    rels = {e.rel for e in backend.iter_tree("", skip_dirs=scanner.scan_skip_dirs())}
    assert "电影/a.mkv" in rels and "b.txt" in rels
    assert not any(".hidden" in r or "@eaDir" in r for r in rels)


def test_scan_movie_library_direct(smb_movie_lib):
    lib, root, _fake = smb_movie_lib
    d = root / "RemoteOnly Movie (2001)"
    d.mkdir()
    (d / "RemoteOnly Movie (2001).mkv").write_bytes(b"x")
    out = scanner.scan_all(library_id=lib["id"])
    assert [r["status"] for r in out] == ["no_match"]
    rel = "RemoteOnly Movie (2001)/RemoteOnly Movie (2001).mkv"
    row = store.get_by_path(rel, library_id=lib["id"])
    assert row is not None
    # 远程直读不写 NFO/图片（挂载点不存在，绝不凭空造目录）
    assert not list(d.glob("*.nfo")) and not (d / "poster.jpg").exists()


def test_scan_file_matched_remote_writes_nfo_only(smb_movie_lib):
    """D1 起：远程可写库匹配成功会经 backend 写 NFO；默认 artwork_mode=nfo 不写海报。"""
    lib, root, _fake = smb_movie_lib
    store.upsert_tmdb_cache(778899, {"title": "Remote Cached", "year": 2001,
                                     "media_type": "movie"}, {}, "")
    rel = "Remote Cached (2001).mkv"
    (root / rel).write_bytes(b"x")
    r = scanner.scan_file(storage.backend_for(lib["id"]), rel, tmdb_hint=778899)
    assert r["status"] == "ok" and r.get("nfo") is True
    assert (root / "movie.nfo").is_file()
    assert not (root / "poster.jpg").exists()
    assert store.get_by_path(rel, library_id=lib["id"])["tmdb_id"] == 778899


def test_scan_offline_skips_and_keeps_rows(smb_movie_lib):
    lib, root, fake = smb_movie_lib
    (root / "Keep (2000).mkv").write_bytes(b"x")
    mid = store.upsert_movie_by_path("Keep (2000).mkv", library_id=lib["id"])
    store.upsert_extra("Keep (2000)-trailer.mkv", mid, "trailer", library_id=lib["id"])
    fake.offline = True
    out = scanner.scan_all(library_id=lib["id"])
    assert [r["status"] for r in out] == ["library_offline"]
    # 离线 ≠ 删除：影片行与花絮行都保留
    assert store.get_by_path("Keep (2000).mkv", library_id=lib["id"]) is not None
    extras = [e for e in store.list_all_extras() if e["library_id"] == lib["id"]]
    assert any(e["file_path"] == "Keep (2000)-trailer.mkv" for e in extras)


def test_scan_disconnect_after_snapshot_keeps_rows_even_with_cached_root(smb_movie_lib):
    lib, _, fake = smb_movie_lib
    library_roots.checked(storage.backend_for(lib["id"]))
    rel = "Keep while offline (2000).mkv"
    movie_id = store.upsert_movie_by_path(rel, library_id=lib["id"])
    store.save_progress(movie_id, 50, 100)

    def disconnect_after_snapshot(done, total):
        assert done == total == 0
        fake.offline = True

    result = scanner.scan_all(library_id=lib["id"], progress_cb=disconnect_after_snapshot)
    assert any(row["status"] == "library_offline" for row in result)
    assert store.get_movie(movie_id)
    assert store.get_progress(movie_id)["position"] == 50


def test_scan_tv_library_direct(tmp_path, monkeypatch):
    lib, root, _fake = _make_lib(tmp_path, monkeypatch, kind="tv")
    try:
        d = root / "Remote Show (2020)" / "Season 01"
        d.mkdir(parents=True)
        (d / "Remote.Show.S01E01.mkv").write_bytes(b"x")
        out = scanner.scan_all(library_id=lib["id"])
        assert [r["status"] for r in out] == ["tv_ok"]
        shows = store.list_shows(lib["id"])
        assert shows and shows[0]["title"] == "Remote Show"
        assert store.count_episodes(lib["id"]) == 1
    finally:
        store.delete_media_library(lib["media_library_id"])
        library_paths.invalidate_cache()
        smb.invalidate()


def test_sidecar_subtitles_fs(smb_movie_lib):
    lib, root, _fake = smb_movie_lib
    (root / "subs").mkdir()
    (root / "Side (2001).mkv").write_bytes(b"x")
    (root / "Side (2001).chs.srt").write_bytes(b"1\n")
    (root / "subs" / "Side (2001).ass").write_bytes(b"[Script Info]")
    items = scanner.sidecar_subtitles_fs(storage.backend_for(lib["id"]),
                                         "Side (2001).mkv")
    by_name = {i["name"]: i for i in items}
    assert by_name["Side (2001).chs.srt"]["codec"] == "srt"
    assert by_name["Side (2001).chs.srt"]["suffix"] == "chs"
    assert by_name["Side (2001).ass"]["codec"] == "ass"
    assert by_name["Side (2001).ass"]["rel"] == "subs/Side (2001).ass"


def test_rescan_remote_movie_api(smb_movie_lib):
    lib, root, _fake = smb_movie_lib
    rel = "Rescan Me (2002).mkv"
    (root / rel).write_bytes(b"x")
    mid = store.upsert_movie_by_path(rel, library_id=lib["id"])
    client = TestClient(app)
    r = client.post(f"/api/movies/{mid}/rescan")
    assert r.status_code == 200, r.text
    assert r.json()["status"] in ("no_match", "ok", "ok_needs_review")


def test_scan_no_per_file_stat(smb_movie_lib, monkeypatch):
    """P2 扫描热路径：iter_tree 已带 size/mtime，不得再为每个视频文件 stat。"""
    lib, root, fake = smb_movie_lib
    d = root / "Many (2001)"
    d.mkdir()
    for i in range(5):
        (d / f"Many.part{i}.mkv").write_bytes(b"x")
    monkeypatch.setenv("SMB_META_TTL", "0")
    stat_paths = []
    original_stat = fake.stat

    def track_stat(unc, **kwargs):
        stat_paths.append(fake._p(unc))
        return original_stat(unc, **kwargs)

    monkeypatch.setattr(fake, "stat", track_stat)
    fake.counts.clear()
    scanner.scan_all(library_id=lib["id"])
    # 遍历前后及 GC 前核验库根；不得退化成逐片 stat，scandir 仍每目录一次。
    assert stat_paths == [root, root, root]
    assert fake.counts.get("scandir", 0) <= 2


def test_replaced_smb_share_keeps_progress_and_acceptance_is_explicit(smb_movie_lib):
    lib, root, _ = smb_movie_lib
    lid = lib["id"]
    (root / "Keep.mkv").write_bytes(b"original")
    movie = store.upsert_movie_by_path("Keep.mkv", library_id=lid)
    store.update_movie_meta(movie, tmdb_id=555444)
    store.save_progress(movie, 600, 1200)
    assert scanner.scan_all(library_id=lid)[0]["status"] == "skipped_cached"
    root.rename(root.with_name("archived-share"))
    root.mkdir()
    review = scanner.scan_all(library_id=lid)[0]["root_review"]
    assert store.get_progress(movie)["position"] == 600
    client = TestClient(app)
    assert client.post("/api/files/clean", json={"ids": [movie], "dry_run": False}).json()["deleted"] == 0
    assert client.post(f"/api/libraries/{lid}/accept-scan-root", json={"token": review["token"]}).status_code == 200
    assert client.post("/api/files/clean", json={"ids": [movie], "dry_run": False}).json()["deleted"] == 1
    assert (root.with_name("archived-share") / "Keep.mkv").read_bytes() == b"original"


@pytest.mark.parametrize("kind", ["movie", "extra", "episode"])
def test_first_standalone_import_binds_the_share_before_cataloguing_its_file(tmp_path, monkeypatch, kind):
    lib, root, fake = _make_lib(tmp_path, monkeypatch, kind="tv" if kind == "episode" else "movie")
    lid = lib["id"]
    assert not fake.counts, "offline SMB registration should not depend on server access"
    try:
        backend = storage.backend_for(lid)
        monkeypatch.setattr(scanner.scan, "search_with_fallback", lambda title, year: (None, title, False))
        rel, operation = {
            "movie": ("First (2001).mkv", scanner.scan_file),
            "extra": ("First/trailers/bonus.mkv", scanner.attribute_extra_file),
            "episode": ("First/Season 01/First.S01E01.mkv", scanner.scan_tv_file),
        }[kind]
        source = root / rel
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"original file")
        assert operation(backend, rel)["status"] in {"no_match", "extra_orphan", "tv_ok"}
        root.rename(root.with_name("original-share"))
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"different file at the same path")
        with pytest.raises(library_roots.RootReview):
            operation(backend, rel)
        assert scanner.scan_all(library_id=lid)[0]["root_review"]
        assert (root.with_name("original-share") / rel).read_bytes() == b"original file"
    finally:
        store.delete_media_library(lib["media_library_id"])
        library_paths.invalidate_cache()
        smb.invalidate()


def test_legacy_empty_smb_replacement_needs_only_one_directory_listing(smb_movie_lib):
    lib, _, fake = smb_movie_lib
    lid = lib["id"]
    with store._conn() as c:
        c.executemany("INSERT INTO movies(library_id,file_path) VALUES(?,?)",
                      [(lid, f"Film-{i}/old.mkv") for i in range(100)])
    fake.counts.clear()
    with pytest.raises(library_roots.RootReview):
        library_roots.checked(storage.backend_for(lid))
    assert fake.counts.get("scandir") == 1 and fake.counts.get("stat") == 2


@pytest.mark.parametrize("count", [1, 2])
def test_legacy_smb_root_check_ignores_positive_stat_and_directory_cache(smb_movie_lib, count):
    lib, root, _ = smb_movie_lib
    backend = storage.backend_for(lib["id"])
    for i in range(count):
        rel = f"original-{i}.mkv"
        (root / rel).write_bytes(b"video")
        store.upsert_movie_by_path(rel, library_id=lib["id"])
        assert not backend.stat(rel).is_dir
    assert len(backend.list("")) == count
    for original in root.iterdir():
        original.unlink()
    with pytest.raises(library_roots.RootReview):
        library_roots.checked(backend)


def test_scan_remote_generic_extras_dir_backend_aware(smb_movie_lib):
    """远程直读库 Movie/Scenes/ 归花絮（backend 感知，不再依赖 POSIX default_root）。"""
    lib, root, _fake = smb_movie_lib
    d = root / "Remote Movie (2002)"
    d.mkdir()
    (d / "Remote Movie (2002).mkv").write_bytes(b"x")
    (d / "Scenes").mkdir()
    (d / "Scenes" / "clip.mkv").write_bytes(b"x")
    out = scanner.scan_all(library_id=lib["id"])
    statuses = {r["file"]: r["status"] for r in out}
    # 归花絮（能按祖先目录名归属则 attached，否则 orphan）；绝不建 movies 行
    assert statuses.get("Remote Movie (2002)/Scenes/clip.mkv") in (
        "extra_attached", "extra_orphan")
    assert store.get_by_path("Remote Movie (2002)/Scenes/clip.mkv",
                             library_id=lib["id"]) is None


def test_relocating_smb_extra_preserves_other_movies_same_name(smb_movie_lib):
    lib, root, _ = smb_movie_lib
    backend = storage.backend_for(lib["id"])
    original = "First Film/trailers/trailer.mkv"
    other = "Second Film/trailers/trailer.mkv"
    moved = "First Film/extras/trailer.mkv"
    for rel in (original, other):
        path = root / rel
        path.parent.mkdir(parents=True)
        path.write_bytes(rel.encode())
    scanner.scan_all(library_id=lib["id"])

    def extras():
        return {e["file_path"]: e["id"] for e in store.list_all_extras() if e["library_id"] == lib["id"]}

    before = extras()
    assert before.keys() == {original, other}
    (root / moved).parent.mkdir()
    (root / original).rename(root / moved)
    scanner.attribute_extra_file(backend, moved)
    scanner.attribute_extra_file(backend, other)
    assert extras() == {moved: before[original], other: before[other]}


@pytest.mark.parametrize("with_owner", [False, True])
def test_two_smb_extras_relocated_together_keep_distinct_ids(smb_movie_lib, with_owner):
    lib, root, _ = smb_movie_lib
    expected, moves = {}, []
    for name in ("First Film", "Second Film"):
        old = f"{name}/trailers/trailer.mkv"
        new = f"{name}/extras/trailer.mkv"
        (root / old).parent.mkdir(parents=True)
        (root / old).write_bytes(name.encode())
        if with_owner:
            feature = f"{name}/{name}.mkv"
            (root / feature).write_bytes(b"feature")
            movie = store.upsert_movie_by_path(feature, library_id=lib["id"])
            store.update_movie_meta(movie, title=name)
        else:
            movie = None
        extra = store.upsert_extra(old, movie, library_id=lib["id"])
        expected[new] = extra, movie
        (root / new).parent.mkdir()
        moves.append((root / old, root / new))
    # The share has been used before the files are moved outside the application.
    library_roots.checked(storage.backend_for(lib["id"]))
    for old, new in moves:
        old.rename(new)
    scanner.scan_all(library_id=lib["id"])
    actual = {e["file_path"]: (e["id"], e["movie_id"]) for e in store.list_all_extras()
              if e["library_id"] == lib["id"]}
    assert actual == expected
