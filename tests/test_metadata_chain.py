"""Provider 链失败冷却（E 阶段补全，MULTI_LIBRARY_PLAN §9.1）：
连续失败暂停、冷却期跳过、成功清零、空结果不误冷却、状态接口与重置。"""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.metadata import chain, state

client = TestClient(app)


@pytest.fixture(autouse=True)
def _clean_provider_state():
    state.reset()
    yield
    state.reset()


def test_fail_then_cooldown_and_skip(monkeypatch):
    calls = {"local": 0}

    def boom(*a, **k):
        calls["local"] += 1
        raise RuntimeError("offline index broken")

    monkeypatch.setattr("app.metadata.local.search", boom)
    monkeypatch.setattr("app.metadata.wikidata.search", lambda *a, **k: [])
    for _ in range(state.FAIL_THRESHOLD):
        assert chain.search("测试片") == []
    assert calls["local"] == state.FAIL_THRESHOLD
    assert not state.available("local")

    # 冷却期内跳过：不再调用，也不会把异常抛出去
    assert chain.search("测试片") == []
    assert calls["local"] == state.FAIL_THRESHOLD

    snap = {p["name"]: p for p in state.snapshot()}
    assert snap["local"]["cooldown_remaining"] > 0
    assert "offline index broken" in snap["local"]["last_error"]

    # 重置（设置页按钮/排障）后可再次调用
    state.reset("local")
    assert state.available("local")
    assert chain.search("测试片") == []
    assert calls["local"] == state.FAIL_THRESHOLD + 1


def test_success_clears_failure_count(monkeypatch):
    state.note_fail("local", "e1")
    state.note_fail("local", "e2")
    hits = [chain.Candidate(title="命中", year=2000, tmdb_id=1,
                            source="local", source_id="1")]
    monkeypatch.setattr("app.metadata.local.search", lambda *a, **k: hits)
    got = chain.search("测试片")
    assert got and got[0].title == "命中"
    snap = {p["name"]: p for p in state.snapshot()}
    assert snap["local"]["fails"] == 0 and snap["local"]["available"]


def test_empty_result_keeps_provider_healthy(monkeypatch):
    # 正常「无结果」不是失败：provider 仍可用，不会被冷却
    monkeypatch.setattr("app.metadata.local.search", lambda *a, **k: [])
    monkeypatch.setattr("app.metadata.wikidata.search", lambda *a, **k: [])
    for _ in range(state.FAIL_THRESHOLD + 2):
        assert chain.search("不存在的片名") == []
    assert state.available("local")
    assert state.available("wikidata")


def test_providers_api_and_reset():
    state.note_fail("tmdb", "401 unauthorized")
    r = client.get("/api/metadata/providers").json()
    assert "local" in r["known"] and "tmdb" in r["known"]
    assert r["fail_threshold"] == state.FAIL_THRESHOLD
    assert r["cooldown_sec"] == state.COOLDOWN_SEC
    row = {p["name"]: p for p in r["providers"]}["tmdb"]
    assert row["fails"] == 1 and "401" in row["last_error"]

    r2 = client.post("/api/metadata/providers/reset", json={"name": "tmdb"}).json()
    row2 = {p["name"]: p for p in r2["providers"]}["tmdb"]
    assert row2["fails"] == 0 and row2["last_error"] == ""

    assert client.post("/api/metadata/providers/reset",
                       json={"name": "nope"}).status_code == 422
    # 全量重置兜底
    state.note_fail("wikidata", "gw timeout")
    r3 = client.post("/api/metadata/providers/reset").json()
    assert all(p["fails"] == 0 for p in r3["providers"])
