"""Run P3 real state APIs with P2's isolated database, MacCMS and media origin.

The root intentionally uses /tmp/jzmedia-online-p2-p3-* and the P2 marker so
all existing path, symlink, media and cleanup guards remain in force. Generate
synthetic media only on the development host, before starting the server.
"""

import argparse
from contextlib import closing, contextmanager
from pathlib import Path
import shutil

from scripts.online_p2.server import FIXTURE_TOKEN as FIXTURE_TOKEN
from scripts.online_p2.server import isolated_fixture as catalogue_fixture
from scripts.online_p3.fixtures import MARKER, ROOT_PREFIX, fixture_root, prepare_media
from app.store import _base


@contextmanager
def isolated_fixture(value, *, static_dir=None):
    root = fixture_root(value, require_media=True)
    if not root.name.startswith(ROOT_PREFIX):
        raise ValueError("P3 root must use its /tmp/jzmedia-online-p2-p3-* prefix")
    with catalogue_fixture(root, static_dir=static_dir) as app:
        app.title = "jzmedia isolated P3 state fixture"
        previous_metrics = app.state.fixture.metrics

        def state_metrics():
            result = previous_metrics()
            with closing(_base._conn()) as connection:
                result["online_state_rows"] = {
                    table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    for table in ("online_favorites", "online_progress")}
            return result

        app.state.fixture.metrics = state_metrics
        yield app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--port", type=int, default=18193)
    parser.add_argument("--static-dir", type=Path)
    parser.add_argument("--generate-media", action="store_true")
    parser.add_argument("--cleanup", action="store_true")
    args = parser.parse_args()
    try:
        root = fixture_root(args.root)
        if not root.name.startswith(ROOT_PREFIX):
            raise ValueError("P3 root must use its /tmp/jzmedia-online-p2-p3-* prefix")
        if args.generate_media:
            prepare_media(root)
            print(f"P3 synthetic media prepared: {root}", flush=True)
            return
        if not 1024 <= args.port <= 65535:
            parser.error("port must be between 1024 and 65535")
        import uvicorn
        try:
            with isolated_fixture(root, static_dir=args.static_dir) as app:
                print(f"P3 isolated fixture: http://127.0.0.1:{args.port}; root={root}", flush=True)
                uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
        finally:
            if args.cleanup and (root / MARKER).is_file():
                shutil.rmtree(fixture_root(root, require_media=True))
    except ValueError as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
