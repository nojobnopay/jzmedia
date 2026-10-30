"""Serve a native TV preview with real online APIs and synthetic loopback media.

Generate media explicitly on the development host, then start the loopback-only
server. The preview has its own temporary database, fixed fixture-only token,
enabled synthetic MacCMS source and empty local-library responses. It never
starts the production application lifespan, scans media, or contacts a device.
"""

import argparse
from contextlib import contextmanager
from pathlib import Path
import shutil
import signal

from app.routers.stream.media import stream_client_check, stream_client_info
from app.store import online, online_network
from scripts.online_fixture.catalog import FIXTURE_TOKEN, _isolated_fixture
from scripts.online_fixture.catalog_origin import _prepare_media
from scripts.online_fixture.media import validate_fixture_root


__all__ = ["FIXTURE_TOKEN", "MARKER", "MARKER_TEXT", "ROOT_PREFIX", "fixture_root",
           "prepare_media", "isolated_fixture", "main"]

MARKER = ".online-tv-fixture"
MARKER_TEXT = "jzmedia online TV isolated fixture v1\n"
ROOT_PREFIX = "jzmedia-online-tv-"


def fixture_root(value, *, require_media=False):
    return validate_fixture_root(value, prefix=ROOT_PREFIX, marker=MARKER,
                                 marker_text=MARKER_TEXT, require_media=require_media)


def prepare_media(value):
    return _prepare_media(fixture_root(value), marker_name=MARKER, marker_text=MARKER_TEXT)


@contextmanager
def isolated_fixture(value):
    root = fixture_root(value, require_media=True)
    with _isolated_fixture(root, root_guard=fixture_root) as app:
        app.title = "jzmedia isolated native TV fixture"
        # These two read-only production functions retain the actual native
        # protocol/features, without mounting the local playback router.
        app.add_api_route("/api/stream/client-info", stream_client_info, methods=["GET"])
        app.add_api_route("/api/stream/client-check", stream_client_check, methods=["POST"])

        def empty_local_catalogue():
            return {"items": [], "total": 0, "offset": 0, "limit": 18, "synthetic": True}

        for path in ("/api/movies/recent-played", "/api/tv/recent-played", "/api/movies", "/api/tv/shows", "/api/collections", "/api/search"):
            app.add_api_route(path, empty_local_catalogue, methods=["GET"])

        def empty_local_facets():
            return {"genres": [], "regions": [], "years": [], "decades": [], "synthetic": True}

        for path in ("/api/facets", "/api/tv/facets"):
            app.add_api_route(path, empty_local_facets, methods=["GET"])

        origin = app.state.fixture.origin
        source = online.create_online_source({"name": "隔离合成MacCMS · TV验证", "provider": "maccms",
            "api_base_url": f"{origin.base_url}/provide/vod", "allowed_hosts": ["fixture.invalid"],
            "enabled": True, "priority": 0})
        online_network.update_source_network_policy(source["id"], {
            purpose: {"mode": "direct", "profile_id": None} for purpose in ("default", "api", "media")})
        online.update_online_settings(True)
        app.state.online_source = source
        yield app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--port", type=int, default=18194)
    parser.add_argument("--generate-media", action="store_true",
                        help="generate synthetic media on the development host, then exit")
    parser.add_argument("--cleanup", action="store_true", help="remove only the marked TV temporary root on exit")
    args = parser.parse_args()
    try:
        root = fixture_root(args.root)
        if args.generate_media:
            prepare_media(root)
            print(f"TV synthetic media prepared: {root}", flush=True)
            return
        if not 1024 <= args.port <= 65535:
            parser.error("port must be between 1024 and 65535")
        import uvicorn
        class FixtureServer(uvicorn.Server):
            @contextmanager
            def capture_signals(self):
                # Uvicorn re-raises captured signals after shutdown. This CLI
                # must unwind its isolated database and --cleanup root first.
                previous = {sig: signal.signal(sig, self.handle_exit)
                            for sig in (signal.SIGINT, signal.SIGTERM)}
                try:
                    yield
                finally:
                    for sig, handler in previous.items():
                        signal.signal(sig, handler)

        try:
            with isolated_fixture(root) as app:
                print(f"Isolated TV fixture: http://127.0.0.1:{args.port}; root={root}", flush=True)
                print(f"Fixture-only access token: {FIXTURE_TOKEN}; synthetic MacCMS is enabled.", flush=True)
                FixtureServer(uvicorn.Config(app, host="127.0.0.1", port=args.port, log_level="warning")).run()
        finally:
            if args.cleanup and (root / MARKER).is_file():
                shutil.rmtree(fixture_root(root, require_media=True))
    except ValueError as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
