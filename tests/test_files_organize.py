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
