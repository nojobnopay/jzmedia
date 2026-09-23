"""v24：整理审计（organize_moves）+ 撤销整理（plan_restore/execute_restore）。

覆盖：往返还原（磁盘/DB/scan_state）、子集还原、冲突不执行、legacy 回填（从 scan_state
配对 v24 之前的移动）、批次历史与 undone 标记。
"""
import os

import pytest

from app import library_paths, scanner, store
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
    assert version == _base.SCHEMA_VERSION == 25
    assert {"batch_id", "kind", "action", "from_path", "to_path",
            "undone_at"} <= cols
