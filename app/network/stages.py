"""Fixed-size, request-local timings; never retain network targets or secrets.

Stages may overlap: ``response_headers`` includes connection establishment.
It measures waiting for complete HTTP headers, not an isolated first byte.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import json
import logging
import math
import re
import ssl
import time


STAGES = ("dns", "tcp", "peer", "tls", "proxy_connect", "response_headers", "body")
_PURPOSES = {"api", "media", "image", "subtitle", "subscription"}
_TIMEOUTS = {"ConnectTimeout", "ReadTimeout", "WriteTimeout", "PoolTimeout", "TimeoutError"}
_COUNT_LIMIT = 1024
_MILLISECONDS_LIMIT = 24 * 60 * 60 * 1000
_current = ContextVar("network_stage_observation", default=None)
_logger = logging.getLogger("jzmedia.network.stages")


def _outcome(error):
    if isinstance(error, ssl.SSLCertVerificationError):
        return "certificate_rejected"
    category = getattr(error, "category", None)
    if category is None and error.args and isinstance(error.args[0], str):
        # Only compare fixed internal codes; never format the original error.
        category = error.args[0]
    if category in {"operation_cancelled", "provider_cancelled"} or isinstance(error, GeneratorExit):
        return "cancelled"
    if category in {"network_timeout", "network_connect_timeout", "operation_timeout", "target_dns_timeout"} or type(error).__name__ in _TIMEOUTS:
        return "timeout"
    if isinstance(error, (ValueError, ssl.SSLError)):
        return "rejected"
    return "error"


class StageObservation:
    """Only fixed enum keys and bounded counters survive a measured call."""

    def __init__(self, purpose, fingerprint, *, clock=time.monotonic):
        self.purpose = purpose if purpose in _PURPOSES else "unknown"
        self.policy = fingerprint if isinstance(fingerprint, str) and re.fullmatch(r"[0-9a-f]{64}", fingerprint) else "unavailable"
        self._clock = clock
        self._started = clock()
        self._values = {name: {"count": 0, "failures": 0, "ms": 0.0} for name in STAGES}
        self._depth = {name: 0 for name in STAGES}
        self.failure = None
        self.status = None

    def begin(self, name):
        started = self._clock() if self._depth[name] == 0 else None
        # A later stage begins only after an earlier failed attempt recovered.
        # During exception unwinding, no new stage starts, preserving the most
        # specific inner failure rather than replacing TLS with HTTP headers.
        self.failure = None
        self._depth[name] += 1
        if self._depth[name] != 1:
            return None
        value = self._values[name]
        value["count"] = min(_COUNT_LIMIT, value["count"] + 1)
        return started

    def failed(self, name, error):
        if self.failure is None:
            self.failure = (name, _outcome(error))
        value = self._values[name]
        if self._depth[name] == 1:
            value["failures"] = min(_COUNT_LIMIT, value["failures"] + 1)

    def end(self, name, started):
        self._depth[name] -= 1
        if started is None:
            return
        elapsed = (self._clock() - started) * 1000
        if math.isfinite(elapsed) and elapsed >= 0:
            value = self._values[name]
            value["ms"] = min(_MILLISECONDS_LIMIT, value["ms"] + elapsed)

    def summary(self, outcome):
        elapsed = (self._clock() - self._started) * 1000
        elapsed = min(_MILLISECONDS_LIMIT, max(0, elapsed)) if math.isfinite(elapsed) else 0
        failure_stage = (self.failure[0] if self.failure else "request") if outcome != "ok" else "none"
        return {"purpose": self.purpose, "policy": self.policy, "outcome": outcome,
                "failure_stage": failure_stage, "status": self.status, "elapsed_ms": round(elapsed, 1),
                "stages": {name: {**value, "ms": round(value["ms"], 1)} for name, value in self._values.items()}}


@contextmanager
def stage(name):
    observation, started, entered = None, None, False
    try:
        candidate = _current.get()
        if candidate is not None and name in STAGES:
            started = candidate.begin(name)
            observation, entered = candidate, True
    except Exception:
        pass
    try:
        yield
    except BaseException as error:
        if observation is not None:
            try:
                observation.failed(name, error)
            except Exception:
                pass
        raise
    finally:
        if entered:
            try:
                observation.end(name, started)
            except Exception:
                pass


def measured(name):
    def decorate(function):
        @wraps(function)
        def observe(*args, **kwargs):
            with stage(name):
                return function(*args, **kwargs)
        return observe
    return decorate


def response_status(status):
    try:
        observation = _current.get()
        if observation is not None and type(status) is int and 100 <= status <= 599:
            observation.status = status
    except Exception:
        pass


@contextmanager
def observe_request(snapshot):
    observation, token = None, None
    try:
        observation = StageObservation(snapshot.purpose, snapshot.fingerprint)
    except Exception:
        observation = None
    try:
        # A failed observer must not make this request inherit its parent's
        # measurements, including when a streaming caller nests requests.
        token = _current.set(observation)
    except Exception:
        observation = None
    outcome = "ok"
    try:
        yield
    except BaseException as error:
        try:
            outcome = observation.failure[1] if observation and observation.failure else _outcome(error)
        except Exception:
            outcome = "error"
        raise
    finally:
        if token is not None:
            try:
                _current.reset(token)
            except Exception:
                pass
        if observation is not None:
            try:
                if outcome == "ok" and observation.status is not None and observation.status >= 400:
                    outcome = "http_error"
                level = logging.WARNING if outcome != "ok" else logging.INFO if observation.purpose == "api" else logging.DEBUG
                if _logger.isEnabledFor(level):
                    _logger.log(level, "network stages %s", json.dumps(observation.summary(outcome), separators=(",", ":")))
            except Exception:
                # Observations must not replace successful I/O or its error.
                pass
