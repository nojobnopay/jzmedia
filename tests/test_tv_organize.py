"""T4.2 v2：TV 目录规范化（剧根/季目录/包装层/补 Season/特典/花絮/正片改名）。"""
import os

import pytest

from app import library_paths, scanner, store
from app.scanner import tv_organize

ACTIONS = tv_organize.TV_ORGANIZE_ACTIONS


@pytest.fixture()
def tv_lib(tmp_path):
    root = tmp_path / "tvlib"
    root.mkdir()
    lib = store.create_library(name=f"tv6-{tmp_path.name}", kind="tv", path=str(root))
    library_paths.invalidate_cache()
    yield lib, root
    store.delete_media_library(lib["media_library_id"])
    library_paths.invalidate_cache()


def _touch(root, rel, data=b"x"):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def _run(lib, actions=ACTIONS, allow_torrent=False):
    plan = tv_organize.plan_tv_organize(library_ids=[lib["id"]], actions=actions,
                                        allow_torrent=allow_torrent)
    res = tv_organize.execute_tv_organize(plan["plans"], allow_torrent=allow_torrent)
    return plan, res


def _match_show(lib, title="测试剧", year=2020, tmdb_id=999001):
    show = store.list_shows(lib["id"])[0]
    store.update_show_meta(show["id"], title=title, year=year, tmdb_id=tmdb_id)
    return store.get_show(show["id"])


def _match_episodes(show_id, prefix="集", start=1):
    for e in store.list_episodes(show_id):
        store.update_episode_meta(e["id"], tmdb_episode_id=900000 + start,
                                  title=f"{prefix}{int(e['episode'])}")


def test_wrapper_flatten_moves_season_dir(tv_lib):
    lib, root = tv_lib
    _touch(root, "Wrapped Show (2021)/Release.Name/Season 01/Wrapped.S01E01.mkv")
    _touch(root, "Wrapped Show (2021)/Release.Name/Season 02/Wrapped.S02E01.mkv")
    scanner.scan_all(library_id=lib["id"])
    plan, res = _run(lib, actions=("wrapper",))
    assert plan["counts"]["wrapper"] == 2
    assert res["renamed"] == 2 and res["failed"] == 0, res
    assert (root / "Wrapped Show (2021)/Season 01/Wrapped.S01E01.mkv").is_file()
    assert not (root / "Wrapped Show (2021)/Release.Name").exists()
    plan2 = tv_organize.plan_tv_organize(library_ids=[lib["id"]], actions=("wrapper",))
    assert plan2["total"] == 0


def test_season_dir_for_flat_show(tv_lib):
    lib, root = tv_lib
    _touch(root, "Flat Show (2020)/Flat.Show.E01.mkv")
    _touch(root, "Flat Show (2020)/Flat.Show.E02.mkv")
    _touch(root, "Flat Show (2020)/Flat.Show.E01.chs.srt")
    scanner.scan_all(library_id=lib["id"])
    plan, res = _run(lib, actions=("season",))
    assert plan["counts"]["season"] == 3   # 2 集 + 同茎字幕
    assert res["moved"] == 3 and res["failed"] == 0, res
    assert (root / "Flat Show (2020)/Season 01/Flat.Show.E01.mkv").is_file()
    assert (root / "Flat Show (2020)/Season 01/Flat.Show.E01.chs.srt").is_file()
    show = store.list_shows(lib["id"])[0]
    assert all("/Season 01/" in e["file_path"]
               for e in store.list_episodes(show["id"]))


def test_seasondir_normalization(tv_lib):
    lib, root = tv_lib
    _touch(root, "Show (2020)/season 1/strike.back.s01e01-e02.mkv")
    _touch(root, "Show (2020)/S02/S02E01.mkv")
    scanner.scan_all(library_id=lib["id"])
    plan, res = _run(lib, actions=("seasondir",))
    assert plan["counts"]["seasondir"] == 2
    assert res["renamed"] == 2 and res["failed"] == 0, res
    assert (root / "Show (2020)/Season 01/strike.back.s01e01-e02.mkv").is_file()
    assert (root / "Show (2020)/Season 02/S02E01.mkv").is_file()
    show = store.list_shows(lib["id"])[0]
    paths = sorted(e["file_path"] for e in store.list_episodes(show["id"]))
    assert paths == ["Show (2020)/Season 01/strike.back.s01e01-e02.mkv",
                     "Show (2020)/Season 02/S02E01.mkv"]


def test_specials_move_to_season00(tv_lib):
    lib, root = tv_lib
    _touch(root, "Show (2020)/Season 01/Show.S01E01.mkv")
    _touch(root, "Show (2020)/[OVA]/Show.OVA.01.mkv")
    _touch(root, "Show (2020)/[OVA]/Show.OVA.01.nfo")
    scanner.scan_all(library_id=lib["id"])
    show = store.list_shows(lib["id"])[0]
    assert any(int(e["season"]) == 0 for e in store.list_episodes(show["id"]))
    plan, res = _run(lib, actions=("specials",))
    assert plan["counts"]["specials"] == 2   # 正片 + 同茎 nfo
    assert res["moved"] == 2 and res["failed"] == 0, res
    assert (root / "Show (2020)/Season 00/Show.OVA.01.mkv").is_file()
    assert (root / "Show (2020)/Season 00/Show.OVA.01.nfo").is_file()
    assert not (root / "Show (2020)/[OVA]").exists()


def test_season_relocate_misplaced_files(tv_lib):
    """重绑后季拆分变化：文件仍在 Season 01 但库内是第 2 季 → 移入 Season 02（+DB 同步）。"""
    lib, root = tv_lib
    _touch(root, "Show (2020)/Season 01/Show.S01E01.mkv")
    _touch(root, "Show (2020)/Season 01/Show.S01E02.mkv")
    scanner.scan_all(library_id=lib["id"])
    show = store.list_shows(lib["id"])[0]
    eps = sorted(store.list_episodes(show["id"]), key=lambda e: int(e["episode"]))
    store.update_episode_meta(eps[1]["id"], season=2, episode=1)
    plan, res = _run(lib, actions=("season",))
    assert plan["counts"]["season"] == 1, plan["counts"]
    assert res["moved"] == 1 and res["failed"] == 0, res
    assert (root / "Show (2020)/Season 01/Show.S01E01.mkv").is_file()
    assert (root / "Show (2020)/Season 02/Show.S01E02.mkv").is_file()
    assert store.get_episode(eps[1]["id"])["file_path"] == \
        "Show (2020)/Season 02/Show.S01E02.mkv"
    plan2 = tv_organize.plan_tv_organize(library_ids=[lib["id"]], actions=("season",))
    assert plan2["total"] == 0          # 幂等


def test_specials_relocate_from_wrong_season_dir(tv_lib):
    """库内特典（S0）落在 Season 01 目录 → specials 动作归位 Season 00。"""
    lib, root = tv_lib
    _touch(root, "Show (2020)/Season 01/Show.S01E01.mkv")
    _touch(root, "Show (2020)/Season 01/S00E01.mkv")
    scanner.scan_all(library_id=lib["id"])
    show = store.list_shows(lib["id"])[0]
    assert any(int(e["season"]) == 0 for e in store.list_episodes(show["id"]))
    plan, res = _run(lib, actions=("specials",))
    assert plan["counts"]["specials"] == 1, plan["counts"]
    assert res["moved"] == 1 and res["failed"] == 0, res
    assert (root / "Show (2020)/Season 00/S00E01.mkv").is_file()
    assert (root / "Show (2020)/Season 01/Show.S01E01.mkv").is_file()


def test_summarize_dir_totals_includes_in_place(tv_lib):
    """最终分布含无需移动的正片：Season 01 不应只显示"被移动的那 1 项"。"""
    lib, root = tv_lib
    _touch(root, "Show (2020)/Season 01/Show.S01E01.mkv")
    _touch(root, "Show (2020)/Season 01/Show.S01E02.mkv")
    scanner.scan_all(library_id=lib["id"])
    show = store.list_shows(lib["id"])[0]
    eps = sorted(store.list_episodes(show["id"]), key=lambda e: int(e["episode"]))
    store.update_episode_meta(eps[1]["id"], season=2, episode=1)
    plan = tv_organize.plan_tv_organize(library_ids=[lib["id"]], actions=("season",))
    s = tv_organize.summarize_plan(plan["plans"][0])
    totals = {os.path.basename(r["dir"]): r["count"] for r in s["dir_totals"]}
    assert totals == {"Season 01": 1, "Season 02": 1}, totals
    assert s["counts"]["season"] == 1     # 变更清单里只有 1 项要移动


def test_root_rename_matched_show(tv_lib):
    lib, root = tv_lib
    _touch(root, "Breaking.Bad.2008/Season 01/Breaking.Bad.S01E01.mkv")
    scanner.scan_all(library_id=lib["id"])
    show = _match_show(lib, title="绝命毒师", year=2008, tmdb_id=1396)
    plan, res = _run(lib, actions=("root",))
    assert plan["counts"]["root"] == 1
    assert res["renamed"] == 1 and res["failed"] == 0, res
    assert (root / "绝命毒师 (2008)/Season 01/Breaking.Bad.S01E01.mkv").is_file()
    assert not (root / "Breaking.Bad.2008").exists()
    assert store.list_episodes(show["id"])[0]["file_path"] == \
        "绝命毒师 (2008)/Season 01/Breaking.Bad.S01E01.mkv"


def test_root_rename_skips_unmatched(tv_lib):
    lib, root = tv_lib
    _touch(root, "Some.Show.2019/Season 01/S01E01.mkv")
    scanner.scan_all(library_id=lib["id"])
    plan = tv_organize.plan_tv_organize(library_ids=[lib["id"]], actions=("root",))
    assert plan["total"] == 0 and plan["conflicts"] == 0
    assert (root / "Some.Show.2019/Season 01/S01E01.mkv").is_file()


def test_root_rename_conflict_reported(tv_lib):
    lib, root = tv_lib
    _touch(root, "Old.Name.2020/Season 01/S01E01.mkv")
    _touch(root, "测试剧 (2020)/Season 01/other.mkv")
    scanner.scan_all(library_id=lib["id"])
    _match_show(lib, title="测试剧", year=2020)
    plan = tv_organize.plan_tv_organize(library_ids=[lib["id"]], actions=("root",))
    assert plan["counts"]["root"] == 0 and plan["conflicts"] == 1
    assert (root / "Old.Name.2020/Season 01/S01E01.mkv").is_file()


def test_extras_promotes_depth2_and_reports_deeper(tv_lib):
    lib, root = tv_lib
    _touch(root, "Show (2020)/Season 01/Show.S01E01.mkv")
    _touch(root, "Show (2020)/Featurettes/Deleted Scenes/cut.mkv")
    _touch(root, "Show (2020)/Featurettes/Season 1/gag.mkv")
    _touch(root, "Show (2020)/Season 02/Interviews/making.mkv")
    _touch(root, "Show (2020)/Featurettes/Season 3/Behind The Scenes/deep.mkv")
    _touch(root, "Show (2020)/Behind The Scene/intro.mkv")
    scanner.scan_all(library_id=lib["id"])
    show = store.list_shows(lib["id"])[0]
    plan, res = _run(lib, actions=("extras",))
    # Featurettes/Deleted Scenes（深度 2）上移 + Behind The Scene 改名
    assert plan["counts"]["extras"] == 2, plan["counts"]
    assert res["renamed"] == 2 and res["failed"] == 0, res
    assert (root / "Show (2020)/Deleted Scenes/cut.mkv").is_file()
    assert (root / "Show (2020)/Behind The Scenes/intro.mkv").is_file()
    # 季目录内已是合法季级花絮；深度 3 / 散文件只报告
    assert (root / "Show (2020)/Season 02/Interviews/making.mkv").is_file()
    assert (root / "Show (2020)/Featurettes/Season 1/gag.mkv").is_file()
    assert (root / "Show (2020)/Featurettes/Season 3/Behind The Scenes/deep.mkv").is_file()
    assert sorted(plan["plans"][0]["untouched"]) == [
        "Show (2020)/Featurettes/Season 1/gag.mkv",
        "Show (2020)/Featurettes/Season 3/Behind The Scenes/deep.mkv",
    ]
    paths = sorted(x["file_path"] for x in store.list_extras_by_show(show["id"]))
    assert "Show (2020)/Deleted Scenes/cut.mkv" in paths
    assert "Show (2020)/Behind The Scenes/intro.mkv" in paths


def test_extras_kind_dir_target_conflict_reported(tv_lib):
    lib, root = tv_lib
    _touch(root, "Show (2020)/Season 01/Show.S01E01.mkv")
    _touch(root, "Show (2020)/Deleted Scenes/keep.mkv")
    _touch(root, "Show (2020)/Featurettes/Deleted Scenes/cut.mkv")
    scanner.scan_all(library_id=lib["id"])
    plan = tv_organize.plan_tv_organize(library_ids=[lib["id"]], actions=("extras",))
    assert plan["total"] == 0 and plan["conflicts"] == 1
    assert (root / "Show (2020)/Featurettes/Deleted Scenes/cut.mkv").is_file()


def test_rename_moves_and_renames_single_op(tv_lib):
    """裸编号 + S01 目录：补 Season 与改名合并，终态一次到位。"""
    lib, root = tv_lib
    _touch(root, "测试剧.2020/S01/01.mp4")
    _touch(root, "测试剧.2020/S01/01.chs.srt")
    scanner.scan_all(library_id=lib["id"])
    show = _match_show(lib, title="测试剧", year=2020)
    _match_episodes(show["id"])
    plan, res = _run(lib)
    assert res["failed"] == 0, res
    dst = root / "测试剧 (2020)/Season 01/测试剧-S01E01-集1.mp4"
    assert dst.is_file()
    assert (root / "测试剧 (2020)/Season 01/测试剧-S01E01-集1.chs.srt").is_file()
    assert not (root / "测试剧.2020/S01/01.mp4").exists()
    ep = store.list_episodes(show["id"])[0]
    assert ep["file_path"] == "测试剧 (2020)/Season 01/测试剧-S01E01-集1.mp4"


def test_rename_separator_no_spaces(tv_lib):
    """文件名分隔符用 `-` 且不留空格；标题里的 ` - ` 也归一为 `-`。"""
    lib, root = tv_lib
    _touch(root, "Old - Name.2020/Season 01/01.mkv")
    scanner.scan_all(library_id=lib["id"])
    show = _match_show(lib, title="测试 - 剧", year=2020)
    _match_episodes(show["id"])
    plan, res = _run(lib, actions=("rename",))
    assert res["failed"] == 0, res
    assert (root / "Old - Name.2020/Season 01/测试-剧-S01E01-集1.mkv").is_file()


def test_rename_special_and_multi_version(tv_lib):
    lib, root = tv_lib
    _touch(root, "测试剧.2020/Season 00/S00E01.mkv")
    _touch(root, "测试剧.2020/Season 01/01.mkv")
    _touch(root, "测试剧.2020/Season 01/01.DV.mkv")
    scanner.scan_all(library_id=lib["id"])
    show = _match_show(lib, title="测试剧", year=2020)
    _match_episodes(show["id"])
    for e in store.list_episodes(show["id"]):
        if int(e["season"]) == 0:
            store.update_episode_meta(e["id"], tmdb_episode_id=800001, title="特典")
    plan0 = tv_organize.plan_tv_organize(library_ids=[lib["id"]], actions=("rename",))
    order = [m["to"].rsplit("/", 1)[-1] for m in plan0["plans"][0]["file_moves"]
             if m["kind"] == "episode" and "S01E01" in m["to"]]
    # 版本分组：V1 在前，V2 在后
    assert order[0] == "测试剧-S01E01-集1.mkv", order
    assert order[1] == "测试剧-V2-S01E01-集1.mkv", order
    plan, res = _run(lib, actions=("rename",))
    assert res["failed"] == 0, res
    files = sorted(p.name for p in (root / "测试剧.2020/Season 01").iterdir())
    assert files == ["测试剧-S01E01-集1.mkv", "测试剧-V2-S01E01-集1.mkv"]
    assert (root / "测试剧.2020/Season 00/测试剧-S00E01-特典.mkv").is_file()


def test_rename_skips_needs_review_and_renames_long_range(tv_lib):
    """needs_review 只报告；多集区间（含整季单文件）按同一模板正常改名。"""
    lib, root = tv_lib
    _touch(root, "测试剧.2020/Season 01/01.mkv")
    _touch(root, "测试剧.2020/Season 01/02.mkv")
    scanner.scan_all(library_id=lib["id"])
    show = _match_show(lib, title="测试剧", year=2020)
    eps = {int(e["episode"]): e for e in store.list_episodes(show["id"])}
    store.update_episode_meta(eps[1]["id"], tmdb_episode_id=800001, title="集1",
                              needs_review=1)
    store.update_episode_meta(eps[2]["id"], tmdb_episode_id=800002, title="集2",
                              episode_end=8)
    plan, res = _run(lib, actions=("rename",))
    assert plan["counts"]["rename"] == 1
    reasons = {m["reason"] for m in plan["plans"][0]["manual"]}
    assert reasons == {"needs_review"}
    assert (root / "测试剧.2020/Season 01/01.mkv").is_file()
    assert (root / "测试剧.2020/Season 01/测试剧-S01E02-E08-集2.mkv").is_file()
    assert res["failed"] == 0, res


def test_rename_skips_absolute_risk_show(tv_lib):
    lib, root = tv_lib
    _touch(root, "测试剧.2020/Season 01/01.mkv")
    _touch(root, "测试剧.2020/Season 01/02.mkv")
    scanner.scan_all(library_id=lib["id"])
    show = _match_show(lib, title="测试剧", year=2020)
    for e in store.list_episodes(show["id"]):
        store.update_episode_meta(e["id"], tmdb_episode_id=900000 + int(e["episode"]),
                                  title=f"集{int(e['episode'])}", absolute_number=1)
    plan = tv_organize.plan_tv_organize(library_ids=[lib["id"]], actions=("rename",))
    assert plan["absolute"] == 1
    assert plan["plans"][0]["absolute_risk"] is True
    assert plan["counts"]["rename"] == 0
    assert plan["plans"][0]["manual"][0]["reason"] == "absolute"
    # 显式勾选（同意）后按 TMDB 编号改名
    plan2 = tv_organize.plan_tv_organize(
        library_ids=[lib["id"]], actions=("rename",),
        allow_absolute_shows=[show["id"]])
    assert plan2["counts"]["rename"] == 2 and plan2["plans"][0]["manual"] == []
    res = tv_organize.execute_tv_organize(plan2["plans"])
    assert res["failed"] == 0
    assert (root / "测试剧.2020/Season 01/测试剧-S01E01-集1.mkv").is_file()
    assert (root / "测试剧.2020/Season 01/测试剧-S01E02-集2.mkv").is_file()


def test_torrent_guard(tv_lib):
    lib, root = tv_lib
    _touch(root, "Tor Show (2020)/Tor.Show.E01.mkv")
    _touch(root, "Tor Show (2020)/abc.torrent")
    scanner.scan_all(library_id=lib["id"])
    plan = tv_organize.plan_tv_organize(library_ids=[lib["id"]], actions=("season",))
    assert plan["blocked"] == 1 and plan["total"] == 1
    res = tv_organize.execute_tv_organize(plan["plans"])
    assert res["skipped"] == 1 and res["moved"] == 0
    assert (root / "Tor Show (2020)/Tor.E01.mkv").is_file() or \
        (root / "Tor Show (2020)/Tor.Show.E01.mkv").is_file()
    plan2 = tv_organize.plan_tv_organize(library_ids=[lib["id"]], actions=("season",),
                                         allow_torrent=True)
    assert plan2["blocked"] == 0
    res2 = tv_organize.execute_tv_organize(plan2["plans"], allow_torrent=True)
    assert res2["moved"] == 1
    assert (root / "Tor Show (2020)/Season 01/Tor.Show.E01.mkv").is_file()


def test_rename_sanitizes_slash_in_episode_title(tv_lib):
    """集名含 `/` 必须清洗成空格，绝不能建出子目录。"""
    lib, root = tv_lib
    _touch(root, "测试剧.2020/Season 01/01.mkv")
    scanner.scan_all(library_id=lib["id"])
    show = _match_show(lib, title="测试剧", year=2020)
    for e in store.list_episodes(show["id"]):
        store.update_episode_meta(e["id"], tmdb_episode_id=900001,
                                  title="OVA#6「走光风/变身」")
    plan, res = _run(lib, actions=("rename",))
    assert res["failed"] == 0, res
    dst = root / "测试剧.2020/Season 01/测试剧-S01E01-OVA#6「走光风 变身」.mkv"
    assert dst.is_file()
    assert not (root / "测试剧.2020/Season 01/测试剧-S01E01-OVA#6「走光风").exists()


def test_resolve_show_dir_nested_subshow_and_wrapper(tv_lib):
    """子剧套在父目录：剧根停在子剧目录（父目录含其它剧）；发布包装层仍上溯。"""
    lib, _root = tv_lib
    s1 = store.upsert_show(lib["id"], "甲剧", 2020)
    s2 = store.upsert_show(lib["id"], "乙剧", 2021)
    s3 = store.upsert_show(lib["id"], "丙剧", 2019)
    store.upsert_episode(s1, lib["id"], "合集/甲剧/Season 01/A.S01E01.mkv", 1, 1)
    store.upsert_episode(s2, lib["id"], "合集/乙剧/Season 01/B.S01E01.mkv", 1, 1)
    store.upsert_episode(s3, lib["id"], "丙剧/Release.Name/Season 01/C.S01E01.mkv", 1, 1)
    assert tv_organize._resolve_show_dir(store.list_episodes(s1), show_id=s1,
                                         library_id=lib["id"]) == "合集/甲剧"
    assert tv_organize._resolve_show_dir(store.list_episodes(s3), show_id=s3,
                                         library_id=lib["id"]) == "丙剧"


def test_rename_idempotent_after_execute(tv_lib):
    """执行后再预览应无动作（含同集多版本 `-V2` 不互换、不误报 exists）。"""
    lib, root = tv_lib
    _touch(root, "测试剧.2020/S01/01.mkv")
    _touch(root, "测试剧.2020/S01/01.DV.mkv")
    scanner.scan_all(library_id=lib["id"])
    show = _match_show(lib, title="测试剧", year=2020)
    _match_episodes(show["id"])
    plan, res = _run(lib)
    assert res["failed"] == 0, res
    plan2 = tv_organize.plan_tv_organize(library_ids=[lib["id"]])
    assert plan2["total"] == 0 and plan2["manual"] == 0, plan2
    files = sorted(p.name for p in (root / "测试剧 (2020)/Season 01").iterdir())
    assert files == ["测试剧-S01E01-集1.mkv", "测试剧-V2-S01E01-集1.mkv"]


def test_summarize_plan_groups(tv_lib):
    lib, root = tv_lib
    _touch(root, "测试剧.2020/S01/01.mp4")
    _touch(root, "测试剧.2020/S01/01.chs.srt")
    scanner.scan_all(library_id=lib["id"])
    show = _match_show(lib, title="测试剧", year=2020)
    _match_episodes(show["id"])
    plan = tv_organize.plan_tv_organize(library_ids=[lib["id"]], actions=ACTIONS)
    s = tv_organize.summarize_plan(plan["plans"][0])
    assert s["counts"]["root"] == 1 and s["counts"]["rename"] == 1
    labels = {g["label"] for g in s["groups"]}
    assert "剧根改名" in labels and "季目录规范化" in labels
    # 正片与附属文件分开计数（避免"正片 3 / 文件 12"两个口径）
    ren = next(g for g in s["groups"] if g["action"] == "rename")
    assert ren["episodes"] == 1 and ren["files"] == 1 and ren["count"] == 2
    assert s["manual"] == [] and s["untouched"] == []


def test_rename_keeps_part_files(tv_lib):
    """Plex 拆分集命名（`…-partN`）：整理器识别为规范名，不改名、不转成 -V2-。"""
    lib, root = tv_lib
    _touch(root, "测试剧.2020/Season 01/测试剧-S01E01-集1-part1.mkv")
    _touch(root, "测试剧.2020/Season 01/测试剧-S01E01-集1-part2.mkv")
    scanner.scan_all(library_id=lib["id"])
    show = _match_show(lib, title="测试剧", year=2020)
    _match_episodes(show["id"])
    plan = tv_organize.plan_tv_organize(library_ids=[lib["id"]], actions=("rename",))
    assert plan["total"] == 0 and plan["manual"] == 0, plan
    plan2 = tv_organize.plan_tv_organize(library_ids=[lib["id"]])
    assert plan2["counts"]["rename"] == 0, plan2


def test_wide_range_pack_moves_and_renames(tv_lib):
    """整季单文件（S01E01-E13）按普通集处理：补 Season + 模板改名，不报需手动。"""
    lib, root = tv_lib
    _touch(root, "Pack.Show.2020/Usavich.S01E01-E13.WEB.1080p.mkv")
    scanner.scan_all(library_id=lib["id"])
    show = _match_show(lib, title="测试剧", year=2020)
    ep = store.list_episodes(show["id"])[0]
    store.update_episode_meta(ep["id"], tmdb_episode_id=800001, title="第一话")
    plan, res = _run(lib, actions=("season", "rename"))
    assert plan["manual"] == 0, plan
    assert plan["counts"].get("season") == 1, plan
    assert plan["plans"][0]["rename_count"] == 1, plan
    assert res["failed"] == 0, res
    assert (root / "Pack.Show.2020/Season 01/测试剧-S01E01-E13-第一话.mkv").is_file()


def test_wide_range_canonical_name_not_manual(tv_lib):
    """整季单文件已是规范名 + 目录已就位：无动作、无 manual（一休式误报回归）。"""
    lib, root = tv_lib
    _touch(root, "测试剧.2020/Season 01/测试剧-S01E01-E06-集1.mkv")
    scanner.scan_all(library_id=lib["id"])
    show = _match_show(lib, title="测试剧", year=2020)
    ep = store.list_episodes(show["id"])[0]
    store.update_episode_meta(ep["id"], tmdb_episode_id=800001, title="集1")
    plan = tv_organize.plan_tv_organize(library_ids=[lib["id"]],
                                        actions=("season", "rename"))
    assert plan["total"] == 0 and plan["manual"] == 0, plan


def test_paired_episodes_and_sidecars_need_no_renaming(tv_lib):
    """Three correctly labelled files cover six episodes without twelve moves."""
    lib, root = tv_lib
    for start in (1, 3, 5):
        stem = f"反击-S01E{start:02d}-E{start + 1:02d}-第 {start}-{start + 1} 集"
        for ext in (".mkv", ".ass", ".nfo", ".chs.srt"):
            _touch(root, f"反击 (2010)/Season 01/{stem}{ext}")
    scanner.scan_all(library_id=lib["id"])
    show = _match_show(lib, title="反击", year=2010)
    for ep in store.list_episodes(show["id"]):
        store.update_episode_meta(ep["id"], tmdb_episode_id=900000 + ep["episode"],
                                  title=f"第 {ep['episode']} 集")
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    for _ in range(2):
        plan, result = _run(lib)
        assert plan["total"] == 0 and plan["manual"] == 0, plan
        assert result["moved"] == result["renamed"] == result["failed"] == 0
    episodes = store.list_episodes(show["id"])
    assert len(episodes) == 3
    assert {n for ep in episodes for n in range(ep["episode"], ep["episode_end"] + 1)} == set(range(1, 7))
    assert {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("suffix", ["第1-2集", "第 1 – 2 集", "第1至第2话", "Episodes 1~2",
                                   "第 1 集", "Episode 1", ""])
def test_range_names_and_legacy_titles_do_not_trigger_absolute_risk(tv_lib, suffix):
    lib, root = tv_lib
    raw_title = suffix if suffix in ("第 1 集", "Episode 1", "") else "第 1 集"
    stem = "测试剧-S01E01-E02" + (f"-{suffix}" if suffix else "")
    _touch(root, f"测试剧.2020/Season 01/{stem}.mkv")
    scanner.scan_all(library_id=lib["id"])
    show = _match_show(lib)
    ep = store.list_episodes(show["id"])[0]
    store.update_episode_meta(ep["id"], tmdb_episode_id=900001, title=raw_title, absolute_number=1)
    plan = tv_organize.plan_tv_organize(library_ids=[lib["id"]], actions=("rename",))
    assert plan["total"] == plan["manual"] == plan["absolute"] == 0, plan


@pytest.mark.parametrize("title", ["第1-3集", "第2-1集", "第1-1集", "第0-2集"])
def test_wrong_title_range_is_corrected_even_if_it_matches_upstream_title(tv_lib, title):
    lib, root = tv_lib
    _touch(root, f"测试剧.2020/Season 01/测试剧-S01E01-E02-{title}.mkv")
    scanner.scan_all(library_id=lib["id"])
    show = _match_show(lib)
    ep = store.list_episodes(show["id"])[0]
    store.update_episode_meta(ep["id"], tmdb_episode_id=900001, title=title)
    plan, result = _run(lib, actions=("rename",))
    assert plan["counts"]["rename"] == 1 and result["failed"] == 0, plan
    assert (root / "测试剧.2020/Season 01/测试剧-S01E01-E02-第1-2集.mkv").is_file()
    assert store.get_episode(ep["id"])["title"] == title


@pytest.mark.parametrize("stem", [
    "别的剧-S01E01-E02-第1-2集", "测试剧-S02E01-E02-第1-2集",
    "测试剧-S01E01-E03-第1-2集", "测试剧-V2-S01E01-E02-第1-2集",
    "测试剧-S01E01-第1-2集", "测试剧-S01E01-E02-任意后缀",
])
def test_title_compatibility_requires_exact_show_version_and_episode_head(stem):
    ep = {"season": 1, "episode": 1, "episode_end": 2, "title": "第 1 集"}
    assert not tv_organize._is_canonical_stem(stem, "测试剧", ep, ver=1)


def test_canonical_range_file_moves_without_changing_its_name(tv_lib):
    lib, root = tv_lib
    stem = "测试剧-S01E01-E02-第 1-2 集"
    for ext in (".mkv", ".chs.srt", ".nfo"):
        _touch(root, f"测试剧.2020/{stem}{ext}")
    scanner.scan_all(library_id=lib["id"])
    show = _match_show(lib)
    ep = store.list_episodes(show["id"])[0]
    store.update_episode_meta(ep["id"], tmdb_episode_id=900001, title="第 1 集")
    plan, result = _run(lib, actions=("season", "rename"))
    assert plan["plans"][0]["rename_count"] == 0, plan
    assert result["moved"] == 3 and result["failed"] == 0, result
    for ext in (".mkv", ".chs.srt", ".nfo"):
        assert (root / f"测试剧.2020/Season 01/{stem}{ext}").is_file()
    assert tv_organize.plan_tv_organize(library_ids=[lib["id"]], actions=("season", "rename"))["total"] == 0


def test_range_versions_and_split_parts_remain_canonical(tv_lib):
    lib, root = tv_lib
    names = ["测试剧-S01E01-E02-第 1-2 集-part1", "测试剧-S01E01-E02-第 1-2 集-part2",
             "测试剧-V2-S01E01-E02-第 1-2 集"]
    for name in names:
        _touch(root, f"测试剧.2020/Season 01/{name}.mkv")
    scanner.scan_all(library_id=lib["id"])
    show = _match_show(lib)
    for ep in store.list_episodes(show["id"]):
        store.update_episode_meta(ep["id"], tmdb_episode_id=900001, title="第 1 集")
    plan = tv_organize.plan_tv_organize(library_ids=[lib["id"]], actions=("rename",))
    assert plan["total"] == plan["manual"] == 0, plan


def test_newly_organized_paired_episode_uses_range_title(tv_lib):
    lib, root = tv_lib
    for ext in (".mkv", ".ass", ".nfo"):
        _touch(root, f"测试剧.2020/Season 01/Release.S01E01-E02{ext}")
    scanner.scan_all(library_id=lib["id"])
    show = _match_show(lib)
    ep = store.list_episodes(show["id"])[0]
    store.update_episode_meta(ep["id"], tmdb_episode_id=900001, title="第 1 集")
    plan, result = _run(lib, actions=("rename",))
    assert plan["plans"][0]["rename_count"] == 1 and result["failed"] == 0, result
    for ext in (".mkv", ".ass", ".nfo"):
        assert (root / f"测试剧.2020/Season 01/测试剧-S01E01-E02-第1-2集{ext}").is_file()
    assert tv_organize.plan_tv_organize(library_ids=[lib["id"]], actions=("rename",))["total"] == 0


def test_absolute_risk_not_reported_when_already_canonical(tv_lib):
    """绝对集号剧正片已全部规范名：不报风险、不需手动（既往已整理不再打扰）。"""
    lib, root = tv_lib
    _touch(root, "测试剧.2020/Season 01/测试剧-S01E01-集1.mkv")
    scanner.scan_all(library_id=lib["id"])
    show = _match_show(lib, title="测试剧", year=2020)
    ep = store.list_episodes(show["id"])[0]
    store.update_episode_meta(ep["id"], tmdb_episode_id=900001, title="集1",
                              absolute_number=1)
    plan = tv_organize.plan_tv_organize(library_ids=[lib["id"]], actions=("rename",))
    assert plan["absolute"] == 0
    assert plan["manual"] == 0 and plan["total"] == 0, plan
    # 非规范名（需改名）仍照旧提示，需显式确认
    _touch(root, "测试剧.2020/Season 01/02.mkv")
    scanner.scan_all(library_id=lib["id"], force=True)
    eps = store.list_episodes(show["id"])
    for e in eps:
        store.update_episode_meta(e["id"], tmdb_episode_id=900000 + int(e["episode"]),
                                  title=f"集{int(e['episode'])}", absolute_number=1)
    plan2 = tv_organize.plan_tv_organize(library_ids=[lib["id"]], actions=("rename",))
    assert plan2["absolute"] == 1
    assert any(m["reason"] == "absolute" for m in plan2["plans"][0]["manual"])


def test_local_only_listed_as_kept_not_manual(tv_lib):
    """已确认本地集：列入 kept（保持原名），不计需手动、不触发改名。"""
    lib, root = tv_lib
    _touch(root, "测试剧.2020/Season 01/01.mkv")
    scanner.scan_all(library_id=lib["id"])
    show = _match_show(lib, title="测试剧", year=2020)
    ep = store.list_episodes(show["id"])[0]
    store.update_episode_meta(ep["id"], title="本地集", local_only=1)
    plan = tv_organize.plan_tv_organize(library_ids=[lib["id"]], actions=("rename",))
    assert plan["manual"] == 0 and plan["kept"] == 1, plan
    p = plan["plans"][0]
    assert p["manual"] == [] and len(p["kept"]) == 1
    s = tv_organize.summarize_plan(p)
    assert s["manual"] == [] and s["kept_count"] == 1
    assert (root / "测试剧.2020/Season 01/01.mkv").is_file()
