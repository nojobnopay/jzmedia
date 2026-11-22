"""Short-lived settings measurements used only to order actual episode probes."""

from collections import OrderedDict
from dataclasses import dataclass
import sqlite3
import threading
import time

from ..store import online, online_network
from . import health


_TTL = 30 * 60
_CAPACITY = 32
_lock = threading.Lock()
_records = OrderedDict()


@dataclass(frozen=True)
class Measurement:
    started: float
    rank: tuple | None


def measurement(report, started):
    rate, ratio = report.get("sample_kbps"), report.get("realtime_ratio")
    if report["status"] != "measured" or not rate or ratio is not None and ratio < 1:
        return Measurement(started, None)
    return Measurement(started, (min(ratio or 1, 3), rate, -(report.get("media_ms") or 0)))


def _fingerprint(state):
    # A disabled source is explicitly probed before the user enables it.
    # Toggling that flag doesn't change the endpoint, host list or network path.
    source = {**state["source_config"], "enabled": True}
    return health.configuration({**state, "source_config": source}, "media")[0]


def remember(state, observed):
    sid = state["source_config"]["id"]
    fingerprint = _fingerprint(state)
    with _lock:
        previous = _records.get(sid)
        if previous and observed.started < previous[1].started:
            return
        _records[sid] = (fingerprint, observed)
        _records.move_to_end(sid)
        while len(_records) > _CAPACITY:
            _records.popitem(last=False)


def prioritize_measured(sources):
    """Preserve member identities and existing order when a recent score is unavailable."""
    now = time.monotonic()
    with _lock:
        recent = {sid: (version, observed.rank) for sid, (version, observed) in _records.items()
                  if now - observed.started <= _TTL and observed.rank is not None}
    matched = [source["id"] for source in sources if source["enabled"] and source["id"] in recent]
    if not matched:
        return sources
    ranks = {}
    try:
        with online_network._connection(snapshot=True) as connection:
            for sid in matched:
                try:
                    state = online_network.online_network_snapshot(connection, sid)
                    if (state["online_enabled"] and state["source_config"]["enabled"]
                            and _fingerprint(state) == recent[sid][0]):
                        ranks[sid] = recent[sid][1]
                except (online.OnlineNotFound, KeyError, ValueError, TypeError):
                    continue
    except sqlite3.Error:
        return sources
    return sorted(sources, key=lambda src: ranks.get(src["id"], ()), reverse=True)
