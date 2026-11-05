"""Isolated persistent download/queue tests, with no upstream or NAS access."""

from contextlib import contextmanager
from types import SimpleNamespace
import threading

import pytest

from app import store
from app.online import cache, cache_download, cache_files, hls, providers
from app.network.client import NetworkRequestError
from app.store import _base, online, online_cache as ledger


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(_base, "DB_PATH", str(tmp_path / "cache.db"))
    monkeypatch.setattr(_base, "ensure_dirs", lambda: None)
    monkeypatch.setenv("ONLINE_CACHE_DIR", str(tmp_path / "packages"))
    monkeypatch.setenv("ONLINE_CACHE_MIN_FREE_GB", "0")
    monkeypatch.setattr(cache, "_stopping", False)
    store.init_db()
    online.update_online_settings(True)
    monkeypatch.setattr(cache_download.Download, "probe", lambda self, expected: expected or 12.0)
    yield
    assert not cache._active


def task(transport="mp4"):
    source = online.create_online_source({"name": "Source", "provider": "maccms", "enabled": True,
                                         "api_base_url": "https://cache.example.invalid/api/"})
    item = online.upsert_online_item(source["id"], "100", "tv", "Show")
    episode = online.upsert_online_episode(item["id"], "first", "Episode 1")
    identity = {"source_id": source["id"], "item_id": item["id"], "kind": "episode",
                "episode_id": episode["id"], "line_id": "ln_" + "1" * 32}
    detail = {"source": {"id": source["id"], "name": "Source"}, "item": {"id": item["id"], "title": "Show"},
              "poster_path": "", "lines": []}
    value = {"id": "oc_" + "a" * 32, "identity": identity, "label": "Episode 1", "line_label": "Line",
             "state": "queued", "transport": transport, "detail": detail, "external_id": "100",
             "source_name": "Source", "downloaded_bytes": 0, "total_bytes": None, "completed_resources": 0,
             "total_resources": 0, "speed_bytes_per_second": 0, "error_code": "", "duration_seconds": None,
             "package": {}}
    return ledger.insert(value, cache._key(identity))


class Response:
    def __init__(self, url, body, status=200, headers=None):
        self.url, self.body, self.status_code = url, body, status
        self.headers = {"content-length": str(len(body)), **(headers or {})}

    def iter_raw(self, chunk_size=65536):
        for offset in range(0, len(self.body), chunk_size):
            yield self.body[offset:offset + chunk_size]


class Client:
    def __init__(self, data):
        self.data, self.calls, self.closed = data, [], False

    @contextmanager
    def guarded_stream(self, method, url, operation, headers=None):
        operation.check()
        self.calls.append((url, headers or {}))
        body = self.data[url]
        if isinstance(body, Exception):
            raise body
        if headers and "Range" in headers:
            start, end = map(int, headers["Range"].removeprefix("bytes=").split("-"))
            end = min(end, len(body) - 1)
            yield Response(url, body[start:end + 1], 206,
                           {"content-range": f"bytes {start}-{end}/{len(body)}", "etag": '"revision1"'})
        else:
            yield Response(url, body)

    def close(self):
        self.closed = True


class EntityClient(Client):
    def __init__(self, data, *, etag='"version1"', head="valid"):
        super().__init__(data)
        self.etag, self.head = etag, head
        self.etags = {}
        self.requests = []
        self.response_origin = "https://cache.example.invalid"

    @contextmanager
    def guarded_stream(self, method, url, operation, headers=None):
        self.requests.append((method, url, headers or {}))
        etag = self.etags.get(url, self.etag)
        if method == "HEAD":
            operation.check()
            if self.head == "disconnect":
                raise NetworkRequestError("network_request_failed")
            response = Response(url, b"", 405 if self.head == "unsupported" else 200,
                                {"content-length": str(len(self.data[url]))})
        else:
            with super().guarded_stream(method, url, operation, headers=headers) as response:
                if etag:
                    response.headers["etag"] = etag
                else:
                    response.headers.pop("etag", None)
                if not url.endswith(".m3u8"):
                    response.url = url.replace("https://cache.example.invalid", self.response_origin)
                yield response
            return
        if etag and self.head == "valid":
            response.headers["etag"] = etag
        if not url.endswith(".m3u8"):
            response.url = url.replace("https://cache.example.invalid", self.response_origin)
        yield response


def install(monkeypatch, row, data, *, url="https://cache.example.invalid/entry.mp4", transport="mp4"):
    client = Client(data)
    monkeypatch.setattr(providers, "resolve", lambda identity: providers.ResolvedPlayback(url, transport))
    monkeypatch.setattr(cache_download, "create_online_client", lambda *args, **kwargs: client)
    return client


def interrupted_hls(monkeypatch, *, etag='"version1"', head="valid", byte_ranges=False):
    """Persist one resource, then interrupt before the other starts writing."""
    row = task("hls")
    url = "https://cache.example.invalid/entry.m3u8"
    first, second = ("https://cache.example.invalid/" + name for name in ("one.ts", "two.ts"))
    playlist = b"#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXTINF:6,\none.ts\n#EXTINF:6,\ntwo.ts\n#EXT-X-ENDLIST\n"
    data = {url: playlist, first: b"old-first", second: b"old-second"}
    if byte_ranges:
        first = second = "https://cache.example.invalid/all.mp4"
        data = {url: (b"#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXTINF:6,\n#EXT-X-BYTERANGE:4@0\nall.mp4\n"
                      b"#EXTINF:6,\n#EXT-X-BYTERANGE:4@4\nall.mp4\n#EXT-X-ENDLIST\n"), first: b"old1old2"}
    install(monkeypatch, row, data, url=url, transport="hls")
    client = EntityClient(data, etag=etag, head=head)
    monkeypatch.setattr(cache_download, "create_online_client", lambda *args, **kwargs: client)
    completed = threading.Event()
    interrupted = {"enabled": True}
    normal = cache_download.Download.download_resource

    def download(work, resource):
        is_second = resource.get("byte_range") == (4, 7) if byte_ranges else resource["url"] == second
        if is_second and interrupted["enabled"]:
            assert completed.wait(3)
            raise cache.CacheError("online_cache_network_failed", 502)
        normal(work, resource)
        if not is_second:
            completed.set()

    monkeypatch.setattr(cache_download.Download, "download_resource", download)
    assert cache.run_next()
    assert ledger.get(row["id"])["state"] == "failed"
    assert len(ledger.get(row["id"])["package"]["records"]) == 1
    interrupted["enabled"] = False
    return row, client, first, second


def test_complete_mp4_is_durable_and_lookup_never_resolves(monkeypatch):
    row = task()
    body = b"synthetic" * 20000
    client = install(monkeypatch, row, {"https://cache.example.invalid/entry.mp4": body})
    assert cache.run_next()
    ready = ledger.get(row["id"])
    assert ready["state"] == "ready"
    assert ready["downloaded_bytes"] == ready["total_bytes"] == len(body)
    assert ready["completed_resources"] == ready["total_resources"] == 1
    assert client.closed
    monkeypatch.setattr(providers, "resolve", lambda *args: pytest.fail("local lookup requested upstream"))
    found = cache.lookup(row["identity"])
    assert found["cache_id"] == row["id"]
    assert cache.asset_path(row["id"], "entry.mp4").read_bytes() == body
    assert cache.package_files(row["id"])["files"] == ["entry.mp4"]
    assert cache.item_detail(row["identity"]["item_id"])["external_id"] == "100"
    assert cache.list_items()["items"][0]["ready_count"] == 1


def test_changed_local_file_fails_closed_and_retry_does_not_silently_network(monkeypatch):
    row = task()
    install(monkeypatch, row, {"https://cache.example.invalid/entry.mp4": b"saved bytes"})
    cache.run_next()
    cache.asset_path(row["id"], "entry.mp4").write_bytes(b"broken")
    with pytest.raises(cache.CacheError, match="online_cache_corrupt"):
        cache.lookup(row["identity"])
    assert ledger.get(row["id"])["state"] == "failed"
    with pytest.raises(cache.CacheError, match="online_cache_corrupt"):
        cache.lookup(row["identity"])


def test_hls_preserves_every_required_resource_and_ranges(monkeypatch):
    row = task("hls")
    url = "https://cache.example.invalid/master.m3u8"
    master = (b'#EXTM3U\n#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="a",NAME="Audio",URI="audio.m3u8"\n'
              b'#EXT-X-STREAM-INF:BANDWIDTH=100\nlow.m3u8\n'
              b'#EXT-X-STREAM-INF:BANDWIDTH=200,AUDIO="a"\nhigh.m3u8\n')
    media = (b'#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXT-X-MAP:URI="all.mp4",BYTERANGE="4@2"\n'
             b'#EXTINF:6,\n#EXT-X-BYTERANGE:4@6\nall.mp4\n#EXT-X-DISCONTINUITY\n'
             b'#EXT-X-MAP:URI="init.mp4"\n#EXTINF:6,\nsecond.m4s\n#EXT-X-ENDLIST\n')
    audio = b"#EXTM3U\n#EXT-X-TARGETDURATION:12\n#EXTINF:12,\naudio.aac\n#EXT-X-ENDLIST\n"
    data = {url: master, "https://cache.example.invalid/high.m3u8": media,
            "https://cache.example.invalid/audio.m3u8": audio,
            "https://cache.example.invalid/all.mp4": b"0123456789abcdef",
            "https://cache.example.invalid/init.mp4": b"init", "https://cache.example.invalid/second.m4s": b"second",
            "https://cache.example.invalid/audio.aac": b"audio"}
    client = install(monkeypatch, row, data, url=url, transport="hls")
    assert cache.run_next()
    ready = ledger.get(row["id"])
    assert ready["state"] == "ready", ready.get("error_code")
    assert ready["total_resources"] == ready["completed_resources"] == 5
    package = cache.package_files(row["id"])
    local = [cache.asset_path(row["id"], name).read_bytes() for name in package["files"]]
    assert b"2345" in local and b"6789" in local
    assert any(b'BYTERANGE="4@0"' in body and b"#EXT-X-BYTERANGE:4@0" in body for body in local)
    assert not any("low.m3u8" in called for called, _headers in client.calls)
    assert all(b"https://" not in body for body in local if body.startswith(b"#EXTM3U"))


def test_missing_hls_fragment_fails_and_does_not_publish(monkeypatch):
    row = task("hls")
    url = "https://cache.example.invalid/entry.m3u8"
    playlist = b"#EXTM3U\n#EXT-X-TARGETDURATION:12\n#EXTINF:12,\nmissing.ts\n#EXT-X-ENDLIST\n"
    install(monkeypatch, row, {url: playlist, "https://cache.example.invalid/missing.ts": OSError()},
            url=url, transport="hls")
    cache.run_next()
    assert ledger.get(row["id"])["state"] == "failed"
    with pytest.raises(cache.CacheError, match="online_cache_not_ready"):
        cache.package_files(row["id"])


def test_hls_resume_restarts_whole_package_when_same_urls_and_manifest_have_new_entities(monkeypatch):
    row, client, first, second = interrupted_hls(monkeypatch)
    client.data.update({first: b"new-first", second: b"new-second"})
    client.etag = '"version2"'
    resumed = len(client.requests)
    cache.action(row["id"], "retry")
    assert cache.run_next()
    ready = ledger.get(row["id"])
    assert ready["state"] == "ready"
    assert [cache_files.path(row["id"], name).read_bytes() for name in sorted(ready["package"]["records"])
            if name.endswith(".ts")] == [b"new-first", b"new-second"]
    resumed_requests = client.requests[resumed:]
    assert resumed_requests.index(("HEAD", first, {})) < resumed_requests.index(("GET", second, {}))


@pytest.mark.parametrize("etag", [None, 'W/"version1"'])
def test_hls_resume_without_strong_validator_restarts_whole_package(monkeypatch, etag):
    row, client, first, second = interrupted_hls(monkeypatch, etag=etag)
    client.data.update({first: b"new-first", second: b"new-second"})
    cache.action(row["id"], "resume")
    assert cache.run_next()
    assert ledger.get(row["id"])["state"] == "ready"
    assert sum(method == "GET" and url == first for method, url, _headers in client.requests) == 2
    assert cache_files.path(row["id"], "resource_00000.ts").read_bytes() == b"new-first"


@pytest.mark.parametrize("head", ["valid", "unsupported", "missing-validator", "disconnect"])
def test_hls_resume_with_same_strong_entity_reuses_completed_fragment(monkeypatch, head):
    row, client, first, second = interrupted_hls(monkeypatch, head=head)
    cache.action(row["id"], "resume")
    assert cache.run_next()
    ready = ledger.get(row["id"])
    assert ready["state"] == "ready"
    checkpoint = ready["package"]["records"]["resource_00000.ts"]["upstream"]
    assert checkpoint["etag"] == '"version1"' and checkpoint["total"] == len(b"old-first")
    assert checkpoint["byte_range"] is None
    assert len(checkpoint["url_hash"]) == len(checkpoint["response_url_hash"]) == 64
    assert sum(method == "GET" and url == first and not headers
               for method, url, headers in client.requests) == 1
    if head != "valid":
        assert ("GET", first, {"Range": "bytes=0-0"}) in client.requests
    assert client.requests.index(("HEAD", first, {})) < client.requests.index(("GET", second, {}))


@pytest.mark.parametrize("changed_total", [False, True])
def test_hls_range_resume_checks_full_entity_size_and_preserves_source_offsets(monkeypatch, changed_total):
    row, client, first, _second = interrupted_hls(monkeypatch, byte_ranges=True)
    record = ledger.get(row["id"])["package"]["records"]["resource_00000.mp4"]
    assert record["size"] == 4
    assert record["upstream"]["total"] == 8 and record["upstream"]["byte_range"] == [0, 3]
    if changed_total:
        client.data[first] = b"new1new2extra"
    cache.action(row["id"], "retry")
    assert cache.run_next()
    ready = ledger.get(row["id"])
    assert ready["state"] == "ready"
    assert sum(method == "GET" and headers == {"Range": "bytes=0-3"}
               for method, _url, headers in client.requests) == (2 if changed_total else 1)
    assert cache_files.path(row["id"], "resource_00000.mp4").read_bytes() == (b"new1" if changed_total else b"old1")
    assert ready["package"]["records"]["resource_00000.mp4"]["upstream"]["total"] == len(client.data[first])


def test_hls_resume_does_not_trust_same_etag_from_different_redirect_target(monkeypatch):
    row, client, first, second = interrupted_hls(monkeypatch)
    client.data.update({first: b"new-first", second: b"new-second"})
    client.response_origin = "https://another.example.invalid"
    cache.action(row["id"], "retry")
    assert cache.run_next()
    assert ledger.get(row["id"])["state"] == "ready"
    assert cache_files.path(row["id"], "resource_00000.ts").read_bytes() == b"new-first"


def test_hls_resume_checks_all_saved_entities_before_any_new_segment_download(monkeypatch):
    row = task("hls")
    url = "https://cache.example.invalid/entry.m3u8"
    parts = [f"https://cache.example.invalid/part{index}.ts" for index in range(3)]
    data = {url: b"#EXTM3U\n#EXT-X-TARGETDURATION:4\n" + b"".join(
        f"#EXTINF:4,\npart{index}.ts\n".encode() for index in range(3)) + b"#EXT-X-ENDLIST\n",
        **{part: b"old-part" for part in parts}}
    install(monkeypatch, row, data, url=url, transport="hls")
    client = EntityClient(data)
    monkeypatch.setattr(cache_download, "create_online_client", lambda *args, **kwargs: client)
    completed = [threading.Event(), threading.Event()]
    interrupted = {"enabled": True}
    normal = cache_download.Download.download_resource

    def download(work, resource):
        index = parts.index(resource["url"])
        if index == 2 and interrupted["enabled"]:
            assert all(event.wait(3) for event in completed)
            raise cache.CacheError("online_cache_network_failed", 502)
        normal(work, resource)
        if index < 2:
            completed[index].set()

    monkeypatch.setattr(cache_download.Download, "download_resource", download)
    assert cache.run_next()
    assert len(ledger.get(row["id"])["package"]["records"]) == 2
    interrupted["enabled"] = False
    client.etags[parts[1]] = '"version2"'
    client.data[parts[1]] = b"new-part"
    resumed = len(client.requests)
    cache.action(row["id"], "retry")
    assert cache.run_next()
    assert ledger.get(row["id"])["state"] == "ready"
    requests = client.requests[resumed:]
    assert requests.index(("HEAD", parts[1], {})) < min(
        index for index, (method, selected, _headers) in enumerate(requests) if method == "GET" and selected in parts)
    assert sum(method == "GET" and selected == parts[0] for method, selected, _headers in client.requests) == 2


def test_space_pressure_pauses_and_retains_partial_data(monkeypatch):
    row = task()
    install(monkeypatch, row, {"https://cache.example.invalid/entry.mp4": b"x" * 120000})
    monkeypatch.setattr(cache_download.shutil, "disk_usage", lambda path: SimpleNamespace(free=0, total=1))
    cache.run_next()
    paused = ledger.get(row["id"])
    assert paused["state"] == "paused" and paused["error_code"] == "online_cache_space_low"
    assert cache_files.path(row["id"], "entry.mp4.part").exists()
    assert cache.action(row["id"], "resume")["state"] == "queued"


def test_delete_is_idempotent_and_callback_precedes_removal(monkeypatch):
    row = task()
    cache_files.directory(row["id"], create=True)
    cache_files.atomic_bytes(row["id"], "entry.mp4", b"x")
    seen = []
    result = cache.delete([row["id"]], before_delete=lambda task_id: seen.append(cache_files.directory(task_id).exists()))
    assert seen == [True]
    assert result == {"results": [{"id": row["id"], "deleted": True, "error_code": ""}]}
    assert cache.delete([row["id"]]) == result
    assert ledger.get(row["id"]) is None


def test_pause_waits_for_active_writer_and_does_not_resurrect(monkeypatch):
    row = task()
    started = threading.Event()

    def run(work):
        started.set()
        while not work.cancelled.wait(.01):
            pass
        work.check()

    monkeypatch.setattr(cache.Download, "run", run)
    worker = threading.Thread(target=cache.run_next)
    worker.start()
    assert started.wait(2)
    assert cache.action(row["id"], "pause")["state"] == "paused"
    worker.join(2)
    assert not worker.is_alive()
    assert ledger.get(row["id"])["state"] == "paused"


@pytest.mark.parametrize("name", ["../outside.mp4", "/entry.mp4", "entry.mp4/other", "x\\entry.mp4"])
def test_rejects_path_traversal(name):
    row = task()
    cache_files.directory(row["id"], create=True)
    with pytest.raises(cache.CacheError):
        cache_files.path(row["id"], name, missing=True)


def test_rejects_symlink_files_and_symlink_task_directories(tmp_path):
    row = task()
    directory = cache_files.directory(row["id"], create=True)
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"untouched")
    (directory / "entry.mp4").symlink_to(outside)
    with pytest.raises(cache.CacheError):
        cache_files.open_file(row["id"], "entry.mp4", "wb")
    assert outside.read_bytes() == b"untouched"
    (directory / "entry.mp4").unlink()
    directory.rmdir()
    directory.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(cache.CacheError):
        cache_files.directory(row["id"])


def test_recovery_only_requeues_previously_running_tasks():
    row = task()
    ledger.update(row["id"], state="downloading")
    ledger.recover()
    assert ledger.get(row["id"])["state"] == "queued"
    ledger.update(row["id"], state="paused")
    ledger.recover()
    assert ledger.get(row["id"])["state"] == "paused"


def test_offline_manifest_rejects_source_gap():
    body = b"#EXTM3U\n#EXT-X-TARGETDURATION:12\n#EXT-X-GAP\n#EXTINF:12,\nmissing.ts\n#EXT-X-ENDLIST\n"
    with pytest.raises(hls.ManifestError, match="incomplete_hls_playlist"):
        hls.offline_manifest(body, "https://cache.example.invalid/entry.m3u8", lambda *args, **kwargs: "one.ts")


def test_ledger_never_persists_signed_playback_url(monkeypatch):
    row = task()
    url = "https://cache.example.invalid/entry.mp4?signature=private"
    install(monkeypatch, row, {url: b"test bytes"}, url=url)
    cache.run_next()
    with _base._conn() as connection:
        payload = connection.execute("SELECT payload FROM online_cache_tasks").fetchone()[0]
    assert "signature=private" not in payload and "https://cache.example.invalid/entry.mp4" not in payload


def test_operation_timeout_does_not_cancel_task_and_mp4_retries(monkeypatch):
    row = task()
    url = "https://cache.example.invalid/entry.mp4"
    client = install(monkeypatch, row, {url: b"video" * 1000})
    normal = client.guarded_stream
    failed = []

    @contextmanager
    def transient(method, url, operation, headers=None):
        if not failed:
            failed.append(True)
            operation.abort("operation_timeout")
            raise NetworkRequestError("operation_timeout")
        with normal(method, url, operation, headers=headers) as response:
            yield response

    monkeypatch.setattr(client, "guarded_stream", transient)
    cache.run_next()
    assert ledger.get(row["id"])["state"] == "ready"
    assert failed


def test_mp4_disconnect_resumes_only_under_same_entity_validator(monkeypatch):
    row = task()
    url = "https://cache.example.invalid/entry.mp4"
    body = b"x" * 200000
    client = install(monkeypatch, row, {url: body})
    normal = client.guarded_stream
    dropped = []

    @contextmanager
    def transient(method, url, operation, headers=None):
        with normal(method, url, operation, headers=headers) as response:
            if headers.get("Range") != "bytes=0-0" and not dropped:
                dropped.append(True)

                def iter_raw(chunk_size=65536):
                    yield response.body[:chunk_size]
                    raise NetworkRequestError("network_request_failed")

                response.iter_raw = iter_raw
            yield response

    monkeypatch.setattr(client, "guarded_stream", transient)
    cache.run_next()
    assert ledger.get(row["id"])["state"] == "ready"
    assert cache.asset_path(row["id"], "entry.mp4").read_bytes() == body
    assert any(headers.get("Range") == "bytes=65536-199999" and headers.get("If-Range") == '"revision1"'
               for _url, headers in client.calls)


def test_expired_signature_refreshes_once_without_persisting_urls(monkeypatch):
    row = task()
    old, fresh = "https://cache.example.invalid/old.mp4", "https://cache.example.invalid/fresh.mp4"
    client = install(monkeypatch, row, {fresh: b"fresh media"})
    resolved = []

    def resolve(identity):
        resolved.append(True)
        return providers.ResolvedPlayback(old if len(resolved) == 1 else fresh, "mp4")

    monkeypatch.setattr(providers, "resolve", resolve)
    normal = client.guarded_stream

    @contextmanager
    def expired(method, url, operation, headers=None):
        if url == old:
            yield Response(url, b"", 403)
        else:
            with normal(method, url, operation, headers=headers) as response:
                yield response

    monkeypatch.setattr(client, "guarded_stream", expired)
    cache.run_next()
    assert ledger.get(row["id"])["state"] == "ready"
    assert len(resolved) == 2


@pytest.mark.parametrize(("count", "ready"), [(3, True), (4, False)])
def test_hls_inventory_reserves_one_resource_for_entry(monkeypatch, count, ready):
    row = task("hls")
    url = "https://cache.example.invalid/entry.m3u8"
    playlist = b"#EXTM3U\n#EXT-X-TARGETDURATION:1\n" + b"".join(
        f"#EXTINF:1,\npart{index}.ts\n".encode() for index in range(count)) + b"#EXT-X-ENDLIST\n"
    data = {url: playlist, **{f"https://cache.example.invalid/part{index}.ts": b"data" for index in range(count)}}
    install(monkeypatch, row, data, url=url, transport="hls")
    monkeypatch.setattr(hls, "REFERENCE_LIMIT", 4)
    cache.run_next()
    assert (ledger.get(row["id"])["state"] == "ready") is ready


def test_resource_concurrency_is_four_and_all_segments_are_required(monkeypatch):
    row = task("hls")
    url = "https://cache.example.invalid/entry.m3u8"
    playlist = b"#EXTM3U\n#EXT-X-TARGETDURATION:1\n" + b"".join(
        f"#EXTINF:1,\npart{index}.ts\n".encode() for index in range(8)) + b"#EXT-X-ENDLIST\n"
    client = install(monkeypatch, row, {url: playlist, **{
        f"https://cache.example.invalid/part{index}.ts": b"segment" for index in range(8)}}, url=url, transport="hls")
    normal = client.guarded_stream
    lock, four_started = threading.Lock(), threading.Event()
    counts = {"active": 0, "maximum": 0}

    @contextmanager
    def concurrent(method, url, operation, headers=None):
        if not url.endswith(".ts"):
            with normal(method, url, operation, headers=headers) as response:
                yield response
            return
        with lock:
            counts["active"] += 1
            counts["maximum"] = max(counts["maximum"], counts["active"])
            if counts["active"] == 4:
                four_started.set()
        try:
            assert four_started.wait(2)
            with normal(method, url, operation, headers=headers) as response:
                yield response
        finally:
            with lock:
                counts["active"] -= 1

    monkeypatch.setattr(client, "guarded_stream", concurrent)
    cache.run_next()
    assert ledger.get(row["id"])["state"] == "ready"
    assert counts["maximum"] == 4


def test_same_size_same_mtime_tampering_is_detected(monkeypatch):
    import os
    row = task()
    url = "https://cache.example.invalid/entry.mp4"
    install(monkeypatch, row, {url: b"original"})
    cache.run_next()
    path = cache.asset_path(row["id"], "entry.mp4")
    old = path.stat()
    path.write_bytes(b"modified")
    os.utime(path, ns=(old.st_atime_ns, old.st_mtime_ns))
    with pytest.raises(cache.CacheError, match="online_cache_corrupt"):
        cache.lookup(row["identity"])


def test_worker_compare_and_swap_cannot_override_user_pause():
    row = task()
    ledger.update(row["id"], state="paused")
    ledger.update(row["id"], expected_state="downloading", state="ready")
    assert ledger.get(row["id"])["state"] == "paused"


def test_ready_lookup_uses_recorded_identity_without_reading_entire_movie(monkeypatch):
    row = task()
    url = "https://cache.example.invalid/entry.mp4"
    install(monkeypatch, row, {url: b"unchanged media"})
    cache.run_next()
    monkeypatch.setattr(cache_files, "signature", lambda *args: pytest.fail("first playback rehashed complete movie"))
    assert cache.lookup(row["identity"])["cache_id"] == row["id"]


def test_mount_disappearance_never_creates_another_directory_and_recovers(monkeypatch, tmp_path):
    row = task()
    row = ledger.update(row["id"], storage_identity=cache_files.identity())
    url = "https://cache.example.invalid/entry.mp4"
    install(monkeypatch, row, {url: b"complete media"})
    cache.run_next()
    root = cache_files.root()
    moved = tmp_path / "temporarily-unmounted"
    root.rename(moved)
    with pytest.raises(cache.CacheError, match="online_cache_storage_unavailable") as error:
        cache.lookup(row["identity"])
    assert error.value.status == 503
    assert not root.exists()
    assert ledger.get(row["id"])["state"] == "ready"
    assert cache.list_items()["storage"]["ok"] is False
    assert not root.exists()
    moved.rename(root)
    assert cache.lookup(row["identity"])["cache_id"] == row["id"]


def test_marker_survives_service_restart_and_explicit_directory_migration(monkeypatch, tmp_path):
    import shutil
    row = task()
    row = ledger.update(row["id"], storage_identity=cache_files.identity())
    url = "https://cache.example.invalid/entry.mp4"
    install(monkeypatch, row, {url: b"migrated media"})
    cache.run_next()
    original = cache_files.root()
    migrated = tmp_path / "new-disk"
    shutil.copytree(original, migrated)
    monkeypatch.setenv("ONLINE_CACHE_DIR", str(migrated))
    assert cache_files.identity() == row["storage_identity"]
    assert cache.lookup(row["identity"])["cache_id"] == row["id"]


def test_missing_marker_is_not_reinitialized_for_an_existing_task(monkeypatch):
    row = task()
    row = ledger.update(row["id"], storage_identity=cache_files.identity())
    root = cache_files.root()
    (root / ".cache-identity").unlink()
    cache_files._roots.pop(str(root), None)  # simulate a fresh process
    with pytest.raises(cache.CacheError, match="online_cache_storage_unavailable"):
        cache_files.verify_identity(row["storage_identity"])
    assert not (root / ".cache-identity").exists()


def test_probe_failed_mp4_retry_fetches_new_entity(monkeypatch):
    row = task()
    old = "https://cache.example.invalid/entry.mp4"
    install(monkeypatch, row, {old: b"invalid movie"})

    def fail_probe(work, expected):
        raise cache.CacheError("online_cache_probe_failed", 422)

    monkeypatch.setattr(cache_download.Download, "probe", fail_probe)
    cache.run_next()
    assert ledger.get(row["id"])["error_code"] == "online_cache_probe_failed"
    fresh = "https://cache.example.invalid/repaired.mp4"
    install(monkeypatch, row, {fresh: b"repaired movie"}, url=fresh)
    monkeypatch.setattr(cache_download.Download, "probe", lambda *args: 12.)
    cache.action(row["id"], "retry")
    cache.run_next()
    assert cache.asset_path(row["id"], "entry.mp4").read_bytes() == b"repaired movie"


def test_probe_failed_hls_retry_redownloads_even_when_manifest_is_unchanged(monkeypatch):
    row = task("hls")
    url = "https://cache.example.invalid/entry.m3u8"
    part = "https://cache.example.invalid/one.ts"
    playlist = b"#EXTM3U\n#EXT-X-TARGETDURATION:12\n#EXTINF:12,\none.ts\n#EXT-X-ENDLIST\n"
    client = install(monkeypatch, row, {url: playlist, part: b"broken"}, url=url, transport="hls")

    def fail_probe(work, expected):
        raise cache.CacheError("online_cache_probe_failed", 422)

    monkeypatch.setattr(cache_download.Download, "probe", fail_probe)
    cache.run_next()
    client.data[part] = b"repaired"
    monkeypatch.setattr(cache_download.Download, "probe", lambda *args: 12.)
    cache.action(row["id"], "retry")
    cache.run_next()
    assert ledger.get(row["id"])["state"] == "ready"
    assert sum(called == part for called, _headers in client.calls) == 2


def test_pause_during_verification_rechecks_complete_mp4_entity_before_reuse(monkeypatch):
    row = task()
    old = "https://cache.example.invalid/entry.mp4"
    install(monkeypatch, row, {old: b"old video"})
    verifying = threading.Event()

    def wait_probe(work, expected):
        verifying.set()
        assert work.cancelled.wait(3)
        work.check()

    monkeypatch.setattr(cache_download.Download, "probe", wait_probe)
    worker = threading.Thread(target=cache.run_next)
    worker.start()
    assert verifying.wait(3)
    cache.action(row["id"], "pause")
    worker.join(3)
    fresh = "https://cache.example.invalid/updated.mp4"
    install(monkeypatch, row, {fresh: b"updated video"}, url=fresh)
    monkeypatch.setattr(cache_download.Download, "probe", lambda *args: 12.)
    cache.action(row["id"], "resume")
    cache.run_next()
    assert cache.asset_path(row["id"], "entry.mp4").read_bytes() == b"updated video"


def test_same_second_tasks_keep_enqueue_order_despite_inverse_random_ids(monkeypatch):
    monkeypatch.setattr(ledger.time, "time", lambda: 1000)
    first = task()
    second = {**first, "id": "oc_" + "0" * 32,
              "identity": {**first["identity"], "episode_id": "ep_" + "0" * 32}}
    ledger.insert(second, cache._key(second["identity"]))
    assert first["id"] > second["id"]
    assert [row["id"] for row in ledger.rows()] == [first["id"], second["id"]]


def test_snapshots_are_stored_once_and_unused_snapshots_are_removed():
    import json
    first = task()
    second = {**first, "id": "oc_" + "0" * 32,
              "identity": {**first["identity"], "episode_id": "ep_" + "0" * 32}}
    ledger.insert(second, cache._key(second["identity"]))
    with _base._conn() as connection:
        assert connection.execute("SELECT COUNT(*) FROM online_cache_snapshots").fetchone()[0] == 1
        payload = json.loads(connection.execute("SELECT payload FROM online_cache_tasks LIMIT 1").fetchone()[0])
        assert "detail" not in payload and payload["detail_id"]
    assert ledger.get(first["id"])["detail"] == first["detail"]
    ledger.remove(first["id"])
    with _base._conn() as connection:
        assert connection.execute("SELECT COUNT(*) FROM online_cache_snapshots").fetchone()[0] == 1
    ledger.remove(second["id"])
    with _base._conn() as connection:
        assert connection.execute("SELECT COUNT(*) FROM online_cache_snapshots").fetchone()[0] == 0


def test_root_identity_survives_deleting_last_task_and_blocks_wrong_disk_after_restart(tmp_path):
    row = task()
    cache_files.directory(row["id"], create=True)
    expected = cache._verify_root(initialize=True)
    cache.delete([row["id"]])
    assert not ledger.rows()
    assert ledger.root_identity() == expected
    root = cache_files.root()
    moved = tmp_path / "gone-volume"
    root.rename(moved)
    cache_files._roots.pop(str(root), None)
    assert not cache.storage_info()["ok"]
    assert not root.exists()
    with pytest.raises(cache.CacheError, match="online_cache_storage_unavailable"):
        cache._verify_root(initialize=True)
    assert not root.exists()
    moved.rename(root)
    assert cache._verify_root(initialize=True) == expected


def test_progress_poll_reuses_occupied_inventory_but_checks_free_space_live(monkeypatch):
    row = task()
    cache_files.directory(row["id"], create=True)
    cache_files.atomic_bytes(row["id"], "entry.mp4", b"saved bytes")
    count = {"path": 0, "free": 0}
    normal = cache_files.path

    def counted_path(*args, **kwargs):
        count["path"] += 1
        return normal(*args, **kwargs)

    def counted_free(path):
        count["free"] += 1
        return SimpleNamespace(free=1000 + count["free"], total=5000)

    monkeypatch.setattr(cache_files, "path", counted_path)
    monkeypatch.setattr(cache.shutil, "disk_usage", counted_free)
    first = cache.storage_info()
    stat_count = count["path"]
    second = cache.storage_info()
    assert first["used_bytes"] == second["used_bytes"] == len(b"saved bytes")
    assert count["path"] == stat_count and count["free"] == 2
    assert second["available_bytes"] == first["available_bytes"] + 1
    cache.delete([row["id"]])
    assert cache.storage_info()["used_bytes"] == 0
