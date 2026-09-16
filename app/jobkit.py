"""通用内存任务注册表（评审 B9/R04-D6）：扫描等长任务复用 collections 的 job 模式。

- 单进程内存态，重启丢失可重跑（任务本身幂等）；
- 只保留运行中 + 最近 N 个完成态，防无界增长；
- 协作式取消：worker 通过 `state_of()`/`should_stop()` 主动退出。
"""
import threading
import time
import uuid


class JobRegistry:
    def __init__(self, max_finished: int = 5, prefix: str = "job"):
        self._jobs: dict = {}
        self._lock = threading.RLock()
        self._max_finished = max(1, int(max_finished))
        self._prefix = prefix

    def create(self, **fields) -> dict:
        jid = uuid.uuid4().hex[:12]
        job = {"job_id": jid, "state": "running", "done": 0, "total": 0,
               "started_at": int(time.time()), "finished_at": 0,
               "current": "", "failed": [], "error": ""}
        job.update(fields)
        with self._lock:
            self._jobs[jid] = job
        return dict(job)

    def update(self, jid: str, **fields) -> None:
        with self._lock:
            job = self._jobs.get(jid)
            if job is None:
                return
            job.update(fields)
            if fields.get("state") in ("done", "cancelled", "failed") \
                    and not job.get("finished_at"):
                job["finished_at"] = int(time.time())
            self._trim_locked()

    def get(self, jid: str) -> dict | None:
        with self._lock:
            job = self._jobs.get(jid or "")
            return dict(job) if job else None

    def cancel(self, jid: str) -> str:
        with self._lock:
            job = self._jobs.get(jid or "")
            if job is None:
                return "idle"
            if job["state"] == "running":
                job["state"] = "cancelled"
                job["finished_at"] = int(time.time())
            return job["state"]

    def running(self) -> dict | None:
        with self._lock:
            for job in self._jobs.values():
                if job["state"] == "running":
                    return dict(job)
        return None

    def latest(self) -> dict | None:
        with self._lock:
            if not self._jobs:
                return None
            jid = max(self._jobs, key=lambda k: self._jobs[k].get("started_at", 0))
            return dict(self._jobs[jid])

    def _trim_locked(self) -> None:
        finished = [(k, j) for k, j in self._jobs.items() if j["state"] != "running"]
        if len(finished) <= self._max_finished:
            return
        finished.sort(key=lambda kv: kv[1].get("finished_at") or kv[1].get("started_at") or 0)
        for k, _ in finished[:len(finished) - self._max_finished]:
            self._jobs.pop(k, None)
