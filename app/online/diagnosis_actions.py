"""Short-lived, device-bound confirmation of changes proven by the diagnostic tools."""

from dataclasses import dataclass, field
import sqlite3
import threading
import time
import uuid

from .. import viewing
from ..store import online, online_network
from . import diagnostics, service, source_hosts, source_migration


_LIFETIME = 300
_lock = threading.Lock()
_pending = {}
_reading = set()
_latest = {}


@dataclass
class Proposal:
    access: service.PlaybackAccess
    source_id: str
    context: dict
    fingerprint: str
    public: dict
    selection: dict
    operator: str | None
    deadline: float
    receipt: dict | None = None
    checked: threading.Event = field(default_factory=threading.Event)


def _changed():
    return service.PlaybackError("online_diagnosis_changed", 409)


def _get_state(source_id):
    try:
        return online_network.load_online_network_state(source_id)
    except online.OnlineNotFound:
        raise service.PlaybackError("online_source_missing", 404) from None


def _check_current(access, source_id, fingerprint):
    source_hosts._management(access)
    if online_network.online_network_fingerprint(_get_state(source_id)) != fingerprint:
        raise _changed()


def _purge():
    now = time.monotonic()
    for owner in [key for key, plan in _pending.items() if plan.deadline <= now]:
        del _pending[owner]


def _proposal(access, state, context, result):
    current = state["source_config"]
    selection = {}
    if result["status"] == "update_available" and result.get("candidate_url") and result.get("operator_id"):
        new_url = result["candidate_url"]
        report = source_migration.preview(current, new_url, current["allowed_hosts"])
        # The live test proves the catalogue host. An unrelated image or CDN
        # migration still belongs to the individually selectable settings preview.
        changes = [change for change in report["changes"] if change["kind"] == "api"]
        selection = {"expected_fingerprint": report["expected_fingerprint"],
                     "selected_old_hosts": [change["old_host"] for change in changes]}
        public = {"kind": "api", "old_url": current["api_base_url"], "new_url": new_url,
                  "changes": changes}
    elif result["status"] == "media_route_available" and context.get("compare_direct") is True:
        public = {"kind": "media_direct", "old_label": "代理", "new_label": "直连", "changes": []}
    else:
        return None
    public.update(id="diag_" + uuid.uuid4().hex, expires_at=int(time.time()) + _LIFETIME)
    saved_context = {key: context[key] for key in ("item_id", "external_id", "line_id", "episode_id", "error_code")
                     if context.get(key)}
    return Proposal(access, current["id"], saved_context, result["network_fingerprint"], public,
                    selection, result.get("operator_id"), time.monotonic() + _LIFETIME)


def start(access, source_id, context, *, operation=None):
    source_hosts._management(access)
    ticket = object()
    with _lock:
        _purge()
        if len(_reading) >= 4:
            raise service.PlaybackError("online_diagnosis_busy", 429)
        _reading.add(ticket)
        _latest[access.owner] = ticket
        _pending.pop(access.owner, None)
    try:
        state = _get_state(source_id)
        original = online_network.online_network_fingerprint(state)
        session = viewing.session_by_hash(access.owner)
        context = {**context, "allow_model": bool(session and session["mode"] == "normal")}

        def check():
            with _lock:
                if _latest.get(access.owner) is not ticket:
                    raise _changed()
            _check_current(access, source_id, original)

        result = diagnostics.diagnose(state["source_config"], context, guard=check, operation=operation)
        if operation and operation.cancelled.is_set():
            raise service.PlaybackError("online_diagnosis_cancelled", 409)
        source_hosts._management(access)
        response = {key: result[key] for key in ("status", "answer", "evidence", "can_compare_direct") if key in result}
        response["proposal"] = None
        fingerprint = result.get("network_fingerprint")
        if fingerprint and fingerprint != online_network.online_network_fingerprint(_get_state(source_id)):
            raise _changed()
        if fingerprint and state["online_enabled"] and state["source_config"]["enabled"]:
            proposal = _proposal(access, state, context, result)
            if proposal:
                with _lock:
                    if _latest.get(access.owner) is not ticket:
                        raise _changed()
                    if len(_pending) >= 256:
                        raise service.PlaybackError("online_diagnosis_busy", 429)
                    _pending[access.owner] = proposal
                response["proposal"] = proposal.public
        return response
    finally:
        with _lock:
            _reading.discard(ticket)
            if _latest.get(access.owner) is ticket:
                del _latest[access.owner]
        if operation:
            operation.close()


def _apply(plan):
    with online._connection(write=True) as connection:
        state = online_network.online_network_snapshot(connection, plan.source_id)
        if time.monotonic() >= plan.deadline or online_network.online_network_fingerprint(state) != plan.fingerprint:
            raise _changed()
        current = state["source_config"]
        source_hosts._management(plan.access)
        if plan.public["kind"] == "api":
            current, _ = online._update_source(connection, current, {
                "api_base_url": plan.public["new_url"], "host_migration": plan.selection})
            online_network.save_verified_operator(connection, plan.source_id, plan.operator)
        else:
            online_network._update_source_policy(connection, plan.source_id, {"media": {"mode": "direct"}})
        saved = online_network.online_network_snapshot(connection, plan.source_id)
        return current, online_network.online_network_fingerprint(saved)


def confirm(access, source_id, proposal_id):
    source_hosts._management(access)
    with _lock:
        _purge()
        plan = _pending.get(access.owner)
        if not plan or plan.access != access or plan.source_id != source_id or plan.public["id"] != proposal_id:
            raise _changed()
        replay = plan.receipt is not None
        if not replay:
            try:
                source, fingerprint = _apply(plan)
            except (online.OnlineNotFound, source_migration.SourceChanged):
                raise _changed() from None
            prefix = "已更新此来源的接口和允许主机。" if plan.public["kind"] == "api" else "已将此来源的视频播放出口改为直连。"
            plan.receipt = {"status": "applied", "source": source, "answer": prefix + "此前的确认正在复测，请稍后重新诊断查看结果。",
                            "evidence": [], "verification_status": "pending"}
    if replay:
        plan.checked.wait(35)
        source_hosts._management(access)
        return plan.receipt
    # Once committed, a failed check must still return a receipt for the saved change.
    try:
        service.registry.revoke_source(source_id)
        _check_current(access, source_id, fingerprint)
        result = diagnostics.diagnose(source, plan.context, verify_saved=True,
                                      guard=lambda: _check_current(access, source_id, fingerprint))
        if (result.get("network_fingerprint") != fingerprint or
                online_network.online_network_fingerprint(_get_state(source_id)) != fingerprint):
            result = {"status": "inconclusive", "answer": "复测期间配置已变化或检测繁忙，请重新诊断。", "evidence": []}
    except (ValueError, RuntimeError, LookupError, OSError, sqlite3.Error):
        result = {"status": "inconclusive", "answer": "这次未能完成复测，可以重新诊断。", "evidence": []}
    with _lock:
        plan.receipt = {**plan.receipt, "verification_status": result["status"],
                        "answer": prefix + result["answer"], "evidence": result["evidence"]}
        plan.checked.set()
    source_hosts._management(access)
    return plan.receipt
