"""Build a movie deletion manifest from catalogue ownership and unambiguous sidecars."""
import os
import re
from collections import defaultdict

from fastapi import HTTPException

from ... import storage, store
from ...scanner import SIDECAR_SUB_DIRS, SUBTITLE_EXTS, VIDEO_EXTS, _SIDECAR_LANG_HINTS, is_sidecar, same_stem

__all__ = ["_movie_delete_scope"]

_ART_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
_ART_NAMES = {"poster", "fanart", "backdrop", "folder", "banner", "logo", "clearlogo", "landscape"}
_SUB_TAGS = {tag for tag, *_ in _SIDECAR_LANG_HINTS} | set(
    "hans hant cn tw hk us gb ja kr fr fra fre es spa de deu ger it ita nl nld ru rus "
    "pt por br ar ara hi hin und sdh cc forced default signs commentary 双语 字幕 双字".split())


def catalogue_owners(library_id, *, directory=None, path=None):
    """A registered movie, TV file or another film's extra takes precedence over a filename."""
    where = "library_id=?"
    params = [library_id]
    if path is not None:
        where += " AND file_path=?"
        params.append(path)
    elif directory:
        # Both ends delimit the prefix 'directory/' in SQLite's binary path ordering.
        where += " AND file_path>=? AND file_path<?"
        params.extend((directory + "/", directory + "0"))
    elif directory == "":
        where += " AND instr(file_path, '/')=0"
    with store._lock, store._conn() as conn:
        rows = conn.execute(
            f"SELECT file_path, id AS owner FROM main.movies WHERE {where} "
            f"UNION ALL SELECT file_path, CASE WHEN show_id IS NULL THEN movie_id END FROM main.extras WHERE {where} "
            f"UNION ALL SELECT file_path, NULL FROM main.tv_episodes WHERE {where}", params * 3).fetchall()
    owners = defaultdict(set)
    for row in rows:
        owners[row["file_path"]].add(row["owner"])
    return owners


def _sibling_kind(rel, own_stems, foreign_stems, dedicated):
    name = os.path.basename(rel)
    stem, ext = os.path.splitext(name)
    ext = ext.lower()
    own = max((s for s in own_stems if same_stem(stem, (s,))), key=len, default="")
    if any(len(s) >= len(own) and same_stem(stem, (s,)) for s in foreign_stems):
        return None
    if ext in SUBTITLE_EXTS and own:
        tags = [tag for tag in re.split(r"[-._ ]+", stem[len(own):].casefold()) if tag]
        if stem == own or tags and all(tag in _SUB_TAGS for tag in tags):
            return "subtitle"
    if ext == ".nfo" and (stem in own_stems or dedicated and name.lower() == "movie.nfo"):
        return "nfo"
    if ext in _ART_EXTS and (stem in own_stems or dedicated and stem.lower() in _ART_NAMES
                             or any(stem.startswith(s + "-") and stem[len(s) + 1:].lower() in _ART_NAMES for s in own_stems)):
        return "artwork"
    return None


def _foreign_stems(directory, entries, owners, own_paths, backend):
    stems = {os.path.splitext(os.path.basename(rel))[0] for rel in owners
             if rel not in own_paths and os.path.dirname(rel) == directory}
    for entry in entries:
        if entry["is_dir"]:
            continue
        rel = os.path.join(directory, entry["name"])
        stem, ext = os.path.splitext(entry["name"])
        if rel not in own_paths and ext.lower() in VIDEO_EXTS and not is_sidecar(rel, backend=backend):
            stems.add(stem)
    return stems


def _movie_delete_scope(movie_id: int, backend=None, *, movie=None, owners=None) -> dict:
    movie = movie or store.get_movie(movie_id)
    if not movie:
        raise HTTPException(404, "movie not found")
    lid = movie["library_id"]
    backend = backend or storage.backend_for(lid)
    owners = catalogue_owners(lid) if owners is None else owners
    versions = {row["file_path"]: row["id"] for row in movie["versions"] if row["library_id"] == lid}
    version_ids = set(versions.values())
    own_paths = {rel for rel, ids in owners.items() if ids and ids <= version_ids}
    candidates = {rel: "feature" if rel in versions else "sidecar" for rel in own_paths}
    by_dir = defaultdict(set)
    for rel in versions:
        by_dir[os.path.dirname(rel)].add(rel)
    exclusive = True
    for directory, paths in by_dir.items():
        own_stems = {os.path.splitext(os.path.basename(rel))[0] for rel in paths}
        try:
            entries = backend.list(directory)
        except storage.StorageNotFound:
            continue
        foreign_stems = _foreign_stems(directory, entries, owners, own_paths, backend)
        names = set(own_stems)
        for title in (movie.get("title"), movie.get("original_title")):
            if title:
                names.add(title)
                if movie.get("year"):
                    names.add(f"{title} ({movie['year']})")
        dedicated = bool(directory and not foreign_stems and os.path.basename(directory).casefold()
                         in {name.casefold() for name in names})
        exclusive = exclusive and dedicated
        for entry in entries:
            if entry["is_dir"] or entry["name"].startswith("."):
                continue
            rel = os.path.join(directory, entry["name"])
            if rel in owners:
                continue
            kind = _sibling_kind(rel, own_stems, foreign_stems, dedicated)
            if kind:
                candidates[rel] = kind
        for entry in entries:
            if not entry["is_dir"] or entry["name"] not in SIDECAR_SUB_DIRS:
                continue
            subdir = os.path.join(directory, entry["name"])
            subs = backend.list(subdir)
            if any(not sub["is_dir"] and os.path.splitext(sub["name"])[1].lower() in VIDEO_EXTS for sub in subs):
                continue
            for sub in subs:
                rel = os.path.join(subdir, sub["name"])
                if (not sub["is_dir"] and rel not in owners
                        and _sibling_kind(rel, own_stems, foreign_stems, dedicated=False) == "subtitle"):
                    candidates[rel] = "subtitle"
    files = []
    for rel, kind in sorted(candidates.items()):
        try:
            identity = backend.file_delete_identity(rel)
        except storage.StorageNotFound:
            continue
        if identity is None:
            continue
        size, revision = identity
        files.append({"rel": rel, "size": size, "kind": kind, "revision": revision,
                      **({"version_id": versions[rel]} if kind == "feature" else {})})
    present = {f["rel"] for f in files if f["kind"] == "feature"}
    retained = [{"id": mid, "rel": rel} for rel, mid in sorted(versions.items()) if rel not in present]
    return {"movie": movie, "version_ids": sorted(version_ids), "retained_versions": retained, "exclusive": exclusive,
            "files": files, "total_size": sum(f["size"] for f in files)}
