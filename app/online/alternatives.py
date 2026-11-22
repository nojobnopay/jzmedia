"""Compare bounded media samples for explicitly matched online playback identities."""

from dataclasses import dataclass, field, replace
import re
import time

from pydantic import BaseModel

from ..network.guard import NetworkOperationError, child_operation
from ..network.policy import NetworkPolicyError
from ..store import online, online_network
from . import catalog, diagnostic_probes, discovery_hints, discovery_media, liangzi, maccms, recommendations, related_matching, service
from .models import OnlinePlaybackIdentity
from .providers import PlaybackMatch, ProviderError


DEADLINE_SECONDS = 20
SOURCE_SECONDS = 4
MAX_OTHER_SOURCES = 3
MAX_LINES = 2
_SOFT_FAILURES = (ProviderError, diagnostic_probes.ProbeFailure, NetworkOperationError, NetworkPolicyError)
_CANDIDATE_FAILURES = (*_SOFT_FAILURES, catalog.CatalogError, online.OnlineNotFound)
_EPISODE = re.compile(r"(?:第([0-9]{1,4})[集话話]|(?:ep(?:isode)?[.\-]?)?([0-9]{1,4}))")
_FILM_LABELS = {"正片", "完整影片", "hd", "高清", "1080p", "720p", "蓝光", "4k"}


class Alternative(BaseModel):
    identity: OnlinePlaybackIdentity
    external_id: str
    title: str
    source_name: str
    line_label: str
    entry_label: str
    latency_ms: int
    sample_bytes: int
    throughput_kbps: float | None


class Alternatives(BaseModel):
    items: list[Alternative]
    partial: bool


def _label_key(kind, label):
    value = related_matching.normalize(label)
    if kind == "movie" and value in _FILM_LABELS:
        return ("movie",)
    match = _EPISODE.fullmatch(value) if kind == "episode" else None
    if match:
        return ("episode", int(match[1] or match[2]))
    return ("label", value)


def _configuration(access, source_id):
    service._current_access(access)
    state = online_network.load_online_network_state(source_id)
    if not state["online_enabled"] or not state["source_config"] or not state["source_config"]["enabled"]:
        raise catalog.CatalogError("online_source_disabled", 503)
    return state


def _confirm(access, state):
    if _configuration(access, state["source_config"]["id"]) != state:
        raise catalog.CatalogError("online_source_changed", 409)


def _current_entry(detail, identity, episode):
    lines = [line for line in detail.lines if identity.line_id in (None, line.id)]
    if identity.kind == "movie" and len(lines) != 1:
        return None
    possible = [(line.id, entry) for line in lines for entry in line.entries
                if identity.kind == "movie" or maccms.episode_external_id(line.name, entry.label) == episode["external_id"]]
    return possible[0] if len(possible) == 1 else None


def _active_match(access, identity, configuration):
    # A stalled origin may also have lost its API. Keep the verified labels
    # available for exactly the lifetime and viewer of the playing session.
    match, path = None, None
    for session in service.registry.sessions():
        if session.context != access or session.identity != identity or not session.playback_match:
            continue
        if session.playback_configuration not in (None, configuration):
            raise catalog.CatalogError("online_recommendation_changed", 409)
        if match and not match.same_edition(session.playback_match):
            # Two tabs can have the same database ID but different editions if
            # the provider reused an ID between their respective start times.
            raise catalog.CatalogError("online_recommendation_changed", 409)
        match, path = session.playback_match, recommendations.path_digest(session.entry.url)
    return match, path


def _candidate_detail(source, match, kind, guard):
    adapter = catalog._adapter(source)
    root, name, season = related_matching.title_parts(match.title)
    if not re.fullmatch(r"[1-9][0-9]{3}", match.year) or not root or len(root) > 100:
        return None
    guard()
    rows = adapter.search(source, root, 1)["items"]
    choices = [row for row in rows
               if related_matching.title_parts(row["title"])[1:] == (name, season)
               and row["kind"] in ("unknown", kind) and row.get("year") in (None, "", match.year)]
    if len(choices) != 1:
        return None
    guard()
    detail = adapter.detail(source, choices[0]["external_id"])
    if detail.kind == kind and match.same_programme(PlaybackMatch.from_detail(detail, match.label)):
        return detail
    return None


def _lines(detail, identity, match, current_source):
    key = _label_key(identity.kind, match.label)
    offset = 0
    for line in detail.lines:
        start, offset = offset, offset + len(line.entries)
        if current_source and identity.line_id == line.id:
            continue
        entries = [(start + i, entry) for i, entry in enumerate(line.entries)
                   if _label_key(identity.kind, entry.label) == key]
        if len(entries) == 1:
            sort_order, entry = entries[0]
            if entry.transport and not entry.reason:
                yield replace(line, entries=(entry,)), entry, sort_order


@dataclass(frozen=True)
class Measurement:
    rank: float
    row: dict
    match: PlaybackMatch
    configuration: str


@dataclass
class Comparison:
    identity: OnlinePlaybackIdentity
    origin: dict
    item: dict
    episode: dict | None
    sources: tuple
    match: PlaybackMatch | None
    original_path: bytes | None
    partial: bool
    require_rate: bool = False
    source_index: int = 0
    state: dict | None = None
    detail: maccms.Detail | None = None
    lines: tuple = ()
    seen: set = field(default_factory=set)
    results: tuple = ()

    @classmethod
    def start(cls, access, identity, *, incremental=False):
        origin = _configuration(access, identity.source_id)
        service._identity_ready(identity)
        item = online.get_online_item(identity.item_id)
        episode = online.get_online_episode(identity.episode_id) if identity.episode_id else None
        configuration = online_network.online_network_fingerprint(origin)
        match, path = _active_match(access, identity, configuration)
        if incremental and match is None:
            match, path = recommendations.last_origin(access, identity, configuration)
        others = discovery_hints.prioritize_measured([
            source for source in online.list_online_sources() if source["enabled"] and source["id"] != identity.source_id])
        sources = (identity.source_id, *(s["id"] for s in others[:MAX_OTHER_SOURCES]))
        comparison = cls(identity, origin, item, episode, sources, match, path, len(others) > MAX_OTHER_SOURCES, require_rate=incremental)
        if path:
            try:
                policy = diagnostic_probes.snapshot(origin, "media")
                comparison.seen.add((policy.mode, policy.profile_id, path))
            except NetworkPolicyError:
                comparison.partial = True
        return comparison

    @property
    def done(self):
        return self.source_index >= len(self.sources)

    def fork(self):
        # A preempted advance must retry the same line. Shared parsed details,
        # configurations and already-published measurements remain immutable.
        return replace(self, seen=self.seen.copy())

    def _next_source(self):
        self.source_index += 1
        self.state = self.detail = None
        self.lines = ()

    def _origin_detail(self, source):
        detail = catalog._adapter(source).detail(source, self.item["external_id"])
        selection = _current_entry(detail, self.identity, self.episode)
        if (detail.kind != self.item["kind"] or selection is None or self.item["title"]
                and related_matching.normalize(detail.title) != related_matching.normalize(self.item["title"])):
            raise ProviderError("provider_not_found")
        line_id, entry = selection
        fresh = PlaybackMatch.from_detail(detail, entry.label)
        if self.match and not self.match.same_edition(fresh):
            raise ProviderError("provider_not_found")
        self.match = fresh
        self.original_path = self.original_path or recommendations.path_digest(entry.url)
        self.identity = self.identity.model_copy(update={"line_id": line_id})
        return detail

    def _load(self, access):
        source_id = self.sources[self.source_index]
        current = self.source_index == 0
        self.state = self.origin if current else _configuration(access, source_id)
        source = self.state["source_config"]
        if current:
            self.detail = self._origin_detail(source)
        elif self.match is not None:
            self.detail = _candidate_detail(source, self.match, self.item["kind"], lambda: self.guard(access))
        if self.detail is None:
            return
        policy = diagnostic_probes.snapshot(self.state, "media")
        if current and self.original_path:
            self.seen.add((policy.mode, policy.profile_id, self.original_path))
        possible = []
        for line, entry, sort_order in _lines(self.detail, self.identity, self.match, current):
            path = (policy.mode, policy.profile_id, recommendations.path_digest(entry.url))
            if path in self.seen or path in {candidate[0] for candidate in possible}:
                continue
            if len(possible) >= MAX_LINES:
                self.partial = True
                break
            possible.append((path, line, entry, sort_order))
        # A paused client retains only the two selected URLs across advances,
        # never every episode from a large provider detail response.
        self.lines = tuple(possible)
        self.detail = replace(self.detail, lines=(), description="", poster_url="")

    def guard(self, access):
        _confirm(access, self.origin)
        if self.state is not None and self.state is not self.origin:
            _confirm(access, self.state)

    def _measure_line(self, access, path, line, entry, sort_order):
        source = self.state["source_config"]
        policy = diagnostic_probes.snapshot(self.state, "media")
        if entry.resolver == "liangzi_page":
            page = liangzi.resolve(source, entry.url)
            self.guard(access)
            if page.media_policy_fingerprint != policy.fingerprint:
                raise catalog.CatalogError("online_source_changed", 409)
            entry = replace(entry, url=page.url, resolver="direct")
            path = (policy.mode, policy.profile_id, recommendations.path_digest(entry.url))
        if path in self.seen:
            return
        with diagnostic_probes.guarded_reads(lambda: self.guard(access)):
            metrics = discovery_media.sample_media(source, entry, policy)
        self.guard(access)
        self.seen.add(path)
        ratio = metrics.get("realtime_ratio")
        if (self.require_rate and not metrics.get("sample_kbps")) or (ratio is not None and ratio < 1):
            return
        saved = catalog._persist(source, self.detail, selected_entry=(sort_order, (line, entry)))
        target = {"source_id": source["id"], "item_id": saved["item"]["id"], "kind": self.identity.kind, "line_id": line.id}
        if self.identity.kind == "episode":
            external = maccms.episode_external_id(line.name, entry.label)
            target["episode_id"] = next(row["id"] for row in saved["episodes"] if row["external_id"] == external)
        row = {"identity": target, "source_name": source["name"], "external_id": self.detail.external_id,
               "title": self.detail.title, "line_label": line.name, "entry_label": entry.label,
               "latency_ms": metrics["media_ms"], "sample_bytes": metrics["sample_bytes"],
               "throughput_kbps": metrics["sample_kbps"]}
        rank = (metrics["sample_kbps"] or 0) * (1.25 if ratio is not None else 1)
        measurement = Measurement(rank, row, PlaybackMatch.from_detail(self.detail, entry.label),
                                  online_network.online_network_fingerprint(self.state))
        self.results += (measurement,)

    def step(self, access, operation):
        """Advance one source or sample at most one playable line, then yield."""
        _confirm(access, self.origin)
        try:
            with child_operation(operation, min(time.monotonic() + SOURCE_SECONDS, operation.deadline - 0.15)):
                if self.state is None:
                    self._load(access)
                self.guard(access)
                if self.lines:
                    chosen = self.lines[0]
                    self.lines = self.lines[1:]
                    self._measure_line(access, *chosen)
        except (catalog.CatalogError, online.OnlineNotFound):
            operation.check()
            self.lines = ()
            self.partial = True
        except _SOFT_FAILURES:
            operation.check()
            self.partial = True
        if not self.lines:
            self._next_source()
            if self.match is None:
                self.source_index = len(self.sources)
        _confirm(access, self.origin)

    def ranked(self, access):
        _confirm(access, self.origin)
        results = []
        versions = {}
        for candidate in self.results:
            sid = candidate.row["identity"]["source_id"]
            if sid not in versions:
                try:
                    versions[sid] = online_network.online_network_fingerprint(_configuration(access, sid))
                except (catalog.CatalogError, online.OnlineNotFound):
                    versions[sid] = None
            if versions[sid] == candidate.configuration:
                results.append(candidate)
            else:
                self.partial = True
        return sorted(results, key=lambda item: (-item.rank, item.row["latency_ms"]))[:3]


def read(access, identity, *, operation):
    """Compatibility response for clients using the original single request."""
    try:
        with catalog._admit(access.owner, operation, background=True):
            comparison = Comparison.start(access, identity)
            while not comparison.done and operation.remaining() > 0.4:
                comparison.step(access, operation)
            rows = [result.row for result in comparison.ranked(access)]
            return {"items": rows, "partial": comparison.partial or not comparison.done}
    except catalog.BackgroundDeferred:
        return {"items": [], "partial": True}
