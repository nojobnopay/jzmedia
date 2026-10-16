import importlib
import json
from pathlib import Path
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import cache_settings, data_usage, store
from app.main import app
from app.routers.stream import common, previews

GIB = 1024 ** 3
VALUES = {"data_warning_gb": 100, "transcode_cache_gb": 40,
          "preview_cache_gb": 30}


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    import app.store._base as base
    monkeypatch.setattr(base, "DB_PATH", str(tmp_path / "cache-settings.db"))
    monkeypatch.delenv("TRANSCODE_CACHE_GB", raising=False)
    monkeypatch.delenv("PREVIEW_CACHE_GB", raising=False)
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setattr(data_usage.settings, "data_dir", str(root))
    monkeypatch.setattr(data_usage.settings, "media_root", str(tmp_path / "media"))
    data_usage.invalidate()
    store.init_db()
    yield
    data_usage.invalidate()


def test_environment_defaults_and_persistent_live_limits(monkeypatch):
    client = TestClient(app)
    assert client.get("/api/cache-settings").json() == {
        "data_warning_gb": 20, "transcode_cache_gb": 10, "preview_cache_gb": 10}
    monkeypatch.setenv("TRANSCODE_CACHE_GB", "8")
    monkeypatch.setenv("PREVIEW_CACHE_GB", "2")
    assert common._cache_cap_bytes() == 8 * GIB
    assert previews._cache_cap_bytes() == 2 * GIB
    previews._auto_wake.clear()
    assert client.put("/api/cache-settings", json=VALUES).json() == VALUES
    assert previews._auto_wake.is_set()
    assert common._cache_cap_bytes() == 40 * GIB
    assert previews._cache_cap_bytes() == 30 * GIB
    importlib.reload(cache_settings)
    assert client.get("/api/cache-settings").json() == VALUES
    assert client.put("/api/cache-settings", json={**VALUES, "transcode_cache_gb": 0}).status_code == 422
    assert common._cache_cap_bytes() == 40 * GIB


@pytest.mark.parametrize("change", [
    {"data_warning_gb": 0}, {"preview_cache_gb": 0}, {"transcode_cache_gb": -1},
    {"data_warning_gb": 102400.1}, {"preview_cache_gb": 0.15},
    {"data_warning_gb": "NaN"}, {"transcode_cache_gb": "Infinity"},
    {"preview_cache_gb": None}, {"unknown": 1},
])
def test_invalid_update_does_not_partially_save(change):
    client = TestClient(app)
    assert client.put("/api/cache-settings", json=VALUES).status_code == 200
    assert client.put("/api/cache-settings", json={**VALUES, "transcode_cache_gb": 25, **change}).status_code == 422
    assert client.get("/api/cache-settings").json() == VALUES


def test_warning_and_auto_pause_update_without_extra_rescanning(monkeypatch):
    scans = []

    def scan():
        scans.append(True)
        return {"ok": True, "total_bytes": 50 * GIB, "error": "",
                "categories": {"database": 0, "posters": 0, "previews": 0,
                               "transcode": 50 * GIB, "other": 0}}

    monkeypatch.setattr(data_usage, "_scan", scan)
    monkeypatch.setattr(previews, "_auto_mode", lambda: "all")
    monkeypatch.setattr(previews, "_playback_busy", lambda: False)
    monkeypatch.setattr(previews, "_auto_allowed_now", lambda: True)
    client = TestClient(app)
    assert client.get("/api/data-usage?refresh=true").json()["over_limit"]
    assert previews._auto_pause_reason() == "data_limit"
    assert client.put("/api/cache-settings", json=VALUES).status_code == 200
    usage = client.get("/api/data-usage").json()
    assert usage["warning_bytes"] == 100 * GIB and not usage["over_limit"]
    assert previews._auto_pause_reason() == ""
    assert client.put("/api/cache-settings", json={
        "data_warning_gb": 50, "transcode_cache_gb": 20, "preview_cache_gb": 20,
    }).status_code == 200
    assert previews._auto_pause_reason() == "data_limit"
    assert len(scans) == 3  # Initial statistics plus one fresh scan per successful save.


def test_limits_require_management_and_write_token():
    client = TestClient(app)
    store.set_setting("jzmedia_token", "cache-settings-test-token")
    assert client.put("/api/cache-settings", json=VALUES).status_code == 401
    headers = {"X-Api-Token": "cache-settings-test-token"}
    assert client.put("/api/cache-settings", json=VALUES, headers=headers).status_code == 200
    store.set_setting("jzmedia_token", "")
    assert client.post("/api/viewing/password", json={"new_password": "123456"}).status_code == 200
    assert client.get("/api/cache-settings").status_code == 403
    assert client.put("/api/cache-settings", json=VALUES).status_code == 403


@pytest.mark.parametrize("raw", ["not-a-number", "NaN", "Infinity"])
def test_invalid_environment_values_use_finite_defaults(monkeypatch, raw):
    monkeypatch.setenv("TRANSCODE_CACHE_GB", raw)
    monkeypatch.setenv("PREVIEW_CACHE_GB", raw)
    assert common._cache_cap_bytes() == 10 * GIB
    assert previews._cache_cap_bytes() == 10 * GIB


BUDGET_CASES = json.loads((Path(__file__).parent / "fixtures" / "cache_budget.json").read_text())


@pytest.mark.parametrize("case", BUDGET_CASES, ids=lambda case: case["name"])
def test_base_reservation_uses_exact_integer_rounding(case):
    assert cache_settings.reserved_tenths(case["base_bytes"]) == case["reserved_tenths"]


def usage_snapshot(*, database=GIB, posters=3 * GIB, other=GIB,
                   transcode=0, previews=0, ok=True):
    categories = {"database": database, "posters": posters, "other": other,
                  "transcode": transcode, "previews": previews}
    return {"ok": ok, "total_bytes": sum(categories.values()),
            "categories": categories, "error": "" if ok else "部分应用数据暂时无法统计"}


def test_budget_exact_fit_and_excess_report_all_base_categories(monkeypatch):
    monkeypatch.setattr(data_usage, "_scan", usage_snapshot)
    client = TestClient(app)
    exact = {**VALUES, "data_warning_gb": 76}
    assert client.put("/api/cache-settings", json=exact).json() == exact
    response = client.put("/api/cache-settings", json={**exact, "data_warning_gb": 75.9})
    assert response.status_code == 422
    assert "超出总预算 0.1 GB" in response.json()["detail"]
    assert "基础数据需预留 6.0 GB" in response.json()["detail"]
    assert "增大总预算或调低" in response.json()["detail"]
    assert client.get("/api/cache-settings").json() == exact


def test_fractional_allocations_can_exactly_fill_budget_without_float_error():
    values = {"data_warning_gb": 1.3, "transcode_cache_gb": 0.1,
              "preview_cache_gb": 0.2}
    assert TestClient(app).put("/api/cache-settings", json=values).json() == values


def test_latest_base_growth_requires_reallocation_without_changing_saved_limits(monkeypatch):
    snapshots = [usage_snapshot(), usage_snapshot(database=2 * GIB)]
    scans = []

    def scan():
        scans.append(True)
        return snapshots[len(scans) - 1]

    monkeypatch.setattr(data_usage, "_scan", scan)
    client = TestClient(app)
    values = {**VALUES, "data_warning_gb": 76}
    assert client.put("/api/cache-settings", json=values).status_code == 200
    response = client.put("/api/cache-settings", json=values)
    assert response.status_code == 422
    assert "超出总预算 1.0 GB" in response.json()["detail"]
    assert len(scans) == 2
    assert client.get("/api/cache-settings").json() == values


@pytest.mark.parametrize("transcode", [40, 0])
def test_existing_overallocated_or_unlimited_values_remain_readable(monkeypatch, transcode):
    values = {"data_warning_gb": 2, "transcode_cache_gb": transcode,
              "preview_cache_gb": 30}
    store.set_setting("cache_limits", json.dumps(values))
    monkeypatch.setenv("TRANSCODE_CACHE_GB", "8")
    monkeypatch.setenv("PREVIEW_CACHE_GB", "2")
    importlib.reload(cache_settings)
    client = TestClient(app)
    assert client.get("/api/cache-settings").json() == values
    assert common._cache_cap_bytes() == transcode * GIB
    assert previews._cache_cap_bytes() == 30 * GIB
    assert client.put("/api/cache-settings", json=values).status_code == 422
    assert client.get("/api/cache-settings").json() == values


def test_environment_unlimited_limit_is_readable_but_must_be_allocated_on_save(monkeypatch):
    monkeypatch.setenv("TRANSCODE_CACHE_GB", "0")
    client = TestClient(app)
    values = client.get("/api/cache-settings").json()
    assert values["transcode_cache_gb"] == 0
    response = client.put("/api/cache-settings", json=values)
    assert response.status_code == 422
    assert "未限制容量" in response.json()["detail"]
    assert not store.get_setting("cache_limits")


@pytest.mark.parametrize("failure", ["partial", "exception", "missing_category", "invalid_count"])
def test_failed_usage_scan_returns_503_without_saving_or_waking_queue(monkeypatch, failure):
    store.set_setting("cache_limits", json.dumps(VALUES))
    previews._auto_wake.clear()

    def scan():
        if failure == "exception":
            raise PermissionError("private filesystem path")
        snapshot = usage_snapshot(ok=failure != "partial")
        if failure == "missing_category":
            snapshot["categories"].pop("other")
        if failure == "invalid_count":
            snapshot["categories"]["posters"] = -1
        return snapshot

    monkeypatch.setattr(data_usage, "_scan", scan)
    client = TestClient(app)
    response = client.put("/api/cache-settings", json={**VALUES, "transcode_cache_gb": 25})
    assert response.status_code == 503
    assert "未保存" in response.json()["detail"]
    assert "private filesystem path" not in response.text
    assert client.get("/api/cache-settings").json() == VALUES
    assert not previews._auto_wake.is_set()


def test_legal_budget_can_save_while_actual_caches_exceed_limits_without_cleanup(monkeypatch):
    monkeypatch.setattr(data_usage, "_scan", lambda: usage_snapshot(
        transcode=100 * GIB, previews=100 * GIB))
    monkeypatch.setattr(common, "_purge_old", lambda: pytest.fail("save must not clean caches"))
    monkeypatch.setattr(common, "_evict_transcode_cache", lambda **_kw: pytest.fail("save must not evict"))
    client = TestClient(app)
    values = {**VALUES, "data_warning_gb": 76}
    assert client.put("/api/cache-settings", json=values).json() == values
    usage = client.get("/api/data-usage").json()
    assert usage["over_limit"] and usage["total_bytes"] == 205 * GIB


def test_failed_atomic_settings_write_preserves_previous_row_and_does_not_wake():
    import app.store._base as base

    store.set_setting("cache_limits", json.dumps(VALUES))
    previews._auto_wake.clear()
    with sqlite3.connect(base.DB_PATH) as connection:
        connection.execute("""CREATE TRIGGER reject_cache_budget BEFORE UPDATE ON app_settings
            WHEN NEW.key = 'cache_limits' BEGIN SELECT RAISE(ABORT, 'test write failure'); END""")
    client = TestClient(app, raise_server_exceptions=False)
    response = client.put("/api/cache-settings", json={**VALUES, "transcode_cache_gb": 25})
    assert response.status_code == 500
    assert client.get("/api/cache-settings").json() == VALUES
    assert not previews._auto_wake.is_set()
