"""P1-10 日志基建回归网：setup_logging 行为 + 关键失败路径必须留痕。"""
import logging

import pytest

from app import scanner, store
from app.config import settings
from app.log import get_logger, setup_logging
from app.routers import files as files_router


def _row(rel, title="T", year=2020) -> int:
    mid = store.upsert_movie_by_path(rel)
    store.update_movie_meta(mid, title=title, year=year)
    return mid


def _touch(media_root, rel):
    p = media_root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")
    return p


def test_setup_logging_level_and_idempotent(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    setup_logging(force=True)
    assert logging.getLogger().level == logging.DEBUG
    assert get_logger("x").getEffectiveLevel() == logging.DEBUG
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    setup_logging(force=True)
    assert logging.getLogger().level == logging.INFO
    setup_logging()  # 幂等，不抛错
    assert logging.getLogger().name == "root"


def test_move_failure_logs_and_rolls_back(monkeypatch, media_root, caplog):
    src = _touch(media_root, "log/a.mkv")
    mid = _row("log/a.mkv")

    def _boom(*a, **kw):
        raise RuntimeError("db down")

    monkeypatch.setattr(store, "update_movie_local", _boom)
    with caplog.at_level(logging.WARNING, logger="jzmedia.files"):
        r = files_router._move_one({"id": mid, "from": "log/a.mkv", "to": "log/b.mkv"})
    assert r["status"].startswith("error")
    assert src.is_file() and not (media_root / "log/b.mkv").exists()  # 已回滚
    assert any("move failed" in rec.message for rec in caplog.records)


def test_sync_nfos_failure_logs(monkeypatch, media_root, caplog):
    _touch(media_root, "syncnfo-only/c.mkv")
    mid = _row("syncnfo-only/c.mkv")

    def _boom(*a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(scanner.nfo_link, "write_movie_nfo", _boom)
    with caplog.at_level(logging.WARNING, logger="jzmedia.scanner"):
        r = scanner.sync_nfos_for(mid, str(media_root / "syncnfo-only/c.mkv"))
    assert r["ok"] is False
    assert any("sync_nfos_for failed" in rec.message for rec in caplog.records)


def test_sync_nfos_multi_version_write_failure_logged(monkeypatch, media_root, caplog):
    _touch(media_root, "syncnfo-multi/x (2020).mkv")
    _touch(media_root, "syncnfo-multi/x (2020)-1080p.mkv")
    mid = _row("syncnfo-multi/x (2020).mkv")
    mid2 = _row("syncnfo-multi/x (2020)-1080p.mkv")

    def _boom(*a, **kw):
        raise OSError("nfo boom")

    monkeypatch.setattr(scanner.nfo_link, "write_movie_nfo", _boom)
    with caplog.at_level(logging.WARNING, logger="jzmedia.scanner"):
        r = scanner.sync_nfos_for(mid, str(media_root / "syncnfo-multi/x (2020).mkv"))
    assert r["ok"] is False
    assert any("nfo write failed" in rec.message for rec in caplog.records)
    assert store.get_movie(mid2) is not None


def test_scan_one_error_logged(monkeypatch, media_root, caplog):
    _touch(media_root, "log/d.mkv")

    def _boom(*a, **kw):
        raise RuntimeError("probe boom")

    monkeypatch.setattr(scanner.scan, "scan_one", _boom)
    with caplog.at_level(logging.DEBUG, logger="jzmedia.scanner"):
        out = scanner.scan_all()
    assert any(str(r.get("status", "")).startswith("error") for r in out)
    assert any("scan_one failed" in rec.message for rec in caplog.records)
