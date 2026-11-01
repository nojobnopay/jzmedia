"""Preview inventories must not commit a catalogue invalidated by file changes."""
import json
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import library_paths, storage, store
from app.jobkit import JobRegistry
from app.library_mutex import library_mutation_lock
from app.routers.fs import routes as fs
from app.routers.stream import previews as pv


@pytest.fixture
def catalogue(tmp_path, monkeypatch):
    import app.store._base as base

    pv.shutdown_previews()
    monkeypatch.setattr(base, "DB_PATH", str(tmp_path / "catalogue.db"))
    store.init_db()
    library_paths.invalidate_cache()
    root = tmp_path / "media"
    root.mkdir()
    library = store.create_library("Preview consistency fixture", path=str(root))
    for name, value in {
        "_jobs": JobRegistry(), "_active_job": None, "_worker_thread": None,
        "_auto_thread": None, "_auto_queue": [], "_auto_job_id": None,
        "_queued_finished": None, "_queue_retry_at": 0.0, "_failure_store": None,
        "_ready_queue_check": (None, 0.0), "_reconcile_thread": None,
        "_submission_store": None,
    }.items():
        monkeypatch.setattr(pv, name, value)
    monkeypatch.setattr(pv, "_root", lambda: tmp_path / "previews")
    monkeypatch.setenv("PREVIEW_INTERVAL", "10")
    monkeypatch.setenv("PREVIEW_AUTO_MODE", "all")
    pv._stop.clear()
    pv._auto_wake.clear()

    def add(rel, *, selected_library=library):
        path = library_paths.resolve(selected_library["id"], rel)
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"isolated source fixture")
        vid = store.upsert_movie_by_path(rel, library_id=selected_library["id"])
        return store.get_playable("movie", vid)

    first = add("before/first.mkv")
    second = add("before/second.mkv")

    def make_ready(row=first):
        backend = storage.backend_for(row["library_id"])
        info = backend.stat(row["file_path"])
        src = storage.MediaSource(backend, row["file_path"], "", None, info.size, info.mtime)
        directory = pv._root() / pv._cache_key(row, src, 10)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "page_0000.jpg").write_bytes(b"isolated complete sprite")
        pv._write_manifest(directory, dict(
            version=pv.VERSION, quality_revision=pv.QUALITY_REVISION,
            extraction_revision=pv.EXTRACTION_REVISION, item_kind=row["kind"], item_id=row["id"],
            state="ready", interval=10, duration=30, count=3, width=pv.WIDTH,
            height=pv.HEIGHT, columns=pv.COLUMNS, rows=pv.ROWS, pages=["page_0000.jpg"]))
        return directory

    yield SimpleNamespace(root=root, library=library, first=first, second=second,
                          add=add, make_ready=make_ready)
    pv.shutdown_previews()
    pv._stop.clear()
    library_paths.invalidate_cache()


def run_inventory(library_id, mode):
    if mode == "immediate":
        result = pv.preview_batch(pv.PreviewBatchBody(library_id=library_id))
        assert result["state"] == "accepted"
        task = pv._submissions().tasks[library_id]
        return task.thread, lambda: task.snapshot()
    outcome = {}

    def idle():
        try:
            outcome["result"] = pv.preview_queue_existing(pv.PreviewQueueBody(library_id=library_id))
        except Exception as error:
            outcome["error"] = error

    thread = threading.Thread(target=idle)
    thread.start()
    return thread, lambda: outcome


def finish(thread, result):
    thread.join(timeout=3)
    assert not thread.is_alive(), "preview inventory remained blocked after the test released I/O"
    return result()


def block_first_check(monkeypatch, item_id, *, at=1):
    entered, release = threading.Event(), threading.Event()
    original = pv._current_ready
    calls = [0]

    def checked(row, src, interval):
        result = original(row, src, interval)
        if row["id"] == item_id:
            calls[0] += 1
            if calls[0] == at:
                entered.set()
                assert release.wait(5), "test did not release the saved preview check"
        return result

    monkeypatch.setattr(pv, "_current_ready", checked)
    return entered, release


def mutate_while_checking(change, fixture):
    lid = fixture.library["id"]
    old = fixture.first["file_path"]
    if change == "rename":
        result = fs.fs_rename({"library_id": lid, "from": old,
                               "name": "renamed.mkv", "dry_run": False})
        assert result["moved"] == 1
        assert store.get_playable("movie", fixture.first["id"])["file_path"] == "before/renamed.mkv"
    elif change == "move":
        result = fs.fs_move({"library_id": lid, "from": old,
                            "to_dir": "after", "dry_run": False})
        assert result["moved"] == 1
        assert store.get_playable("movie", fixture.first["id"])["file_path"] == "after/first.mkv"
    elif change == "directory":
        # Directory organization keeps IDs while rebasing catalogue paths. The
        # legacy organizer does not journal these writes, so the source guard
        # must not rely exclusively on FileStation's fs_changes watermark.
        with library_mutation_lock(lid):
            (fixture.root / "before").rename(fixture.root / "after")
            store.repath_movies_prefix(lid, "before", "after")
    elif change in ("replace", "replace_during_recheck"):
        (fixture.root / old).write_bytes(b"new source has a different complete revision")
    elif change == "delete":
        result = fs.fs_delete({"library_id": lid, "paths": [old],
                              "dry_run": False, "confirm": True})
        assert result["deleted"] == 1
        assert store.get_playable("movie", fixture.first["id"]) is None
    elif change == "scan_add":
        # This is the scanner's catalogue-registration operation, without parsing
        # or contacting metadata services for an intentionally synthetic source.
        fixture.add("newly-discovered.mkv")
    elif change == "external_delete":
        (fixture.root / old).unlink()
    else:
        raise AssertionError(change)


@pytest.mark.parametrize("mode", ["immediate", "idle"])
@pytest.mark.parametrize("change,ready", [
    ("rename", True), ("move", True), ("directory", True), ("replace", True),
    ("delete", False), ("scan_add", True), ("external_delete", True),
    ("replace_during_recheck", False),
])
def test_changed_catalogue_or_source_rejects_whole_inventory_then_retry_uses_current_state(
        catalogue, monkeypatch, mode, change, ready):
    inv = catalogue
    if ready:
        inv.make_ready()
    entered, release = block_first_check(monkeypatch, inv.first["id"],
                                        at=2 if change == "replace_during_recheck" else 1)
    thread, result = run_inventory(inv.library["id"], mode)
    assert entered.wait(2)
    errors = []

    def change_source():
        try:
            mutate_while_checking(change, inv)
        except BaseException as error:
            errors.append(error)

    mutation = threading.Thread(target=change_source)
    mutation.start()
    try:
        mutation.join(timeout=1)
        assert not mutation.is_alive(), "saved-preview I/O blocked a physical file mutation"
        if errors:
            raise errors[0]
    finally:
        release.set()
        mutation.join(timeout=2)
    outcome = finish(thread, result)
    if mode == "immediate":
        assert outcome["state"] == "error", outcome
        assert "重试" in outcome["error"] or "重新" in outcome["error"], outcome
    else:
        assert "error" in outcome, outcome
        assert isinstance(outcome["error"], HTTPException), outcome
        assert outcome["error"].status_code in (409, 410, 503)
    assert pv._auto_queue == [], "an invalidated inventory committed a partial or stale queue"
    if pv._queue_path().exists():
        assert json.loads(pv._queue_path().read_text()) == []

    retry_thread, retry_result = run_inventory(inv.library["id"], mode)
    retry = finish(retry_thread, retry_result)
    if mode == "idle":
        assert "error" not in retry, retry
        retry = retry["result"]
    else:
        assert retry["state"] == "done", retry
    expected = 1 if change in ("delete", "external_delete") else 2
    assert retry["added"] == expected
    assert len(pv._auto_queue) == expected
    current = {(row["kind"], row["id"]) for row in store.get_library_preview_playables(inv.library["id"])}
    assert {(item["kind"], item["id"]) for item in pv._auto_queue} <= current


@pytest.mark.parametrize("mode", ["immediate", "idle"])
def test_metadata_only_inventory_does_not_resolve_read_urls_or_probe_media(catalogue, monkeypatch, mode):
    catalogue.make_ready()

    def unexpected(*_args, **_kwargs):
        raise AssertionError("attribute inventory must not resolve a read URL or decode media")

    monkeypatch.setattr(storage.LocalStorageBackend, "get_read_url", unexpected)
    monkeypatch.setattr(pv, "_media_cached_or_probe", unexpected)
    monkeypatch.setattr(pv, "_generate", unexpected)
    thread, result = run_inventory(catalogue.library["id"], mode)
    outcome = finish(thread, result)
    if mode == "idle":
        assert "error" not in outcome, outcome
        outcome = outcome["result"]
    else:
        assert outcome["state"] == "done", outcome
    assert outcome["ready"] == 1 and outcome["added"] == 1


@pytest.mark.parametrize("mode", ["immediate", "idle"])
def test_change_in_another_library_does_not_invalidate_selected_library(catalogue, monkeypatch, tmp_path, mode):
    inv = catalogue
    inv.make_ready()
    other_root = tmp_path / "other-media"
    other_root.mkdir()
    other_library = store.create_library("Unrelated library", path=str(other_root))
    entered, release = block_first_check(monkeypatch, inv.first["id"])
    thread, result = run_inventory(inv.library["id"], mode)
    try:
        assert entered.wait(2)
        inv.add("discovered.mkv", selected_library=other_library)
    finally:
        release.set()
    outcome = finish(thread, result)
    if mode == "idle":
        assert "error" not in outcome, outcome
        outcome = outcome["result"]
    else:
        assert outcome["state"] == "done", outcome
    assert outcome["ready"] == 1 and outcome["added"] == 1
    assert all(item["library_id"] == inv.library["id"] for item in pv._auto_queue)


def test_reconcile_does_not_retire_a_replaced_identical_queue_membership(catalogue, monkeypatch):
    inv = catalogue
    inv.make_ready()
    original = {"kind": "movie", "id": inv.first["id"], "library_id": inv.library["id"]}
    pv._enqueue_auto_items([original])
    entered, release = block_first_check(monkeypatch, inv.first["id"])
    thread = threading.Thread(target=pv._reconcile_ready_queue)
    thread.start()
    try:
        assert entered.wait(2)
        with pv._lock:
            pv._auto_queue.clear()
            replacement = dict(original)
            pv._auto_queue.append(replacement)
            pv._save_auto_queue()
    finally:
        release.set()
    thread.join(timeout=3)
    assert not thread.is_alive()
    assert pv._auto_queue == [original]
    assert pv._auto_queue[0] is replacement
    assert json.loads(pv._queue_path().read_text()) == [original]


@pytest.mark.parametrize("change", ["rename", "replace", "delete"])
def test_reconcile_does_not_retire_a_source_changed_after_complete_validation(catalogue, monkeypatch, change):
    inv = catalogue
    inv.make_ready()
    entry = {"kind": "movie", "id": inv.first["id"], "library_id": inv.library["id"]}
    pv._enqueue_auto_items([entry])
    entered, release = block_first_check(monkeypatch, inv.first["id"])
    thread = threading.Thread(target=pv._reconcile_ready_queue)
    thread.start()
    try:
        assert entered.wait(2)
        mutate_while_checking(change, inv)
    finally:
        release.set()
    thread.join(timeout=3)
    assert not thread.is_alive()
    assert pv._auto_queue == [entry], "old completion validation retired a changed source"
    assert json.loads(pv._queue_path().read_text()) == [entry]


def configure_generation(catalogue, monkeypatch):
    """Drive the real single-item dispatcher without decoding synthetic media."""
    inv = catalogue
    monkeypatch.setattr(pv, "_media_cached_or_probe", lambda *_args: {"playable": True, "duration": 30})
    monkeypatch.setattr(pv, "_transcoding_busy", lambda: False)
    monkeypatch.setattr(pv, "_playback_busy", lambda: False)
    monkeypatch.setattr(pv, "_auto_allowed_now", lambda: True)
    monkeypatch.setattr(pv, "_auto_pause_reason", lambda: "")
    entry = {"kind": "movie", "id": inv.first["id"], "library_id": inv.library["id"]}
    pv._enqueue_auto_items([entry])
    ledger = pv._failure_ledger()
    ledger.update("movie", inv.first["id"], dict(
        kind="movie", id=inv.first["id"], library_id=inv.library["id"],
        failed_at=100, error_code="decoder", error="previous source failure"))
    assert ledger.flush()
    return entry, ledger.snapshot()


@pytest.mark.parametrize("change", ["rename", "replace", "delete"])
@pytest.mark.parametrize("generation_fails", [False, True])
def test_worker_preserves_request_and_failure_when_source_changes_during_generation(
        catalogue, monkeypatch, change, generation_fails):
    inv = catalogue
    entry, failures = configure_generation(inv, monkeypatch)
    entered, release = threading.Event(), threading.Event()
    generated = []

    def generate(_jid, row, src, _info, _interval):
        generated.append((row["file_path"], src.size))
        if len(generated) == 1:
            entered.set()
            assert release.wait(5), "test did not release preview generation"
            if generation_fails:
                raise RuntimeError("无法从采样位置取得预览画面")

    monkeypatch.setattr(pv, "_generate", generate)
    pv._dispatch_queue_once()
    jid, worker = pv._auto_job_id, pv._worker_thread
    try:
        assert entered.wait(2)
        mutate_while_checking(change, inv)
    finally:
        release.set()
    worker.join(timeout=3)
    assert not worker.is_alive()
    assert pv._jobs.get(jid)["state"] == "cancelled"
    assert pv._auto_queue == [entry], "stale generation retired the current source's request"
    assert json.loads(pv._queue_path().read_text()) == [entry]
    assert pv._failure_ledger().snapshot() == failures, "stale completion cleared or replaced the failure"

    if change != "delete":
        pv._dispatch_queue_once()
        following = pv._auto_job_id
        pv._worker_thread.join(timeout=3)
        assert not pv._worker_thread.is_alive()
        assert following != jid
        assert pv._jobs.get(following)["state"] == "done"
        current = store.get_playable("movie", inv.first["id"])
        current_size = (inv.root / current["file_path"]).stat().st_size
        assert generated[-1] == (current["file_path"], current_size)
        assert generated[-1] != generated[0]
        assert pv._auto_queue == []
        assert pv._failure_ledger().snapshot() == {}


def test_worker_does_not_retire_an_identical_request_recreated_during_generation(catalogue, monkeypatch):
    inv = catalogue
    entry, _failures = configure_generation(inv, monkeypatch)
    entered, release = threading.Event(), threading.Event()

    def generate(*_args):
        entered.set()
        assert release.wait(5), "test did not release preview generation"

    monkeypatch.setattr(pv, "_generate", generate)
    pv._dispatch_queue_once()
    jid, worker = pv._auto_job_id, pv._worker_thread
    try:
        assert entered.wait(2)
        with pv._lock:
            replacement = dict(entry)
            pv._auto_queue[:] = [replacement]
            pv._save_auto_queue()
    finally:
        release.set()
    worker.join(timeout=3)
    assert not worker.is_alive()
    assert pv._jobs.get(jid)["state"] in ("done", "cancelled")
    assert pv._auto_queue == [entry] and pv._auto_queue[0] is replacement
    assert json.loads(pv._queue_path().read_text()) == [entry]
    # Finalizing the old terminal job must also preserve the new membership.
    # Keep the next worker behind the claim lock until these checks complete.
    with pv._lock:
        pv._dispatch_queue_once()
        following = pv._auto_job_id
        assert following != jid
        assert pv._auto_queue == [entry] and pv._auto_queue[0] is replacement
        assert "_queue_owner" not in pv.preview_job(following)
    pv._worker_thread.join(timeout=3)
    assert not pv._worker_thread.is_alive()
    assert pv._jobs.get(following)["state"] == "done"
    assert pv._auto_queue == []


def test_worker_completes_and_clears_failure_for_the_unchanged_source(catalogue, monkeypatch):
    inv = catalogue
    configure_generation(inv, monkeypatch)
    generated = []
    monkeypatch.setattr(pv, "_generate", lambda _jid, row, *_args: generated.append(row["id"]))
    pv._dispatch_queue_once()
    jid, worker = pv._auto_job_id, pv._worker_thread
    worker.join(timeout=3)
    assert not worker.is_alive()
    assert generated == [inv.first["id"]]
    assert pv._jobs.get(jid)["state"] == "done"
    assert pv._auto_queue == []
    assert json.loads(pv._queue_path().read_text()) == []
    assert pv._failure_ledger().snapshot() == {}


def test_probe_failure_after_source_replacement_is_not_attributed_to_the_new_source(catalogue, monkeypatch):
    inv = catalogue
    entry, failures = configure_generation(inv, monkeypatch)

    def changed_probe(row, _src):
        (inv.root / row["file_path"]).write_bytes(b"replacement arrived while the old source was being probed")
        raise RuntimeError("片源不可播放")

    def unexpected(*_args):
        raise AssertionError("failed probing must not start generation")

    monkeypatch.setattr(pv, "_media_cached_or_probe", changed_probe)
    monkeypatch.setattr(pv, "_generate", unexpected)
    pv._dispatch_queue_once()
    jid, worker = pv._auto_job_id, pv._worker_thread
    worker.join(timeout=3)
    assert not worker.is_alive()
    assert pv._jobs.get(jid)["state"] == "cancelled"
    assert pv._auto_queue == [entry]
    assert pv._failure_ledger().snapshot() == failures


@pytest.mark.parametrize("change,status", [("rename", 410), ("library_root", 503)])
def test_source_resolution_failure_after_identity_change_preserves_request_and_failure(
        catalogue, monkeypatch, change, status):
    inv = catalogue
    entry, failures = configure_generation(inv, monkeypatch)
    entered, release = threading.Event(), threading.Event()

    def source_failure(*_args):
        entered.set()
        assert release.wait(5), "test did not release source resolution"
        raise HTTPException(status, "the original source became unavailable")

    monkeypatch.setattr(pv, "_version_source", source_failure)
    pv._dispatch_queue_once()
    jid, worker = pv._auto_job_id, pv._worker_thread
    try:
        assert entered.wait(2)
        if change == "rename":
            mutate_while_checking("rename", inv)
        else:
            replacement_root = inv.root / "replacement-media"
            replacement_root.mkdir()
            store.update_library(inv.library["id"], subpath=replacement_root.name)
    finally:
        release.set()
    worker.join(timeout=3)
    assert not worker.is_alive()
    assert pv._jobs.get(jid)["state"] == "cancelled"
    assert pv._auto_queue == [entry]
    assert json.loads(pv._queue_path().read_text()) == [entry]
    assert pv._failure_ledger().snapshot() == failures


def test_unchanged_missing_source_records_failure_and_retires_request(catalogue, monkeypatch):
    inv = catalogue
    configure_generation(inv, monkeypatch)
    (inv.root / inv.first["file_path"]).unlink()
    pv._dispatch_queue_once()
    jid, worker = pv._auto_job_id, pv._worker_thread
    worker.join(timeout=3)
    assert not worker.is_alive()
    assert pv._jobs.get(jid)["state"] == "failed"
    record = pv._failure_ledger().snapshot()[("movie", inv.first["id"])]
    assert record["library_id"] == inv.library["id"]
    assert record["error_code"] == "source_missing"
    pv._dispatch_queue_once()
    assert pv._auto_queue == []
    assert json.loads(pv._queue_path().read_text()) == []


@pytest.mark.parametrize("change", ["restore", "replace"])
def test_source_resolution_failure_after_same_path_recovery_preserves_request_and_failure(
        catalogue, monkeypatch, change):
    inv = catalogue
    entry, failures = configure_generation(inv, monkeypatch)
    path = inv.root / inv.first["file_path"]
    original = path.read_bytes()
    if change == "restore":
        path.unlink()
    entered, release = threading.Event(), threading.Event()

    def source_failure(*_args):
        entered.set()
        assert release.wait(5), "test did not release source resolution"
        raise HTTPException(410, "the original source was missing")

    monkeypatch.setattr(pv, "_version_source", source_failure)
    pv._dispatch_queue_once()
    jid, worker = pv._auto_job_id, pv._worker_thread
    try:
        assert entered.wait(2)
        path.write_bytes(original if change == "restore" else b"replacement source with a new revision")
    finally:
        release.set()
    worker.join(timeout=3)
    assert not worker.is_alive()
    assert pv._jobs.get(jid)["state"] == "cancelled"
    assert pv._auto_queue == [entry]
    assert json.loads(pv._queue_path().read_text()) == [entry]
    assert pv._failure_ledger().snapshot() == failures


def test_unchanged_source_with_unavailable_read_url_records_failure_and_retires_request(
        catalogue, monkeypatch):
    inv = catalogue
    configure_generation(inv, monkeypatch)

    def unavailable_read_url(*_args):
        raise HTTPException(503, "source read URL is unavailable")

    monkeypatch.setattr(pv, "_version_source", unavailable_read_url)
    pv._dispatch_queue_once()
    jid, worker = pv._auto_job_id, pv._worker_thread
    worker.join(timeout=3)
    assert not worker.is_alive()
    assert pv._jobs.get(jid)["state"] == "failed"
    record = pv._failure_ledger().snapshot()[("movie", inv.first["id"])]
    assert record["library_id"] == inv.library["id"]
    assert record["error_code"] == "source_offline"
    pv._dispatch_queue_once()
    assert pv._auto_queue == []
    assert json.loads(pv._queue_path().read_text()) == []
