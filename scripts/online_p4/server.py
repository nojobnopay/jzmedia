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

from app.routers.stream.media import stream_client_check, stream_client_info
from app.store import online, online_network
from scripts.online_p3.server import FIXTURE_TOKEN, isolated_fixture as state_fixture
from scripts.online_p4.fixtures import MARKER, fixture_root, prepare_media


__all__ = ["FIXTURE_TOKEN", "isolated_fixture", "main"]


@contextmanager
def isolated_fixture(value):
    root = fixture_root(value, require_media=True)
    with state_fixture(root) as app:
        app.title = "jzmedia isolated P4 native TV fixture"
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
    parser.add_argument("--cleanup", action="store_true", help="remove only the marked P4 temporary root on exit")
    args = parser.parse_args()
    try:
        root = fixture_root(args.root)
        if args.generate_media:
            prepare_media(root)
            print(f"P4 synthetic media prepared: {root}", flush=True)
            return
        if not 1024 <= args.port <= 65535:
            parser.error("port must be between 1024 and 65535")
        import uvicorn
        try:
            with isolated_fixture(root) as app:
                print(f"P4 isolated TV fixture: http://127.0.0.1:{args.port}; root={root}", flush=True)
                print(f"Fixture-only access token: {FIXTURE_TOKEN}; synthetic MacCMS is enabled.", flush=True)
                uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
        finally:
            if args.cleanup and (root / MARKER).is_file():
                shutil.rmtree(fixture_root(root, require_media=True))
    except ValueError as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
