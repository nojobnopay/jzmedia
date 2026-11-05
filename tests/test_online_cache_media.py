"""Real synthetic media traverses guarded downloads, disk validation and local HTTP."""

import json
from pathlib import Path
import shutil
import subprocess
from urllib.parse import urlsplit

from fastapi.testclient import TestClient
import httpx
import pytest

from app import library_paths, media, store
from app.config import settings
from app.main import app
from app.network import client as network_client, targets
from app.online import cache, cache_download, catalog, providers, service
from app.online.sessions import SessionRegistry
from app.store import online, online_cache as ledger


@pytest.fixture(scope="module")
def clips(tmp_path_factory):
    binaries = media._static_bins_if_present()
    ffmpeg = shutil.which("ffmpeg") or binaries.get("ffmpeg")
    if not ffmpeg or not (shutil.which("ffprobe") or binaries.get("ffprobe")):
        pytest.skip("real synthetic cache media requires existing FFmpeg and FFprobe")
    root = tmp_path_factory.mktemp("cache-synthetic-media")

    def run(*args):
        subprocess.run([ffmpeg, "-v", "error", "-nostdin", "-y", *args], check=True,
                       capture_output=True, timeout=30)

    run("-f", "lavfi", "-i", "testsrc2=size=160x90:rate=12", "-f", "lavfi", "-i", "sine=frequency=440",
        "-t", "4", "-c:v", "libx264", "-threads", "1", "-g", "12", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-movflags", "+faststart", str(root / "entry.mp4"))
    for kind in ("ts", "fmp4"):
        directory = root / kind
        directory.mkdir()
        run("-i", str(root / "entry.mp4"), "-c", "copy", "-hls_time", "1", "-hls_playlist_type", "vod",
            "-hls_segment_type", "mpegts" if kind == "ts" else "fmp4", str(directory / "entry.m3u8"))
    return root, ffmpeg


@pytest.mark.parametrize("transport", ["mp4", "ts", "fmp4"])
def test_real_download_publish_disconnect_and_local_playback(transport, clips, tmp_path, monkeypatch):
    root, ffmpeg = clips
    source_root = root if transport == "mp4" else root / transport
    entry_name = "entry.mp4" if transport == "mp4" else "entry.m3u8"
    monkeypatch.setattr(store._base, "DB_PATH", str(tmp_path / "cache.db"))
    monkeypatch.setattr(store._base, "ensure_dirs", lambda: None)
    monkeypatch.setattr(settings, "data_dir", str(tmp_path / "data"))
    monkeypatch.setattr(settings, "media_root", str(tmp_path / "media"))
    monkeypatch.setenv("ONLINE_CACHE_DIR", str(tmp_path / "packages"))
    monkeypatch.setenv("ONLINE_CACHE_MIN_FREE_GB", "0")
    monkeypatch.setattr(cache, "_stopping", False)
    monkeypatch.setattr(service, "registry", SessionRegistry())
    library_paths.invalidate_cache()
    store.init_db()
    online.update_online_settings(True)
    source = online.create_online_source({"name": "Synthetic source", "provider": "maccms", "enabled": True,
                                         "api_base_url": "https://synthetic.example/api/"})
    item = online.upsert_online_item(source["id"], "fixture", "tv", "Synthetic cache show")
    episode = online.upsert_online_episode(item["id"], "first", "Episode 1")
    identity = {"source_id": source["id"], "item_id": item["id"], "kind": "episode",
                "episode_id": episode["id"], "line_id": "ln_" + "1" * 32}
    detail = {"source": {"id": source["id"], "name": source["name"], "provider": "maccms"},
              "item": {"id": item["id"], "source_id": source["id"], "kind": "tv", "title": item["title"]},
              "description": "Isolated generated media", "year": "", "poster_path": "", "attribution": [],
              "lines": [{"id": identity["line_id"], "label": "Line", "entries": [{"label": "Episode 1",
                "identity": identity, "transport": "mp4" if transport == "mp4" else "hls", "supported": True,
                "reason": ""}]}]}
    monkeypatch.setattr(catalog, "detail", lambda *_args, **_kwargs: detail)
    monkeypatch.setattr(providers, "resolve", lambda *_args: providers.ResolvedPlayback(
        "https://synthetic.example/" + entry_name, "mp4" if transport == "mp4" else "hls"))
    reads = []

    def serve(request):
        path = source_root / Path(urlsplit(str(request.url)).path).name
        assert path.is_relative_to(source_root) and request.url.host == "synthetic.example"
        reads.append(path.name)
        body = path.read_bytes()
        total = len(body)
        headers = {"Content-Length": str(total), "ETag": '"synthetic-revision"'}
        status = 200
        if request.headers.get("Range"):
            start, end = map(int, request.headers["Range"].removeprefix("bytes=").split("-"))
            end = min(end, total - 1)
            body = body[start:end + 1]
            headers.update({"Content-Range": f"bytes {start}-{end}/{total}", "Content-Length": str(len(body))})
            status = 206
        return httpx.Response(status, headers=headers, stream=httpx.ByteStream(body))

    monkeypatch.setattr(network_client, "PinnedTransport", lambda *_args: httpx.MockTransport(serve))
    monkeypatch.setattr(targets, "_system_addresses", lambda *_args: ("8.8.8.8",))
    client = TestClient(app)
    try:
        client.get("/api/viewing/session").raise_for_status()
        response = client.post("/api/online/cache/tasks", json={"source_id": source["id"], "item_id": item["id"],
            "line_id": identity["line_id"], "episode_ids": [episode["id"]]})
        assert response.status_code == 202, response.text
        cache_id = response.json()["tasks"][0]["id"]
        assert cache.run_next()
        saved = ledger.get(cache_id)
        assert saved["state"] == "ready", json.dumps(saved, ensure_ascii=False)
        assert reads and saved["duration_seconds"] >= 3.9
        package = cache.package_files(cache_id)
        subprocess.run([ffmpeg, "-v", "error", "-nostdin", "-protocol_whitelist", "file",
                        "-i", str(cache.asset_path(cache_id, package["entry_name"])), "-t", "1",
                        "-f", "null", "-"], capture_output=True, check=True, timeout=30)

        def forbidden(*_args, **_kwargs):
            pytest.fail("saved media attempted an upstream request after disconnect")

        monkeypatch.setattr(providers, "resolve", forbidden)
        monkeypatch.setattr(service, "create_online_client", forbidden)
        monkeypatch.setattr(cache_download, "create_online_client", forbidden)
        monkeypatch.setattr(catalog, "detail", forbidden)
        online.update_online_source(source["id"], {"enabled": False})
        count = len(reads)
        assert client.get("/api/online/cache/items").json()["items"][0]["ready_count"] == 1
        snapshot = client.get(f'/api/online/cache/items/{item["id"]}')
        assert snapshot.status_code == 200 and snapshot.json()["external_id"] == "fixture"
        response = client.post("/api/online/sessions", json=identity, headers={"X-Online-Cache-Only": "true"})
        assert response.status_code == 201, response.text
        descriptor = response.json()
        result = client.get(descriptor["media_url"])
        assert result.status_code == 200
        session = service.registry.sessions()[0]
        for resource in session._local_files.values():
            result = client.get(resource.path(session.id))
            assert result.status_code == 200
            if resource.kind == "manifest":
                assert "synthetic.example" not in result.text and "https://" not in result.text
            else:
                result = client.get(resource.path(session.id), headers={"Range": "bytes=0-7"})
                assert result.status_code == 206 and len(result.content) == 8
        assert len(reads) == count
        result = client.post("/api/online/cache/entries/delete", json={"ids": [cache_id]})
        assert result.status_code == 200 and result.json()["results"][0]["deleted"]
        assert client.get(descriptor["media_url"]).status_code == 404
    finally:
        service.registry.close_all()
        client.close()
        library_paths.invalidate_cache()
