#!/usr/bin/env python3
"""双库隔离冒烟（发布前自检；用临时目录，不触碰真实 data/ 与媒体库）。

覆盖：建两库 → 同名相对路径文件 → 分库扫描 → 搜索/版本/统计按库隔离
      → 删库只清记录（磁盘文件仍在）。

运行：.venv/bin/python scripts/smoke_multi_library.py
"""
import os
import pathlib
import sys
import tempfile


def _fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    raise SystemExit(1)


def main() -> None:
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="jzmedia-smoke-lib-"))
    os.environ["DATA_DIR"] = str(tmp / "data")
    os.environ["MEDIA_ROOT"] = str(tmp / "media")
    for k in ("TMDB_READ_TOKEN", "TMDB_API_KEY", "JZMEDIA_TOKEN"):
        os.environ.pop(k, None)
    root = pathlib.Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    from fastapi.testclient import TestClient
    from app.main import app
    from app import library_paths, scanner, store
    from app import tmdb

    tmdb.search_movie = lambda q, year=None: []   # 禁止触网

    with TestClient(app) as c:
        lib_a = tmp / "libA"
        lib_b = tmp / "libB"
        for lib in (lib_a, lib_b):
            (lib / "a").mkdir(parents=True)
            (lib / "a" / "Same.Movie.2000.mkv").write_bytes(b"x")

        ra = c.post("/api/libraries", json={"name": "smoke-a", "path": str(lib_a)})
        rb = c.post("/api/libraries", json={"name": "smoke-b", "path": str(lib_b)})
        if ra.status_code != 200 or rb.status_code != 200:
            _fail(f"create libraries: {ra.status_code} {rb.status_code} {ra.text} {rb.text}")
        a, b = ra.json(), rb.json()

        scanner.scan_all(library_id=a["id"])
        scanner.scan_all(library_id=b["id"])
        rel = "a/Same.Movie.2000.mkv"
        row_a = store.get_by_path(rel, library_id=a["id"])
        row_b = store.get_by_path(rel, library_id=b["id"])
        if not row_a or not row_b or row_a["id"] == row_b["id"]:
            _fail("两库同名文件未各自建行")

        items_a = c.get(f"/api/movies?library={a['id']}").json()["items"]
        ids_a = {x["id"] for x in items_a}
        if row_a["id"] not in ids_a or row_b["id"] in ids_a:
            _fail("列表 library 过滤失效（串库）")
        exp = store.expand_ids_to_versions([row_a["id"], row_b["id"]])
        if exp[row_a["id"]] != [row_a["id"]] or exp[row_b["id"]] != [row_b["id"]]:
            _fail(f"版本展开越库: {exp}")
        stats = c.get(f"/api/jobs/stats?library={a['id']}").json()
        if stats["movies"] != 1:
            _fail(f"按库统计应=1: {stats}")

        rd = c.delete(f"/api/libraries/{b['id']}")
        if rd.status_code != 200 or rd.json().get("movies") != 1:
            _fail(f"删库事务异常: {rd.status_code} {rd.text}")
        if not (lib_b / rel).is_file():
            _fail("删库不该删除磁盘文件")
        if store.get_by_path(rel, library_id=b["id"]) is not None:
            _fail("删库后仍有残留行")
        library_paths.invalidate_cache()

    print("PASS smoke_multi_library：双库隔离 / 列表与统计 scope / 删库不删文件")


if __name__ == "__main__":
    main()
