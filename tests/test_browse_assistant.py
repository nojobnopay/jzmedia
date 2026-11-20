"""The browsing companion is available without management and respects the viewing session."""
import json

import pytest
from fastapi.testclient import TestClient

from app import store, viewing
from app.ai import browse_assistant, client as ai_client, settings
from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(store._base, "DB_PATH", str(tmp_path / "browse.db"))
    for name in settings.DEFAULTS:
        monkeypatch.delenv("AI_" + name.upper(), raising=False)
    monkeypatch.delenv("JZMEDIA_TOKEN", raising=False)
    store.init_db()
    client = TestClient(app)
    yield client
    client.close()
    ai_client.clear_cache()


def ask(client, question, channel="movie", **extra):
    return client.post("/api/ai/assistant", json={"channel": channel, "question": question, **extra})


def protect(client):
    assert client.get("/api/viewing/session").status_code == 200
    assert client.post("/api/viewing/password", json={"new_password": "123456"}).status_code == 200


@pytest.mark.parametrize("channel, question, expected", [
    ("movie", "电影怎么筛选？", "应用筛选"),
    ("movie", "怎么打开字幕？", "字幕与音轨"),
    ("tv", "怎么选集？", "已收藏"),
    ("collections", "如何新建合集？", "加入合集"),
    ("online", "怎么续播？", "我的记录"),
    ("online", "怎么切字幕？", "片源和设备"),
    ("settings", "第一次入库怎么做？", "新手配置"),
])
def test_channel_help_before_model_setup(client, channel, question, expected):
    response = ask(client, question, channel)
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert response.json() == {"answer": response.json()["answer"], "reaction": None, "mode": "local"}
    assert expected in response.json()["answer"] and settings.usage()["requests"] == 0


def test_offline_conversation_can_follow_a_channel_switch(client):
    first = ask(client, "怎么新建合集？", "collections").json()
    history = [{"question": "怎么新建合集？", "answer": first["answer"]}]
    assert "加入合集" in ask(client, "然后呢？", "movie", history=history).json()["answer"]
    dance = ask(client, "跳个舞", "movie", history=history).json()
    assert dance["mode"] == "social" and dance["reaction"] == "dance"
    history.append({"question": "跳个舞", "answer": dance["answer"]})
    again = ask(client, "再来一次", "tv", history=history).json()
    assert again["reaction"] == "dance" and again["answer"] != dance["answer"]


def test_source_update_from_browsing_hands_off_without_model_or_source_access(client, monkeypatch):
    monkeypatch.setattr(browse_assistant, "call_json", lambda *args: pytest.fail("unexpected model request"))
    question = "帮我更新量子资源接口"
    response = ask(client, question, "online").json()
    assert response["mode"] == "local" and response["open_settings"] == "online_source"
    assert "suggestion" not in response and settings.usage()["requests"] == 0
    protect(client)
    for mode in ("private", "children"):
        client.post("/api/viewing/mode", json={"mode": mode, "password": "123456"}).raise_for_status()
        assert "open_settings" not in ask(client, question, "movie").json()


def test_offline_followup_applies_the_subject_to_the_new_channel(client):
    question = "怎么找没看过的电影？"
    movie = ask(client, question).json()
    assert "未看影片" in movie["answer"]
    tv = ask(client, "那剧集呢？", "tv", history=[{"question": question, "answer": movie["answer"]}]).json()
    assert tv["mode"] == "local" and "整剧已看完或未看完" in tv["answer"]


def test_configured_model_gets_only_public_guide_channel_and_bounded_conversation(client, monkeypatch, tmp_path):
    protect(client)
    settings.update({"enabled": True, "api_key": "test-provider-credential", "daily_limit": 1})
    store.set_setting("tmdb_read_token", "private-setting")
    directory = tmp_path / "private-library"
    directory.mkdir()
    store.create_library(name="Unshared film library", kind="movie", path=str(directory))
    sent = []

    async def respond(config, body):
        sent.append(json.loads(body["messages"][1]["content"]))
        content = json.dumps({"mode": "social", "answer": "当然可以，想聊点什么？", "reaction": "smile"})
        return {"choices": [{"message": {"content": content}}]}

    monkeypatch.setattr(ai_client, "_post", respond)
    assert client.post("/api/ai/settings-assistant", json={"question": "你好"}).status_code == 403
    history = [{"question": "提" * 500, "answer": "答" * 1600}] * 3 + [{"question": "在吗", "answer": "我在呀"}]
    result = ask(client, "可以陪我聊聊天吗？", "collections", history=history).json()
    assert result == {"mode": "social", "answer": "当然可以，想聊点什么？", "reaction": "smile"}
    payload, = sent
    assert set(payload) == {"guide", "question", "history", "channel"}
    assert payload["history"][-1] == history[-1] and len(payload["history"]) <= 4
    assert payload["channel"] == "collections" and payload["question"] == "可以陪我聊聊天吗？"
    assert any("加入合集" in entry for entry in payload["guide"])
    assert all(term not in str(payload) for term in (
        "private-setting", "private-library", "Unshared film library", "test-provider-credential"))
    limited = ask(client, "在线怎么搜？", "online").json()
    assert limited["mode"] == "local" and "当前来源" in limited["answer"] and "额度" in limited["answer"]
    assert settings.usage()["requests"] == len(sent) == 1


@pytest.mark.parametrize("mode", ["children", "private"])
def test_restricted_modes_do_not_contact_a_provider(client, monkeypatch, mode):
    protect(client)
    assert client.post("/api/viewing/mode", json={"mode": mode, "password": "123456"}).status_code == 200
    monkeypatch.setattr(browse_assistant, "call_json", lambda *args: pytest.fail("unexpected model request"))
    assert ask(client, "小映，招招手！", "collections").json()["reaction"] == "wave"
    answer = ask(client, "怎么选集？", "tv").json()
    assert answer["mode"] == "local" and "已收藏" in answer["answer"]
    online = ask(client, "怎么在线看？", "online").json()
    if mode == "children":
        assert "儿童模式" in online["answer"] and "视频源" not in online["answer"]
        collections = ask(client, "如何新建合集？", "collections").json()
        assert "儿童模式" in collections["answer"] and "加入合集" not in collections["answer"]
    else:
        assert "视频源" in online["answer"]


def test_private_text_is_checked_in_the_question_and_both_history_fields(client, monkeypatch):
    monkeypatch.setattr(browse_assistant, "call_json", lambda *args: pytest.fail("unexpected model request"))
    for response in (ask(client, "smb://local/private/"),
                     ask(client, "你好", history=[{"question": "密码是 fake-password", "answer": "你好"}]),
                     ask(client, "继续", history=[{"question": "告诉我", "answer": "/private/path"}])):
        body = response.json()
        assert body["mode"] == "local" and "省略具体值" in body["answer"] and body["reaction"] is None


def test_write_token_and_viewing_lease(client, monkeypatch):
    protect(client)
    store.set_setting("jzmedia_token", "test-token")

    def invalidate(*args):
        with viewing._connection() as connection:
            connection.execute("UPDATE viewing_sessions SET generation=generation+1")
        return {"mode": "ai", "answer": "过期的答复"}

    monkeypatch.setattr(browse_assistant, "call_json", invalidate)
    assert ask(client, "怎么续播？").status_code == 401
    client.headers["X-Api-Token"] = "test-token"
    assert ask(client, "你好").json()["reaction"] == "wave"
    expired = ask(client, "怎么续播？")
    assert expired.status_code == 403 and expired.json()["code"] == "viewing_locked"
    assert "过期的答复" not in expired.text


def test_stale_browser_mode_cannot_send_private_turns_after_server_unlock(client, monkeypatch):
    monkeypatch.setattr(browse_assistant, "call_json", lambda *args: pytest.fail("stale mode reached provider"))
    client.headers["X-Viewing-Mode"] = "private"
    response = ask(client, "那后来呢？", history=[{"question": "上一轮私密问题", "answer": "上一轮私密回答"}])
    assert response.status_code == 403 and response.json()["code"] == "viewing_locked"


@pytest.mark.parametrize("raw", [
    {"mode": "ai", "answer": "点 https://example.invalid/ 看看吧"},
    {"mode": "ai", "answer": "好了", "suggestion": {"kind": "display"}},
    {"mode": "social", "answer": "我来了", "reaction": "delete"},
])
def test_model_reply_cannot_introduce_links_or_actions(client, monkeypatch, raw):
    monkeypatch.setattr(browse_assistant, "call_json", lambda *args: raw)
    result = ask(client, "如何调整字幕？").json()
    assert result["mode"] == "local" and "字幕与音轨" in result["answer"]
    assert result["reaction"] is None and set(result) == {"answer", "mode", "reaction"}


@pytest.mark.parametrize("data", [
    {"channel": "admin"}, {"watch_history": ["title"]}, {"history": [{"question": "q", "answer": "a"}] * 5},
    {"question": "提" * 501}, {"question": "  "},
])
def test_reject_unknown_or_unbounded_client_state(client, data):
    response = client.post("/api/ai/assistant", json={"channel": "movie", "question": "问", **data})
    assert response.status_code == 422
