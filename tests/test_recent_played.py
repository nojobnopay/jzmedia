"""继续观看/最近播放（GET /api/movies/recent-played）回归：阈值、已看过滤、排序、分组去重、库范围。"""
from fastapi.testclient import TestClient

from app import store
from app.main import app
from app.store import _base

client = TestClient(app)


def _mk(path: str, tmdb: int, title: str, year: int = 2020, watched: int = 0) -> int:
    mid = store.upsert_movie_by_path(path)
    store.update_movie_meta(mid, tmdb_id=tmdb, title=title, year=year, watched=watched)
    return mid


def _progress(mid: int, pos: float, dur: float, played_at: int | None = None) -> None:
    store.save_progress(mid, pos, dur)
    if played_at is not None:
        with _base._lock, _base._conn() as c:
            c.execute("UPDATE playback_progress SET updated_at=?"
                      " WHERE kind='movie' AND item_id=?", (played_at, mid))


def _recent(**params) -> list[dict]:
    d = client.get("/api/movies/recent-played", params={"limit": 50, **params}).json()
    return d["items"]


def _ids(items) -> list[int]:
    return [x["id"] for x in items]


def test_recent_played_filters_thresholds_and_order():
    a = _mk("rp/A.mkv", 991001, "RP 在看", watched=0)
    _progress(a, 600, 7200, played_at=1000)
    b = _mk("rp/B.mkv", 991002, "RP 快完", watched=0)
    _progress(b, 7000, 7200, played_at=2000)      # 已看超过95%，且剩余200s → 已完成
    c = _mk("rp/C.mkv", 991003, "RP 刚点开", watched=0)
    _progress(c, 10, 7200, played_at=3000)        # <15s 不算开看
    d = _mk("rp/D.mkv", 991004, "RP 已看", watched=1)
    _progress(d, 600, 7200, played_at=4000)
    e = _mk("rp/E.mkv", 991005, "RP 更近", watched=0)
    _progress(e, 100, 7200, played_at=5000)

    ids = _ids(_recent())
    assert a in ids and e in ids
    assert b not in ids and c not in ids and d not in ids
    assert ids.index(e) < ids.index(a)            # 按最后播放时间倒序

    ids2 = _ids(_recent(include_finished="true"))
    assert d in ids2 and b in ids2                # 含已看完（自动阈值 + 手动已看）
    assert ids2.index(e) < ids2.index(d) < ids2.index(a)

    item = next(x for x in _recent() if x["id"] == a)
    p = item["progress"]
    assert p["version_id"] == a
    assert p["percent"] == round(600 / 7200, 4)
    assert p["remaining_sec"] == 6600
    assert p["last_played_at"] == 1000
    assert item["added_at"] > 0


def test_recent_played_grouped_dedupe_keeps_latest_version():
    v1 = _mk("rp/g1.mkv", 991010, "RP 多版本")
    v2 = _mk("rp/g2.mkv", 991010, "RP 多版本")
    _progress(v1, 300, 7200, played_at=100)
    _progress(v2, 400, 7200, played_at=200)
    items = [x for x in _recent() if x["tmdb_id"] == 991010]
    assert len(items) == 1
    assert items[0]["progress"]["version_id"] == v2     # 最近播放的那个版本
    assert items[0]["version_count"] == 2


def test_recent_played_library_scope_and_sentinel():
    m = _mk("rp/scope.mkv", 991020, "RP 库范围")
    _progress(m, 300, 7200, played_at=100)
    assert m not in _ids(_recent(library="-1"))         # 空哨兵绝不退化成全库
    assert m in _ids(_recent(library="1"))
    assert _recent(media_library=987654) == []          # 未知媒体库 → 空
