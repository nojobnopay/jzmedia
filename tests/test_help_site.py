"""帮助站挂载、同源 CSP 与独立静态资源的回归测试。"""

import base64
import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from app.main import _CSP, app, help_site
from scripts.build_docs import source_digest


@pytest.fixture
def help_dist(tmp_path, monkeypatch):
    directory = tmp_path / "help"
    directory.mkdir()
    monkeypatch.setattr(help_site, "directory", str(directory))
    monkeypatch.setattr(help_site, "all_directories", [str(directory)])
    monkeypatch.setattr(help_site, "config_checked", False)
    monkeypatch.delenv("JZMEDIA_CSP", raising=False)
    (directory / "index.html").write_text('<h1>使用帮助</h1>', encoding="utf-8")
    (directory / "404.html").write_text('<h1>没有这篇教程</h1>', encoding="utf-8")
    (directory / "user-guide").mkdir()
    (directory / "user-guide/subtitles.html").write_text('<h1>字幕延迟</h1>', encoding="utf-8")
    (directory / "demo.mp4").write_bytes(b"0123456789")
    return directory


def test_help_redirect_and_deep_page_refresh(help_dist):
    client = TestClient(app)
    redirect = client.get("/help", follow_redirects=False)
    assert redirect.status_code == 308 and redirect.headers["location"] == "/help/"
    for url, text in [("/help/", "使用帮助"), ("/help/user-guide/subtitles.html", "字幕延迟")]:
        response = client.get(url)
        assert response.status_code == 200 and text in response.text
        assert response.headers["cache-control"] == "no-cache"
    assert client.head("/help/user-guide/subtitles.html").status_code == 200
    assert client.get("/docs").status_code == 200


def test_help_missing_page_never_falls_back_to_spa(help_dist):
    response = TestClient(app).get("/help/no-such-page.html")
    assert response.status_code == 404 and "没有这篇教程" in response.text


def test_help_missing_build_explains_recovery(help_dist):
    (help_dist / "index.html").unlink()
    client = TestClient(app)
    response = client.get("/help/")
    assert response.status_code == 503
    assert "帮助文档尚未构建" in response.text and "npm run build" in response.text
    assert response.headers["cache-control"] == "no-store"
    assert client.get("/docs").status_code == 200


def test_help_cannot_read_source_or_follow_escape_symlink(help_dist):
    private = help_dist.parent / "private.md"
    private.write_text("private-doc-marker")
    (help_dist / "leak.md").symlink_to(private)
    client = TestClient(app)
    for url in ("/help/%2e%2e/private.md", "/help/leak.md", "/help/private/report.md"):
        response = client.get(url)
        assert response.status_code == 404
        assert "private-doc-marker" not in response.text


def test_help_video_supports_range_requests(help_dist):
    response = TestClient(app).get("/help/demo.mp4", headers={"Range": "bytes=2-5"})
    assert response.status_code == 206
    assert response.content == b"2345"
    assert response.headers["content-type"].startswith("video/mp4")


def test_only_help_html_receives_generated_script_hashes(help_dist):
    script_hash = "sha256-" + base64.b64encode(hashlib.sha256(b"window.demo=1").digest()).decode()
    manifest = help_dist / "csp-hashes.json"
    manifest.write_text(json.dumps({"scriptHashes": [script_hash]}))
    client = TestClient(app)
    assert script_hash in client.get("/help/").headers["content-security-policy"]
    assert client.get("/help/demo.mp4").headers["content-security-policy"] == _CSP
    assert client.get("/api/health").headers["content-security-policy"] == _CSP
    manifest.write_text(json.dumps({"scriptHashes": ["'unsafe-inline'"]}))
    assert client.get("/help/").headers["content-security-policy"] == _CSP


def test_docs_build_detects_changed_and_deleted_inputs_but_not_private_files(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    source = docs / "index.md"
    source.write_text("first")
    first = source_digest(tmp_path)
    source.write_text("second")
    second = source_digest(tmp_path)
    assert first != second
    (docs / "private").mkdir()
    (docs / "private/data.md").write_text("not-published")
    (docs / "node_modules").mkdir()
    (docs / "node_modules/package.json").write_text("{}")
    assert source_digest(tmp_path) == second
    source.unlink()
    assert source_digest(tmp_path) != second
