"""B2 批次（一致性/并发）回归网：P1-06 整理/恢复的 DB 占用保护。

红→绿约定：修复前 `_move_one`/`_restore_one` 会先 rename 再写库，UNIQUE 冲突时
文件已挪走而库行未更新；`_collect_plans` 预览也不标冲突。
"""
import pathlib

import pytest

from app import store
from app.config import settings
from app.routers import files as files_router


def _touch(root: pathlib.Path, rel: str) -> pathlib.Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")
    return p


def _row(rel: str, title="A", year=2020, tmdb_id=None) -> int:
    mid = store.upsert_movie_by_path(rel)
    meta = {"title": title, "year": year}
    if tmdb_id:
        meta["tmdb_id"] = tmdb_id
    store.update_movie_meta(mid, **meta)
    return mid


def test_move_one_refuses_db_occupied_target(media_root):
    src = _touch(media_root, "raw/a.mkv")
    a = _row("raw/a.mkv", title="A")
    # B 行占着目标路径，但磁盘文件不存在（missing 行，常见于清理前）
    store.upsert_movie_by_path("raw/b.mkv")

    r = files_router._move_one({"id": a, "from": "raw/a.mkv", "to": "raw/b.mkv"})

    assert r["status"] == "conflict_db_occupied"
    assert store.get_movie(a)["file_path"] == "raw/a.mkv"
    assert src.is_file()  # 文件未被挪走


def test_restore_one_refuses_db_occupied_target(media_root):
    src = _touch(media_root, "now/a.mkv")
    a = _row("now/a.mkv", title="A")
    store.update_movie_local(a, original_file_path="orig/a.mkv")
    store.upsert_movie_by_path("orig/a.mkv")   # 目标路径被另一行占用（文件缺失）

    r = files_router._restore_one(store.get_movie(a), dry_run=False)

    assert r["status"] == "conflict_db_occupied"
    assert store.get_movie(a)["file_path"] == "now/a.mkv"
    assert src.is_file()


def test_collect_plans_flags_db_occupied(media_root):
    _touch(media_root, "raw/A.mkv")
    a = _row("raw/A.mkv", title="A", year=2020)
    # 就地归档目标：raw/A (2020)/A (2020).mkv；该路径被另一行占用（文件缺失）
    store.upsert_movie_by_path("raw/A (2020)/A (2020).mkv")

    plans, conflicts = files_router._collect_plans(only={a})

    assert plans == []
    assert any(c.get("status") == "conflict_db_occupied" and c["id"] == a
               for c in conflicts)


def test_move_one_still_moves_when_target_free(media_root):
    _touch(media_root, "raw/c.mkv")
    a = _row("raw/c.mkv", title="C")

    r = files_router._move_one({"id": a, "from": "raw/c.mkv", "to": "raw/d.mkv"})

    assert r["status"] == "moved"
    assert store.get_movie(a)["file_path"] == "raw/d.mkv"
    assert (media_root / "raw/d.mkv").is_file()


# ---------- B5a-4（R09-D4）：/files/clean 默认 dry_run ----------

def test_clean_defaults_to_dry_run(media_root):
    mid = _row("clean-ghost/missing.mkv")   # 不建文件 → 属于失效行
    prev = files_router.clean({"ids": [mid]})
    assert prev["dry_run"] is True
    assert any(p["id"] == mid for p in prev["plans"])
    assert store.get_movie(mid) is not None      # 预览不删

    out = files_router.clean({"ids": [mid], "dry_run": False})
    assert out["dry_run"] is False
    assert store.get_movie(mid) is None


# ---------- B5a-8（R09-Q4/R12-B3）：旧兼容口已删除 ----------

def test_legacy_endpoints_removed():
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app)
    assert c.post("/api/files/rename", json={}).status_code in (404, 405)
    assert c.post("/api/files/relocate", json={}).status_code in (404, 405)
    # 旧 HLS 直连口：不再返回 HLS 播放列表（未知 GET 落到 SPA catch-all）
    r = c.get("/api/stream/1/master.m3u8")
    assert "mpegurl" not in (r.headers.get("content-type") or "")
    assert "#EXTM3U" not in r.text


# ---------- B9 后续：重新匹配后的归档推荐 ----------

def test_organize_hint_relocate(media_root):
    from fastapi.testclient import TestClient
    from app.main import app
    from app import store
    c = TestClient(app)
    rel = "待整理/Hint.Movie.2018.mkv"
    p = media_root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")
    mid = store.upsert_movie_by_path(rel)
    store.update_movie_meta(mid, tmdb_id=99001, title="Hint Movie", year=2018,
                            region="华语", original_language="zh")
    d = c.get(f"/api/movies/{mid}/organize-hint").json()
    assert d["needs"] is True and d["params"]["mode"] == "relocate"
    assert d["params"]["from_prefix"] == "待整理"
    assert d["plans"] and d["plans"][0]["to"].startswith("电影/")
    # 未匹配行 → 不给归档建议
    mid2 = store.upsert_movie_by_path("待整理/Unmatched.2020.mkv")
    d2 = c.get(f"/api/movies/{mid2}/organize-hint").json()
    assert d2["needs"] is False and d2["reason"] == "unmatched"


def test_organize_hint_inplace_when_already_canonical(media_root):
    from fastapi.testclient import TestClient
    from app.main import app
    from app import store
    c = TestClient(app)
    # 已规范：片目录 + 规范文件名 → 无计划
    rel = "电影/华语/Hint Movie (2018)/Hint Movie (2018).mkv"
    p = media_root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")
    mid = store.upsert_movie_by_path(rel)
    store.update_movie_meta(mid, tmdb_id=99002, title="Hint Movie", year=2018,
                            region="华语", original_language="zh")
    d = c.get(f"/api/movies/{mid}/organize-hint").json()
    assert d["params"]["mode"] == "inplace"
    assert d["needs"] is False            # 已规范 → 无计划
    # 正式库内但平铺/命名不规范 → 就地归档计划
    rel2 = "电影/华语/Hint.Other.2018.mkv"
    p2 = media_root / rel2
    p2.write_bytes(b"x")
    mid2 = store.upsert_movie_by_path(rel2)
    store.update_movie_meta(mid2, tmdb_id=99003, title="Hint Other", year=2018,
                            region="华语", original_language="zh")
    d2 = c.get(f"/api/movies/{mid2}/organize-hint").json()
    assert d2["params"]["mode"] == "inplace" and d2["needs"] is True
    assert d2["plans"][0]["to"].startswith("电影/华语/Hint Other (2018)/")


# ---------- B9 后续：执行路径回归（拆分后漏 import 曾致 500） ----------

def test_organize_execute_inplace_moves_file(media_root):
    from fastapi.testclient import TestClient
    from app.main import app
    from app import store
    rel = "raw/Exec.One.2015.mkv"
    p = media_root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")
    mid = store.upsert_movie_by_path(rel)
    store.update_movie_meta(mid, tmdb_id=88001, title="Exec One", year=2015,
                            region="华语", original_language="zh")
    c = TestClient(app)
    prev = c.post("/api/files/organize", json={"mode": "inplace", "ids": [mid],
                                               "dry_run": True}).json()
    assert prev["plans"], prev
    d = c.post("/api/files/organize", json={"mode": "inplace", "ids": [mid],
                                            "dry_run": False}).json()
    assert [r["status"] for r in d["results"]] == ["moved"]
    new_rel = (store.get_movie(mid) or {}).get("file_path")
    assert new_rel and new_rel != rel
    assert (media_root / new_rel).exists()
    assert not (media_root / rel).exists()


def test_restore_execute_moves_file_back(media_root):
    from app import store
    from app.routers import files as files_router
    rel = "moved/Back.Movie.2016.mkv"
    p = media_root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")
    mid = store.upsert_movie_by_path(rel)
    # 模拟历史原始位置（审计列只在首次入库写入，这里直接改库构造场景）
    store.update_movie_meta(mid, tmdb_id=88002, title="Back Movie", year=2016,
                            original_file_path="origin/Back.Movie.2016.mkv")
    r = files_router._restore_one(store.get_movie(mid), dry_run=False)
    assert r["status"] == "restored", r
    assert (media_root / "origin/Back.Movie.2016.mkv").exists()
    assert (store.get_movie(mid) or {}).get("file_path") == "origin/Back.Movie.2016.mkv"


def test_organize_execute_relocate_via_hint_params(media_root):
    """用户实际路径：详情页匹配 → 归档推荐（relocate）→ 确认执行。"""
    from fastapi.testclient import TestClient
    from app.main import app
    from app import store
    rel = "待整理/Relocate.Me.2017.mkv"
    p = media_root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")
    mid = store.upsert_movie_by_path(rel)
    store.update_movie_meta(mid, tmdb_id=88003, title="Relocate Me", year=2017,
                            region="华语", original_language="zh")
    c = TestClient(app)
    hint = c.get(f"/api/movies/{mid}/organize-hint").json()
    assert hint["needs"] and hint["params"]["mode"] == "relocate"
    d = c.post("/api/files/organize", json={**hint["params"], "ids": [mid],
                                            "dry_run": False}).json()
    assert [r["status"] for r in d["results"]] == ["moved"], d
    new_rel = (store.get_movie(mid) or {}).get("file_path")
    assert new_rel.startswith("电影/华语/Relocate Me (2017)/")
    assert (media_root / new_rel).exists()


# ---------- B11 审计补齐：链式排序 / 预览标注 / 花絮跳过明细 / orphan 猜测 ----------

def test_ordered_plans_chain_and_cycle():
    from app.routers.files.planner import _ordered_plans
    chain = [{"id": 1, "from": "a.mkv", "to": "b.mkv"},
             {"id": 2, "from": "b.mkv", "to": "c.mkv"}]
    assert [p["id"] for p in _ordered_plans(chain)] == [2, 1]
    cyc = [{"id": 1, "from": "a.mkv", "to": "b.mkv"},
           {"id": 2, "from": "b.mkv", "to": "a.mkv"}]
    assert len(_ordered_plans(cyc)) == 2          # 环：原样返回不丢项
    free = [{"id": 1, "from": "a.mkv", "to": "x/a.mkv"}]
    assert [p["id"] for p in _ordered_plans(free)] == [1]


def test_organize_preview_marks_source_missing(media_root):
    from fastapi.testclient import TestClient
    from app.main import app
    from app import store
    mid = store.upsert_movie_by_path("ghost/Not.On.Disk.2010.mkv")
    store.update_movie_meta(mid, tmdb_id=99101, title="Ghost Movie", year=2010,
                            region="华语", original_language="zh")
    c = TestClient(app)
    d = c.post("/api/files/organize", json={"mode": "inplace", "ids": [mid],
                                            "dry_run": True}).json()
    plan = next(p for p in d["plans"] if p["id"] == mid)
    assert plan.get("status") == "source_missing"


def test_orphan_guessed_title(media_root):
    from fastapi.testclient import TestClient
    from app.main import app
    from app import store
    store.upsert_extra("scatter/Making.Of.The.Guess.1998.mkv", None, "extra")
    c = TestClient(app)
    d = c.get("/api/extras/orphans").json()
    row = next(x for x in d["items"] if "Guess" in x["file_path"])
    assert row.get("guessed_title") and row.get("guessed_year") == 1998


def test_collect_reports_skipped_conflict(media_root):
    from fastapi.testclient import TestClient
    from app.main import app
    from app import store
    movie_dir = media_root / "lib/Film (2020)"
    movie_dir.mkdir(parents=True, exist_ok=True)
    (movie_dir / "Film (2020).mkv").write_bytes(b"x")
    mid = store.upsert_movie_by_path("lib/Film (2020)/Film (2020).mkv")
    store.update_movie_meta(mid, tmdb_id=99102, title="Film", year=2020)
    (media_root / "scatter").mkdir(parents=True, exist_ok=True)
    (media_root / "scatter/featurette.mkv").write_bytes(b"z")
    store.upsert_extra("scatter/featurette.mkv", mid, "extra")
    # 目标已存在 → 不覆盖，应上报 skipped
    (movie_dir / "extras").mkdir(exist_ok=True)
    (movie_dir / "extras/featurette.mkv").write_bytes(b"y")
    c = TestClient(app)
    d = c.post("/api/extras/collect", json={"dry_run": False, "ids": [mid]}).json()
    assert d["moved"] == 0 and d["skipped"]
    assert any(sk["file_path"] == "scatter/featurette.mkv"
               and sk["reason"] == "target_exists" for sk in d["skipped"])
