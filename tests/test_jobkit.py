from app.jobkit import JobRegistry


def test_latest_prefers_new_scan_when_jobs_start_in_same_second(monkeypatch):
    monkeypatch.setattr("app.jobkit.time.time", lambda: 1000)
    jobs = JobRegistry()
    empty = jobs.create(library_id=1)
    jobs.update(empty["job_id"], state="done")
    imported = jobs.create(library_id=1)
    assert jobs.running()["job_id"] == imported["job_id"]
    jobs.update(imported["job_id"], state="done", total=1, done=1)
    assert jobs.latest()["job_id"] == imported["job_id"]
    assert jobs.latest()["total"] == 1


def test_cancelled_worker_remains_discoverable_until_io_finishes():
    jobs = JobRegistry(max_finished=2)
    copying = jobs.create(worker_finished=False)
    jobs.cancel(copying['job_id'])
    for _ in range(6):
        other = jobs.create()
        jobs.update(other['job_id'], state='done')
    assert jobs.get(copying['job_id'])['worker_finished'] is False
    assert any(job['job_id'] == copying['job_id'] for job in jobs.snapshot())
    jobs.update(copying['job_id'], worker_finished=True)
    assert len(jobs.snapshot()) == 2
