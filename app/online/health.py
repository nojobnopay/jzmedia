"""Bounded observations of actual online reads; no probes or media processing.

This process-local cache records semantic catalogue results and completed
bounded gateway reads. Configuration changes and age invalidate observations;
neither a successful manifest nor a byte range establishes device decoding.
"""

from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
import json
import threading
import time

from ..network.models import PURPOSES


MAX_RECORDS = 512
MAX_AGE_SECONDS = 1800
_current = ContextVar("online_health_observation", default=None)
_CANCELLED = {"operation_cancelled", "request_cancelled", "session_closed", "session_expired",
              "provider_cancelled", "online_create_cancelled", "byte_budget_exhausted",
              "request_limit", "session_limit", "resource_limit", "provider_catalog_busy",
              "online_gateway_stopped"}
_CATEGORIES = {
    "provider_timeout", "network_timeout", "operation_timeout", "target_dns_timeout",
    "provider_network_error", "network_request_failed", "proxy_connection_failed",
    "provider_auth_failed", "provider_rate_limited", "provider_upstream_error", "provider_not_found",
    "invalid_provider_response", "provider_response_limit", "provider_ambiguous_identity",
    "online_upstream_status", "invalid_manifest_response", "unsupported_upstream_content_type",
    "upstream_length_mismatch", "invalid_upstream_range", "upstream_range_mismatch", "upstream_ignored_range",
    "encoded_response_not_supported", "response_byte_limit", "invalid_upstream_headers",
    "invalid_upstream_length", "target_host_not_allowed", "non_public_target_address",
}


def configuration(state, purpose):
    """Derive an opaque route/source version without TLS, decryption or I/O."""
    if purpose not in PURPOSES:
        raise ValueError("unsupported health purpose")
    source = state["source_config"]
    policy = state["source"]["policy"]
    rule = policy[purpose]
    if rule["mode"] == "inherit":
        rule = policy["default"]
    if rule["mode"] == "inherit":
        rule = state["application"]["rule"]
    if rule["mode"] not in {"direct", "proxy"}:
        raise ValueError("invalid health configuration")
    # Include referenced profile material only in the digest. No ciphertext,
    # proxy address, CA path, upstream URL or credential enters a record.
    profiles = {key: {name: value for name, value in profile.items()
                      if name not in {"name", "created_at", "updated_at"}}
                for key, profile in state["profiles"].items()}
    value = {"purpose": purpose,
             "source": {key: source[key] for key in
                        ("id", "provider", "api_base_url", "allowed_hosts", "provider_options", "enabled")},
             "application": state["application"], "network": state["source"], "profiles": profiles}
    digest = hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return digest, rule["mode"]


@dataclass(frozen=True)
class ReadContext:
    source_id: str
    purpose: str
    configuration_fingerprint: str
    mode: str


@dataclass
class Observation:
    stage: str
    started: float
    context: ReadContext | None = None


class HealthRegistry:
    def __init__(self, *, capacity=MAX_RECORDS, ttl=MAX_AGE_SECONDS, clock=time.monotonic, wall_clock=time.time):
        self._capacity, self._ttl = capacity, ttl
        self._clock, self._wall_clock = clock, wall_clock
        self._records = OrderedDict()
        self._lock = threading.Lock()

    def finish(self, observation, error=None):
        if observation is None or observation.context is None:
            return
        context = observation.context
        category = getattr(error, "category", None)
        if category in _CANCELLED or error is not None and str(error) in _CANCELLED:
            return
        category = category if category in _CATEGORIES else "online_read_failed"
        now = self._clock()
        record = {"state": "success" if error is None else "failure", "mode": context.mode,
                  "configuration_fingerprint": context.configuration_fingerprint,
                  "observed_at": int(self._wall_clock()), "stage": observation.stage,
                  "category": None if error is None else category,
                  "latency_ms": round(max(0, now - observation.started) * 1000, 1),
                  "_started": observation.started, "_observed": now}
        key = (context.source_id, context.purpose)
        with self._lock:
            previous = self._records.get(key)
            if previous is not None and previous["_started"] > observation.started:
                return
            self._records[key] = record
            self._records.move_to_end(key)
            while len(self._records) > self._capacity:
                self._records.popitem(last=False)

    def summary(self, state, purpose):
        fingerprint, mode = configuration(state, purpose)
        source = state["source_config"]
        result = {"state": "unknown", "mode": mode, "configuration_fingerprint": fingerprint,
                  "observed_at": None, "age_seconds": None, "stage": None, "category": None,
                  "latency_ms": None, "reason": "not_observed"}
        if not state["online_enabled"] or not source["enabled"]:
            return {**result, "state": "disabled", "reason": "disabled"}
        if purpose == "api" and source["provider"] == "open_catalog":
            return {**result, "state": "local", "reason": "local_catalogue"}
        with self._lock:
            raw = self._records.get((source["id"], purpose))
            record = dict(raw) if raw is not None else None
        if record is None:
            return result
        if record["configuration_fingerprint"] != fingerprint:
            return {**result, "reason": "configuration_changed"}
        age = max(0, self._clock() - record["_observed"])
        public = {key: value for key, value in record.items() if not key.startswith("_")}
        return {**public, "state": "stale" if age > self._ttl else record["state"],
                "age_seconds": round(age, 1), "reason": "observation_expired" if age > self._ttl else "observed"}

    def clear(self):
        with self._lock:
            self._records.clear()


registry = HealthRegistry()


def finish_observation(observation, error=None):
    """Keep optional health bookkeeping outside the request's error contract."""
    try:
        registry.finish(observation, error)
    except Exception:
        # An observation must never replace successful bytes or the original
        # upstream error. Do not log exceptions that may contain source secrets.
        pass


def bind_client(client, state):
    """Attach the exact configuration used to create this independent client."""
    try:
        fingerprint, mode = configuration(state, client.snapshot.purpose)
    except (KeyError, TypeError, ValueError):
        # Observability must not change a client's playback/authentication
        # contract, including callers using deliberately partial test doubles.
        return
    context = ReadContext(client.snapshot.source_id, client.snapshot.purpose, fingerprint, mode)
    client._online_health_context = context
    observation = _current.get()
    if observation is not None and context.purpose == "api":
        observation.context = context


@contextmanager
def observe_catalogue():
    observation = Observation("catalogue", time.monotonic())
    token = _current.set(observation)
    try:
        yield
    except Exception as error:
        finish_observation(observation, error)
        raise
    else:
        finish_observation(observation)
    finally:
        _current.reset(token)


def begin_media(client, stage):
    context = getattr(client, "_online_health_context", None)
    return Observation(stage, time.monotonic(), context) if isinstance(context, ReadContext) else None
