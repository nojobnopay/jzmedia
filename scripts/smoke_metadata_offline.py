#!/usr/bin/env python3
"""离线/降级刮削冒烟（发布前自检；临时目录，不触网）。

覆盖：TMDB 不可用时 → NFO 导入匹配 → match_index 本地匹配；
      IMDb TSV 导入 → 本地候选；手动搜索接口离线回退。

运行：.venv/bin/python scripts/smoke_metadata_offline.py
"""
import gzip
import os
import pathlib
import sys
import tempfile


def _fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    raise SystemExit(1)


def main() -> None:
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="jzmedia-smoke-meta-"))
    os.environ["DATA_DIR"] = str(tmp / "data")
    os.environ["MEDIA_ROOT"] = str(tmp / "media")
    for k in ("TMDB_READ_TOKEN", "TMDB_API_KEY", "JZMEDIA_TOKEN"):
        os.environ.pop(k, None)
    root = pathlib.Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    from fastapi.testclient import TestClient
    from app.main import app
    from app import scanner, store
    from app import tmdb

    def _offline(*a, **kw):
        raise RuntimeError("smoke: tmdb offline")
    tmdb.search_movie = _offline

    with TestClient(app) as c:
        media = pathlib.Path(os.environ["MEDIA_ROOT"])

        # 1) 本地索引匹配（TMDB 缓存候选）
        store.upsert_match_entry("tmdb", "990001", "movie", "Offline Smoke", "",
                                 2001, 990001, "tt990001", {})
        store.upsert_tmdb_cache(990001, {"title": "Offline Smoke", "year": 2001,
                                         "media_type": "movie"})
        rel = "smoke/Offline.Smoke.2001.mkv"
        p = media / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
        r = scanner.scan_one(str(p))
        if not str(r.get("status", "")).startswith("ok"):
            _fail(f"本地索引匹配失败: {r}")
        row = store.get_by_path(rel)
        if not row or row["tmdb_id"] != 990001:
            _fail("本地匹配未绑定 tmdb_id")

        # 2) NFO 导入匹配
        rel2 = "smoke2/Nfo.Smoke.2002.mkv"
        p2 = media / rel2
        p2.parent.mkdir(parents=True, exist_ok=True)
        p2.write_bytes(b"x")
        (p2.parent / "movie.nfo").write_text(
            '<?xml version="1.0"?><movie><title>Nfo Smoke</title><year>2002</year>'
            '<uniqueid type="tmdb">990002</uniqueid></movie>', encoding="utf-8")
        store.upsert_tmdb_cache(990002, {"title": "Nfo Smoke", "year": 2002,
                                         "media_type": "movie"})
        r2 = scanner.scan_one(str(p2))
        if r2.get("match_source") != "nfo":
            _fail(f"NFO 导入失败: {r2}")

        # 3) 手动搜索离线回退
        store.upsert_match_entry("tmdb", "990003", "movie", "接口离线 Smoke", "",
                                 2003, 990003, "", {})
        d = c.get("/api/tmdb/search", params={"q": "接口离线 Smoke"}).json()
        if d.get("source") != "offline" or not d.get("items"):
            _fail(f"离线搜索回退失败: {d.get('source')}")

        # 4) IMDb 数据集导入 + 候选
        tsv = tmp / "title.basics.tsv.gz"
        with gzip.open(tsv, "wt", encoding="utf-8", newline="") as fh:
            fh.write("tconst\ttitleType\tprimaryTitle\toriginalTitle\tstartYear\n")
            fh.write("tt9900001\tmovie\tIMDb Smoke\t\t1999\n")
        n = store.import_imdb_tsv(str(tsv))["imported"]
        if n != 1:
            _fail(f"IMDb 导入条数异常: {n}")
        from app.metadata import local
        if not any(x.imdb_id == "tt9900001" for x in local.search("IMDb Smoke", 1999)):
            _fail("IMDb 候选未进本地索引")

    print("PASS smoke_metadata_offline：本地索引 / NFO 导入 / 离线搜索回退 / IMDb 导入")


if __name__ == "__main__":
    main()
