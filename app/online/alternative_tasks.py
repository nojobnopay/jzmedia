"""Bounded, pull-driven alternate probes; no workers survive an idle client."""

from collections import OrderedDict
from dataclasses import dataclass, field
from threading import Lock
import time
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel

from ..network.guard import NetworkOperation, child_operation
from . import alternatives, catalog, recommendations, service
from .models import OnlinePlaybackIdentity
from .providers import ProviderError


class Candidate(alternatives.Alternative):
    recommendation_token: str


class Progress(BaseModel):
    task_id: str
    status: Literal["queued", "running", "partial", "done"]
    items: list[Candidate]
    retry_after_ms: int
    partial: bool


@dataclass
class Task:
    access: service.PlaybackAccess
    comparison: alternatives.Comparison
    expires: float
    id: str = field(default_factory=lambda: "alt_" + uuid4().hex)
    tokens: dict = field(default_factory=dict)
    active: NetworkOperation | None = None


_LIFETIME = 90
_RETRY_MS = 300
_tasks = {}
# Only clients actively requesting a turn enter the queue. An abandoned poll
# loses its place quickly even if its DELETE never arrives.
_waiting = OrderedDict()
_lock = Lock()


def _prune(now):
    for key, task in list(_tasks.items()):
        if task.expires <= now and task.active is None:
            del _tasks[key]
    for key, deadline in list(_waiting.items()):
        if deadline <= now or key not in _tasks:
            del _waiting[key]


def sweep():
    with _lock:
        _prune(time.monotonic())


def _get(access, key):
    task = _tasks.get(key)
    if not task or task.access != access:
        raise catalog.CatalogError("online_alternative_task_expired", 404)
    return task


def _reply(task, comparison, tokens, queued=False):
    ranked = comparison.ranked(task.access)
    if comparison.done:
        status = "partial" if comparison.partial else "done"
    else:
        status = "queued" if queued else "running"
    return {"task_id": task.id, "status": status, "retry_after_ms": _RETRY_MS if queued else 0,
            "partial": comparison.partial,
            "items": [{**item.row, "recommendation_token": tokens[OnlinePlaybackIdentity.model_validate(item.row["identity"])]}
                      for item in ranked]}


def start(access, identity):
    comparison = alternatives.Comparison.start(access, identity, incremental=True)
    now = time.monotonic()
    with _lock:
        _prune(now)
        # Finished tasks still answer a retried final advance, but shouldn't
        # consume this viewer's quota when a new comparison is requested.
        for key, previous in list(_tasks.items()):
            if previous.active is None and previous.access == access and previous.comparison.done:
                del _tasks[key]
        owned = [task for task in _tasks.values() if task.access == access]
        if len(owned) >= 4 or len(_tasks) >= 64:
            raise catalog.CatalogError("provider_catalog_busy", 429)
        task = Task(access, comparison, now + _LIFETIME)
        _tasks[task.id] = task
    return _reply(task, comparison, {})


def cancel(access, key):
    with _lock:
        task = _get(access, key)
        del _tasks[key]
        _waiting.pop(key, None)
        operation = task.active
    if operation is not None:
        operation.abort()


def advance(access, key, *, operation):
    now = time.monotonic()
    with _lock:
        _prune(now)
        task = _get(access, key)
        comparison, tokens = task.comparison, task.tokens
        queued = task.active is not None
        if not comparison.done and not queued:
            _waiting[key] = now + 1.5
            queued = next(iter(_waiting)) != key
            if not queued:
                task.active = operation
    if comparison.done or queued:
        return _reply(task, comparison, tokens, queued=queued)

    working = comparison.fork()
    progressed = False
    try:
        # Foreground catalogue reads can preempt the child without cancelling
        # this HTTP caller. Publish only an entire verified step after admission.
        try:
            with child_operation(operation, operation.deadline) as admitted:
                with catalog._admit(access.owner, admitted, background=True):
                    working.step(access, admitted)
            progressed = True
        except catalog.BackgroundDeferred:
            pass
        except ProviderError as exc:
            if exc.category != "provider_cancelled":
                raise
            operation.check()
        operation.check()
        with _lock:
            _get(access, key)
            if not progressed:
                # This viewer may have its own foreground read. Let another
                # owner try the next turn even when this one cannot be admitted.
                if key in _waiting:
                    _waiting.move_to_end(key)
            else:
                tokens = tokens.copy()
                for result in working.results[len(comparison.results):]:
                    token = recommendations.issue(access, result.row["identity"], result.match, result.configuration)
                    tokens[OnlinePlaybackIdentity.model_validate(result.row["identity"])] = token
                comparison = task.comparison = working
                task.tokens = tokens
                _waiting.pop(key, None)
                # A second tab from this owner cannot jump ahead of another
                # device already polling for its turn.
                for other in list(_waiting):
                    if _tasks[other].access.owner == access.owner:
                        _waiting.move_to_end(other)
        return _reply(task, comparison, tokens, queued=not progressed)
    finally:
        with _lock:
            if task.active is operation:
                task.active = None
