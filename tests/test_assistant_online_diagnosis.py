"""A read proposal is bound to one viewing session and one verified network configuration."""

from concurrent.futures import ThreadPoolExecutor
import threading

import pytest
import test_online_state

from app.ai import diagnostic_tools
from app.ai.online_source_intent import diagnosis_intent
from app.online import diagnosis_actions, diagnostics
from app.store import online, online_network


environment = test_online_state.environment
OLD = "https://cj.lziapi.com/api.php/provide/vod/"
NEW = "https://new.example/api.php/provide/vod/"
EVIDENCE = [{"tool": "current_catalogue", "label": "目录", "outcome": "ok", "detail": "目录可用", "checked_at": 1}]


@pytest.fixture
def diagnostic(environment, monkeypatch):
    env = environment
    env.sid = env.source["id"]
    online.update_online_source(env.sid, {"api_base_url": OLD, "allowed_hosts": ["p.lztutu.com", "v.lzcdn28.com"]})
    env.path = "/api/online/sources/" + env.sid + "/diagnosis"
    env.observations = []
    env.status = "update_available"
    env.verify = "healthy"

    def check(source, context, *, verify_saved=False, guard=None, operation=None):
        if guard:
            guard()
        env.observations.append((source["api_base_url"], context, verify_saved))
        fingerprint = online_network.online_network_fingerprint(online_network.load_online_network_state(env.sid))
        if verify_saved and env.verify == "raise":
            raise OSError
        return {"status": env.verify if verify_saved else env.status, "answer": "检测结果。",
                "evidence": EVIDENCE, "network_fingerprint": fingerprint,
                "candidate_url": NEW, "operator_id": "liangzi"}

    monkeypatch.setattr(diagnostics, "diagnose", check)
    return env


def pending(env, request=None):
    response = env.client.post(env.path, json=request or {})
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    record = response.json()
    assert "network_fingerprint" not in record and "candidate_url" not in record
    return record["proposal"]


def confirm_path(env, proposal):
    return env.path + "/" + proposal["id"] + "/confirm"


def test_only_current_owner_can_apply_exact_plan_once_and_recheck_stored_value(diagnostic):
    env = diagnostic
    proposal = pending(env, {"question": "这集为什么超时", "external_id": "11"})
    assert proposal["old_url"] == OLD and proposal["new_url"] == NEW
    assert [change["kind"] for change in proposal["changes"]] == ["api"]
    assert online.get_online_source(env.sid)["api_base_url"] == OLD
    path = confirm_path(env, proposal)
    assert env.stranger.post(path, json={}).status_code == 409
    assert env.client.post(path, json={"new_url": OLD}).status_code == 422
    saved = env.client.post(path, json={})
    assert saved.status_code == 200, saved.text
    receipt = saved.json()
    assert receipt["verification_status"] == "healthy"
    assert receipt["source"]["id"] == env.sid
    assert set(receipt["source"]["allowed_hosts"]) == {"new.example", "p.lztutu.com", "v.lzcdn28.com"}
    assert online_network.verified_source_operator(env.sid, NEW) == "liangzi"
    assert online_network.verified_source_operator(env.sid, OLD) is None
    assert env.client.post(path, json={}).json() == receipt
    assert env.observations[-1] == (NEW, {"external_id": "11"}, True)
    assert len(env.observations) == 2
    online.update_online_source(env.sid, {"api_base_url": OLD})
    assert online_network.verified_source_operator(env.sid, OLD) is None


@pytest.mark.parametrize("conflict", ["rule", "source", "expired", "superseded", "disabled"])
def test_confirm_rejects_a_stale_plan(diagnostic, conflict):
    env = diagnostic
    path = confirm_path(env, pending(env))
    if conflict == "rule":
        online_network.update_source_network_policy(env.sid, {"image": {"mode": "direct"}})
    elif conflict == "source":
        online.update_online_source(env.sid, {"name": "其他管理员修订"})
    elif conflict == "disabled":
        online.update_online_settings(False)
    elif conflict == "superseded":
        pending(env)
    else:
        diagnosis_actions._pending[env.access().owner].deadline = 0
    assert env.client.post(path, json={}).status_code == 409
    assert online.get_online_source(env.sid)["api_base_url"] == OLD
    assert not any(observation[-1] for observation in env.observations)


def test_direct_media_requires_separate_permission_and_only_changes_that_rule(diagnostic):
    env = diagnostic
    env.status = "media_route_available"
    assert pending(env) is None
    before = online_network.get_source_network_policy(env.sid)
    p = pending(env, {"compare_direct": True})
    assert p["kind"] == "media_direct"
    env.verify = "raise"
    receipt = env.client.post(confirm_path(env, p), json={}).json()
    assert receipt["status"] == "applied" and receipt["verification_status"] == "inconclusive"
    assert "已将此来源的视频播放出口改为直连" in receipt["answer"]
    after = online_network.get_source_network_policy(env.sid)
    assert after["policy"] == {**before["policy"], "media": {"mode": "direct", "profile_id": None}}
    assert online.get_online_source(env.sid)["api_base_url"] == OLD


def test_duplicate_confirmation_while_saved_config_is_being_probed(diagnostic, monkeypatch):
    env = diagnostic
    original = diagnostics.diagnose
    entered, finish = threading.Event(), threading.Event()

    def check(source, context, *, verify_saved=False, guard=None, operation=None):
        if verify_saved:
            entered.set()
            assert finish.wait(5)
        return original(source, context, verify_saved=verify_saved, guard=guard, operation=operation)

    monkeypatch.setattr(diagnostics, "diagnose", check)
    p = pending(env)
    access = env.access()
    with ThreadPoolExecutor(2) as threads:
        worker = threads.submit(diagnosis_actions.confirm, access, env.sid, p["id"])
        try:
            assert entered.wait(5)
            repeat = threads.submit(diagnosis_actions.confirm, access, env.sid, p["id"])
        finally:
            finish.set()
        assert worker.result()["verification_status"] == "healthy"
        assert repeat.result() == worker.result()
    assert len(env.observations) == 2


def test_insufficient_evidence_read_is_safe_and_new_protection_revokes_grant(diagnostic):
    env = diagnostic
    env.status = "media_problem"
    assert pending(env) is None
    env.status = "update_available"
    p = pending(env)
    assert env.client.post("/api/viewing/password", json={"new_password": "demo-password"}).status_code == 200
    assert env.client.post(confirm_path(env, p), json={}).status_code == 403
    assert online.get_online_source(env.sid)["api_base_url"] == OLD


@pytest.mark.parametrize("model_result,expected", [
    ({"tools": ["inspect_item", "inspect_item"]}, ["inspect_item"]),
    ({"tools": ["write_config"]}, ["source_history"]), ({"tools": []}, ["source_history"]),
    ({"tools": ["inspect_item"], "url": "private"}, ["source_history"]),
    ({"tools": [{}]}, ["source_history"]), (None, ["source_history"]),
])
def test_planner_only_sends_categories_and_accepts_advertised_tools(monkeypatch, model_result, expected):
    options = {"source_history": "查看历史", "inspect_item": "检测节目"}
    def model(task, payload, instruction, *, timeout_limit):
        assert task == "diagnostic_tools" and timeout_limit <= 4
        assert payload == {"symptoms": ["network"], "observations": [{"tool": "current_catalogue", "outcome": "ok"}],
                           "tools": options}
        return model_result
    monkeypatch.setattr(diagnostic_tools, "call_json", model)
    assert diagnostic_tools.choose_tools("代理 https://private.invalid", [dict(EVIDENCE[0], detail="private")],
                                         options, ["source_history"]) == expected


def test_diagnosis_intent_requires_an_online_symptom():
    assert diagnosis_intent("为什么量子在线资源无法播放了？", online_context=False)["diagnosis_template_id"] == "liangzi"
    assert diagnosis_intent("这一集怎么一直转圈？", online_context=True)["diagnose_online"]
    assert diagnosis_intent("量子资源在哪里找", online_context=True) is None
    assert diagnosis_intent("本地影片无法播放", online_context=False) is None


def test_network_or_permission_changes_interrupt_diagnosis_before_next_tool(diagnostic, monkeypatch):
    env = diagnostic

    def reading(source, context, *, guard, operation):
        guard()
        online_network.update_source_network_policy(env.sid, {"image": {"mode": "direct"}})
        guard()
        pytest.fail("next external tool should be stopped")

    monkeypatch.setattr(diagnostics, "diagnose", reading)
    assert env.client.post(env.path, json={}).status_code == 409
    assert online.get_online_source(env.sid)["api_base_url"] == OLD
