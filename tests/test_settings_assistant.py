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
    system = calls[0]["messages"][0]
    assert system["role"] == "system" and system["content"].startswith(assistant._INSTRUCTION)
    assert "小映" in system["content"] and "仅返回一个 JSON 对象" in system["content"]
    history = [{"question": "说" * 500, "answer": "答" * 1600}] * 3
    history.append({"question": question, "answer": first["answer"]})
    next_turn = ask(client, "帮我优先用 TMDB", history=history).json()
    assert next_turn["mode"] == "ai" and next_turn["suggestion"] == {
        "kind": "matching", "operation": "prioritize", "providers": ["tmdb"]}
    followup = json.loads(calls[-1]["messages"][1]["content"])
    assert followup["history"][-1] == history[-1] and len(followup["manual"]) == len(assistant._manual())
    limited = ask(client, "如何调整海报大小？").json()
    assert limited["mode"] == "local" and "已达上限" in limited["answer"] and limited["topics"]
    assert ask(client, "给我笑一笑").json()["reaction"] == "smile"
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


@pytest.mark.parametrize("question,reaction,keyword", [
    ("你叫什么名字？", "wave", "小映"), ("小映，你好啊！", "wave", "你好"),
    ("给我笑一笑", "smile", "笑"), ("给我哭一个", "cry", "眼泪"),
    ("跳个舞", "dance", "舞"), ("请给我跳一支舞好吗？", "dance", "舞"),
    ("跳支舞", "dance", "舞"), ("跳段舞", "dance", "舞"), ("请你给我跳支舞吧", "dance", "舞"),
    ("小映小映，你叫什么名字呀？", "wave", "小映"),
    ("谢谢你", "smile", "不客气"), ("我有点难过", None, "我在"),
])
def test_whole_social_cues_reply_without_model_or_side_effects(client, monkeypatch, question, reaction, keyword):
    monkeypatch.setattr(assistant, "call_json", lambda *a: pytest.fail("unexpected model call"))
    before = store.get_library(store.DEFAULT_LIBRARY_ID)
    result = ask(client, question, section="sec-display",
                 history=[{"question": "把字体调大", "answer": "核对后确认。"}]).json()
    assert keyword in result["answer"]
    assert result == {"answer": result["answer"], "mode": "social", "reaction": reaction,
                      "topics": [], "suggestion": None}
    assert store.get_library(store.DEFAULT_LIBRARY_ID) == before and settings.usage()["requests"] == 0


def test_repeat_performance_uses_only_immediately_preceding_turn(client, monkeypatch):
    sent = mock_model(monkeypatch, "display")
    history = []
    for question in ["跳个舞", "再来一次", "再跳一次", "再来一段吧"]:
        result = ask(client, question, history=history).json()
        assert result["reaction"] == "dance" and result["mode"] == "social"
        if history:
            assert result["answer"] != history[-1]["answer"]
        history.append({"question": question, "answer": result["answer"]})
    assert not sent
    history = [history[-1], {"question": "请帮我调大字体", "answer": "请核对显示设置。"}]
    assert ask(client, "再来一次", history=history).json()["mode"] == "ai"
    assert len(sent) == 1
    monkeypatch.setattr(assistant, "call_json", lambda *a: {"mode": "social", "answer": "再跳一段！", "reaction": "dance"})
    ambiguous = ask(client, "再来一次", history=history).json()
    assert ambiguous["reaction"] is None and ambiguous["suggestion"] is None and "哪个动作" in ambiguous["answer"]


def test_social_and_manual_guards_apply_to_model_responses(client, monkeypatch):
    social = {"mode": "social", "answer": "小映在。这场电影有什么地方让你舍不得？", "reaction": None}
    calls = []

    def send(task, data, prompt):
        calls.append(prompt)
        return social

    monkeypatch.setattr(assistant, "call_json", send)
    result = ask(client, "看完散场时总有点舍不得，你呢？").json()
    assert result == {**social, "topics": [], "suggestion": None}
    assert len(calls) == 1 and "小映" in calls[0] and "确认" in calls[0]
    for question in ("你好啊，帮我把字体调大一点", "怎么关闭跳舞动画？", "你好，怎么连接媒体库？"):
        rejected = ask(client, question).json()
        assert rejected["mode"] == "local" and rejected["topics"] and rejected["suggestion"] is None
    assert len(calls) == 4
    sent = mock_model(monkeypatch, "display", {"kind": "display", "field": "fontSize", "delta": 1})
    configured = ask(client, "你好啊，帮我把字体调大一点").json()
    assert configured["mode"] == "ai" and configured["suggestion"]["delta"] == 1 and len(sent) == 1


def test_setting_proposal_does_not_require_built_in_change_phrasing(client, monkeypatch):
    matching = {"kind": "matching", "operation": "prioritize", "providers": ["tmdb"]}
    mock_model(monkeypatch, "matching", matching)
    for question in ("帮我把 TMDB 放前面", "把本地索引挪到最后"):
        assert ask(client, question).json()["suggestion"] == matching
    mock_model(monkeypatch, "display", {"kind": "display", "field": "fontSize", "delta": 2})
    assert ask(client, "字体放大两号").json()["suggestion"]["delta"] == 2
    history = [{"question": "怎么调字号？", "answer": "字体大小可以按像素调整。"}]
    assert ask(client, "按刚才说的来", history=history).json()["suggestion"]["delta"] == 2
    assert ask(client, "讲个故事吧").json()["suggestion"] is None


@pytest.mark.parametrize("invalid", [
    {"reaction": "delete"}, {"topics": ["display"]}, {"url": "/api/settings"},
    {"suggestion": {"kind": "display", "field": "fontSize", "value": 20}},
    {"answer": "点击 https://example.invalid 就能看到我跳舞"},
])
def test_model_small_talk_cannot_smuggle_actions_or_references(client, monkeypatch, invalid):
    monkeypatch.setattr(assistant, "call_json", lambda *a: {"mode": "social", "answer": "在呢。", **invalid})
    result = ask(client, "给我表演个拿手绝活").json()
    assert result["mode"] == "local" and result["suggestion"] is None and "reaction" not in result


def test_private_history_and_management_protect_social_requests(client, monkeypatch):
    monkeypatch.setattr(assistant, "call_json", lambda *a: pytest.fail("unexpected model call"))
    private = ask(client, "给我笑一笑", history=[{"question": "密码：fake-secret", "answer": "收到。"}]).json()
    assert private["mode"] == "local" and "fake-secret" not in str(private) and "reaction" not in private
    assert client.post("/api/viewing/password", json={"new_password": "123456"}).status_code == 200
    assert ask(client, "给我笑一笑").status_code == 403


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


def test_source_change_is_a_local_proposal_and_never_modifies_the_saved_source(client, monkeypatch):
    monkeypatch.setattr(assistant, "call_json", lambda *args: pytest.fail("source request reached model"))
    created = client.post("/api/online/sources", json={
        "provider": "maccms", "name": "自定义收藏名称", "api_base_url": "https://cj.lziapi.com/api.php/provide/vod/"})
    created.raise_for_status()
    path = "/api/online/sources/" + created.json()["id"]
    before = client.get(path).json()

    for question, template_id, url in (
        ("帮我更新量子资源接口", "liangzi", None),
        ("帮我把量子换到新接口", "liangzi", None),
        ("给我把量子源换成最新的", "liangzi", None),
        ("把暴风资源的接口换到最新的主接口", "baofeng", None),
        ("请更新非凡API", "feifan", None),
        ("更换视频源接口", None, None),
        ("帮我换接口", None, None),
        ("把接口换成 https://catalog.example/api.php/provide/vod/", None, "https://catalog.example/api.php/provide/vod/"),
        ("请把量子资源改成 https://CUSTOM.example:443/catalog/", "liangzi", "https://custom.example:443/catalog/"),
    ):
        response = ask(client, question, section="sec-online")
        assert response.status_code == 200
        body = response.json()
        assert body["mode"] == "local" and body["topics"][0]["id"] == "online-source-migration"
        assert body["suggestion"] == {"kind": "online_source", "template_id": template_id, "api_base_url": url}
        assert "https://" not in body["answer"] and "确认" in body["answer"]

    assert client.get(path).json() == before
    assert settings.usage()["requests"] == 0


def test_playback_failure_links_to_live_diagnosis_help(client, monkeypatch):
    monkeypatch.setattr(assistant, "call_json", lambda *args: pytest.fail("playback diagnosis intent reached model"))
    result = ask(client, "为什么量子在线资源无法播放了？").json()
    assert result["mode"] == "local" and result["diagnose_online"] is True
    assert result["diagnosis_template_id"] == "liangzi" and result["suggestion"] is None
    assert result["topics"][0]["id"] == "online-source-diagnosis"


@pytest.mark.parametrize("source_request", [
    "帮我把量子和暴风的接口都更新", "帮我更新量子的备用接口", "帮我更新量子资源的海报地址",
    "帮我更换视频源接口，从 https://old.example/vod/ 换到最新主接口",
    "帮我更换视频源接口 https://new.example/vod/ 或 https://other.example/vod/",
    "帮我把视频源改成 https://name:secret@source.example/vod/",
    "帮我把视频源改成 https://source.example/vod/?token=secret",
    "帮我把视频源改成 https://source.example/token/secret/",
    "帮我把视频源改成 https://source.example/vod/#secret",
    "帮我把量子资源接口改成 https://lzizy.net/index.php/macrep.html",
    "帮我把视频源改成 https://source.example/api.php/provide/vod/at/xml/",
    "帮我把视频源改成 http://127.0.0.1:8000/",
    "帮我把视频源改成 http://router.lan/catalog",
    "帮我把视频源改成 http://localhost/catalog",
    "帮我把视频源改成 ftp://source.example/vod/", "帮我把视频源改成site.example/vod/",
    "帮我把视频源改成 https://source.example/vod/ 密码是测试密码",
    "先别改量子资源接口",
])
def test_source_change_ambiguity_and_invalid_addresses_stay_local(client, monkeypatch, source_request):
    monkeypatch.setattr(assistant, "call_json", lambda *args: pytest.fail("private or ambiguous request reached model"))
    result = ask(client, source_request, section="sec-online").json()
    assert result["mode"] == "local" and result["suggestion"] is None
    assert all(value not in str(result) for value in ("source.example", "127.0.0.1", "secret", "测试密码"))


def test_source_questions_and_model_suggestions_cannot_open_source_change(client, monkeypatch):
    sent = mock_model(monkeypatch, "online-source-migration",
                      {"kind": "online_source", "template_id": "liangzi", "api_base_url": "https://invented.example/"})
    for question in ("量子接口是多少？", "怎么更换量子资源接口？", "可以更新接口吗？",
                     "类似修改资源接口的操作小映可以帮忙操作吗？", "帮我改一下设置"):
        result = ask(client, question).json()
        assert result["suggestion"] is None and "invented.example" not in str(result)
    assert sent
