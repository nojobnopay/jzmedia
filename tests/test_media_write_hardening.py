"""2026-09 换绑/刷新落盘加固：artwork 失败重试、同片媒体任务串行。"""
import threading
import time

from app.scanner import persist


def _mock_media(monkeypatch, active: dict | None = None):
    """把 finish_tmdb_media 的耗时环节换成可控 fake（不触网/不落盘）。"""
    monkeypatch.setattr(persist, "ensure_movie_poster", lambda *a, **k: "")
    monkeypatch.setattr(persist, "sync_persons", lambda *a, **k: 0)

    def fake_nfo(mid, abs_path="", backend=None, rel=""):
        if active is not None:
            active["n"] += 1
            active["max"] = max(active["max"], active["n"])
            time.sleep(0.05)
            active["n"] -= 1
        return True

    monkeypatch.setattr(persist, "_write_nfo_for", fake_nfo)


def test_finish_tmdb_media_artwork_retry(monkeypatch):
    _mock_media(monkeypatch)
    calls = {"n": 0}

    def fake_art(*a, **k):
        calls["n"] += 1
        return {"ok": calls["n"] > 1, "reason": "boom"}

    monkeypatch.setattr(persist.artwork, "write_for_movie", fake_art)
    out = persist.finish_tmdb_media(880001, {"id": 990001, "credits": {}},
                                    "", "", "")
    assert calls["n"] == 2                     # 首次失败 → 重试一次
    assert out["artwork"]["ok"] is True


def test_finish_tmdb_media_artwork_retry_failed_visible(monkeypatch, caplog):
    _mock_media(monkeypatch)
    calls = {"n": 0}

    def fake_art(*a, **k):
        calls["n"] += 1
        return {"ok": False, "reason": "smb down"}

    monkeypatch.setattr(persist.artwork, "write_for_movie", fake_art)
    with caplog.at_level("WARNING", logger="jzmedia.scanner.persist"):
        out = persist.finish_tmdb_media(880002, {"id": 990002, "credits": {}},
                                        "", "", "")
    assert calls["n"] == 2
    assert out["artwork"]["ok"] is False
    assert any("artwork retry failed" in r.message for r in caplog.records)


def test_media_lock_serializes_same_movie(monkeypatch):
    active = {"n": 0, "max": 0}
    _mock_media(monkeypatch, active)
    monkeypatch.setattr(persist.artwork, "write_for_movie",
                        lambda *a, **k: {"ok": True, "wrote": []})
    detail = {"id": 990003, "credits": {}}
    ts = [threading.Thread(target=persist.finish_tmdb_media,
                           args=(880003, detail, "", "", "")) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert active["max"] == 1                  # 同片串行，不并发进媒体写入
