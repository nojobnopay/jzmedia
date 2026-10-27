"""Disconnected create requests reclaim late sessions without idle expiry."""

import asyncio
from contextlib import closing
import json
import threading
from types import SimpleNamespace

import anyio
from fastapi import FastAPI
import pytest

from app import library_paths, store, viewing
from app.config import settings
from app.network.targets import TargetPolicy
from app.online import providers, service
from app.online.sessions import SessionRegistry
from app.routers import online_playback
from app.store import online


@pytest.fixture(params=[False, True], ids=["router", "base-http-middleware"])
def environment(request, tmp_path, monkeypatch):
    monkeypatch.setattr(store._base, "DB_PATH", str(tmp_path / "create-disconnect.db"))
    monkeypatch.setattr(store._base, "ensure_dirs", lambda: None)
    monkeypatch.setattr(settings, "data_dir", str(tmp_path / "data"))
    library_paths.invalidate_cache()
    store.init_db()
    registry = SessionRegistry()
    monkeypatch.setattr(service, "registry", registry)
    source = online.create_online_source({"name": "Isolated delayed provider", "provider": "maccms",
                                         "api_base_url": "https://source.example/api/",
                                         "allowed_hosts": ["cdn.example"], "enabled": True})
    item = online.upsert_online_item(source["id"], "delayed-movie", "movie", "Delayed movie")
    online.update_online_settings(True)
    viewer, secret, _expired = viewing.resolve(create=True)
    with closing(store._base._conn()) as connection:
        revision = connection.execute("SELECT revision FROM protection_settings WHERE id=1").fetchone()[0]
    access = service.PlaybackAccess(viewer["token_hash"], viewer["generation"], revision)
    identity = {"source_id": source["id"], "item_id": item["id"], "kind": "movie"}
    readers, created = [], []
    entered, release = threading.Event(), threading.Event()

    class FakeReader:
        def __init__(self):
            self.snapshot = SimpleNamespace(source_id=source["id"])
            self.targets = TargetPolicy(source["id"], ["cdn.example"],
                                        resolver=lambda _host, _port: ("8.8.8.8",))
            self.closed = threading.Event()

        def close(self):
            self.closed.set()

    def client_factory(source_id, purpose, *, header_policy=None):
        assert source_id == source["id"] and purpose == "media" and header_policy is None
        reader = FakeReader()
        readers.append(reader)
        return reader

    original_registry_create = registry.create

    def register(*args, **kwargs):
        session = original_registry_create(*args, **kwargs)
        created.append(session)
        return session

    monkeypatch.setattr(service, "create_online_client", client_factory)
    monkeypatch.setattr(registry, "create", register)
    resolved = providers.ResolvedPlayback("https://cdn.example/movie.mp4?signature=private-source-secret", "mp4")
    monkeypatch.setattr(providers, "resolve", lambda _identity: resolved)
    app = FastAPI()
    app.include_router(online_playback.router)
    if request.param:
        @app.middleware("http")
        async def passthrough(incoming, call_next):
            return await call_next(incoming)
    try:
        yield SimpleNamespace(app=app, registry=registry, identity=identity, access=access, secret=secret,
                              entered=entered, release=release, readers=readers, created=created, resolved=resolved)
    finally:
        release.set()
        registry.close_all()
        library_paths.invalidate_cache()


def request(environment, payload=None):
    body = json.dumps(environment.identity if payload is None else payload).encode()
    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
             "http_version": "1.1", "method": "POST", "scheme": "http", "path": "/api/online/sessions",
             "raw_path": b"/api/online/sessions", "query_string": b"", "root_path": "",
             "headers": [(b"content-type", b"application/json"),
                         (b"content-length", str(len(body)).encode()),
                         (b"x-viewing-session", environment.secret.encode())],
             "server": ("fixture", 80), "client": ("127.0.0.1", 12345), "state": {}}
    messages = []
    incoming = asyncio.Queue()
    incoming.put_nowait({"type": "http.request", "body": body, "more_body": False})

    async def receive():
        # Model ASGI receive wrappers that checkpoint before delivering even a
        # queued message. Starlette's pre-cancelled is_disconnected() polling
        # cannot see http.disconnect through this receive implementation.
        await anyio.lowlevel.checkpoint()
        return await incoming.get()

    async def send(message):
        messages.append(message)

    task = asyncio.create_task(environment.app(scope, receive, send))
    return task, incoming, messages


async def until(predicate, timeout=2):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(.01)
    return predicate()


@pytest.mark.parametrize("stage", ["provider", "registered"])
@pytest.mark.parametrize("cancellation", ["disconnect", "asgi_cancel"])
def test_late_creation_is_closed_after_request_disappears(environment, monkeypatch, stage, cancellation):
    def wait_for_release():
        environment.entered.set()
        if not environment.release.wait(timeout=3):
            raise RuntimeError("isolated delayed creation was not released")

    if stage == "provider":
        def resolve(_identity):
            wait_for_release()
            return environment.resolved
        monkeypatch.setattr(providers, "resolve", resolve)
    else:
        original_create = service.create

        def create(identity, access):
            session = original_create(identity, access)
            wait_for_release()
            return session
        monkeypatch.setattr(service, "create", create)

    async def scenario():
        task, incoming, messages = request(environment)
        try:
            assert await asyncio.to_thread(environment.entered.wait, 2)
            if stage == "registered":
                assert environment.registry.stats()["sessions"] == 1
            if cancellation == "disconnect":
                incoming.put_nowait({"type": "http.disconnect"})
                await asyncio.wait_for(task, timeout=2)
                assert [message["status"] for message in messages if message["type"] == "http.response.start"] == [410]
            else:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            assert not any(message.get("status") == 201 for message in messages)
            environment.release.set()
            # P2 cancels the entire creation network operation. A provider
            # that ignores cancellation cannot register a session afterwards;
            # an already registered result still has to be reclaimed.
            expected_created = 0 if stage == "provider" else 1
            assert await until(lambda: len(environment.created) == expected_created
                               and environment.registry.stats()["sessions"] == 0
                               and len(environment.readers) == 1
                               and all(reader.closed.is_set() for reader in environment.readers))
            assert len(environment.readers) == 1
            if environment.created:
                assert environment.created[0].cancelled.is_set()
            assert environment.registry.stats()["pending_sessions"] == 0
            assert environment.registry.stats()["active_requests"] == 0
            assert environment.registry.stats()["resources"] == 0
        finally:
            environment.release.set()
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    asyncio.run(scenario())


def test_normal_creation_commits_the_typed_descriptor_without_reclaiming_its_session(environment):
    async def scenario():
        task, _incoming, messages = request(environment)
        await asyncio.wait_for(task, timeout=2)
        assert [message["status"] for message in messages if message["type"] == "http.response.start"] == [201]
        body = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
        descriptor = json.loads(body)
        assert descriptor["processing"] == "passthrough"
        assert descriptor["identity"]["item_id"] == environment.identity["item_id"]
        assert descriptor["media_url"] == f"/api/online/sessions/{descriptor['session_id']}/media"
        assert "private-source-secret" not in body.decode()
        assert environment.registry.stats()["sessions"] == 1
        assert len(environment.readers) == 1 and not environment.readers[0].closed.is_set()

    asyncio.run(scenario())


def test_invalid_url_input_never_starts_a_creation_worker(environment):
    async def scenario():
        task, _incoming, messages = request(environment, {**environment.identity, "url": "https://private.invalid/secret"})
        await asyncio.wait_for(task, timeout=2)
        assert [message["status"] for message in messages if message["type"] == "http.response.start"] == [422]
        assert environment.created == environment.readers == []
        assert environment.registry.stats()["sessions"] == 0

    asyncio.run(scenario())
