"""An administrator can replace an unrelated catalogue while retaining old offline identities."""

from concurrent.futures import ThreadPoolExecutor

from app.online import cache, cache_download, maccms, providers
from app.online.models import OnlinePlaybackIdentity
from test_online_cache_core import Client
from test_online_state import HEADER, claim, environment as environment, save


def test_replacement_isolates_cache_favorites_and_progress_but_mirrors_keep_them(environment, monkeypatch, tmp_path):
    env = environment
    monkeypatch.setenv("ONLINE_CACHE_DIR", str(tmp_path / "packages"))
    monkeypatch.setenv("ONLINE_CACHE_MIN_FREE_GB", "0")
    monkeypatch.setattr(cache, "_stopping", False)
    monkeypatch.setattr(cache_download.Download, "probe", lambda self, expected: 30.0)
    old_url, new_url = "https://cdn.example/old.mp4", "https://cdn.example/new.mp4"
    old_media = b"first catalogue episode" * 100
    monkeypatch.setattr(cache_download, "create_online_client", lambda *_a, **_k: Client({old_url: old_media}))

    def fetch(source, params):
        assert params["ids"] == "show-1"
        new = source["api_base_url"] == "https://different.example/api/"
        return {"list": [{"vod_id": "show-1", "vod_name": "新剧" if new else "旧剧", "type_name": "国产剧",
                          "vod_play_from": "main", "vod_play_url": "第1集$" + (new_url if new else old_url)}]}

    monkeypatch.setattr(maccms, "fetch", fetch)
    client, source_id = env.client, env.source["id"]
    path = "/api/online/sources/" + source_id

    def detail(selected):
        result = client.get("/api/online/catalog/detail", params={"source_id": selected, "external_id": "show-1"})
        result.raise_for_status()
        return result.json()["lines"][0]["entries"][0]["identity"]

    old = detail(source_id)
    episode = {"id": old["episode_id"]}
    writer, _ = claim(env, item=env.show, episode=episode, line=old["line_id"])
    save(env, writer, position=17)
    client.put("/api/online/state/items/" + old["item_id"] + "/favorite", headers={HEADER: writer["scope_token"]},
               json={"favorite": True}).raise_for_status()
    client.post("/api/online/cache/tasks", json={"source_id": source_id, "item_id": old["item_id"],
                "line_id": old["line_id"], "episode_ids": [old["episode_id"]]}).raise_for_status()
    assert cache.run_next() and cache.lookup(old)
    # Existing clients patch the address when the catalogue and its numbering are unchanged.
    mirror = "https://mirror.example/api/"
    assert client.patch(path, json={"api_base_url": mirror}).json()["id"] == source_id
    assert detail(source_id) == old
    client.patch(path + "/network", json={"default": {"mode": "direct"}}).raise_for_status()

    draft = {"api_base_url": "https://different.example/api/", "allowed_hosts": ["cdn.example", "mirror.example"]}
    preview = client.post(path + "/host-migration-preview", json=draft).json()
    draft["host_migration"] = {"expected_fingerprint": preview["expected_fingerprint"],
                               "selected_old_hosts": ["mirror.example"]}
    with ThreadPoolExecutor(max_workers=2) as pool:
        requests = [pool.submit(client.post, path + "/replace-catalogue", json=draft) for _ in range(2)]
        result, duplicate = [request.result() for request in requests]
    result.raise_for_status()
    duplicate.raise_for_status()
    assert duplicate.json() == result.json()
    previous, new = result.json()["previous"], result.json()["source"]
    assert previous["id"] == source_id and previous["api_base_url"] == mirror and not previous["enabled"]
    assert previous["name"].endswith("（旧片库）") and new["id"] != source_id and new["enabled"]
    assert client.post(path + "/replace-catalogue", json={**draft, "name": "另一份草稿"}).status_code == 409
    assert len(client.get("/api/online/sources").json()["items"]) == 2
    assert client.get("/api/online/sources/" + new["id"] + "/network").json()["policy"]["default"]["mode"] == "direct"

    current = detail(new["id"])
    assert old["item_id"] != current["item_id"] and old["episode_id"] != current["episode_id"]
    assert providers.resolve(OnlinePlaybackIdentity.model_validate(current)).url == new_url
    lookup = client.get("/api/online/cache/lookup", params={"source_id": new["id"], "external_id": "show-1"})
    assert lookup.status_code == 404 and cache.lookup(current) is None
    saved = client.get("/api/online/state/library", params={"section": "favorites"}).json()["items"]
    assert len(saved) == 1 and saved[0]["item"]["title"] == "旧剧" and saved[0]["progress"]["position"] == 17
    assert saved[0]["available"] and not saved[0]["source"]["enabled"]  # This episode remains cached.
    state = client.get("/api/online/state/items/" + current["item_id"]).json()
    assert not state["favorite"] and state["last_progress"] is None
    assert client.get("/api/online/catalog/detail", params={"source_id": source_id, "external_id": "show-1"}).status_code != 200
    cached = client.post("/api/online/sessions", json=old, headers={"X-Online-Cache-Only": "true"})
    cached.raise_for_status()
    assert client.post(path + "/replace-catalogue", json=draft).json() == result.json()
    assert client.get(cached.json()["media_url"]).content == old_media
    client.patch("/api/online/sources/" + new["id"], json={"name": "已编辑的新来源"}).raise_for_status()
    assert client.post(path + "/replace-catalogue", json=draft).status_code == 409
    client.patch(path, json={"enabled": True}).raise_for_status()
    assert client.post(path + "/replace-catalogue", json=draft).status_code == 409
    assert detail(source_id) == old
    assert providers.resolve(OnlinePlaybackIdentity.model_validate(old)).url == old_url
