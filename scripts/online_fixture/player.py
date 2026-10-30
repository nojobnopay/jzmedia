"""Serve a temporary, loopback-only preview of the formal online player gateway.

Generate synthetic assets separately on the development machine:
  .venv/bin/python -m scripts.online_fixture.player --root /tmp/jzmedia-online-player-demo --generate-media
Then serve an isolated frontend build from that fixture's dist directory:
  .venv/bin/python -m scripts.online_fixture.player --root /tmp/jzmedia-online-player-demo --static-dir /tmp/jzmedia-online-player-demo/dist
No production application lifespan, media library, .env or source adapter runs.
"""

import argparse
from contextlib import ExitStack, asynccontextmanager, closing, contextmanager
from pathlib import Path
import shutil
import tempfile
import threading
import time
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from app import config, library_paths, secrets, store
from app.network.client import create_client
from app.network.policy import resolve_online_policy
from app.online import providers, runtime, service
from app.online.models import OnlinePlaybackIdentity
from app.routers import online, online_playback, viewing
from app.store import _base
from app.store import online as online_store
from app.viewing_http import middleware as viewing_middleware
from scripts.online_fixture.common import FixtureRegistry, FixtureTargetPolicy
from scripts.online_fixture.player_origin import DURATION, MARKER, FixtureOrigin, fixture_root, prepare_media


FIXTURE_TOKEN = "online-player-fixture-only"
MODES = {"normal", "provider_error", "unsupported_mp4", "bad_hls", "missing_media"}


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    delay_ms: int = Field(default=0, ge=0, le=2000)
    next_mode: str = "normal"
    expire_sessions: bool = False


class FixtureState:
    def __init__(self, origin):
        self.origin = origin
        self.registry = FixtureRegistry()
        self.items = []
        self.mapping = {}
        self.api_calls = []
        self.lock = threading.Lock()
        self.next_plan = (0, "normal")
        self.pending_resolutions = 0

    def seed(self):
        source = online_store.create_online_source({
            "name": "隔离合成来源", "provider": "open_catalog", "enabled": True,
            "api_base_url": "https://fixture.invalid/catalog/", "allowed_hosts": ["fixture.invalid"],
        })
        online_store.update_online_settings(True)
        self.source_id = source["id"]
        movie_specs = (("movie-mp4", "合成电影 · MP4", "mp4", "ep-1/movie.mp4"),
                       ("movie-hls", "合成电影 · HLS", "hls", "ep-1/hls/master.m3u8"))
        for key, title, transport, asset in movie_specs:
            item = online_store.upsert_online_item(self.source_id, key, "movie", title)
            identity = OnlinePlaybackIdentity(source_id=self.source_id, item_id=item["id"], kind="movie")
            self._add(key, title, identity, transport, asset)
        show = online_store.upsert_online_item(self.source_id, "show", "tv", "合成剧集")
        for number, transport in ((1, "mp4"), (2, "hls")):
            title = f"合成剧集 · 第 {number} 集"
            episode = online_store.upsert_online_episode(show["id"], f"ep-{number}", title,
                                                         season=1, episode=number, sort_order=number)
            identity = OnlinePlaybackIdentity(source_id=self.source_id, item_id=show["id"],
                                              episode_id=episode["id"], kind="episode")
            asset = f"ep-{number}/" + ("movie.mp4" if transport == "mp4" else "hls/master.m3u8")
            self._add(f"episode-{number}", title, identity, transport, asset)
        store.set_setting("jzmedia_token", FIXTURE_TOKEN)

    def _add(self, key, title, identity, transport, asset):
        self.items.append({"key": key, "title": title, "identity": identity.model_dump(mode="json"),
                           "transport": transport})
        self.mapping[(identity.item_id, identity.episode_id)] = (transport, asset)

    def resolve(self, identity):
        selected = self.mapping.get((identity.item_id, identity.episode_id))
        if identity.source_id != self.source_id or selected is None:
            raise providers.ProviderError("provider_resolution_failed")
        with self.lock:
            delay_ms, mode = self.next_plan
            self.next_plan = (0, "normal")
            self.pending_resolutions += 1
        try:
            if delay_ms:
                time.sleep(delay_ms / 1000)
            if mode == "provider_error":
                raise providers.ProviderError("provider_not_ready")
            transport, asset = selected
            if mode == "unsupported_mp4":
                transport, asset = "mp4", "scenarios/unsupported.mp4"
            elif mode == "bad_hls":
                transport, asset = "hls", "scenarios/encrypted.m3u8"
            elif mode == "missing_media":
                asset = "scenarios/missing.mp4" if transport == "mp4" else "scenarios/missing.m3u8"
            return providers.ResolvedPlayback(f"{self.origin.base_url}/files/{asset}?fixture_signature=server-only",
                                               transport, duration_seconds=DURATION)
        finally:
            with self.lock:
                self.pending_resolutions -= 1

    def client(self, source_id, purpose, *, header_policy=None):
        if source_id != self.source_id or purpose != "media":
            raise ValueError("unexpected fixture source or purpose")
        return create_client(resolve_online_policy(source_id, purpose),
                             targets=FixtureTargetPolicy(source_id, self.origin.port), header_policy=header_policy)

    def metrics(self):
        active = {session.id for session in self.registry.sessions()}
        created = self.registry.recorded_ids()
        with self.origin.lock:
            origin_calls = list(self.origin.calls)
        with self.lock:
            api_calls = list(self.api_calls)
            pending = self.pending_resolutions
        with closing(_base._conn()) as connection:
            counts = {table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                      for table in ("movies", "tv_episodes", "playback_progress")}
        return {"registry": self.registry.stats(), "origin_calls": origin_calls, "api_calls": api_calls,
                "created_sessions": list(created), "released_sessions": [sid for sid in created if sid not in active],
                "pending_resolutions": pending, "local_rows": counts}


@contextmanager
def isolated_fixture(value, *, static_dir=None):
    """Install process-local test seams, restoring them after the app stops."""
    root = fixture_root(value, require_media=True)
    static = Path(static_dir).resolve() if static_dir is not None else None
    if static is not None and (not static.is_dir() or not static.is_relative_to(root)):
        raise ValueError("static-dir must be an existing build inside the fixture root")
    with ExitStack() as stack:
        state_dir = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="state-", dir=root)))
        stack.enter_context(patch.object(_base, "DB_PATH", str(state_dir / "fixture.db")))
        stack.enter_context(patch.object(_base, "ensure_dirs", lambda: None))
        stack.enter_context(patch.object(config.settings, "data_dir", str(state_dir)))
        stack.enter_context(patch.object(config.settings, "media_root", str(state_dir / "unused-library")))
        stack.enter_context(patch.object(config.settings, "jzmedia_token", FIXTURE_TOKEN))
        stack.enter_context(patch.object(secrets, "_key_cache", None))
        library_paths.invalidate_cache()
        store.init_db()
        origin = stack.enter_context(FixtureOrigin(root / "media"))
        state = FixtureState(origin)
        state.seed()
        stack.enter_context(patch.object(service, "registry", state.registry))
        stack.enter_context(patch.object(providers, "resolve", state.resolve))
        stack.enter_context(patch.object(service, "create_online_client", state.client))

        @asynccontextmanager
        async def lifespan(_app):
            await runtime.start()
            try:
                yield
            finally:
                await runtime.shutdown()

        # Reuse application guards, without importing/running its lifespan.
        from app.main import _auth_write, _security_headers

        app = FastAPI(title="jzmedia isolated player fixture", lifespan=lifespan)
        app.state.fixture = state

        async def fixture_viewing(request, call_next):
            if request.url.path.startswith("/api/fixture/"):
                return await call_next(request)
            return await viewing_middleware(request, call_next)

        app.middleware("http")(fixture_viewing)
        app.middleware("http")(_auth_write)
        app.middleware("http")(_security_headers)

        @app.middleware("http")
        async def record_api(request, call_next):
            response = await call_next(request)
            if request.url.path.startswith("/api/"):
                with state.lock:
                    state.api_calls.append({"method": request.method, "path": request.url.path,
                                            "status": response.status_code})
                    del state.api_calls[:-2000]
            return response

        app.include_router(online_playback.router)
        app.include_router(online.router)
        app.include_router(viewing.router)

        @app.get("/api/fixture/health")
        def health():
            return {"ok": True, "synthetic": True, "phase": "online-player", "duration_seconds": DURATION}

        @app.get("/api/fixture/catalog")
        def catalog():
            return {"items": state.items, "api_token": FIXTURE_TOKEN}

        @app.get("/api/fixture/metrics")
        def metrics():
            return state.metrics()

        @app.post("/api/fixture/scenario")
        def scenario(body: Scenario):
            if body.next_mode not in MODES:
                from fastapi import HTTPException
                raise HTTPException(422, "unknown isolated scenario")
            with state.lock:
                state.next_plan = (body.delay_ms, body.next_mode)
            if body.expire_sessions:
                with state.registry._lock:
                    for session in state.registry._sessions.values():
                        session.expires_monotonic = time.monotonic() - 1
                state.registry.sweep()
            return {"ok": True}

        if static is not None:
            app.mount("/", StaticFiles(directory=static, html=True))
        try:
            yield app
        finally:
            state.registry.close_all()
            library_paths.invalidate_cache()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--port", type=int, default=18191)
    parser.add_argument("--static-dir", type=Path)
    parser.add_argument("--generate-media", action="store_true",
                        help="generate the synthetic media once on the development machine, then exit")
    parser.add_argument("--cleanup", action="store_true",
                        help="remove the marked temporary fixture root after the server stops")
    args = parser.parse_args()
    try:
        root = fixture_root(args.root)
        if args.generate_media:
            prepare_media(root)
            print(f"Player synthetic media prepared: {root}", flush=True)
            return
        if not 1024 <= args.port <= 65535:
            parser.error("port must be between 1024 and 65535")
        import uvicorn
        try:
            with isolated_fixture(root, static_dir=args.static_dir) as app:
                print(f"Player isolated fixture: http://127.0.0.1:{args.port}; root={root}", flush=True)
                uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
        finally:
            if args.cleanup and (root / MARKER).is_file():
                shutil.rmtree(fixture_root(root, require_media=True))
    except ValueError as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
