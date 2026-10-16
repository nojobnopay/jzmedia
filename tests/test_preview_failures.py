"""Persistent preview failures and manual retries use isolated media and storage."""
import json
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import library_paths, store
from app.jobkit import JobRegistry
from app.main import app
from app.routers.stream import previews as pv
from app.routers.stream.preview_failures import FailureStore, failure_reason
from app.routers.stream.preview_log import ExtractionError


@pytest.fixture
def previews(tmp_path, monkeypatch):
    import app.store._base as base
    pv.shutdown_previews()
    monkeypatch.setattr(base, "DB_PATH", str(tmp_path / "failures.db"))
    store.init_db()
    library_paths.invalidate_cache()
    for name, value in {"_jobs": JobRegistry(max_finished=1), "_active_job": None,
                        "_worker_thread": None, "_auto_thread": None, "_auto_queue": [],
                        "_auto_job_id": None, "_queued_finished": None,
                        "_queue_retry_at": 0.0, "_failure_store": None,
                        "_ready_queue_check": (None, 0.0)}.items():
        monkeypatch.setattr(pv, name, value)
    cache = tmp_path / "previews"
    monkeypatch.setattr(pv, "_root", lambda: cache)
    monkeypatch.setenv("PREVIEW_AUTO_MODE", "off")
    monkeypatch.setattr(pv, "_transcoding_busy", lambda: False)
    monkeypatch.setattr(pv, "_playback_busy", lambda: False)
    monkeypatch.setattr(pv, "_auto_allowed_now", lambda: True)
    pv._stop.clear()
    pv._auto_wake.clear()
    libraries = []
    for number in (1, 2):
        root = tmp_path / f"media-{number}"
        root.mkdir()
        libraries.append(store.create_library(f"Failures {number}", path=str(root)))

    def item(kind="movie", library=None, name=None):
        lib = library or libraries[0]
        name = name or f"{kind}-{len(store.preview_items(lib['id']))}.mkv"
        if kind == "movie":
            vid = store.upsert_movie_by_path(name, library_id=lib["id"])
            store.update_movie_meta(vid, title=f"电影 {vid}")
        elif kind == "episode":
            show = store.upsert_show(lib["id"], "隔离剧集")
            vid = store.upsert_episode(show, lib["id"], name, 1, 3, title="第三集")
        else:
            parent = store.upsert_movie_by_path("parent.mkv", library_id=lib["id"])
            vid = store.upsert_extra(name, parent, kind="behindthescenes", library_id=lib["id"])
        return kind, vid

    def failure(ref, stamp=100):
        kind, vid = ref
        record = dict(kind=kind, id=vid, library_id=store.get_playable(kind, vid)["library_id"],
                      failed_at=stamp, error_code="decoder", error="未使用的磁盘文本")
        ledger = pv._failure_ledger()
        ledger.update(kind, vid, record)
        assert ledger.flush()
        return record

    def queue(ref, manual=False):
        record = dict(kind=ref[0], id=ref[1], library_id=store.get_playable(*ref)["library_id"])
        if manual:
            record["manual_retry"] = True
        pv._auto_queue.append(record)
        pv._save_auto_queue()
        return record

    def job(refs, *, queued=None, claimed=False):
        lid = store.get_playable(*refs[0])["library_id"]
        created = pv._jobs.create(items=refs, total=len(refs), library_id=lid,
                                  auto=bool(queued and not queued.get("manual_retry")),
                                  source="retry" if queued else "batch", queue_item=queued)
        jid = created["job_id"]
        pv._active_job = jid
        if queued:
            pv._auto_job_id = jid
        if claimed:
            pv._jobs.update(jid, current_kind=refs[0][0], current_id=refs[0][1])
        return jid

    def source(vid, kind):
        row = store.get_playable(kind, vid)
        return row, SimpleNamespace(rel=row["file_path"])

    monkeypatch.setattr(pv, "_version_source", source)
    monkeypatch.setattr(pv, "_media_cached_or_probe", lambda *_a: {"playable": True})
    client = TestClient(app)
    yield SimpleNamespace(client=client, libraries=libraries, cache=cache, item=item,
                          failure=failure, queue=queue, job=job)
    pv.shutdown_previews()
    pv._stop.clear()
    client.close()
    library_paths.invalidate_cache()


def failed_path():
    return "/api/stream/previews/failed"


def retry_path(ref):
    return f"{failed_path()}/{ref[0]}/{ref[1]}/retry"


def retry(previews, ref, library=None):
    lib = library or previews.libraries[0]
    return previews.client.post(retry_path(ref), json={"library_id": lib["id"]})


def test_failure_inventory_is_independent_of_workers_and_source_access(previews, monkeypatch):
    inv = previews
    refs = [inv.item(kind) for kind in ("movie", "episode", "extra")]
    refs.append(inv.item(library=inv.libraries[1]))
    for stamp, ref in enumerate(refs, 100):
        inv.failure(ref, stamp)
    path = inv.cache / "failures.json"
    before = path.read_bytes(), path.stat().st_mtime_ns
    monkeypatch.setattr(pv, "_failure_store", None)  # A new process, with auto generation disabled.
    monkeypatch.setattr(pv, "_version_source", lambda *_a: pytest.fail("GET must not access sources"))
    monkeypatch.setattr(pv, "_start", lambda *_a, **_kw: pytest.fail("GET must not start a worker"))
    first = inv.client.get(failed_path(), params={"offset": 0, "limit": 2}).json()
    second = inv.client.get(failed_path(), params={"offset": 2, "limit": 2}).json()
    assert (first["total"], first["offset"], first["limit"]) == (4, 0, 2)
    assert [item["failed_at"] for item in first["items"] + second["items"]] == [103, 102, 101, 100]
    assert "幕后 · extra-" in first["items"][1]["title"]
    assert second["items"][0]["title"].startswith("隔离剧集 S01E03")
    assert all(set(item) == {"kind", "id", "library_id", "title", "file_name", "failed_at", "error_code", "error"}
               for item in first["items"] + second["items"])
    scoped = inv.client.get(failed_path(), params={"library_id": inv.libraries[1]["id"]}).json()
    assert scoped["total"] == 1 and scoped["items"][0]["id"] == refs[-1][1]
    assert before == (path.read_bytes(), path.stat().st_mtime_ns)
    assert pv._auto_thread is None


@pytest.mark.parametrize("query", ["offset=-1", "limit=0", "limit=101", "library_id=0"])
def test_failed_inventory_validates_pagination(previews, query):
    assert previews.client.get(f"{failed_path()}?{query}").status_code == 422


@pytest.mark.parametrize("contents", ["{broken", "{}", json.dumps([
    dict(kind="movie", id=1, library_id=1, failed_at=100, error_code=[]),
])])
def test_failed_inventory_missing_and_corrupt_data_are_distinct(previews, contents):
    assert previews.client.get(failed_path()).json()["total"] == 0
    assert not previews.cache.exists()
    previews.cache.mkdir()
    path = previews.cache / "failures.json"
    path.write_text(contents)
    response = previews.client.get(failed_path())
    assert response.status_code == 503 and "失败记录" in response.json()["detail"]
    assert path.read_text() == contents
    assert previews.client.get(f"{failed_path()}?library_id=999999").status_code == 404


def test_inventory_hides_removed_media_and_follows_current_library(previews):
    ref = previews.item()
    previews.failure(ref)
    with store._base._lock, store._base._conn() as c:
        c.execute("UPDATE movies SET library_id=? WHERE id=?", (previews.libraries[1]["id"], ref[1]))
    assert previews.client.get(failed_path(), params={"library_id": previews.libraries[0]["id"]}).json()["total"] == 0
    assert previews.client.get(failed_path(), params={"library_id": previews.libraries[1]["id"]}).json()["total"] == 1
    store.delete_movie(ref[1])
    assert previews.client.get(failed_path()).json()["total"] == 0


@pytest.mark.parametrize("error,code", [
    (ExtractionError("unsafe", reason="exit", error_kind="filter"), "filter"),
    (ExtractionError("unsafe", reason="timeout", timed_out=True), "timeout"),
    (HTTPException(410, "https://user:secret@example.invalid?token=private"), "source_missing"),
    (HTTPException(503, "https://user:secret@example.invalid?token=private"), "source_offline"),
    (PermissionError("/private/file"), "permission"),
    (RuntimeError("该 Dolby Vision 片源无可用兼容基底，暂不支持预览"), "dolby_vision"),
    (RuntimeError("无法取得有效时长（支持 24 小时以内的视频）"), "duration"),
    (RuntimeError("https://user:secret@example.invalid?token=private"), "unknown"),
])
def test_published_failure_reasons_are_safe(error, code):
    actual, message = failure_reason(error)
    assert actual == code
    assert not any(value in message for value in ("private", "secret", "example.invalid", "unsafe"))


def test_worker_records_latest_failure_and_success_resolves_it(previews, monkeypatch):
    ref = previews.item()
    def broken(*_a):
        raise ExtractionError("https://user:secret@example.invalid", reason="exit", error_kind="filter")
    monkeypatch.setattr(pv, "_generate", broken)
    for _ in range(2):
        jid = previews.job([ref])
        pv._worker(jid, [ref], True)
    response = previews.client.get(failed_path()).json()
    assert response["total"] == 1 and response["items"][0]["error_code"] == "filter"
    assert "secret" not in (previews.cache / "failures.json").read_text()
    assert len(pv._jobs.snapshot()) == 1  # The persistent record survives job registry eviction.
    monkeypatch.setattr(pv, "_generate", lambda *_a: None)
    jid = previews.job([ref])
    pv._worker(jid, [ref], True)
    assert previews.client.get(failed_path()).json()["total"] == 0
    assert json.loads((previews.cache / "failures.json").read_text()) == []


def test_persistent_failures_are_not_limited_to_a_jobs_last_hundred_items(previews, monkeypatch):
    refs = [previews.item(name=f"failed-{number}.mkv") for number in range(105)]
    def unplayable(*_a):
        raise RuntimeError("片源不可播放")
    monkeypatch.setattr(pv, "_generate", unplayable)
    jid = previews.job(refs)
    pv._worker(jid, refs, True)
    job = pv._jobs.get(jid)
    assert len(job["failed"]) == 100 and job["failure_count"] == 105
    first = previews.client.get(failed_path(), params={"limit": 100}).json()
    second = previews.client.get(failed_path(), params={"limit": 100, "offset": 100}).json()
    assert first["total"] == second["total"] == 105
    assert len(first["items"]) == 100 and len(second["items"]) == 5
    assert len({item["id"] for item in first["items"] + second["items"]}) == 105


def test_cancelled_generation_does_not_replace_previous_failure(previews, monkeypatch):
    ref = previews.item()
    previews.failure(ref)
    before = (previews.cache / "failures.json").read_bytes()
    def cancelled(*_a):
        raise pv._Cancelled()
    monkeypatch.setattr(pv, "_generate", cancelled)
    jid = previews.job([ref])
    pv._worker(jid, [ref], True)
    assert pv._jobs.get(jid)["state"] == "cancelled"
    assert before == (previews.cache / "failures.json").read_bytes()


def test_retry_appends_and_promotes_idle_item_at_the_full_visible_tail(previews):
    first, failed, last, batch = [previews.item() for _ in range(4)]
    previews.failure(failed)
    for ref in (first, failed, last):
        previews.queue(ref)
    previews.job([batch])
    response = retry(previews, failed)
    assert response.json() == {"state": "queued"}
    pending = previews.client.get("/api/stream/previews/queue").json()["items"]
    assert [item["id"] for item in pending] == [batch[1], first[1], last[1], failed[1]]
    assert [item["source"] for item in pending] == ["batch", "auto", "auto", "retry"]
    assert pv._auto_queue[-1]["manual_retry"] is True
    assert retry(previews, failed).json() == {"state": "already_queued"}
    assert previews.client.get(failed_path()).json()["total"] == 1


@pytest.mark.parametrize("claimed,state", [(False, "already_queued"), (True, "running")])
def test_retry_deduplicates_current_immediate_batch(previews, claimed, state):
    ref = previews.item()
    previews.failure(ref)
    previews.job([ref], claimed=claimed)
    assert retry(previews, ref).json() == {"state": state}
    assert pv._auto_queue == []


def test_retry_validates_scope_and_stale_failures_and_rolls_back_failed_save(previews, monkeypatch):
    ref = previews.item()
    assert retry(previews, ref, previews.libraries[1]).status_code == 404
    assert retry(previews, ref).status_code == 409
    previews.failure(ref)
    before = previews.queue(ref)
    monkeypatch.setattr(pv, "_save_auto_queue", lambda: (_ for _ in ()).throw(OSError("disk full")))
    assert retry(previews, ref).status_code == 503
    assert pv._auto_queue == [before] and not pv._auto_queue[0].get("manual_retry")


@pytest.mark.parametrize("pause", ["disabled", "scheduled", "playback", "data_limit"])
def test_dispatch_skips_ineligible_idle_items_without_reordering_the_queue(previews, monkeypatch, pause):
    first, last, ref = [previews.item() for _ in range(3)]
    previews.queue(first)
    previews.queue(last)
    previews.failure(ref)
    assert retry(previews, ref).json() == {"state": "queued"}
    monkeypatch.setattr(pv, "_auto_pause_reason", lambda: pause)
    processed = []
    monkeypatch.setattr(pv, "_generate", lambda _jid, row, *_a: processed.append(row["id"]))
    pv._dispatch_queue_once()
    pv._worker_thread.join(timeout=2)
    pv._dispatch_queue_once()
    assert processed == [ref[1]]
    assert [item["id"] for item in pv._auto_queue] == [first[1], last[1]]
    job = pv._jobs.get(pv._active_job)
    assert not job["auto"] and job["source"] == "retry"
    assert job["library_id"] == previews.libraries[0]["id"]
    assert previews.client.get(failed_path()).json()["total"] == 0


def test_dispatch_keeps_earlier_eligible_auto_item_before_retry(previews, monkeypatch):
    automatic, manual = [previews.item() for _ in range(2)]
    previews.queue(automatic)
    previews.queue(manual, manual=True)
    monkeypatch.setenv("PREVIEW_AUTO_MODE", "all")
    monkeypatch.setattr(pv, "_auto_pause_reason", lambda: "")
    processed = []
    monkeypatch.setattr(pv, "_generate", lambda _jid, row, *_a: processed.append(row["id"]))
    pv._dispatch_queue_once()
    pv._worker_thread.join(timeout=2)
    assert processed == [automatic[1]]
    assert pv._jobs.get(pv._active_job)["auto"] is True


def test_queued_retry_never_preempts_a_live_worker(previews, monkeypatch):
    ref = previews.item()
    previews.queue(ref, manual=True)
    monkeypatch.setattr(pv, "_worker_thread", SimpleNamespace(is_alive=lambda: True, join=lambda **_kw: None))
    monkeypatch.setattr(pv, "_start", lambda *_a, **_kw: pytest.fail("must wait for existing worker"))
    pv._dispatch_queue_once()
    assert len(pv._auto_queue) == 1


def test_manual_retry_waits_for_playback_transcoding_and_keeps_the_old_reason(previews, monkeypatch):
    ref = previews.item()
    previews.failure(ref)
    previews.queue(ref, manual=True)
    checked, available, generated = threading.Event(), threading.Event(), threading.Event()
    def busy():
        checked.set()
        return not available.is_set()
    monkeypatch.setattr(pv, "_transcoding_busy", busy)
    monkeypatch.setattr(pv, "_generate", lambda *_a: generated.set())
    pv._dispatch_queue_once()
    assert checked.wait(2) and not generated.is_set()
    assert previews.client.get(failed_path()).json()["total"] == 1
    available.set()
    pv._worker_thread.join(timeout=2)
    assert generated.is_set()
    assert previews.client.get(failed_path()).json()["total"] == 0


@pytest.mark.parametrize("route", ["queue", "job"])
def test_cancel_running_retry_at_non_head_preserves_failure_and_earlier_items(previews, route):
    earlier, ref = [previews.item() for _ in range(2)]
    previews.queue(earlier)
    item = previews.queue(ref, manual=True)
    previews.failure(ref)
    jid = previews.job([ref], queued=item, claimed=True)
    if route == "queue":
        response = previews.client.delete(f"/api/stream/previews/queue/{ref[0]}/{ref[1]}",
                                          params={"library_id": item["library_id"]})
        assert response.json() == {"removed": True, "cancelled": True}
    else:
        response = previews.client.post(f"/api/stream/previews/jobs/{jid}/cancel")
        assert response.json()["state"] == "cancelled"
    assert [item["id"] for item in pv._auto_queue] == [earlier[1]]
    assert pv._jobs.get(jid)["state"] == "cancelled"
    pv._dispatch_queue_once()
    assert previews.client.get(failed_path()).json()["total"] == 1


def test_late_cancel_of_old_job_does_not_remove_new_retry(previews):
    ref = previews.item()
    item = previews.queue(ref, manual=True)
    jid = previews.job([ref], queued=item)
    pv._jobs.update(jid, state="failed")
    pv._auto_job_id = None
    response = previews.client.post(f"/api/stream/previews/jobs/{jid}/cancel")
    assert response.json()["state"] == "failed"
    assert pv._auto_queue == [item]


def test_failed_metadata_save_retries_only_persistence_after_thirty_seconds(previews, monkeypatch):
    ref = previews.item()
    previews.queue(ref, manual=True)
    generated, attempts = [], []
    def broken(_jid, row, *_a):
        generated.append(row["id"])
        raise RuntimeError("片源不可播放")
    monkeypatch.setattr(pv, "_generate", broken)
    write_text = Path.write_text
    def full(path, *args, **kwargs):
        if path.name == "failures.tmp":
            attempts.append(path.name)
            raise OSError("disk full")
        return write_text(path, *args, **kwargs)
    monkeypatch.setattr(Path, "write_text", full)
    pv._dispatch_queue_once()
    pv._worker_thread.join(timeout=2)
    for _ in range(5):
        pv._auto_wake.set()
        pv._dispatch_queue_once()
    assert generated == [ref[1]] and attempts == ["failures.tmp"]
    assert len(pv._auto_queue) == 1
    assert previews.client.get(failed_path()).json()["items"][0]["error_code"] == "unplayable"
    # Job eviction must not lose the terminal result and restart FFmpeg.
    replacement = pv._jobs.create()
    pv._jobs.update(replacement["job_id"], state="done")
    assert pv._jobs.get(pv._auto_job_id) is None
    monkeypatch.setattr(Path, "write_text", write_text)
    pv._failure_ledger().retry_at = 0
    pv._dispatch_queue_once()
    assert generated == [ref[1]] and pv._auto_queue == []
    assert json.loads((previews.cache / "failures.json").read_text())[0]["id"] == ref[1]


def test_successful_generation_waits_for_failure_clear_without_becoming_a_failure(previews, monkeypatch):
    ref = previews.item()
    previews.failure(ref)
    previews.queue(ref, manual=True)
    generated = []
    monkeypatch.setattr(pv, "_generate", lambda _jid, row, *_a: generated.append(row["id"]))
    write_text = Path.write_text
    def full(path, *args, **kwargs):
        if path.name == "failures.tmp":
            raise OSError("disk full")
        return write_text(path, *args, **kwargs)
    monkeypatch.setattr(Path, "write_text", full)
    pv._dispatch_queue_once()
    pv._worker_thread.join(timeout=2)
    pv._dispatch_queue_once()
    assert generated == [ref[1]] and len(pv._auto_queue) == 1
    assert pv._jobs.get(pv._active_job)["state"] == "done"
    monkeypatch.setattr(Path, "write_text", write_text)
    pv._failure_ledger().retry_at = 0
    pv._dispatch_queue_once()
    assert generated == [ref[1]] and pv._auto_queue == []
    assert json.loads((previews.cache / "failures.json").read_text()) == []


def test_restart_restores_manual_retry_even_when_auto_is_disabled(previews, monkeypatch):
    ref = previews.item()
    previews.failure(ref)
    previews.queue(ref, manual=True)
    pv._auto_queue.clear()
    processed = []
    complete = threading.Event()
    def generate(_jid, row, *_a):
        processed.append(row["id"])
        complete.set()
    monkeypatch.setattr(pv, "_generate", generate)
    pv.start_auto_previews()
    assert complete.wait(2)
    pv._worker_thread.join(timeout=2)
    assert processed == [ref[1]]
    assert previews.client.get(failed_path()).json()["total"] == 0


def test_failure_store_refuses_to_overwrite_corruption(previews):
    ledger = FailureStore(previews.cache)
    previews.cache.mkdir()
    ledger.path.write_text("[corrupt")
    ledger.update("movie", 1, dict(kind="movie", id=1, library_id=1, failed_at=100,
                                    error_code="unknown", error="unsafe"))
    with pytest.raises(ValueError):
        ledger.flush()
    assert ledger.path.read_text() == "[corrupt" and ledger.pending
    assert ledger.flush() is False


@pytest.mark.parametrize("status,code", [(410, "source_missing"), (503, "source_offline")])
def test_single_item_preflight_failures_are_saved_with_safe_messages(previews, monkeypatch, status, code):
    ref = previews.item()
    def unavailable(*_a):
        raise HTTPException(status, "https://user:secret@example.invalid?token=private")
    monkeypatch.setattr(pv, "_version_source", unavailable)
    response = previews.client.post(f"/api/stream/{ref[1]}/previews", params={"kind": ref[0]})
    assert response.status_code == status
    assert "secret" not in response.text
    failed = previews.client.get(failed_path()).json()
    assert failed["total"] == 1 and failed["items"][0]["error_code"] == code
    assert pv._worker_thread is None


def test_unknown_single_item_does_not_create_a_failure_record(previews, monkeypatch):
    def missing(*_a):
        raise HTTPException(404, "version not found")
    monkeypatch.setattr(pv, "_version_source", missing)
    assert previews.client.post("/api/stream/999999/previews").status_code == 404
    assert not previews.cache.exists()


def test_retry_respects_configured_write_authentication(previews):
    ref = previews.item()
    previews.failure(ref)
    store.set_setting("jzmedia_token", "isolated-preview-token")
    assert retry(previews, ref).status_code == 401
    assert previews.client.get(failed_path()).status_code == 200
    response = previews.client.post(retry_path(ref), json={"library_id": previews.libraries[0]["id"]},
                                    headers={"X-Api-Token": "isolated-preview-token"})
    assert response.json() == {"state": "queued"}


def test_cancel_then_requeue_is_not_removed_by_late_old_worker_failure(previews, monkeypatch):
    ref = previews.item()
    previews.failure(ref)
    previews.queue(ref, manual=True)
    started, release = threading.Event(), threading.Event()
    def delayed(*_a):
        started.set()
        assert release.wait(3)
        raise RuntimeError("片源不可播放")
    monkeypatch.setattr(pv, "_generate", delayed)
    pv._dispatch_queue_once()
    assert started.wait(2)
    old_job = pv._active_job
    assert previews.client.post(f"/api/stream/previews/jobs/{old_job}/cancel").json()["state"] == "cancelled"
    assert retry(previews, ref).json() == {"state": "queued"}
    release.set()
    pv._worker_thread.join(timeout=2)
    assert pv._jobs.get(old_job)["state"] == "cancelled"
    assert len(pv._auto_queue) == 1 and pv._auto_queue[0]["manual_retry"]
    monkeypatch.setattr(pv, "_generate", lambda *_a: None)
    pv._dispatch_queue_once()
    pv._worker_thread.join(timeout=2)
    assert pv._active_job != old_job
    assert previews.client.get(failed_path()).json()["total"] == 0


def test_shutdown_keeps_the_running_retry_for_restart(previews, monkeypatch):
    ref = previews.item()
    previews.failure(ref)
    item = previews.queue(ref, manual=True)
    started = threading.Event()
    def interrupted(*_a):
        started.set()
        assert pv._stop.wait(3)
        raise pv._Cancelled()
    monkeypatch.setattr(pv, "_generate", interrupted)
    pv._dispatch_queue_once()
    assert started.wait(2)
    pv.shutdown_previews()
    assert pv._jobs.get(pv._active_job)["state"] == "cancelled"
    assert json.loads(pv._queue_path().read_text()) == [item]
    assert previews.client.get(failed_path()).json()["total"] == 1
