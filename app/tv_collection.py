"""Web-only official TV inventory projected onto local media, never a media write.

Official numbering and local numbering may differ. Keep the actual local source
with every match, and never manufacture a playable episode from an official row.
"""
import json
from collections import defaultdict
from datetime import date, datetime, timezone

from . import store, tv_airing
from .store._base import _conn, _lock


def _int(value, default=0):
    try:
        return int(default if value is None or value == "" else value)
    except (TypeError, ValueError):
        return default


def air_state(value, today=None):
    try:
        aired = date.fromisoformat(str(value or ""))
    except ValueError:
        return "unknown"
    return "upcoming" if aired > (today or datetime.now(timezone.utc).date()) else "aired"


def status_text(value):
    return {"Returning Series": "连载中", "Continuing": "连载中", "Running": "连载中",
            "In Production": "制作中", "Planned": "计划中", "Pilot": "试播",
            "Ended": "已完结", "Canceled": "已取消", "Cancelled": "已取消"}.get(
                str(value or ""), "播出状态未知")


def _source(row):
    return {"show_id": row["show_id"], "library_id": row["library_id"],
            "library_name": row["library_name"], "season": _int(row["season"]),
            "episode": _int(row["episode"]), "episode_id": row["id"]}


def _span(row):
    start = _int(row.get("episode"))
    end = max(start, _int(row.get("episode_end")))
    # Corrupt ranges must not allocate unbounded memory on a read endpoint.
    return range(start, end + 1) if start > 0 and end - start <= 10000 else ()


class Collection:
    def __init__(self, show_id, extra_catalog=None, extra_episodes=()):
        self.show = store.get_show_meta(show_id)
        if not self.show:
            raise LookupError("show not found")
        lib = store.get_library(self.show["library_id"]) or {}
        self.media_id = _int(lib.get("media_library_id"))
        self.tid = _int(self.show.get("tmdb_id"))
        self.confirmed = bool(self.tid and not self.show.get("needs_review"))
        self.snapshot = tv_airing.get_snapshot(self.tid) if self.confirmed else {}
        self.snapshot = self.snapshot or {}
        self.detail = self.snapshot.get("detail") or {}
        if not self.detail and self.confirmed:
            cached = store.get_tmdb_cached(self.tid, "tv") or {}
            try:
                self.detail = json.loads(cached.get("payload_json") or "{}")
            except (TypeError, ValueError):
                self.detail = {}
            if not isinstance(self.detail, dict):
                self.detail = {}
        with _lock, _conn() as c:
            if self.confirmed:
                self.shows = [dict(r) for r in c.execute(
                    "SELECT s.id, s.library_id, l.name AS library_name FROM tv_shows s"
                    " JOIN libraries l ON l.id=s.library_id"
                    " JOIN media_libraries m ON m.id=l.media_library_id"
                    " WHERE l.media_library_id=? AND s.tmdb_id=? AND s.needs_review=0"
                    " AND l.enabled=1 AND m.enabled=1 AND l.kind='tv' ORDER BY s.id",
                    (self.media_id, self.tid))]
            else:
                self.shows = [{"id": self.show["id"], "library_id": self.show["library_id"],
                               "library_name": lib.get("name") or ""}]
            ids = [r["id"] for r in self.shows]
            if ids:
                ph = ",".join("?" for _ in ids)
                self.rows = [dict(r) for r in c.execute(
                    "SELECT e.*, l.name AS library_name FROM tv_episodes e"
                    " JOIN libraries l ON l.id=e.library_id"
                    f" WHERE e.show_id IN ({ph}) ORDER BY e.show_id,e.id", ids)]
                self.local_seasons = [dict(r) for r in c.execute(
                    f"SELECT * FROM tv_seasons WHERE show_id IN ({ph}) ORDER BY show_id", ids)]
            else:
                self.rows, self.local_seasons = [], []
        self.catalogs = store.get_tmdb_cache_seasons(self.tid) if self.confirmed else {}
        if self.confirmed:
            self.catalogs.update(tv_airing.get_cached_catalogs(self.tid))
        if extra_catalog:
            self.catalogs[_int(extra_catalog.get("season_number"))] = extra_catalog
        self.official = {}
        self.by_id = {}
        self.by_coord = {}
        for s in self.detail.get("seasons") or []:
            sn = _int(s.get("season_number"), -1)
            if sn >= 0:
                self.official[sn] = s
        for ep in extra_episodes:
            self._index({"id": ep.get("tmdb_episode_id"), "season_number": ep.get("season"),
                         "episode_number": ep.get("episode"), "air_date": ep.get("air_date")})
        # Events supplement cold caches; current official records take precedence.
        for sn, catalog in self.catalogs.items():
            for ep in catalog.get("episodes") or []:
                self._index(ep, sn)
        for ep in (self.detail.get("last_episode_to_air"), self.detail.get("next_episode_to_air")):
            if ep:
                self._index(ep)
        self.matches = defaultdict(list)
        self.id_sources = defaultdict(list)
        self.uncertain = defaultdict(set)
        self.uncertain_all = set() if self.confirmed else {"show_unconfirmed"}
        self.local_counts = defaultdict(set)
        self._match()

    def _index(self, ep, sn=None):
        coord = (_int(ep.get("season_number"), sn if sn is not None else -1),
                 _int(ep.get("episode_number")))
        if coord[0] < 0 or coord[1] <= 0:
            return
        self.by_coord[coord] = ep
        if _int(ep.get("id")):
            self.by_id[_int(ep["id"])] = coord

    def _add(self, coord, row):
        source = _source(row)
        if source not in self.matches[coord]:
            self.matches[coord].append(source)

    def _reason(self, sn):
        reasons = self.uncertain_all | self.uncertain.get(sn, set())
        return next((reason for reason in (
            "show_unconfirmed", "match_review", "numbering_unresolved",
            "catalog_missing", "coverage_incomplete")
            if reason in reasons), "")

    def _match(self):
        for e in self.rows:
            if e.get("missing"):
                continue
            sn, en = _int(e.get("season")), _int(e.get("episode"))
            span = _span(e)
            if _int(e["show_id"]) == _int(self.show["id"]):
                self.local_counts[sn].update(span)
            if not self.confirmed or e.get("local_only"):
                continue
            tid = _int(e.get("tmdb_episode_id"))
            coord = self.by_id.get(tid)
            if e.get("binding_conflict") or e.get("needs_review"):
                # An explicit local review flag belongs to the affected seasons,
                # not every season of the show. Unknown identity coordinates only
                # make the rest of the inventory incomplete, not manually suspect.
                self.uncertain[sn].add("match_review")
                if coord:
                    self.uncertain[coord[0]].add("match_review")
                    if len(span) > 1 and coord != (sn, en):
                        self.uncertain_all.add("coverage_incomplete")
                elif tid:
                    self.uncertain_all.add("catalog_missing")
                elif e.get("absolute_number") or e.get("match_source") == "manual":
                    self.uncertain_all.add("coverage_incomplete")
                continue
            if tid:
                # A confirmed identity survives cache eviction. Its local S/E is
                # still not evidence of its official S/E (automatic matches can
                # also use cross-season or continuous-numbering fallbacks).
                self.id_sources[tid].append(_source(e))
            if coord:
                self._add(coord, e)
                if len(span) > 1:
                    if coord == (sn, en) and all((sn, n) in self.by_coord for n in span):
                        for n in span:
                            self._add((sn, n), e)
                    else:
                        # A remapped range may cross an official season boundary.
                        if coord == (sn, en) and "episodes" not in self.catalogs.get(sn, {}):
                            self.uncertain_all.add("catalog_missing")
                        else:
                            self.uncertain[sn].add("numbering_unresolved")
                            self.uncertain[coord[0]].add("numbering_unresolved")
                            self.uncertain_all.add("coverage_incomplete")
                continue
            if tid:
                # An ID without its official coordinates could belong to any season.
                self.uncertain_all.add("catalog_missing")
                continue
            if e.get("absolute_number") or e.get("match_source") == "manual":
                self.uncertain[sn].add("numbering_unresolved")
                self.uncertain_all.add("coverage_incomplete")
                continue
            if self.confirmed and sn in self.official and span:
                # Plain SxxEyy numbering, no conflicting match or absolute numbering.
                catalog = self.catalogs.get(sn) or {}
                known = {_int(ep.get("episode_number")) for ep in catalog.get("episodes") or []}
                total = _int(self.official[sn].get("episode_count"))
                if (("episodes" in catalog and any(n not in known for n in span))
                        or (total > 0 and any(n > total for n in span))):
                    self.uncertain[sn].add("numbering_unresolved")
                    continue
                for n in span:
                    self._add((sn, n), e)
            else:
                self.uncertain[sn].add("numbering_unresolved" if self.official
                                       else "catalog_missing")

    def episode_state(self, season, episode, episode_id=None):
        state, sources, _ = self._episode_result(season, episode, episode_id)
        return state, sources

    def _episode_result(self, season, episode, episode_id=None):
        sources = self.id_sources.get(_int(episode_id), [])
        if sources:
            return "collected", sources, ""
        coord = self.by_id.get(_int(episode_id), (_int(season), _int(episode)))
        sources = self.matches.get(coord, [])
        if sources:
            return "collected", sources, ""
        reason = self._reason(coord[0])
        if reason:
            return "uncertain", [], reason
        return "uncollected", [], ""

    def season_sources(self, sn):
        groups = {}
        for (season, ep), sources in self.matches.items():
            if season != sn:
                continue
            for src in sources:
                key = (src["show_id"], src["season"])
                if key not in groups:
                    groups[key] = ({k: src[k] for k in (
                        "show_id", "library_id", "library_name", "season")}, set())
                groups[key][1].add(ep)
        return [{**src, "count": len(eps)} for src, eps in groups.values()]

    def season_cards(self):
        local = {}
        for s in self.local_seasons:
            sn = _int(s["season"])
            if sn not in local or s["show_id"] == self.show["id"]:
                local[sn] = s
        nums = set(local) | set(self.official) | set(self.local_counts)
        out = []
        for sn in sorted(nums):
            off, loc = self.official.get(sn, {}), local.get(sn, {})
            count = sum(1 for key in self.matches if key[0] == sn)
            reason = self._reason(sn)
            uncertain = bool(reason)
            state = "uncertain" if uncertain else "collected" if count else "uncollected"
            # Unofficial local seasons stay accessible, without an official denominator.
            if sn not in self.official and not uncertain:
                state = "collected" if self.local_counts[sn] else "uncertain"
                if state == "uncertain":
                    reason, uncertain = "catalog_missing", True
            local_poster = loc.get("poster_path") or ""
            poster_url = (f"/api/tv/shows/{self.show['id']}/seasons/{sn}/poster"
                          f"?tmdb_id={self.tid}" if self.confirmed and off.get("poster_path") else "")
            aired = off.get("air_date") if "air_date" in off else loc.get("air_date")
            out.append({"season": sn, "name": ("特别篇（SP）" if sn == 0 else
                        off.get("name") or loc.get("name") or f"第 {sn} 季"),
                        "overview": off.get("overview") or loc.get("overview") or "",
                        "air_date": aired or "", "airing_state": air_state(aired),
                        "poster_path": local_poster, "poster_url": poster_url,
                        "official_count": _int(off.get("episode_count")) if off else None,
                        "official": bool(off), "collected_count": count,
                        "local_count": len(self.local_counts[sn]), "collection_state": state,
                        "collection_reason": reason,
                        "uncertain": uncertain, "sources": self.season_sources(sn)})
        return out

    def summary(self):
        seasons = self.season_cards()
        latest = self.detail.get("last_episode_to_air") or {}
        latest_payload = None
        if latest:
            sn, ep = _int(latest.get("season_number")), _int(latest.get("episode_number"))
            state, sources, reason = self._episode_result(sn, ep, latest.get("id"))
            latest_payload = {"season": sn, "episode": ep,
                              "title": latest.get("name") or "", "air_date": latest.get("air_date") or "",
                              "airing_state": air_state(latest.get("air_date")),
                              "collection_state": state, "collection_reason": reason,
                              "sources": sources}
        status = self.detail.get("status") or self.show.get("status") or ""
        return {"show_id": self.show["id"], "tmdb_id": self.tid or None,
                "media_library_id": self.media_id, "confirmed": self.confirmed,
                "status": status, "status_text": status_text(status),
                "checked_at": self.snapshot.get("checked_at") or 0,
                "next_check_at": self.snapshot.get("next_check_at") or 0,
                "error": self.snapshot.get("error") or "",
                "seasons": seasons, "latest_episode": latest_payload,
                "missing_seasons": [s["season"] for s in seasons if s["season"] > 0
                                    and s["official"] and s["airing_state"] == "aired"
                                    and s["collection_state"] == "uncollected"]}


def catalog_result(show_id, sn, cached):
    detail = dict(cached.get("detail") or {})
    detail["season_number"] = sn
    ctx = Collection(show_id, extra_catalog=detail)
    card = next((s for s in ctx.season_cards() if s["season"] == sn), {})
    items = []
    for ep in detail.get("episodes") or []:
        en = _int(ep.get("episode_number"))
        if en <= 0:
            continue
        state, sources, reason = ctx._episode_result(sn, en, ep.get("id"))
        items.append({"tmdb_episode_id": ep.get("id"), "season": sn, "episode": en,
                      "title": ep.get("name") or "", "overview": ep.get("overview") or "",
                      "air_date": ep.get("air_date") or "", "airing_state": air_state(ep.get("air_date")),
                      "collection_state": state, "collection_reason": reason,
                      "sources": sources})
    return {**card, "show_id": show_id, "tmdb_id": ctx.tid, "season": sn,
            "name": card.get("name") or detail.get("name") or f"第 {sn} 季",
            "overview": detail.get("overview") or card.get("overview") or "",
            "items": items, "checked_at": cached.get("checked_at") or 0,
            "stale": bool(cached.get("stale")), "refreshing": bool(cached.get("refreshing")),
            "error": cached.get("error") or ""}


def updates(media_library_id):
    with _lock, _conn() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT s.id,s.tmdb_id,s.title,s.year,s.poster_path FROM tv_shows s"
            " JOIN libraries l ON l.id=s.library_id JOIN media_libraries m ON m.id=l.media_library_id"
            " WHERE l.media_library_id=? AND l.enabled=1 AND m.enabled=1 AND l.kind='tv'"
            " AND s.needs_review=0 AND s.tmdb_id>0 ORDER BY s.id", (media_library_id,))]
    by_id = {}
    for r in rows:
        by_id.setdefault(r["tmdb_id"], r)
    events = tv_airing.list_events(list(by_id)) if by_id else []
    grouped = defaultdict(list)
    for event in events:
        if _int(event.get("season")) > 0 and air_state(event.get("air_date")) == "aired":
            grouped[event["tmdb_id"]].append(event)
    items, checked = [], []
    for tid, row in by_id.items():
        ctx = Collection(row["id"], extra_episodes=grouped[tid])
        checked.append(ctx.snapshot.get("checked_at") or 0)
        missing = []
        for ev in grouped[tid]:
            state, _ = ctx.episode_state(ev["season"], ev["episode"], ev.get("tmdb_episode_id"))
            if state == "uncollected":
                missing.append(ev)
        if missing:
            missing.sort(key=lambda e: (e.get("air_date") or "", e["season"], e["episode"]), reverse=True)
            items.append({"show_id": row["id"], "tmdb_id": tid, "title": row["title"],
                          "year": row["year"], "poster_path": row["poster_path"],
                          "events": missing, "latest_air_date": missing[0]["air_date"]})
    items.sort(key=lambda i: (i["latest_air_date"], i["tmdb_id"]), reverse=True)
    return {"items": items, "checked_at": max(checked, default=0), "error": ""}
