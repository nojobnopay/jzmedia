"""Run a loopback-only, synthetic online search/episode/playback prototype."""
import argparse
from contextlib import asynccontextmanager
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
import httpx

from scripts.online_prototype.core import Gateway, NetworkPolicy, SourceError
from scripts.online_prototype.fixtures import FixtureOrigin
from scripts.online_fixture.media import generate_media, guarded_path


class UpstreamStreamingResponse(StreamingResponse):
    """Own the upstream before ASGI starts, even if the body iterator never runs."""

    def __init__(self, upstream: httpx.Response, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.upstream = upstream

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            self.upstream.close()


def create_app(origin: FixtureOrigin, *, api_policy: NetworkPolicy | None = None,
               media_policy: NetworkPolicy | None = None, static_dir: Path | None = None) -> FastAPI:
    gateway = Gateway(origin, api_policy, media_policy)

    @asynccontextmanager
    async def lifespan(_app):
        yield
        gateway.close()

    app = FastAPI(title="jzmedia isolated online prototype", lifespan=lifespan)
    app.state.gateway = gateway

    @app.exception_handler(SourceError)
    async def source_error(_request: Request, error: SourceError):
        return Response(str(error), status_code=502, media_type="text/plain")

    @app.exception_handler(httpx.HTTPError)
    async def network_error(_request: Request, _error: httpx.HTTPError):
        return Response("fixture upstream unavailable", status_code=502, media_type="text/plain")

    @app.exception_handler(KeyError)
    async def missing(_request: Request, _error: KeyError):
        return Response("unknown or expired prototype item/session", status_code=404, media_type="text/plain")

    @app.get("/api/prototype/health")
    def health():
        return {"ok": True, "source": "fixture-maccms", "synthetic": True,
                "api_network": gateway.api_policy.mode, "media_network": gateway.media_policy.mode}

    @app.get("/api/prototype/search")
    def search(q: str = ""):
        return gateway.search(q)

    @app.get("/api/prototype/items/{item_id}")
    def detail(item_id: str):
        return gateway.detail(item_id)[0]

    @app.get("/api/prototype/play/{item_id}/{episode_id}")
    def play(item_id: str, episode_id: str, transport: str = "hls"):
        if transport not in {"mp4", "hls"}:
            raise HTTPException(422, "transport must be mp4 or hls")
        return gateway.resolve(item_id, episode_id, transport)

    @app.delete("/api/prototype/sessions/{sid}")
    def close_session(sid: str):
        with gateway.lock:
            gateway.sessions.pop(sid, None)
        return {"ok": True}

    @app.api_route("/api/prototype/media/{sid}/{rid}", methods=["GET", "HEAD"])
    def media(sid: str, rid: str, request: Request):
        url = gateway.media_url(sid, rid)
        if url.endswith(".m3u8"):
            data = gateway.rewrite_hls(sid, url)
            return Response(b"" if request.method == "HEAD" else data,
                            media_type="application/vnd.apple.mpegurl",
                            headers={"Content-Length": str(len(data)), "Cache-Control": "no-store"})
        headers = {}
        if "Range" in request.headers:
            headers["Range"] = request.headers["Range"]
        upstream = gateway.media.send(gateway.media.build_request(request.method, url, headers=headers),
                                      stream=True)
        if upstream.status_code not in {200, 206, 416}:
            upstream.close()
            raise SourceError(f"upstream status {upstream.status_code}")
        response_headers = {key: value for key, value in upstream.headers.items()
                            if key in {"content-type", "content-length", "content-range", "accept-ranges"}}
        response_headers["cache-control"] = "no-store"
        if request.method == "HEAD" or upstream.status_code == 416:
            upstream.close()
            return Response(status_code=upstream.status_code, headers=response_headers)

        def chunks():
            try:
                for chunk in upstream.iter_bytes(chunk_size=65536):
                    try:
                        gateway.session(sid)
                    except KeyError:
                        return
                    yield chunk
            finally:
                upstream.close()

        return UpstreamStreamingResponse(upstream, chunks(), status_code=upstream.status_code,
                                         headers=response_headers)

    if static_dir:
        app.mount("/", StaticFiles(directory=static_dir, html=True))
    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=os.environ.get("ONLINE_PROTOTYPE_ROOT"))
    parser.add_argument("--port", type=int, default=18190)
    parser.add_argument("--static-dir", type=Path)
    parser.add_argument("--api-proxy", help="explicit loopback HTTP test proxy; default direct")
    parser.add_argument("--media-proxy", help="explicit loopback HTTP test proxy; default direct")
    args = parser.parse_args()
    if args.root is None:
        parser.error("provide --root or ONLINE_PROTOTYPE_ROOT for isolated generated media")
    try:
        root = guarded_path(args.root)
    except ValueError as error:
        parser.error(str(error))
    if not root.is_relative_to(Path("/tmp")) or not root.name.startswith("jzmedia-online-prototype-"):
        parser.error("--root must be under /tmp/jzmedia-online-prototype-*")
    media_root = root / "media"
    if args.static_dir is not None:
        try:
            static = guarded_path(args.static_dir)
        except ValueError as error:
            parser.error(str(error))
        if not static.is_dir() or not static.is_relative_to(root):
            parser.error("--static-dir must be an existing build inside the prototype root")
        args.static_dir = static
    generate_media(media_root)
    api = NetworkPolicy("proxy", args.api_proxy) if args.api_proxy else NetworkPolicy()
    media = NetworkPolicy("proxy", args.media_proxy) if args.media_proxy else NetworkPolicy()
    import uvicorn
    with FixtureOrigin(media_root) as origin:
        app = create_app(origin, api_policy=api, media_policy=media, static_dir=args.static_dir)
        print(f"prototype synthetic fixture: {root}; http://127.0.0.1:{args.port}", flush=True)
        uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
