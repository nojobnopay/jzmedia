"""Inventory reservations survive retries, failures and interrupted lifecycles."""
from concurrent.futures import ThreadPoolExecutor
import json
import threading

import pytest

from app.routers.stream.preview_submission import SubmissionRegistry


def finish(registry, library_id=1):
    task = registry.tasks[library_id]
    task.thread.join(timeout=2)
    assert not task.thread.is_alive()
    return registry.latest(library_id)


def test_concurrent_same_library_submissions_share_one_worker_and_durable_reservation(tmp_path):
    registry = SubmissionRegistry(tmp_path)
    entered, release = threading.Event(), threading.Event()
    calls = []

    def worker(task):
        calls.append(task.snapshot()["id"])
        entered.set()
        assert release.wait(3)
        task.update(state="done", phase="done", total=5, checked=5)

    try:
        with ThreadPoolExecutor(max_workers=12) as executor:
            results = list(executor.map(lambda _i: registry.submit(1, worker), range(12)))
        assert entered.wait(1)
        assert len({result["id"] for result in results}) == len(calls) == 1
        saved = json.loads(registry.path.read_text(encoding="utf-8"))
        assert len(saved) == 1 and saved[0]["id"] == results[0]["id"]
        assert saved[0]["state"] == "running"
        results[0]["checked"] = 999
        assert registry.latest(1)["checked"] == 0
    finally:
        release.set()
        registry.shutdown()


def test_different_libraries_keep_separate_reservations(tmp_path):
    registry = SubmissionRegistry(tmp_path)
    entered = {1: threading.Event(), 2: threading.Event()}
    release = threading.Event()

    def worker(task):
        entered[task.snapshot()["library_id"]].set()
        assert release.wait(3)
        task.update(state="done", phase="done")

    try:
        first, second = registry.submit(1, worker), registry.submit(2, worker)
        assert entered[1].wait(1) and entered[2].wait(1)
        assert first["id"] != second["id"]
    finally:
        release.set()
        registry.shutdown()


@pytest.mark.parametrize("previous", [False, True])
def test_failed_initial_persistence_does_not_start_worker_or_leave_reservation(
        tmp_path, monkeypatch, previous):
    registry = SubmissionRegistry(tmp_path)
    prior = None
    if previous:
        registry.submit(1, lambda task: task.update(state="done", phase="done"))
        prior = finish(registry)
    called = threading.Event()

    def fail():
        raise OSError("isolated write failure")

    monkeypatch.setattr(registry, "save", fail)
    with pytest.raises(OSError, match="isolated write failure"):
        registry.submit(1, lambda _task: called.set())
    assert not called.is_set()
    assert registry.latest(1) == prior


def test_thread_start_failure_is_terminal_and_can_be_retried(tmp_path, monkeypatch):
    registry = SubmissionRegistry(tmp_path)
    called = threading.Event()
    original_start = threading.Thread.start

    def fail_start(_thread):
        raise RuntimeError("isolated thread limit")

    monkeypatch.setattr(threading.Thread, "start", fail_start)
    with pytest.raises(OSError, match="could not start"):
        registry.submit(1, lambda _task: called.set())
    failed = registry.latest(1)
    assert failed["state"] == "error" and failed["error"]
    assert not called.is_set()
    assert registry.tasks[1].thread is None
    assert json.loads(registry.path.read_text(encoding="utf-8"))[0]["state"] == "error"
    monkeypatch.setattr(threading.Thread, "start", original_start)
    retry = registry.submit(1, lambda task: task.update(state="done", phase="done"))
    assert retry["id"] != failed["id"]
    assert finish(registry)["state"] == "done"


def test_worker_exception_becomes_observable_durable_error(tmp_path):
    registry = SubmissionRegistry(tmp_path)

    def worker(task):
        task.update(phase="checking", total=8, checked=3)
        raise OSError("isolated storage failure")

    registry.submit(1, worker)
    result = finish(registry)
    assert result["state"] == "error" and result["error"]
    assert (result["total"], result["checked"]) == (8, 3)
    assert json.loads(registry.path.read_text(encoding="utf-8"))[0] == result


def test_worker_returning_without_result_does_not_leave_running_state(tmp_path):
    registry = SubmissionRegistry(tmp_path)
    registry.submit(1, lambda _task: None)
    assert finish(registry)["state"] == "error"


def test_restart_marks_interrupted_inventory_and_preserves_completed_library(tmp_path):
    history = [dict(id="a" * 32, library_id=1, state="running", phase="checking",
                    total=5000, checked=130, added=0, promoted=0, ready=120,
                    already_queued=0, unavailable=10, batch_id=None, error=""),
               dict(id="b" * 32, library_id=2, state="done", phase="done",
                    total=2, checked=2, added=2, promoted=0, ready=0,
                    already_queued=0, unavailable=0, batch_id="c" * 32, error="")]
    (tmp_path.parent / f".{tmp_path.name}-submissions.json").write_text(json.dumps(history), encoding="utf-8")
    registry = SubmissionRegistry(tmp_path)
    interrupted = registry.latest(1)
    assert interrupted["state"] == "error" and "重启" in interrupted["error"]
    assert interrupted["checked"] == 130
    assert registry.latest(2) == history[1]
    retry = registry.submit(1, lambda task: task.update(state="done", phase="done"))
    assert retry["id"] != interrupted["id"]
    assert finish(registry)["state"] == "done"


def test_shutdown_records_cancel_before_late_worker_and_prevents_old_file_overwrite(tmp_path, monkeypatch):
    registry = SubmissionRegistry(tmp_path)
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    updates = []

    def worker(task):
        entered.set()
        assert release.wait(3)
        updates.append(task.update(state="done", phase="done", checked=5000))
        finished.set()

    registry.submit(1, worker)
    assert entered.wait(1)
    task = registry.tasks[1]
    join = task.thread.join
    # Simulate the bounded shutdown wait expiring during a blocked storage call.
    monkeypatch.setattr(task.thread, "join", lambda **_kwargs: None)
    registry.shutdown()
    cancelled = registry.latest(1)
    assert task.stop.is_set() and cancelled["state"] == "cancelled"
    assert json.loads(registry.path.read_text(encoding="utf-8"))[0]["state"] == "cancelled"
    with pytest.raises(OSError, match="stopping"):
        registry.submit(1, lambda _task: pytest.fail("closed registry must not start workers"))
    restarted = SubmissionRegistry(tmp_path)
    restarted.submit(1, lambda task: task.update(state="done", phase="done", checked=2))
    current = finish(restarted)
    try:
        release.set()
        assert finished.wait(1)
        join(timeout=1)
        assert not task.thread.is_alive()
        assert updates == [False]
        assert registry.latest(1) == cancelled
        assert json.loads(registry.path.read_text(encoding="utf-8"))[0] == current
    finally:
        release.set()
        join(timeout=1)


def test_best_effort_progress_save_failure_keeps_task_running_and_final_result_can_persist(
        tmp_path, monkeypatch):
    registry = SubmissionRegistry(tmp_path)
    entered, release = threading.Event(), threading.Event()

    def worker(task):
        entered.set()
        assert release.wait(3)
        task.update(checked=3, total=5, phase="checking")
        task.update(state="done", phase="done", checked=5)

    registry.submit(1, worker)
    assert entered.wait(1)
    original_save = registry.save
    attempts = []

    def save():
        attempts.append(True)
        if len(attempts) == 1:
            raise OSError("isolated progress write failure")
        original_save()

    monkeypatch.setattr(registry, "save", save)
    registry.saved_at = 0
    release.set()
    result = finish(registry)
    assert result["state"] == "done" and result["checked"] == 5
    assert len(attempts) == 2
    assert json.loads(registry.path.read_text(encoding="utf-8"))[0] == result


def test_progress_writes_do_not_invalidate_sprite_discovery_signature(tmp_path):
    registry = SubmissionRegistry(tmp_path / "previews")
    registry.root.mkdir()
    before = registry.root.stat().st_mtime_ns
    registry.submit(1, lambda task: task.update(state="done", phase="done", total=5000, checked=5000))
    assert finish(registry)["state"] == "done"
    assert registry.root.stat().st_mtime_ns == before
    assert registry.path.parent == registry.root.parent
