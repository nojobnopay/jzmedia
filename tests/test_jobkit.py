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
