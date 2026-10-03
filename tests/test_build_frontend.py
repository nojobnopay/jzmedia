"""Brand-only and deleted inputs must invalidate a locally hosted web build."""

import importlib.util
from pathlib import Path


spec = importlib.util.spec_from_file_location(
    "build_frontend", Path(__file__).resolve().parents[1] / "scripts/build_frontend.py")
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


def test_digest_tracks_assets_additions_changes_and_deletions(tmp_path):
    public = tmp_path / "frontend/public"
    public.mkdir(parents=True)
    before = build.source_digest(tmp_path)
    logo = public / "favicon.svg"
    logo.write_text("first")
    added = build.source_digest(tmp_path)
    assert added != before
    logo.write_text("next!")
    assert build.source_digest(tmp_path) != added
    logo.unlink()
    assert build.source_digest(tmp_path) == before


def test_digest_tracks_configuration_but_ignores_output_and_caches(tmp_path):
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    previous = build.source_digest(tmp_path)
    for filename in ("index.html", "package.json", "package-lock.json", "vite.config.js"):
        (frontend / filename).write_text("input")
        current = build.source_digest(tmp_path)
        assert current != previous
        previous = current
    for directory in ("dist", "node_modules"):
        (frontend / directory).mkdir()
        (frontend / directory / "generated.txt").write_text("output")
    assert build.source_digest(tmp_path) == previous
