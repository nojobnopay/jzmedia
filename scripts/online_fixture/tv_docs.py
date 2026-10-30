"""Isolated native-TV documentation demo using the original fictional artwork.

Uses the regular disposable TV fixture; artwork and catalogue substitutions are
local to this process and never affect the ordinary fixture or a real library.
"""

import argparse
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import shutil
import signal

from PIL import Image

from scripts.online_fixture.tv import FIXTURE_TOKEN, MARKER, fixture_root, isolated_fixture, prepare_media


REPOSITORY = Path(__file__).resolve().parents[2]


def install_documentation_artwork(origin):
    """Replace only this fixture's generated catalogue and artwork responses."""
    manifest = json.loads((REPOSITORY / "design/demo-artwork/manifest.json").read_text(encoding="utf-8"))
    artwork = [item for item in manifest["assets"] if item["id"].startswith(("movie-", "show-"))]
    if not artwork:
        raise ValueError("No documentation artwork found")
    for item in artwork:
        master = REPOSITORY / item["master"]
        if hashlib.sha256(master.read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError(f"Documentation artwork hash mismatch: {item['id']}")
        path = origin.root / f"tv-doc-{item['id']}.png"
        if path.is_symlink():
            raise ValueError("Documentation poster cannot be a symlink")
        with Image.open(master) as image:
            image = image.convert("RGB")
            image.thumbnail((240, 360), Image.Resampling.LANCZOS)
            image.quantize(colors=256).save(path, optimize=True)
        if path.stat().st_size > 512 * 1024:
            raise ValueError(f"Documentation poster exceeds gateway limit: {item['id']}")
        origin.assets.add(path.name)

    original_records = origin.records

    def documentation_records(mode="normal"):
        rows = original_records(mode)
        for index, row in enumerate(rows):
            item = artwork[index % len(artwork)]
            media_type, number = item["id"].split("-")
            number = int(number)
            row["vod_name"] = item["title"]
            row["vod_pic"] = f"{origin.base_url}/files/tv-doc-{item['id']}.png"
            row["vod_year"] = str((2014 if media_type == "movie" else 2023) + (number if media_type == "movie" else -number))
            row["type_name"] = "电影" if media_type == "movie" else "剧集"
            row["vod_class"] = "剧情,悬疑" if number % 2 else "剧情,冒险"
            row["vod_remarks"] = "正片" if media_type == "movie" else "全2集"
            if media_type == "movie" and row.get("vod_play_url"):
                row["vod_play_url"] = "$$$".join(
                    "正片$" + group.split("#", 1)[0].split("$", 1)[1]
                    for group in row["vod_play_url"].split("$$$")
                )
        return rows

    origin.records = documentation_records
    origin.taxonomy = True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--port", type=int, default=18194)
    parser.add_argument("--generate-media", action="store_true")
    parser.add_argument("--cleanup", action="store_true")
    args = parser.parse_args()
    try:
        root = fixture_root(args.root)
        if args.generate_media:
            prepare_media(root)
            print(f"TV documentation media prepared: {root}", flush=True)
            return
        if not 1024 <= args.port <= 65535:
            parser.error("port must be between 1024 and 65535")
        import uvicorn

        class FixtureServer(uvicorn.Server):
            @contextmanager
            def capture_signals(self):
                previous = {sig: signal.signal(sig, self.handle_exit) for sig in (signal.SIGINT, signal.SIGTERM)}
                try:
                    yield
                finally:
                    for sig, handler in previous.items():
                        signal.signal(sig, handler)

        try:
            with isolated_fixture(root) as app:
                install_documentation_artwork(app.state.fixture.origin)
                print(f"Isolated TV documentation fixture: http://127.0.0.1:{args.port}; root={root}", flush=True)
                print(f"Fixture-only access token: {FIXTURE_TOKEN}; fictional artwork and taxonomy enabled.", flush=True)
                FixtureServer(uvicorn.Config(app, host="127.0.0.1", port=args.port, log_level="warning")).run()
        finally:
            if args.cleanup and (root / MARKER).is_file():
                shutil.rmtree(fixture_root(root, require_media=True))
    except ValueError as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
