"""v24：整理审计（organize_moves）+ 撤销整理（plan_restore/execute_restore）。

覆盖：往返还原（磁盘/DB/scan_state）、子集还原、冲突不执行、legacy 回填（从 scan_state
配对 v24 之前的移动）、批次历史与 undone 标记。
"""
import errno
import os
from pathlib import Path

import pytest

from app import fsutil, library_paths, scanner, store
from app.scanner import tv_organize
from app.store import _base


@pytest.fixture()
def tv_lib(tmp_path):
    root = tmp_path / "tvlib"
    root.mkdir()
    lib = store.create_library(name=f"tvundo-{tmp_path.name}", kind="tv", path=str(root))
    library_paths.invalidate_cache()
    yield lib, root
    store.delete_media_library(lib["media_library_id"])
    library_paths.invalidate_cache()


def _touch(root, rel, data=b"x"):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def test_audit_and_undo_roundtrip(tv_lib):
    lib, root = tv_lib
    _touch(root, "测试剧.2020/Release/Season 01/S01E01.mkv")
    _touch(root, "测试剧.2020/Release/Season 02/S02E01.mkv")
    _touch(root, "测试剧.2020/Loose.S01E10.mkv")
    _touch(root, "测试剧.2020/[OVA]/测试剧.OVA.01.mkv")
    _touch(root, "测试剧.2020/Featurettes/Deleted Scenes/cut.mkv")
    _touch(root, "测试剧.2020/Behind The Scene/intro.mkv")
    scanner.scan_all(library_id=lib["id"])
    show = store.list_shows(lib["id"])[0]
    store.update_show_meta(show["id"], title="测试剧", year=2020, tmdb_id=999001)
    for e in store.list_episodes(show["id"]):
        store.update_episode_meta(
            e["id"], tmdb_episode_id=900000 + int(e["episode"]) * 10 + int(e["season"]),
            title=f"第{int(e['episode'])}集")
    plan = tv_organize.plan_tv_organize(library_ids=[lib["id"]])
    res = tv_organize.execute_tv_organize(plan["plans"])
    assert res["failed"] == 0 and res["batch_id"], res
    assert plan["total"] == res["moved"] + res["renamed"], (plan["total"], res)
    rows = store.list_organize_moves(batch_id=res["batch_id"])
    moved_rows = [r for r in rows if r["kind"] != "rmdir"]      # rmdir 只在审计里
    assert len(moved_rows) == plan["total"] > 0
    assert all(int(r["undone_at"] or 0) == 0 for r in rows)
    # 整理已生效：剧根/季目录/特典/花絮/正片改名
    assert (root / "测试剧 (2020)/Season 01/测试剧-S01E01-第1集.mkv").is_file()
    assert (root / "测试剧 (2020)/Season 02/测试剧-S02E01-第1集.mkv").is_file()
    assert (root / "测试剧 (2020)/Season 01/测试剧-S01E10-第10集.mkv").is_file()
    assert (root / "测试剧 (2020)/Season 00/测试剧-S00E01-第1集.mkv").is_file()
    assert (root / "测试剧 (2020)/Deleted Scenes/cut.mkv").is_file()
    assert (root / "测试剧 (2020)/Behind The Scenes/intro.mkv").is_file()
    assert not (root / "测试剧.2020").exists()

    # 撤销：预览 → 执行（含空目录重建，总数=审计行数）
    rplan = tv_organize.plan_restore(batch_id=res["batch_id"])
    assert rplan["total"] == len(rows) and rplan["conflicts"] == 0, rplan
    rres = tv_organize.execute_restore(rplan["plans"])
    assert rres["failed"] == 0 and rres["restored"] == rplan["total"], rres
    # 磁盘/DB 完全回到整理前
    assert (root / "测试剧.2020/Release/Season 01/S01E01.mkv").is_file()
    assert (root / "测试剧.2020/Release/Season 02/S02E01.mkv").is_file()
    assert (root / "测试剧.2020/Loose.S01E10.mkv").is_file()
    assert (root / "测试剧.2020/[OVA]/测试剧.OVA.01.mkv").is_file()
    assert (root / "测试剧.2020/Featurettes/Deleted Scenes/cut.mkv").is_file()
    assert (root / "测试剧.2020/Behind The Scene/intro.mkv").is_file()
    assert not (root / "测试剧 (2020)").exists()
    show = store.get_show(show["id"])
    eps = sorted(e["file_path"] for e in store.list_episodes(show["id"]))
    assert eps == ["测试剧.2020/Loose.S01E10.mkv",
                   "测试剧.2020/Release/Season 01/S01E01.mkv",
                   "测试剧.2020/Release/Season 02/S02E01.mkv",
                   "测试剧.2020/[OVA]/测试剧.OVA.01.mkv"]
    paths = [x["file_path"] for x in store.list_extras_by_show(show["id"])]
    assert "测试剧.2020/Behind The Scene/intro.mkv" in paths
    with _base._lock, _base._conn() as c:
        stale = [r["file_path"] for r in c.execute(
            "SELECT file_path FROM scan_state WHERE library_id=? AND"
            " (file_path LIKE '测试剧 (2020)/%'"
            "  OR file_path LIKE '测试剧.2020/Season %')", (lib["id"],))]
    assert stale == []
    # 原批次已标记撤销；再撤销无可还原
    assert all(int(r["undone_at"] or 0) > 0
               for r in store.list_organize_moves(batch_id=res["batch_id"],
                                                  include_undone=True))
    again = tv_organize.plan_restore(batch_id=res["batch_id"])
    assert again["total"] == 0 and again["conflicts"] == 0


def test_restore_subset_by_kinds_keeps_episode_moves(tv_lib):
    lib, root = tv_lib
    _touch(root, "Show (2020)/Loose.E01.mkv")
    _touch(root, "Show (2020)/Featurettes/Deleted Scenes/cut.mkv")
    scanner.scan_all(library_id=lib["id"])
    plan = tv_organize.plan_tv_organize(library_ids=[lib["id"]])
    res = tv_organize.execute_tv_organize(plan["plans"])
    rplan = tv_organize.plan_restore(batch_id=res["batch_id"], kinds=["extra"])
    assert rplan["total"] >= 1 and rplan["conflicts"] == 0
    rres = tv_organize.execute_restore(rplan["plans"])
    assert rres["failed"] == 0
    # 花絮还原，补 Season 保留
    assert (root / "Show (2020)/Featurettes/Deleted Scenes/cut.mkv").is_file()
    assert (root / "Show (2020)/Season 01/Loose.E01.mkv").is_file()
    remaining = tv_organize.plan_restore(batch_id=res["batch_id"], kinds=["extra"])
    assert remaining["total"] == 0


def test_restore_conflict_source_missing(tv_lib):
    lib, root = tv_lib
    _touch(root, "Show (2020)/Loose.E01.mkv")
    scanner.scan_all(library_id=lib["id"])
    plan = tv_organize.plan_tv_organize(library_ids=[lib["id"]], actions=("season",))
    res = tv_organize.execute_tv_organize(plan["plans"])
    # 手工把已整理文件移走 → 还原时报「源不存在」，不执行
    os.replace(root / "Show (2020)/Season 01/Loose.E01.mkv",
               root / "Show (2020)/elsewhere.mkv")
    rplan = tv_organize.plan_restore(batch_id=res["batch_id"])
    assert rplan["total"] == 0 and rplan["conflicts"] == 1
    assert "源不存在" in rplan["plans"][0]["conflicts"][0]["reason"]
    rres = tv_organize.execute_restore(rplan["plans"])
    assert rres["restored"] == 0 and rres["failed"] == 0


def _organized_episode(library, *, rename_root=True):
    lib, root = library
    original = "Demo.2020/Loose.S01E01.mkv"
    _touch(root, original, data=b"actual episode")
    sid = store.upsert_show(lib["id"], "Demo", 2020)
    store.update_show_meta(sid, title="Demo", year=2020, tmdb_id=999001)
    eid = store.upsert_episode(sid, lib["id"], original, 1, 1)
    actions = ("season", "root") if rename_root else ("season",)
    plan = tv_organize.plan_tv_organize(library_ids=[lib["id"]], actions=actions)
    result = tv_organize.execute_tv_organize(plan["plans"])
    assert result["failed"] == 0 and result["moved"] == 1
    return result["batch_id"], eid


@pytest.fixture(params=["local", "smb"])
def organize_target(request, tv_lib, tmp_path, monkeypatch):
    if request.param == "local":
        yield tv_lib
        return
    from app.storage import smb
    from _smb_fake import FakeSmbClient
    root = tmp_path / "fake-share"
    root.mkdir()
    monkeypatch.setattr(smb, "smbclient", FakeSmbClient(root))
    monkeypatch.setenv("SMB_DRIVER", "direct")
    lib = store.create_library("undo-smb-" + tmp_path.name, kind="tv", source="smb",
                               smb={"host": "fake-undo", "share": "tv"}, artwork_mode="none")
    library_paths.invalidate_cache()
    smb.invalidate()
    yield lib, root
    store.delete_media_library(lib["media_library_id"])
    library_paths.invalidate_cache()
    smb.invalidate()


def test_restore_only_episodes_follows_unselected_renames_and_ignores_reused_old_path(organize_target):
    _lib, root = organize_target
    batch, eid = _organized_episode(organize_target)
    other = _touch(root, "Demo.2020/Season 01/Loose.S01E01.mkv", b"other import")
    rplan = tv_organize.plan_restore(batch_id=batch, kinds=["episode"])
    assert (rplan["total"], rplan["conflicts"]) == (1, 0)
    move = rplan["plans"][0]["moves"][0]
    assert (move["from"], move["to"]) == ("Demo (2020)/Season 01/Loose.S01E01.mkv", "Demo (2020)/Loose.S01E01.mkv")
    assert tv_organize.execute_restore(rplan["plans"])["restored"] == 1
    assert other.read_bytes() == b"other import" and (root / move["to"]).read_bytes() == b"actual episode"
    assert store.get_episode(eid)["file_path"] == move["to"]
    assert [m["kind"] for m in store.list_organize_moves(batch_id=batch)] == ["dir"]


def test_older_batch_follows_root_renamed_in_a_later_batch(tv_lib):
    lib, root = tv_lib
    first, eid = _organized_episode(tv_lib, rename_root=False)
    root_plan = tv_organize.plan_tv_organize(library_ids=[lib["id"]], actions=("root",))
    assert tv_organize.execute_tv_organize(root_plan["plans"])["renamed"] == 1
    rplan = tv_organize.plan_restore(batch_id=first)
    assert rplan["total"] == 1
    assert tv_organize.execute_restore(rplan["plans"])["restored"] == 1
    assert (root / "Demo (2020)/Loose.S01E01.mkv").read_bytes() == b"actual episode"
    assert store.get_episode(eid)["file_path"] == "Demo (2020)/Loose.S01E01.mkv"


def test_replaced_source_and_destination_conflicts_preserve_original_audit_for_retry(tv_lib):
    _lib, root = tv_lib
    batch, eid = _organized_episode(tv_lib, rename_root=False)
    plan = tv_organize.plan_restore(batch_id=batch)
    entry = plan["plans"][0]["moves"][0]
    src = root / entry["from"]
    stat = src.stat()
    saved = root / "saved-original"
    src.rename(saved)
    src.write_bytes(b"other episode!")
    assert src.stat().st_size == stat.st_size
    os.utime(src, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert tv_organize.plan_restore(batch_id=batch)["conflicts"] == 1
    assert tv_organize.execute_restore(plan["plans"])["failed"] == 1
    assert store.get_episode(eid)["file_path"] == entry["from"]
    assert src.read_bytes() == b"other episode!" and len(store.list_organize_moves(batch_id=batch)) == 1
    src.unlink()
    saved.rename(src)
    dst = _touch(root, entry["to"], b"destination import")
    assert tv_organize.execute_restore(plan["plans"])["failed"] == 1
    assert dst.read_bytes() == b"destination import" and src.read_bytes() == b"actual episode"
    dst.unlink()
    fresh = tv_organize.plan_restore(batch_id=batch)
    assert tv_organize.execute_restore(fresh["plans"])["restored"] == 1
    assert not store.list_organize_moves(batch_id=batch)


def test_db_failure_rolls_filesystem_back_and_preserves_retry(tv_lib, monkeypatch):
    _lib, root = tv_lib
    batch, eid = _organized_episode(tv_lib, rename_root=False)
    plan = tv_organize.plan_restore(batch_id=batch)
    entry = plan["plans"][0]["moves"][0]
    from app.store import tv
    with monkeypatch.context() as patch:
        def fail(*args, **kwargs):
            raise RuntimeError("disk full")
        patch.setattr(tv, "record_organize_moves", fail)
        result = tv_organize.execute_restore(plan["plans"])
    assert result["failed"] == 1 and result["restored"] == 0
    assert store.get_episode(eid)["file_path"] == entry["from"]
    assert (root / entry["from"]).read_bytes() == b"actual episode" and not (root / entry["to"]).exists()
    assert len(store.list_organize_moves(batch_id=batch)) == 1
    assert tv_organize.execute_restore(tv_organize.plan_restore(batch_id=batch)["plans"])["restored"] == 1


def test_unverifiable_old_audit_is_not_consumed(tv_lib):
    lib, root = tv_lib
    batch, eid = _organized_episode(tv_lib, rename_root=False)
    with _base._lock, _base._conn() as c:
        c.execute("UPDATE organize_moves SET object_identity='' WHERE batch_id=?", (batch,))
    path = store.get_episode(eid)["file_path"]
    plan = tv_organize.plan_restore(batch_id=batch)
    assert plan["total"] == 0 and plan["conflicts"] == 1
    assert "旧整理记录" in plan["plans"][0]["conflicts"][0]["reason"]
    result = tv_organize.execute_restore(plan["plans"])
    assert result["skipped"] == 1 and result["restored"] == 0 and result["errors"]
    assert (root / path).read_bytes() == b"actual episode" and store.list_organize_moves(batch_id=batch)


@pytest.mark.parametrize("obj", ["file", "dir"])
def test_smb_creation_time_identifies_entries_even_without_a_file_index(obj):
    import stat
    from types import SimpleNamespace
    from app.storage.base import StorageBackend

    info = SimpleNamespace(st_mode=stat.S_IFREG if obj == "file" else stat.S_IFDIR, st_dev=3, st_ino=0,
                           st_ctime_ns=101, st_chgtime_ns=201, st_size=22, st_mtime_ns=301)
    original = StorageBackend._organize_stamp(info, obj, None)
    assert original
    info.st_chgtime_ns += 1
    assert StorageBackend._organize_stamp(info, obj, None) == original
    info.st_ctime_ns += 1
    assert StorageBackend._organize_stamp(info, obj, None) != original
    info.st_ctime_ns = 0
    assert not StorageBackend._organize_stamp(info, obj, None)


def test_no_birthtime_file_identity_checks_contents_when_inode_size_and_mtime_match(tv_lib, monkeypatch):
    lib, root = tv_lib
    monkeypatch.setattr(fsutil, "file_birthtime_ns", lambda path, info: None)
    first = _touch(root, "episode.mkv", b"old episode")
    stamp = first.stat()
    backend = tv_organize._backend(lib["id"])
    original = backend.organize_identity("episode.mkv", "file")
    first.write_bytes(b"new episode")
    os.utime(first, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    assert first.stat().st_ino == stamp.st_ino and backend.organize_identity("episode.mkv", "file") != original


def test_weak_directory_never_moves_a_replacement_but_its_episodes_remain_restorable(organize_target, monkeypatch):
    import shutil
    from types import SimpleNamespace
    lib, root = organize_target
    backend = tv_organize._backend(lib["id"])
    original_stat = type(backend)._entry_lstat
    monkeypatch.setattr(fsutil, "file_birthtime_ns", lambda path, info: None)

    def without_birth(self, path):
        info = original_stat(self, path)
        fields = {field: getattr(info, field) for field in ("st_mode", "st_ino", "st_dev", "st_size",
                                                            "st_mtime_ns", "st_ctime_ns")}
        if self.abs_path(path) is None:
            fields.update(st_ctime_ns=0, st_chgtime_ns=info.st_ctime_ns)
        return SimpleNamespace(**fields)

    monkeypatch.setattr(type(backend), "_entry_lstat", without_birth)
    batch, episode = _organized_episode(organize_target)
    path = root / store.get_episode(episode)["file_path"]
    plan = tv_organize.plan_restore(batch_id=batch)
    assert (plan["total"], plan["conflicts"]) == (1, 1)
    assert "目录创建时间" in plan["plans"][0]["conflicts"][0]["reason"]
    before = path.stat()
    path.write_bytes(b"other episode!")
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert path.stat().st_ino == before.st_ino and path.stat().st_size == before.st_size
    assert tv_organize.execute_restore(plan["plans"])["failed"] == 1
    assert path.read_bytes() == b"other episode!"
    path.write_bytes(b"actual episode")
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    clean = tv_organize.plan_restore(batch_id=batch, kinds=["episode"])
    assert tv_organize.execute_restore(clean["plans"])["restored"] == 1
    shutil.rmtree(root / "Demo (2020)")
    foreign = _touch(root, "Demo (2020)/foreign.mkv", b"new film")
    directory = tv_organize.plan_restore(batch_id=batch, kinds=["dir"])
    assert (directory["total"], directory["conflicts"]) == (0, 1)
    assert tv_organize.execute_restore(directory["plans"])["restored"] == 0
    assert foreign.read_bytes() == b"new film" and not (root / "Demo.2020").exists()
    assert store.list_organize_moves(batch_id=batch)[0]["kind"] == "dir"


def _cross_device(monkeypatch, *, on_publish=None):
    """Force EXDEV only for public paths. The private staged copy is on the destination volume."""
    native = fsutil.rename_no_replace

    def rename(src, dst):
        if any(part.startswith(".jzmedia-move-") for part in Path(src).parts):
            if on_publish:
                return on_publish(native, src, dst)
            return native(src, dst)
        raise OSError(errno.EXDEV, "different volumes", dst)

    monkeypatch.setattr(fsutil, "rename_no_replace", rename)


@pytest.mark.parametrize("selective", [True, False])
def test_cross_device_directory_preserves_its_member_audits_for_undo_and_redo(tv_lib, monkeypatch, selective):
    lib, root = tv_lib
    _cross_device(monkeypatch)
    batch, eid = _organized_episode(tv_lib)
    be = tv_organize._backend(lib["id"])
    current = store.get_episode(eid)["file_path"]
    rows = {m["kind"]: m for m in store.list_organize_moves(batch_id=batch)}
    assert rows["episode"]["object_identity"] == be.organize_identity(current, "file")
    assert rows["dir"]["object_identity"] == be.organize_identity("Demo (2020)", "dir")
    for batch_action in ("undo", "redo"):
        plan = tv_organize.plan_restore(batch_id=batch, kinds=["episode"] if selective else None)
        assert plan["total"] == (1 if selective else 2) and plan["conflicts"] == 0, plan
        result = tv_organize.execute_restore(plan["plans"])
        assert result["restored"] == plan["total"] and result["failed"] == 0, result
        batch = result["batch_id"]
        if batch_action == "undo":
            show_dir = "Demo (2020)" if selective else "Demo.2020"
            path = f"{show_dir}/Loose.S01E01.mkv"
        else:
            path = current
        assert store.get_episode(eid)["file_path"] == path
        assert (root / path).read_bytes() == b"actual episode"
    assert not list(root.rglob(".jzmedia-move-*"))


def test_cross_device_db_failure_can_retry_after_new_inode_on_rollback(tv_lib, monkeypatch):
    lib, root = tv_lib
    _cross_device(monkeypatch)
    batch, eid = _organized_episode(tv_lib, rename_root=False)
    before = store.list_organize_moves(batch_id=batch)[0]["object_identity"]
    from app.store import tv
    with monkeypatch.context() as patch:
        def full(*args, **kwargs):
            raise RuntimeError("disk full")
        patch.setattr(tv, "record_organize_moves", full)
        failed = tv_organize.execute_restore(tv_organize.plan_restore(batch_id=batch)["plans"])
    assert failed["failed"] == 1 and failed["restored"] == 0
    current = store.get_episode(eid)["file_path"]
    after = store.list_organize_moves(batch_id=batch)[0]["object_identity"]
    assert after != before and after == tv_organize._backend(lib["id"]).organize_identity(current, "file")
    assert (root / current).read_bytes() == b"actual episode"
    assert tv_organize.execute_restore(tv_organize.plan_restore(batch_id=batch)["plans"])["restored"] == 1


@pytest.mark.parametrize("failure", ["occupied", "new_source", "copy_failure"])
def test_cross_device_failure_preserves_competing_files_and_original(tv_lib, monkeypatch, failure):
    lib, root = tv_lib
    src = root / "From"
    dst = root / "To"
    original = _touch(src, "episode.mkv", b"original")

    def on_publish(native, staged, dest):
        if failure == "occupied":
            _touch(dst, "user.mkv", b"occupied")
        native(staged, dest)
        if failure == "new_source":
            _touch(src, "user.srt", b"late")

    _cross_device(monkeypatch, on_publish=on_publish)
    if failure == "copy_failure":
        def fail(*args, **kwargs):
            raise OSError(errno.ENOSPC, "disk full")
        monkeypatch.setattr(fsutil.shutil, "copytree", fail)
    from app.storage import StorageError
    with pytest.raises(StorageError):
        tv_organize._backend(lib["id"]).organize_move("From", "To", "dir")
    assert original.read_bytes() == b"original"
    if failure == "occupied":
        assert (dst / "user.mkv").read_bytes() == b"occupied" and not (dst / "episode.mkv").exists()
    elif failure == "new_source":
        assert (src / "user.srt").read_bytes() == b"late" and (dst / "episode.mkv").read_bytes() == b"original"
    else:
        assert not dst.exists()
    assert not list(root.rglob(".jzmedia-move-*"))


def test_legacy_backfill_pairs_from_scan_state(tv_lib):
    """v24 之前整理无审计：scan_state 旧路径 + DB 新路径按 (basename,size,mtime) 配对。"""
    lib, root = tv_lib
    old = "Show (2020)/Featurettes/Season 1/Deleted Scenes/cut.mkv"
    new = "Show (2020)/Featurettes/cut.mkv"
    _touch(root, old, data=b"legacy-data")
    scanner.scan_all(library_id=lib["id"])
    with _base._lock, _base._conn() as c:
        row = c.execute("SELECT size, mtime FROM scan_state WHERE library_id=?"
                        " AND file_path=?", (lib["id"], old)).fetchone()
        size, mtime = int(row["size"]), int(row["mtime"])
    # 模拟旧版整理：磁盘 rename + DB 路径更新；旧 scan_state 行残留，新行由重扫写入
    os.rename(root / old, root / new)
    with _base._lock, _base._conn() as c:
        c.execute("UPDATE extras SET file_path=? WHERE library_id=? AND file_path=?",
                  (new, lib["id"], old))
        c.execute("INSERT OR REPLACE INTO scan_state (file_path, library_id, mtime,"
                  " size, status, updated_at) VALUES (?,?,?,?,?,?)",
                  (new, lib["id"], mtime, size, "extra_tv", 1700000001))
        c.execute("DELETE FROM organize_moves WHERE batch_id LIKE 'legacy-%'")
    pairs = store.pair_moved_tv_paths(library_id=lib["id"])
    show = store.list_shows(lib["id"])[0]
    assert len(pairs) == 1 and pairs[0]["from_path"] == old \
        and pairs[0]["to_path"] == new and pairs[0]["kind"] == "extra"
    assert int(pairs[0]["show_id"]) == int(show["id"])
    n = store.backfill_legacy_organize_moves()
    assert n == 1
    batches = store.list_organize_batches()
    legacy = [b for b in batches if b["batch_id"].startswith("legacy-")]
    assert legacy and int(legacy[0]["total"]) == 1
    # 回填后再调不重复
    assert store.backfill_legacy_organize_moves() == 0
    # 可按 legacy 批次撤销
    rplan = tv_organize.plan_restore(batch_id=legacy[0]["batch_id"])
    assert rplan["total"] == 1 and rplan["conflicts"] == 0


def test_organize_moves_table_migrated(tv_lib):
    with _base._lock, _base._conn() as c:
        version = int(c.execute("PRAGMA user_version").fetchone()[0])
        cols = {r["name"] for r in c.execute("PRAGMA table_info(organize_moves)")}
    assert version == _base.SCHEMA_VERSION
    assert version >= 24
    assert {"batch_id", "kind", "action", "from_path", "to_path", "object_identity",
            "undone_at"} <= cols


def test_cleanup_skips_a_regular_file_replacing_a_planned_empty_directory(organize_target):
    lib, root = organize_target
    rel = "Demo/old-season"
    replacement = _touch(root, rel, b"new local file")
    result = tv_organize.execute_tv_organize([{"library_id": lib["id"], "show_id": 1, "rmdirs": [rel]}])
    assert replacement.read_bytes() == b"new local file"
    assert not store.list_organize_moves(batch_id=result["batch_id"])


def test_restore_preview_pins_the_scoped_batch_and_manifest(tv_lib, tmp_path, monkeypatch):
    from app.jobkit import JobRegistry
    from app.routers import jobs
    lib, root = tv_lib
    lid = lib["id"]
    batch, eid = _organized_episode(tv_lib, rename_root=False)
    with _base._lock, _base._conn() as c:
        c.execute("UPDATE organize_moves SET created_at=1000 WHERE batch_id=?", (batch,))
    (tmp_path / "other").mkdir()
    other = store.create_library("other-tv-" + tmp_path.name, kind="tv", path=str(tmp_path / "other"))
    try:
        store.record_organize_moves("other-batch", [{"kind": "file", "from_path": "a", "to_path": "b"}],
                                    library_id=other["id"])
        assert jobs.tv_organize_history(library_id=lid)["batches"][0]["batch_id"] == batch
        registry = JobRegistry(prefix="review")
        monkeypatch.setattr(jobs, "_TV_RESTORE_JOBS", registry)
        def run(dry, batch_id=None, key=None):
            job = registry.create(library_id=lid)
            jobs._tv_restore_worker(job["job_id"], batch_id=batch_id, library_id=lid, kinds=["episode"],
                                    dry_run=dry, preview_id=key)
            return registry.get(job["job_id"])
        preview = run(True)["summary"]
        assert preview["batch_id"] == batch and preview["total"] == 1
        src = store.get_episode(eid)["file_path"]
        replacement = _touch(root, "replacement.bin", b"new data")
        replacement.replace(root / src)
        failed = run(False, batch, preview["preview_id"])
        assert failed["state"] == "failed" and "重新预览" in failed["error"]
        assert (root / src).read_bytes() == b"new data" and store.list_organize_moves(batch_id=batch)
    finally:
        store.delete_media_library(other["media_library_id"])


def test_legacy_audit_cannot_move_a_newer_replacement_with_identical_size_and_mtime(tv_lib, monkeypatch):
    lib, root = tv_lib
    batch, eid = _organized_episode(tv_lib, rename_root=False)
    rel = store.get_episode(eid)["file_path"]
    old = (root / rel).stat()
    replacement = _touch(root, "replacement.tmp", b"x" * old.st_size)
    os.utime(replacement, ns=(old.st_atime_ns, old.st_mtime_ns))
    replacement.replace(root / rel)
    with _base._lock, _base._conn() as c:
        c.execute("UPDATE organize_moves SET object_identity='',created_at=1000 WHERE batch_id=?", (batch,))
        c.execute("INSERT OR REPLACE INTO scan_state(library_id,file_path,mtime,size,status,updated_at)"
                  " VALUES (?,?,?,?,?,?)", (lib["id"], rel, int(old.st_mtime), old.st_size, "tv_ok", 999))
    monkeypatch.setattr("app.fsutil.file_birthtime_ns", lambda path, info: 1001_000_000_000)
    plan = tv_organize.plan_restore(batch_id=batch)
    assert plan["total"] == 0 and plan["conflicts"] == 1
    assert (root / rel).read_bytes() == b"x" * old.st_size
