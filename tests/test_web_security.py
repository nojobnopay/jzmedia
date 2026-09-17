"""B6-REQ 安全面回归网：SPA 目录边界、安全头、未知 /api 404、health 语义、blob inline。"""
from fastapi.testclient import TestClient

from app import store
from app.main import _spa_file, app

client = TestClient(app)


def test_spa_file_rejects_sibling_prefix_escape(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("spa", encoding="utf-8")
    sibling = tmp_path / "dist-x"
    sibling.mkdir()
    secret = sibling / "secret.txt"
    secret.write_text("secret", encoding="utf-8")

    assert _spa_file(str(dist), "index.html") == str(dist / "index.html")
    assert _spa_file(str(dist), "../dist-x/secret.txt") is None      # 前缀越权已堵
    assert _spa_file(str(dist), "../../etc/passwd") is None
    assert _spa_file(str(dist), "nope.png") is None


def test_unknown_api_returns_json_404():
    r = client.get("/api/definitely-not-a-route")
    assert r.status_code == 404
    assert r.headers.get("content-type", "").startswith("application/json")
    assert r.json().get("detail")


def test_security_headers_present():
    for path in ("/api/health", "/api/movies"):
        h = client.get(path).headers
        assert h.get("x-content-type-options") == "nosniff"
        assert h.get("referrer-policy") == "same-origin"
        assert h.get("x-frame-options") == "SAMEORIGIN"
        csp = h.get("content-security-policy") or ""
        assert "default-src 'self'" in csp and "wasm-unsafe-eval" in csp


def test_health_reports_db_and_media():
    d = client.get("/api/health").json()
    assert d["status"] == "ok"
    assert d["db"]["ok"] is True and d["db"]["readable"] is True
    assert d["media"]["ok"] is True
    assert "phase" not in d          # 陈旧字段已删（R01-B4）


def test_blob_inline_whitelist(media_root):
    rel = "websec/preview.pdf"
    p = media_root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"%PDF-1.4 fake")
    mid = store.upsert_movie_by_path(rel)
    store.update_movie_meta(mid, title="PDF", year=2024)

    r_inline = client.get(f"/api/movies/{mid}/blob", params={"name": rel, "inline": 1})
    assert r_inline.status_code == 200
    assert "attachment" not in (r_inline.headers.get("content-disposition") or "")
    assert r_inline.headers.get("content-type", "").startswith("application/pdf")

    r_default = client.get(f"/api/movies/{mid}/blob", params={"name": rel})
    assert "attachment" in (r_default.headers.get("content-disposition") or "")

    # 非白名单扩展名即使 inline=1 也保持 attachment（防内联 HTML）
    rel2 = "websec/evil.html"
    p2 = media_root / rel2
    p2.write_bytes(b"<script>1</script>")
    mid2 = store.upsert_movie_by_path(rel2)
    store.update_movie_meta(mid2, title="HTML", year=2024)
    r2 = client.get(f"/api/movies/{mid2}/blob", params={"name": rel2, "inline": 1})
    assert "attachment" in (r2.headers.get("content-disposition") or "")


def test_health_check_store_helper():
    d = store.health_check()
    assert d["ok"] is True and d["bytes"] > 0


def test_settings_proxy_masked_and_echo_safe():
    """评审 R01-B6：代理 URL 含凭证只回脱敏值；回显值不回写；空串仍可清空。"""
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app)
    c.put("/api/settings", json={"tmdb_proxy": "http://user:pass@proxy.local:7890"})
    d = c.get("/api/settings").json()
    assert d["tmdb_proxy"] == "http://***@proxy.local:7890"
    assert "pass" not in d["tmdb_proxy"]
    # 前端把脱敏回显原样提交 → 视为不修改（不得把 *** 落库）
    c.put("/api/settings", json={"tmdb_proxy": d["tmdb_proxy"]})
    assert c.get("/api/settings").json()["tmdb_proxy"] == "http://***@proxy.local:7890"
    # 空串=清空（恢复跟随 env）
    c.put("/api/settings", json={"tmdb_proxy": ""})
    assert c.get("/api/settings").json()["tmdb_proxy"] == ""


def test_settings_proxy_without_creds_unchanged():
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app)
    c.put("/api/settings", json={"tmdb_proxy": "http://proxy.local:7890"})
    assert c.get("/api/settings").json()["tmdb_proxy"] == "http://proxy.local:7890"
    c.put("/api/settings", json={"tmdb_proxy": ""})
