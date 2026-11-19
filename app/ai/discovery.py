"""Ground a natural language request in current visible works and verified catalogue data."""
from datetime import date
import json
import re

from pydantic import ValidationError

from .. import store
from . import discovery_sessions as sessions
from .client import AiUnavailable, call_json, discard_cached, require_config
from .discovery_models import (COMPARE_INSTRUCTION, Comparison, Constraints, DiscoveryPlan, MAX_RESULTS, PLAN_INSTRUCTION, RANK_INSTRUCTION,
                               Selection)

# The database reader can return internal metadata for matching. Only these fields travel to the UI.
_CARD_FIELDS = ("kind", "id", "title", "original_title", "year", "poster_path", "poster_url",
                "detail_path", "tmdb_id", "external_url", "library_id", "media_library_id",
                "rating", "rating_source", "watch_state", "overview", "genres", "tags", "keywords",
                "runtime", "runtime_minutes", "show_id", "season", "episode", "version_id",
                "matched_version_id", "matched_episode_path", "matched_episode_id", "show_title",
                "audio_languages", "subtitle_languages", "episode_end", "absolute_number",
                "vote_count", "release_date")
_FILTER_NAMES = {"genre": "genres", "country": "countries", "region": "regions", "tag": "tags",
                 "year": "years", "min_rating": "min_rating", "rating_source": "rating_source",
                 "status": "status"}
_SOURCE = {"tmdb": "TMDB", "douban": "豆瓣", "custom": "自评"}
# Words that express a structured ranking dimension have no topical meaning by
# themselves. Keep actual content terms such as “公路旅行” and titles such as “1917”.
_RANKING_TERM = re.compile(
    r"(?:(?:tmdb|豆瓣|自评)?(?:评分|分数|口碑)(?:最高|最低|排行|排名|榜|从高到低|从低到高)?"
    r"|(?:最高|最低)(?:分|评分|口碑)|高分|低分|(?:近期|最新|最早)(?:上映|首播|入库|添加)?"
    r"|排行(?:榜)?|排名|前(?:\d+|[一二三四五六七八九十百]+)(?:部|名)?|top\d*"
    r"|今年|本年(?:度)?|去年|\d{4}年?|电影|影片|剧集|电视剧)", re.I)


def _model(task, payload, prompt, cls):
    sessions.verify_access()
    raw = call_json(task, payload, prompt)
    try:
        return cls.model_validate(raw)
    except ValidationError:
        discard_cached(task, payload, prompt)
        raise AiUnavailable("invalid_result", "模型的找片建议未能通过校验，请重试或改写要求。") from None


def _local(*args, **kwargs):
    from ..store.ai_discovery import discover_local
    return discover_local(*args, **kwargs)


def _libraries(body):
    return [r["id"] for r in store.list_libraries(only_enabled=True)
            if r["kind"] in ("movie", "tv")
            and (body.media_library_id is None or r["media_library_id"] == body.media_library_id)
            and (body.library_id is None or r["id"] == body.library_id)]


def _constraints(plan):
    return {k: v for k, v in plan.constraints.model_dump(exclude_none=True).items() if v != []}


def _with_wall(plan, wall):
    """Explicitly inherited wall filters remain effective even if the model omits them."""
    if not wall:
        return {}, []
    draft = wall.model_dump(exclude_unset=True, exclude_none=True)
    c = {_FILTER_NAMES[k]: v for k, v in draft.items() if k in _FILTER_NAMES and v != []}
    if draft.get("watched") is not None:
        c["watch_state"] = "watched" if draft["watched"] else "unfinished"
    if draft.get("decade"):
        years = {x for start in draft["decade"] for x in range(start, start + 10)}
        if c.get("years"):
            years.intersection_update(c["years"])
        # A disjoint selection must remain empty.
        c["years"] = sorted(years) or [-1]
    if "sort" in draft and plan.order == "relevance" and draft["sort"] in ("rating", "year", "added"):
        plan.order = draft["sort"] + "_" + draft.get("order", "desc")
    return c, [draft["q"]] if draft.get("q") else []


def _editable(plan):
    c = _constraints(plan)
    result = {wall: c[field] for wall, field in _FILTER_NAMES.items() if field in c}
    if c.get("watch_state") in ("watched", "unfinished"):
        result["watched"] = int(c["watch_state"] == "watched")
    if plan.order != "relevance":
        result["sort"], result["order"] = plan.order.split("_")
    return result


def _texts(value):
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (int, float)):
        return [str(value)]
    if isinstance(value, list):
        return [v for part in value for v in _texts(part)]
    if isinstance(value, dict):
        return [v for k in ("name", "title", "character", "character_name", "role", "value", "label")
                for v in _texts(value.get(k))]
    return []


def _card(row, source):
    card = {k: row.get(k) for k in _CARD_FIELDS if row.get(k) is not None}
    for key, limit in (("title", 300), ("original_title", 300), ("overview", 1500)):
        if isinstance(card.get(key), str):
            card[key] = card[key][:limit]
    for key in ("genres", "tags", "keywords"):
        if key in card:
            card[key] = [text[:140] for text in _texts(card[key])[:24]]
    kind = card["kind"]
    identity = card.get("tmdb_id") if source == "public" else card.get("id")
    card.update(key=f"{source}-{kind}-{identity}", in_library=source == "library",
                evidence=_texts(row.get("evidence"))[:6], reason="")
    if card.get("matched_episode_path"):
        card["detail_path"] = card["matched_episode_path"]
        card["evidence"].append("链接指向已核实音轨或字幕条件的分集。")
    return card


def _dossier(row):
    facts = {k: "、".join(_texts(row.get(k)))[:bound] for k, bound in
             (("title", 120), ("original_title", 120), ("year", 5), ("overview", 260),
              ("genres", 90), ("tags", 90), ("people", 180), ("characters", 100),
              ("keywords", 120), ("evidence", 120))}
    return {k: v for k, v in facts.items() if v}


def _cited(choice, dossier):
    values = {k: v for k, v in dossier.items() if k != "key"}
    quotes = list(choice.evidence)
    if any(not any(quote in v for v in values.values()) for quote in quotes):
        raise ValueError("unsupported quotation")
    for field in choice.fields:
        if field not in values:
            raise ValueError("unknown evidence field")
        text = values[field]
        if field in {"people", "characters", "genres", "tags", "keywords", "evidence"}:
            names = text.split("、")
            text = next((name for name in names if name in choice.reason), names[0])
        quotes.append(text[:140])
    if not quotes:
        raise ValueError("missing evidence")
    return list(dict.fromkeys(quotes))[:3]


def _select(rows, query, plan, count, warnings):
    if not rows:
        return []
    semantic = bool(plan.preferences or plan.references or plan.intent in ("identify", "recommend"))
    if not semantic:
        for row in rows:
            if plan.order.startswith("rating") and row.get("rating") is not None:
                row["reason"] = f"{_SOURCE.get(row.get('rating_source'), 'TMDB')} 评分 {row['rating']:g} 分"
            elif row.get("evidence"):
                row["reason"] = "；".join(row["evidence"][:2])
        return rows[:count]

    dossiers = []
    for idx, row in enumerate(rows):
        facts = {"key": f"c{idx}", **_dossier(row)}
        # Leave headroom for prompts, references, query and JSON serialization.
        if len(json.dumps(dossiers + [facts], ensure_ascii=False).encode()) > 24000:
            break
        dossiers.append(facts)
    payload = {"query": query, "intent": plan.intent, "preferences": plan.preferences,
               "references": plan.references, "clues": plan.clues.model_dump(), "count": count,
               "candidates": dossiers}
    try:
        selection = _model("discovery_select", payload, RANK_INSTRUCTION, Selection)
        chosen = []
        used = set()
        for choice in selection.choices:
            idx = int(choice.key[1:])
            if idx >= len(dossiers) or choice.key != dossiers[idx]["key"] or idx in used:
                raise ValueError("unknown or duplicate candidate")
            used.add(idx)
            row = rows[idx]
            row["evidence"] = _cited(choice, dossiers[idx])
            reason = choice.reason
            if re.search(r"\bc\d{1,2}\b|\b(?:overview|characters|field[s]?)\b", reason, re.I | re.ASCII):
                reason = ""
            row["reason"] = reason or "；".join(row["evidence"])
            chosen.append(row)
        if plan.order != "relevance":
            order = {r["key"]: i for i, r in enumerate(rows)}
            chosen.sort(key=lambda r: order[r["key"]])
        return chosen[:count]
    except ValueError:
        discard_cached("discovery_select", payload, RANK_INSTRUCTION)
        failure = AiUnavailable("invalid_result", "模型引用了无法核实的候选或依据，请重试。")
    except AiUnavailable as exc:
        failure = exc
    warnings.append(failure.message)
    # Degrade to verified title matches only; an arbitrary short-list is not a recommendation.
    titles = {name.casefold() for name in plan.clues.titles}
    verified = [r for r in rows if plan.intent == "identify" and titles.intersection(
        t.casefold() for t in (r.get("title") or "", r.get("original_title") or ""))]
    for row in verified:
        row["reason"] = "片名与查找线索一致，其他线索尚待核对。"
    if not verified and plan.intent != "identify":
        for row in rows:
            if row["evidence"] or (plan.order.startswith("rating") and row.get("rating") is not None):
                row["reason"] = "作品资料已核实，推荐理由尚未完成核对。"
                verified.append(row)
    if not verified:
        raise failure
    return verified[:count]


def _public_links(rows, libraries, state=None):
    for kind in {row["kind"] for row in rows}:
        ids = list({row["tmdb_id"] for row in rows if row["kind"] == kind and row.get("tmdb_id")})
        if not ids or not libraries:
            continue
        found = _local(libraries, kinds=[kind], constraints={"tmdb_ids": ids},
                       limit=max(100, len(ids)))["items"]
        # A work may have multiple local releases. Prefer a visible copy satisfying the requested state.
        match = {}
        for item in found:
            tid = item.get("tmdb_id")
            if tid not in match or (state and item.get("watch_state") == state):
                match[tid] = item
        for row in rows:
            local = match.get(row.get("tmdb_id")) if row["kind"] == kind else None
            if local:
                row.update(in_library=True, **{k: local[k] for k in ("detail_path", "id", "version_id",
                           "library_id", "media_library_id", "watch_state") if k in local})


def _known_state(state, actual):
    return not state or actual == state or (state == "unfinished" and actual in ("unseen", "in_progress"))


def _refresh_cards(cards, libraries, source, hard, plan):
    if source == "public":
        rows = [{k: v for k, v in card.items() if k not in
                 ("watch_state", "id", "version_id", "detail_path", "library_id", "media_library_id")}
                for card in cards]
        for row in rows:
            row.update(in_library=False, watch_state="unknown")
        _public_links(rows, libraries, hard.get("watch_state"))
        return [r for r in rows if _known_state(hard.get("watch_state"), r.get("watch_state"))]
    fresh = {}
    for kind in sorted({row["kind"] for row in cards}):
        ids = [row["id"] for row in cards if row["kind"] == kind]
        query = {**hard, "ids": ids}
        found = _local(libraries, kinds=[kind], constraints=query, limit=len(ids),
                       clues=plan.clues.model_dump(), references=plan.references, explore=True,
                       order=plan.order if plan.order.startswith("rating") else "relevance")
        for row in found["items"]:
            card = _card(row, source)
            fresh[(kind, row["id"])] = (card, _dossier(row))
    result = []
    for old in cards:
        current = fresh.get((old["kind"], old["id"]))
        if current:
            row, facts = current
            if old.get("evidence") and all(any(e in v for v in facts.values())
                                           for e in old["evidence"]):
                row.update(reason=old.get("reason", ""), evidence=old["evidence"])
            elif old.get("rating") == row.get("rating") and old.get("reason", "").startswith(
                    _SOURCE.get(row.get("rating_source"), "TMDB") + " 评分"):
                row["reason"] = old["reason"]
            elif row.get("evidence"):
                row["reason"] = "；".join(row["evidence"][:2])
            result.append(row)
    return result


def _generate(plan, source, query, libraries, count, hard, excluded, today, warnings):
    pool_size = (min(60, max(24, count * 4)) if
                 plan.preferences or plan.references or plan.intent in ("recommend", "identify")
                 else count)
    clues = plan.clues.model_dump()
    ordered = plan.order != "relevance"
    if ordered and plan.preferences and not any(clues.values()):
        clues["terms"] = plan.preferences
    if source == "public":
        from .discovery_public import discover_public
        # Public discovery cannot verify a local keyword search or independent, conflicting
        # filter dimensions. Explain these requests instead of broadening the result.
        wall = hard.get("_wall") or {}
        conflicts = any(hard.get(k, v) != v for k, v in wall.items())
        if conflicts or hard.get("_wall_q"):
            warnings.append("公开片目无法同时核实沿用的片库条件与本次要求。请取消沿用筛选或改查当前库。")
            return [], 0, 0, ""
        ext_hard = {k: v for k, v in {**wall, **hard}.items()
                    if k not in ("watch_state", "_wall", "_wall_q")}
        # Fetch enough provider results to remove previously shown works before slicing.
        allow_exploration = bool(plan.intent in ("recommend", "identify") or plan.preferences or plan.references)
        data = discover_public(plan.kinds, ext_hard, clues, plan.order, min(100, pool_size + len(excluded)),
                               today, allow_exploration, plan.references)
    else:
        omitted = {}
        for kind, item_id in excluded:
            omitted.setdefault(kind, []).append(item_id)
        params = {**hard, **({"exclude_ids": omitted} if omitted else {})}
        data = _local(libraries, kinds=plan.kinds, constraints=params, clues=clues,
                      references=plan.references, order=plan.order, limit=pool_size, today=today,
                      explore=not ordered and bool(plan.intent in ("identify", "recommend") or plan.preferences or plan.references))
    warnings.extend(data.get("warnings", []))
    if data.get("clarification"):
        return [], 0, 0, data["clarification"]
    unknown = data.get("excluded_unknown", 0)
    rows = []
    for raw in data["items"]:
        row = _card(raw, source)
        if (row["kind"], row.get("tmdb_id" if source == "public" else "id")) in excluded:
            continue
        if (source == "library" and plan.intent == "search" and any(clues.values()) and
                not plan.preferences and not plan.references and not raw.get("retrieval_score")):
            continue
        row["people"] = _texts(raw.get("people"))[:16]
        row["characters"] = _texts(raw.get("characters"))[:12]
        rows.append(row)
    if source == "public":
        for row in rows:
            row["watch_state"] = "unknown"
        _public_links(rows, libraries, hard.get("watch_state"))
        if hard.get("watch_state"):
            unknown += sum(row["watch_state"] == "unknown" for row in rows)
            rows = [r for r in rows if _known_state(hard["watch_state"], r["watch_state"])]
            warnings.append("观看状态只按当前库的记录核实，未收录作品的观看状态未知。")
    selected = _select(rows, query, plan, count, warnings)
    return [{k: v for k, v in r.items() if k not in ("people", "characters")} for r in selected], data["total"], unknown, ""


def _comparison(items):
    result = []
    for row in items:
        highlights = []
        if row.get("year"):
            highlights.append(f"{row['year']} 年")
        if row.get("rating") is not None:
            highlights.append(f"{_SOURCE.get(row.get('rating_source'), 'TMDB')} {row['rating']:g} 分")
        if row.get("genres"):
            highlights.append("、".join(_texts(row["genres"])))
        description = (row.get("overview") or "")[:180]
        if description:
            highlights.append(description)
        result.append({"title": row["title"], "text": " · ".join(highlights) or "现有资料不足。",
                       "key": row["key"]})
    return result


_COMPARISON_REF = re.compile(r"\bc[0-9]+\b", re.ASCII | re.I)


def _comparison_text(text, names, literals):
    """Render candidate references once, keeping quoted catalogue titles and evidence verbatim."""
    spans = [match.span() for literal in literals if _COMPARISON_REF.search(literal)
             for match in re.finditer(re.escape(literal), text)]

    def resolve(match):
        title = names.get(match[0].lower())
        if any(start <= match.start() and match.end() <= end for start, end in spans):
            # A bare title can itself be another film's candidate key. Only an
            # explicit title quotation identifies which work the model meant.
            if match.span() in spans and title and title != match[0]:
                surrounding = text[max(0, match.start() - 1):match.start()] + text[match.end():match.end() + 1]
                if surrounding not in {"《》", "“”", '""'}:
                    raise ValueError("ambiguous comparison reference")
            return match[0]
        if not title:
            raise ValueError("unknown comparison reference")
        return title

    return _COMPARISON_REF.sub(resolve, text)


def _compare(items, query, warnings):
    candidates = []
    for i, row in enumerate(items):
        facts = _dossier(row)
        for field in ("runtime", "rating", "rating_source"):
            if row.get(field) is not None:
                facts[field] = str(row[field])
        candidates.append({"key": f"c{i}", **facts})
    payload = {"query": query, "candidates": candidates}
    try:
        analysis = _model("discovery_compare", payload, COMPARE_INSTRUCTION, Comparison)
        choices = {entry.key: entry for entry in analysis.entries}
        if len(choices) != len(analysis.entries) or set(choices) != {c["key"] for c in candidates}:
            raise ValueError("invalid comparison")
        names = {facts["key"]: item["title"] for item, facts in zip(items, candidates)}
        literals = {row[field] for row in items for field in ("title", "original_title") if row.get(field)}
        proofs = {facts["key"]: _cited(choices[facts["key"]], facts) for facts in candidates}
        literals.update(quote for evidence in proofs.values() for quote in evidence)
        entries = []
        for item, facts in zip(items, candidates):
            choice = choices[facts["key"]]
            evidence = proofs[facts["key"]]
            text = _comparison_text(choice.reason, names, literals) if choice.reason else "；".join(evidence)
            entries.append({"key": item["key"], "title": item["title"], "text": text, "evidence": evidence})
        return entries, _comparison_text(analysis.conclusion, names, literals) or "按指定维度比较"
    except ValueError:
        discard_cached("discovery_compare", payload, COMPARE_INSTRUCTION)
        warnings.append("模型的比较依据未能核实，以下列出现有资料。")
    except AiUnavailable as exc:
        warnings.append(exc.message)
    return _comparison(items), "按片库现有资料比较"


def _revalidate(items, access, source, hard, overrides, plan):
    # A one-item replacement may have its own stricter criteria. Revalidate each
    # group against the criteria used to select it, then restore list order.
    libraries = _libraries(access)
    groups = {}
    for row in items:
        constraints = overrides.get(row["key"], hard)
        encoded = json.dumps(constraints, sort_keys=True)
        if encoded not in groups:
            groups[encoded] = (constraints, [])
        groups[encoded][1].append(row)
    live = {}
    for constraints, group in groups.values():
        for row in _refresh_cards(group, libraries, source, constraints, plan):
            live[row["key"]] = row
    return [live[r["key"]] for r in items if r["key"] in live]


def _turn_response(result, access, plan, hard, shown, overrides=None):
    sessions.verify_access()
    overrides = overrides or {}
    live = _revalidate(result["items"], access, result["source"], hard, overrides, plan)
    if len(live) != len(result["items"]):
        result["summary"] = "片库状态已更新，本次仅保留仍符合要求的作品。"
        result["status"] = "partial" if live else "empty"
        result["comparison"] = []
    result["items"] = live
    result.update(filters=_editable(plan), warnings=list(dict.fromkeys(result["warnings"])))
    result["session_id"] = sessions.save(access, {
        "plan": plan.model_dump(), "constraints": hard, "overrides": overrides, "response": result.copy(),
        "shown": list(dict.fromkeys(shown))[-450:]})
    return result


def _response(plan, source):
    return {"ok": True, "intent": plan.intent, "source": source, "summary": "", "items": [],
            "status": "results", "warnings": [], "excluded_unknown": 0, "total": 0,
            "clarification": "", "comparison": []}


def _previous_context(previous):
    if not previous:
        return None
    return {"previous_plan": previous["plan"], "previous_source": previous["response"]["source"],
            "clarification": previous["response"].get("clarification", ""),
            "items": [{"position": i + 1, "kind": row["kind"], "title": row["title"][:100]}
                      for i, row in enumerate(previous["response"]["items"])]}


def run(body, access):
    sessions.verify_access()
    previous = sessions.load(body.session_id, access)
    libraries = _libraries(body)
    if (body.library_id and not libraries) or (body.media_library_id and not libraries
            and not store.get_media_library(body.media_library_id)):
        raise AiUnavailable("empty_scope", "当前范围没有可搜索的视频库，请重新选择媒体库。")
    today = date.today()
    context = _previous_context(previous)
    # Revalidate before titles of prior results enter the provider payload.
    if previous:
        last = previous["response"]
        live = _revalidate(last["items"], access, last["source"], previous["constraints"], previous["overrides"],
                           DiscoveryPlan.model_validate(previous["plan"]))
        valid_keys = {r["key"] for r in live}
        context["items"] = [item for item, row in zip(context["items"], last["items"])
                            if row["key"] in valid_keys]
    wall_preview = body.filters.model_dump(exclude_unset=True) if body.filters else {}
    wall_preview = {k: v[:12] if isinstance(v, list) else v for k, v in wall_preview.items()}
    payload = {"query": body.q.strip(), "today": today.isoformat(), "entry_kinds": body.kinds or [body.kind],
               "source": body.source or (previous["response"]["source"] if previous else "library"),
               "current_filters": wall_preview,
               "last_turn": context}
    if body.reuse_plan:
        if not previous or not body.source:
            raise AiUnavailable("invalid_result", "请先生成片单，再选择新的搜索范围。")
        require_config()
        plan = DiscoveryPlan.model_validate(previous["plan"])
        plan.action, plan.positions, plan.source = "refine", [], body.source
    else:
        plan = _model("discovery_plan", payload, PLAN_INSTRUCTION, DiscoveryPlan)
    sessions.verify_access()
    continuing = previous and plan.action != "new"
    old = previous["response"] if continuing else None
    if continuing:
        prior_plan = DiscoveryPlan.model_validate(previous["plan"])
        # Older or smaller models sometimes return only the changed fields on follow-ups.
        for field in ("kinds", "order", "limit", "clues", "preferences", "references"):
            if field not in plan.model_fields_set:
                setattr(plan, field, getattr(prior_plan, field))
        prior = {k: v for k, v in prior_plan.constraints.model_dump(exclude_none=True, exclude_defaults=True).items()
                 if k not in plan.remove_constraints}
        prior.update(plan.constraints.model_dump(exclude_unset=True))
        try:
            plan.constraints = Constraints.model_validate(prior)
        except ValidationError:
            raise AiUnavailable("invalid_result", "后续要求与之前的条件冲突，请明确要调整的条件。") from None
    plan.kinds = list(dict.fromkeys(plan.kinds or body.kinds or [body.kind]))
    if body.kinds:
        requested_kinds = [k for k in plan.kinds if ("tv" if k in ("season", "episode") else k) in body.kinds]
        plan.kinds = requested_kinds or list(body.kinds)
    public_requested = body.source == "public" or (body.allow_query_source and plan.source == "public" and re.search(
        r"公开|全网|全站|全世界|全球|库外|不[只限].*片库|public|worldwide|global", body.q, re.I))
    source = "public" if public_requested else (body.source or (old["source"] if old else "library"))
    # Switching from public results back to library is a fresh result set.
    if old and old["source"] != source:
        old = None
    if plan.intent == "ranking" and plan.order == "relevance":
        plan.order = "rating_desc"
    if plan.order != "relevance":
        plan.clues.terms = [t for t in plan.clues.terms if not _RANKING_TERM.fullmatch(t.replace(" ", ""))]
        plan.preferences = [t for t in plan.preferences if not _RANKING_TERM.fullmatch(t.replace(" ", ""))]
    count = plan.limit or (1 if plan.intent == "identify" else 10 if plan.intent == "ranking" else 6)
    count = min(count, MAX_RESULTS)
    wall_hard, wall_terms = _with_wall(plan, body.filters)
    hard = {**wall_hard, **_constraints(plan)}
    if continuing and not body.filters:
        wall_hard = previous["constraints"].get("_wall", {})
        wall_terms = [previous["constraints"]["_wall_q"]] if previous["constraints"].get("_wall_q") else []
    if wall_hard:
        hard["_wall"] = wall_hard
    if wall_terms:
        hard["_wall_q"] = wall_terms[0]
    if "rating_source" in wall_hard and "rating_source" not in plan.constraints.model_fields_set:
        hard["rating_source"] = wall_hard["rating_source"]
    plan.clues.terms.extend(t for t in wall_terms if t not in plan.clues.terms)
    result = _response(plan, source)
    warnings = result["warnings"]
    shown = previous["shown"] if old else []
    if plan.intent == "identify" and (plan.clues.terms or plan.clues.people) and plan.action in ("new", "refine"):
        # Verify concrete clues before asking about the planner's unverified title guess.
        plan.clarification = ""
    if plan.clarification or plan.unsupported:
        missing = "、".join(plan.unsupported)
        question = plan.clarification or f"目前无法核实“{missing}”。可以调整这项要求吗？"
        result.update(status="clarification", clarification=question, summary=question)
        if old:
            result["items"] = live
        return _turn_response(result, access, plan, hard, shown)
    if plan.action in ("compare", "replace"):
        positions = plan.positions
        valid = bool(old and positions and all(n <= len(old["items"]) for n in positions))
        valid = valid and len(set(positions)) == len(positions) and (
            len(positions) >= 2 if plan.action == "compare" else len(positions) == 1)
        if not valid or (old and len(live) != len(old["items"])):
            message = "请根据当前片单说明要比较或替换的编号。"
            result.update(status="clarification", clarification=message, summary=message,
                          items=live if old else [])
            return _turn_response(result, access, plan, hard, shown)
        if plan.action == "compare":
            rows, summary = _compare([live[n - 1] for n in positions], body.q.strip(), warnings)
            result.update(status="comparison", intent="compare", items=live, total=len(live),
                          summary=summary, comparison=rows)
            return _turn_response(result, access, prior_plan, previous["constraints"], shown, previous["overrides"])
    excluded = set(shown) if old and plan.action in ("more", "replace") else set()
    get_count = 1 if plan.action == "replace" else count
    selected, total, unknown, question = _generate(plan, source, body.q.strip(), libraries, get_count, hard,
                                         excluded, today, warnings)
    if question:
        result.update(status="clarification", clarification=question, summary=question, items=live if old else [])
        return _turn_response(result, access, plan, hard, shown)
    if plan.action == "replace" and old:
        items = list(live)
        if selected:
            items[plan.positions[0] - 1] = selected[0]
        else:
            warnings.append("没有找到合适的替换作品，原片单已保留。")
    else:
        items = selected
    if unknown:
        warnings.append(f"{unknown} 项资料不足，无法核实全部要求。")
    requested = plan.limit
    partial = (any("至少 100 票" not in w for w in warnings)
               or bool(requested and len(selected) < get_count))
    status = ("partial" if partial else "results") if items else "empty"
    scope = "TMDB 公开片目" if source == "public" else "当前媒体库"
    summary = f"{scope} · 共返回 {len(items)} 项"
    if not items:
        summary = f"{scope}中未找到能核实符合要求的作品。"
    elif requested and len(selected) < get_count and plan.action != "replace":
        summary = f"要求 {get_count} 项，{scope}中找到 {len(items)} 项。"
    if plan.intent == "ranking":
        period = "、".join(str(y) for y in (hard.get("years") or []))
        if not period and (hard.get("date_from") or hard.get("date_to")):
            period = f"{hard.get('date_from') or '不限'} 至 {hard.get('date_to') or '今天'}"
        if not period and (hard.get("year_min") or hard.get("year_max")):
            period = f"{hard.get('year_min') or '不限'} 至 {hard.get('year_max') or '今年'}"
        ordering = { "rating_desc": "评分从高到低", "rating_asc": "评分从低到高",
                     "year_desc": "年份从新到旧", "year_asc": "年份从早到晚",
                     "added_desc": "最近入库优先", "added_asc": "最早入库优先" }.get(plan.order)
        label = _SOURCE.get(hard.get("rating_source", "tmdb"), "TMDB") if plan.order.startswith("rating") else ""
        summary += " · " + " · ".join(filter(None, [period, (label + " " + (ordering or "")).strip()]))
    result.update(status=status, summary=summary, items=items, total=total, excluded_unknown=unknown)
    ident = "tmdb_id" if source == "public" else "id"
    shown = [*shown, *((r["kind"], r[ident]) for r in items)]
    if old and plan.action == "replace":
        overrides = dict(previous["overrides"])
        if selected:
            overrides.pop(old["items"][plan.positions[0] - 1]["key"], None)
            overrides[selected[0]["key"]] = hard
        return _turn_response(result, access, prior_plan, previous["constraints"], shown, overrides)
    return _turn_response(result, access, plan, hard, shown)


def refresh(body, access):
    sessions.verify_access()
    previous = sessions.load(body.session_id, access)
    result = previous["response"].copy()
    result["items"] = _revalidate(result["items"], access, result["source"], previous["constraints"],
                                  previous["overrides"], DiscoveryPlan.model_validate(previous["plan"]))
    if len(result["items"]) != len(previous["response"]["items"]):
        result.update(status="partial" if result["items"] else "empty",
                      summary=f"重新核对后保留 {len(result['items'])} 项。")
    if result["comparison"]:
        allowed = {r["key"]: r for r in result["items"]}
        previous_items = {r["key"]: r for r in previous["response"]["items"]}
        changed = any(k not in allowed or allowed[k].get("overview") != r.get("overview")
                      or allowed[k].get("rating") != r.get("rating") or allowed[k].get("runtime") != r.get("runtime")
                      for k, r in previous_items.items())
        if changed:
            result["comparison"] = _comparison([allowed[c["key"]] for c in result["comparison"]
                                               if c["key"] in allowed])
            result["summary"] = "片库资料已更新，以下按现有资料比较。"
    sessions.verify_access()
    if result != previous["response"]:
        # Subsequent ordinals refer to the refreshed, visible list. Keep the original
        # turn immutable for a different tab that still holds its earlier positions.
        result["total"] = len(result["items"])
        overrides = {i["key"]: previous["overrides"][i["key"]] for i in result["items"]
                     if i["key"] in previous["overrides"]}
        return _turn_response(result, access, DiscoveryPlan.model_validate(previous["plan"]),
                              previous["constraints"], previous["shown"], overrides)
    result["session_id"] = body.session_id
    return result
