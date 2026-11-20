"""Settings questions return local references and inert, validated proposals."""
import json

import pytest
from fastapi.testclient import TestClient

from app import store, viewing
from app.ai import client as ai_client, settings, settings_assistant as assistant
from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(store._base, "DB_PATH", str(tmp_path / "assistant.db"))
    for name in settings.DEFAULTS:
        monkeypatch.delenv("AI_" + name.upper(), raising=False)
    monkeypatch.delenv("JZMEDIA_TOKEN", raising=False)
    store.init_db()
    client = TestClient(app)
    yield client
    client.close()
    ai_client.clear_cache()


def ask(client, question, **extra):
    return client.post("/api/ai/settings-assistant", json={"question": question, **extra})


def mock_model(monkeypatch, topic, suggestion=None, **other):
    sent = []

    def answer(task, payload, instruction):
        sent.append(payload)
        return {"answer": "请核对设置及其作用范围。", "topics": [topic],
                "suggestion": suggestion, **other}

    monkeypatch.setattr(assistant, "call_json", answer)
    return sent


def test_local_manual_without_ai_or_credentials(client):
    result = ask(client, "我第一次使用，怎么连接 SMB？")
    assert result.status_code == 200 and result.headers["cache-control"] == "no-store"
    body = result.json()
    assert body["mode"] == "local" and body["suggestion"] is None
    assert body["topics"][0]["id"] == "smb" and "尚未启用" in body["answer"]
    assert any(t["section"] == "sec-ai" for t in body["topics"])
    assert set(body["topics"][0]) == {"id", "title", "page", "anchor", "section"}
    assert settings.usage()["requests"] == 0


def test_local_history_and_topic_switch(client):
    history = [{"question": "在哪里调整匹配规则？", "answer": "可以先选好视频库。"},
               {"question": "然后呢？", "answer": "可以继续。"}]
    followup = ask(client, "帮我改", history=history, section="sec-display").json()
    assert followup["topics"][0]["id"] == "matching"
    switched = ask(client, "现在我想连接 SMB", history=history, section="sec-matching").json()
    assert switched["topics"][0]["id"] == "smb"
    unrelated = ask(client, "白板上的蓝色圆圈呢？", section="sec-status").json()
    assert "未能定位" in unrelated["answer"] and unrelated["topics"][0]["id"] == "overview"


@pytest.mark.parametrize("question", ["把字调大一点", "自动推荐新剧集怎么关", "界面的字有点费眼"])
def test_model_can_ground_natural_language_even_without_a_keyword(client, monkeypatch, question):
    sent = mock_model(monkeypatch, "display")
    result = ask(client, question, section="sec-status").json()
    assert result["mode"] == "ai" and result["topics"][0]["id"] == "display"
    assert {t["id"] for t in sent[0]["manual"]} == {t["id"] for t in assistant._manual()}


def test_configured_client_and_daily_quota_use_the_same_local_manual(client, monkeypatch):
    settings.update({"enabled": True, "api_key": "test-deepseek-key", "daily_limit": 2})
    calls = []

    async def provider(current, body):
        calls.append(body)
        result = {"answer": "请在匹配规则里选择视频库，调整来源顺序。", "topics": ["matching"]}
        if len(calls) == 2:
            result["suggestion"] = {"kind": "matching", "operation": "prioritize", "providers": ["tmdb"]}
        content = json.dumps(result)
        return {"choices": [{"message": {"content": content}}]}

    monkeypatch.setattr(ai_client, "_post", provider)
    question = "怎么调整匹配规则？"
    first = ask(client, question).json()
    assert first["mode"] == "ai" and first["suggestion"] is None
    history = [{"question": "说" * 500, "answer": "答" * 1600}] * 3
    history.append({"question": question, "answer": first["answer"]})
    next_turn = ask(client, "帮我优先用 TMDB", history=history).json()
    assert next_turn["mode"] == "ai" and next_turn["suggestion"] == {
        "kind": "matching", "operation": "prioritize", "providers": ["tmdb"]}
    followup = json.loads(calls[-1]["messages"][1]["content"])
    assert followup["history"][-1] == history[-1] and len(followup["manual"]) == len(assistant._manual())
    limited = ask(client, "如何调整海报大小？").json()
    assert limited["mode"] == "local" and "已达上限" in limited["answer"] and limited["topics"]
    assert len(calls) == settings.usage()["requests"] == 2
    assert "test-deepseek-key" not in json.dumps(calls)


def test_conversation_uses_only_manual_and_user_messages(client, monkeypatch, tmp_path):
    store.set_settings({"jzmedia_token": "secret-for-write-access", "tmdb_read_token": "tmdb-app-secret",
                        "tmdb_proxy": "http://internal.proxy.invalid"})
    client.headers["X-Api-Token"] = "secret-for-write-access"
    directory = tmp_path / "private-media"
    directory.mkdir()
    library = store.create_library(name="Private films test", kind="movie", path=str(directory))
    sent = mock_model(monkeypatch, "matching", {"kind": "matching", "operation": "prioritize",
                                               "providers": ["tmdb"]})
    history = [{"question": "在哪里设置匹配规则？", "answer": "打开匹配规则分区。"}]
    result = ask(client, "帮我把 TMDB 放在第一位", history=history, section="sec-matching").json()
    assert result["mode"] == "ai" and result["suggestion"]["providers"] == ["tmdb"]
    assert sent[0]["history"] == history and sent[0]["section"] == "sec-matching"
    assert "matching" in [t["id"] for t in sent[0]["manual"]]
    assert store.get_library(library["id"])["metadata_providers"] == library["metadata_providers"]
    payload = json.dumps(sent)
    for private in ["private-media", "Private films test", "secret-for-write-access", "tmdb-app-secret",
                    "internal.proxy.invalid", "library_id", "metadata_providers"]:
        assert private not in payload


@pytest.mark.parametrize("field,change", [
    ("fontSize", {"value": 18}), ("posterMin", {"delta": -10}), ("tvUpdates", {"value": "off"}),
])
def test_browser_suggestions_have_no_extra_state(client, monkeypatch, field, change):
    expected = {"kind": "display", "field": field, **change}
    mock_model(monkeypatch, "display", expected)
    assert ask(client, "帮我调整界面显示", section="sec-display").json()["suggestion"] == expected
    assert ask(client, "如何调整界面显示？").json()["suggestion"] is None


@pytest.mark.parametrize("operation,question", [
    ("enable", "帮我启用 Wikidata，其他来源保持顺序。"),
    ("disable", "停用 Wikidata，保留其他勾选项。"),
])
def test_relative_provider_operations_do_not_require_actual_library_state(client, monkeypatch, operation, question):
    proposal = {"kind": "matching", "operation": operation, "providers": ["wikidata"]}
    mock_model(monkeypatch, "matching", proposal)
    assert ask(client, question).json()["suggestion"] == proposal
    assert ask(client, "怎样调整匹配来源？").json()["suggestion"] is None


@pytest.mark.parametrize("replacement", [
    {"topics": ["https://example.invalid/private"]}, {"topics": ["display"], "url": "/api/settings"},
    {"answer": "点击 https://example.invalid/ 配置。"},
    {"suggestion": {"kind": "display", "field": "fontSize", "value": 21}},
    {"suggestion": {"kind": "display", "field": "fontSize", "value": True}},
    {"suggestion": {"kind": "display", "field": "posterMin", "delta": 1}},
    {"suggestion": {"kind": "display", "field": "tvUpdates", "value": "daily"}},
    {"suggestion": {"kind": "display", "field": "fontSize", "value": 15, "delta": 1}},
    {"suggestion": {"kind": "display", "field": "jzmedia_token", "value": "not-allowed"}},
    {"suggestion": {"kind": "matching", "operation": "set", "providers": ["unknown"]}},
    {"suggestion": {"kind": "matching", "operation": "set", "providers": ["local", "local"]}},
    {"suggestion": {"kind": "matching", "operation": "set", "providers": ["tmdb"], "library_id": 1}},
])
def test_invalid_output_links_actions_or_ranges_return_local_help(client, monkeypatch, replacement):
    mock_model(monkeypatch, "display", **replacement)
    result = ask(client, "请调整界面显示", section="sec-display").json()
    assert result["mode"] == "local" and result["suggestion"] is None
    assert result["topics"][0]["id"] == "display"
    assert "example.invalid" not in str(result)


@pytest.mark.parametrize("private", [
    "访问令牌：demo12345", "smb://test:private@inside/share", "我的路径是/private/movies",
    "proxy=http://inside", "Bearer test12345", "API Key 为 fake-secret",
    r"\\server\private", r"我的位置是C:\movies", "AI_API_KEY='secret'", "sk-unit-test-key",
    "我的共享是192.168.0.1", "我的密钥用 abc123", "把密码改成fakepass",
])
def test_private_question_or_history_never_calls_provider(client, monkeypatch, private):
    monkeypatch.setattr(assistant, "call_json", lambda *a: pytest.fail("private input reached provider"))
    for result in (ask(client, private), ask(client, "还有呢？", history=[{"question": private, "answer": "好的"}])):
        assert result.json()["mode"] == "local"
        assert "本地手册" in result.json()["answer"] and private not in result.text


def test_locked_management_and_write_token_also_protect_questions(client, monkeypatch):
    sent = mock_model(monkeypatch, "display")
    assert client.post("/api/viewing/password", json={"new_password": "123456"}).status_code == 200
    assert ask(client, "怎么调整字体？").status_code == 403
    assert client.post("/api/viewing/mode", json={"mode": "children"}).status_code == 200
    assert ask(client, "怎么调整字体？").status_code == 403
    assert client.post("/api/viewing/mode", json={"mode": "normal", "password": "123456"}).status_code == 200
    assert client.post("/api/viewing/unlock", json={"password": "123456"}).status_code == 200
    store.set_setting("jzmedia_token", "test-token")
    assert ask(client, "怎么调整字体？").status_code == 401 and not sent
    client.headers["X-Api-Token"] = "test-token"
    assert ask(client, "怎么调整字体？").status_code == 200 and len(sent) == 1

    def lock(*args):
        with viewing._connection() as connection:
            connection.execute("UPDATE viewing_sessions SET management_until=0")
        return {"answer": "不应显示过期内容。", "topics": ["display"]}

    monkeypatch.setattr(assistant, "call_json", lock)
    denied = ask(client, "怎么调整字体？")
    assert denied.status_code == 403 and "过期内容" not in denied.text


@pytest.mark.parametrize("extra", [{"history": [{"question": "x", "answer": "y"}] * 5},
                                     {"section": "not/a/section"}, {"config": {}}])
def test_rejects_unbounded_or_unexpected_context(client, extra):
    assert ask(client, "字体设置", **extra).status_code == 422
