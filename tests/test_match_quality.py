"""P1 匹配质量修复：标题相似门（防错配）+ 本地优先复用（跨库数据）。

背景（2026-09 用户反馈）：TMDB 模糊搜索会把「龙珠Z剧场版07：地球争霸战」配成
「世界大战」，而本地库已有正确匹配（告白 54186 vs 远端错配 471040）却不被复用。
"""
import pytest

from app import library_paths, scanner, storage, store
from app.metadata import local as meta_local
from app.scanner.match import (SIM_THRESHOLD, pick_match, search_with_fallback,
                               title_similar)


# ---------- 标题相似门 ----------

def test_title_similar_cases():
    assert title_similar("告白", "告白") == 1.0
    assert title_similar("Year Ok", "year ok") == 1.0
    assert title_similar("龙珠Z剧场版07：地球争霸战", "地球争霸战") == 1.0   # 互为子串
    assert title_similar("告白", "The Confessions") < SIM_THRESHOLD        # 完全不同
    assert title_similar("告白 Confessions", "告白") == 1.0


def test_pick_match_strict_gate():
    db = [{"id": 1, "title": "世界大战", "original_title": "War of the Worlds",
           "release_date": "2005-06-29"}]
    # 年份近但标题完全不像 → 不绑定（旧逻辑会静默采信 results[0]）
    assert pick_match(db, 2008, query="地球争霸战") == (None, False)
    # 标题匹配 + 年份 ±1 → 直接采信
    ok = [{"id": 2, "title": "地球争霸战", "original_title": "",
           "release_date": "1990-01-01"}]
    m, review = pick_match(ok, 2008, query="地球争霸战")
    assert m and m["id"] == 2 and review is True      # 年份差太多 → 待确认
    # 标题匹配 + 年份一致 → 免确认
    m2, review2 = pick_match([{**ok[0], "release_date": "2008-03-01"}], 2008,
                             query="地球争霸战")
    assert m2 and review2 is False
    # 年份一致但标题不像 → 采信但待确认
    m3, review3 = pick_match(db, 2005, query="地球争霸战")
    assert m3 and review3 is True
    assert pick_match([], 2008, query="x") == (None, False)


def test_search_with_fallback_uses_gate(monkeypatch):
    calls = []

    def _search(q, year=None):
        calls.append(q)
        if q == "地球争霸战":
            return [{"id": 999, "title": "世界大战", "release_date": "2005-01-01"}]
        return []
    monkeypatch.setattr(scanner.tmdb, "search_movie", _search)
    m, used_q, review = search_with_fallback("龙珠Z剧场版07：地球争霸战", 2008)
    assert m is None            # 相似门拒绝后不再盲采
    assert used_q == "龙珠Z剧场版07：地球争霸战"
    assert len(calls) >= 2      # 试过短查询


def test_search_with_fallback_year_mismatch_needs_review(monkeypatch):
    monkeypatch.setattr(scanner.tmdb, "search_movie",
                        lambda q, year=None: [{"id": 7, "title": "告白",
                                               "release_date": "1999-01-01"}])
    m, q, review = search_with_fallback("告白", 2010)
    assert m and m["id"] == 7 and review is True


# ---------- 本地优先复用 ----------

def _touch(root, rel):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")
    return p


@pytest.fixture()
def two_libs(tmp_path):
    root_a = tmp_path / "local"
    root_b = tmp_path / "remote"
    root_a.mkdir()
    root_b.mkdir()
    a = store.create_library(name=f"m-a-{tmp_path.name}", path=str(root_a))
    b = store.create_library(name=f"m-b-{tmp_path.name}", path=str(root_b))
    library_paths.invalidate_cache()
    yield (a, root_a), (b, root_b)
    store.delete_library(a["id"])
    store.delete_library(b["id"])
    library_paths.invalidate_cache()


def test_local_first_binds_from_other_library(two_libs, monkeypatch):
    (a, root_a), (b, root_b) = two_libs
    # 本地库已有正确匹配：硬核亨利 / Hardcore Henry（原标题英文）
    rel_a = "电影/硬核亨利 (2015)/硬核亨利 (2015).mkv"
    _touch(root_a, rel_a)
    mid_a = store.upsert_movie_by_path(rel_a, library_id=a["id"])
    store.update_movie_meta(mid_a, title="硬核亨利", original_title="Hardcore Henry",
                            year=2015, tmdb_id=325348)
    store.upsert_tmdb_cache(325348, {"title": "硬核亨利", "original_title": "Hardcore Henry",
                                     "year": 2015, "media_type": "movie"}, {}, "")
    # 远程库同片用英文文件名；远端搜索若被调用会返回错片——本地优先不应触网
    rel_b = "Hardcore.Henry.2015/Hardcore.Henry.2015.mkv"
    _touch(root_b, rel_b)

    def _boom(*a, **k):
        raise AssertionError("local-first 命中时不应调用 TMDB")
    monkeypatch.setattr(scanner.scan, "search_with_fallback", _boom)
    index = meta_local.library_index()
    r = scanner.scan_file(storage.backend_for(b["id"]), rel_b, local_index=index)
    assert r["status"].startswith("ok"), r
    assert r["match_source"] == "library"
    row = store.get_by_path(rel_b, library_id=b["id"])
    assert row["tmdb_id"] == 325348


def test_force_still_prefers_local(two_libs, monkeypatch):
    """force 重扫同样先走本地优先（纠正远程错配的路径）；手动匹配才是换绑口。"""
    (a, root_a), (b, root_b) = two_libs
    rel_a = "电影/硬核亨利 (2015)/硬核亨利 (2015).mkv"
    _touch(root_a, rel_a)
    mid_a = store.upsert_movie_by_path(rel_a, library_id=a["id"])
    store.update_movie_meta(mid_a, title="硬核亨利", original_title="Hardcore Henry",
                            year=2015, tmdb_id=325348)
    rel_b = "Hardcore.Henry.2015/Hardcore.Henry.2015.mkv"
    _touch(root_b, rel_b)
    called = {"n": 0}

    def _search(title, year):
        called["n"] += 1
        return None, title, False
    monkeypatch.setattr(scanner.scan, "search_with_fallback", _search)
    r = scanner.scan_file(storage.backend_for(b["id"]), rel_b, force=True)
    assert called["n"] == 0              # force 也先命中本地 → 不触网
    assert store.get_by_path(rel_b, library_id=b["id"])["tmdb_id"] == 325348


def test_local_first_uses_cjk_filename_segment(two_libs, monkeypatch):
    """告白.Confessions.2010.mp4：guessit 标题是 Confessions，靠文件名中文段命中本地 54186。"""
    (a, root_a), (b, root_b) = two_libs
    rel_a = "电影/告白 (2010)/告白 (2010).mkv"
    _touch(root_a, rel_a)
    mid_a = store.upsert_movie_by_path(rel_a, library_id=a["id"])
    store.update_movie_meta(mid_a, title="告白", original_title="告白",
                            year=2010, tmdb_id=54186)
    store.upsert_tmdb_cache(54186, {"title": "告白", "year": 2010,
                                    "media_type": "movie"}, {}, "")
    rel_b = "告白.Confessions.2010/Confessions.2010.mp4"
    _touch(root_b, rel_b)

    def _boom(*a, **k):
        raise AssertionError("中文段命中时不应调用 TMDB")
    monkeypatch.setattr(scanner.scan, "search_with_fallback", _boom)
    index = meta_local.library_index()
    r = scanner.scan_file(storage.backend_for(b["id"]), rel_b, local_index=index)
    assert r["status"].startswith("ok"), r
    assert store.get_by_path(rel_b, library_id=b["id"])["tmdb_id"] == 54186
    assert meta_local.parse_title_variants("告白.Confessions.2010.mp4") == [
        "Confessions", "告白"]


# ---------- P4：BDMV/DVD 原盘碎片跳过 ----------

def test_scan_skips_bdmv_fragments(tmp_path):
    root = tmp_path / "lib"
    (root / "电影" / "某片 (2008)" / "BDMV" / "STREAM").mkdir(parents=True)
    (root / "电影" / "某片 (2008)" / "BDMV" / "STREAM" / "00000.m2ts").write_bytes(b"x")
    (root / "电影" / "某片 (2008)" / "正片.mkv").write_bytes(b"x")
    lib = store.create_library(name=f"bdmv-{tmp_path.name}", path=str(root))
    library_paths.invalidate_cache()
    try:
        out = scanner.scan_all(library_id=lib["id"])
        files = [r["file"] for r in out]
        assert all("BDMV" not in f for f in files), out
        assert any(f.endswith("正片.mkv") for f in files), out
    finally:
        store.delete_library(lib["id"])
        library_paths.invalidate_cache()
