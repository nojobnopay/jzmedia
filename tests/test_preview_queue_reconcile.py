"""Queue reconciliation only reads the current candidates, preserving validation."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routers.stream import previews as pv


@pytest.fixture
def queued_cache(monkeypatch, tmp_path):
    root = tmp_path / "previews"
    root.mkdir()
    monkeypatch.setattr(pv, "_root", lambda: root)
    monkeypatch.setattr(pv, "_auto_queue", [])
    monkeypatch.setattr(pv, "_ready_queue_check", (None, 0.0))
    monkeypatch.setattr(pv, "_failure_store", None)
    monkeypatch.setattr(pv.store, "get_library", lambda lid: {"id": lid, "source": "local", "path": "/isolated"})
    monkeypatch.delenv("PREVIEW_INTERVAL", raising=False)
    rows, sources, resolved = {}, {}, []

    def source_for(vid, kind):
        resolved.append((kind, vid))
        return rows[(kind, vid)], sources[(kind, vid)]

    monkeypatch.setattr(pv, "_version_source", source_for)
    monkeypatch.setattr(pv, "preview_source", lambda row, **_kwargs: pv._version_source(row["id"], row["kind"])[1])
    monkeypatch.setattr(pv.store, "get_preview_playables", lambda refs, library_id=None: [
        dict(rows[tuple(ref)]) for ref in refs if tuple(ref) in rows])
    monkeypatch.setattr(pv.store, "preview_catalogue_snapshot", lambda _lid: tuple(
        (row["kind"], row["id"], row["library_id"], row["file_path"]) for row in rows.values()))
    # Physical replacement and final-stat races have their own real-backend tests.
    # These fixtures isolate sprite integrity and source-key selection.
    monkeypatch.setattr(pv, "verify_sources", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(pv, "_media_cached_or_probe", lambda *_a: pytest.fail("must not probe"))
    monkeypatch.setattr(pv, "_generate", lambda *_a: pytest.fail("must not extract"))

    def item(vid, *, kind="movie", revision=None, extraction=None, owner=True):
        identity = (kind, vid)
        row = rows[identity] = {"id": vid, "kind": kind, "library_id": 1,
                                "file_path": f"{kind}-{vid}.mkv"}
        source = sources[identity] = SimpleNamespace(
            rel=f"{kind}-{vid}.mkv", size=100, mtime=1,
            backend=SimpleNamespace(library_id=1, root="/isolated"))
        revision = pv.QUALITY_REVISION if revision is None else revision
        key = pv._cache_key(row, source, 10, quality_revision=revision,
                            extraction_revision=extraction)
        directory = root / key
        directory.mkdir()
        width, height = pv._quality_dimensions(revision)
        manifest = dict(version=pv.VERSION, state="ready", quality_revision=revision,
                        duration=265, count=27, interval=10, width=width, height=height,
                        columns=pv.COLUMNS, rows=pv.ROWS,
                        pages=["page_0000.jpg", "page_0001.jpg"])
        if owner:
            manifest.update(item_kind=kind, item_id=vid)
        (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        for page in manifest["pages"]:
            (directory / page).write_bytes(b"isolated sprite")
        pv._auto_queue.append({"kind": kind, "id": vid, "library_id": 1})
        return directory, manifest

    return SimpleNamespace(root=root, item=item, sources=sources, resolved=resolved)


def save_manifest(directory, manifest):
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def test_reconcile_only_opens_manifests_for_queued_current_sources(queued_cache, monkeypatch):
    cache = queued_cache
    cache.item(1)
    cache.item(2, kind="episode")
    unrelated = cache.root / ("f" * 32)
    unrelated.mkdir()
    unrelated_manifest = unrelated / "manifest.json"
    unrelated_manifest.write_text("unrelated inventory must remain unread", encoding="utf-8")
    original_glob, original_read = Path.glob, Path.read_text

    def glob(path, pattern):
        assert path != cache.root, "queue refresh must not enumerate all preview directories"
        return original_glob(path, pattern)

    def read(path, *args, **kwargs):
        assert path != unrelated_manifest, "queue refresh must not parse unrelated manifests"
        return original_read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "glob", glob)
    monkeypatch.setattr(Path, "read_text", read)
    pv._reconcile_ready_queue()
    assert cache.resolved == [("movie", 1), ("episode", 2)]
    assert pv._auto_queue == []
    assert json.loads(pv._queue_path().read_text(encoding="utf-8")) == []


@pytest.mark.parametrize("damage", ["missing", "empty", "directory", "symlink"])
def test_reconcile_keeps_queue_when_any_sprite_page_is_unusable(queued_cache, damage):
    directory, manifest = queued_cache.item(1)
    page = directory / manifest["pages"][-1]
    page.unlink()
    if damage == "empty":
        page.touch()
    elif damage == "directory":
        page.mkdir()
    elif damage == "symlink":
        page.symlink_to(directory / manifest["pages"][0])
    pv._reconcile_ready_queue()
    assert [entry["id"] for entry in pv._auto_queue] == [1]


@pytest.mark.parametrize("ownership", [
    {"item_kind": "movie", "item_id": 2},
    {"item_kind": "episode", "item_id": 1},
    {"item_kind": "movie"},
    {"item_id": 1},
    {"item_kind": "movie", "item_id": True},
    {"item_kind": "movie", "item_id": [1]},
])
def test_reconcile_rejects_inconsistent_manifest_ownership(queued_cache, ownership):
    directory, manifest = queued_cache.item(1, owner=False)
    manifest.update(ownership)
    save_manifest(directory, manifest)
    pv._reconcile_ready_queue()
    assert [entry["id"] for entry in pv._auto_queue] == [1]


@pytest.mark.parametrize("owner", [False, True])
@pytest.mark.parametrize("extraction", [1, pv.EXTRACTION_REVISION])
def test_reconcile_accepts_current_quality_from_supported_extraction_rules(
        queued_cache, owner, extraction):
    queued_cache.item(1, owner=owner, extraction=extraction)
    pv._reconcile_ready_queue()
    assert pv._auto_queue == []


@pytest.mark.parametrize("changed", ["source", "interval", "quality"])
def test_reconcile_preserves_queue_for_stale_preview(queued_cache, monkeypatch, changed):
    queued_cache.item(1, revision=1 if changed == "quality" else None)
    if changed == "source":
        queued_cache.sources[("movie", 1)].mtime += 1
    elif changed == "interval":
        monkeypatch.setenv("PREVIEW_INTERVAL", "20")
    pv._reconcile_ready_queue()
    assert [entry["id"] for entry in pv._auto_queue] == [1]


def test_one_unavailable_source_does_not_prevent_other_ready_items_retiring(queued_cache, monkeypatch):
    queued_cache.item(1)
    queued_cache.item(2)
    original = pv._version_source

    def source(vid, kind):
        if vid == 1:
            raise HTTPException(503, "isolated source unavailable")
        return original(vid, kind)

    monkeypatch.setattr(pv, "_version_source", source)
    pv._reconcile_ready_queue()
    assert [entry["id"] for entry in pv._auto_queue] == [1]


def test_completed_manifest_checks_each_sprite_with_one_filesystem_lookup(queued_cache, monkeypatch):
    directory, manifest = queued_cache.item(1)
    original = Path.stat
    lookups = []
    pages = {directory / name for name in manifest["pages"]}

    def stat(path, *args, **kwargs):
        if path in pages:
            lookups.append((path, kwargs.get("follow_symlinks", True)))
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", stat)
    assert pv._read_completed_manifest(directory) == manifest
    assert lookups == [(directory / name, False) for name in manifest["pages"]]
