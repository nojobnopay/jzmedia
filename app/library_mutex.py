"""Per-video-library mutation locks.

Slow storage reads may share this lock with catalogue mutations for the same
library, but unrelated SQLite readers and other libraries must never wait for
them. The process is single-worker today; keeping this helper separate makes
the coordination boundary explicit.
"""
import threading


_guard = threading.Lock()
_locks: dict[int, threading.RLock] = {}


def library_mutation_lock(library_id: int) -> threading.RLock:
    lid = int(library_id)
    with _guard:
        lock = _locks.get(lid)
        if lock is None:
            lock = threading.RLock()
            _locks[lid] = lock
        return lock


__all__ = ["library_mutation_lock"]
