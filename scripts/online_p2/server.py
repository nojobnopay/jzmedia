"""Serve the real P2 catalogue, source settings and player with isolated MacCMS.

Generate media explicitly on the development host before serving. No production
application lifespan, NAS, public provider, media library or .env is used.
"""

import argparse
from contextlib import ExitStack, asynccontextmanager, closing, contextmanager
import importlib
from pathlib import Path
import shutil
import tempfile
import threading
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.exception_handlers import http_exception_handler
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from app import config, library_paths, secrets, store
from app.network.client import create_client
from app.network.policy import NetworkPolicyError, resolve_policy_state
from app.online import catalog, runtime, service
from app.routers import online, online_network, online_playback, viewing
from app.store import _base
from app.store.online_network import load_online_network_state
from app.viewing_http import _error_response, middleware as viewing_middleware
from scripts.online_p1.server import FixtureRegistry, FixtureTargetPolicy
from scripts.online_p2.fixtures import DURATION, MARKER, MODES, FixtureOrigin, fixture_root, prepare_media


FIXTURE_TOKEN = "online-p2-fixture-only"


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    delay_ms: int = Field(default=0, ge=0, le=2000)
    next_mode: str = "normal"
    revision: int | None = Field(default=None, ge=1, le=1000)
    reverse_lines: bool | None = None
    expire_sessions: bool = False


class FixtureState:
    def __init__(self, origin):
        self.origin = origin
        self.registry = FixtureRegistry()
        self.lock = threading.Lock()
        self.api_calls, self.client_calls = [], []

    def client(self, source_id, purpose, *, header_policy=None):
        if purpose not in {"api", "media"}:
            raise ValueError("unexpected fixture network purpose")
        state = load_online_network_state(source_id)
        source = state["source_config"]
        if source["api_base_url"] != f"{self.origin.base_url}/provide/vod":
            raise ValueError("fixture clients may only use the exact isolated MacCMS source")
        if not state["online_enabled"] or not source["enabled"]:
            raise NetworkPolicyError("online_source_disabled")
        snapshot = resolve_policy_state(state, purpose)
        with self.lock:
            self.client_calls.append({"source_id": source_id, "purpose": purpose, "mode": snapshot.mode,
                                      "fingerprint": snapshot.fingerprint})
            del self.client_calls[:-2000]
        client = create_client(snapshot, targets=FixtureTargetPolicy(source_id, self.origin.port),
                               header_policy=header_policy)
        from app.online.health import bind_client
        bind_client(client, state)
        return client

    def metrics(self):
        active = {session.id for session in self.registry.sessions()}
        created = self.registry.recorded_ids()
        with self.origin.lock:
            origin_calls = list(self.origin.calls)
            pending = self.origin.pending_requests
        with self.lock:
            api_calls, clients = list(self.api_calls), list(self.client_calls)
        with closing(_base._conn()) as connection:
            local = {table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                     for table in ("movies", "tv_episodes", "playback_progress")}
            catalogue = {table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                         for table in ("online_sources", "online_items", "online_episodes")}
        with catalog._lock:
            catalogue_active, catalogue_owners = catalog._active, len(catalog._owners)
        return {"registry": self.registry.stats(), "catalogue_active": catalogue_active,
                "catalogue_owners": catalogue_owners, "origin_calls": origin_calls, "api_calls": api_calls,
                "client_calls": clients, "pending_requests": pending,
                "created_sessions": list(created), "released_sessions": [sid for sid in created if sid not in active],
                "local_rows": local, "catalog_rows": catalogue}


@contextmanager
def isolated_fixture(value, *, static_dir=None):
    root = fixture_root(value, require_media=True)
    static = Path(static_dir).resolve() if static_dir is not None else None
    if static is not None and (not static.is_dir() or not static.is_relative_to(root)):
        raise ValueError("static-dir must be an existing build inside the fixture root")
    # Import the production adapter here, after repository modules exist. No
    # provider result is monkeypatched: all identities pass through real JSON.
    maccms = importlib.import_module("app.online.maccms")
    catalogue_router = importlib.import_module("app.routers.online_catalog")
    state_router = importlib.import_module("app.routers.online_state")
    health_router = importlib.import_module("app.routers.online_health")
    health_module = importlib.import_module("app.online.health")
    health_module.registry.clear()
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
        store.set_setting("jzmedia_token", FIXTURE_TOKEN)
        stack.enter_context(patch.object(service, "registry", state.registry))
        stack.enter_context(patch.object(service, "create_online_client", state.client))
        stack.enter_context(patch.object(maccms, "create_online_client", state.client))

        @asynccontextmanager
        async def lifespan(_app):
            await runtime.start()
            try:
                yield
            finally:
                await runtime.shutdown()

        from app.main import _auth_write, _security_headers

        app = FastAPI(title="jzmedia isolated P2 catalogue fixture", lifespan=lifespan)
        app.state.fixture = state

        @app.exception_handler(HTTPException)
        async def protection_exception(request, exc):
            if isinstance(exc.detail, dict) and "code" in exc.detail:
                return _error_response(exc)
            return await http_exception_handler(request, exc)

        async def fixture_viewing(request, call_next):
            if request.url.path.startswith("/api/p2/"):
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
        app.include_router(catalogue_router.router)
        app.include_router(state_router.router)
        app.include_router(health_router.router)
        app.include_router(online.router)
        app.include_router(online_network.router)
        app.include_router(viewing.router)

        # App.vue's read-only navigation context uses these empty isolated
        # responses; no scanner, filesystem library or production API runs.
        @app.get("/api/libraries")
        def empty_libraries():
            return {"items": [], "default_id": None}

        @app.get("/api/media-libraries")
        def empty_media_libraries():
            return {"items": []}

        @app.get("/api/data-usage")
        def empty_data_usage():
            return {"over_limit": False, "total_bytes": 0, "warning_bytes": 0}

        @app.get("/favicon.svg")
        def favicon():
            return FileResponse(Path(__file__).resolve().parents[2] / "frontend/public/favicon.svg",
                                media_type="image/svg+xml")

        @app.get("/api/p2/health")
        def health():
            return {"ok": True, "synthetic": True, "phase": "online-p2-catalogue", "duration_seconds": DURATION}

        @app.get("/api/p2/bootstrap")
        def bootstrap():
            return {"api_token": FIXTURE_TOKEN, "api_base_url": f"{origin.base_url}/provide/vod",
                    "allowed_hosts": ["fixture.invalid"]}

        @app.get("/api/p2/metrics")
        def metrics():
            return state.metrics()

        @app.post("/api/p2/scenario")
        def scenario(body: Scenario):
            if body.next_mode not in MODES:
                raise HTTPException(422, "unknown isolated scenario")
            with origin.lock:
                origin.next_plan = (body.delay_ms, body.next_mode)
                if body.revision is not None:
                    origin.revision = body.revision
                if body.reverse_lines is not None:
                    origin.reverse_lines = body.reverse_lines
            if body.expire_sessions:
                state.registry.close_all()
            return {"ok": True}

        if static is not None:
            app.mount("/", StaticFiles(directory=static, html=True))
        try:
            yield app
        finally:
            state.registry.close_all()
            health_module.registry.clear()
            library_paths.invalidate_cache()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--port", type=int, default=18192)
    parser.add_argument("--static-dir", type=Path)
    parser.add_argument("--generate-media", action="store_true",
                        help="generate synthetic video on the development machine, then exit")
    parser.add_argument("--cleanup", action="store_true", help="remove only the marked temporary fixture root on exit")
    args = parser.parse_args()
    try:
        root = fixture_root(args.root)
        if args.generate_media:
            prepare_media(root)
            print(f"P2 synthetic media prepared: {root}", flush=True)
            return
        if not 1024 <= args.port <= 65535:
            parser.error("port must be between 1024 and 65535")
        import uvicorn
        try:
            with isolated_fixture(root, static_dir=args.static_dir) as app:
                print(f"P2 isolated fixture: http://127.0.0.1:{args.port}; root={root}", flush=True)
                uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
        finally:
            if args.cleanup and (root / MARKER).is_file():
                shutil.rmtree(fixture_root(root, require_media=True))
    except ValueError as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
