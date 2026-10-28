"""Apply a P4-specific root guard on the unchanged P3 synthetic media format."""

from pathlib import Path

from scripts.online_p3.fixtures import DURATION, MARKER, MARKER_TEXT
from scripts.online_p3.fixtures import fixture_root as state_fixture_root
from scripts.online_p3.fixtures import prepare_media as prepare_state_media


__all__ = ["DURATION", "MARKER", "MARKER_TEXT", "ROOT_PREFIX", "fixture_root", "prepare_media"]

ROOT_PREFIX = "jzmedia-online-p2-p3-p4-"


def fixture_root(value, *, require_media=False):
    candidate = Path(value)
    if candidate.is_symlink():
        raise ValueError("P4 root must not be a symlink")
    root = state_fixture_root(candidate, require_media=require_media)
    if not root.name.startswith(ROOT_PREFIX):
        raise ValueError("P4 root must use /tmp/jzmedia-online-p2-p3-p4-*")
    if (root / MARKER).is_symlink() or (root / "media").is_symlink():
        raise ValueError("P4 marker and media root must not be symlinks")
    return root


def prepare_media(value):
    return prepare_state_media(fixture_root(value))
