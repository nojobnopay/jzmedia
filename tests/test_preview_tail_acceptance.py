"""Tail acceptance uses saved sprites and file attributes, never media decoding."""
import json
from pathlib import Path
from types import SimpleNamespace
import threading

import pytest
from fastapi.testclient import TestClient

from app import library_paths, media, storage, store
from app.jobkit import JobRegistry
from app.main import app
from app.routers.stream import previews as pv


@pytest.fixture
def tail(tmp_path, monkeypatch):
    import app.store._base as base
    monkeypatch.setattr(base, "DB_PATH", str(tmp_path / "tail.db"))
    store.init_db()
    library_paths.invalidate_cache()
    root = tmp_path / "media"
    root.mkdir()
    lib = store.create_library("Tail fixture", path=str(root))
    source = root / "synthetic.mkv"
    source.write_bytes(b"isolated-source")
    vid = store.upsert_movie_by_path(source.name, library_id=lib["id"])
    row = store.get_playable("movie", vid)
    backend = storage.backend_for(lib["id"])
    stat = backend.stat(source.name)
    src = storage.MediaSource(backend, source.name, "", None, stat.size, stat.mtime)
    cache = tmp_path / "previews"
    monkeypatch.setattr(pv, "_root", lambda: cache)
    for name, value in {"_jobs": JobRegistry(), "_active_job": None, "_auto_job_id": None,
                        "_queued_finished": None, "_auto_queue": [], "_failure_store": None,
                        "_worker_thread": None, "_stop": threading.Event(),
                        "_ready_queue_check": (None, 0.0)}.items():
        monkeypatch.setattr(pv, name, value)
    monkeypatch.setenv("PREVIEW_INTERVAL", "10")
    def unexpected(*_a, **_k):
        pytest.fail("tail decisions must not probe, decode, start workers or resolve read URLs")
    monkeypatch.setattr(pv, "_start", unexpected)
    monkeypatch.setattr(pv, "_media_cached_or_probe", unexpected)
    monkeypatch.setattr(media, "probe", unexpected)
    monkeypatch.setattr(storage.LocalStorageBackend, "get_read_url", unexpected)
    key = pv._cache_key(row, src, 10)
    directory = cache / key
    directory.mkdir(parents=True)
    data = dict(version=pv.VERSION, quality_revision=pv.QUALITY_REVISION,
                extraction_revision=pv.EXTRACTION_REVISION, item_kind="movie", item_id=vid,
                state="partial", interval=10, duration=9449.147, count=945,
                width=pv.WIDTH, height=pv.HEIGHT, columns=pv.COLUMNS, rows=pv.ROWS,
                pages=[f"page_{i:04d}.jpg" for i in range(37)])
    for name in data["pages"]:
        (directory / name).write_bytes(b"isolated-sprite")
    pv._write_manifest(directory, data)
    record = dict(kind="movie", id=vid, library_id=lib["id"], failed_at=100, error_code="decoder")
    pv._failure_ledger().update("movie", vid, record)
    assert pv._failure_ledger().flush()
    body = dict(library_id=lib["id"], key=key, failed_at=100, available_count=925, confirmed=True)
    prefix = f"/api/stream/previews/failed/movie/{vid}"
    client = TestClient(app)
    yield SimpleNamespace(client=client, lib=lib, vid=vid, row=row, src=src, source=source,
                          directory=directory, data=data, body=body, record=record,
                          accept=prefix+"/accept-tail",
                          undo=f"/api/stream/previews/completed/movie/{vid}/tail-acceptance")
    client.close()
    library_paths.invalidate_cache()


def test_tail_confirmation_keeps_real_duration_and_only_saved_complete_pages(tail):
    inv = tail
    candidate = inv.client.get("/api/stream/previews/failed").json()["items"][0]["tail_acceptance"]
    assert candidate == {"key": inv.body["key"], "failed_at": 100, "available_count": 925,
                         "coverage_end": 9250, "duration": 9449.147, "missing_seconds": 199.147}
    sprites = {name: (inv.directory / name).read_bytes() for name in inv.data["pages"]}
    response = inv.client.post(inv.accept, json=inv.body)
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "ready"
    manifest = json.loads((inv.directory / "manifest.json").read_text())
    assert (manifest["duration"], manifest["count"], manifest["available_count"]) == (9449.147, 945, 925)
    assert manifest["coverage_end"] == 9250 and manifest["completion"] == "known_tail_missing"
    assert manifest["warning"]["code"] == "confirmed_tail_damage"
    assert {name: (inv.directory / name).read_bytes() for name in sprites} == sprites
    assert inv.client.get("/api/stream/previews/failed").json()["total"] == 0
    ready = inv.client.get("/api/stream/previews/completed").json()["items"][0]
    assert (ready["frames"], ready["duration"], ready["coverage_end"]) == (925, 9449.147, 9250)
    assert ready["key"] == inv.body["key"] and ready["confirmed_at"] > 0
    assert pv._current_ready(inv.row, inv.src, 10)[0] == inv.body["key"]
    pv._generate("unused", inv.row, inv.src, {"duration": 9449.147}, 10)
    assert inv.client.post(inv.accept, json=inv.body).status_code == 200


@pytest.mark.parametrize("change", ["timeout", "input", "frame", "stale_failure", "source", "queued", "running",
                                   "empty_page", "symlink_page", "owner", "quality", "extraction", "sparse", "too_long"])
def test_tail_candidate_rejects_unproven_or_changed_state(tail, change):
    inv = tail
    data = dict(inv.data)
    if change in ("timeout", "input", "frame", "stale_failure"):
        record = dict(inv.record)
        if change == "stale_failure":
            record["failed_at"] += 1
        else:
            record["error_code"] = change
        pv._failure_ledger().update("movie", inv.vid, record)
        pv._failure_ledger().flush()
    elif change == "source":
        inv.source.write_bytes(b"replacement-source")
    elif change == "queued":
        pv._auto_queue.append({"kind": "movie", "id": inv.vid, "library_id": inv.lib["id"]})
    elif change == "running":
        pv._jobs.create(items=[("movie", inv.vid)], done=0)
    elif change == "empty_page":
        (inv.directory / data["pages"][-1]).write_bytes(b"")
    elif change == "symlink_page":
        page = inv.directory / data["pages"][-1]
        page.unlink(); page.symlink_to(inv.source)
    elif change == "owner":
        data["item_id"] += 1
    elif change == "quality":
        data["quality_revision"] -= 1
    elif change == "extraction":
        data["extraction_revision"] -= 1
    elif change == "sparse":
        data["pages"] = data["pages"][1:]
    elif change == "too_long":
        data.update(duration=10000, count=1000)
    pv._write_manifest(inv.directory, data)
    response = inv.client.post(inv.accept, json=inv.body)
    assert response.status_code == 409, response.text
    assert json.loads((inv.directory / "manifest.json").read_text())["state"] == "partial"


def test_tail_decision_is_reversible_without_dropping_pages_or_starting_work(tail):
    inv = tail
    assert inv.client.post(inv.accept, json=inv.body).status_code == 200
    ready = inv.client.get("/api/stream/previews/completed").json()["items"][0]
    body = {"library_id": inv.lib["id"], "key": inv.body["key"], "confirmed_at": ready["confirmed_at"]}
    response = inv.client.request("DELETE", inv.undo, json=body)
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "partial"
    data = json.loads((inv.directory / "manifest.json").read_text())
    assert data["pages"] == inv.data["pages"] and data["state"] == "partial"
    assert "completion" not in data and "warning" not in data
    failed = inv.client.get("/api/stream/previews/failed").json()["items"][0]
    assert failed["error_code"] == "decoder" and failed["failed_at"] == 100
    assert inv.client.get("/api/stream/previews/completed").json()["total"] == 0


def test_bad_warning_metadata_never_exposes_a_ready_manifest(tail):
    inv = tail
    assert inv.client.post(inv.accept, json=inv.body).status_code == 200
    data = json.loads((inv.directory / "manifest.json").read_text())
    data["coverage_end"] = data["duration"]
    pv._write_manifest(inv.directory, data)
    assert pv._read_completed_manifest(inv.directory) is None
    monkey_source = lambda *_a: (inv.row, inv.src)
    from unittest.mock import patch
    with patch.object(pv, "_version_source", monkey_source):
        response = inv.client.get(f"/api/stream/{inv.vid}/previews")
    assert response.json()["state"] != "ready"


def test_accepted_prefix_does_not_delete_a_whole_legacy_cache(tail):
    inv = tail
    old = inv.directory.parent / pv._cache_key(inv.row, inv.src, 10, quality_revision=2)
    old.mkdir()
    (old / "legacy.jpg").write_bytes(b"keep-whole-legacy")
    assert inv.client.post(inv.accept, json=inv.body).status_code == 200
    pv._generate("unused", inv.row, inv.src, {"duration": 9449.147}, 10)
    assert (old / "legacy.jpg").read_bytes() == b"keep-whole-legacy"


def test_manifest_write_failure_never_removes_the_failure(tail, monkeypatch):
    def fail(*_a):
        raise OSError("isolated-write-failure")
    monkeypatch.setattr(pv, "_write_manifest", fail)
    response = tail.client.post(tail.accept, json=tail.body)
    assert response.status_code == 503
    assert json.loads((tail.directory / "manifest.json").read_text())["state"] == "partial"
    assert pv._failure_ledger().snapshot()[("movie", tail.vid)]["failed_at"] == 100


@pytest.mark.parametrize("undo", [False, True])
def test_manifest_decision_recovers_a_failed_ledger_write_after_restart(tail, monkeypatch, undo):
    inv = tail
    if undo:
        assert inv.client.post(inv.accept, json=inv.body).status_code == 200
    ledger = pv._failure_ledger()
    def fail():
        raise OSError("isolated-ledger-failure")
    monkeypatch.setattr(ledger, "flush", fail)
    response = (inv.client.request("DELETE", inv.undo,
                    json={"library_id": inv.lib["id"], "key": inv.body["key"]})
                if undo else inv.client.post(inv.accept, json=inv.body))
    assert response.status_code == 200, response.text
    monkeypatch.setattr(pv, "_failure_store", None)
    monkeypatch.setattr(pv, "_tail_reconcile_check", (None, 0.0))
    if not undo:
        # A GET only hides the stale failure; recovery performs the durable write.
        assert inv.client.get("/api/stream/previews/failed").json()["total"] == 0
    pv._dispatch_queue_once()
    records = pv._failure_ledger().snapshot()
    assert (("movie", inv.vid) in records) is undo
    saved = json.loads((inv.directory.parent / "failures.json").read_text())
    assert bool(saved) is undo
    if undo:
        assert saved[0]["failed_at"] == 100 and saved[0]["error_code"] == "decoder"


@pytest.mark.parametrize("change", ["key", "failed_at", "available_count", "confirmed", "extra", "float_count", "bool_count"])
def test_tail_confirmation_validates_the_explicit_snapshot(tail, change):
    body = dict(tail.body)
    if change == "key":
        body["key"] = "f" * 32
    elif change in ("failed_at", "available_count"):
        body[change] += 1
    elif change == "confirmed":
        body["confirmed"] = False
    elif change == "extra":
        body["duration"] = 9250
    else:
        body["available_count"] = 925.0 if change == "float_count" else True
    response = tail.client.post(tail.accept, json=body)
    assert response.status_code == (409 if change in ("key", "failed_at", "available_count") else 422)
    assert pv._failure_ledger().snapshot()[("movie", tail.vid)]["failed_at"] == 100


@pytest.mark.parametrize("change", ["available", "coverage", "warning", "decision", "key", "owner", "zero_page", "link_page", "all_fields"])
def test_malformed_tail_completion_cannot_be_read_or_reused(tail, change, monkeypatch):
    assert tail.client.post(tail.accept, json=tail.body).status_code == 200
    data = json.loads((tail.directory / "manifest.json").read_text())
    if change == "available":
        data.pop("available_count")
    elif change == "coverage":
        data["coverage_end"] = float("nan")
    elif change == "warning":
        data["warning"]["message"] = "untrusted message"
    elif change == "decision":
        data.pop("tail_decision")
    elif change == "key":
        data["tail_decision"]["key"] = "f" * 32
    elif change == "zero_page":
        (tail.directory / data["pages"][-1]).write_bytes(b"")
    elif change == "link_page":
        page = tail.directory / data["pages"][-1]
        page.unlink(); page.symlink_to(tail.source)
    elif change == "all_fields":
        for key in pv._TAIL_FIELDS:
            data.pop(key)
    else:
        data["item_id"] += 1
    pv._write_manifest(tail.directory, data)
    monkeypatch.setattr(pv, "_version_source", lambda *_a: (tail.row, tail.src))
    assert tail.client.get(f"/api/stream/{tail.vid}/previews").json()["state"] != "ready"
    assert pv._current_ready(tail.row, tail.src, 10) == (None, None)


def test_disappearing_prefix_pages_are_a_snapshot_conflict(tail, monkeypatch):
    def gone(*_a):
        raise FileNotFoundError("isolated-page-disappeared")
    monkeypatch.setattr(pv, "_read_tail_prefix", gone)
    assert tail.client.post(tail.accept, json=tail.body).status_code == 409


def test_tail_endpoints_require_write_token(tail):
    inv = tail
    store.set_setting("jzmedia_token", "isolated-tail-token")
    assert inv.client.post(inv.accept, json=inv.body).status_code == 401
    assert inv.client.request("DELETE", inv.undo, json={"library_id": inv.lib["id"], "key": inv.body["key"]}).status_code == 401
    headers = {"X-Api-Token": "isolated-tail-token"}
    assert inv.client.post(inv.accept, json=inv.body, headers=headers).status_code == 200
    assert inv.client.request("DELETE", inv.undo, json={"library_id": inv.lib["id"], "key": inv.body["key"]}, headers=headers).status_code == 200


def test_tail_endpoints_require_unlocked_management(tail):
    inv = tail
    inv.client.get("/api/viewing/session")
    assert inv.client.post("/api/viewing/password", json={"new_password": "123456"}).status_code == 200
    inv.client.post("/api/viewing/lock")
    for response in (inv.client.post(inv.accept, json=inv.body), inv.client.request("DELETE", inv.undo,
                    json={"library_id": inv.lib["id"], "key": inv.body["key"]})):
        assert response.status_code == 403 and response.json()["code"] == "management_required"


def test_background_recovery_only_discovers_ordinary_manifests_once_without_page_or_source_stats(tail, monkeypatch):
    def unexpected(*_a, **_k):
        pytest.fail("ordinary caches must not trigger page validation or source reads in recovery")
    monkeypatch.setattr(pv, "_tail_reconcile_check", (None, 0.0))
    monkeypatch.setattr(pv, "_read_manifest", unexpected)
    monkeypatch.setattr(pv, "_tail_source", unexpected)
    pv._dispatch_queue_once()
    # Once discovered, an empty decision set must not rescan even metadata.
    monkeypatch.setattr(Path, "read_text", unexpected)
    pv._dispatch_queue_once()


def test_accepted_tail_is_skipped_by_batch_and_source_replacement_invalidates_it(tail, monkeypatch):
    inv = tail
    assert inv.client.post(inv.accept, json=inv.body).status_code == 200
    monkeypatch.setattr(pv, "_version_source", lambda *_a: (inv.row, inv.src))
    response = inv.client.post("/api/stream/previews/jobs", json={"library_id": inv.lib["id"]})
    assert response.status_code == 200 and response.json()["ready"] == 1
    assert response.json()["added"] == 0 and pv._auto_queue == []
    inv.source.write_bytes(b"isolated-replacement-source")
    assert inv.client.get("/api/stream/previews/completed").json()["total"] == 0
    assert inv.client.post(inv.accept, json=inv.body).status_code == 409


def test_undo_rejects_a_stale_confirmation_or_any_queued_work(tail):
    inv = tail
    assert inv.client.post(inv.accept, json=inv.body).status_code == 200
    ready = inv.client.get("/api/stream/previews/completed").json()["items"][0]
    body = dict(library_id=inv.lib["id"], key=inv.body["key"], confirmed_at=ready["confirmed_at"]+1)
    assert inv.client.request("DELETE", inv.undo, json=body).status_code == 409
    body["confirmed_at"] -= 1
    pv._auto_queue.append({"kind": "movie", "id": inv.vid, "library_id": inv.lib["id"]})
    assert inv.client.request("DELETE", inv.undo, json=body).status_code == 409


def test_cancelled_writer_must_exit_before_acceptance_can_commit(tail, monkeypatch):
    inv = tail
    reached, resume = threading.Event(), threading.Event()
    outcome = []
    def timeline(*_a, **_k):
        reached.set()
        assert resume.wait(5)
        return {"duration": inv.data["duration"], "video_stream_index": 0, "source": "isolated"}
    monkeypatch.setattr(pv, "resolve_timeline", timeline)
    monkeypatch.setattr(media, "ffmpeg_bin", lambda: "/isolated/ffmpeg")
    jid = pv._jobs.create(items=[("movie", inv.vid)], done=0)["job_id"]
    def generate():
        try:
            pv._generate(jid, inv.row, inv.src, {"duration": inv.data["duration"]}, 10)
        except pv._Cancelled:
            outcome.append("cancelled")
    worker = threading.Thread(target=generate)
    monkeypatch.setattr(pv, "_worker_thread", worker)
    monkeypatch.setattr(pv, "_active_job", jid)
    worker.start()
    try:
        assert reached.wait(5)
        pv._jobs.cancel(jid)
        assert inv.client.post(inv.accept, json=inv.body).status_code == 409
        assert json.loads((inv.directory / "manifest.json").read_text())["state"] == "partial"
    finally:
        resume.set()
        worker.join(5)
    assert not worker.is_alive() and outcome == ["cancelled"]
    assert inv.client.post(inv.accept, json=inv.body).status_code == 200
    assert json.loads((inv.directory / "manifest.json").read_text())["state"] == "ready"


def test_successful_retry_using_whole_older_extraction_retires_revocation_durably(tail, monkeypatch):
    inv = tail
    assert inv.client.post(inv.accept, json=inv.body).status_code == 200
    assert inv.client.request("DELETE", inv.undo,
        json={"library_id": inv.lib["id"], "key": inv.body["key"]}).status_code == 200
    key = pv._cache_key(inv.row, inv.src, 10, extraction_revision=1)
    older = inv.directory.parent / key
    older.mkdir()
    data = dict(inv.data, state="ready", extraction_revision=1,
                pages=[f"page_{i:04d}.jpg" for i in range(38)])
    for name in data["pages"]:
        (older / name).write_bytes(b"whole-older-sprite")
    pv._write_manifest(older, data)
    monkeypatch.setattr(pv, "_version_source", lambda *_a: (inv.row, inv.src))
    monkeypatch.setattr(pv, "_media_cached_or_probe", lambda *_a: {"playable": True, "duration": 9449.147})
    items = [("movie", inv.vid)]
    jid = pv._jobs.create(items=items, done=0)["job_id"]
    pv._worker(jid, items, False)
    assert pv._jobs.get(jid)["state"] == "done"
    assert pv._failure_ledger().snapshot() == {}
    assert "tail_decision" not in json.loads((inv.directory / "manifest.json").read_text())
    monkeypatch.setattr(pv, "_tail_reconcile_check", (None, 0.0))
    monkeypatch.setattr(pv, "_failure_store", None)
    pv._dispatch_queue_once()
    assert pv._failure_ledger().snapshot() == {}
    assert pv._current_ready(inv.row, inv.src, 10)[0] == key
    assert (older / data["pages"][-1]).read_bytes() == b"whole-older-sprite"
